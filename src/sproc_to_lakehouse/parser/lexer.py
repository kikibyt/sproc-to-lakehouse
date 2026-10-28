"""Tokenisation built on sqlglot's T-SQL tokenizer.

sqlglot emits ``@`` / ``#`` as separate tokens and keeps character offsets. We merge
those into single variable / temp-table tokens and normalise keywords, which makes the
block splitter's job much simpler. Comments are dropped by sqlglot's tokenizer (they are
still present in the raw text sliced out for each statement).
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from sqlglot.dialects.tsql import TSQL
from sqlglot.tokens import TokenType


class _NoCommandTokenizer(TSQL.Tokenizer):
    """sqlglot treats END / FETCH / GO at statement start as "commands" and swallows the
    rest of the statement into one string token (``END CATCH; SELECT 1`` -> one token).
    The block splitter needs every token, so command handling is switched off."""

    COMMANDS: set[TokenType] = set()  # noqa: RUF012 - overriding a class-level constant


_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$#@]*")
_MULTI_WORD_RE = re.compile(r"[A-Za-z_]+(?:\s+[A-Za-z_]+)+")
_STRING_TYPES = {TokenType.STRING, TokenType.NATIONAL_STRING}


class TokenKind(str, Enum):
    WORD = "word"  # keyword or unquoted identifier
    QUOTED_IDENTIFIER = "quoted_identifier"  # [name] or "name"
    STRING = "string"
    NUMBER = "number"
    VARIABLE = "variable"  # @name
    SYSTEM_VARIABLE = "system_variable"  # @@NAME
    TEMP_NAME = "temp_name"  # #name or ##name
    PUNCT = "punct"  # operators, parentheses, commas, semicolons, dots


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    value: str
    """Upper-cased for WORD and SYSTEM_VARIABLE; as written for everything else."""
    start: int
    end: int
    """Inclusive character offset of the last character."""
    line: int

    def is_word(self, *values: str) -> bool:
        return self.kind is TokenKind.WORD and (not values or self.value in values)

    def is_punct(self, value: str) -> bool:
        return self.kind is TokenKind.PUNCT and self.value == value


class LexError(ValueError):
    pass


def tokenize(sql: str) -> list[Token]:
    """Tokenise T-SQL text into merged :class:`Token` objects."""
    try:
        raw = _NoCommandTokenizer().tokenize(sql)
    except Exception as exc:  # sqlglot raises its own TokenError
        raise LexError(str(exc)) from exc

    newlines = [i for i, ch in enumerate(sql) if ch == "\n"]

    def line_of(offset: int) -> int:
        # Computed from offsets: sqlglot's own line counter misses newlines inside
        # multi-word keyword tokens such as "ORDER\n BY".
        return bisect_left(newlines, offset) + 1

    tokens: list[Token] = []
    i = 0
    while i < len(raw):
        tok = raw[i]
        text = sql[tok.start : tok.end + 1]

        if tok.token_type in (TokenType.PARAMETER, TokenType.HASH):
            merged = _merge_prefixed(sql, raw, i, line_of)
            if merged is not None:
                token, consumed = merged
                tokens.append(token)
                i += consumed
                continue

        if _MULTI_WORD_RE.fullmatch(text):
            tokens.extend(_split_multi_word(text, tok.start, line_of))
        else:
            tokens.append(
                _convert(tok.token_type, tok.text, text, tok.start, tok.end, line_of(tok.start))
            )
        i += 1
    return tokens


def _split_multi_word(text: str, start: int, line_of: Callable[[int], int]) -> list[Token]:
    """sqlglot emits keyword phrases such as ``GROUP BY`` or ``NONCLUSTERED INDEX`` as one
    token; split them into one WORD token per word."""
    words = []
    for match in re.finditer(r"\S+", text):
        offset = start + match.start()
        words.append(
            Token(
                TokenKind.WORD,
                match.group().upper(),
                offset,
                start + match.end() - 1,
                line_of(offset),
            )
        )
    return words


def _merge_prefixed(
    sql: str, raw: list, i: int, line_of: Callable[[int], int]
) -> tuple[Token, int] | None:
    """Merge ``@x``, ``@@X``, ``#x`` and ``##x`` (no whitespace between parts)."""
    prefix_type = raw[i].token_type
    j = i
    while (
        j + 1 < len(raw) and raw[j + 1].token_type == prefix_type and _adjacent(raw[j], raw[j + 1])
    ):
        j += 1
    prefix_len = j - i + 1
    if j + 1 >= len(raw) or not _adjacent(raw[j], raw[j + 1]) or prefix_len > 2:
        return None
    name_tok = raw[j + 1]
    name_text = sql[name_tok.start : name_tok.end + 1]
    if not _WORD_RE.fullmatch(name_text):
        return None

    start, end, line = raw[i].start, name_tok.end, line_of(raw[i].start)
    if prefix_type == TokenType.PARAMETER:
        if prefix_len == 2:
            kind, value = TokenKind.SYSTEM_VARIABLE, "@@" + name_text.upper()
        else:
            kind, value = TokenKind.VARIABLE, "@" + name_text
    else:
        kind, value = TokenKind.TEMP_NAME, "#" * prefix_len + name_text
    return Token(kind, value, start, end, line), prefix_len + 1


def _adjacent(left, right) -> bool:
    return right.start == left.end + 1


def _convert(
    token_type: TokenType, text: str, raw_text: str, start: int, end: int, line: int
) -> Token:
    if token_type == TokenType.IDENTIFIER:
        return Token(TokenKind.QUOTED_IDENTIFIER, text, start, end, line)
    if token_type in _STRING_TYPES:
        return Token(TokenKind.STRING, raw_text, start, end, line)
    if token_type == TokenType.NUMBER:
        return Token(TokenKind.NUMBER, raw_text, start, end, line)
    if _WORD_RE.fullmatch(raw_text):
        return Token(TokenKind.WORD, raw_text.upper(), start, end, line)
    return Token(TokenKind.PUNCT, raw_text, start, end, line)
