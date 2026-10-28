"""Extract the tables a sqlglot expression reads from and writes to."""

from __future__ import annotations

from sqlglot import exp

from sproc_to_lakehouse.models import TableKind, TableRef


def table_ref(table: exp.Table) -> TableRef:
    this = table.this
    if isinstance(this, exp.Parameter):
        return TableRef(name="@" + this.name.lower(), kind=TableKind.TABLE_VARIABLE)
    name = table.name.lower()
    if isinstance(this, exp.Identifier):
        if this.args.get("global_"):
            return TableRef(name="##" + name, kind=TableKind.GLOBAL_TEMP)
        if this.args.get("temporary"):
            return TableRef(name="#" + name, kind=TableKind.TEMP)
    return TableRef(
        name=name,
        kind=TableKind.PERMANENT,
        schema=table.db.lower() or None,
        database=table.catalog.lower() or None,
    )


def _unwrap(node: exp.Expression | None) -> exp.Table | None:
    """INSERT INTO t (a, b) puts the table inside a Schema node."""
    if isinstance(node, exp.Schema):
        node = node.this
    return node if isinstance(node, exp.Table) else None


def _resolve_alias(expression: exp.Expression, target: exp.Table) -> exp.Table:
    """``UPDATE s ... FROM dbo.shipments s``: the target ``s`` is an alias, resolve it."""
    if target.db or isinstance(target.this, exp.Parameter):
        return target
    alias = target.name.lower()
    for table in expression.find_all(exp.Table):
        if table is not target and table.alias and table.alias.lower() == alias:
            return table
    return target


def write_targets(expression: exp.Expression) -> list[exp.Table]:
    """Table nodes that a DML expression modifies (resolved through aliases)."""
    if isinstance(expression, exp.Insert):
        targets = [_unwrap(expression.this)]
    elif isinstance(expression, (exp.Update, exp.Delete, exp.Merge)):
        target = _unwrap(expression.this)
        targets = [_resolve_alias(expression, target) if target is not None else None]
    elif isinstance(expression, exp.Select) and expression.args.get("into") is not None:
        targets = [_unwrap(expression.args["into"].this)]
    elif isinstance(expression, exp.TruncateTable):
        targets = [_unwrap(e) for e in expression.expressions]
    else:
        targets = []
    return [t for t in targets if t is not None]


def _ddl_table(expression: exp.Expression) -> exp.Table | None:
    if isinstance(expression, exp.Drop):
        tables = expression.args.get("tables") or [expression.this]
        return _unwrap(tables[0]) if tables else None
    if isinstance(expression, exp.Create):
        table = _unwrap(expression.this)
        if table is None and isinstance(expression.this, exp.Index):
            table = expression.this.args.get("table")
        return table if isinstance(table, exp.Table) else None
    return None


def ddl_target(expression: exp.Expression) -> TableRef | None:
    table = _ddl_table(expression)
    return table_ref(table) if table is not None else None


def reads_and_writes(
    expression: exp.Expression,
) -> tuple[tuple[TableRef, ...], tuple[TableRef, ...]]:
    targets = write_targets(expression)
    target_nodes = {id(t) for t in targets}
    if isinstance(expression, (exp.Update, exp.Delete, exp.Merge, exp.Insert)):
        # The syntactic target (possibly just an alias) is not a read.
        syntactic = _unwrap(expression.this)
        if syntactic is not None:
            target_nodes.add(id(syntactic))
    if isinstance(expression, exp.Delete):
        # ``DELETE d FROM dbo.t AS d JOIN ...`` lists the alias ``d`` in ``tables``.
        target_nodes.update(id(t) for t in expression.args.get("tables") or [])
    ddl_table = _ddl_table(expression)
    if ddl_table is not None:
        target_nodes.add(id(ddl_table))

    cte_names = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}
    reads: list[TableRef] = []
    for table in expression.find_all(exp.Table):
        if id(table) in target_nodes:
            continue
        if not table.db and table.name.lower() in cte_names:
            continue
        ref = table_ref(table)
        if ref not in reads:
            reads.append(ref)

    writes: list[TableRef] = []
    for table in targets:
        ref = table_ref(table)
        if ref not in writes:
            writes.append(ref)
    return tuple(reads), tuple(writes)
