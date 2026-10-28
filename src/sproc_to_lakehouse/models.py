"""Core data model shared by the parser, analyzer and (later) generators.

The parser turns a T-SQL file into :class:`Procedure` objects whose body is a tree of
:data:`Node` values. The analyzer walks that tree and produces :class:`Finding` objects.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlglot import exp

    from sproc_to_lakehouse.parser.lexer import Token


class Severity(str, Enum):
    """How much human attention a construct needs to land correctly on Databricks."""

    AUTO = "auto"
    """Converted automatically with equivalent semantics."""

    AUTO_WITH_NOTE = "auto_with_note"
    """Converted automatically, but a semantic difference is worth a reviewer's glance."""

    NEEDS_REVIEW = "needs_review"
    """Cannot be converted safely without a human decision."""

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {Severity.AUTO: 0, Severity.AUTO_WITH_NOTE: 1, Severity.NEEDS_REVIEW: 2}


class TableKind(str, Enum):
    PERMANENT = "permanent"
    TEMP = "temp"  # #name
    GLOBAL_TEMP = "global_temp"  # ##name
    TABLE_VARIABLE = "table_variable"  # @name


@dataclass(frozen=True)
class TableRef:
    """A table referenced by a statement. Names are lower-cased (T-SQL default collation)."""

    name: str
    kind: TableKind = TableKind.PERMANENT
    schema: str | None = None
    database: str | None = None

    @property
    def qualified_name(self) -> str:
        return ".".join(part for part in (self.database, self.schema, self.name) if part)

    @property
    def is_transient(self) -> bool:
        """True for temp tables and table variables, which only live for the procedure run."""
        return self.kind is not TableKind.PERMANENT


class StatementKind(str, Enum):
    SELECT = "select"  # returns a result set to the caller
    SELECT_INTO = "select_into"
    SELECT_ASSIGN = "select_assign"  # SELECT @var = ...
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    MERGE = "merge"
    TRUNCATE = "truncate"
    CREATE_TABLE = "create_table"
    CREATE_INDEX = "create_index"
    DROP_TABLE = "drop_table"
    DECLARE_VARIABLE = "declare_variable"
    DECLARE_TABLE_VARIABLE = "declare_table_variable"
    DECLARE_CURSOR = "declare_cursor"
    SET_VARIABLE = "set_variable"
    SET_OPTION = "set_option"  # SET NOCOUNT ON, SET XACT_ABORT ON, ...
    OPEN_CURSOR = "open_cursor"
    FETCH_CURSOR = "fetch_cursor"
    CLOSE_CURSOR = "close_cursor"
    DEALLOCATE_CURSOR = "deallocate_cursor"
    EXEC_PROCEDURE = "exec_procedure"
    EXEC_DYNAMIC = "exec_dynamic"
    BEGIN_TRANSACTION = "begin_transaction"
    COMMIT = "commit"
    ROLLBACK = "rollback"
    THROW = "throw"
    RAISERROR = "raiserror"
    PRINT = "print"
    RETURN = "return"
    BREAK = "break"
    CONTINUE = "continue"
    OTHER = "other"


@dataclass
class Statement:
    """A single executable T-SQL statement."""

    kind: StatementKind
    sql: str
    line: int
    tokens: tuple[Token, ...] = field(repr=False)
    expression: exp.Expression | None = field(default=None, repr=False)
    parse_error: str | None = None
    reads: tuple[TableRef, ...] = ()
    writes: tuple[TableRef, ...] = ()
    ddl_target: TableRef | None = None  # CREATE TABLE / DROP TABLE / CREATE INDEX target

    @property
    def parsed(self) -> bool:
        return self.expression is not None and self.parse_error is None

    @property
    def end_line(self) -> int:
        return self.tokens[-1].line if self.tokens else self.line


@dataclass
class Condition:
    """The boolean expression of an IF or WHILE."""

    sql: str
    line: int
    tokens: tuple[Token, ...] = field(repr=False)
    expression: exp.Expression | None = field(default=None, repr=False)
    reads: tuple[TableRef, ...] = ()


@dataclass
class Block:
    """BEGIN ... END."""

    body: list[Node]
    line: int
    sql: str = field(default="", repr=False)


@dataclass
class IfNode:
    condition: Condition
    then_branch: Node
    else_branch: Node | None
    line: int
    sql: str = field(default="", repr=False)


@dataclass
class WhileNode:
    condition: Condition
    body: Node
    line: int
    sql: str = field(default="", repr=False)


@dataclass
class TryCatchNode:
    try_body: list[Node]
    catch_body: list[Node]
    line: int
    sql: str = field(default="", repr=False)


Node = Statement | Block | IfNode | WhileNode | TryCatchNode


def iter_nodes(nodes: list[Node]) -> Iterator[Node]:
    """Depth-first, source-order traversal of a node list (parents before children)."""
    for node in nodes:
        yield node
        if isinstance(node, Block):
            yield from iter_nodes(node.body)
        elif isinstance(node, IfNode):
            yield from iter_nodes([node.then_branch])
            if node.else_branch is not None:
                yield from iter_nodes([node.else_branch])
        elif isinstance(node, WhileNode):
            yield from iter_nodes([node.body])
        elif isinstance(node, TryCatchNode):
            yield from iter_nodes(node.try_body)
            yield from iter_nodes(node.catch_body)


@dataclass(frozen=True)
class Parameter:
    name: str  # including the leading @
    data_type: str
    default: str | None = None
    is_output: bool = False


@dataclass
class Procedure:
    name: str
    schema: str | None
    parameters: list[Parameter]
    body: list[Node]
    source_sql: str = field(repr=False)
    source_path: str | None = None
    line: int = 1

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name

    def iter_nodes(self) -> Iterator[Node]:
        return iter_nodes(self.body)

    def statements(self) -> list[Statement]:
        """All statements in source order, including those nested in control flow."""
        return [node for node in self.iter_nodes() if isinstance(node, Statement)]


@dataclass(frozen=True)
class Finding:
    """One analyzer observation about one construct at one location."""

    rule_id: str
    construct: str
    severity: Severity
    message: str
    line: int
    snippet: str = ""
    rewrite: str | None = None
    """Name of the generator rewrite this classification relies on, if any.

    A finding is only ``auto``/``auto_with_note`` *because* the generator performs this
    rewrite (e.g. ``update_from_to_merge``). Phase 2 tests assert every referenced rewrite
    is implemented, so the analyzer cannot promise conversions the generator does not do.
    """


@dataclass
class ProcedureAnalysis:
    procedure: Procedure
    findings: list[Finding]

    @property
    def verdict(self) -> Severity:
        """The worst severity among all findings (``auto`` if there are none)."""
        return max((f.severity for f in self.findings), key=lambda s: s.rank, default=Severity.AUTO)

    def by_severity(self, severity: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity is severity]
