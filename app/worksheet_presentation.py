"""Present worksheet quantities and group live results by the readings they depend on."""
from functools import lru_cache
import html
import math
import re

from .worksheet_units import FAMILIES, convert, equivalent_form, read_quantity

SUPERSCRIPT = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")


def unit_text(unit):
    text = unit.replace("degC", "°C")
    text = re.sub(r"(?:\^|\*\*)\{?([-+]?\d+)\}?", lambda m:m[1].translate(SUPERSCRIPT), text)
    return text.replace("*", "·")


@lru_cache(maxsize=256)
def math_image(source):
    from base64 import b64encode
    from .math_display import png, raster
    size, image = raster(source, 15)
    encoded = b64encode(png(image)).decode("ascii")
    return f'<img alt="{html.escape(source, quote=True)}" width="{size.width()}" height="{size.height()}" src="data:image/png;base64,{encoded}">'


def scientific_text(text, rich=False):
    from .math_display import linear_text
    from .scientific_markup import parts
    output = []
    for mathematical, value in parts(str(text)):
        if mathematical:
            try:
                output.append(math_image(value) if rich else linear_text(value))
                continue
            except (ValueError, TypeError, RuntimeError, KeyError):
                pass
        output.append(html.escape(value) if rich else value)
    return "".join(output)



def quantity_label(item, rich=False, unit=None):
    name = scientific_text(item["label"], rich)
    value = unit_text(item.get("unit","") if unit is None else unit)
    if value and value not in ("1","文字","文本"):
        return name + " (" + (html.escape(value) if rich else value) + ")"
    return name


def entry_unit(field):
    unit = field["unit"]
    family = next((f for f in FAMILIES if equivalent_form(unit) in f), ())
    hint = field.get("hint","").replace("毫升","mL")
    # A single explicitly named measuring unit can be used directly at the bench.
    match = re.search(r"(?:由|从|原始单位(?:为|是)|读数单位(?:为|是))\s*([A-Za-zμµ°]+)(?=\s*(?:换算|转为|转换|，|。|；|$))", hint)
    if match and equivalent_form(match[1]) in family:
        return match[1]
    return unit


def field_text(raw, field, unit):
    if unit == field["unit"] or not str(raw).strip():
        return str(raw)
    value = read_quantity(raw, field["unit"])
    return format(convert(value, field["unit"], unit),".15g") if math.isfinite(value) else str(raw)


SYMBOL = r"(?:[Δδ][A-Za-z]|[A-Za-zΑ-ω])(?:_(?:\{[A-Za-z0-9,+-]+\}|[A-Za-z0-9]+))?"
ASSIGN = re.compile(r"(?<![A-Za-zΑ-ω0-9_])(" + SYMBOL + r")\s*=")


def symbol_tex(symbol):
    symbol = (symbol or "").strip().strip("$")
    if not symbol:
        return ""
    if "\\" in symbol:
        from .scientific_markup import EXPLICIT
        from .math_display import linear_text
        wrapped = EXPLICIT.fullmatch(symbol)
        if wrapped:
            symbol = next(v for v in wrapped.groups()[1:] if v is not None)
        if re.fullmatch(r"[a-zA-ZΑ-ω0-9\\_^{} ,'+-]+", symbol) and symbol.count("{")==symbol.count("}"):
            try:
                linear_text(symbol)
                return symbol
            except (TypeError, ValueError, KeyError):
                pass
    if re.fullmatch(r"[Α-ωA-Za-z](?:[A-Za-z0-9Α-ω]+)?(?:_(?:\{[A-Za-z0-9,+-]+\}|[A-Za-z0-9,+-]+))?",symbol):
        stem,sep,sub = symbol.partition("_")
        if len(stem)>2 and not (stem.isupper() and len(stem)<=5):return ""
        sub = sub.strip("{}")
        return stem + ("_{" + (r"\mathrm{"+sub+"}" if len(sub)>1 else sub) + "}" if sep else "")
    if re.fullmatch(r"\\(?:alpha|beta|gamma|delta|eta|lambda|mu|rho|tau|phi|theta|sigma|omega|Delta|Phi|Omega)(?:_\{?[A-Za-z0-9]+\}?)?",symbol):
        return symbol
    if re.fullmatch(r"\\(?:bar|overline|dot)\{[A-Za-zΑ-ω]\}",symbol):
        return symbol
    return ""


def equation_symbols(page):
    import ast
    from .worksheet_math import expression_tree
    items = page["fields"] + [c for t in page["tables"] for c in t["columns"] + t.get("derived", [])] + page["results"]
    symbols = {}
    for item in items:
        key = item["id"]
        symbol = symbol_tex(item.get("symbol"))
        if not symbol:
            tail = re.search(r"(?:^|\s)(" + SYMBOL + r")$", item.get("label", ""))
            if tail:
                symbol = symbol_tex(tail[1])
        if not symbol and item.get("expr"):
            expr = expression_tree(item["expr"])
            note = item.get("note","")
            named = re.search(r"(?:记为|记作|得到|定义为)\s*(" + SYMBOL + r")\s*=",note)
            if isinstance(expr, ast.Call) and expr.func.id == "sample_period":
                symbol = r"\Delta t_{\mathrm{meas}}"
            elif named:
                symbol = symbol_tex(named[1])
            elif isinstance(expr,ast.Call) and expr.func.id=="mean" and isinstance(expr.args[0],ast.Name):
                src = symbols.get(expr.args[0].id,"")
                m = re.fullmatch(r"([A-Za-zΑ-ω])_\{[ijkn]\}",src)
                if m:symbol = r"\overline{"+m[1]+"}"
            elif (first := ASSIGN.search(note)) and (len(set(ASSIGN.findall(note)))==1 or re.fullmatch(r"[\u3400-\u9fff\s]{0,18}(?:系数|效率|功率|速度|温差|位移)",note[:first.start()])):
                candidate = first[1]
                # The quantity on the left of an equation about an input can differ from this output.
                if not any(symbol_tex(candidate)==existing for existing in symbols.values()):
                    symbol = symbol_tex(candidate)
        name = re.sub(r"[^A-Za-z0-9\u3400-\u9fffΑ-ω °℃·+-]+"," ",item["label"]).strip()
        symbols[key] = symbol or r"\text{"+(name or "该量")+"}"
    return symbols


def row_equation(item, row, symbols):
    import ast
    from .worksheet_math import expression_tree
    from .worksheet_output import formula
    rhs = formula(item["expr"], display_names=symbols)
    expr = expression_tree(item["expr"])
    if isinstance(expr,ast.Call) and expr.func.id=="mean" and isinstance(expr.args[0],ast.Name):
        term = symbols.get(expr.args[0].id,"")
        indexed = re.search(r"_\{([ijkn])\}$",term)
        if indexed:
            rhs = r"\frac{1}{N}\sum_{"+indexed[1]+r"=1}^{N} "+term
    tex = symbols[item["id"]] + "=" + rhs
    display = row.get("display","").split("（",1)[0].strip()
    if display:
        value = re.sub(r"([+-]?\d+(?:\.\d+)?)[eE]([+-]?\d+)",lambda m:m[1]+r"\times 10^{"+str(int(m[2]))+"}",display)
        unit = unit_text(item.get("unit",""))
        unit = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+",lambda m:"^{"+m[0].translate(str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻","0123456789+-"))+"}",unit)
        tex += "=" + value.replace("±",r"\pm ")
        if unit and unit!="1":tex += r"\,\mathrm{"+unit.replace("·",r"\,")+"}"
    return tex


def unresolved_messages(page):
    missing = page.get("unresolved", [])
    labels = {x["definition"]["id"]: x["label"] for x in missing
              if isinstance(x.get("definition"), dict) and x["definition"].get("id")}
    for x in missing:
        reason = str(x.get("reason") or "")
        if "含手写倍率" in reason:
            message = "输入单位和公式中的换算系数需要核对"
        elif reason.startswith("未知变量或不支持的表达式") and reason.rsplit("：",1)[-1].strip() in labels:
            message = "依赖" + labels[reason.rsplit("：",1)[-1].strip()]
        elif "量纲" in reason:
            message = "公式两侧的单位需要核对"
        else:
            message = "这项计算的定义需要核对"
        yield x["label"] + " — " + message


def missing_readings(page, state, rows):
    from .worksheet_math import effective_fields
    from .worksheet_identity import FormulaGraph
    graph = FormulaGraph(page)
    fields = effective_fields(page,state)
    table_records = state.get("tables",{})
    for item,row in zip(page["results"],rows):
        reason = row.get("pending","")
        if not reason.startswith(("缺少","尚无有效读数")):
            continue
        deps = graph.dependencies(item["expr"])
        expressions = [item["expr"], *(graph.definitions[k] for k in deps if k in graph.definitions)]
        if any(re.search(r"\b(coalesce|choose)\s*\(|\bif\b", expr) for expr in expressions):
            continue
        missing = [f["label"] for f in page["fields"] if f["id"] in deps and
                   not math.isfinite(read_quantity(fields.get(f["id"],""),f["unit"]))]
        for table in page["tables"]:
            ids = {c["id"] for c in table["columns"]} & deps
            measurements = table_records.get(table["id"],{}).get("rows",[])
            if ids and not any(not r.get("_excluded") and any(str(r.get(k,"")).strip() for k in ids) for r in measurements):
                missing.append(table["title"]+"读数")
        if missing:
            row["pending"] = "待填　" + "、".join(missing)
