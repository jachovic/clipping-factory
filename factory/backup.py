"""Database backups (daily job). SQLite uses the online backup API; Postgres uses pg_dump."""
from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

from .config import settings
from .models import now

KEEP = 14


def backup_db() -> Path:
    out_dir = settings.data_dir / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now().strftime("%Y%m%d-%H%M%S")
    url = settings.db_url
    if url.startswith("sqlite:///"):
        src = sqlite3.connect(url.removeprefix("sqlite:///"))
        dst_path = out_dir / f"factory-{stamp}.db"
        dst = sqlite3.connect(dst_path)
        src.backup(dst)
        dst.close()
        src.close()
    else:
        dst_path = out_dir / f"factory-{stamp}.sql.gz"
        pg_url = url.replace("postgresql+psycopg://", "postgresql://")
        with open(dst_path, "wb") as fh:
            dump = subprocess.Popen(["pg_dump", pg_url], stdout=subprocess.PIPE)
            subprocess.run(["gzip", "-c"], stdin=dump.stdout, stdout=fh, check=True)
            if dump.wait() != 0:
                raise RuntimeError("pg_dump failed")
    os.chmod(dst_path, 0o600)
    for old in sorted(out_dir.glob("factory-*"))[:-KEEP]:
        old.unlink()
    return dst_path
