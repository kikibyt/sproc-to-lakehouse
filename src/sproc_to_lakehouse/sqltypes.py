"""Best-effort column type resolution using the DDL catalog.

Some T-SQL idioms are only wrong on Databricks for certain column types: comparing a
BIT column with ``1``, or comparing a DATE with the string ``'19000101'``. This module
answers "what type is this expression?" by following table aliases, CTEs, subqueries
and temp-table declarations back to the catalog. It returns ``None`` whenever it is not
sure, and callers then leave the SQL unchanged.
"""

from __future__ import annotations

from sqlglot import exp

from sproc_to_lakehouse.catalog import Catalog, TableSchema, column_from_def

_SCOPES = (exp.Select, exp.Update, exp.Delete, exp.Merge)


class TypeResolver:
    def __init__(self, catalog: Catalog, extra_tables: list[TableSchema] | None = None) -> None:
        self.catalog = catalog
        self.extra = {t.name: t for t in extra_tables or []}

    # -- tables ----------------------------------------------------------------------
    def table_schema(self, table: exp.Table) -> TableSchema | None:
        name = table.name.lower()
        if isinstance(table.this, exp.Parameter):
            return self.extra.get("@" + name)
        if isinstance(table.this, exp.Identifier) and table.this.args.get("temporary"):
            return self.extra.get("#" + name)
        schema = (table.db or "dbo").lower()
        return self.catalog.get(f"{schema}.{name}") or self.extra.get(name)

    def column_type_in_table(self, table: exp.Table, column: str) -> exp.DataType | None:
        schema = self.table_schema(table)
        col = schema.column(column) if schema is not None else None
        return exp.DataType.build(col.data_type, dialect="tsql") if col is not None else None

    # -- expressions -----------------------------------------------------------------
    def type_of(self, node: exp.Expression | None) -> exp.DataType | None:
        while isinstance(node, (exp.Paren, exp.Alias)):
            node = node.this
        if node is None:
            return None
        if isinstance(node, (exp.Cast, exp.TryCast)):
            return node.to
        if isinstance(node, exp.Convert):  # T-SQL CONVERT(type, value[, style])
            return node.this if isinstance(node.this, exp.DataType) else None
        if isinstance(node, exp.Column):
            return self.column_type(node)
        if isinstance(node, (exp.Coalesce,)):
            return self.type_of(node.this)
        if isinstance(node, (exp.CurrentDate,)):
            return exp.DataType.build("DATE")
        if isinstance(node, (exp.CurrentTimestamp,)):
            return exp.DataType.build("TIMESTAMP")
        if isinstance(node, (exp.LastDay, exp.DateFromParts)):
            return exp.DataType.build("DATE")
        return None

    def column_type(self, column: exp.Column) -> exp.DataType | None:
        name = column.name.lower()
        qualifier = column.table.lower()
        scope = column.parent
        while scope is not None:
            if isinstance(scope, _SCOPES):
                sources = _sources(scope)
                if qualifier:
                    source = sources.get(qualifier)
                    if source is not None:
                        return self._type_from_source(source, name, column)
                else:
                    for source in sources.values():
                        found = self._type_from_source(source, name, column)
                        if found is not None:
                            return found
                    return None
            scope = scope.parent
        return None

    def _type_from_source(
        self, source: exp.Expression, name: str, origin: exp.Expression
    ) -> exp.DataType | None:
        if isinstance(source, exp.Subquery):
            return self._type_from_query(source.this, name, None)
        if isinstance(source, exp.Table):
            cte = _find_cte(origin, source) if not source.db else None
            if cte is not None:
                columns = [c.name.lower() for c in cte.args["alias"].columns or []]
                return self._type_from_query(cte.this, name, columns or None)
            return self.column_type_in_table(source, name)
        return None

    def _type_from_query(
        self, query: exp.Expression, name: str, columns: list[str] | None
    ) -> exp.DataType | None:
        while isinstance(query, exp.SetOperation):
            query = query.this
        if not isinstance(query, exp.Select):
            return None
        projections = query.expressions
        if columns is not None:
            if name not in columns or columns.index(name) >= len(projections):
                return None
            return self.type_of(projections[columns.index(name)])
        for projection in projections:
            if projection.alias_or_name.lower() == name:
                return self.type_of(projection)
        return None


def _sources(scope: exp.Expression) -> dict[str, exp.Expression]:
    """Relations visible in one query scope, keyed by alias (or table name)."""
    relations: list[exp.Expression] = []
    if isinstance(scope, exp.Merge):
        relations += [scope.this, scope.args.get("using")]
    elif isinstance(scope, (exp.Update, exp.Delete)):
        relations.append(scope.this)
    from_ = scope.args.get("from_")
    if from_ is not None:
        relations.append(from_.this)
        relations += [j.this for j in from_.this.args.get("joins") or []]
    relations += [j.this for j in scope.args.get("joins") or []]
    if scope.this is not None and isinstance(scope.this, exp.Table):
        relations += [j.this for j in scope.this.args.get("joins") or []]
    return {
        (r.alias or r.name).lower(): r
        for r in relations
        if isinstance(r, (exp.Table, exp.Subquery)) and (r.alias or r.name)
    }


def _find_cte(origin: exp.Expression, table: exp.Table) -> exp.CTE | None:
    name = table.name.lower()
    node = origin
    while node is not None:
        with_ = node.args.get("with_") if isinstance(node, exp.Expression) else None
        if with_ is not None:
            for cte in with_.expressions:
                if cte.alias_or_name.lower() == name:
                    return cte
        node = node.parent
    return None


def transient_schemas(statements) -> list[TableSchema]:
    """Schemas of temp tables / table variables declared with column lists."""
    schemas: list[TableSchema] = []
    for stmt in statements:
        e = stmt.expression
        if isinstance(e, exp.Create) and isinstance(e.this, exp.Schema) and stmt.ddl_target:
            schema = TableSchema(None, stmt.ddl_target.name)
            schema.columns = [
                column_from_def(c) for c in e.this.expressions if isinstance(c, exp.ColumnDef)
            ]
            schemas.append(schema)
        elif isinstance(e, exp.Declare):
            for item in e.expressions:
                kind = item.args.get("kind")
                if isinstance(kind, exp.Schema):
                    target = item.this[0] if isinstance(item.this, list) else item.this
                    schema = TableSchema(None, "@" + target.name.lower())
                    schema.columns = [
                        column_from_def(c) for c in kind.expressions if isinstance(c, exp.ColumnDef)
                    ]
                    schemas.append(schema)
    return schemas


def is_bit(dtype: exp.DataType | None) -> bool:
    return dtype is not None and dtype.is_type(exp.DataType.Type.BIT, exp.DataType.Type.BOOLEAN)


def is_date_like(dtype: exp.DataType | None) -> bool:
    return dtype is not None and dtype.is_type(*exp.DataType.TEMPORAL_TYPES)
