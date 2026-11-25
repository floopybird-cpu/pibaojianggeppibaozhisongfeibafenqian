from __future__ import annotations

import html
import json
import re
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from lxml import etree
from latex2mathml.converter import convert
from .display_text import goal_labels,display_unit

M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def add_rich_text(paragraph, value):
    """LaTeX within ordinary prose and table cells stays editable in Word."""
    from .scientific_markup import parts
    for mathematical, part in parts(str(value)):
        if mathematical:
            paragraph._p.append(formula_omml(part))
        else:
            paragraph.add_run(part)
    return paragraph


def math_element(tag, *children, text=None):
    node = etree.Element("{" + M + "}" + tag)
    if text is not None:
        node.text = text
    node.extend(children)
    return node


def formula_omml(latex):
    """MathML -> editable native Word math. Unsupported constructs fail explicitly."""
    if not isinstance(latex, str) or not latex.strip():
        raise ValueError("公式不能为空")
    if re.search(r"\\(?:input|include|href|html|url|class|style|def|newcommand|require)\b", latex):
        raise ValueError("公式含不支持的宏或外部引用")
    try:
        root = etree.fromstring(convert(latex).encode(), etree.XMLParser(resolve_entities=False, no_network=True))
    except Exception as exc:
        raise ValueError("LaTeX 公式无法转换：" + str(exc)) from exc
    if re.search(r"\\[A-Za-z]+", "".join(root.itertext())):
        raise ValueError("存在未支持或拼写错误的 LaTeX 命令，未按普通文字导出")

    def walk(node):
        tag = etree.QName(node).localname
        children = list(node)
        if tag in ("math", "mrow", "mstyle", "mpadded", "semantics"):
            return [item for child in children for item in walk(child) if etree.QName(child).localname != "annotation"]
        if tag in ("mi", "mn", "mo", "mtext", "ms"):
            return [math_element("r", math_element("t", text=node.text or ""))]
        if tag == "mspace":
            return [math_element("r", math_element("t", text=" "))]
        if tag == "mfrac":
            return [math_element("f", math_element("num", *walk(children[0])), math_element("den", *walk(children[1])))]
        if tag in ("msup", "msub", "msubsup"):
            name = {"msup": "sSup", "msub": "sSub", "msubsup": "sSubSup"}[tag]
            result = math_element(name, math_element("e", *walk(children[0])))
            if tag in ("msub", "msubsup"):
                result.append(math_element("sub", *walk(children[1])))
            if tag in ("msup", "msubsup"):
                result.append(math_element("sup", *walk(children[2 if tag == "msubsup" else 1])))
            return [result]
        if tag in ("msqrt", "mroot"):
            props = math_element("radPr")
            if tag == "msqrt":
                hide = math_element("degHide"); hide.set("{" + M + "}val", "1"); props.append(hide)
            deg = math_element("deg", *(walk(children[1]) if tag == "mroot" else []))
            body = walk(children[0]) if tag == "mroot" else [e for c in children for e in walk(c)]
            return [math_element("rad", props, deg, math_element("e", *body))]
        if tag in ("mover", "munder", "munderover"):
            if tag == "mover" and (children[1].text or "") in ("¯", "→", "^", "ˆ", "˙", "⃗", "⏞"):
                char = math_element("chr"); char.set("{" + M + "}val", children[1].text)
                return [math_element("acc", math_element("accPr", char), math_element("e", *walk(children[0])))]
            base = math_element("e", *walk(children[0]))
            if tag == "mover":
                return [math_element("limUpp", base, math_element("lim", *walk(children[1])))]
            low = math_element("limLow", base, math_element("lim", *walk(children[1])))
            return [math_element("limUpp", math_element("e", low), math_element("lim", *walk(children[2])))] if tag == "munderover" else [low]
        if tag == "mtable":
            rows = []
            for row in children:
                rows.append(math_element("mr", *[math_element("e", *walk(cell)) for cell in row]))
            return [math_element("m", *rows)]
        if tag in ("mtd", "mtr"):
            return [e for child in children for e in walk(child)]
        if tag == "mfenced":
            begin = math_element("begChr"); begin.set("{" + M + "}val", node.get("open", "("))
            end = math_element("endChr"); end.set("{" + M + "}val", node.get("close", ")"))
            return [math_element("d", math_element("dPr", begin, end), *[math_element("e", *walk(c)) for c in children])]
        if tag == "menclose" and node.get("notation") in ("box", "roundedbox"):
            return [math_element("borderBox", math_element("e", *[e for c in children for e in walk(c)]))]
        if tag == "annotation":
            return []
        raise ValueError("Word 数学暂不支持 MathML 构造 " + tag + "；保留旧文档，未静默丢弃")

    return math_element("oMath", *walk(root))


def validate_document(content, store):
    if content["doc_type"] not in ("short", "deep", "plan"):
        raise ValueError("文档类型须为 short/deep/plan")
    if not content["summary"].strip() or len(content["sections"]) < 2:
        raise ValueError("文档缺学习主线或章节")
    anchors = set(content.get("sources", []))
    all_sections = content["sections"] + content["appendices"]
    ids = [section["id"] for section in all_sections]
    if len(ids) != len(set(ids)):
        raise ValueError("章节／附录 ID 重复")
    if content["doc_type"] == "deep" and not {"math_basis", "derivation"} <= set(ids):
        raise ValueError("深入讲义必须补数学基础和逐步推导")
    for section in all_sections:
        if not section.get("title") or not section.get("blocks"):
            raise ValueError("章节缺标题或内容")
        for block in section["blocks"]:
            kind = block.get("type")
            if kind not in ("paragraph", "equation", "table", "figure", "code", "crossref"):
                raise ValueError("不支持的文档块: " + str(kind))
            if kind != "crossref" and not block.get("sources"):
                raise ValueError("每个论述／公式／图表必须有旁侧来源")
            if not set(block.get("sources", [])) <= anchors:
                raise ValueError("块来源未列入文档来源清单")
            if kind == "equation":
                formula_omml(block["latex"])
                if not block.get("label") or not block.get("symbols"):
                    raise ValueError("公式必须有编号和符号说明")
                if block.get("formula_ref"):
                    formula = store.object(block["formula_ref"]["id"], block["formula_ref"]["revision"])
                    if formula["kind"] != "Formula" or formula["content"]["latex"] != block["latex"]:
                        raise ValueError("引用公式与显示公式不一致")
            if kind == "table" and (not block.get("columns") or any(len(row) != len(block["columns"]) for row in block.get("rows", []))):
                raise ValueError("表格列数不一致")
            if kind == "figure":
                anchor = store.anchor(block["source_anchor"])
                page = store.one("SELECT image FROM pages WHERE source_id=? AND number=?", (anchor["source_id"], anchor["page"]))
                if not page or not page["image"]:
                    raise ValueError("图像来源缺原页图")
            if kind == "crossref" and block.get("target") not in ids:
                raise ValueError("交叉引用指向不存在的章节／附录")
    if content["doc_type"] == "plan":
        required = {"records", "errors", "instruments", "reading"}
        if not required <= {a["id"] for a in content["appendices"]}:
            raise ValueError("实验单需分别有记录、误差、仪器操作、拓展阅读附录")
        goals, steps = content.get("goals", []), content.get("steps", [])
        if not goals or not steps:
            raise ValueError("实验计划缺目标到读数与操作顺序")
        goal_ids = {g["id"] for g in goals}
        for goal in goals:
            if not all(goal.get(k) for k in ("quantity", "unit", "model", "formula_ref", "measurements", "conditions", "sources")):
                raise ValueError("目标量缺公式、条件、单位、来源或必需读数")
            formula = store.object(goal["formula_ref"]["id"], goal["formula_ref"]["revision"])
            if formula["kind"] != "Formula":
                raise ValueError("目标公式类型错误")
            for reading in goal["measurements"]:
                if not all(reading.get(k) for k in ("quantity", "unit", "timing", "condition", "instrument", "precision")):
                    raise ValueError("每项读数需量、单位、时机、条件、仪器、精度；未知须显式填待确认")
            if not set(goal["sources"]) <= anchors:
                raise ValueError("目标来源不在清单中")
        for step in steps:
            if not all(step.get(k) for k in ("id", "action", "reason", "conditions", "completion", "goal_ids", "instrument_appendix", "sources")):
                raise ValueError("步骤缺理由、条件、完成判据或附录入口")
            if not set(step["goal_ids"]) <= goal_ids or step["instrument_appendix"] != "instruments":
                raise ValueError("步骤目标或仪器附录引用无效")
        if goal_ids - {g for s in steps for g in s["goal_ids"]}:
            raise ValueError("有目标没有对应操作步骤")


def bookmark(paragraph, name, index):
    start = OxmlElement("w:bookmarkStart"); start.set(qn("w:id"), str(index)); start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd"); end.set(qn("w:id"), str(index))
    paragraph._p.insert(0, start); paragraph._p.append(end)


def internal_link(paragraph, text, target):
    link = OxmlElement("w:hyperlink"); link.set(qn("w:anchor"), target)
    run = OxmlElement("w:r"); props = OxmlElement("w:rPr")
    style = OxmlElement("w:rStyle"); style.set(qn("w:val"), "Hyperlink"); props.append(style)
    run.append(props); node = OxmlElement("w:t"); node.text = text; run.append(node); link.append(run); paragraph._p.append(link)


def build_document(content, out: Path, store, template_path):
    config = json.loads(Path(template_path).read_text(encoding="utf-8"))
    if config.get("composition") == "markdown-layout/1":
        from .document_composition import build_composed_document
        return build_composed_document(content, out, store, template_path)
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin = Cm(2), Cm(2)
    sec.left_margin, sec.right_margin = Cm(2.1), Cm(2.1)
    font = content.get("style", {}).get("font") or config.get("font", "Microsoft YaHei")
    language=content.get("style",{}).get("language") or config.get("language","zh-CN")
    doc.core_properties.language=language
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "Caption"):
        style = doc.styles[name]
        style.font.name = font
        style._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), font)
        language_element=OxmlElement("w:lang");language_element.set(qn("w:val"),language);language_element.set(qn("w:eastAsia"),language);style._element.get_or_add_rPr().append(language_element)
        style.font.color.rgb = RGBColor.from_string("172631")
    for border in doc.styles.element.xpath(".//w:pBdr"):
        border.getparent().remove(border)
    doc.styles["Normal"].font.size = Pt(10.5)
    doc.styles["Normal"].paragraph_format.space_after = Pt(6)
    doc.styles["Normal"].paragraph_format.line_spacing = 1.15
    doc.styles["Title"].font.size = Pt(22)
    doc.styles["Title"].font.color.rgb = RGBColor(0, 0, 0)
    doc.styles["Heading 1"].font.size = Pt(15)
    doc.styles["Heading 2"].font.size = Pt(12)
    doc.core_properties.title = content["title"]
    doc.core_properties.author = "物理实验助手"
    add_rich_text(doc.add_paragraph(style="Title"),content["title"])
    add_rich_text(doc.add_paragraph(),content["summary"])
    bg = content["background"]
    doc.add_paragraph("学习背景：" + str(bg.get("level", "未填写")) + "　目标：" + str(bg.get("goal", "未填写")) + "　可用时间：" + str(bg.get("minutes", "未填写")) + " 分钟")
    if content.get("simulation"):
        doc.add_paragraph("模拟教学示例　此文档不包含真实实验测量或课程实测结论")
    if content.get("warnings"):
        doc.add_heading("待确认事项", 1)
        for warning in content["warnings"]:
            doc.add_paragraph(warning)
    source_numbers = {a:i+1 for i,a in enumerate(content["sources"])}
    md = ["# " + content["title"], "", content["summary"], "", "背景：" + json.dumps(bg, ensure_ascii=False), ""]
    if content.get("simulation"):
        md.extend(["> 模拟教学示例，不是真实实验记录。", ""])
    for warning in content.get("warnings", []):
        md.extend(["> 待确认：" + warning, ""])
    preview = ["<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>" + html.escape(content["title"]) + "</title><body>", "<h1>" + html.escape(content["title"]) + "</h1>", "<p>" + html.escape(content["summary"]) + "</p>"]

    def cites(anchors):
        return " ".join("[" + str(source_numbers[a]) + "]" for a in anchors)

    def add_citations(paragraph, anchors):
        for anchor in anchors:
            internal_link(paragraph, " [" + str(source_numbers[anchor]) + "]", "source_" + str(source_numbers[anchor]))

    # The traceability model is rendered, not merely hidden in JSON.
    if content["doc_type"] == "plan":
        doc.add_heading("目标量与必需读数", 1)
        md.extend(["## 目标量与必需读数", ""])
        shown_formulas=set()
        labels=goal_labels(content["goals"])
        for goal in content["goals"]:
            p = add_rich_text(doc.add_paragraph(),labels[goal["id"]] + "　" + goal["quantity"] + " / " + display_unit(goal["unit"]) + "　" + goal["model"])
            add_citations(p, goal["sources"])
            doc.add_paragraph("适用条件：" + "；".join(goal["conditions"]))
            reference=goal["formula_ref"];key=(reference["id"],reference["revision"])
            if config.get("show_goal_equations") and key not in shown_formulas:
                formula=store.object(*key)["content"];paragraph=doc.add_paragraph();paragraph._p.append(formula_omml(formula["latex"]));add_citations(paragraph,goal["sources"])
                symbols="；".join(s["symbol"]+"："+s["quantity"]+" / "+display_unit(s["unit"]) for s in formula["symbols"])
                add_rich_text(doc.add_paragraph(),"符号："+symbols);md.extend(["$$"+formula["latex"]+"$$","",symbols+" "+cites(goal["sources"]),""]);shown_formulas.add(key)
            headers = ["直接读数", "单位", "读数时机", "条件", "仪器", "精度"]
            rows = [[display_unit(r[k]) if k=="unit" else r[k] for k in ("quantity", "unit", "timing", "condition", "instrument", "precision")] for r in goal["measurements"]]
            render_table(doc, headers, rows,keep_header=config.get("keep_table_header_with_first_row",False),content_aware=config.get("content_aware_table_widths",False))
            md.extend([labels[goal["id"]] + " " + goal["quantity"] + " / " + display_unit(goal["unit"]) + " " + cites(goal["sources"]), "", markdown_table(headers, rows), ""])
        doc.add_heading("操作顺序与完成判据", 1)
        md.extend(["## 操作顺序与完成判据", ""])
        for index,step in enumerate(content["steps"],1):
            add_rich_text(doc.add_heading('',2),str(index) + " " + step["action"])
            p = doc.add_paragraph("理由：" + step["reason"] + "；条件：" + step["conditions"] + "；完成判据：" + step["completion"])
            add_citations(p, step["sources"])
            internal_link(doc.add_paragraph("对应目标：" + "、".join(labels[g] for g in step["goal_ids"]) + "　"), "仪器操作附录", "section_instruments")
            md.extend(["### " + str(index) + " " + step["action"], "", "理由：" + step["reason"] + "；条件：" + step["conditions"] + "；完成判据：" + step["completion"] + " " + cites(step["sources"]), "", "[仪器操作附录](#instruments)", ""])

    for i, section in enumerate(content["sections"] + content["appendices"]):
        is_appendix = section in content["appendices"]
        if is_appendix and config.get("appendix_new_page", True):
            doc.add_page_break()
        heading = add_rich_text(doc.add_heading('',1),section["title"])
        bookmark(heading, "section_" + section["id"], 100+i)
        md.extend([f"<a id=\"{section['id']}\"></a>", "## " + section["title"], ""])
        preview.append("<h2 id='" + html.escape(section["id"], quote=True) + "'>" + html.escape(section["title"]) + "</h2>")
        for block in section["blocks"]:
            kind = block["type"]
            source_text = cites(block.get("sources", []))
            if kind == "paragraph":
                p = add_rich_text(doc.add_paragraph(),block["text"]); add_citations(p, block["sources"])
                md.extend([block["text"] + " " + source_text, ""])
                preview.append("<p>" + html.escape(block["text"] + " " + source_text) + "</p>")
            elif kind == "equation":
                p = doc.add_paragraph()
                p._p.append(math_element("oMathPara", formula_omml(block["latex"])))
                p.add_run("　(" + block["label"] + ")"); add_citations(p, block["sources"])
                add_rich_text(doc.add_paragraph(),"符号：" + block["symbols"])
                md.extend(["$$" + block["latex"] + "$$", "", "(" + block["label"] + ") " + block["symbols"] + " " + source_text, ""])
                preview.append("<div class='formula' data-latex='" + html.escape(block["latex"], quote=True) + "'>" + html.escape(block["latex"]) + "</div><p>" + html.escape(block["symbols"] + " " + source_text) + "</p>")
            elif kind == "table":
                p = add_rich_text(doc.add_paragraph(style="Caption"),block.get("caption", "表")); add_citations(p, block["sources"])
                render_table(doc, block["columns"], block["rows"],keep_header=config.get("keep_table_header_with_first_row",False),content_aware=config.get("content_aware_table_widths",False))
                md.extend([block.get("caption", "") + " " + source_text, "", markdown_table(block["columns"], block["rows"]), ""])
                preview.append("<p>" + html.escape(block.get("caption", "") + " " + source_text) + "</p><table><tr>" + "".join("<th>" + html.escape(str(c)) + "</th>" for c in block["columns"]) + "</tr>" + "".join("<tr>" + "".join("<td>" + html.escape(str(c)) + "</td>" for c in row) + "</tr>" for row in block["rows"]) + "</table>")
            elif kind == "figure":
                anchor = store.anchor(block["source_anchor"])
                page = store.one("SELECT image FROM pages WHERE source_id=? AND number=?", (anchor["source_id"], anchor["page"]))
                source_image = store.asset_path(page["image"])
                name = f"figure-{i}-{len(list(out.glob('figure-*.png')))+1}-{block['source_anchor'][-8:]}.png"
                from PIL import Image
                with Image.open(source_image) as image:
                    region = anchor.get("region")
                    if region:
                        image = image.crop(tuple(int(v * (image.width if n % 2 == 0 else image.height)) for n,v in enumerate(region)))
                    image.save(out / name)
                    width = min(16.8, 18 * image.width / max(1,image.height))
                doc.add_picture(str(out / name), width=Cm(width))
                p = add_rich_text(doc.add_paragraph(style="Caption"),block["caption"]); add_citations(p, block["sources"])
                md.extend(["![" + block["caption"] + "](" + name + ") " + source_text, ""])
                preview.append("<figure><img src='" + name + "' alt='" + html.escape(block["caption"], quote=True) + "'><figcaption>" + html.escape(block["caption"] + " " + source_text) + "</figcaption></figure>")
            elif kind == "code":
                p = doc.add_paragraph(); r=p.add_run(block["text"]); r.font.name="Consolas"; r.font.size=Pt(9)
                add_citations(p, block["sources"])
                md.extend(["```python", block["text"], "```", source_text, ""])
                preview.append("<pre>" + html.escape(block["text"]) + "</pre><p>" + source_text + "</p>")
            elif kind == "crossref":
                internal_link(doc.add_paragraph(), block["text"], "section_" + block["target"])
                md.extend(["[" + block["text"] + "](#" + block["target"] + ")", ""])
                preview.append("<a href='#" + html.escape(block["target"], quote=True) + "'>" + html.escape(block["text"]) + "</a>")
    doc.add_heading("来源与固定版本", 1)
    md.extend(["## 来源与固定版本", ""])
    source_index = []
    for anchor_id, number in source_numbers.items():
        anchor = store.anchor(anchor_id)
        source = {"number": number, **anchor}
        source_index.append(source)
        checked = {"verified":"已核对","unverified":"待核对","ocr_uncertain":"识别存疑"}.get(anchor['status'],anchor['status'])
        desc = f"[{number}] {anchor['name']}，第 {anchor['page']} 页（标签 {anchor['label']}），锚点 {anchor_id}。SHA-256 {anchor['source_sha'][:16]}…，完整摘要见 sources.json。{checked}。"
        if anchor["metadata"].get("url"):
            desc += " 网址：" + anchor["metadata"]["url"] + " 访问：" + anchor["metadata"].get("accessed", "未知")
        p = doc.add_paragraph(desc); bookmark(p, "source_" + str(number), 1000+number)
        p.paragraph_format.space_after = Pt(4)
        for run in p.runs:
            run.font.size = Pt(8)
        md.extend([desc, ""])
        preview.append("<p id='source_" + str(number) + "'>" + html.escape(desc) + "</p>")
    preview.append("</body></html>")
    doc.save(out / "document.docx")
    (out / "document.md").write_text("\n".join(md), encoding="utf-8")
    (out / "preview.html").write_text("\n".join(preview), encoding="utf-8")
    (out / "sources.json").write_text(json.dumps(source_index, ensure_ascii=False, indent=2), encoding="utf-8")


def markdown_table(headers, rows):
    def esc(x):
        return str(x).replace("|", "\\|").replace("\n", "<br>")
    return "\n".join(["| " + " | ".join(map(esc, headers)) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"] + ["| " + " | ".join(map(esc, row)) + " |" for row in rows])


def render_table(doc, headers, rows,keep_header=False,content_aware=False):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Shading Accent 1"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    # Fixed total width and explicit cell sizing prevent the six-column readout
    # table from extending beyond an A4 page in Word/WPS/LibreOffice.
    table.autofit = False
    widths=[16.8/len(headers)]*len(headers)
    if content_aware:
        import unicodedata
        def display_length(value):
            return max((sum(2 if unicodedata.east_asian_width(c) in ("W","F") else 1 for c in line) for line in str(value).splitlines()),default=0)
        weights=[max(1,*([display_length(headers[i])]+[display_length(row[i]) for row in rows]))**.5 for i in range(len(headers))]
        # Reserve legible numeric columns, then give prose the remaining space.
        minimum=min(.75,8/len(headers));remaining=16.8-minimum*len(headers)
        widths=[minimum+remaining*w/sum(weights) for w in weights]
    for column,width in zip(table.columns,widths):
        column.width = Cm(width)
    repeat = OxmlElement("w:tblHeader"); table.rows[0]._tr.get_or_add_trPr().append(repeat)
    for cell, value in zip(table.rows[0].cells, headers):
        cell.text = "";add_rich_text(cell.paragraphs[0],value)
    for row in rows:
        cells = table.add_row().cells
        for cell, value in zip(cells, row):
            cell.text = "";add_rich_text(cell.paragraphs[0],value)
    for row_index,row in enumerate(table.rows):
        cant_split = OxmlElement("w:cantSplit"); row._tr.get_or_add_trPr().append(cant_split)
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(3)
                if keep_header and row_index==0:paragraph.paragraph_format.keep_with_next=True
                for run in paragraph.runs:
                    run.font.size = Pt(9)
        if content_aware:
            for cell,width in zip(row.cells,widths):cell.width=Cm(width)
