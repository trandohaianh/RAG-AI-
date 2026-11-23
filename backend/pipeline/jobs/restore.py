"""Khôi phục dữ liệu từ bản sao lưu do job `backup` tạo (bước 8: thử khôi phục).

Một "bộ" sao lưu gồm các tệp cùng dấu thời gian trong BACKUP_DIR:
  app-<stamp>.db, chroma-<stamp>.tar.gz, photos-<stamp>.tar.gz, uploads-<stamp>.tar.gz

An toàn:
- Kiểm tra SQLite (`PRAGMA integrity_check`) và từng tệp trong tar (không `..`, không đường dẫn
  tuyệt đối, không link) TRƯỚC khi đụng vào dữ liệu đang dùng.
- Dữ liệu hiện tại không bị xoá: được chuyển sang `DATA_DIR/pre-restore-<thời điểm>/`.
- Bắt buộc `--yes` (hoặc `--dry-run` để chỉ kiểm tra).

Trên VM (deploy/cloud):
  docker compose stop api pipeline
  docker compose run --rm --no-deps api python -m pipeline.jobs.restore --list
  docker compose run --rm --no-deps api python -m pipeline.jobs.restore --latest --dry-run
  docker compose run --rm --no-deps api python -m pipeline.jobs.restore --latest --yes
  docker compose up -d
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

STAMP_RE = re.compile(r"^(app|chroma|photos|uploads)-(\d{8}T\d{6}\d*Z)\.(db|tar\.gz)$")


class RestoreError(Exception):
    pass


def _paths() -> dict:
    import config

    db_url = config.DATABASE_URL
    if not db_url.startswith("sqlite:///"):
        raise RestoreError("Chỉ khôi phục được SQLite; Postgres dùng bản sao lưu của nhà cung cấp (pg_dump/PITR)")
    return {
        "data_root": Path(config.DATA_ROOT),
        "backup_dir": Path(os.getenv("BACKUP_DIR", str(Path(config.DATA_ROOT) / "backups"))),
        "db": Path(db_url.removeprefix("sqlite:///")),
        "dirs": {
            "chroma": (Path(config.CHROMA_DIR), "chroma_db"),
            "photos": (Path(config.FIELD_PHOTO_DIR), "field_photos"),
            "uploads": (Path(config.UPLOAD_DIR), "uploads"),
        },
    }


def list_sets(backup_dir: Path) -> dict[str, dict[str, Path]]:
    sets: dict[str, dict[str, Path]] = {}
    if backup_dir.exists():
        for path in backup_dir.iterdir():
            match = STAMP_RE.match(path.name)
            if match and path.is_file():
                sets.setdefault(match.group(2), {})[match.group(1)] = path
    return dict(sorted(sets.items()))


def verify_db(path: Path) -> dict:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] if "users" in tables else None
            cases = (
                conn.execute("SELECT COUNT(*) FROM field_service_cases").fetchone()[0]
                if "field_service_cases" in tables
                else None
            )
    except sqlite3.DatabaseError as exc:
        raise RestoreError(f"{path.name}: không phải SQLite hợp lệ ({exc})") from None
    if result != "ok":
        raise RestoreError(f"{path.name}: integrity_check = {result}")
    if users is None:
        raise RestoreError(f"{path.name}: thiếu bảng users, không phải CSDL của ứng dụng")
    return {"users": users, "cases": cases}


def _check_tar(path: Path, arcname: str) -> int:
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = tar.getmembers()
    except (tarfile.TarError, OSError) as exc:
        raise RestoreError(f"{path.name}: tệp nén hỏng ({exc})") from None
    for member in members:
        name = PurePosixPath(member.name)
        if name.is_absolute() or ".." in name.parts or name.parts[:1] != (arcname,):
            raise RestoreError(f"{path.name}: đường dẫn không an toàn trong tệp nén: {member.name}")
        if not (member.isfile() or member.isdir()):
            raise RestoreError(f"{path.name}: tệp nén chứa link/thiết bị: {member.name}")
    return sum(1 for m in members if m.isfile())


def restore(stamp: str, *, dry_run: bool = False, now: datetime | None = None) -> dict:
    paths = _paths()
    sets = list_sets(paths["backup_dir"])
    if stamp not in sets:
        raise RestoreError(f"Không có bộ sao lưu {stamp}")
    files = sets[stamp]
    if "app" not in files:
        raise RestoreError(f"Bộ {stamp} thiếu tệp CSDL app-{stamp}.db")
    report: dict = {"stamp": stamp, "db": verify_db(files["app"]), "archives": {}}
    for kind, (_, arcname) in paths["dirs"].items():
        if kind in files:
            report["archives"][kind] = _check_tar(files[kind], arcname)
    if dry_run:
        report["dry_run"] = True
        return report

    data_root: Path = paths["data_root"]
    aside = data_root / f"pre-restore-{(now or datetime.now(UTC)).strftime('%Y%m%dT%H%M%SZ')}"
    aside.mkdir(parents=True, exist_ok=False)
    report["previous_data_moved_to"] = str(aside)

    db: Path = paths["db"]
    for suffix in ("", "-wal", "-shm"):
        current = db.with_name(db.name + suffix)
        if current.exists():
            shutil.move(str(current), aside / current.name)
    db.parent.mkdir(parents=True, exist_ok=True)
    staging = db.with_name(db.name + ".restoring")
    shutil.copy2(files["app"], staging)
    os.replace(staging, db)

    for kind, (target, arcname) in paths["dirs"].items():
        if kind not in files:
            continue  # bộ sao lưu không có phần này (lúc sao lưu thư mục trống): giữ nguyên hiện trạng
        if target.exists():
            shutil.move(str(target), aside / target.name)
        work = data_root / f".restore-{kind}"
        shutil.rmtree(work, ignore_errors=True)
        with tarfile.open(files[kind], "r:gz") as tar:
            tar.extractall(work, filter="data")
        os.replace(work / arcname, target)
        shutil.rmtree(work, ignore_errors=True)
    report["restored"] = sorted(["app", *report["archives"]])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    pick = parser.add_mutually_exclusive_group(required=True)
    pick.add_argument("--list", action="store_true", help="liệt kê các bộ sao lưu")
    pick.add_argument("--latest", action="store_true", help="khôi phục bộ mới nhất")
    pick.add_argument("--stamp", help="khôi phục bộ có dấu thời gian này")
    parser.add_argument("--dry-run", action="store_true", help="chỉ kiểm tra, không đổi dữ liệu")
    parser.add_argument("--yes", action="store_true", help="xác nhận ghi đè dữ liệu đang dùng")
    args = parser.parse_args(argv)
    try:
        sets = list_sets(_paths()["backup_dir"])
        if args.list:
            for stamp, files in sets.items():
                print(stamp, " ".join(sorted(files)))
            return 0
        stamp = args.stamp or (list(sets)[-1] if sets else None)
        if not stamp:
            raise RestoreError("Chưa có bản sao lưu nào")
        if not args.dry_run and not args.yes:
            raise RestoreError(
                "Thêm --yes để xác nhận (hoặc --dry-run để chỉ kiểm tra). Hãy dừng api và pipeline trước."
            )
        print(restore(stamp, dry_run=args.dry_run))
        return 0
    except RestoreError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
