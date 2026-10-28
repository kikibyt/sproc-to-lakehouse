"""Batch splitting (GO) and CREATE PROCEDURE header parsing."""

from __future__ import annotations

from dataclasses import dataclass

from sproc_to_lakehouse.models import Parameter
from sproc_to_lakehouse.parser.lexer import Token, TokenKind


class ProcedureParseError(ValueError):
    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(f"line {line}: {message}" if line is not None else message)
        self.line = line


@dataclass
class ProcedureHeader:
    schema: str | None
    name: str
    parameters: list[Parameter]
    body_start: int
    """Index of the first body token (just after ``AS``)."""
    line: int


def split_batches(tokens: list[Token]) -> list[list[Token]]:
    """Split on ``GO`` batch separators (a ``GO`` that is alone on its line)."""
    batches: list[list[Token]] = [[]]
    for i, tok in enumerate(tokens):
        if tok.is_word("GO") and _alone_on_line(tokens, i):
            batches.append([])
        else:
            batches[-1].append(tok)
    return [batch for batch in batches if batch]


def _alone_on_line(tokens: list[Token], i: int) -> bool:
    line = tokens[i].line
    before_ok = i == 0 or tokens[i - 1].line != line
    after_ok = i + 1 == len(tokens) or tokens[i + 1].line != line
    return before_ok and after_ok


def find_procedure_start(tokens: list[Token]) -> int | None:
    """Index of the CREATE/ALTER token that starts a procedure definition, if any."""
    for i, tok in enumerate(tokens):
        if not tok.is_word("CREATE", "ALTER"):
            continue
        j = i + 1
        if j + 1 < len(tokens) and tokens[j].is_word("OR") and tokens[j + 1].is_word("ALTER"):
            j += 2
        if j < len(tokens) and tokens[j].is_word("PROC", "PROCEDURE"):
            return i
    return None


def parse_header(tokens: list[Token], sql: str, start: int) -> ProcedureHeader:
    """Parse ``CREATE [OR ALTER] PROC[EDURE] name [params] [WITH ...] AS``."""
    pos = start
    line = tokens[pos].line
    while not tokens[pos].is_word("PROC", "PROCEDURE"):
        pos += 1
    pos += 1

    name_parts, pos = _read_dotted_name(tokens, sql, pos)
    if not name_parts:
        raise ProcedureParseError("missing procedure name", line)
    schema = name_parts[-2] if len(name_parts) >= 2 else None
    name = name_parts[-1]

    wrapped = pos < len(tokens) and tokens[pos].is_punct("(")
    if wrapped:
        pos += 1

    parameters: list[Parameter] = []
    while pos < len(tokens) and tokens[pos].kind is TokenKind.VARIABLE:
        param, pos = _read_parameter(tokens, sql, pos)
        parameters.append(param)
        if pos < len(tokens) and tokens[pos].is_punct(","):
            pos += 1

    if wrapped:
        if pos >= len(tokens) or not tokens[pos].is_punct(")"):
            raise ProcedureParseError("unclosed parameter list", line)
        pos += 1

    if pos < len(tokens) and tokens[pos].is_word("WITH"):
        pos = _skip_procedure_options(tokens, pos + 1)

    if pos >= len(tokens) or not tokens[pos].is_word("AS"):
        raise ProcedureParseError(f"expected AS after header of {name}", line)
    return ProcedureHeader(schema, name, parameters, pos + 1, line)


def _read_dotted_name(tokens: list[Token], sql: str, pos: int) -> tuple[list[str], int]:
    parts: list[str] = []
    while pos < len(tokens) and tokens[pos].kind in (TokenKind.WORD, TokenKind.QUOTED_IDENTIFIER):
        tok = tokens[pos]
        parts.append(tok.value if tok.kind is TokenKind.QUOTED_IDENTIFIER else _text(sql, tok))
        pos += 1
        if pos < len(tokens) and tokens[pos].is_punct("."):
            pos += 1
        else:
            break
    return parts, pos


def _read_parameter(tokens: list[Token], sql: str, pos: int) -> tuple[Parameter, int]:
    name = tokens[pos].value
    pos += 1
    if tokens[pos].is_word("AS"):
        pos += 1

    type_start, pos = pos, _skip_until_param_boundary(tokens, pos, stop_on_equals=True)
    data_type = _span(sql, tokens, type_start, pos)

    default = None
    if pos < len(tokens) and tokens[pos].is_punct("="):
        pos += 1
        default_start, pos = pos, _skip_until_param_boundary(tokens, pos, stop_on_equals=False)
        default = _span(sql, tokens, default_start, pos)

    is_output = False
    while pos < len(tokens) and tokens[pos].is_word("OUTPUT", "OUT", "READONLY"):
        is_output = is_output or tokens[pos].value in ("OUTPUT", "OUT")
        pos += 1
    return Parameter(name=name, data_type=data_type, default=default, is_output=is_output), pos


def _skip_until_param_boundary(tokens: list[Token], pos: int, *, stop_on_equals: bool) -> int:
    depth = 0
    while pos < len(tokens):
        tok = tokens[pos]
        if tok.is_punct("("):
            depth += 1
        elif tok.is_punct(")"):
            if depth == 0:
                break  # closing paren of a wrapped parameter list
            depth -= 1
        elif depth == 0:
            if tok.is_punct(",") or tok.is_word("OUTPUT", "OUT", "READONLY", "AS", "WITH"):
                break
            if stop_on_equals and tok.is_punct("="):
                break
        pos += 1
    return pos


def _skip_procedure_options(tokens: list[Token], pos: int) -> int:
    """Skip ``WITH RECOMPILE, EXECUTE AS OWNER`` style options, returning the AS position."""
    while pos < len(tokens):
        tok = tokens[pos]
        if (
            tok.is_word("EXECUTE", "EXEC")
            and pos + 1 < len(tokens)
            and tokens[pos + 1].is_word("AS")
        ):
            pos += 3  # EXECUTE AS <principal>
            continue
        if tok.is_word("AS"):
            return pos
        pos += 1
    return pos


def _text(sql: str, tok: Token) -> str:
    return sql[tok.start : tok.end + 1]


def _span(sql: str, tokens: list[Token], start: int, stop: int) -> str:
    if stop <= start:
        return ""
    return sql[tokens[start].start : tokens[stop - 1].end + 1].strip()
