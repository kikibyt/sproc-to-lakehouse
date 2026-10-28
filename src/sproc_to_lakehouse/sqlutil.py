"""Small sqlglot helpers shared by the analyzer and the code generators."""

from __future__ import annotations

from sqlglot import exp

# Functions that return text. LEN / CHARINDEX are string *functions* but return integers,
# so they must not make a '+' look like string concatenation.
STRING_RETURNING_FUNCTIONS: tuple[type[exp.Expression], ...] = (
    exp.Upper, exp.Lower, exp.Trim, exp.Substring, exp.Left, exp.Right, exp.Replace,
    exp.Concat, exp.Repeat,
)  # fmt: skip
STRING_FUNCTIONS: tuple[type[exp.Expression], ...] = (
    *STRING_RETURNING_FUNCTIONS,
    exp.Length,
    exp.StrPosition,
)


def is_stringish(node: exp.Expression) -> bool:
    """True if ``node`` is known to produce text without needing column types."""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.National):  # N'...'
        return True
    if isinstance(node, exp.Literal):
        return node.is_string
    if isinstance(node, (exp.Cast, exp.TryCast)):
        return node.to.is_type(*exp.DataType.TEXT_TYPES)
    if isinstance(node, exp.Case):
        branches = [i.args.get("true") for i in node.args.get("ifs") or []]
        branches.append(node.args.get("default"))
        return any(b is not None and is_stringish(b) for b in branches)
    return isinstance(node, STRING_RETURNING_FUNCTIONS)


def add_chain_leaves(node: exp.Add) -> list[exp.Expression]:
    """Operands of ``a + b + (c + d)`` flattened in order: ``[a, b, c, d]``."""
    leaves: list[exp.Expression] = []
    for side in (node.left, node.right):
        while isinstance(side, exp.Paren):
            side = side.this
        leaves.extend(add_chain_leaves(side) if isinstance(side, exp.Add) else [side])
    return leaves


def is_string_concat(node: exp.Expression) -> bool:
    return isinstance(node, exp.Add) and any(is_stringish(leaf) for leaf in add_chain_leaves(node))


def conjuncts(condition: exp.Expression | None) -> list[exp.Expression]:
    """Split ``a AND b AND (c AND d)`` into ``[a, b, c, d]``."""
    if condition is None:
        return []
    while isinstance(condition, exp.Paren) and isinstance(condition.this, exp.And):
        condition = condition.this
    if isinstance(condition, exp.And):
        return conjuncts(condition.left) + conjuncts(condition.right)
    return [condition]


def and_all(parts: list[exp.Expression]) -> exp.Expression | None:
    if not parts:
        return None
    result = parts[0]
    for part in parts[1:]:
        result = exp.And(this=result, expression=part)
    return result
