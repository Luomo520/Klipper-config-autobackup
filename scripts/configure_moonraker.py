from __future__ import annotations

import argparse
import os
import pathlib
import stat
import tempfile
from typing import List, Optional, Tuple


SECTION = "cloud_backup"
DEFAULT_SECTION = [
    "[cloud_backup]",
    "provider: baidu",
    "auth_mode: bypy",
    "bypy_remote_path: /3D打印机备份",
    "web_remote_path: /3D打印机备份",
    "backup_roots: config",
    "retain_local: 5",
    "auto_backup_enabled: false",
    "auto_backup_mode: interval",
    "auto_backup_interval_days: 3",
    "auto_backup_startup_delay_minutes: 15",
    "",
]


def section_bounds(lines: List[str], name: str) -> Optional[Tuple[int, int]]:
    header = f"[{name}]"
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == header),
        None,
    )
    if start is None:
        return None
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].lstrip().startswith("[")
        ),
        len(lines),
    )
    return start, end


def read_lines(path: pathlib.Path) -> List[str]:
    return path.read_text(encoding="utf-8").splitlines()


def write_atomic(path: pathlib.Path, lines: List[str]) -> None:
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent), text=True
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as output:
            output.write("\n".join(lines).rstrip() + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temp_name, stat.S_IMODE(path.stat().st_mode))
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def ensure_section(config: pathlib.Path) -> bool:
    lines = read_lines(config)
    if section_bounds(lines, SECTION) is not None:
        return False
    if lines and lines[-1].strip():
        lines.append("")
    lines.extend(DEFAULT_SECTION)
    write_atomic(config, lines)
    return True


def restore_section(config: pathlib.Path, baseline: pathlib.Path) -> bool:
    current = read_lines(config)
    original = read_lines(baseline)
    current_bounds = section_bounds(current, SECTION)
    original_bounds = section_bounds(original, SECTION)

    replacement: List[str] = []
    if original_bounds is not None:
        replacement = original[original_bounds[0] : original_bounds[1]]

    if current_bounds is None:
        if not replacement:
            return False
        if current and current[-1].strip():
            current.append("")
        current.extend(replacement)
    else:
        current = (
            current[: current_bounds[0]]
            + replacement
            + current[current_bounds[1] :]
        )
    write_atomic(config, current)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)

    ensure = subparsers.add_parser("ensure")
    ensure.add_argument("config", type=pathlib.Path)

    restore = subparsers.add_parser("restore-section")
    restore.add_argument("config", type=pathlib.Path)
    restore.add_argument("baseline", type=pathlib.Path)

    args = parser.parse_args()
    if args.action == "ensure":
        changed = ensure_section(args.config)
    else:
        changed = restore_section(args.config, args.baseline)
    print("changed" if changed else "unchanged")


if __name__ == "__main__":
    main()
