"""Rule definition and shared helpers for walking a parsed procedure."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from sqlglot import exp

from sproc_to_lakehouse.catalog import Catalog
from sproc_to_lakehouse.models import (
    Block,
    Finding,
    IfNode,
    Node,
    Procedure,
    Severity,
    Statement,
    StatementKind,
    WhileNode,
)
from sproc_to_lakehouse.parser.lexer import Token

CheckFn = Callable[..., Iterable[Finding]]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    construct: str
    summary: str
    check: CheckFn
    needs_catalog: bool = False
    """Type-dependent rules only run when DDL is available (``check(rule, proc, catalog)``)."""

    def run(self, procedure: Procedure, catalog: Catalog | None = None) -> list[Finding]:
        if self.needs_catalog:
            return list(self.check(self, procedure, catalog)) if catalog is not None else []
        return list(self.check(self, procedure))

    def finding(
        self,
        severity: Severity,
        message: str,
        line: int,
        snippet: str = "",
        rewrite: str | None = None,
    ) -> Finding:
        return Finding(
            rule_id=self.rule_id,
            construct=self.construct,
            severity=severity,
            message=message,
            line=line,
            snippet=snippet,
            rewrite=rewrite,
        )


def rule(
    rule_id: str, construct: str, summary: str, *, needs_catalog: bool = False
) -> Callable[[CheckFn], Rule]:
    """Decorator turning a check function into a :class:`Rule`."""

    def wrap(check: CheckFn) -> Rule:
        return Rule(rule_id, construct, summary, check, needs_catalog)

    return wrap


def snippet(sql: str, limit: int = 100) -> str:
    text = " ".join(sql.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass(frozen=True)
class SqlUnit:
    """A piece of parsed SQL to inspect: a statement or an IF/WHILE condition."""

    line: int
    sql: str
    tokens: tuple[Token, ...]
    expression: exp.Expression | None
    statement: Statement | None = None


def is_temp_cleanup(node: Node) -> bool:
    """``[IF OBJECT_ID('tempdb..#t') IS NOT NULL] DROP TABLE #t`` - removed on conversion."""
    if isinstance(node, IfNode):
        return node.else_branch is None and is_temp_cleanup(node.then_branch)
    return (
        isinstance(node, Statement)
        and node.kind is StatementKind.DROP_TABLE
        and node.ddl_target is not None
        and node.ddl_target.is_transient
    )


def parameter_default(node: Node) -> tuple[str, exp.Expression] | None:
    """Match ``IF @x IS NULL SET @x = <value>`` and return ``(x, value)``."""
    if not isinstance(node, IfNode) or node.else_branch is not None:
        return None
    then = node.then_branch
    if isinstance(then, Block) and len(then.body) == 1:
        then = then.body[0]
    cond = node.condition.expression
    if not (
        isinstance(then, Statement)
        and then.kind is StatementKind.SET_VARIABLE
        and isinstance(then.expression, exp.Set)
        and isinstance(cond, exp.Is)
        and isinstance(cond.this, exp.Parameter)
        and isinstance(cond.expression, exp.Null)
    ):
        return None
    items = then.expression.expressions
    if len(items) != 1 or not isinstance(items[0].this, exp.EQ):
        return None
    target, value = items[0].this.this, items[0].this.expression
    if not isinstance(target, exp.Parameter) or target.name.lower() != cond.this.name.lower():
        return None
    return target.name.lower(), value


def iter_units(procedure: Procedure) -> Iterator[SqlUnit]:
    """Every statement and every IF/WHILE condition, in source order.

    Temp-table cleanup (and its guarding IF) is skipped: it is removed on conversion,
    so constructs such as ``OBJECT_ID('tempdb..#t')`` inside it are irrelevant.
    """
    skip: set[int] = set()
    for node in procedure.iter_nodes():
        if id(node) in skip:
            continue
        if is_temp_cleanup(node):
            if isinstance(node, IfNode):
                skip.add(id(node.then_branch))
            continue
        if isinstance(node, Statement):
            yield SqlUnit(node.line, node.sql, node.tokens, node.expression, node)
        elif isinstance(node, (IfNode, WhileNode)):
            cond = node.condition
            yield SqlUnit(cond.line, cond.sql, cond.tokens, cond.expression)


def function_calls(tokens: Iterable[Token], *names: str) -> list[Token]:
    """Tokens that are a call to one of ``names`` (the word followed by ``(``)."""
    tokens = list(tokens)
    return [
        tok
        for i, tok in enumerate(tokens[:-1])
        if tok.is_word(*names) and tokens[i + 1].is_punct("(")
    ]


def call_argument_count(tokens: tuple[Token, ...], call: Token) -> int:
    """Number of top-level arguments of the call starting at token ``call``."""
    start = tokens.index(call) + 1  # the "("
    depth, commas = 0, 0
    for tok in tokens[start:]:
        if tok.is_punct("("):
            depth += 1
        elif tok.is_punct(")"):
            depth -= 1
            if depth == 0:
                break
        elif tok.is_punct(",") and depth == 1:
            commas += 1
    return commas + 1
