"""Reference engine: the original T-SQL running on Microsoft SQL Server (via pymssql)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sproc_to_lakehouse.catalog import Catalog
from sproc_to_lakehouse.parity.compare import RowSet

_GO = re.compile(r"(?im)^\s*GO\s*$")


@dataclass
class SqlServerConfig:
    host: str = "localhost"
    port: int = 1433
    user: str = "sa"
    password: str = ""
    database: str = "sproc_parity"


def batches(text: str) -> list[str]:
    """Split a script on GO separators, as sqlcmd and SSMS do."""
    return [b for b in _GO.split(text) if b.strip()]


class SqlServerEngine:
    name = "SQL Server"

    def __init__(self, config: SqlServerConfig) -> None:
        import pymssql  # optional dependency (pip install -e ".[parity]")

        self.config = config
        self.conn = pymssql.connect(
            server=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            autocommit=True,
            login_timeout=30,
        )
        self.cur = self.conn.cursor()

    def version(self) -> str:
        self.cur.execute("SELECT @@VERSION")
        return self.cur.fetchone()[0].splitlines()[0].strip()

    def setup(self, ddl_files: Iterable[Path], procedure_files: Iterable[Path]) -> None:
        """Create a fresh database with the tables and the original procedures."""
        db = self.config.database
        self.cur.execute("USE master")
        self.cur.execute(
            f"IF DB_ID('{db}') IS NOT NULL BEGIN "
            f"ALTER DATABASE [{db}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
            f"DROP DATABASE [{db}]; END"
        )
        self.cur.execute(f"CREATE DATABASE [{db}]")
        self.cur.execute(f"USE [{db}]")
        for path in [*ddl_files, *procedure_files]:
            for batch in batches(path.read_text(encoding="utf-8-sig")):
                self.cur.execute(batch)

    def load(self, data: dict[str, list[dict[str, Any]]], catalog: Catalog) -> None:
        """Replace the contents of every catalog table with ``data`` (empty if absent)."""
        for table in catalog._tables.values():
            name = table.qualified_name
            self.cur.execute(f"DELETE FROM {name}")
            rows = data.get(name) or []
            if not rows:
                continue
            columns = [c.name for c in table.columns]
            identity = any(c.identity for c in table.columns)
            sql = (
                f"INSERT INTO {name} ({', '.join(columns)}) "
                f"VALUES ({', '.join(['%s'] * len(columns))})"
            )
            if identity:
                self.cur.execute(f"SET IDENTITY_INSERT {name} ON")
            self.cur.executemany(sql, [tuple(row[c] for c in columns) for row in rows])
            if identity:
                self.cur.execute(f"SET IDENTITY_INSERT {name} OFF")

    def run(self, schema: str | None, procedure: str, params: dict[str, str]) -> list[RowSet]:
        """EXEC the original procedure; return any result sets it produced."""
        assignments = ", ".join(f"@{name} = %s" for name in params)
        qualified = f"{schema}.{procedure}" if schema else procedure
        self.cur.execute(f"EXEC {qualified} {assignments}".strip(), tuple(params.values()) or None)
        results: list[RowSet] = []
        while True:
            if self.cur.description:
                columns = [d[0] for d in self.cur.description]
                results.append(RowSet(columns, [tuple(r) for r in self.cur.fetchall()]))
            if not self.cur.nextset():
                break
        return results

    def read(self, table: str) -> RowSet:
        self.cur.execute(f"SELECT * FROM {table}")
        columns = [d[0] for d in self.cur.description]
        return RowSet(columns, [tuple(r) for r in self.cur.fetchall()])

    def close(self) -> None:
        self.conn.close()
