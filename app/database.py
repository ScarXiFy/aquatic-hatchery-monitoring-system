import sqlite3
from pathlib import Path

from flask import current_app, g


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(error=None):
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
