"""Tiny safe evaluator for trigger conditions. Never uses eval().

Allowed: literals, names given by the caller (obj, event), attribute/key access
(no leading underscore), comparisons, and/or/not, +-*/%, lists/tuples/sets,
and calls to a few pure helpers (len, lower, upper, str, int, float).

    obj.status == "new" and obj.amount >= 1000
    "vip" in lower(obj.tags)
"""

import ast
import operator
from typing import Any

MAX_LENGTH = 1000
MAX_DEPTH = 30

_COMPARE = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}
_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}
_FUNCS = {
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "lower": lambda v: str(v or "").lower(),
    "upper": lambda v: str(v or "").upper(),
}


class ExpressionError(ValueError):
    pass


def compile_expression(source: str, names: set[str]) -> ast.Expression:
    """Parse and validate once (save-time check). Raises ExpressionError."""
    if len(source) > MAX_LENGTH:
        raise ExpressionError(f"Condition longer than {MAX_LENGTH} characters")
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Invalid condition: {exc.msg}") from None
    _validate(tree.body, names, 0)
    return tree


def evaluate(source: str, values: dict[str, Any]) -> bool:
    tree = compile_expression(source, set(values))
    return bool(_eval(tree.body, values))


def _validate(node: ast.AST, names: set[str], depth: int) -> None:
    if depth > MAX_DEPTH:
        raise ExpressionError("Condition is nested too deeply")
    if isinstance(node, ast.Name):
        if node.id not in names and node.id not in ("True", "False", "None"):
            raise ExpressionError(f"Unknown name '{node.id}'. Available: {sorted(names)}")
    elif isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ExpressionError("Attributes starting with '_' are not allowed")
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS or node.keywords:
            raise ExpressionError(f"Only these functions are allowed: {sorted(_FUNCS)}")
        for arg in node.args:
            _validate(arg, names, depth + 1)
        return
    elif isinstance(node, ast.Compare):
        if not all(type(op) in _COMPARE for op in node.ops):
            raise ExpressionError("Unsupported comparison")
    elif isinstance(node, ast.BinOp):
        if type(node.op) not in _BINARY:
            raise ExpressionError("Unsupported operator")
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, ast.Not | ast.USub):
            raise ExpressionError("Unsupported operator")
    elif not isinstance(node, ast.BoolOp | ast.Constant | ast.List | ast.Tuple | ast.Set | ast.Load | ast.And | ast.Or):
        raise ExpressionError(f"'{type(node).__name__}' is not allowed in conditions")
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, ast.expr_context | ast.operator | ast.cmpop | ast.unaryop | ast.boolop):
            _validate(child, names, depth + 1)


def _eval(node: ast.AST, values: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return {"True": True, "False": False, "None": None}.get(node.id, values.get(node.id))
    if isinstance(node, ast.Attribute):
        return _get(_eval(node.value, values), node.attr)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval(v, values) for v in node.values)
        return any(_eval(v, values) for v in node.values)
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, values)
        return (not operand) if isinstance(node.op, ast.Not) else -operand
    if isinstance(node, ast.BinOp):
        return _BINARY[type(node.op)](_eval(node.left, values), _eval(node.right, values))
    if isinstance(node, ast.Compare):
        left = _eval(node.left, values)
        for op, right_node in zip(node.ops, node.comparators, strict=True):
            right = _eval(right_node, values)
            try:
                if not _COMPARE[type(op)](left, right):
                    return False
            except TypeError:  # e.g. None > 5: a missing value never matches
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        return _FUNCS[node.func.id](*[_eval(a, values) for a in node.args])
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        items = [_eval(e, values) for e in node.elts]
        return set(items) if isinstance(node, ast.Set) else items
    raise ExpressionError(f"Unsupported expression: {type(node).__name__}")


def _get(obj: Any, attr: str) -> Any:
    # Conditions see plain dicts (serialized model fields), so attribute access = key lookup.
    if isinstance(obj, dict):
        return obj.get(attr)
    raise ExpressionError(f"Cannot read '{attr}' here")
