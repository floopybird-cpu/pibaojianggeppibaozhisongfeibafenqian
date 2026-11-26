"""Plotting, formula display and reproducible exports of the current measurement revision."""
import ast
import json
import math
import os
import re
import shutil
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np

from .excel_names import sheet_name
from .worksheet_math import compute, display_series, effective_fields, expression_tree, linear, number, resolved_window, selected_rows
from .worksheets import table_state


def literal(value):
    """A number as LaTeX; long integers and tiny magnitudes keep a readable order."""
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        return str(value)
    if not math.isfinite(value):
        return str(value)
    if abs(value)<1e5 and (value==0 or abs(value)>=1e-4):
        return str(value)
    text=format(value,".6g")
    if "e" not in text:
        return text
    mantissa,exponent=text.split("e")
    return mantissa.rstrip("0").rstrip(".")+r"\times 10^{"+str(int(exponent))+"}"


def formula(expr, symbols=None, *, display_names=None):
    def tex(n):
        if isinstance(n,ast.Subscript):return tex(n.value)+r"_{\left["+tex(n.slice)+r"\right]}"
        if isinstance(n,ast.BinOp) and isinstance(n.op,(ast.BitAnd,ast.BitOr)):
            return tex(n.left)+(r"\ \cap\ " if isinstance(n.op,ast.BitAnd) else r"\ \cup\ ")+tex(n.right)
        if isinstance(n,ast.IfExp):
            return r"\mathrm{choose}\left("+tex(n.test)+", "+tex(n.body)+", "+tex(n.orelse)+r"\right)"
        if isinstance(n,ast.Compare):
            sign={ast.Eq:"=",ast.NotEq:r"\ne ",ast.Gt:">",ast.GtE:r"\ge ",ast.Lt:"<",ast.LtE:r"\le "}[type(n.ops[0])]
            return tex(n.left)+sign+tex(n.comparators[0])
        if isinstance(n,(ast.List,ast.Tuple)):
            return r"\left["+ ", ".join(tex(e) for e in n.elts)+r"\right]"
        if isinstance(n,ast.Constant):
            return literal(n.value)
        if isinstance(n,ast.Name):
            if display_names and n.id in display_names:return display_names[n.id]
            source_symbol=(symbols or {}).get(n.id)
            if isinstance(source_symbol,str) and re.fullmatch(r"[A-Za-zΑ-Ωα-ω][A-Za-z0-9Α-Ωα-ω]*(?:_[A-Za-z0-9Α-Ωα-ω]+)?",source_symbol):
                left,sep,right=source_symbol.partition("_")
                return left+("_{"+right+"}" if sep else "")
            parts=n.id.split("_",1)
            var=lambda s:r"\mathrm{"+s.replace("_",r"\_")+"}"
            return r"\pi" if n.id=="pi" else var(parts[0])+("_{"+var(parts[1])+"}" if len(parts)>1 else "")
        if isinstance(n,ast.UnaryOp):
            return ("-" if isinstance(n.op,ast.USub) else "+")+tex(n.operand)
        if isinstance(n,ast.Call):
            args=", ".join(tex(a) for a in n.args)
            if n.func.id in ("first", "last", "sample_period") and len(n.args) == 1:
                def sample(index):
                    if isinstance(n.args[0], ast.Name):
                        indexed = re.search(r"_\{([ijkn])\}$", args)
                        if indexed:
                            return args[:indexed.start()] + "_{" + index + "}"
                    return "{" + args + "}_{" + index + "}"
                if n.func.id == "sample_period":
                    return r"\frac{" + sample("N") + "-" + sample("1") + "}{N-1}"
                return sample("1" if n.func.id == "first" else "N")
            if n.func.id=="quantity":
                from .worksheet_units import unit_expression
                u=unit_expression(n.args[1].value)
                u=re.sub(r"\*\*(-?\d+)",r"^{\1}",u).replace("*",r"\,")
                return tex(n.args[0])+r"\,\mathrm{"+u+"}"
            if n.func.id=="sum":
                indexed=re.search(r"_\{([ijkn])\}$",args)
                return (r"\sum_{"+indexed[1]+"} " if indexed else r"\sum ")+args
            if n.func.id=="integral" and len(n.args)==2:return r"\int "+tex(n.args[1])+r"\,\mathrm{d}"+tex(n.args[0])
            if n.func.id=="mean":return r"\overline{"+args+"}"
            if n.func.id=="abs":return r"\left|"+args+r"\right|"
            return r"\sqrt{"+args+"}" if n.func.id=="sqrt" else r"\mathrm{"+n.func.id.replace("_",r"\_")+r"}\left("+args+r"\right)"
        a,b=tex(n.left),tex(n.right)
        if isinstance(n.op,ast.Div):
            return r"\frac{"+a+"}{"+b+"}"
        if isinstance(n.op,ast.Pow):
            return "{"+group(n.left,a)+"}^{"+b+"}"
        op={ast.Add:"+",ast.Sub:"-",ast.Mult:r"\,\cdot\, "}[type(n.op)]
        if display_names and isinstance(n.op,ast.Mult) and not isinstance(n.right,(ast.Constant,ast.UnaryOp)):
            op=r"\,"
        def need(child,right=False):
            return (isinstance(n.op,ast.Mult) and isinstance(child,ast.BinOp) and isinstance(child.op,(ast.Add,ast.Sub)) or
                    right and isinstance(n.op,ast.Sub) and isinstance(child,ast.BinOp) and isinstance(child.op,(ast.Add,ast.Sub)))
        return (group(n.left,a) if need(n.left) else a)+op+(group(n.right,b) if need(n.right,True) else b)
    def group(node,value):
        return r"\left("+value+r"\right)" if isinstance(node,(ast.BinOp,ast.UnaryOp)) else value
    return tex(expression_tree(expr))


def label(item):
    unit=item.get("unit","")
    return item["label"]+(f" ({unit})" if unit and unit!="1" else "")


def draw(ax, page, data, plot):
    table=next(t for t in page["tables"] if t["id"]==plot["table"])
    columns={c["id"]:c for c in table["columns"]+table.get("derived",[])}
    record=data.get("tables",{}).get(table["id"],{})
    rows=record.get("rows",[])
    fields=effective_fields(page,data)
    active=list(selected_rows(table,record,fields,page))
    series=display_series(page,table,rows,fields,record.get("directions"))
    subset=display_series(page,table,active,fields,record.get("directions"))
    colors=["#007a79","#b05a14","#6d56a0","#4472a8"]
    from .scientific_markup import plot_text
    ax.clear();ax.set_title(plot_text(plot["title"]),loc="left",fontsize=11)
    ax.set_xlabel(plot_text(label(columns[plot["x"]])))
    flipped = [plot_text(columns[k]["label"]) for k, v in record.get("directions", {}).items() if v == -1 and k in columns]
    if flipped:
        ax.set_title(plot_text(plot["title"]) + "\n计算反向  " + "、".join(flipped),loc="left",fontsize=10)
    ys=plot["ys"];ax.set_ylabel(" / ".join(dict.fromkeys(columns[k]["unit"] for k in ys)))
    info=["按确认的反向通道计算："+ "、".join(columns[k]["label"] for k,v in record.get("directions",{}).items() if v==-1 and k in columns)] if any(v==-1 for v in record.get("directions",{}).values()) else []
    for i,col in enumerate(ys):
        color=colors[i%len(colors)]
        x,y=series[plot["x"]],series[col]
        valid=np.isfinite(x)&np.isfinite(y)
        if np.any(valid):
            ax.plot(x,y,color=color,alpha=.4,linewidth=.9,label=plot_text(columns[col]["label"])+" · 全部")
        if active:
            xa,ya=subset[plot["x"]],subset[col]
            good=np.isfinite(xa)&np.isfinite(ya)
            if np.any(good):
                ax.plot(xa[good],ya[good],".",markersize=3,color=color,label=plot_text(columns[col]["label"])+" · 选用")
                info.append(f"{columns[col]['label']}：选区有效点 {int(good.sum())} / 全部有效点 {int(valid.sum())}")
            if plot.get("fit")=="linear" and good.sum()>=2:
                try:
                    sigma=subset[plot["sigma"]] if plot.get("sigma") else None
                    fit=linear(xa,ya,sigma)
                    interval=[xa[good].min(),xa[good].max()]
                    ax.plot(interval,fit["slope"]*np.array(interval)+fit["intercept"],"--",color=color,label=plot_text(columns[col]["label"])+" · 拟合")
                    info.append(f"{columns[col]['label']}　斜率 {fit['slope']:.5g}　截距 {fit['intercept']:.5g}　R² {fit['r2']:.4g}")
                except ValueError as e:
                    info.append(str(e))
    window=resolved_window(table,record,fields,page)
    if window and window.get("pending"):info.append("请填写选区条件："+window["pending"])
    if window and window["column"]==plot["x"]:
        lo,hi=ax.get_xlim()
        ax.axvspan(window["min"] if window["min"] is not None else lo,window["max"] if window["max"] is not None else hi,color="#f0bc50",alpha=.14)
    if ax.lines:ax.legend(fontsize=8,loc="best")
    ax.grid(alpha=.18)
    return "　".join(info)


def configure_fonts():
    from matplotlib import rcParams
    rcParams["font.sans-serif"]=["Microsoft YaHei","SimHei","DejaVu Sans"]
    rcParams["axes.unicode_minus"]=False


def export(app,obj,path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from .publications import filename
    configure_fonts()
    target=Path(path).resolve()
    target.parent.mkdir(parents=True,exist_ok=True)
    version,data=app.worksheets.state(obj["id"])
    spec={**obj["content"],"prior_result_links":app.worksheets.prior_links(obj)}
    book=Workbook();book.remove(book.active);names=set();results={}
    def json_file(directory,name,value):
        (directory/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    with tempfile.TemporaryDirectory(dir=target.parent) as staging:
        stage=Path(staging)
        manifest={"experiment":obj["experiment_id"],"definition_revision":obj["revision"],"measurement_revision":version,
                  "draft_ref":spec["draft_ref"],"citations":[]}
        for a in spec["sources"]:
            anchor=app.store.anchor(a)
            manifest["citations"].append(dict(id=a,name=anchor["name"],page=anchor["page"],sha256=anchor["source_sha"]))
        json_file(stage,"definition.json",spec);json_file(stage,"measurements.json",data)
        gaps = {p["id"]:p["unresolved"] for p in spec["pages"] if p.get("unresolved")}
        if gaps:
            manifest["unresolved_file"] = "unresolved.json"
            json_file(stage, "unresolved.json", gaps)
        draft=app.store.object(spec["draft_ref"]["id"],spec["draft_ref"]["revision"])
        from .experiment_drafts import outline_markdown
        (stage/"实验草稿.md").write_text(outline_markdown(draft["content"]),encoding="utf-8")
        json_file(stage,"实验草稿.json",draft["content"])
        current=app.agent.experiment_drafts.current(obj["experiment_id"])
        if current and current["id"]==draft["id"] and current["revision"]>draft["revision"]:
            manifest["latest_draft_ref"]={"id":current["id"],"revision":current["revision"]}
            (stage/"实验草稿（含后续调整）.md").write_text(outline_markdown(current["content"]),encoding="utf-8")
        json_file(stage,"sources.json",manifest)
        shutil.copyfile(Path(__file__).with_name("worksheet_math.py"),stage/"analysis.py")
        shutil.copyfile(Path(__file__).with_name("worksheet_units.py"),stage/"worksheet_units.py")
        (stage/"使用说明.txt").write_text("数据和结果可用 Excel 阅读。用含 NumPy 的 Python 运行 analysis.py 可从相同定义、同一批读数和选区重算 results.json。数值以原始精度保存，display 为报告用值。",encoding="utf-8")
        def append(ws,values,header=False):
            ws.append(values)
            for cell in ws[ws.max_row]:
                if cell.data_type=="f":cell.data_type="s"
                if header:
                    cell.fill=PatternFill("solid",fgColor="195769")
                    cell.font=Font(color="FFFFFF",bold=True)
        for i,page in enumerate(spec["pages"],1):
            state=data.get(page["id"],{})
            result=compute(page,state);results[page["id"]]=result
            ws=book.create_sheet(sheet_name(page["title"],names))
            from .worksheet_presentation import equation_symbols, row_equation, scientific_text, unresolved_messages
            from .math_display import linear_text, png, raster
            from openpyxl.drawing.image import Image as SheetImage
            from io import BytesIO
            symbols = equation_symbols(page)
            append(ws,["量","输入或结果","单位","计算式","计算条件","公式排版"],True)
            formula_width = 14
            for f in page["fields"]:
                raw=effective_fields(page,state).get(f["id"],"")
                numeric=number(raw)
                append(ws,[scientific_text(f["label"]),numeric if np.isfinite(numeric) else raw,f["unit"],None,scientific_text(f.get("hint") or "")])
            for table in page["tables"]:
                setting = state.get("tables", {}).get(table["id"], {}).get("directions", {})
                reversed_labels = [scientific_text(c["label"]) for c in table["columns"] if setting.get(c["id"]) == -1]
                if reversed_labels:
                    append(ws, ["通道方向", "、".join(reversed_labels), None, None, "表中保存仪器原始符号；逐行计算及汇总使用反向后的值。"])
            for observation in page.get("observations",[]):
                append(ws,[observation["label"],state.get("observations",{}).get(observation["id"],""),None,None,observation.get("meaning")])
            for r,model in zip(result,page["results"]):
                equation = row_equation(model, r, symbols)
                try:
                    readable = linear_text(equation)
                    size, img = raster(equation)
                except ValueError:
                    readable, img = equation, None
                condition = " ".join(filter(None, [r.get("interval_note"), scientific_text(model.get("note") or "")]))
                append(ws,[scientific_text(r["label"]), r.get("display") or r.get("pending"), r["unit"], readable, condition])
                if img is None:
                    continue
                graphic = SheetImage(BytesIO(png(img)))
                graphic.width, graphic.height = size.width(), size.height()
                ws.add_image(graphic, "F" + str(ws.max_row))
                ws.row_dimensions[ws.max_row].height = max(20, size.height() * 0.8 + 5)
                formula_width = max(formula_width, size.width()/7 + 3)
            ws.column_dimensions["F"].width = min(255,formula_width)
            for missing, explanation in zip(page.get("unresolved",[]), unresolved_messages(page)):
                append(ws,[scientific_text(missing["label"]),"待核对",None,None,explanation])
            for table in page["tables"]:
                t=book.create_sheet(sheet_name(table["title"],names))
                derived=table.get("derived",[])
                append(t,["参与计算",*[scientific_text(label(c)) for c in table["columns"]+derived]],True)
                content=table_state(state,table)
                fields=effective_fields(page,state)
                chosen={id(r) for r in selected_rows(table,content,fields,page)}
                series=display_series(page,table,content["rows"],fields,content.get("directions"))
                for j,row in enumerate(content["rows"]):
                    vals=[]
                    for col in table["columns"]:
                        val=row.get(col["id"],"");num=number(val)
                        vals.append(float(num) if np.isfinite(num) else val)
                    has_data=any(str(row.get(c["id"],"")).strip() for c in table["columns"])
                    for col in derived:
                        value=series[col["id"]][j]
                        vals.append(float(value) if np.isfinite(value) else None)
                    append(t,["是" if has_data and id(row) in chosen else "否",*vals])
            for n,plot in enumerate(page["plots"],1):
                rows=state.get("tables",{}).get(plot["table"],{}).get("rows",[])
                if not any(any(k!="_excluded" and str(v).strip() for k,v in r.items()) for r in rows):
                    continue
                fig=Figure(figsize=(8,4.4),layout="constrained");FigureCanvasAgg(fig)
                draw(fig.add_subplot(),page,state,plot)
                fig.savefig(stage/f"{i}-{n} {filename(plot['title'])}.png",dpi=145)
        for ws in book:
            ws.freeze_panes="B2"
            for col in ws.iter_cols():
                if col[0].value == "公式排版": continue
                ws.column_dimensions[col[0].column_letter].width=min(70,max(13,max(len(str(c.value or "")) for c in col)*1.6+1))
        title=filename(spec["title"].split(" · 数据处理台")[0])
        workbook_name=f"{title} 测量与结果 {obj['id'][-6:]}-r{obj['revision']}-{version}.xlsx"
        book.save(stage/workbook_name);json_file(stage,"results.json",results)
        archive=stage/"export.zip"
        with ZipFile(archive,"w",ZIP_DEFLATED) as out:
            for file in stage.iterdir():
                if file!=archive:out.write(file,file.name)
        os.replace(archive,target)
    return target
