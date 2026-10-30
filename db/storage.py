"""Database location and consistent, verified SQLite snapshots."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
# BotHost persistent volume; keep local development data on Windows.
SHARED_ROOT = Path("/app/shared")
DEFAULT_DATA_DIR = SHARED_ROOT / "hunter-bot" if os.name != "nt" and SHARED_ROOT.is_dir() else ROOT / "data"
DATA_DIR = Path(os.environ.get("BOT_DATA_DIR") or str(DEFAULT_DATA_DIR)).expanduser().resolve()
DB_PATH = DATA_DIR / "bot.db"
BACKUP_DIR = DATA_DIR / "backups"


def check_database(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as source:
        result = source.execute("PRAGMA quick_check").fetchall()
        if result != [("ok",)]:
            raise RuntimeError(f"SQLite integrity check failed for {path}: {result}")


def snapshot(source_path, target_path):
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_path.with_suffix(".tmp")
    try:
        with closing(sqlite3.connect(Path(source_path).resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(temporary)) as target:
                source.backup(target)
        check_database(temporary)
        os.replace(temporary, target_path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_database():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Prefer the latest layout, then the original root-level database.
    if not DB_PATH.exists():
        for legacy in (ROOT / "data" / "bot.db", ROOT / "bot.db"):
            if legacy.exists() and legacy.resolve() != DB_PATH:
                snapshot(legacy, DB_PATH)
                break
    if DB_PATH.exists():
        check_database(DB_PATH)
        backup_database("startup")
    elif os.environ.get("BOT_REQUIRE_EXISTING_DB", "1" if DATA_DIR == (SHARED_ROOT / "hunter-bot") else "0") == "1":
        raise RuntimeError(f"Expected database missing: {DB_PATH}. Restore backup; refusing to create an empty database.")
    return str(DB_PATH)


def backup_database(label="scheduled"):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S_%f")
    path = BACKUP_DIR / f"bot_{stamp}_{label}.db"
    snapshot(DB_PATH, path)
    # Keep startup/update snapshots separate from daily backup retention.
    for old in sorted(BACKUP_DIR.glob(f"bot_*_{label}.db"), reverse=True)[7:]:
        old.unlink()
    return path
