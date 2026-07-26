from __future__ import annotations

import pathlib
import sys
import tarfile


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 4 and args[0] == "-xzf" and args[2] == "-C":
        archive = pathlib.Path(args[1])
        destination = pathlib.Path(args[3])
        with tarfile.open(archive, "r:gz") as source:
            source.extractall(destination, filter="data")
        return 0
    print(f"unsupported test tar arguments: {args!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
