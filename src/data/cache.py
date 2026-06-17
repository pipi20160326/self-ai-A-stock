from __future__ import annotations

import re
import hashlib
import sqlite3
from pathlib import Path

import pandas as pd


def _table_name(kind: str, *parts: str) -> str:
    raw = "_".join([kind, *[str(p) for p in parts if p is not None]])
    readable = re.sub(r"[^0-9a-zA-Z_]+", "_", raw).strip("_").lower()[:48]
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{readable}_{digest}"


class SqliteCache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self, kind: str, *parts: str) -> pd.DataFrame | None:
        table = _table_name(kind, *parts)
        with sqlite3.connect(self.path) as conn:
            exists = conn.execute(
                "select name from sqlite_master where type='table' and name=?",
                (table,),
            ).fetchone()
            if not exists:
                return None
            return pd.read_sql_query(f'select * from "{table}"', conn)

    def write(self, frame: pd.DataFrame, kind: str, *parts: str) -> None:
        table = _table_name(kind, *parts)
        with sqlite3.connect(self.path) as conn:
            frame.to_sql(table, conn, if_exists="replace", index=False)

    def has(self, kind: str, *parts: str) -> bool:
        return self.read(kind, *parts) is not None
