"""Table schemas read from T-SQL DDL (CREATE TABLE statements).

The catalog gives the generators column lists, nullability and primary keys, which
drive dbt schema tests and the column lists of generated models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str  # as written in T-SQL, e.g. DECIMAL(12,2)
    nullable: bool
    identity: bool = False  # IDENTITY(seed, increment)

    @property
    def databricks_type(self) -> str:
        """Databricks SQL type: BIT -> BOOLEAN, DATETIME2(0) -> TIMESTAMP, VARCHAR(n) -> STRING."""
        return databricks_type(self.data_type)


def databricks_type(tsql_type: str) -> str:
    dtype = exp.DataType.build(tsql_type, dialect="tsql")
    if dtype.is_type(*exp.DataType.TEMPORAL_TYPES):
        dtype.set("expressions", [])  # Databricks TIMESTAMP/DATE take no precision
    return dtype.sql(dialect="databricks")


@dataclass
class TableSchema:
    schema: str | None
    name: str
    columns: list[Column] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name.lower()), None)


class Catalog:
    """Lookup of table schemas by ``schema.table`` (lower case)."""

    def __init__(self, tables: list[TableSchema] | None = None) -> None:
        self._tables = {t.qualified_name: t for t in tables or []}

    def get(self, qualified_name: str) -> TableSchema | None:
        return self._tables.get(qualified_name.lower())

    def __contains__(self, qualified_name: str) -> bool:
        return qualified_name.lower() in self._tables

    def __len__(self) -> int:
        return len(self._tables)

    @classmethod
    def from_ddl(cls, sql: str) -> Catalog:
        tables = [
            _table_schema(statement)
            for statement in sqlglot.parse(sql, read="tsql")
            if isinstance(statement, exp.Create) and statement.kind == "TABLE"
        ]
        return cls(tables)

    @classmethod
    def from_files(cls, paths: list[Path]) -> Catalog:
        tables: list[TableSchema] = []
        for path in paths:
            tables.extend(cls.from_ddl(path.read_text(encoding="utf-8-sig"))._tables.values())
        return cls(tables)


def column_from_def(item: exp.ColumnDef) -> Column:
    constraints = [c.kind for c in item.constraints]
    is_pk = any(isinstance(c, exp.PrimaryKeyColumnConstraint) for c in constraints)
    not_null = any(
        isinstance(c, exp.NotNullColumnConstraint) and not c.args.get("allow_null")
        for c in constraints
    )
    identity = any(isinstance(c, exp.GeneratedAsIdentityColumnConstraint) for c in constraints)
    return Column(
        item.name.lower(),
        item.args["kind"].sql(dialect="tsql"),
        nullable=not (not_null or is_pk),
        identity=identity,
    )


def _table_schema(create: exp.Create) -> TableSchema:
    schema_node = create.this
    table = schema_node.this if isinstance(schema_node, exp.Schema) else schema_node
    result = TableSchema(schema=table.db.lower() or None, name=table.name.lower())
    if not isinstance(schema_node, exp.Schema):
        return result

    for item in schema_node.expressions:
        if isinstance(item, exp.ColumnDef):
            column = column_from_def(item)
            result.columns.append(column)
            if any(isinstance(c.kind, exp.PrimaryKeyColumnConstraint) for c in item.constraints):
                result.primary_key.append(column.name)
        elif isinstance(item, exp.PrimaryKey):
            result.primary_key = [
                (e.name if isinstance(e, exp.Expression) else str(e)).lower()
                for e in item.expressions
            ]
    return result
