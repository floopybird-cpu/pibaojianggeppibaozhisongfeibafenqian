"""Compare two source-to-reading formula graphs without substituting student data."""
import ast
import copy
import re
from collections import defaultdict
from fractions import Fraction

import numpy as np

from .worksheet_compile import pair_observation_means
from .worksheet_math import CONSTANTS, evaluate, expression_tree, normalize_factors, parse


def formula_items(page):
    return [item for table in page.get("tables", []) for item in table.get("derived", [])] + list(page.get("results", []))


class FormulaGraph:
    def __init__(self, page):
        self.definitions = {item["id"]: item["expr"] for item in formula_items(page)
                            if isinstance(item, dict) and isinstance(item.get("expr"), str) and item.get("id")}
        self.inputs = {item["id"] for item in page.get("fields", [])}
        self.inputs.update(col["id"] for table in page.get("tables", []) for col in table["columns"])
        self.columns = {column["id"]: table["id"] for table in page.get("tables", [])
                        for column in table["columns"] + table.get("derived", [])}
        self.cache = {}

    def dependencies(self, expression):
        from .worksheet_units import quantity_names
        todo = list(quantity_names(expression_tree(expression)))
        seen = set()
        while todo:
            key = todo.pop()
            if key not in seen:
                seen.add(key)
                if key in self.definitions:
                    todo.extend(quantity_names(expression_tree(self.definitions[key])))
        return seen

    def expand(self, expression, trail=()):
        graph = self

        class Substitute(ast.NodeTransformer):
            def visit_Call(self, node):
                # A named scalar and a function can have the same spelling in an author's receipt.
                node.args = [self.visit(arg) for arg in node.args]
                return node

            def visit_Name(self, node):
                key = node.id
                if key in graph.inputs or key not in graph.definitions:
                    return node
                if key in trail:
                    raise ValueError("计算关系循环引用 " + key)
                if key not in graph.cache:
                    graph.cache[key] = graph.expand(graph.definitions[key], trail + (key,))
                return copy.deepcopy(graph.cache[key])

        tree, _ = pair_observation_means(expression_tree(expression), self.columns)
        tree,_=pair_observation_means(Substitute().visit(tree),self.columns)
        return tree


# A polynomial is represented by its exact rational coefficients, indexed by monomials.
# Other operations (including fits and reductions) are opaque, so that different raw-row
# pairing or fit choices cannot accidentally be proved equivalent by algebra.
def _add(a, b, sign=1):
    result = defaultdict(Fraction, a)
    for term, value in b.items():
        result[term] += sign * value
    return {term: value for term, value in result.items() if value}


def _multiply(a, b):
    if len(a) * len(b) > 2048:
        raise ValueError("展开式过大，保留原式比较")
    result = defaultdict(Fraction)
    for x, first in a.items():
        for y, second in b.items():
            powers = dict(x)
            for name, exponent in y:
                powers[name] = powers.get(name, 0) + exponent
            result[tuple(sorted(powers.items()))] += first * second
    return {term: value for term, value in result.items() if value}


def _polynomials(node):
    one = {(): Fraction(1)}
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return {(): Fraction(str(node.value))}, one
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        num, den = _polynomials(node.operand)
        return (_add({}, num, -1) if isinstance(node.op, ast.USub) else num), den
    if isinstance(node, ast.BinOp):
        a, b = _polynomials(node.left)
        c, d = _polynomials(node.right)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            return _add(_multiply(a, d), _multiply(b, c), -1 if isinstance(node.op, ast.Sub) else 1), _multiply(b, d)
        if isinstance(node.op, ast.Mult):
            return _multiply(a, c), _multiply(b, d)
        if isinstance(node.op, ast.Div):
            return _multiply(a, d), _multiply(b, c)
        if isinstance(node.op, ast.Pow) and isinstance(node.right, ast.Constant) and type(node.right.value) is int:
            exponent = node.right.value
            if 0 <= exponent <= 16:
                num, den = one, one
                for _ in range(exponent):
                    num, den = _multiply(num, a), _multiply(den, b)
                return num, den
    return {((ast.dump(node), 1),): Fraction(1)}, one


def _names(node):
    functions = {item.func for item in ast.walk(node) if isinstance(item, ast.Call)}
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name) and item not in functions}


def equivalent(first, second):
    """Prove a rational identity or exact syntax. Unsupported identities stay undecided."""
    if first is None or second is None:
        return False
    if ast.dump(first) == ast.dump(second):
        return True
    if _names(first) != _names(second):
        return False
    try:
        a, b = _polynomials(first)
        c, d = _polynomials(second)
        return any(b.values()) and any(d.values()) and _multiply(a, d) == _multiply(c, b)
    except (ValueError, ArithmeticError, RecursionError):
        return False


def expand_or_none(graph, expr):
    try:
        return graph.expand(expr)
    except (ArithmeticError, AttributeError, KeyError, RecursionError, SyntaxError, TypeError, ValueError):
        return None


def compare_formulas(first, second, columns):
    if equivalent(first, second):
        return "equivalent"
    if first is None or second is None:
        return "unconfirmed"
    first_names,second_names=_names(first)-CONSTANTS.keys(),_names(second)-CONSTANTS.keys()
    if first_names!=second_names:return "different_inputs"
    names = sorted(first_names)
    # These values are independent mathematical probes, never measured observations.
    # A failed evaluation says nothing about correctness. A valid unequal pair records
    # why two definitions cannot represent the same input-to-result map.
    for trial in range(8):
        environment = {}
        for index, key in enumerate(names):
            value = 0.83 + (index + 1) * 1.17 + trial * 0.39
            # Include increasing, uniformly sampled data: functions such as
            # sample_period have a legitimate domain absent from irregular probes.
            offsets=np.arange(4,dtype=float)*(1+trial*.2) if trial%2==0 else np.array([0,.7,1.9,3.8])
            environment[key] = value+offsets if key in columns else (1.0 if trial==6 else value)
        try:
            with np.errstate(all="ignore"):
                a, b = np.asarray(evaluate(first, environment)), np.asarray(evaluate(second, environment))
            if a.shape == b.shape and np.all(np.isfinite(a)) and np.all(np.isfinite(b)) and not np.allclose(a, b, rtol=1e-10, atol=1e-12):
                return "different"
        except (ArithmeticError, AttributeError, IndexError, KeyError, TypeError, ValueError):
            continue
    return "unconfirmed"


def _math_text(text):
    normalized = re.sub(r"\s+", "", text.translate(str.maketrans({"＝": "=", "×": "*", "·": "*", "−": "-", "÷": "/",
                                                                  "²": "^2", "³": "^3"}))).replace("**", "^")
    return re.sub(r"(?<=[0-9)])(?=[A-Za-zΑ-Ωα-ω(])", "*", normalized)


def source_witness(item, part, catalog, shown_images, inputs):
    """Bind a transcribed source equation to physical inputs independently of either result graph."""
    from .worksheet_evidence import matched_passage

    witness, basis = item.get("verification"), item.get("basis")
    if not isinstance(witness, dict) or not isinstance(basis, dict):
        return None
    key = basis.get("page")
    source = catalog.get(key, {})
    formula = witness.get("equation", "")
    if not isinstance(formula, str) or formula.count("=") != 1 or not basis.get("locator"):
        return None
    lhs, rhs = (s.strip() for s in formula.split("="))
    if not lhs or not rhs:
        return None
    norm = _math_text(formula)
    text = source.get("text", "")
    # An adjacent caption alone cannot establish the coefficient. Match a whole equation
    # against the actual text, or corroborate an actual image with a separate saved transcription.
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(norm) + r"(?![A-Za-z0-9_*/^+\-])"
    in_text = re.search(pattern, _math_text(text)) and matched_passage(text, basis.get("quote"))
    in_image = key in shown_images and matched_passage(text, basis.get("quote")) and any(
        isinstance(e, dict) and item["id"] in e.get("targets", []) and e.get("source") == key
        and re.search(pattern, _math_text(str(e.get("relation", "")))) for e in part.get("equations", []))
    if not in_text and not in_image:
        return None
    try:
        node = expression_tree(_math_text(rhs))
    except (SyntaxError, ValueError, TypeError):
        return None
    bindings = {}
    declarations = witness.get("bindings")
    if not isinstance(declarations, list):
        return None
    for binding in declarations:
        if not isinstance(binding, dict) or not binding.get("meaning"):
            return None
        symbol, variable, page = (binding.get(k) for k in ("symbol", "input", "page"))
        if (not isinstance(symbol, str) or symbol in bindings or variable not in inputs
                or page not in part.get("material_pages", [])
                or not matched_passage(catalog.get(page, {}).get("text", ""), binding.get("quote"))):
            return None
        value = ast.Name(id=variable, ctx=ast.Load())
        op = binding.get("operation", "value")
        if op in ("first", "last", "mean", "sample_period"):
            value = ast.Call(func=ast.Name(id=op, ctx=ast.Load()), args=[value], keywords=[])
        elif op in ("slope", "integral") and binding.get("axis") in inputs:
            value = ast.Call(func=ast.Name(id=op, ctx=ast.Load()),
                             args=[ast.Name(id=binding["axis"], ctx=ast.Load()), value], keywords=[])
        elif op != "value":
            return None
        bindings[symbol] = value
    if not bindings or _names(node) - CONSTANTS.keys() != bindings.keys():
        return None
    class Bind(ast.NodeTransformer):
        def visit_Name(self, n):
            return copy.deepcopy(bindings[n.id]) if n.id in bindings else n

        def visit_Call(self, n):
            n.args = [self.visit(arg) for arg in n.args]
            return n

    result=Bind().visit(node)
    try:parse(ast.unparse(result),set(inputs))
    except (KeyError,TypeError,ValueError,SyntaxError):return None
    return result


def explained_source_revision(item,part,catalog):
    """A traced source review is a human-readable correction, not an identity proof."""
    from .worksheet_evidence import matched_passage
    basis=item.get("basis",{})
    reason=item.get("revision_reason")
    return (isinstance(reason,str) and bool(reason.strip()) and isinstance(basis,dict)
            and basis.get("page") in part.get("material_pages",[]) and bool(basis.get("locator"))
            and matched_passage(catalog.get(basis.get("page"),{}).get("text",""),basis.get("quote")))


def corroborate(original, revised, part, catalog, shown_images, pending):
    """Resolve each complete calculation independently, including changes to its dependencies."""
    old_graph, new_graph = FormulaGraph(original), FormulaGraph(revised)
    old_items = {item["id"]: item for item in formula_items(original)}
    accepted = {}
    held = set()
    for item in formula_items(revised):
        key = item["id"]
        old = old_items.get(key)
        current = expand_or_none(new_graph, item["expr"])
        earlier = expand_or_none(old_graph, old["expr"]) if old else None
        reference = source_witness(item, part, catalog, shown_images, new_graph.inputs|new_graph.definitions.keys())
        if reference is not None:reference=expand_or_none(new_graph,ast.unparse(reference))
        witness = item.get("verification")
        if reference is None and old is not None:
            reference = source_witness(old, part, catalog, shown_images, old_graph.inputs|old_graph.definitions.keys())
            if reference is not None:reference=expand_or_none(old_graph,ast.unparse(reference))
            if reference is not None:
                witness = old.get("verification")
        columns = old_graph.columns | new_graph.columns
        comparison = compare_formulas(earlier, current, columns)
        source_comparison = compare_formulas(current, reference, columns)
        if reference is not None and equivalent(current, reference):
            item["relation_check"] = "source_equation"
            item["verification"] = copy.deepcopy(witness)
            accepted[key] = current
        elif reference is not None and equivalent(earlier, reference):
            witness_basis = item.get("basis")
            item.update(copy.deepcopy(old))
            if witness_basis:
                item["basis"] = witness_basis
            item["relation_check"] = "source_equation"
            item["verification"] = copy.deepcopy(witness)
            accepted[key] = earlier
        elif comparison=="equivalent" and (reference is None or source_comparison=="unconfirmed"):
            # Retain a valid compact definition and its valid dependency path;
            # do not needlessly inline a differently scaled but identical graph.
            item.update(copy.deepcopy(old))
            item["relation_check"]="equivalent_definition"
            accepted[key]=earlier
        elif reference is None and comparison in ("different","different_inputs") and explained_source_revision(item,part,catalog):
            item["relation_check"]="source_review"
            item["earlier_definition"]=copy.deepcopy(old)
            accepted[key]=current
        elif (reference is None and (not old or comparison not in ("different","different_inputs"))
              or reference is not None and source_comparison == "unconfirmed" and (not old or comparison not in ("different","different_inputs"))):
            item["relation_check"] = "unconfirmed"
            accepted[key] = current
        else:
            reason = ("两份定义依赖的原始输入不同，需要原式与测量含义来选择" if reference is None and comparison=="different_inputs" else
                      "两份定义对同一输入给出不同值，需要原式与测量含义来选择"
                      if reference is None else "计算式与已绑定读数的来源原式给出不同值")
            problem = {"label": item["label"], "reason": reason, "definition": copy.deepcopy(item)}
            if old:
                problem["earlier_definition"] = copy.deepcopy(old)
            pending.append(problem)
            held.add(key)

    for group in [revised["results"], *(table.get("derived", []) for table in revised["tables"])]:
        group[:] = [v for v in group if v["id"] not in held]
    # Selections may come from different complete graphs. Keep a confirmed final result
    # evaluable from its own actual readings even if a disputed intermediate was removed.
    merged = FormulaGraph(revised)
    for item in formula_items(revised):
        expected = accepted.get(item["id"])
        if expected is not None and not equivalent(expected, expand_or_none(merged, item["expr"])):
            item["expr"] = ast.unparse(normalize_factors(expected))
