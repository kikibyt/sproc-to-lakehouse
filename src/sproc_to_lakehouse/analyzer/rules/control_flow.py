"""Procedural control flow: cursors, loops, error handling, transactions, dynamic SQL."""

from __future__ import annotations

from collections.abc import Iterator

from sproc_to_lakehouse.analyzer.registry import (
    Rule,
    is_temp_cleanup,
    parameter_default,
    rule,
    snippet,
)
from sproc_to_lakehouse.models import (
    Finding,
    IfNode,
    Procedure,
    Severity,
    Statement,
    StatementKind,
    TryCatchNode,
    WhileNode,
)

K = StatementKind


def _statements(procedure: Procedure, *kinds: StatementKind) -> Iterator[Statement]:
    return (s for s in procedure.statements() if s.kind in kinds)


@rule("CF001", "cursor", "Row-by-row cursor processing")
def cursor(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in _statements(procedure, K.DECLARE_CURSOR):
        yield r.finding(
            Severity.NEEDS_REVIEW,
            "Cursor loops process one row at a time. Spark has no cursors; the loop body "
            "must be re-expressed as set-based logic (joins, window functions, aggregates).",
            stmt.line,
            snippet(stmt.sql),
        )


@rule("CF002", "while_loop", "WHILE loop")
def while_loop(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for node in procedure.iter_nodes():
        if isinstance(node, WhileNode):
            yield r.finding(
                Severity.NEEDS_REVIEW,
                "Procedural loop. Its termination and per-iteration state cannot be "
                "translated mechanically; rewrite set-based or as a driver loop in Python.",
                node.line,
                snippet("WHILE " + node.condition.sql),
            )


@rule("CF003", "try_catch", "TRY/CATCH error handling")
def try_catch(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for node in procedure.iter_nodes():
        if isinstance(node, TryCatchNode):
            yield r.finding(
                Severity.NEEDS_REVIEW,
                "Error handling must be redesigned: a notebook uses Python try/except, while "
                "dbt has no CATCH equivalent (failures stop the run; use on-run-end hooks or "
                "orchestration retries).",
                node.line,
                "BEGIN TRY ... END TRY BEGIN CATCH ... END CATCH",
            )


@rule("CF004", "transaction", "Explicit multi-statement transaction")
def transaction(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in _statements(procedure, K.BEGIN_TRANSACTION):
        yield r.finding(
            Severity.NEEDS_REVIEW,
            "Delta Lake commits each statement atomically per table, but this procedure "
            "relies on several statements committing or rolling back together. Needs a "
            "design decision (idempotent re-runs, staging then swap, or multi-statement "
            "transactions if available in your workspace).",
            stmt.line,
            snippet(stmt.sql),
        )


@rule("CF005", "dynamic_sql", "Dynamic SQL execution")
def dynamic_sql(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in _statements(procedure, K.EXEC_DYNAMIC):
        yield r.finding(
            Severity.NEEDS_REVIEW,
            "SQL text is assembled at run time, so it cannot be analysed or converted "
            "statically. Rewrite with the shape known in advance (e.g. PySpark pivot() "
            "or a dbt macro that builds the column list).",
            stmt.line,
            snippet(stmt.sql),
        )


@rule("CF006", "procedure_call", "Call to another stored procedure")
def procedure_call(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in _statements(procedure, K.EXEC_PROCEDURE):
        yield r.finding(
            Severity.NEEDS_REVIEW,
            "Calls another procedure, which must be converted first and invoked as a "
            "dependency (dbt ref / notebook run) rather than a nested call.",
            stmt.line,
            snippet(stmt.sql),
        )


@rule("CF007", "raise_error", "THROW / RAISERROR")
def raise_error(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in _statements(procedure, K.THROW, K.RAISERROR):
        yield r.finding(
            Severity.NEEDS_REVIEW,
            "Raises a SQL Server error. Map to a Python exception (notebook) or a dbt "
            "test / exceptions.raise_compiler_error, and confirm what callers expect.",
            stmt.line,
            snippet(stmt.sql),
        )


@rule("CF008", "if_branch", "IF / ELSE branching")
def if_branch(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for node in procedure.iter_nodes():
        if not isinstance(node, IfNode) or is_temp_cleanup(node):
            continue
        text = snippet("IF " + node.condition.sql)
        if parameter_default(node) is not None:
            yield r.finding(
                Severity.AUTO,
                "Parameter default (IF @p IS NULL SET @p = ...): folded into COALESCE(@p, ...).",
                node.line,
                text,
            )
        elif node.condition.reads:
            tables = ", ".join(t.qualified_name for t in node.condition.reads)
            yield r.finding(
                Severity.NEEDS_REVIEW,
                f"Branch depends on table data ({tables}) evaluated at run time. dbt models "
                "cannot branch on data; express it as a set-based condition or MERGE.",
                node.line,
                text,
            )
        else:
            yield r.finding(
                Severity.NEEDS_REVIEW,
                "Procedural branch on variable values. A notebook can use a Python `if` and "
                "dbt a Jinja `{% if %}` on vars, but which statements run then depends on "
                "run-time values; not generated automatically.",
                node.line,
                text,
            )


@rule("CF009", "return_contract", "OUTPUT parameters / RETURN values")
def return_contract(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for param in procedure.parameters:
        if param.is_output:
            yield r.finding(
                Severity.NEEDS_REVIEW,
                f"OUTPUT parameter {param.name} passes a value back to the caller; the "
                "calling code must be changed to read it from a table or task value.",
                procedure.line,
                f"{param.name} {param.data_type} OUTPUT",
            )
    for stmt in _statements(procedure, K.RETURN):
        if len(stmt.tokens) > 1:
            yield r.finding(
                Severity.NEEDS_REVIEW,
                "RETURN with a status code is part of the caller contract and has no "
                "dbt/notebook equivalent.",
                stmt.line,
                snippet(stmt.sql),
            )


@rule("CF010", "print", "PRINT diagnostic output")
def print_statement(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in _statements(procedure, K.PRINT):
        yield r.finding(
            Severity.AUTO_WITH_NOTE,
            "Diagnostic message; becomes print()/logging in the notebook and is dropped "
            "from dbt models.",
            stmt.line,
            snippet(stmt.sql),
        )


RULES: list[Rule] = [
    cursor,
    while_loop,
    try_catch,
    transaction,
    dynamic_sql,
    procedure_call,
    raise_error,
    if_branch,
    return_contract,
    print_statement,
]
