import sqlite3
from pathlib import Path

try:
    from flask import current_app, g
except ImportError:
    current_app = None
    g = None


def get_db():
    try:
        if g is not None and "db" not in g:
            g.db = sqlite3.connect(
                current_app.config["DATABASE"],
                timeout=30.0,
                check_same_thread=False,
            )
            g.db.row_factory = sqlite3.Row
            try:
                g.db.execute("PRAGMA journal_mode=WAL")
                g.db.execute("PRAGMA synchronous=NORMAL")
            except Exception:
                pass
        if g is not None:
            return g.db
    except (RuntimeError, AttributeError):
        pass

    # Fallback when outside Flask context
    base_dir = Path(__file__).resolve().parent.parent
    db_path = base_dir / "hatchery.db"
    conn = sqlite3.connect(
        str(db_path),
        timeout=30.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return conn


def close_db(error=None):
    try:
        if g is not None:
            db = g.pop("db", None)
            if db is not None:
                db.close()
    except (RuntimeError, AttributeError):
        pass


def init_db(app):
    database_path = Path(app.config["DATABASE"])
    schema_path = app.config["BASE_DIR"] / "schema.sql"

    database_path.parent.mkdir(parents=True, exist_ok=True)

    def _execute_init():
        with app.app_context():
            db = get_db()
            # Perform quick integrity check
            check_row = db.execute("PRAGMA quick_check").fetchone()
            if check_row and check_row[0] != "ok":
                raise sqlite3.DatabaseError(f"PRAGMA quick_check failed: {check_row[0]}")

            with schema_path.open("r", encoding="utf-8") as schema_file:
                db.executescript(schema_file.read())
            db.commit()

            from app.models import (
                cleanup_old_notifications,
                init_motor_state,
                ensure_default_calibrations,
            )
            cleanup_old_notifications()
            init_motor_state()
            ensure_default_calibrations()

    try:
        _execute_init()
    except sqlite3.DatabaseError as e:
        err_msg = str(e).lower()
        if (
            "malformed" in err_msg
            or "disk image" in err_msg
            or "quick_check" in err_msg
            or "not a database" in err_msg
            or "corrupt" in err_msg
        ):
            import logging
            import time
            import shutil

            logging.critical(
                f"[DATABASE] Malformed database disk image detected ({e}). Backing up corrupt DB and regenerating fresh database..."
            )
            print(f"\n[WARN] Malformed SQLite database detected ({e}).")
            print("[INFO] Backing up corrupted database and creating a clean database from schema...\n")

            close_db()

            ts = int(time.time())
            for ext in ("", "-wal", "-shm"):
                target = Path(str(database_path) + ext)
                if target.exists():
                    try:
                        shutil.move(str(target), str(target) + f".corrupted.{ts}")
                    except Exception:
                        try:
                            target.unlink()
                        except Exception:
                            pass

            _execute_init()
            print("[INFO] Fresh hatchery database initialized successfully.\n")
        else:
            raise
