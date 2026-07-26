from __future__ import annotations

import hashlib
import pathlib
import sys


def digest(path: pathlib.Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def verify(checksum_file: pathlib.Path) -> int:
    failed = False
    for raw_line in checksum_file.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        expected, name = raw_line.split(None, 1)
        name = name.lstrip(" *")
        path = pathlib.Path(name)
        ok = path.is_file() and digest(path) == expected.lower()
        print(f"{name}: {'OK' if ok else 'FAILED'}")
        failed = failed or not ok
    return int(failed)


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] in {"-c", "--check"}:
        return verify(pathlib.Path(args[1]))
    if not args:
        print("sha256sum test helper requires file arguments", file=sys.stderr)
        return 2
    for name in args:
        path = pathlib.Path(name)
        print(f"{digest(path)}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
