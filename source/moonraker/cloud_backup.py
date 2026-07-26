# Fluidd configuration backup uploader for cloud storage providers
#
# Copyright (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import pathlib
import re
import shutil
import stat
import sys
import tarfile
import time
import uuid
from datetime import datetime
from urllib.parse import quote

from ..common import RequestType

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from ..common import WebRequest
    from ..confighelper import ConfigHelper
    from .file_manager.file_manager import FileManager
    from .http_client import HttpClient, HttpResponse


CLOUD_BACKUP_VERSION = "0.1alpha"
MIN_REASON_LENGTH = 10
MAX_REASON_LENGTH = 500
MAX_HISTORY = 100
WEB_REMOTE_PATH_RE = re.compile(r"^/(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\x00-\x1f]+$")
AUTH_MODES = {"bypy", "web_password"}
PROVIDERS = {"baidu", "github"}
AUTO_BACKUP_MODES = {"interval", "startup"}
MIN_AUTO_INTERVAL_DAYS = 1
MAX_AUTO_INTERVAL_DAYS = 365
MIN_STARTUP_DELAY_MINUTES = 1
MAX_STARTUP_DELAY_MINUTES = 1440
AUTO_RETRY_SECONDS = 60 * 60
AUTO_BUSY_RETRY_SECONDS = 15 * 60
GITHUB_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
GITHUB_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
MAX_GITHUB_CONTENT_SIZE = 100 * 1024 * 1024
BYPY_AUTH_URL_RE = re.compile(r"https://openapi\.baidu\.com/[^\s]+")
BYPY_FINAL_ERROR_RE = re.compile(r"(?m)^Error\s+-?\d+(?::[^\r\n]*)?\s*$")
BYPY_DEFAULT_REMOTE_PATH = "/3D打印机备份"
BYPY_DIRECTORY_MANIFEST_NAME = "backup-manifest.json"
BACKUP_DESCRIPTION_NAME = "备份说明.txt"
BACKUP_CHECKSUM_NAME = "SHA256SUMS"
DOWNLOAD_ROOT_NAME = "cloud_backup_downloads"
DOWNLOAD_RETAIN = 5
DOWNLOAD_MAX_AGE_SECONDS = 24 * 60 * 60
SNAPSHOT_EXCLUDED_DIRECTORIES = {".git", "__pycache__"}


def _validate_web_remote_path(value: str) -> str:
    value = "/" + value.strip().strip("/")
    parts = value.split("/")[1:]
    if (
        len(value) > 512 or value == "/" or
        not WEB_REMOTE_PATH_RE.fullmatch(value) or
        any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("web_remote_path must be an absolute Baidu folder path")
    return value


def _validate_github_name(value: str, field: str) -> str:
    value = value.strip()
    if not GITHUB_NAME_RE.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"{field} contains unsupported characters")
    return value


def _bypy_final_error(output: str) -> Optional[str]:
    matches = BYPY_FINAL_ERROR_RE.findall(output)
    return matches[-1].strip() if matches else None


def _validate_github_branch(value: str) -> str:
    value = value.strip().strip("/")
    if (
        not GITHUB_BRANCH_RE.fullmatch(value) or
        ".." in value or "//" in value or value.endswith(".")
    ):
        raise ValueError("github_branch is invalid")
    return value


def _validate_github_path(value: str) -> str:
    value = value.strip().strip("/")
    parts = value.split("/") if value else []
    if (
        len(value) > 512 or not parts or
        any(part in {"", ".", ".."} for part in parts) or
        any("\x00" <= char <= "\x1f" for char in value)
    ):
        raise ValueError("github_path must be a non-empty repository folder")
    return "/".join(parts)


def _remove_tree(path: pathlib.Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)


def _atomic_json_write(path: pathlib.Path, value: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temp_path.chmod(mode)
        os.replace(str(temp_path), str(path))
        path.chmod(mode)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _read_json(path: pathlib.Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        with path.open("r", encoding="utf-8") as source:
            return json.load(source)
    except (OSError, ValueError):
        logging.warning("Cloud backup state file is invalid: %s", path.name)
        return default


def _prune_archives_sync(archive_dir: pathlib.Path, retain: int) -> None:
    archives = sorted(
        archive_dir.glob("*.tar.gz"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for archive in archives[max(0, retain):]:
        archive.unlink()


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> pathlib.PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("backup path is invalid")
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("backup path is invalid")
    return path


def _snapshot_file_record(
    snapshot_dir: pathlib.Path, relative_path: str
) -> Dict[str, Any]:
    safe_path = _safe_relative_path(relative_path)
    file_path = snapshot_dir.joinpath(*safe_path.parts)
    file_stat = file_path.stat()
    return {
        "path": safe_path.as_posix(),
        "size": file_stat.st_size,
        "sha256": _sha256_file(file_path),
        "mode": format(stat.S_IMODE(file_stat.st_mode), "04o"),
    }


def _write_symlink_marker(
    source_path: pathlib.Path,
    snapshot_dir: pathlib.Path,
    relative_path: pathlib.PurePosixPath,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    marker_relative = pathlib.PurePosixPath(f"{relative_path.as_posix()}.symlink.txt")
    marker_path = snapshot_dir.joinpath(*marker_relative.parts)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    target = os.readlink(source_path)
    marker_path.write_text(
        "Symbolic link recorded without following its target.\n"
        f"Original path: {relative_path.as_posix()}\n"
        f"Link target: {target}\n",
        encoding="utf-8",
        newline="\n",
    )
    return (
        _snapshot_file_record(snapshot_dir, marker_relative.as_posix()),
        {
            "path": relative_path.as_posix(),
            "target": target,
            "marker": marker_relative.as_posix(),
        },
    )


def _create_snapshot_sync(
    snapshot_dir: pathlib.Path,
    roots: List[Tuple[str, pathlib.Path]],
    reason: str,
    created_at: float,
    job_id: str,
) -> Dict[str, Any]:
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    files: List[Dict[str, Any]] = []
    directories: List[str] = []
    symlinks: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    source_file_count = 0
    source_size = 0

    for root_name, root_path in roots:
        root_relative = _safe_relative_path(root_name)
        root_destination = snapshot_dir.joinpath(*root_relative.parts)
        root_destination.mkdir(parents=True, exist_ok=True)
        directories.append(root_relative.as_posix())
        for current, dirnames, filenames in os.walk(root_path, followlinks=False):
            current_path = pathlib.Path(current)
            current_relative = current_path.relative_to(root_path)
            snapshot_current = root_destination.joinpath(current_relative)
            snapshot_current.mkdir(parents=True, exist_ok=True)
            if current_relative.parts:
                directories.append(
                    root_relative.joinpath(*current_relative.parts).as_posix()
                )

            retained_dirs: List[str] = []
            for dirname in sorted(dirnames):
                source_path = current_path.joinpath(dirname)
                relative = root_relative.joinpath(
                    *current_relative.parts, dirname
                )
                if dirname in SNAPSHOT_EXCLUDED_DIRECTORIES:
                    skipped.append({
                        "path": relative.as_posix(),
                        "reason": "excluded internal metadata directory",
                    })
                    continue
                if source_path.is_symlink():
                    marker, link = _write_symlink_marker(
                        source_path, snapshot_dir, relative
                    )
                    files.append(marker)
                    symlinks.append(link)
                    continue
                retained_dirs.append(dirname)
            dirnames[:] = retained_dirs

            for filename in sorted(filenames):
                source_path = current_path.joinpath(filename)
                relative = root_relative.joinpath(
                    *current_relative.parts, filename
                )
                if source_path.is_symlink():
                    marker, link = _write_symlink_marker(
                        source_path, snapshot_dir, relative
                    )
                    files.append(marker)
                    symlinks.append(link)
                    continue
                try:
                    source_stat = source_path.lstat()
                except OSError as exc:
                    skipped.append({
                        "path": relative.as_posix(),
                        "reason": str(exc),
                    })
                    continue
                if not stat.S_ISREG(source_stat.st_mode):
                    skipped.append({
                        "path": relative.as_posix(),
                        "reason": "not a regular file",
                    })
                    continue
                destination = snapshot_dir.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination, follow_symlinks=False)
                record = _snapshot_file_record(snapshot_dir, relative.as_posix())
                files.append(record)
                source_file_count += 1
                source_size += int(record["size"])

    timestamp = datetime.fromtimestamp(created_at).astimezone().isoformat()
    description = (
        "Fluidd 3D 打印机配置云备份说明\n"
        f"备份时间：{timestamp}\n"
        f"任务编号：{job_id}\n"
        f"备份原因：{reason}\n"
        f"来源目录：{', '.join(name for name, _ in roots)}\n"
        f"普通文件数量：{source_file_count}\n"
        f"普通文件总字节数：{source_size}\n"
        f"符号链接数量：{len(symlinks)}\n"
        f"跳过项目数量：{len(skipped)}\n"
        "内容范围：所选 Moonraker 文件根目录的配置文件及原目录结构。\n"
        "校验方式：backup-manifest.json 记录每个文件的大小和 SHA-256，"
        "SHA256SUMS 可用于整批校验。\n"
        "恢复用途：下载后人工核对并按需恢复；本功能不会自动覆盖打印机当前配置。\n"
        "创建程序：Moonraker cloud_backup component\n"
    )
    description_path = snapshot_dir.joinpath(BACKUP_DESCRIPTION_NAME)
    description_path.write_text(
        description, encoding="utf-8", newline="\n"
    )
    files.append(_snapshot_file_record(snapshot_dir, BACKUP_DESCRIPTION_NAME))
    files.sort(key=lambda item: str(item["path"]))
    manifest = {
        "format": "fluidd-cloud-backup-directory",
        "format_version": 1,
        "job_id": job_id,
        "created_at": created_at,
        "created_at_iso": timestamp,
        "reason": reason,
        "roots": [name for name, _ in roots],
        "source_file_count": source_file_count,
        "source_size": source_size,
        "directories": sorted(set(directories)),
        "files": files,
        "symlinks": symlinks,
        "skipped": skipped,
    }
    manifest_path = snapshot_dir.joinpath(BYPY_DIRECTORY_MANIFEST_NAME)
    _atomic_json_write(manifest_path, manifest, 0o600)

    checksum_files = [
        item for item in snapshot_dir.rglob("*")
        if item.is_file() and item.name != BACKUP_CHECKSUM_NAME
    ]
    checksum_lines = [
        f"{_sha256_file(item)}  {item.relative_to(snapshot_dir).as_posix()}"
        for item in sorted(checksum_files)
    ]
    checksum_path = snapshot_dir.joinpath(BACKUP_CHECKSUM_NAME)
    checksum_path.write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8", newline="\n"
    )
    upload_files = sorted(
        (item for item in snapshot_dir.rglob("*") if item.is_file()),
        key=lambda item: (
            item.name == BYPY_DIRECTORY_MANIFEST_NAME,
            item.relative_to(snapshot_dir).as_posix(),
        ),
    )
    return {
        "file_count": source_file_count,
        "source_size": source_size,
        "snapshot_size": sum(item.stat().st_size for item in upload_files),
        "upload_total_files": len(upload_files),
        "symlink_count": len(symlinks),
        "skipped_count": len(skipped),
    }


def _archive_snapshot_sync(
    snapshot_dir: pathlib.Path, archive_path: pathlib.Path
) -> Dict[str, Any]:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(str(archive_path), "w:gz", format=tarfile.PAX_FORMAT) as archive:
            for item in sorted(snapshot_dir.rglob("*")):
                archive.add(
                    str(item),
                    arcname=item.relative_to(snapshot_dir).as_posix(),
                    recursive=False,
                )
    except Exception:
        if archive_path.exists():
            archive_path.unlink()
        raise
    return {
        "archive_size": archive_path.stat().st_size,
        "sha256": _sha256_file(archive_path),
    }


def _prepare_backup_sync(
    snapshot_dir: pathlib.Path,
    archive_path: pathlib.Path,
    roots: List[Tuple[str, pathlib.Path]],
    reason: str,
    created_at: float,
    job_id: str,
) -> Dict[str, Any]:
    result = _create_snapshot_sync(
        snapshot_dir, roots, reason, created_at, job_id
    )
    result.update(_archive_snapshot_sync(snapshot_dir, archive_path))
    return result


def _verify_snapshot_sync(snapshot_dir: pathlib.Path) -> Dict[str, Any]:
    snapshot_root = snapshot_dir.resolve()
    manifest_path = snapshot_dir.joinpath(BYPY_DIRECTORY_MANIFEST_NAME)
    checksum_path = snapshot_dir.joinpath(BACKUP_CHECKSUM_NAME)
    manifest = _read_json(manifest_path, None)
    if not isinstance(manifest, dict) or manifest.get("format") != (
        "fluidd-cloud-backup-directory"
    ):
        raise ValueError("backup-manifest.json is missing or invalid")
    file_records = manifest.get("files")
    if not isinstance(file_records, list):
        raise ValueError("backup manifest file list is invalid")

    expected_files: Dict[str, Dict[str, Any]] = {}
    for record in file_records:
        if not isinstance(record, dict):
            raise ValueError("backup manifest contains an invalid file record")
        relative = _safe_relative_path(str(record.get("path", ""))).as_posix()
        if relative in expected_files:
            raise ValueError("backup manifest contains duplicate file paths")
        file_path = snapshot_dir.joinpath(*pathlib.PurePosixPath(relative).parts)
        resolved = file_path.resolve()
        if snapshot_root != resolved and snapshot_root not in resolved.parents:
            raise ValueError("backup manifest path escapes the download directory")
        if not file_path.is_file() or file_path.is_symlink():
            raise ValueError(f"backup file is missing: {relative}")
        expected_size = record.get("size")
        expected_sha = record.get("sha256")
        if (
            not isinstance(expected_size, int) or isinstance(expected_size, bool) or
            file_path.stat().st_size != expected_size or
            not isinstance(expected_sha, str) or
            _sha256_file(file_path) != expected_sha
        ):
            raise ValueError(f"backup file verification failed: {relative}")
        expected_files[relative] = record

    if not checksum_path.is_file() or checksum_path.is_symlink():
        raise ValueError("SHA256SUMS is missing")
    checksum_entries: Dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise ValueError("SHA256SUMS contains an invalid line")
        relative = _safe_relative_path(match.group(2)).as_posix()
        if relative in checksum_entries:
            raise ValueError("SHA256SUMS contains duplicate paths")
        checksum_entries[relative] = match.group(1)
    checksum_expected = set(expected_files) | {BYPY_DIRECTORY_MANIFEST_NAME}
    if set(checksum_entries) != checksum_expected:
        raise ValueError("SHA256SUMS does not match the backup manifest")
    for relative, expected_sha in checksum_entries.items():
        file_path = snapshot_dir.joinpath(*pathlib.PurePosixPath(relative).parts)
        if _sha256_file(file_path) != expected_sha:
            raise ValueError(f"SHA256SUMS verification failed: {relative}")

    actual_files = {
        item.relative_to(snapshot_dir).as_posix()
        for item in snapshot_dir.rglob("*") if item.is_file()
    }
    allowed_files = checksum_expected | {BACKUP_CHECKSUM_NAME}
    if actual_files != allowed_files:
        raise ValueError("downloaded backup contains unlisted files")
    if any(item.is_symlink() for item in snapshot_dir.rglob("*")):
        raise ValueError("downloaded backup contains symbolic links")
    return manifest


def _prune_downloads_sync(
    download_dir: pathlib.Path,
    retain: int = DOWNLOAD_RETAIN,
    max_age: int = DOWNLOAD_MAX_AGE_SECONDS,
) -> None:
    download_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    archives = sorted(
        download_dir.glob("*.tar.gz"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for index, archive in enumerate(archives):
        if index >= retain or now - archive.stat().st_mtime > max_age:
            archive.unlink()
    for item in download_dir.iterdir():
        if item.name.startswith(".") and now - item.stat().st_mtime > 60 * 60:
            if item.is_dir():
                shutil.rmtree(item)
            elif item.is_file():
                item.unlink()


def _publish_archive_sync(
    source_path: pathlib.Path,
    download_dir: pathlib.Path,
    filename: str,
) -> pathlib.Path:
    if pathlib.Path(filename).name != filename or not filename.endswith(".tar.gz"):
        raise ValueError("download archive name is invalid")
    download_dir.mkdir(parents=True, exist_ok=True)
    destination = download_dir.joinpath(filename)
    temp_path = download_dir.joinpath(f".{filename}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source_path, temp_path)
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return destination


def _latest_success_at(history: List[Dict[str, Any]]) -> Optional[float]:
    timestamps: List[float] = []
    for job in history:
        if job.get("state") != "success":
            continue
        value = job.get("finished_at", job.get("created_at"))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            timestamps.append(float(value))
    return max(timestamps, default=None)


def _latest_history_at(history: List[Dict[str, Any]]) -> Optional[float]:
    timestamps: List[float] = []
    for job in history:
        value = job.get("finished_at", job.get("created_at"))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            timestamps.append(float(value))
    return max(timestamps, default=None)


def _latest_automatic_run_at(
    history: List[Dict[str, Any]]
) -> Optional[float]:
    timestamps: List[float] = []
    for job in history:
        if job.get("trigger") != "automatic":
            continue
        value = job.get("created_at")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            timestamps.append(float(value))
    return max(timestamps, default=None)


def _next_interval_run_at(
    history: List[Dict[str, Any]], interval_days: int, not_before: float
) -> float:
    latest_success = _latest_success_at(history)
    if latest_success is None:
        return not_before
    return max(not_before, latest_success + interval_days * 24 * 60 * 60)


def _encode_file_base64(path: pathlib.Path, max_size: int) -> str:
    if path.stat().st_size > max_size:
        raise ValueError("Backup archive exceeds the provider size limit")
    return base64.b64encode(path.read_bytes()).decode("ascii")


class CloudBackup:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.eventloop = self.server.get_event_loop()
        self.http_client: HttpClient = self.server.lookup_component("http_client")
        self.file_manager: FileManager = self.server.lookup_component("file_manager")
        data_path = pathlib.Path(self.server.get_app_arg("data_path"))
        self.data_dir = data_path.joinpath("cloud_backup")
        self.archive_dir = self.data_dir.joinpath("archives")
        self.snapshot_dir = self.data_dir.joinpath("snapshots")
        self.download_dir = self.data_dir.joinpath("downloads")
        self.credentials_path = self.data_dir.joinpath("credentials.json")
        self.history_path = self.data_dir.joinpath("history.json")
        self.settings_path = self.data_dir.joinpath("settings.json")
        self.baidu_web_profile_dir = self.data_dir.joinpath("baidu_web_profile")
        self.bypy_home_dir = self.data_dir.joinpath("bypy_home")
        self.bypy_config_dir = self.bypy_home_dir.joinpath(".bypy")
        self.bypy_token_path = self.bypy_config_dir.joinpath("bypy.json")
        self.web_worker_path = pathlib.Path(__file__).with_name("cloud_backup_web.py")
        default_bypy = self.data_dir.joinpath("bypy-env", "bin", "bypy")
        self.bypy_executable = pathlib.Path(
            config.get("bypy_executable", str(default_bypy)).strip()
        )
        self.web_browser_executable = config.get("web_browser_executable", "").strip()
        self.config_github_token = (
            config.load_template("github_token", "").render().strip()
        )
        self.provider = config.get("provider", "baidu").strip()
        if self.provider not in PROVIDERS:
            raise config.error("provider must be baidu or github")
        self.allowed_root_names = config.getlist("backup_roots", ["config"])
        self.selected_roots = list(self.allowed_root_names)
        self.web_remote_path = _validate_web_remote_path(
            config.get("web_remote_path", "/3D打印机备份")
        )
        self.bypy_remote_path = _validate_web_remote_path(
            config.get("bypy_remote_path", BYPY_DEFAULT_REMOTE_PATH)
        )
        self.github_owner = _validate_github_name(
            config.get("github_owner", "printer-backups"), "github_owner"
        )
        self.github_repo = _validate_github_name(
            config.get("github_repo", "printer-config-backups"), "github_repo"
        )
        self.github_branch = _validate_github_branch(
            config.get("github_branch", "main")
        )
        self.github_path = _validate_github_path(
            config.get("github_path", "printer-backups")
        )
        self.auth_mode = config.get("auth_mode", "bypy").strip()
        if self.auth_mode not in AUTH_MODES:
            raise config.error("auth_mode must be bypy or web_password")
        self.retain_local = max(0, config.getint("retain_local", 5))
        self.auto_backup_enabled = config.getboolean(
            "auto_backup_enabled", False
        )
        self.auto_backup_mode = config.get(
            "auto_backup_mode", "interval"
        ).strip()
        if self.auto_backup_mode not in AUTO_BACKUP_MODES:
            raise config.error(
                "auto_backup_mode must be interval or startup"
            )
        self.auto_backup_interval_days = config.getint(
            "auto_backup_interval_days", 3,
            minval=MIN_AUTO_INTERVAL_DAYS,
            maxval=MAX_AUTO_INTERVAL_DAYS,
        )
        self.auto_backup_startup_delay_minutes = config.getint(
            "auto_backup_startup_delay_minutes", 15,
            minval=MIN_STARTUP_DELAY_MINUTES,
            maxval=MAX_STARTUP_DELAY_MINUTES,
        )
        self.started_at = time.time()
        self.credentials: Dict[str, Any] = {}
        self.history: List[Dict[str, Any]] = []
        self.oauth: Dict[str, Any] = {"state": "idle"}
        self.web_login: Dict[str, Any] = {"state": "idle"}
        self.active_job: Optional[Dict[str, Any]] = None
        self.oauth_task: Optional[asyncio.Task] = None
        self.bypy_auth_process: Optional[asyncio.subprocess.Process] = None
        self.bypy_upload_process: Optional[asyncio.subprocess.Process] = None
        self.web_task: Optional[asyncio.Task] = None
        self.web_process: Optional[asyncio.subprocess.Process] = None
        self.backup_task: Optional[asyncio.Task] = None
        self.auto_backup_task: Optional[asyncio.Task] = None
        self.download_lock = asyncio.Lock()
        self.download_in_progress = False
        self.auto_backup_next_run_at: Optional[float] = None
        self.auto_backup_last_run_at: Optional[float] = None
        self.auto_backup_message = ""
        self.auto_backup_startup_completed = False
        self.component_initialized = False

        self.server.register_notification("cloud_backup:status_changed")
        self.server.register_notification("cloud_backup:job_progress")
        self.server.register_endpoint(
            "/server/cloud_backup/status", RequestType.GET,
            self._handle_status
        )
        self.server.register_endpoint(
            "/server/cloud_backup/config", RequestType.GET | RequestType.POST,
            self._handle_config
        )
        self.server.register_endpoint(
            "/server/cloud_backup/oauth/device", RequestType.POST,
            self._handle_oauth_device
        )
        self.server.register_endpoint(
            "/server/cloud_backup/oauth/status", RequestType.GET,
            self._handle_oauth_status
        )
        self.server.register_endpoint(
            "/server/cloud_backup/oauth/verify", RequestType.POST,
            self._handle_oauth_verify
        )
        self.server.register_endpoint(
            "/server/cloud_backup/oauth/revoke", RequestType.POST,
            self._handle_oauth_revoke
        )
        self.server.register_endpoint(
            "/server/cloud_backup/web/login", RequestType.POST,
            self._handle_web_login
        )
        self.server.register_endpoint(
            "/server/cloud_backup/web/status", RequestType.GET,
            self._handle_web_status
        )
        self.server.register_endpoint(
            "/server/cloud_backup/web/verify", RequestType.POST,
            self._handle_web_verify
        )
        self.server.register_endpoint(
            "/server/cloud_backup/web/logout", RequestType.POST,
            self._handle_web_logout
        )
        self.server.register_endpoint(
            "/server/cloud_backup/github/logout", RequestType.POST,
            self._handle_github_logout
        )
        self.server.register_endpoint(
            "/server/cloud_backup/backup", RequestType.POST,
            self._handle_backup
        )
        self.server.register_endpoint(
            "/server/cloud_backup/history", RequestType.GET,
            self._handle_history
        )
        self.server.register_endpoint(
            "/server/cloud_backup/job", RequestType.GET,
            self._handle_job
        )
        self.server.register_endpoint(
            "/server/cloud_backup/download", RequestType.POST,
            self._handle_download
        )

    async def component_init(self) -> None:
        self.credentials = await self.eventloop.run_in_thread(
            _read_json, self.credentials_path, {}
        )
        if self.config_github_token:
            self.credentials["github_token"] = self.config_github_token
        settings = await self.eventloop.run_in_thread(
            _read_json, self.settings_path, {}
        )
        if isinstance(settings, dict):
            saved_provider = settings.get("provider")
            if saved_provider in PROVIDERS:
                self.provider = saved_provider
            saved_auth_mode = settings.get("auth_mode")
            if saved_auth_mode in AUTH_MODES:
                self.auth_mode = saved_auth_mode
            saved_web_remote_path = settings.get("web_remote_path")
            if isinstance(saved_web_remote_path, str):
                try:
                    self.web_remote_path = _validate_web_remote_path(
                        saved_web_remote_path
                    )
                except ValueError:
                    logging.warning("Ignoring invalid web backup remote path")
            saved_bypy_path = settings.get("bypy_remote_path")
            if isinstance(saved_bypy_path, str):
                try:
                    self.bypy_remote_path = _validate_web_remote_path(
                        saved_bypy_path
                    )
                except ValueError:
                    logging.warning("Ignoring invalid bypy backup path")
            for key, validator, attribute in (
                ("github_owner", lambda value: _validate_github_name(
                    value, "github_owner"), "github_owner"),
                ("github_repo", lambda value: _validate_github_name(
                    value, "github_repo"), "github_repo"),
                ("github_branch", _validate_github_branch, "github_branch"),
                ("github_path", _validate_github_path, "github_path"),
            ):
                saved_value = settings.get(key)
                if isinstance(saved_value, str):
                    try:
                        setattr(self, attribute, validator(saved_value))
                    except ValueError:
                        logging.warning("Ignoring invalid %s", key)
            saved_roots = settings.get("selected_roots")
            if isinstance(saved_roots, list):
                valid_roots = [
                    name for name in saved_roots
                    if isinstance(name, str) and name in self.allowed_root_names
                ]
                if valid_roots:
                    self.selected_roots = valid_roots
            saved_auto_enabled = settings.get("auto_backup_enabled")
            if isinstance(saved_auto_enabled, bool):
                self.auto_backup_enabled = saved_auto_enabled
            saved_auto_mode = settings.get("auto_backup_mode")
            if saved_auto_mode in AUTO_BACKUP_MODES:
                self.auto_backup_mode = saved_auto_mode
            saved_interval = settings.get("auto_backup_interval_days")
            if (
                isinstance(saved_interval, int) and
                not isinstance(saved_interval, bool) and
                MIN_AUTO_INTERVAL_DAYS <= saved_interval <= MAX_AUTO_INTERVAL_DAYS
            ):
                self.auto_backup_interval_days = saved_interval
            saved_delay = settings.get("auto_backup_startup_delay_minutes")
            if (
                isinstance(saved_delay, int) and
                not isinstance(saved_delay, bool) and
                MIN_STARTUP_DELAY_MINUTES <= saved_delay <=
                MAX_STARTUP_DELAY_MINUTES
            ):
                self.auto_backup_startup_delay_minutes = saved_delay
        history = await self.eventloop.run_in_thread(
            _read_json, self.history_path, []
        )
        if isinstance(history, list):
            self.history = history[:MAX_HISTORY]
            self.auto_backup_last_run_at = _latest_automatic_run_at(
                self.history
            )
        await self.eventloop.run_in_thread(
            self.data_dir.mkdir, 0o700, True, True
        )
        await self.eventloop.run_in_thread(self.data_dir.chmod, 0o700)
        for directory in (self.archive_dir, self.snapshot_dir, self.download_dir):
            await self.eventloop.run_in_thread(
                directory.mkdir, 0o700, True, True
            )
            await self.eventloop.run_in_thread(directory.chmod, 0o700)
        self.file_manager.register_directory(
            DOWNLOAD_ROOT_NAME, str(self.download_dir), False
        )
        await self.eventloop.run_in_thread(
            _prune_downloads_sync, self.download_dir
        )
        await self.eventloop.run_in_thread(
            self.bypy_home_dir.mkdir, 0o700, True, True
        )
        await self.eventloop.run_in_thread(self.bypy_home_dir.chmod, 0o700)
        bypy_authorized = await self.eventloop.run_in_thread(
            self._bypy_token_is_valid
        )
        self.credentials["bypy_authorized"] = bypy_authorized
        if bypy_authorized:
            self.oauth = {
                "state": "authorized",
                "message": "已保存百度 bypy 命令行授权",
            }
        if self.provider == "baidu" and self.credentials.get("web_authorized"):
            self.web_login = {
                "state": "authorized",
                "message": "已保存百度网页登录会话",
            }
        self.component_initialized = True
        self._restart_auto_backup_scheduler()

    def _root_map(self) -> Dict[str, pathlib.Path]:
        result: Dict[str, pathlib.Path] = {}
        for root_name in self.allowed_root_names:
            directory = self.file_manager.get_directory(root_name)
            if directory:
                path = pathlib.Path(directory).resolve()
                if path.is_dir():
                    result[root_name] = path
        return result

    def _public_oauth(self) -> Dict[str, Any]:
        return {
            key: value for key, value in self.oauth.items()
            if key not in {"device_code"}
        }

    def _public_web_login(self, include_screenshot: bool = False) -> Dict[str, Any]:
        hidden = {"screenshot_data"} if not include_screenshot else set()
        return {
            key: value for key, value in self.web_login.items()
            if key not in hidden
        }

    def _bypy_token_is_valid(self) -> bool:
        token = _read_json(self.bypy_token_path, {})
        return bool(
            isinstance(token, dict) and
            isinstance(token.get("access_token"), str) and
            token["access_token"]
        )

    def _is_configured(self) -> bool:
        if self.provider == "github":
            return bool(
                self.github_owner and self.github_repo and
                self.github_branch and self.github_path and
                self.credentials.get("github_token")
            )
        if self.auth_mode == "web_password":
            return bool(
                self.credentials.get("web_username") and
                self.credentials.get("web_password")
            )
        return self.bypy_executable.is_file()

    def _is_authorized(self) -> bool:
        if self.provider == "github":
            return bool(self.credentials.get("github_token"))
        if self.auth_mode == "web_password":
            return bool(self.credentials.get("web_authorized"))
        return bool(
            self.credentials.get("bypy_authorized") and
            self.bypy_token_path.is_file()
        )

    def _backup_is_running(self) -> bool:
        return (
            (self.backup_task is not None and not self.backup_task.done()) or
            self.download_in_progress
        )

    def _require_backup_idle(self, operation: str) -> None:
        if self._backup_is_running():
            raise self.server.error(
                f"Wait for the active cloud backup before {operation}", 409
            )

    def _auto_backup_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.auto_backup_enabled,
            "mode": self.auto_backup_mode,
            "interval_days": self.auto_backup_interval_days,
            "startup_delay_minutes": self.auto_backup_startup_delay_minutes,
            "next_run_at": self.auto_backup_next_run_at,
            "last_run_at": self.auto_backup_last_run_at,
            "last_success_at": _latest_success_at(self.history),
            "message": self.auto_backup_message,
        }

    def _status(self) -> Dict[str, Any]:
        roots = self._root_map()
        return {
            "version": CLOUD_BACKUP_VERSION,
            "ready": bool(roots),
            "provider": self.provider,
            "auth_mode": self.auth_mode,
            "configured": self._is_configured(),
            "authorized": self._is_authorized(),
            "available_roots": list(roots),
            "oauth": self._public_oauth(),
            "web_login": self._public_web_login(),
            "active_job": self.active_job,
            "download_in_progress": self.download_in_progress,
            "auto_backup": self._auto_backup_status(),
            "history_updated_at": _latest_history_at(self.history),
        }

    async def _handle_status(self, web_request: WebRequest) -> Dict[str, Any]:
        return self._status()

    async def _handle_config(self, web_request: WebRequest) -> Dict[str, Any]:
        if web_request.get_request_type() == RequestType.POST:
            args = web_request.get_args()
            self._require_backup_idle("changing cloud backup configuration")

            provider = self.provider
            auth_mode = self.auth_mode
            web_remote_path = self.web_remote_path
            bypy_remote_path = self.bypy_remote_path
            github_owner = self.github_owner
            github_repo = self.github_repo
            github_branch = self.github_branch
            github_path = self.github_path
            selected_roots = list(self.selected_roots)
            auto_backup_enabled = self.auto_backup_enabled
            auto_backup_mode = self.auto_backup_mode
            auto_backup_interval_days = self.auto_backup_interval_days
            auto_backup_startup_delay_minutes = (
                self.auto_backup_startup_delay_minutes
            )
            credentials = dict(self.credentials)
            web_login = dict(self.web_login)
            schedule_changed = False

            if "provider" in args:
                provider = str(args["provider"]).strip()
                if provider not in PROVIDERS:
                    raise self.server.error("Invalid cloud backup provider")
                if provider != self.provider:
                    if self.web_task is not None and not self.web_task.done():
                        raise self.server.error(
                            "Finish or cancel web login before changing provider",
                            409,
                        )
                    schedule_changed = True
                    web_login = {"state": "idle"}
            if "auth_mode" in args:
                auth_mode = str(args["auth_mode"]).strip()
                if auth_mode not in AUTH_MODES:
                    raise self.server.error("Invalid cloud backup auth mode")
                if auth_mode != self.auth_mode:
                    schedule_changed = True
                    web_login = {"state": "idle"}
            if (
                (provider != self.provider or auth_mode != self.auth_mode) and
                self.oauth_task is not None and not self.oauth_task.done()
            ):
                raise self.server.error(
                    "Finish or revoke bypy authorization before changing mode",
                    409,
                )
            web_username = args.get("web_username")
            web_password = args.get("web_password")
            github_token = args.get("github_token")
            web_credentials_changed = False
            if web_username is not None:
                web_username = str(web_username).strip()
                if not 3 <= len(web_username) <= 128:
                    raise self.server.error("Invalid Baidu username")
                web_credentials_changed = (
                    web_username != credentials.get("web_username")
                )
                credentials["web_username"] = web_username
            if web_password is not None:
                web_password = str(web_password)
                if not 6 <= len(web_password) <= 256:
                    raise self.server.error("Invalid Baidu password")
                web_credentials_changed = (
                    web_credentials_changed or
                    web_password != credentials.get("web_password")
                )
                credentials["web_password"] = web_password
            if web_credentials_changed:
                credentials["web_authorized"] = False
                web_login = {"state": "idle"}
                schedule_changed = True
            if github_token is not None:
                github_token = str(github_token).strip()
                if not 20 <= len(github_token) <= 512:
                    raise self.server.error("Invalid GitHub token")
                schedule_changed = (
                    schedule_changed or
                    github_token != credentials.get("github_token")
                )
                credentials["github_token"] = github_token

            if "web_remote_path" in args:
                try:
                    value = _validate_web_remote_path(
                        str(args["web_remote_path"])
                    )
                except ValueError as exc:
                    raise self.server.error(str(exc)) from exc
                schedule_changed = schedule_changed or value != self.web_remote_path
                web_remote_path = value
            if "bypy_remote_path" in args:
                try:
                    value = _validate_web_remote_path(
                        str(args["bypy_remote_path"])
                    )
                except ValueError as exc:
                    raise self.server.error(str(exc)) from exc
                schedule_changed = schedule_changed or value != self.bypy_remote_path
                bypy_remote_path = value
            for key, validator, attribute in (
                ("github_owner", lambda value: _validate_github_name(
                    value, "github_owner"), "github_owner"),
                ("github_repo", lambda value: _validate_github_name(
                    value, "github_repo"), "github_repo"),
                ("github_branch", _validate_github_branch, "github_branch"),
                ("github_path", _validate_github_path, "github_path"),
            ):
                if key not in args:
                    continue
                try:
                    value = validator(str(args[key]))
                except ValueError as exc:
                    raise self.server.error(str(exc)) from exc
                schedule_changed = (
                    schedule_changed or value != getattr(self, attribute)
                )
                if attribute == "github_owner":
                    github_owner = value
                elif attribute == "github_repo":
                    github_repo = value
                elif attribute == "github_branch":
                    github_branch = value
                else:
                    github_path = value
            if "selected_roots" in args:
                requested = web_request.get_list("selected_roots")
                invalid = set(requested) - set(self.allowed_root_names)
                if invalid or not requested:
                    raise self.server.error("Invalid or empty backup root selection")
                schedule_changed = (
                    schedule_changed or requested != self.selected_roots
                )
                selected_roots = requested
            if "auto_backup_enabled" in args:
                value = web_request.get_boolean("auto_backup_enabled")
                schedule_changed = (
                    schedule_changed or value != self.auto_backup_enabled
                )
                auto_backup_enabled = value
            if "auto_backup_mode" in args:
                value = web_request.get_str("auto_backup_mode").strip()
                if value not in AUTO_BACKUP_MODES:
                    raise self.server.error("Invalid automatic backup mode")
                schedule_changed = schedule_changed or value != self.auto_backup_mode
                auto_backup_mode = value
            if "auto_backup_interval_days" in args:
                value = web_request.get_int("auto_backup_interval_days")
                if not MIN_AUTO_INTERVAL_DAYS <= value <= MAX_AUTO_INTERVAL_DAYS:
                    raise self.server.error(
                        "Automatic backup interval must be 1-365 days"
                    )
                schedule_changed = (
                    schedule_changed or value != self.auto_backup_interval_days
                )
                auto_backup_interval_days = value
            if "auto_backup_startup_delay_minutes" in args:
                value = web_request.get_int(
                    "auto_backup_startup_delay_minutes"
                )
                if not MIN_STARTUP_DELAY_MINUTES <= value <= MAX_STARTUP_DELAY_MINUTES:
                    raise self.server.error(
                        "Automatic backup startup delay must be 1-1440 minutes"
                    )
                schedule_changed = (
                    schedule_changed or
                    value != self.auto_backup_startup_delay_minutes
                )
                auto_backup_startup_delay_minutes = value

            settings = {
                "provider": provider,
                "auth_mode": auth_mode,
                "web_remote_path": web_remote_path,
                "bypy_remote_path": bypy_remote_path,
                "github_owner": github_owner,
                "github_repo": github_repo,
                "github_branch": github_branch,
                "github_path": github_path,
                "selected_roots": selected_roots,
                "auto_backup_enabled": auto_backup_enabled,
                "auto_backup_mode": auto_backup_mode,
                "auto_backup_interval_days": auto_backup_interval_days,
                "auto_backup_startup_delay_minutes": (
                    auto_backup_startup_delay_minutes
                ),
            }
            credentials_changed = credentials != self.credentials
            old_credentials = dict(self.credentials)
            credentials_written = False
            try:
                if credentials_changed:
                    await self.eventloop.run_in_thread(
                        _atomic_json_write,
                        self.credentials_path,
                        credentials,
                        0o600,
                    )
                    credentials_written = True
                await self.eventloop.run_in_thread(
                    _atomic_json_write, self.settings_path, settings, 0o600
                )
            except Exception:
                if credentials_written:
                    try:
                        await self.eventloop.run_in_thread(
                            _atomic_json_write,
                            self.credentials_path,
                            old_credentials,
                            0o600,
                        )
                    except Exception:
                        logging.exception(
                            "Failed to roll back cloud backup credentials"
                        )
                raise

            self.provider = provider
            self.auth_mode = auth_mode
            self.web_remote_path = web_remote_path
            self.bypy_remote_path = bypy_remote_path
            self.github_owner = github_owner
            self.github_repo = github_repo
            self.github_branch = github_branch
            self.github_path = github_path
            self.selected_roots = selected_roots
            self.auto_backup_enabled = auto_backup_enabled
            self.auto_backup_mode = auto_backup_mode
            self.auto_backup_interval_days = auto_backup_interval_days
            self.auto_backup_startup_delay_minutes = (
                auto_backup_startup_delay_minutes
            )
            self.credentials = credentials
            self.web_login = web_login
            self.server.send_event(
                "cloud_backup:status_changed", self._status()
            )
            if schedule_changed:
                self._restart_auto_backup_scheduler()
        root_map = self._root_map()
        return {
            "provider": self.provider,
            "auth_mode": self.auth_mode,
            "web_username": self.credentials.get("web_username", ""),
            "has_web_password": bool(self.credentials.get("web_password")),
            "has_github_token": bool(self.credentials.get("github_token")),
            "credential_source": (
                "moonraker_secrets"
                if self.provider == "github" and self.config_github_token
                else "fluidd"
            ),
            "web_remote_path": self.web_remote_path,
            "bypy_remote_path": self.bypy_remote_path,
            "bypy_app_root": "/apps/bypy",
            "bypy_executable_available": self.bypy_executable.is_file(),
            "github_owner": self.github_owner,
            "github_repo": self.github_repo,
            "github_branch": self.github_branch,
            "github_path": self.github_path,
            "web_browser_configured": bool(self.web_browser_executable),
            "retain_local": self.retain_local,
            "auto_backup_enabled": self.auto_backup_enabled,
            "auto_backup_mode": self.auto_backup_mode,
            "auto_backup_interval_days": self.auto_backup_interval_days,
            "auto_backup_startup_delay_minutes": (
                self.auto_backup_startup_delay_minutes
            ),
            "available_roots": [
                {"name": name}
                for name in root_map
            ],
            "selected_roots": [
                name for name in self.selected_roots if name in root_map
            ],
        }

    async def _handle_oauth_device(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        self._require_backup_idle("starting bypy authorization")
        if self.provider != "baidu" or self.auth_mode != "bypy":
            raise self.server.error("bypy authorization is not selected", 409)
        if not self.bypy_executable.is_file():
            raise self.server.error("bypy is not installed", 503)
        if self.oauth_task is not None and not self.oauth_task.done():
            return self._public_oauth()
        self.oauth = {
            "state": "starting",
            "message": "正在启动百度 bypy 授权",
        }
        self.oauth_task = self.eventloop.create_task(self._run_bypy_authorization())
        self.server.send_event(
            "cloud_backup:status_changed", self._status()
        )
        return self._public_oauth()

    async def _handle_oauth_status(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        return self._public_oauth()

    async def _handle_oauth_verify(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        code = web_request.get_str("authorization_code").strip()
        if not 8 <= len(code) <= 256 or any(char.isspace() for char in code):
            raise self.server.error("Invalid Baidu authorization code")
        process = self.bypy_auth_process
        if (
            process is None or process.returncode is not None or
            process.stdin is None or self.oauth.get("state") != "pending"
        ):
            raise self.server.error("No bypy authorization is waiting", 409)
        process.stdin.write(code.encode("utf-8") + b"\n")
        await process.stdin.drain()
        self.oauth = {
            "state": "authorizing",
            "message": "正在验证百度授权码",
        }
        self.server.send_event("cloud_backup:status_changed", self._status())
        return self._public_oauth()

    async def _handle_oauth_revoke(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        self._require_backup_idle("revoking cloud authorization")
        task = self.oauth_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        process = self.bypy_auth_process
        if process is not None and process.returncode is None:
            process.terminate()
            await process.wait()
        self.credentials["bypy_authorized"] = False
        await self.eventloop.run_in_thread(
            _remove_tree, self.bypy_config_dir
        )
        self.oauth = {"state": "idle"}
        await self._save_credentials()
        self._restart_auto_backup_scheduler()
        self.server.send_event("cloud_backup:status_changed", self._status())
        return {"revoked": True}

    async def _handle_web_login(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        self._require_backup_idle("starting web login")
        if self.provider != "baidu" or self.auth_mode != "web_password":
            raise self.server.error("Web login is unavailable for this provider", 409)
        if self.web_task is not None and not self.web_task.done():
            return self._public_web_login(include_screenshot=True)
        username = str(self.credentials.get("web_username", ""))
        password = str(self.credentials.get("web_password", ""))
        if not username or not password:
            raise self.server.error("Configure Baidu username and password first")
        if not self.web_worker_path.is_file():
            self.web_login = {
                "state": "environment_missing",
                "message": "cloud_backup_web.py is not installed",
            }
            return self._public_web_login()
        self.credentials["web_authorized"] = False
        await self._save_credentials()
        self.web_login = {
            "state": "starting",
            "message": "正在启动百度网页登录",
        }
        self.web_task = self.eventloop.create_task(
            self._run_web_login(username, password)
        )
        self.server.send_event("cloud_backup:status_changed", self._status())
        return self._public_web_login()

    async def _handle_web_status(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        return self._public_web_login(include_screenshot=True)

    async def _handle_web_verify(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        if self.web_login.get("challenge_type") == "qr":
            raise self.server.error("QR login does not accept a verification code", 409)
        code = web_request.get_str("verification_code").strip()
        if not 4 <= len(code) <= 12:
            raise self.server.error("Verification code must contain 4-12 characters")
        if (
            self.web_process is None or self.web_process.stdin is None or
            self.web_login.get("state") != "verification_required"
        ):
            raise self.server.error("No web verification is waiting", 409)
        command = json.dumps(
            {"verification_code": code}, ensure_ascii=False
        ).encode("utf-8") + b"\n"
        self.web_process.stdin.write(command)
        await self.web_process.stdin.drain()
        self.web_login = {"state": "signing_in", "message": "正在验证"}
        self.server.send_event("cloud_backup:status_changed", self._status())
        return self._public_web_login()

    async def _handle_web_logout(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        self._require_backup_idle("logging out of cloud storage")
        task = self.web_task
        process = self.web_process
        if task is not None and not task.done():
            task.cancel()
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5.)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if task is not None and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.credentials["web_authorized"] = False
        await self._save_credentials()
        await self.eventloop.run_in_thread(
            _remove_tree, self._active_web_profile_dir()
        )
        self.web_login = {"state": "idle"}
        self._restart_auto_backup_scheduler()
        self.server.send_event("cloud_backup:status_changed", self._status())
        return {"logged_out": True}

    async def _handle_github_logout(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        self._require_backup_idle("removing the GitHub token")
        self.credentials.pop("github_token", None)
        await self._save_credentials()
        self._restart_auto_backup_scheduler()
        self.server.send_event("cloud_backup:status_changed", self._status())
        return {"logged_out": True}

    def _active_web_profile_dir(self) -> pathlib.Path:
        return self.baidu_web_profile_dir

    def _web_profile_dir(self, provider: str) -> pathlib.Path:
        return self.baidu_web_profile_dir

    def _web_worker_command(self, action: str, provider: str) -> List[str]:
        command = [
            sys.executable, str(self.web_worker_path), action,
            "--provider", provider,
        ]
        if self.web_browser_executable:
            command.extend(["--browser-executable", self.web_browser_executable])
        return command

    async def _start_web_process(
        self, action: str, request: Dict[str, Any],
        provider: Optional[str] = None
    ) -> asyncio.subprocess.Process:
        selected_provider = provider or self.provider
        process = await asyncio.create_subprocess_exec(
            *self._web_worker_command(action, selected_provider),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert process.stdin is not None
        process.stdin.write(
            json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
        )
        await process.stdin.drain()
        return process

    async def _run_web_login(self, username: str, password: str) -> None:
        terminal_state = False
        try:
            self.web_process = await self._start_web_process(
                "login",
                {
                    "profile_dir": str(self.baidu_web_profile_dir),
                    "username": username,
                    "password": password,
                    "login_mode": "password",
                },
                "baidu",
            )
            assert self.web_process.stdout is not None
            while True:
                line = await self.web_process.stdout.readline()
                if not line:
                    break
                message = json.loads(line.decode("utf-8"))
                if not isinstance(message, dict):
                    continue
                state = str(message.get("state", "error"))
                self.web_login = {
                    key: value for key, value in message.items()
                    if key in {
                        "state", "message", "challenge_type", "screenshot_data"
                    }
                }
                if state == "authorized":
                    self.credentials["web_authorized"] = True
                    await self._save_credentials()
                    self._restart_auto_backup_scheduler()
                    terminal_state = True
                elif state in {
                    "error", "environment_missing", "manual_verification"
                }:
                    terminal_state = True
                self.server.send_event(
                    "cloud_backup:status_changed", self._status()
                )
            return_code = await self.web_process.wait()
            if not terminal_state and return_code:
                self.web_login = {
                    "state": "error",
                    "message": "百度网页登录工作进程异常退出",
                }
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Baidu web login failed")
            self.web_login = {"state": "error", "message": str(exc)}
        finally:
            self.web_process = None
            self.server.send_event("cloud_backup:status_changed", self._status())

    async def _run_bypy_authorization(self) -> None:
        process: Optional[asyncio.subprocess.Process] = None
        try:
            await self.eventloop.run_in_thread(
                self.bypy_home_dir.mkdir, 0o700, True, True
            )
            env = dict(os.environ)
            env["HOME"] = str(self.bypy_home_dir)
            env["PYTHONUNBUFFERED"] = "1"
            process = await asyncio.create_subprocess_exec(
                str(self.bypy_executable),
                "--config-dir", str(self.bypy_config_dir),
                "info",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            self.bypy_auth_process = process
            assert process.stdout is not None
            authorization_deadline: Optional[float] = None
            while True:
                if authorization_deadline is None:
                    line = await process.stdout.readline()
                else:
                    remaining = authorization_deadline - time.monotonic()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    try:
                        line = await asyncio.wait_for(
                            process.stdout.readline(), min(1., remaining)
                        )
                    except asyncio.TimeoutError:
                        authorized = await self.eventloop.run_in_thread(
                            self._bypy_token_is_valid
                        )
                        if authorized:
                            if process.returncode is None:
                                process.terminate()
                                await process.wait()
                            break
                        continue
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                match = BYPY_AUTH_URL_RE.search(text)
                if match:
                    authorization_deadline = time.monotonic() + 10 * 60
                    self.oauth = {
                        "state": "pending",
                        "verification_url": match.group(0),
                        "expires_at": time.time() + 10 * 60,
                        "message": "请在百度页面授权后粘贴授权码",
                    }
                    self.server.send_event(
                        "cloud_backup:status_changed", self._status()
                    )
            return_code = await process.wait()
            authorized = await self.eventloop.run_in_thread(
                self._bypy_token_is_valid
            )
            if authorized:
                self.credentials["bypy_authorized"] = True
                await self._save_credentials()
                self.oauth = {
                    "state": "authorized",
                    "message": "百度 bypy 命令行授权成功",
                }
                self._restart_auto_backup_scheduler()
            else:
                self.credentials["bypy_authorized"] = False
                self.oauth = {
                    "state": "error",
                    "message": "百度 bypy 授权失败，请重新开始授权",
                }
        except asyncio.TimeoutError:
            if process is not None and process.returncode is None:
                process.terminate()
                await process.wait()
            self.credentials["bypy_authorized"] = False
            self.oauth = {
                "state": "expired",
                "message": "百度 bypy 授权等待已超过 10 分钟，请重新开始",
            }
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        except Exception as exc:
            logging.exception("Baidu bypy authorization failed")
            self.oauth = {
                "state": "error",
                "message": "百度 bypy 授权进程异常，请检查 Moonraker 日志",
            }
        finally:
            self.bypy_auth_process = None
            self.server.send_event(
                "cloud_backup:status_changed", self._status()
            )

    async def _save_credentials(self) -> None:
        await self.eventloop.run_in_thread(
            _atomic_json_write, self.credentials_path, self.credentials, 0o600
        )

    def _restart_auto_backup_scheduler(self) -> None:
        if (
            self.auto_backup_task is not None and
            not self.auto_backup_task.done()
        ):
            self.auto_backup_task.cancel()
        self.auto_backup_task = None
        self.auto_backup_next_run_at = None
        self.auto_backup_message = ""
        if self.component_initialized and self.auto_backup_enabled:
            self.auto_backup_task = self.eventloop.create_task(
                self._auto_backup_loop()
            )

    def _notify_auto_backup_status(self) -> None:
        self.server.send_event(
            "cloud_backup:status_changed", self._status()
        )

    async def _auto_backup_loop(self) -> None:
        startup_ready_at = (
            self.started_at + self.auto_backup_startup_delay_minutes * 60
        )
        retry_not_before = 0.0
        try:
            while self.auto_backup_enabled:
                if (
                    self.auto_backup_mode == "startup" and
                    self.auto_backup_startup_completed
                ):
                    self.auto_backup_next_run_at = None
                    self.auto_backup_message = "本次开机自动备份已完成"
                    self._notify_auto_backup_status()
                    return
                not_before = max(startup_ready_at, retry_not_before)
                if self.auto_backup_mode == "interval":
                    due_at = _next_interval_run_at(
                        self.history,
                        self.auto_backup_interval_days,
                        not_before,
                    )
                else:
                    due_at = not_before
                self.auto_backup_next_run_at = due_at
                self._notify_auto_backup_status()
                delay = due_at - time.time()
                if delay > 0:
                    await asyncio.sleep(min(delay, AUTO_RETRY_SECONDS))
                    continue
                if self.backup_task is not None and not self.backup_task.done():
                    self.auto_backup_message = "等待当前备份任务完成"
                    retry_not_before = time.time() + AUTO_BUSY_RETRY_SECONDS
                    continue
                if not self._is_authorized():
                    self.auto_backup_message = "等待当前云端备份目标授权"
                    retry_not_before = time.time() + AUTO_RETRY_SECONDS
                    continue
                root_map = self._root_map()
                roots = [
                    name for name in self.selected_roots if name in root_map
                ]
                if not roots:
                    self.auto_backup_message = "等待可用的备份目录"
                    retry_not_before = time.time() + AUTO_RETRY_SECONDS
                    continue
                if self.auto_backup_mode == "startup":
                    reason = "开机延迟自动备份：Moonraker 启动后按计划上传打印机配置"
                else:
                    reason = (
                        "定期自动备份：距上次成功上传已达到 "
                        f"{self.auto_backup_interval_days} 天"
                    )
                job = self._start_backup_job(reason, roots, "automatic")
                self.auto_backup_last_run_at = job["created_at"]
                self.auto_backup_next_run_at = None
                self.auto_backup_message = "自动备份任务已启动"
                self._notify_auto_backup_status()
                task = self.backup_task
                if task is not None:
                    await asyncio.shield(task)
                record = next((
                    item for item in self.history
                    if item.get("job_id") == job["job_id"]
                ), None)
                if record is not None and record.get("state") == "success":
                    self.auto_backup_message = "最近一次自动备份已完成"
                    retry_not_before = 0.0
                    if self.auto_backup_mode == "startup":
                        self.auto_backup_next_run_at = None
                        self._notify_auto_backup_status()
                        return
                else:
                    self.auto_backup_message = "自动备份失败，稍后重试"
                    retry_not_before = time.time() + AUTO_RETRY_SECONDS
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Automatic cloud backup scheduler failed")
            self.auto_backup_message = "自动备份调度异常"
            self.auto_backup_next_run_at = None
            self._notify_auto_backup_status()

    def _start_backup_job(
        self, reason: str, roots: List[str], trigger: str
    ) -> Dict[str, Any]:
        now = time.time()
        job_id = uuid.uuid4().hex[:12]
        upload_context = {
            "provider": self.provider,
            "auth_mode": self.auth_mode,
            "web_remote_path": self.web_remote_path,
            "bypy_remote_path": self.bypy_remote_path,
            "github_owner": self.github_owner,
            "github_repo": self.github_repo,
            "github_branch": self.github_branch,
            "github_path": self.github_path,
        }
        self.active_job = {
            "job_id": job_id,
            "state": "queued",
            "progress": 0,
            "stage": "queued",
            "reason": reason,
            "roots": roots,
            "created_at": now,
            "trigger": trigger,
            "provider": self.provider,
            "auth_mode": self.auth_mode,
        }
        self.backup_task = self.eventloop.create_task(
            self._run_backup(job_id, reason, roots, now, upload_context)
        )
        self._notify_job()
        return self.active_job

    async def _handle_backup(self, web_request: WebRequest) -> Dict[str, Any]:
        if self.backup_task is not None and not self.backup_task.done():
            raise self.server.error("A cloud backup job is already running", 409)
        reason = web_request.get_str("reason").strip()
        if not MIN_REASON_LENGTH <= len(reason) <= MAX_REASON_LENGTH:
            raise self.server.error(
                f"Backup reason must be {MIN_REASON_LENGTH}-{MAX_REASON_LENGTH} characters"
            )
        roots = web_request.get_list("roots", self.selected_roots)
        root_map = self._root_map()
        if not roots or set(roots) - set(root_map):
            raise self.server.error("Invalid or empty backup root selection")
        if not self._is_authorized():
            raise self.server.error("Cloud backup authorization is required", 401)
        return {"job": self._start_backup_job(reason, roots, "manual")}

    async def _run_backup(
        self, job_id: str, reason: str, root_names: List[str], created_at: float,
        upload_context: Dict[str, str]
    ) -> None:
        assert self.active_job is not None
        stamp = datetime.fromtimestamp(created_at).strftime("%Y%m%d_%H%M%S")
        archive_path = self.archive_dir.joinpath(f"{stamp}_{job_id}.tar.gz")
        snapshot_path = self.snapshot_dir.joinpath(f"{stamp}_{job_id}")
        try:
            self._update_job("archiving", 5, "creating_snapshot")
            root_map = self._root_map()
            roots = [(name, root_map[name]) for name in root_names]
            archive_info = await self.eventloop.run_in_thread(
                _prepare_backup_sync,
                snapshot_path,
                archive_path,
                roots,
                reason,
                created_at,
                job_id,
            )
            self.active_job.update(archive_info)
            self.active_job["archive_name"] = archive_path.name
            provider = upload_context["provider"]
            uses_readable_directory = (
                provider == "baidu" and upload_context["auth_mode"] == "bypy"
            )
            upload_total_bytes = (
                archive_info["snapshot_size"]
                if uses_readable_directory else archive_info["archive_size"]
            )
            self.active_job.update({
                "uploaded_bytes": 0,
                "upload_total_bytes": upload_total_bytes,
                "upload_progress": 0,
                "uploaded_files": 0,
            })
            self._update_job("uploading", 20, "preparing_upload")
            if provider == "github":
                remote_file = self._remote_file_path(
                    archive_path.name, created_at, upload_context
                )
                cloud_info = await self._upload_archive_github(
                    archive_path, remote_file, upload_context
                )
            elif upload_context["auth_mode"] == "web_password":
                remote_file = self._remote_file_path(
                    archive_path.name, created_at, upload_context
                )
                cloud_info = await self._upload_archive_web(
                    archive_path, remote_file, provider
                )
            else:
                remote_directory = self._remote_directory_path(
                    created_at, job_id, upload_context
                )
                cloud_info = await self._upload_directory_bypy(
                    snapshot_path, remote_directory
                )
            self.active_job.update(cloud_info)
            self.active_job.update({
                "uploaded_bytes": upload_total_bytes,
                "upload_total_bytes": upload_total_bytes,
                "upload_progress": 100,
            })
            self.active_job["finished_at"] = time.time()
            self._update_job("success", 100, "complete")
        except asyncio.CancelledError:
            if (
                self.active_job is not None and
                self.active_job.get("job_id") == job_id
            ):
                self.active_job.update({
                    "state": "failed",
                    "stage": "cancelled",
                    "error": "Moonraker stopped before the backup completed",
                    "finished_at": time.time(),
                })
                self._notify_job()
            raise
        except Exception as exc:
            logging.exception("Cloud backup job %s failed", job_id)
            self.active_job["state"] = "failed"
            self.active_job["stage"] = "failed"
            self.active_job["error"] = str(exc)
            self.active_job["finished_at"] = time.time()
            self._notify_job()
        finally:
            if snapshot_path.exists():
                try:
                    await self.eventloop.run_in_thread(
                        _remove_tree, snapshot_path
                    )
                except Exception:
                    logging.exception(
                        "Failed to remove cloud backup snapshot %s", job_id
                    )
            if self.active_job is not None:
                record = dict(self.active_job)
                self.history.insert(0, record)
                self.history = self.history[:MAX_HISTORY]
                automatic_success = (
                    record.get("trigger") == "automatic" and
                    record.get("state") == "success"
                )
                if automatic_success and self.auto_backup_mode == "startup":
                    self.auto_backup_startup_completed = True
                self.active_job = None
                try:
                    await self.eventloop.run_in_thread(
                        _atomic_json_write,
                        self.history_path,
                        self.history,
                        0o600,
                    )
                except Exception:
                    logging.exception(
                        "Failed to persist cloud backup history for job %s",
                        job_id,
                    )
                try:
                    await self._prune_archives()
                except Exception:
                    logging.exception("Failed to prune local cloud backup archives")
                finally:
                    should_refresh_schedule = (
                        self.auto_backup_enabled and
                        (
                            record.get("trigger") == "manual" or
                            automatic_success
                        )
                    )
                    if should_refresh_schedule:
                        self._restart_auto_backup_scheduler()
                    self.server.send_event(
                        "cloud_backup:status_changed", self._status()
                    )

    def _remote_file_path(
        self, filename: str, created_at: float, context: Dict[str, str]
    ) -> str:
        created = datetime.fromtimestamp(created_at)
        provider = context["provider"]
        if provider == "github":
            base_path = context["github_path"].strip("/")
        else:
            base_path = (
                context["web_remote_path"]
                if context["auth_mode"] == "web_password"
                else context["bypy_remote_path"]
            )
        return (
            f"{base_path}/backups/{created:%Y}/{created:%Y-%m}/"
            f"{created:%Y-%m-%d}/{filename}"
        )

    def _remote_directory_path(
        self, created_at: float, job_id: str, context: Dict[str, str]
    ) -> str:
        created = datetime.fromtimestamp(created_at)
        base_path = context["bypy_remote_path"].rstrip("/")
        return (
            f"{base_path}/backups/{created:%Y}/{created:%Y-%m}/"
            f"{created:%Y-%m-%d}/{created:%Y%m%d_%H%M%S}_{job_id}"
        )

    async def _upload_archive_web(
        self, archive_path: pathlib.Path, remote_file: str, provider: str
    ) -> Dict[str, Any]:
        if self.web_task is not None and not self.web_task.done():
            raise self.server.error("Cloud web login is still running", 409)
        if self.web_process is not None:
            raise self.server.error("Cloud web worker is busy", 409)
        remote_dir = remote_file.rsplit("/", 1)[0]
        process = await self._start_web_process(
            "upload",
            {
                "profile_dir": str(self._web_profile_dir(provider)),
                "archive_path": str(archive_path),
                "remote_dir": remote_dir,
            },
            provider,
        )
        self.web_process = process
        uploaded: Optional[Dict[str, Any]] = None
        last_error = "Baidu web upload failed"
        try:
            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                message = json.loads(line.decode("utf-8"))
                if not isinstance(message, dict):
                    continue
                state = str(message.get("state", ""))
                if state == "uploading":
                    self._update_job(
                        "uploading",
                        max(20, min(95, int(message.get("progress", 25)))),
                        "uploading",
                    )
                elif state == "uploaded":
                    uploaded = {
                        "remote_path": message.get("remote_path", remote_file),
                        "upload_mode": "baidu_web",
                    }
                elif state in {"error", "environment_missing"}:
                    last_error = str(message.get("message", last_error))
            return_code = await process.wait()
            if uploaded is None or return_code:
                if "expired" in last_error.lower():
                    self.credentials["web_authorized"] = False
                    self.web_login = {
                        "state": "expired",
                        "message": "网页登录会话已过期，请重新登录",
                    }
                    await self._save_credentials()
                raise self.server.error(last_error, 502)
            return uploaded
        finally:
            self.web_process = None

    async def _upload_archive_github(
        self, archive_path: pathlib.Path, remote_file: str,
        context: Dict[str, str]
    ) -> Dict[str, Any]:
        token = str(self.credentials.get("github_token", ""))
        if not token:
            raise self.server.error("GitHub token is not configured", 401)
        try:
            content = await self.eventloop.run_in_thread(
                _encode_file_base64, archive_path, MAX_GITHUB_CONTENT_SIZE
            )
        except ValueError as exc:
            raise self.server.error(str(exc), 413) from exc
        owner = quote(context["github_owner"], safe="")
        repo = quote(context["github_repo"], safe="")
        path = quote(remote_file, safe="/")
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        body = json.dumps({
            "message": f"backup: add {archive_path.name}",
            "content": content,
            "branch": context["github_branch"],
        }).encode("utf-8")
        created = await self._request_json(
            "PUT", url, body,
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "Moonraker-Cloud-Backup",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=180., service="GitHub",
        )
        content_info = created.get("content", {})
        if not isinstance(content_info, dict):
            content_info = {}
        return {
            "remote_path": remote_file,
            "remote_url": content_info.get("html_url"),
            "upload_mode": "github_contents",
        }

    async def _run_bypy_command(
        self, *arguments: str, timeout: float = 900.
    ) -> Tuple[int, str]:
        if not self.bypy_executable.is_file():
            raise self.server.error("bypy is not installed", 503)
        env = dict(os.environ)
        env["HOME"] = str(self.bypy_home_dir)
        env["PYTHONUNBUFFERED"] = "1"
        process = await asyncio.create_subprocess_exec(
            str(self.bypy_executable),
            "--config-dir", str(self.bypy_config_dir),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        self.bypy_upload_process = process
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout)
            return process.returncode or 0, output.decode(
                "utf-8", errors="replace"
            )
        except asyncio.CancelledError:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5.)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            raise
        except asyncio.TimeoutError as exc:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5.)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            raise self.server.error("bypy command timed out", 504) from exc
        finally:
            self.bypy_upload_process = None

    async def _verify_bypy_remote_size(
        self, relative_remote: str, expected_size: int
    ) -> int:
        for attempt in range(12):
            meta_code, meta_output = await self._run_bypy_command(
                "-q", "meta", relative_remote, "$s", timeout=120.
            )
            sizes = [
                int(value) for value in
                re.findall(r"(?m)^\s*(\d+)\s*$", meta_output)
            ]
            if (
                not meta_code and not _bypy_final_error(meta_output) and
                sizes and sizes[-1] == expected_size
            ):
                return sizes[-1]
            if attempt < 11:
                await asyncio.sleep(5.)
        raise self.server.error(
            f"bypy could not verify remote size for {relative_remote}", 502
        )

    async def _upload_bypy_file(
        self, local_path: pathlib.Path, relative_remote: str
    ) -> int:
        return_code, output = await self._run_bypy_command(
            "-q", "upload", str(local_path), relative_remote, "overwrite"
        )
        command_error = _bypy_final_error(output)
        if return_code or command_error:
            if "auth" in output.lower() or "token" in output.lower():
                self.credentials["bypy_authorized"] = False
                self.oauth = {
                    "state": "expired",
                    "message": "百度 bypy 授权已失效，请重新授权",
                }
                await self._save_credentials()
            detail = command_error or "bypy upload failed"
            raise self.server.error(detail, 502)
        return await self._verify_bypy_remote_size(
            relative_remote, local_path.stat().st_size
        )

    async def _ensure_bypy_remote_directory(
        self, relative_remote: str, known_directories: Optional[set[str]] = None
    ) -> None:
        relative_remote = relative_remote.strip("/")
        if known_directories is None:
            known_directories = set()
        current_parts: List[str] = []
        for part in relative_remote.split("/"):
            current_parts.append(part)
            current = "/".join(current_parts)
            if current in known_directories:
                continue
            return_code, output = await self._run_bypy_command(
                "-q", "mkdir", current, timeout=120.
            )
            if not return_code and not _bypy_final_error(output):
                known_directories.add(current)
                continue
            meta_code, meta_output = await self._run_bypy_command(
                "-q", "meta", current, "$t", timeout=120.
            )
            if not meta_code and not _bypy_final_error(meta_output):
                known_directories.add(current)
                continue
            detail = _bypy_final_error(output) or "bypy mkdir failed"
            raise self.server.error(f"{detail}: {current}", 502)

    async def _upload_directory_bypy(
        self, snapshot_path: pathlib.Path, remote_directory: str
    ) -> Dict[str, Any]:
        relative_remote = remote_directory.strip("/")
        files = sorted(
            (item for item in snapshot_path.rglob("*") if item.is_file()),
            key=lambda item: (
                item.name == BYPY_DIRECTORY_MANIFEST_NAME,
                item.relative_to(snapshot_path).as_posix(),
            ),
        )
        directories = sorted(
            (item for item in snapshot_path.rglob("*") if item.is_dir()),
            key=lambda item: (
                len(item.relative_to(snapshot_path).parts),
                item.relative_to(snapshot_path).as_posix(),
            ),
        )
        total_size = sum(item.stat().st_size for item in files)
        total_files = len(files)
        known_directories: set[str] = set()
        await self._ensure_bypy_remote_directory(
            relative_remote, known_directories
        )
        for directory in directories:
            relative = directory.relative_to(snapshot_path).as_posix()
            await self._ensure_bypy_remote_directory(
                f"{relative_remote}/{relative}", known_directories
            )

        uploaded_bytes = 0
        uploaded_files = 0
        self._update_upload_progress(
            uploaded_bytes, total_size, "", uploaded_files, total_files
        )
        for local_path in files:
            relative = local_path.relative_to(snapshot_path).as_posix()
            stage = (
                "verifying_upload"
                if local_path.name == BYPY_DIRECTORY_MANIFEST_NAME
                else "uploading"
            )
            self._update_upload_progress(
                uploaded_bytes,
                total_size,
                relative,
                uploaded_files,
                total_files,
                stage,
            )
            await self._upload_bypy_file(
                local_path, f"{relative_remote}/{relative}"
            )
            uploaded_bytes += local_path.stat().st_size
            uploaded_files += 1
            self._update_upload_progress(
                uploaded_bytes,
                total_size,
                relative,
                uploaded_files,
                total_files,
                stage,
            )
        return {
            "remote_path": f"/apps/bypy/{relative_remote}",
            "remote_manifest": (
                f"/apps/bypy/{relative_remote}/"
                f"{BYPY_DIRECTORY_MANIFEST_NAME}"
            ),
            "upload_mode": "bypy_cli_directory",
            "remote_size": total_size,
            "uploaded_files": uploaded_files,
            "upload_total_files": total_files,
        }

    def _history_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        for job in self.history:
            if job.get("job_id") == job_id:
                return job
        return None

    def _local_archive_for_job(
        self, job: Dict[str, Any]
    ) -> Optional[pathlib.Path]:
        archive_name = job.get("archive_name")
        if (
            not isinstance(archive_name, str) or
            pathlib.Path(archive_name).name != archive_name or
            not archive_name.endswith(".tar.gz")
        ):
            return None
        archive_path = self.archive_dir.joinpath(archive_name)
        return archive_path if archive_path.is_file() else None

    def _job_download_available(self, job: Dict[str, Any]) -> bool:
        if job.get("state") != "success":
            return False
        if self._local_archive_for_job(job) is not None:
            return True
        remote_path = job.get("remote_path")
        return bool(
            job.get("upload_mode") == "bypy_cli_directory" and
            isinstance(remote_path, str) and
            remote_path.startswith("/apps/bypy/")
        )

    def _public_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(job)
        result["download_available"] = self._job_download_available(job)
        return result

    async def _download_bypy_directory(
        self, job: Dict[str, Any], workspace: pathlib.Path
    ) -> pathlib.Path:
        remote_path = str(job["remote_path"])
        relative_remote = remote_path.removeprefix("/apps/bypy/").strip("/")
        if not relative_remote:
            raise self.server.error("Cloud backup remote path is invalid", 400)
        return_code, output = await self._run_bypy_command(
            "-q", "download", relative_remote, str(workspace), timeout=1800.
        )
        command_error = _bypy_final_error(output)
        if return_code or command_error:
            if "auth" in output.lower() or "token" in output.lower():
                self.credentials["bypy_authorized"] = False
                self.oauth = {
                    "state": "expired",
                    "message": "百度 bypy 授权已失效，请重新授权",
                }
                await self._save_credentials()
            raise self.server.error(
                command_error or "bypy download failed", 502
            )
        manifests = list(workspace.rglob(BYPY_DIRECTORY_MANIFEST_NAME))
        if len(manifests) != 1:
            raise self.server.error(
                "Downloaded backup does not contain one valid manifest", 502
            )
        snapshot_root = manifests[0].parent
        try:
            await self.eventloop.run_in_thread(
                _verify_snapshot_sync, snapshot_root
            )
        except ValueError as exc:
            raise self.server.error(str(exc), 502) from exc
        return snapshot_root

    async def _prepare_download(self, job: Dict[str, Any]) -> Dict[str, Any]:
        await self.eventloop.run_in_thread(
            _prune_downloads_sync, self.download_dir
        )
        archive_name = str(job.get("archive_name", ""))
        if (
            pathlib.Path(archive_name).name != archive_name or
            not archive_name.endswith(".tar.gz")
        ):
            stamp = datetime.fromtimestamp(
                float(job["created_at"])
            ).strftime("%Y%m%d_%H%M%S")
            archive_name = f"{stamp}_{job['job_id']}.tar.gz"
        local_archive = self._local_archive_for_job(job)
        if local_archive is not None:
            expected_sha = job.get("sha256")
            actual_sha = await self.eventloop.run_in_thread(
                _sha256_file, local_archive
            )
            if isinstance(expected_sha, str) and actual_sha != expected_sha:
                raise self.server.error(
                    "Local backup archive failed SHA-256 verification", 502
                )
            published = await self.eventloop.run_in_thread(
                _publish_archive_sync,
                local_archive,
                self.download_dir,
                archive_name,
            )
            archive_sha = actual_sha
        else:
            if job.get("upload_mode") != "bypy_cli_directory":
                raise self.server.error(
                    "This backup is no longer available for download", 409
                )
            if not (
                self.credentials.get("bypy_authorized") and
                self.bypy_token_path.is_file()
            ):
                raise self.server.error(
                    "Baidu bypy authorization is required for cloud download",
                    401,
                )
            workspace = self.download_dir.joinpath(
                f".{job['job_id']}.{uuid.uuid4().hex}.download"
            )
            await self.eventloop.run_in_thread(
                workspace.mkdir, 0o700, True, False
            )
            try:
                snapshot_root = await self._download_bypy_directory(
                    job, workspace
                )
                temp_archive = workspace.joinpath(archive_name)
                archive_info = await self.eventloop.run_in_thread(
                    _archive_snapshot_sync, snapshot_root, temp_archive
                )
                published = await self.eventloop.run_in_thread(
                    _publish_archive_sync,
                    temp_archive,
                    self.download_dir,
                    archive_name,
                )
                archive_sha = str(archive_info["sha256"])
            finally:
                if workspace.exists():
                    await self.eventloop.run_in_thread(_remove_tree, workspace)
        return {
            "root": DOWNLOAD_ROOT_NAME,
            "filename": published.name,
            "size": published.stat().st_size,
            "sha256": archive_sha,
            "expires_at": time.time() + DOWNLOAD_MAX_AGE_SECONDS,
        }

    async def _handle_download(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        job_id = web_request.get_str("job_id").strip()
        if not re.fullmatch(r"[0-9a-f]{12}", job_id):
            raise self.server.error("Cloud backup job id is invalid", 400)
        job = self._history_job(job_id)
        if job is None:
            raise self.server.error("Cloud backup job not found", 404)
        if not self._job_download_available(job):
            raise self.server.error(
                "This cloud backup is not available for download", 409
            )
        if self.backup_task is not None and not self.backup_task.done():
            raise self.server.error(
                "Wait for the active cloud backup before downloading", 409
            )
        if self.download_lock.locked():
            raise self.server.error("A cloud backup download is already running", 409)
        async with self.download_lock:
            self.download_in_progress = True
            self.server.send_event(
                "cloud_backup:status_changed", self._status()
            )
            try:
                return await self._prepare_download(job)
            finally:
                self.download_in_progress = False
                self.server.send_event(
                    "cloud_backup:status_changed", self._status()
                )

    async def _request_json(
        self,
        method: str,
        url: str,
        body: Optional[Any] = None,
        headers: Optional[Dict[str, Any]] = None,
        timeout: float = 30.,
        allow_api_error: bool = False,
        service: str = "Baidu",
    ) -> Dict[str, Any]:
        response: HttpResponse = await self.http_client.request(
            method, url, body=body, headers=headers,
            connect_timeout=10., request_timeout=timeout,
            attempts=2, retry_pause_time=1.
        )
        try:
            payload = response.json()
        except Exception as exc:
            raise self.server.error(
                f"{service} returned an invalid response"
            ) from exc
        if not isinstance(payload, dict):
            raise self.server.error(f"{service} returned an unexpected response")
        api_error = payload.get("error") or payload.get("errno")
        if response.status_code >= 400 or (
            api_error not in (None, 0, "0") and not allow_api_error
        ):
            message = (
                payload.get("error_description") or
                payload.get("errmsg") or
                payload.get("message") or
                payload.get("error") or
                f"{service} API request failed"
            )
            raise self.server.error(str(message), 502)
        return payload

    def _update_job(self, state: str, progress: int, stage: str) -> None:
        if self.active_job is None:
            return
        self.active_job.update({
            "state": state,
            "progress": progress,
            "stage": stage,
        })
        self._notify_job()

    def _update_upload_progress(
        self,
        uploaded_bytes: int,
        total_bytes: int,
        current_file: str,
        uploaded_files: int,
        total_files: int,
        stage: str = "uploading",
    ) -> None:
        if self.active_job is None:
            return
        safe_total = max(0, total_bytes)
        safe_uploaded = max(0, min(uploaded_bytes, safe_total))
        ratio = safe_uploaded / safe_total if safe_total else 0.
        self.active_job.update({
            "state": "uploading",
            "stage": stage,
            "progress": min(95, 20 + int(round(ratio * 75))),
            "uploaded_bytes": safe_uploaded,
            "upload_total_bytes": safe_total,
            "upload_progress": int(round(ratio * 100)),
            "current_file": current_file,
            "uploaded_files": max(0, min(uploaded_files, total_files)),
            "upload_total_files": max(0, total_files),
        })
        self._notify_job()

    def _notify_job(self) -> None:
        if self.active_job is not None:
            self.server.send_event(
                "cloud_backup:job_progress", dict(self.active_job)
            )

    async def _handle_history(self, web_request: WebRequest) -> Dict[str, Any]:
        limit = max(1, min(100, web_request.get_int("limit", 30)))
        return {
            "jobs": [self._public_job(job) for job in self.history[:limit]]
        }

    async def _handle_job(self, web_request: WebRequest) -> Dict[str, Any]:
        job_id = web_request.get_str("job_id")
        if self.active_job is not None and self.active_job["job_id"] == job_id:
            return {"job": self._public_job(self.active_job)}
        for job in self.history:
            if job.get("job_id") == job_id:
                return {"job": self._public_job(job)}
        raise self.server.error("Cloud backup job not found", 404)

    async def _prune_archives(self) -> None:
        await self.eventloop.run_in_thread(
            _prune_archives_sync, self.archive_dir, self.retain_local
        )

    def close(self) -> None:
        for task in (
            self.oauth_task,
            self.web_task,
            self.backup_task,
            self.auto_backup_task,
        ):
            if task is not None and not task.done():
                task.cancel()
        if self.web_process is not None and self.web_process.returncode is None:
            self.web_process.terminate()
        for process in (self.bypy_auth_process, self.bypy_upload_process):
            if process is not None and process.returncode is None:
                process.terminate()


def load_component(config: ConfigHelper) -> CloudBackup:
    return CloudBackup(config)
