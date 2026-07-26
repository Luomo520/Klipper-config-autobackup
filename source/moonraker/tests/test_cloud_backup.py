from __future__ import annotations

import hashlib
import asyncio
import enum
import json
import os
import pathlib
import sys
import tarfile
import time
import types
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

# The component only needs RequestType while these pure helpers are imported.
# Avoid requiring Moonraker's full Tornado runtime for this focused unit test.
if "moonraker.common" not in sys.modules:
    common_stub = types.ModuleType("moonraker.common")

    class RequestType(enum.IntFlag):
        GET = 1
        POST = 2

    common_stub.RequestType = RequestType
    sys.modules["moonraker.common"] = common_stub

from moonraker.components.cloud_backup import (
    BACKUP_CHECKSUM_NAME,
    BACKUP_DESCRIPTION_NAME,
    BYPY_DIRECTORY_MANIFEST_NAME,
    CloudBackup,
    _bypy_final_error,
    _encode_file_base64,
    _latest_automatic_run_at,
    _latest_history_at,
    _latest_success_at,
    _next_interval_run_at,
    _prune_archives_sync,
    _prepare_backup_sync,
    _read_json,
    _validate_github_branch,
    _validate_github_name,
    _validate_github_path,
    _validate_web_remote_path,
    _verify_snapshot_sync,
)


class CloudBackupHelpersTest(unittest.TestCase):
    def test_read_json_preserves_persisted_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = pathlib.Path(temp_dir).joinpath("settings.json")
            state_path.write_text(
                '{"auto_backup_enabled": true, "auto_backup_mode": "startup"}',
                encoding="utf-8",
            )

            self.assertEqual(
                _read_json(state_path, {}),
                {
                    "auto_backup_enabled": True,
                    "auto_backup_mode": "startup",
                },
            )

    def test_validate_web_remote_path(self) -> None:
        self.assertEqual(
            _validate_web_remote_path("打印机配置备份/2026"),
            "/打印机配置备份/2026",
        )
        for value in ["/", "/backup/../other", "/backup//other"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                _validate_web_remote_path(value)

    def test_bypy_final_error_uses_command_result_line(self) -> None:
        output = "<E> [20:00:00] ---\nError 31081: combine failed\n"
        self.assertEqual(
            _bypy_final_error(output), "Error 31081: combine failed"
        )
        self.assertIsNone(_bypy_final_error("Upload completed\n"))

    def test_validate_github_destination(self) -> None:
        self.assertEqual(
            _validate_github_name("printer-backups", "github_owner"),
            "printer-backups",
        )
        self.assertEqual(_validate_github_branch("release/v1"), "release/v1")
        self.assertEqual(
            _validate_github_path("backups/printer-one"),
            "backups/printer-one",
        )

        for value in ("", ".", "owner/name", "contains space"):
            with self.subTest(name=value), self.assertRaises(ValueError):
                _validate_github_name(value, "github_owner")
        for value in ("", "/", "feature//bad", "feature/../bad", "bad."):
            with self.subTest(branch=value), self.assertRaises(ValueError):
                _validate_github_branch(value)
        for value in ("", "/", "backups/../other", "backups//other"):
            with self.subTest(path=value), self.assertRaises(ValueError):
                _validate_github_path(value)

    def test_encode_file_base64_enforces_size_limit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = pathlib.Path(temp_dir).joinpath("archive.bin")
            source.write_bytes(b"configuration-data")

            self.assertEqual(
                _encode_file_base64(source, source.stat().st_size),
                "Y29uZmlndXJhdGlvbi1kYXRh",
            )
            with self.assertRaises(ValueError):
                _encode_file_base64(source, source.stat().st_size - 1)

    def test_remote_file_path_uses_provider_destination(self) -> None:
        backup = CloudBackup.__new__(CloudBackup)
        created_at = 1_784_979_200.0
        filename = "printer.tar.gz"

        bypy_path = backup._remote_file_path(filename, created_at, {
                "provider": "baidu",
                "auth_mode": "bypy",
                "web_remote_path": "/unused",
                "bypy_remote_path": "/3D打印机备份",
                "github_path": "unused",
            })
        self.assertTrue(bypy_path.startswith("/3D打印机备份/backups/2026/"))
        self.assertTrue(bypy_path.endswith("/printer.tar.gz"))
        github_path = backup._remote_file_path(filename, created_at, {
            "provider": "github",
            "auth_mode": "bypy",
            "web_remote_path": "/unused",
            "bypy_remote_path": "/unused",
            "github_path": "printer-backups",
        })
        self.assertTrue(github_path.startswith("printer-backups/backups/"))
        self.assertTrue(github_path.endswith("/printer.tar.gz"))

    def test_snapshot_contains_readable_files_manifest_and_checksums(self) -> None:
        with TemporaryDirectory() as temp_dir:
            tmp_path = pathlib.Path(temp_dir)
            config_dir = tmp_path.joinpath("config")
            config_dir.mkdir()
            config_dir.joinpath("macros").mkdir()
            config_dir.joinpath("empty").mkdir()
            config_dir.joinpath(".git").mkdir()
            config_dir.joinpath(".git", "config").write_text(
                "repository metadata", encoding="utf-8"
            )
            config_dir.joinpath("printer.cfg").write_text(
                "[printer]\nkinematics: cartesian\n", encoding="utf-8"
            )
            config_dir.joinpath("macros", "test.cfg").write_text(
                "[gcode_macro TEST]\n", encoding="utf-8"
            )
            snapshot_path = tmp_path.joinpath("snapshot")
            archive_path = tmp_path.joinpath("backup.tar.gz")
            reason = "Changed extruder rotation distance after calibration"
            result = _prepare_backup_sync(
                snapshot_path,
                archive_path,
                [("config", config_dir)],
                reason,
                time.time(),
                "job123",
            )

            manifest = _verify_snapshot_sync(snapshot_path)
            info = snapshot_path.joinpath(BACKUP_DESCRIPTION_NAME).read_text(
                encoding="utf-8"
            )
            with tarfile.open(archive_path, "r:gz") as archive:
                names = archive.getnames()
            self.assertIn("config/printer.cfg", names)
            self.assertIn(BACKUP_DESCRIPTION_NAME, names)
            self.assertIn(BYPY_DIRECTORY_MANIFEST_NAME, names)
            self.assertIn(BACKUP_CHECKSUM_NAME, names)
            self.assertIn(f"备份原因：{reason}", info)
            self.assertIn("来源目录：config", info)
            self.assertIn("config/empty", manifest["directories"])
            self.assertNotIn("config/.git/config", names)
            self.assertTrue(any(
                item["path"] == "config/.git" for item in manifest["skipped"]
            ))
            self.assertEqual(result["file_count"], 2)
            self.assertGreater(result["snapshot_size"], result["source_size"])
            self.assertEqual(
                result["sha256"],
                hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            )

    def test_snapshot_records_symlink_without_following_target(self) -> None:
        with TemporaryDirectory() as temp_dir:
            tmp_path = pathlib.Path(temp_dir)
            config_dir = tmp_path.joinpath("config")
            config_dir.mkdir()
            outside = tmp_path.joinpath("secret.txt")
            outside.write_text("must not be copied", encoding="utf-8")
            link = config_dir.joinpath("timelapse.cfg")
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symbolic links unavailable: {exc}")
            snapshot_path = tmp_path.joinpath("snapshot")
            archive_path = tmp_path.joinpath("backup.tar.gz")

            _prepare_backup_sync(
                snapshot_path,
                archive_path,
                [("config", config_dir)],
                "Record a configuration link without following its target",
                time.time(),
                "job123",
            )
            manifest = _verify_snapshot_sync(snapshot_path)

            self.assertFalse(snapshot_path.joinpath("config", "timelapse.cfg").exists())
            marker = snapshot_path.joinpath(
                "config", "timelapse.cfg.symlink.txt"
            )
            self.assertTrue(marker.is_file())
            self.assertIn(str(outside), marker.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["symlinks"]), 1)

    def test_bypy_file_upload_verifies_exact_remote_size(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = pathlib.Path(temp_dir).joinpath("printer.cfg")
            source.write_bytes(b"configuration-data")
            calls = []

            async def run_bypy(*arguments: str, timeout: float = 900.):
                calls.append((arguments, timeout))
                if "meta" in arguments:
                    return 0, f"{source.stat().st_size}\n"
                return 0, ""

            backup = CloudBackup.__new__(CloudBackup)
            backup._run_bypy_command = run_bypy

            result = asyncio.run(backup._upload_bypy_file(
                source, "3D打印机备份/job/config/printer.cfg"
            ))

            self.assertEqual(result, source.stat().st_size)
            self.assertIn("overwrite", calls[0][0])
            self.assertEqual(calls[1][0][-1], "$s")

    def test_bypy_upload_rejects_remote_size_mismatch(self) -> None:
        class FakeServer:
            @staticmethod
            def error(message: str, status_code: int) -> RuntimeError:
                return RuntimeError(f"{status_code}: {message}")

        with TemporaryDirectory() as temp_dir:
            source = pathlib.Path(temp_dir).joinpath("archive.bin")
            source.write_bytes(b"configuration-data")

            async def run_bypy(*arguments: str, timeout: float = 900.):
                return (0, "1\n") if "meta" in arguments else (0, "")

            backup = CloudBackup.__new__(CloudBackup)
            backup.server = FakeServer()
            backup._run_bypy_command = run_bypy

            with (
                patch(
                    "moonraker.components.cloud_backup.asyncio.sleep",
                    new=AsyncMock(),
                ),
                self.assertRaisesRegex(
                    RuntimeError, "could not verify remote size"
                ),
            ):
                asyncio.run(
                    backup._upload_bypy_file(
                        source, "3D打印机备份/job/archive.bin"
                    )
                )

    def test_bypy_directory_uploads_files_individually_and_manifest_last(self) -> None:
        class FakeServer:
            @staticmethod
            def error(message: str, status_code: int) -> RuntimeError:
                return RuntimeError(f"{status_code}: {message}")

        class FakeEventLoop:
            @staticmethod
            async def run_in_thread(function, *args):
                return function(*args)

        with TemporaryDirectory() as temp_dir:
            tmp_path = pathlib.Path(temp_dir)
            config_dir = tmp_path.joinpath("config-source")
            config_dir.mkdir()
            config_dir.joinpath("printer.cfg").write_text(
                "[printer]\n", encoding="utf-8"
            )
            config_dir.joinpath("moonraker.conf").write_text(
                "[server]\n", encoding="utf-8"
            )
            snapshot = tmp_path.joinpath("snapshot")
            _prepare_backup_sync(
                snapshot,
                tmp_path.joinpath("local.tar.gz"),
                [("config", config_dir)],
                "Upload readable printer configuration files one by one",
                time.time(),
                "job123",
            )
            remote_sizes = {}
            uploaded_paths = []
            mkdir_paths = []
            progress = []

            async def run_bypy(*arguments: str, timeout: float = 900.):
                if "mkdir" in arguments:
                    mkdir_paths.append(arguments[2])
                    return 0, ""
                if "upload" in arguments:
                    local_path = pathlib.Path(arguments[2])
                    remote_path = arguments[3]
                    remote_sizes[remote_path] = local_path.stat().st_size
                    uploaded_paths.append(remote_path)
                    return 0, ""
                remote_path = arguments[2]
                if arguments[-1] == "$t":
                    return 0, "D\n"
                return 0, f"{remote_sizes[remote_path]}\n"

            backup = CloudBackup.__new__(CloudBackup)
            backup.server = FakeServer()
            backup.eventloop = FakeEventLoop()
            backup._run_bypy_command = run_bypy
            backup._update_upload_progress = (
                lambda uploaded, total, current, done, count, stage="uploading":
                progress.append((uploaded, total, current, done, count, stage))
            )

            result = asyncio.run(backup._upload_directory_bypy(
                snapshot, "/3D打印机备份/backups/job123"
            ))

            self.assertEqual(result["upload_mode"], "bypy_cli_directory")
            self.assertEqual(len(uploaded_paths), result["upload_total_files"])
            self.assertTrue(uploaded_paths[-1].endswith(
                f"/{BYPY_DIRECTORY_MANIFEST_NAME}"
            ))
            self.assertTrue(any(path.endswith("config/printer.cfg") for path in uploaded_paths))
            self.assertEqual(len(mkdir_paths), len(set(mkdir_paths)))
            self.assertEqual(progress[-1][0], result["remote_size"])
            self.assertEqual(progress[-1][3], result["upload_total_files"])
            self.assertEqual(progress[-1][-1], "verifying_upload")

    def test_upload_progress_reports_verified_bytes(self) -> None:
        events = []
        backup = CloudBackup.__new__(CloudBackup)
        backup.active_job = {"state": "uploading"}
        backup._notify_job = lambda: events.append(dict(backup.active_job))

        backup._update_upload_progress(8, 20, "config/printer.cfg", 1, 3)

        self.assertEqual(backup.active_job["uploaded_bytes"], 8)
        self.assertEqual(backup.active_job["upload_total_bytes"], 20)
        self.assertEqual(backup.active_job["upload_progress"], 40)
        self.assertEqual(backup.active_job["uploaded_files"], 1)
        self.assertEqual(events[-1]["current_file"], "config/printer.cfg")

    def test_snapshot_verification_rejects_tampered_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            tmp_path = pathlib.Path(temp_dir)
            config_dir = tmp_path.joinpath("config")
            config_dir.mkdir()
            config_dir.joinpath("printer.cfg").write_text(
                "[printer]\n", encoding="utf-8"
            )
            snapshot = tmp_path.joinpath("snapshot")
            _prepare_backup_sync(
                snapshot,
                tmp_path.joinpath("backup.tar.gz"),
                [("config", config_dir)],
                "Create a verified readable backup for download testing",
                time.time(),
                "job123",
            )
            snapshot.joinpath("config", "printer.cfg").write_text(
                "tampered\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "verification failed"):
                _verify_snapshot_sync(snapshot)

    def test_prune_archives_limits_all_local_results(self) -> None:
        with TemporaryDirectory() as temp_dir:
            archive_dir = pathlib.Path(temp_dir)
            archives = []
            for index in range(7):
                archive = archive_dir.joinpath(f"backup-{index}.tar.gz")
                archive.write_bytes(str(index).encode("ascii"))
                os.utime(archive, (1000 + index, 1000 + index))
                archives.append(archive)

            _prune_archives_sync(archive_dir, 3)

            self.assertEqual(
                sorted(path.name for path in archive_dir.glob("*.tar.gz")),
                [path.name for path in archives[-3:]],
            )

    def test_backup_running_guard_rejects_mutations(self) -> None:
        class RunningTask:
            @staticmethod
            def done() -> bool:
                return False

        class FakeServer:
            @staticmethod
            def error(message: str, status_code: int) -> RuntimeError:
                return RuntimeError(f"{status_code}: {message}")

        backup = CloudBackup.__new__(CloudBackup)
        backup.backup_task = RunningTask()
        backup.download_in_progress = False
        backup.server = FakeServer()

        with self.assertRaisesRegex(RuntimeError, "409: Wait for the active"):
            backup._require_backup_idle("changing configuration")

    def test_download_availability_requires_success_and_controlled_source(self) -> None:
        backup = CloudBackup.__new__(CloudBackup)
        backup.archive_dir = pathlib.Path("missing-local-archives")

        readable = {
            "state": "success",
            "upload_mode": "bypy_cli_directory",
            "remote_path": "/apps/bypy/3D打印机备份/backups/job123",
        }
        self.assertTrue(backup._job_download_available(readable))
        self.assertFalse(backup._job_download_available({
            **readable,
            "state": "failed",
        }))
        self.assertFalse(backup._job_download_available({
            **readable,
            "remote_path": "/etc/passwd",
        }))

    def test_interval_schedule_uses_latest_successful_backup(self) -> None:
        history = [
            {"state": "failed", "finished_at": 500.0},
            {"state": "success", "created_at": 100.0, "finished_at": 200.0},
            {"state": "success", "created_at": 300.0},
        ]

        self.assertEqual(_latest_success_at(history), 300.0)
        self.assertEqual(
            _next_interval_run_at(history, 3, 250.0),
            300.0 + 3 * 24 * 60 * 60,
        )

    def test_history_timestamps_ignore_invalid_values(self) -> None:
        history = [
            {
                "state": "success",
                "trigger": "automatic",
                "created_at": 100.0,
                "finished_at": 120.0,
            },
            {
                "state": "failed",
                "trigger": "automatic",
                "created_at": 200.0,
                "finished_at": 230.0,
            },
            {"state": "success", "created_at": True, "finished_at": True},
        ]

        self.assertEqual(_latest_success_at(history), 120.0)
        self.assertEqual(_latest_history_at(history), 230.0)
        self.assertEqual(_latest_automatic_run_at(history), 200.0)

    def test_interval_schedule_uses_startup_delay_without_history(self) -> None:
        self.assertEqual(_latest_success_at([]), None)
        self.assertEqual(_next_interval_run_at([], 7, 1234.0), 1234.0)


if __name__ == "__main__":
    unittest.main()
