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
    if g is not None:
        db = g.pop("db", None)
        if db is not None:
            db.close()


def init_db(app):
    database_path = Path(app.config["DATABASE"])
    schema_path = app.config["BASE_DIR"] / "schema.sql"

    database_path.parent.mkdir(parents=True, exist_ok=True)
    with app.app_context():
        db = get_db()
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
