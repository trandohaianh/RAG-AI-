"""Load only explicitly configured *_FILE secrets before importing app/task modules."""
from pathlib import Path
import os
import sys


def load_secret_files(environ):
    for name, filename in list(environ.items()):
        if not name.endswith("_FILE") or not filename:
            continue
        value = Path(filename).read_text(encoding="utf-8-sig").rstrip("\r\n")
        if not value or len(value) > 16384 or "\x00" in value or "\n" in value or "\r" in value:
            raise RuntimeError("Invalid secret file for " + name[:-5])
        environ[name[:-5]] = value


if __name__ == "__main__":
    load_secret_files(os.environ)
    if len(sys.argv) < 2:
        raise SystemExit("Service command required")
    os.execvp(sys.argv[1], sys.argv[1:])
