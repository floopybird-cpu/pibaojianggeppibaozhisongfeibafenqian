"""Attach physical units from an actual course-page assignment to numerical formula factors."""
import ast
import copy
import math
import re

from .worksheet_math import expression_tree
from .worksheet_units import check_formula_dimensions, convert, dimension, transform, UNITLESS
from .worksheet_evidence import matched_passage

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
VALUE = re.compile(r"(?<![A-Za-z_0-9.^+−-])"+NUMBER)
UNIT = re.compile(r"\s*([A-Za-zΩΩµμ℃°%][A-Za-z\dΩΩµμ℃°²³⁻¹*/·⋅^()（）−%\s-]*)")
ASSIGNMENT = re.compile(r"(?:[=＝≈]|约为|为约|为|取)\s*$")


def numeric_literal(node):
    if isinstance(node,ast.Constant) and type(node.value) in (int,float):
        return float(node.value)
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
        value=numeric_literal(node.operand)
        if value is not None:return -value if isinstance(node.op,ast.USub) else value
    return None


def printed_quantities(text):
    for token in VALUE.finditer(text.replace("−","-")):
        # Consume the entire adjoining unit, including PDF spacing and inverse powers.
        match=UNIT.match(text,token.end())
        if not match:continue
        unit=match[1].strip()
        while unit.endswith((")","）")) and unit.count(")")+unit.count("）")>unit.count("(")+unit.count("（"):
            unit=unit[:-1].strip()
        yield float(token[0]),unit,token.start(),match.start(1)+len(unit)


def verified_reference(ref,field_unit,source_text):
    """Find the stated physical quantity in the *cited passage*, in the units actually printed."""
    if not isinstance(ref,dict) or type(ref.get("value")) not in (int,float) or not isinstance(source_text,str):return None
    quote=matched_passage(source_text,ref.get("quote"))
    if not quote:return None
    stated_unit=ref.get("unit") or field_unit
    try:
        expected=convert(ref["value"],stated_unit,field_unit)
    except (ValueError,TypeError,KeyError):return None
    for value,unit,_,_ in printed_quantities(quote):
        try:
            dimension(unit)
            converted=convert(value,unit,field_unit)
        except (ValueError,TypeError,KeyError):continue
        if math.isfinite(converted) and math.isclose(converted,expected,rel_tol=1e-10,abs_tol=0):
            return {"page":ref.get("page"),"value":value,"unit":unit,"quote":quote}
    return None


def course_literals(text):
    matches={}
    for value,unit,start,end in printed_quantities(text):
        assignment=ASSIGNMENT.search(text[max(0,start-32):start])
        if not assignment:continue
        try:
            key=dimension(unit),transform(unit)
            if key[0]==UNITLESS:continue
        except (KeyError,ValueError,SyntaxError):continue
        matches.setdefault(value,{})[key]=(unit,text[max(0,start-32)+assignment.start():end])
    return matches


def bind_literals(item,units,catalog):
    item.pop("_course_parameter",None)
    basis=item.get("basis")
    source=basis.get("page") if isinstance(basis,dict) else None
    text=catalog.get(source,{}).get("text","")
    try:tree=expression_tree(item["expr"])
    except (KeyError,ValueError,TypeError,SyntaxError):return
    if not text:
        if any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="quantity" for n in ast.walk(tree)):
            item["literal_error"]="带单位的讲义定值需要能定位到数值与单位的原页"
        return
    fixed=numeric_literal(tree)
    fixed_unit=item["unit"]
    if isinstance(tree,ast.Call) and isinstance(tree.func,ast.Name) and tree.func.id=="quantity" and len(tree.args)==2:
        fixed=numeric_literal(tree.args[0])
        if isinstance(tree.args[1],ast.Constant) and isinstance(tree.args[1].value,str):fixed_unit=tree.args[1].value
        else:fixed=None
    if fixed is not None and fixed_unit not in ("","1","无量纲"):
        ref=verified_reference({"page":source,"quote":(item.get("basis") or {}).get("quote"),
                                "value":fixed,"unit":fixed_unit},item["unit"],text)
        if ref is not None:item["_course_parameter"]=ref
        else:item["literal_error"]="所引原文未能核对该定值及单位，请补充讲义原句或实际读数"
        return
    readings=course_literals(text)
    bound=[]
    def evidence(value,unit):
        return readings.get(value,{}).get((dimension(unit),transform(unit)))
    for n in ast.walk(tree):
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="quantity":
            try:
                original=evidence(float(n.args[0].value),n.args[1].value)
                if not original:raise ValueError()
                bound.append(dict(value=n.args[0].value,unit=original[0],page=source,quote=original[1]))
            except (ValueError,KeyError,TypeError,AttributeError,IndexError):
                item["literal_error"]="所引原页未能定位这个常数及单位，请核对课程参数"
                return
    if bound:item["literal_sources"]=bound
    try:check_formula_dimensions(tree,units,item["unit"],item["label"])
    except (ValueError,KeyError,TypeError,AttributeError):pass
    else:return
    # Only multiplicative coefficients can be annotated this way. Do not reinterpret
    # a power, list index, fitted intercept, or an unverified conversion factor.
    annotated=copy.deepcopy(tree)
    for node in ast.walk(annotated):
        if not isinstance(node,ast.BinOp) or not isinstance(node.op,(ast.Mult,ast.Div)):continue
        for side in ("left","right"):
            factor=getattr(node,side)
            if not isinstance(factor,ast.Constant) or type(factor.value) not in (int,float):continue
            choices=readings.get(factor.value,{})
            if len(choices)!=1:continue
            unit,quote=next(iter(choices.values()))
            setattr(node,side,ast.Call(func=ast.Name(id="quantity",ctx=ast.Load()),args=[factor,ast.Constant(value=unit)],keywords=[]))
            bound.append(dict(value=factor.value,unit=unit,page=source,quote=quote))
    if bound:
        try:check_formula_dimensions(annotated,units,item["unit"],item["label"])
        except (ValueError,KeyError,TypeError,AttributeError):return
        item["expr"]=ast.unparse(annotated)
        item["literal_sources"]=bound
