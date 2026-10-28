"""Split a procedure body into statements and control-flow blocks.

T-SQL does not require semicolons, so statement boundaries are found the way SQL Server
finds them: a new statement starts at a statement keyword (SELECT, UPDATE, SET, IF, ...)
that appears outside parentheses and outside a CASE expression, unless that keyword is a
legitimate continuation of the current statement, for example:

* ``INSERT INTO t SELECT ...``            (SELECT is the INSERT's source)
* ``UPDATE t SET ...``                     (SET belongs to the UPDATE)
* ``WITH cte AS (...) SELECT ...``         (the CTE's main statement)
* ``... UNION ALL SELECT ...``             (set operation)
* ``DECLARE c CURSOR FOR SELECT ...``      (cursor query)
* ``FROM t WITH (NOLOCK)``                 (table hint, not a CTE)
* ``DROP TABLE IF EXISTS #t``              (not an IF statement)

MERGE is the exception: SQL Server requires it to end with a semicolon, so it is
consumed up to the next top-level ``;``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sproc_to_lakehouse.models import Block, IfNode, Node, TryCatchNode, WhileNode
from sproc_to_lakehouse.parser.header import ProcedureParseError
from sproc_to_lakehouse.parser.lexer import Token, TokenKind
from sproc_to_lakehouse.parser.statements import build_condition, build_statement

STATEMENT_STARTERS = frozenset(
    {
        "SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "WITH", "DECLARE", "SET", "IF",
        "WHILE", "BEGIN", "EXEC", "EXECUTE", "PRINT", "RETURN", "THROW", "RAISERROR", "OPEN",
        "CLOSE", "DEALLOCATE", "FETCH", "CREATE", "DROP", "ALTER", "TRUNCATE", "COMMIT",
        "ROLLBACK", "BREAK", "CONTINUE", "WAITFOR", "GOTO", "USE",
    }
)  # fmt: skip

_SET_OPERATORS = frozenset({"UNION", "ALL", "EXCEPT", "INTERSECT"})
_DDL_OBJECTS = frozenset({"TABLE", "VIEW", "PROCEDURE", "PROC", "FUNCTION", "INDEX", "SCHEMA"})
_TRANSACTION_WORDS = frozenset({"TRAN", "TRANSACTION", "DISTRIBUTED"})


@dataclass
class _StatementState:
    """What the statement being scanned has seen so far (drives continuation rules)."""

    kind: str
    cte_main_seen: bool = False
    insert_source_seen: bool = False
    update_set_seen: bool = False


class _BlockParser:
    def __init__(self, tokens: list[Token], sql: str) -> None:
        self.tokens = tokens
        self.sql = sql
        self.pos = 0

    # -- token helpers ---------------------------------------------------------------
    def _peek(self, offset: int = 0) -> Token | None:
        idx = self.pos + offset
        return self.tokens[idx] if idx < len(self.tokens) else None

    def _at_word(self, *values: str, offset: int = 0) -> bool:
        tok = self._peek(offset)
        return tok is not None and tok.is_word(*values)

    def _expect_word(self, value: str) -> Token:
        tok = self._peek()
        if tok is None or not tok.is_word(value):
            found = tok.value if tok else "end of input"
            line = tok.line if tok else (self.tokens[-1].line if self.tokens else None)
            raise ProcedureParseError(f"expected {value}, found {found}", line)
        self.pos += 1
        return tok

    def _skip_semicolons(self) -> None:
        while (tok := self._peek()) is not None and tok.is_punct(";"):
            self.pos += 1

    # -- structure -------------------------------------------------------------------
    def parse_items(self, *, until_end: str | None = None, top_level: bool = False) -> list[Node]:
        """Parse nodes until ``END`` (optionally ``END TRY`` / ``END CATCH``) or EOF."""
        items: list[Node] = []
        while True:
            self._skip_semicolons()
            tok = self._peek()
            if tok is None:
                if top_level:
                    return items
                raise ProcedureParseError(
                    "unexpected end of input inside a block", self.tokens[-1].line
                )
            if tok.is_word("END"):
                if top_level:
                    raise ProcedureParseError("END without matching BEGIN", tok.line)
                follower = self._peek(1)
                is_qualified = follower is not None and follower.is_word("TRY", "CATCH")
                if until_end is None and not is_qualified:
                    return items
                if until_end is not None and is_qualified and follower.value == until_end:
                    return items
                raise ProcedureParseError("mismatched END", tok.line)
            if tok.is_word("ELSE"):
                raise ProcedureParseError("ELSE without IF", tok.line)
            items.append(self.parse_node())

    def parse_node(self) -> Node:
        self._skip_semicolons()
        tok = self._peek()
        if tok is None:
            raise ProcedureParseError("expected a statement", self.tokens[-1].line)
        if tok.is_word("BEGIN"):
            if self._at_word("TRY", offset=1):
                return self._parse_try_catch()
            if not self._at_word(*_TRANSACTION_WORDS, offset=1):
                return self._parse_begin_end()
        if tok.is_word("IF"):
            return self._parse_if()
        if tok.is_word("WHILE"):
            return self._parse_while()
        return self._parse_statement()

    def _parse_begin_end(self) -> Block:
        begin = self._expect_word("BEGIN")
        body = self.parse_items()
        self._expect_word("END")
        return Block(body=body, line=begin.line)

    def _parse_try_catch(self) -> TryCatchNode:
        begin = self._expect_word("BEGIN")
        self._expect_word("TRY")
        try_body = self.parse_items(until_end="TRY")
        self._expect_word("END")
        self._expect_word("TRY")
        self._skip_semicolons()
        self._expect_word("BEGIN")
        self._expect_word("CATCH")
        catch_body = self.parse_items(until_end="CATCH")
        self._expect_word("END")
        self._expect_word("CATCH")
        return TryCatchNode(try_body=try_body, catch_body=catch_body, line=begin.line)

    def _parse_if(self) -> IfNode:
        if_tok = self._expect_word("IF")
        condition = build_condition(self._scan_condition(), self.sql)
        then_branch = self.parse_node()
        else_branch = None
        # ``IF c stmt; ELSE stmt`` is valid T-SQL, so look past semicolons for ELSE.
        lookahead = self.pos
        while lookahead < len(self.tokens) and self.tokens[lookahead].is_punct(";"):
            lookahead += 1
        if lookahead < len(self.tokens) and self.tokens[lookahead].is_word("ELSE"):
            self.pos = lookahead + 1
            else_branch = self.parse_node()
        return IfNode(condition, then_branch, else_branch, line=if_tok.line)

    def _parse_while(self) -> WhileNode:
        while_tok = self._expect_word("WHILE")
        condition = build_condition(self._scan_condition(), self.sql)
        return WhileNode(condition, self.parse_node(), line=while_tok.line)

    def _scan_condition(self) -> tuple[Token, ...]:
        start = self.pos
        depth = case_depth = 0
        while (tok := self._peek()) is not None:
            if tok.is_punct("("):
                depth += 1
            elif tok.is_punct(")"):
                depth -= 1
            elif depth == 0 and tok.kind is TokenKind.WORD:
                if tok.value == "CASE":
                    case_depth += 1
                elif tok.value == "END" and case_depth:
                    case_depth -= 1
                elif case_depth == 0 and tok.value in STATEMENT_STARTERS:
                    break
            self.pos += 1
        if self.pos == start:
            line = self._peek().line if self._peek() else None
            raise ProcedureParseError("empty IF/WHILE condition", line)
        return tuple(self.tokens[start : self.pos])

    # -- simple statements -----------------------------------------------------------
    def _parse_statement(self):
        start = self.pos
        first = self.tokens[start]
        if first.is_word("MERGE"):
            self._scan_to_semicolon()
        else:
            self._scan_statement(first)
        return build_statement(tuple(self.tokens[start : self.pos]), self.sql)

    def _scan_to_semicolon(self) -> None:
        depth = 0
        start_line = self.tokens[self.pos].line
        while (tok := self._peek()) is not None:
            if tok.is_punct("("):
                depth += 1
            elif tok.is_punct(")"):
                depth -= 1
            elif depth == 0 and tok.is_punct(";"):
                return  # leave the semicolon for _skip_semicolons
            self.pos += 1
        raise ProcedureParseError("MERGE must be terminated by a semicolon", start_line)

    def _scan_statement(self, first: Token) -> None:
        state = _StatementState(kind=first.value if first.kind is TokenKind.WORD else "")
        self.pos += 1
        depth = case_depth = 0
        while (tok := self._peek()) is not None:
            if tok.is_punct("("):
                depth += 1
            elif tok.is_punct(")"):
                if depth == 0:
                    raise ProcedureParseError("unbalanced ')'", tok.line)
                depth -= 1
            elif depth == 0:
                if tok.is_punct(";"):
                    return
                if tok.kind is TokenKind.WORD:
                    if tok.value == "CASE":
                        case_depth += 1
                    elif tok.value == "END":
                        if case_depth == 0:
                            return  # closes the enclosing BEGIN ... END
                        case_depth -= 1
                    elif case_depth == 0:
                        if tok.value == "ELSE":
                            return  # ends the THEN branch of an enclosing IF
                        if tok.value in STATEMENT_STARTERS and not self._continues(state):
                            return
            self.pos += 1

    def _continues(self, state: _StatementState) -> bool:
        """Is the starter keyword at ``self.pos`` part of the current statement?"""
        tok = self.tokens[self.pos]
        prev = self.tokens[self.pos - 1]
        nxt = self._peek(1)
        value = tok.value

        if prev.is_word("FOR"):
            return True  # CURSOR FOR SELECT, FOR UPDATE
        if value == "SELECT" and prev.kind is TokenKind.WORD and prev.value in _SET_OPERATORS:
            return True
        if value == "WITH" and not self._starts_cte():
            return True  # table hint, WITH TIES, WITH ROLLUP, ...
        if value == "IF" and prev.kind is TokenKind.WORD and prev.value in _DDL_OBJECTS:
            return nxt is not None and nxt.is_word("EXISTS")
        if value == "FETCH" and prev.is_word("ROW", "ROWS"):
            return True  # OFFSET ... FETCH NEXT
        if value == "SET" and state.kind == "UPDATE" and not state.update_set_seen:
            state.update_set_seen = True
            return True
        if state.kind == "WITH" and not state.cte_main_seen and value in (
            "SELECT", "INSERT", "UPDATE", "DELETE", "MERGE",
        ):  # fmt: skip
            state.cte_main_seen = True
            state.kind = value
            return True
        if state.kind == "INSERT" and not state.insert_source_seen and value in (
            "SELECT", "WITH", "EXEC", "EXECUTE",
        ):  # fmt: skip
            state.insert_source_seen = True
            return True
        return False

    def _starts_cte(self) -> bool:
        """``WITH name AS (`` or ``WITH name (cols) AS (`` starts a CTE."""
        name = self._peek(1)
        after = self._peek(2)
        if name is None or after is None:
            return False
        if name.kind not in (TokenKind.WORD, TokenKind.QUOTED_IDENTIFIER):
            return False
        return after.is_word("AS") or after.is_punct("(")


def parse_body(tokens: list[Token], sql: str) -> list[Node]:
    """Parse procedure body tokens into nodes. A single wrapping BEGIN/END is unwrapped."""
    parser = _BlockParser(tokens, sql)
    nodes = parser.parse_items(top_level=True)
    if len(nodes) == 1 and isinstance(nodes[0], Block):
        return nodes[0].body
    return nodes
