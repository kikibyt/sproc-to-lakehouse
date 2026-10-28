"""Turn a token slice into a classified :class:`Statement`, parsed by sqlglot where possible."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from sproc_to_lakehouse.models import Condition, Statement, StatementKind, TableKind, TableRef
from sproc_to_lakehouse.parser.lexer import Token, TokenKind
from sproc_to_lakehouse.parser.tables import ddl_target, reads_and_writes

K = StatementKind

# Statement kinds whose text is handed to sqlglot, and the expression type we expect back.
# A different type means sqlglot silently mis-parsed the statement (e.g. ``OPEN cur`` is
# parsed as a column alias), so we treat that as a parse failure rather than trust it.
_EXPECTED_EXPRESSION: dict[
    StatementKind, type[exp.Expression] | tuple[type[exp.Expression], ...]
] = {
    K.SELECT: (exp.Select, exp.SetOperation),
    K.SELECT_INTO: exp.Select,
    K.SELECT_ASSIGN: exp.Select,
    K.INSERT: exp.Insert,
    K.UPDATE: exp.Update,
    K.DELETE: exp.Delete,
    K.MERGE: exp.Merge,
    K.TRUNCATE: exp.TruncateTable,
    K.CREATE_TABLE: exp.Create,
    K.CREATE_INDEX: exp.Create,
    K.DROP_TABLE: exp.Drop,
    K.DECLARE_VARIABLE: exp.Declare,
    K.DECLARE_TABLE_VARIABLE: exp.Declare,
    K.SET_VARIABLE: exp.Set,
}

_DML_WORDS = ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE")


@contextmanager
def _quiet_sqlglot() -> Iterator[None]:
    """sqlglot logs a warning whenever it falls back to a generic Command; we report those
    ourselves, so silence the logger while parsing."""
    logger = logging.getLogger("sqlglot")
    previous = logger.level
    logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        logger.setLevel(previous)


def parse_sql(text: str) -> tuple[exp.Expression | None, str | None]:
    """Parse one T-SQL statement or expression. Returns ``(expression, error)``."""
    try:
        with _quiet_sqlglot():
            expression = sqlglot.parse_one(text, read="tsql")
    except SqlglotError as exc:
        first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return None, f"sqlglot could not parse: {first_line}"
    if isinstance(expression, exp.Command):
        return None, "sqlglot fell back to an unparsed generic command"
    return expression, None


def _depth0(tokens: tuple[Token, ...]) -> Iterator[tuple[int, Token]]:
    depth = 0
    for i, tok in enumerate(tokens):
        if tok.is_punct("("):
            depth += 1
        elif tok.is_punct(")"):
            depth -= 1
        elif depth == 0:
            yield i, tok


def classify(tokens: tuple[Token, ...]) -> StatementKind:
    first = tokens[0]
    second = tokens[1] if len(tokens) > 1 else None
    word = first.value if first.kind is TokenKind.WORD else ""

    if word == "WITH":
        main = next((t.value for _, t in _depth0(tokens[1:]) if t.is_word(*_DML_WORDS)), "")
        return _classify_dml(main, tokens) if main else K.OTHER
    if word in _DML_WORDS:
        return _classify_dml(word, tokens)
    if word == "DECLARE":
        if any(t.is_word("CURSOR") for t in tokens[1:4]):
            return K.DECLARE_CURSOR
        if (len(tokens) > 2 and tokens[2].is_word("TABLE")) or (
            len(tokens) > 3 and tokens[2].is_word("AS") and tokens[3].is_word("TABLE")
        ):
            return K.DECLARE_TABLE_VARIABLE
        return K.DECLARE_VARIABLE
    if word == "SET":
        return K.SET_VARIABLE if second and second.kind is TokenKind.VARIABLE else K.SET_OPTION
    if word in ("EXEC", "EXECUTE"):
        return _classify_exec(tokens)
    if word == "BEGIN" and second and second.is_word("TRAN", "TRANSACTION", "DISTRIBUTED"):
        return K.BEGIN_TRANSACTION
    if word == "CREATE":
        if any(t.is_word("INDEX") for t in tokens[1:4]):
            return K.CREATE_INDEX
        return K.CREATE_TABLE if second and second.is_word("TABLE") else K.OTHER
    if word == "DROP":
        return K.DROP_TABLE if second and second.is_word("TABLE") else K.OTHER
    simple = {
        "TRUNCATE": K.TRUNCATE,
        "OPEN": K.OPEN_CURSOR,
        "FETCH": K.FETCH_CURSOR,
        "CLOSE": K.CLOSE_CURSOR,
        "DEALLOCATE": K.DEALLOCATE_CURSOR,
        "COMMIT": K.COMMIT,
        "ROLLBACK": K.ROLLBACK,
        "THROW": K.THROW,
        "RAISERROR": K.RAISERROR,
        "PRINT": K.PRINT,
        "RETURN": K.RETURN,
        "BREAK": K.BREAK,
        "CONTINUE": K.CONTINUE,
    }
    return simple.get(word, K.OTHER)


def _classify_dml(word: str, tokens: tuple[Token, ...]) -> StatementKind:
    if word != "SELECT":
        return {"INSERT": K.INSERT, "UPDATE": K.UPDATE, "DELETE": K.DELETE, "MERGE": K.MERGE}[word]
    depth0 = list(_depth0(tokens))
    select_idx = next(i for i, (_, t) in enumerate(depth0) if t.is_word("SELECT"))
    after = [t for _, t in depth0[select_idx + 1 :]]
    while after and after[0].is_word("DISTINCT", "ALL"):
        after = after[1:]
    if len(after) >= 2 and after[0].kind is TokenKind.VARIABLE and after[1].is_punct("="):
        return K.SELECT_ASSIGN
    for tok in after:
        if tok.is_word("FROM", "UNION", "EXCEPT", "INTERSECT"):
            break
        if tok.is_word("INTO"):
            return K.SELECT_INTO
    return K.SELECT


def _classify_exec(tokens: tuple[Token, ...]) -> StatementKind:
    rest = tokens[1:]
    if not rest:
        return K.OTHER
    if rest[0].is_punct("("):
        return K.EXEC_DYNAMIC  # EXEC (@sql)
    if rest[0].kind is TokenKind.VARIABLE:
        # EXEC @rc = dbo.proc  -> static call capturing a return code
        # EXEC @proc_name      -> procedure name chosen at runtime
        if len(rest) > 1 and rest[1].is_punct("="):
            rest = rest[2:]
        else:
            return K.EXEC_DYNAMIC
    if any(t.is_word("SP_EXECUTESQL") for t in rest[:3]):
        return K.EXEC_DYNAMIC
    return K.EXEC_PROCEDURE


def _slice_text(sql: str, tokens: tuple[Token, ...]) -> str:
    return sql[tokens[0].start : tokens[-1].end + 1]


def build_statement(tokens: tuple[Token, ...], sql: str) -> Statement:
    kind = classify(tokens)
    text = _slice_text(sql, tokens)
    statement = Statement(kind=kind, sql=text, line=tokens[0].line, tokens=tokens)

    if kind is K.DECLARE_CURSOR:
        _attach_cursor_query(statement, tokens, sql)
        return statement
    expected = _EXPECTED_EXPRESSION.get(kind)
    if expected is None:
        return statement

    if kind in (K.UPDATE, K.DELETE) and _has_top_clause(tokens):
        # sqlglot's T-SQL parser mis-reads ``DELETE TOP (n)`` as a table alias.
        statement.parse_error = f"{kind.value.upper()} TOP (n) is not supported by sqlglot"
        statement.writes = _fallback_writes(tokens, sql)
        return statement

    expression, error = parse_sql(text)
    if expression is not None and not isinstance(expression, expected):
        expression, error = None, f"sqlglot produced {type(expression).__name__}, not {kind.value}"
    statement.expression = expression
    statement.parse_error = error
    if expression is not None:
        statement.reads, statement.writes = reads_and_writes(expression)
        statement.ddl_target = ddl_target(expression)
    elif kind in (K.INSERT, K.UPDATE, K.DELETE, K.MERGE, K.TRUNCATE):
        statement.writes = _fallback_writes(tokens, sql)
    return statement


def _has_top_clause(tokens: tuple[Token, ...]) -> bool:
    return len(tokens) > 1 and tokens[1].is_word("TOP")


def _fallback_writes(tokens: tuple[Token, ...], sql: str) -> tuple[TableRef, ...]:
    """Best-effort write targets from tokens, for DML that sqlglot could not parse.

    Finds the table after the DML keyword (skipping ``TOP (n)``, ``INTO``, ``FROM``,
    ``TABLE``) and any ``OUTPUT ... INTO <table>`` target.
    """
    writes: list[TableRef] = []
    pos = 1
    if pos < len(tokens) and tokens[pos].is_word("TOP"):
        pos += 1
        if pos < len(tokens) and tokens[pos].is_punct("("):
            while pos < len(tokens) and not tokens[pos].is_punct(")"):
                pos += 1
            pos += 1
    while pos < len(tokens) and tokens[pos].is_word("INTO", "FROM", "TABLE"):
        pos += 1
    if (ref := _table_at(tokens, pos, sql)) is not None:
        writes.append(ref)

    depth0 = list(_depth0(tokens))
    for n, (_, tok) in enumerate(depth0):
        if tok.is_word("OUTPUT"):
            into = next((i for i, t in depth0[n + 1 :] if t.is_word("INTO")), None)
            ref = _table_at(tokens, into + 1, sql) if into is not None else None
            if ref is not None and ref not in writes:
                writes.append(ref)
            break
    return tuple(writes)


def _table_at(tokens: tuple[Token, ...], pos: int, sql: str) -> TableRef | None:
    if pos >= len(tokens):
        return None
    tok = tokens[pos]
    if tok.kind is TokenKind.VARIABLE:
        return TableRef(name=tok.value.lower(), kind=TableKind.TABLE_VARIABLE)
    if tok.kind is TokenKind.TEMP_NAME:
        kind = TableKind.GLOBAL_TEMP if tok.value.startswith("##") else TableKind.TEMP
        return TableRef(name=tok.value.lower(), kind=kind)
    parts: list[str] = []
    while pos < len(tokens) and tokens[pos].kind in (TokenKind.WORD, TokenKind.QUOTED_IDENTIFIER):
        t = tokens[pos]
        parts.append(
            (t.value if t.kind is TokenKind.QUOTED_IDENTIFIER else sql[t.start : t.end + 1]).lower()
        )
        if pos + 1 < len(tokens) and tokens[pos + 1].is_punct("."):
            pos += 2
        else:
            break
    if not parts:
        return None
    schema = parts[-2] if len(parts) >= 2 else None
    database = parts[-3] if len(parts) >= 3 else None
    return TableRef(name=parts[-1], schema=schema, database=database)


def _attach_cursor_query(statement: Statement, tokens: tuple[Token, ...], sql: str) -> None:
    """``DECLARE c CURSOR ... FOR <select>``: parse the query so its reads are known."""
    start = next(
        (
            i + 1
            for i, tok in enumerate(tokens[:-1])
            if tok.is_word("FOR") and tokens[i + 1].is_word("SELECT", "WITH")
        ),
        None,
    )
    if start is None:
        return
    query_tokens = tokens[start:]
    # A trailing ``FOR UPDATE [OF ...]`` / ``FOR READ ONLY`` is cursor syntax, not SQL.
    for i, tok in _depth0(query_tokens):
        if (
            tok.is_word("FOR")
            and i + 1 < len(query_tokens)
            and query_tokens[i + 1].is_word("UPDATE", "READ")
        ):
            query_tokens = query_tokens[:i]
            break
    expression, error = parse_sql(_slice_text(sql, query_tokens))
    statement.expression, statement.parse_error = expression, error
    if expression is not None:
        statement.reads, _ = reads_and_writes(expression)


def build_condition(tokens: tuple[Token, ...], sql: str) -> Condition:
    text = _slice_text(sql, tokens)
    condition = Condition(sql=text, line=tokens[0].line, tokens=tokens)
    expression, _ = parse_sql(text)
    if expression is not None:
        condition.expression = expression
        condition.reads, _ = reads_and_writes(expression)
    return condition
