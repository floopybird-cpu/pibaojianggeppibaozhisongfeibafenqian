"""Bind an explicitly documented instrument-to-SI conversion at the input boundary."""
import ast
import math
import re

from .worksheet_math import expression_tree
from .worksheet_units import convert, transform


CONVERSION = re.compile(r"(乘以?|除以?)\s*(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(?:后|再)?\s*(?:换算|转换|折算|换|转)(?:为|成|到|至)?\s*([A-Za-zμµΩ]+(?:\^?[23]|[²³])?)")
CLAUSES = re.compile(r"[；;。\n]")


def unit_factors(equation, readings):
    factors = {}
    for clause in CLAUSES.split(equation.get("bindings", "")):
        matches = list(CONVERSION.finditer(clause))
        if len(matches) != 1:
            continue
        operation, literal, target = matches[0].groups()
        candidates = []
        for field in readings:
            unit = field["unit"]
            names = [name for name in (field["id"], field["label"], field.get("symbol")) if name]
            if not any(re.search(r"(?<![\w.])" + re.escape(name) + r"(?![A-Za-z0-9_])", clause) for name in names):
                continue
            if not re.search(r"(?<![A-Za-z])" + re.escape(unit) + r"(?![A-Za-z])", clause):
                continue
            factor = float(literal)
            if not factor or factor == 1:
                continue
            try:
                if transform(target) != (1, 0) or transform(unit)[1]:
                    continue
                ratio = 1/factor if operation.startswith("除") else factor
                if math.isclose(convert(1, unit, target), ratio, rel_tol=1e-12):
                    candidates.append((field["id"], ratio))
            except ValueError:
                continue
        if len(candidates) == 1:
            key, ratio = candidates[0]
            factors[key] = ratio
    return factors


def bind_si_conversions(method):
    """Leave gains and unconfirmed constants for the usual source/quantity validator."""
    changes = []
    for eq in method.get("equations", []):
        if not isinstance(eq, dict):
            continue
        readings = [f for f in method.get("observables", []) if not f.get("derived_from")]
        factors = unit_factors(eq, readings)
        if not factors:
            continue

        class Bind(ast.NodeTransformer):
            def visit_BinOp(self, node):
                self.generic_visit(node)
                if not isinstance(node.op, (ast.Mult, ast.Div)):
                    return node
                value, literal = node.left, node.right
                if isinstance(value, ast.Constant) and isinstance(node.op, ast.Mult):
                    value, literal = literal, value
                if not isinstance(literal, ast.Constant) or type(literal.value) not in (int, float) or not literal.value:
                    return node
                operand = value.right if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Mult) else value
                if isinstance(operand, ast.Name) and operand.id in factors:
                    ratio = 1/literal.value if isinstance(node.op, ast.Div) else literal.value
                    if math.isclose(ratio, factors[operand.id], rel_tol=1e-12):
                        used.append(operand.id)
                        return value
                return node

        for step in eq.get("calculations", []):
            for key in ("expr", "uncertainty"):
                if not step.get(key):
                    continue
                used = []
                try:
                    tree = Bind().visit(expression_tree(step[key]))
                except (SyntaxError, ValueError):
                    continue
                if used and len(used) == len(set(used)):
                    updated = ast.unparse(tree)
                    changes.append(dict(result=step["id"], inputs=used, original=step[key], expression=updated))
                    step[key] = updated
    return changes
