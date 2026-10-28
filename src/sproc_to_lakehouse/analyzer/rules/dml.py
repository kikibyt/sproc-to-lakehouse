"""Data-modification statements and their Delta Lake equivalents."""

from __future__ import annotations

from collections.abc import Iterator

from sqlglot import exp

from sproc_to_lakehouse.analyzer import rewrites as rw
from sproc_to_lakehouse.analyzer.registry import Rule, rule, snippet
from sproc_to_lakehouse.models import Finding, Procedure, Severity, Statement, StatementKind

K = StatementKind
_DML = {K.INSERT, K.UPDATE, K.DELETE, K.MERGE}


def _has_top(stmt: Statement) -> bool:
    return (
        stmt.kind in (K.UPDATE, K.DELETE) and len(stmt.tokens) > 1 and stmt.tokens[1].is_word("TOP")
    )


@rule("DM001", "merge", "MERGE upsert")
def merge(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in procedure.statements():
        if not isinstance(stmt.expression, exp.Merge):
            continue
        message = (
            "Maps to Delta MERGE INTO. Like SQL Server, Delta fails when several source "
            "rows match one target row, so source de-duplication must be preserved."
        )
        if "NOT MATCHED BY SOURCE" in " ".join(stmt.sql.upper().split()):
            message += " WHEN NOT MATCHED BY SOURCE needs Databricks Runtime 12.2 LTS or later."
        yield r.finding(Severity.AUTO_WITH_NOTE, message, stmt.line, snippet(stmt.sql))


@rule("DM002", "update_with_join", "UPDATE/DELETE with FROM ... JOIN")
def update_with_join(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in procedure.statements():
        e = stmt.expression
        joined = (isinstance(e, exp.Update) and e.args.get("from_") is not None) or (
            isinstance(e, exp.Delete)
            and (e.args.get("tables") or (e.this is not None and e.this.args.get("joins")))
        )
        if joined:
            yield r.finding(
                Severity.AUTO_WITH_NOTE,
                "Delta UPDATE/DELETE has no FROM/JOIN clause; rewritten as MERGE INTO with "
                "the join as the source. If the join matches a target row more than once, "
                "SQL Server picks one silently but Delta MERGE fails.",
                stmt.line,
                snippet(stmt.sql),
                rewrite=rw.UPDATE_FROM_TO_MERGE,
            )


@rule("DM003", "top_in_dml", "UPDATE/DELETE TOP (n)")
def top_in_dml(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in procedure.statements():
        if _has_top(stmt):
            yield r.finding(
                Severity.NEEDS_REVIEW,
                "Batched modification with TOP (n) has no Delta equivalent and picks rows "
                "non-deterministically. Usually a whole-set operation replaces the batch loop.",
                stmt.line,
                snippet(stmt.sql),
            )


@rule("DM004", "output_clause", "OUTPUT clause on DML")
def output_clause(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in procedure.statements():
        if stmt.kind in _DML and any(t.is_word("OUTPUT") for t in stmt.tokens):
            yield r.finding(
                Severity.NEEDS_REVIEW,
                "OUTPUT returns or captures the affected rows. Delta DML does not; select "
                "the rows first, write them, then modify the source.",
                stmt.line,
                snippet(stmt.sql),
            )


@rule("DM005", "unparsed_statement", "Statement sqlglot could not parse")
def unparsed_statement(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in procedure.statements():
        if stmt.parse_error and not _has_top(stmt):
            yield r.finding(
                Severity.NEEDS_REVIEW,
                f"Not converted automatically: {stmt.parse_error}. Kept verbatim for manual work.",
                stmt.line,
                snippet(stmt.sql),
            )


@rule("DM006", "truncate", "TRUNCATE TABLE")
def truncate(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in procedure.statements():
        if stmt.kind is K.TRUNCATE:
            yield r.finding(
                Severity.AUTO,
                "Full reload: becomes a table materialization (dbt) / overwrite mode (notebook).",
                stmt.line,
                snippet(stmt.sql),
            )


@rule("DM007", "result_set", "SELECT returning rows to the caller")
def result_set(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for stmt in procedure.statements():
        if stmt.kind is K.SELECT:
            yield r.finding(
                Severity.AUTO_WITH_NOTE,
                "Returns a result set to the caller; becomes a model/view (dbt) or a "
                "displayed DataFrame (notebook). Consumers must read it from there.",
                stmt.line,
                snippet(stmt.sql),
            )


RULES: list[Rule] = [
    merge,
    update_with_join,
    top_in_dml,
    output_clause,
    unparsed_statement,
    truncate,
    result_set,
]
