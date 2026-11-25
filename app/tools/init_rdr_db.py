"""Build the RDR demo SQLite DB from data/schema.sql and data/seed.sql.

Run from repo root: python -m app.tools.init_rdr_db
Refuses to overwrite an existing DB. Delete data/rdr.db yourself to rebuild.
"""

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_DB = DATA_DIR / "rdr.db"


def main(db_path: str = str(DEFAULT_DB)) -> None:
    target = Path(db_path)
    if target.exists():
        sys.exit(f"{target} already exists. Delete it yourself to rebuild.")
    with closing(sqlite3.connect(target)) as conn:
        conn.executescript((DATA_DIR / "schema.sql").read_text(encoding="utf-8"))
        conn.executescript((DATA_DIR / "seed.sql").read_text(encoding="utf-8"))
    print(f"Built {target}")


if __name__ == "__main__":
    main(*sys.argv[1:])
