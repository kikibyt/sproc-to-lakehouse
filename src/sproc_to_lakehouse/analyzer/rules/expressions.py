"""Expression-level constructs: functions, operators and their Databricks semantics.

Each rule reports at most once per statement (or IF/WHILE condition) to keep reports
readable; the message says what was found.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlglot import exp

from sproc_to_lakehouse.analyzer import rewrites as rw
from sproc_to_lakehouse.analyzer.registry import (
    Rule,
    SqlUnit,
    call_argument_count,
    function_calls,
    iter_units,
    rule,
    snippet,
)
from sproc_to_lakehouse.catalog import Catalog
from sproc_to_lakehouse.models import Finding, Procedure, Severity
from sproc_to_lakehouse.sqltypes import TypeResolver, transient_schemas
from sproc_to_lakehouse.sqlutil import STRING_FUNCTIONS, is_string_concat
from sproc_to_lakehouse.typed_rewrites import bit_to_boolean, changed, compact_date_literal


def _parsed_units(procedure: Procedure) -> Iterator[tuple[SqlUnit, exp.Expression]]:
    for unit in iter_units(procedure):
        if unit.expression is not None:
            yield unit, unit.expression


def _auto_construct(
    r: Rule, procedure: Procedure, node_types: tuple[type[exp.Expression], ...], message: str
) -> Iterator[Finding]:
    for unit, expression in _parsed_units(procedure):
        if any(expression.find_all(*node_types)):
            yield r.finding(Severity.AUTO, message, unit.line, snippet(unit.sql))


@rule("EX001", "cte", "Common table expressions (WITH)")
def cte(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    yield from _auto_construct(r, procedure, (exp.With,), "CTEs are supported natively.")


@rule("EX002", "window_function", "Window functions (OVER ...)")
def window_function(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    yield from _auto_construct(
        r, procedure, (exp.Window,), "Window functions and frames translate directly."
    )


@rule("EX003", "case_expression", "CASE / IIF")
def case_expression(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    yield from _auto_construct(
        r, procedure, (exp.Case, exp.If), "CASE and IIF translate directly (IIF becomes IF)."
    )


@rule("EX004", "date_function", "Date arithmetic (DATEADD, EOMONTH, DATEFROMPARTS, ...)")
def date_function(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    yield from _auto_construct(
        r,
        procedure,
        (exp.DateAdd, exp.LastDay, exp.DateFromParts, exp.Year, exp.Month, exp.Day),
        "Date functions map to Databricks equivalents (DATEADD, LAST_DAY, MAKE_DATE, ...).",
    )


@rule("EX005", "datediff", "DATEDIFF")
def datediff(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for unit, expression in _parsed_units(procedure):
        units = sorted({d.text("unit").upper() or "DAY" for d in expression.find_all(exp.DateDiff)})
        if units:
            yield r.finding(
                Severity.AUTO_WITH_NOTE,
                f"DATEDIFF({'/'.join(units)}): SQL Server counts unit boundaries crossed, "
                "Databricks counts whole units elapsed. Identical for DATE inputs in days; "
                "can differ by one for datetimes with a time part or for larger units.",
                unit.line,
                snippet(unit.sql),
            )


@rule("EX006", "datepart_week", "DATEPART week / weekday")
def datepart_week(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for unit, expression in _parsed_units(procedure):
        parts = {e.text("this").upper() for e in expression.find_all(exp.Extract)}
        if "WEEKISO" in parts:
            yield r.finding(
                Severity.AUTO_WITH_NOTE,
                "DATEPART(ISO_WEEK): sqlglot emits EXTRACT(WEEKISO ...), which is not a "
                "Databricks field; rewritten to EXTRACT(WEEK ...), which is ISO 8601.",
                unit.line,
                snippet(unit.sql),
                rewrite=rw.ISO_WEEK_EXTRACT,
            )
        if parts & {"DAYOFWEEK", "WEEK"}:
            yield r.finding(
                Severity.AUTO_WITH_NOTE,
                "Weekday/week numbers in SQL Server depend on SET DATEFIRST and the login "
                "language; Databricks uses fixed conventions (DAYOFWEEK: 1 = Sunday).",
                unit.line,
                snippet(unit.sql),
            )


@rule("EX007", "current_time", "GETDATE / SYSDATETIME")
def current_time(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for unit, expression in _parsed_units(procedure):
        if any(expression.find_all(exp.CurrentTimestamp, exp.CurrentDate)):
            yield r.finding(
                Severity.AUTO_WITH_NOTE,
                "Current time: SQL Server returns the server's local time; Databricks "
                "returns the session time zone (UTC unless configured).",
                unit.line,
                snippet(unit.sql),
            )


@rule("EX008", "convert", "CONVERT / TRY_CONVERT")
def convert(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for unit in iter_units(procedure):
        for call in function_calls(unit.tokens, "CONVERT", "TRY_CONVERT"):
            has_style = call_argument_count(unit.tokens, call) >= 3
            if call.value == "TRY_CONVERT" and has_style:
                yield r.finding(
                    Severity.AUTO_WITH_NOTE,
                    "TRY_CONVERT with a style code: sqlglot emits TO_DATE/TO_TIMESTAMP, which "
                    "raises on bad input under ANSI mode instead of returning NULL; rewritten "
                    "to the TRY_ variant.",
                    call.line,
                    snippet(unit.sql),
                    rewrite=rw.TRY_CONVERT_SAFE_PARSE,
                )
            elif call.value == "CONVERT" and has_style:
                yield r.finding(
                    Severity.AUTO_WITH_NOTE,
                    "CONVERT style code is mapped to a Databricks format pattern; verify the "
                    "output format on sample data.",
                    call.line,
                    snippet(unit.sql),
                )
            elif call.value == "CONVERT":
                yield r.finding(
                    Severity.AUTO_WITH_NOTE,
                    "CONVERT without a style becomes TRY_CAST: invalid values turn into NULL "
                    "instead of failing the procedure.",
                    call.line,
                    snippet(unit.sql),
                )


@rule("EX009", "isnull", "ISNULL")
def isnull(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for unit in iter_units(procedure):
        if function_calls(unit.tokens, "ISNULL"):
            yield r.finding(
                Severity.AUTO_WITH_NOTE,
                "ISNULL becomes COALESCE. ISNULL returns the type of its first argument "
                "(and can truncate the fallback value); COALESCE uses type precedence.",
                unit.line,
                snippet(unit.sql),
            )


@rule("EX010", "string_concat_plus", "String concatenation with +")
def string_concat_plus(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for unit, expression in _parsed_units(procedure):
        if any(is_string_concat(add) for add in expression.find_all(exp.Add)):
            yield r.finding(
                Severity.AUTO_WITH_NOTE,
                "'+' concatenates strings in SQL Server but is numeric addition in "
                "Databricks (sqlglot leaves it unchanged, which yields NULL or an error); "
                "rewritten to CONCAT. NULL handling is the same: any NULL gives NULL.",
                unit.line,
                snippet(unit.sql),
                rewrite=rw.PLUS_TO_CONCAT,
            )


@rule("EX011", "string_function", "String functions")
def string_function(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    yield from _auto_construct(
        r,
        procedure,
        STRING_FUNCTIONS,
        "String functions map directly (CHARINDEX becomes LOCATE, LTRIM/RTRIM stay).",
    )


@rule("EX012", "len_trailing_spaces", "LEN")
def len_trailing_spaces(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for unit in iter_units(procedure):
        if function_calls(unit.tokens, "LEN"):
            yield r.finding(
                Severity.AUTO_WITH_NOTE,
                "LEN ignores trailing spaces; Databricks LENGTH counts them. Equal only "
                "when values are already right-trimmed.",
                unit.line,
                snippet(unit.sql),
            )


# Functions recognised by sqlglot but emitted unchanged although Databricks lacks them.
_PASSTHROUGH_FUNCTIONS: dict[type[exp.Expression], str] = {
    exp.Stuff: "STUFF",
    exp.NumberToStr: "FORMAT",
}
# Anonymous functions covered by more specific rules (or by TRY/CATCH review).
_HANDLED_ELSEWHERE = {
    "SCOPE_IDENTITY", "IDENT_CURRENT", "ERROR_MESSAGE", "ERROR_NUMBER", "ERROR_SEVERITY",
    "ERROR_STATE", "ERROR_LINE", "ERROR_PROCEDURE", "RAISERROR",
}  # fmt: skip


@rule("EX013", "unsupported_function", "Function without a Databricks equivalent")
def unsupported_function(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for unit, expression in _parsed_units(procedure):
        names = {
            name
            for node_type, name in _PASSTHROUGH_FUNCTIONS.items()
            if any(expression.find_all(node_type))
        }
        names |= {
            a.name.upper()
            for a in expression.find_all(exp.Anonymous)
            if a.name.upper() not in _HANDLED_ELSEWHERE
        }
        if names:
            listed = ", ".join(f"{n}()" for n in sorted(names))
            yield r.finding(
                Severity.NEEDS_REVIEW,
                f"{listed}: no Databricks equivalent; sqlglot passes the call through "
                "unchanged, so the generated SQL would fail. Needs a manual rewrite.",
                unit.line,
                snippet(unit.sql),
            )


@rule("EX014", "string_agg", "STRING_AGG")
def string_agg(r: Rule, procedure: Procedure) -> Iterator[Finding]:
    for unit, expression in _parsed_units(procedure):
        if any(expression.find_all(exp.GroupConcat)):
            yield r.finding(
                Severity.AUTO_WITH_NOTE,
                "STRING_AGG becomes LISTAGG, which requires a recent Databricks Runtime; on "
                "older runtimes use array_join(collect_list(...)). Check your runtime version.",
                unit.line,
                snippet(unit.sql),
            )


def _typed_units(procedure: Procedure, catalog: Catalog):
    resolver = TypeResolver(catalog, transient_schemas(procedure.statements()))
    for unit, expression in _parsed_units(procedure):
        yield unit, expression, resolver


@rule("EX015", "bit_column", "BIT column used as 0/1", needs_catalog=True)
def bit_column(r: Rule, procedure: Procedure, catalog: Catalog) -> Iterator[Finding]:
    for unit, expression, resolver in _typed_units(procedure, catalog):
        if changed(expression, bit_to_boolean(expression, resolver)):
            yield r.finding(
                Severity.AUTO_WITH_NOTE,
                "BIT column compared with or assigned 0/1. On Databricks BIT is BOOLEAN and "
                "ANSI mode rejects BOOLEAN = INT; rewritten to TRUE/FALSE and "
                "CAST(... AS BOOLEAN).",
                unit.line,
                snippet(unit.sql),
                rewrite=rw.BIT_TO_BOOLEAN,
            )


@rule("EX016", "compact_date_literal", "'yyyymmdd' string used as a date", needs_catalog=True)
def compact_date(r: Rule, procedure: Procedure, catalog: Catalog) -> Iterator[Finding]:
    for unit, expression, resolver in _typed_units(procedure, catalog):
        if changed(expression, compact_date_literal(expression, resolver)):
            yield r.finding(
                Severity.AUTO_WITH_NOTE,
                "Compact date string such as '19000101' compared with a date. SQL Server "
                "converts it implicitly; Spark fails at run time (CAST_INVALID_INPUT). "
                "Rewritten to a DATE literal.",
                unit.line,
                snippet(unit.sql),
                rewrite=rw.COMPACT_DATE_LITERAL,
            )


RULES: list[Rule] = [
    cte,
    window_function,
    case_expression,
    date_function,
    datediff,
    datepart_week,
    current_time,
    convert,
    isnull,
    string_concat_plus,
    string_function,
    len_trailing_spaces,
    unsupported_function,
    string_agg,
    bit_column,
    compact_date,
]
