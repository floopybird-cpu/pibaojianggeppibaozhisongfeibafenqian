"""Offline DOCX reading and PDF typesetting, with native Word equations retained.

The PDF is independently paginated from the same complete OOXML document. No
Office installation, browser, online converter or TeX executable is required.
"""
from __future__ import annotations

import html
import io
import re
import tempfile
import threading
import copy
from pathlib import Path
from xml.etree import ElementTree as ET

from docx import Document
from docx.oxml.ns import qn


RENDER_LOCK = threading.RLock()
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
CSS = """body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.5; color: #172631; }
p { margin: 0 0 7pt; white-space: normal; }
h1 { font-size: 21pt; color: black; margin: 0 0 12pt; }
h2 { font-size: 15pt; color: black; margin: 15pt 0 8pt; page-break-after: avoid; }
h3 { font-size: 12pt; color: black; margin: 10pt 0 6pt; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 5pt 0 11pt; font-size: 9pt; }
td, th { border: .5pt solid #d9d9d9; padding: 5pt; vertical-align: middle; }
th { background: #e9eef3; color: black; font-weight: bold; }
.caption { font-size: 9pt; margin: 7pt 0 4pt; page-break-after: avoid; }
.source { font-size: 8pt; color: #48515b; }
.figure { text-align: center; margin: 6pt 0; }
.break { page-break-before: always; }
a { color: #285e4c; text-decoration: none; }
"""


def local(element):
    return str(element.tag).split("}")[-1]


def omml_to_mathml(element):
    """Translate the mathematical OOXML structures rather than flattening text."""
    def node(name, children=(), text=None, **attributes):
        result = ET.Element(name, attributes)
        result.text = text
        result.extend(children)
        return result

    def child(value, name):
        return next((x for x in value if local(x) == name), None)

    def prop(value, name, default=""):
        for item in value.iter():
            if local(item) == name:
                return item.get("{" + M + "}val", default)
        return default

    def group(value):
        return node("mrow", [walk(x) for x in value] if value is not None else [])

    def walk(value):
        name = local(value)
        if name.endswith("Pr") or name in ("ctrlPr", "bookmarkStart", "bookmarkEnd"):
            return node("mrow")
        if name in ("oMath", "oMathPara", "e", "num", "den", "sub", "sup", "deg", "lim", "mr", "box", "phant"):
            return group(value)
        if name == "r":
            string = "".join(x.text or "" for x in value.iter() if local(x) == "t")
            # A Word math run may hold a full expression, rather than a single
            # identifier. Retain its operators and spacing without inventing math.
            return node("mtext" if prop(value, "nor") == "1" else "mi", text=string)
        if name == "t":
            return node("mi", text=value.text or "")
        if name == "f":
            return node("mfrac", [group(child(value, "num")), group(child(value, "den"))])
        if name in ("sSup", "sSub", "sSubSup"):
            names = ["e", "sub", "sup"] if name == "sSubSup" else ["e", "sup" if name == "sSup" else "sub"]
            return node({"sSup": "msup", "sSub": "msub", "sSubSup": "msubsup"}[name], [group(child(value, n)) for n in names])
        if name == "sPre":
            return node("mmultiscripts", [group(child(value, "e")), node("mprescripts"), group(child(value, "sub")), group(child(value, "sup"))])
        if name == "rad":
            degree = child(value, "deg")
            return node("mroot", [group(child(value, "e")), group(degree)]) if degree is not None and len(degree) else node("msqrt", [group(child(value, "e"))])
        if name in ("limLow", "limUpp"):
            return node("munder" if name == "limLow" else "mover", [group(child(value, "e")), group(child(value, "lim"))])
        if name in ("acc", "bar", "groupChr"):
            char = prop(value, "chr", "¯" if name == "bar" else "ˆ")
            return node("munder" if prop(value,"pos","top")=="bot" else "mover", [group(child(value, "e")), node("mo", text=char)], accent="true")
        if name == "d":
            return node("mfenced", [group(x) for x in value if local(x) == "e"], open=prop(value, "begChr", "("), close=prop(value, "endChr", ")"))
        if name == "m":
            return node("mtable", [node("mtr", [node("mtd", [group(cell)]) for cell in row if local(cell) == "e"]) for row in value if local(row) == "mr"])
        if name == "eqArr":
            return node("mtable", [node("mtr", [node("mtd", [group(row)])]) for row in value if local(row) == "e"])
        if name == "nary":
            operator = node("mo", text=prop(value, "chr", "∫"))
            lower, upper = child(value, "sub"), child(value, "sup")
            if lower is not None and len(lower) and upper is not None and len(upper):
                operator = node("munderover", [operator, group(lower), group(upper)])
            elif lower is not None and len(lower):
                operator = node("munder", [operator, group(lower)])
            elif upper is not None and len(upper):
                operator = node("mover", [operator, group(upper)])
            return node("mrow", [operator, group(child(value, "e"))])
        if name == "func":
            return node("mrow", [group(child(value, "fName")), group(child(value, "e"))])
        if name == "fName":
            return group(value)
        if name == "borderBox":
            return node("menclose", [group(child(value, "e"))], notation="box")
        raise ValueError("暂不能排版 Word 公式结构 " + name + "，请保留原 Word 核查；未当成普通文本丢弃")

    return ET.tostring(node("math", [walk(element)], xmlns="http://www.w3.org/1998/Math/MathML"), encoding="unicode")


class RichReader:
    def __init__(self, source, work):
        self.document = Document(io.BytesIO(source) if isinstance(source, bytes) else str(source))
        self.work = Path(work)
        self.serial = 0
        self.equations = 0
        self.images = 0
        self.tables = 0

    def formula(self, value, latex=False):
        import ziamath
        import pymupdf
        # MuPDF supports SVG 1.1 paths. SVG 2 symbol/use glyph reuse can appear
        # as blank characters, while fractions still look superficially valid.
        ziamath.config.svg2=False
        self.serial += 1
        if latex:
            from latex2mathml.converter import convert
            mathml=convert(value)
        else:mathml=omml_to_mathml(value)
        from .math_fonts import math_object
        math = math_object(mathml,13)
        if math.getsize()[0]>470:
            root=ET.fromstring(mathml)
            def flatten(element):
                if local(element) in ("math","mrow"):
                    return [item for child in element for item in flatten(child)]
                return [copy.deepcopy(element)]
            elements=flatten(root)
            def row(nodes):
                result=ET.Element("mrow");result.extend(copy.deepcopy(nodes));return result
            def width(nodes):
                m=ET.Element("math");m.append(row(nodes));return math_object(m,13).getsize()[0]
            rows=[];start=0
            while start<len(elements):
                end=start;split=None
                while end<len(elements):
                    token="".join(elements[end].itertext()).strip()
                    if end>start and token in ("=","≡","≈",",",";","，","；"):split=end
                    if width(elements[start:end+1])>465 and end>start:break
                    end+=1
                if end==len(elements):rows.append(row(elements[start:]));break
                stop=split if split is not None and split>start else end
                rows.append(row(elements[start:stop]));start=stop
            if len(rows)>1:
                wrapped=ET.Element("math");stack=ET.SubElement(wrapped,"mtable",{"columnalign":"left","rowspacing":"0.4em"})
                for part in rows:ET.SubElement(ET.SubElement(stack,"mtr"),"mtd").append(part)
                math=math_object(wrapped,13)
        svg = math.svg().encode("utf-8")
        # Validate the SVG with the same engine used for PDF output; paths are
        # independent of fonts on the recipient's computer.
        with pymupdf.open(stream=svg, filetype="svg") as image:
            width, height = image[0].rect.width, image[0].rect.height
        scale = min(1, 470 / max(1, width), 650 / max(1, height))
        name = f"equation-{self.serial}.svg"
        (self.work / name).write_bytes(svg)
        self.equations += 1
        return f'<img src="{name}" width="{width * scale:.3f}" height="{height * scale:.3f}">'

    def text(self, value):
        # Inline LaTeX is rendered wherever it occurs, including table cells.
        pattern = r"(\$\$.+?\$\$|\\\[.+?\\\]|\\\(.+?\\\)|(?<!\\)\$[^$\n]+?(?<!\\)\$)"
        result = []
        for part in re.split(pattern, value or "", flags=re.S):
            if not part:
                continue
            if (part.startswith("$$") and part.endswith("$$")) or part.startswith(("\\[", "\\(")):
                result.append(self.formula(part[2:-2], latex=True))
            elif part.startswith("$") and part.endswith("$"):
                result.append(self.formula(part[1:-1], latex=True))
            else:
                result.append(html.escape(part).replace("\n", "<br>"))
        return "".join(result)

    def inline(self, element):
        name = local(element)
        if name in ("footnoteReference", "endnoteReference", "altChunk", "object", "pict"):
            raise ValueError("此 Word 含尚未支持的附注或嵌入对象（"+name+"），请提供其 PDF 阅读版；原 Word 保留，未静默省略")
        if element.tag == qn("m:oMath"):
            return self.formula(element)
        if name == "t":
            return self.text(element.text)
        if name == "tab":
            return "　"
        if name in ("br", "cr"):
            return '<br>' if element.get(qn("w:type")) != "page" else '<p class="break"></p>'
        if name == "blip":
            identity = element.get(qn("r:embed"))
            if not identity:
                raise ValueError("Word 含外部链接图片，请先将图片嵌入 Word；不会联网替换原图")
            part = self.document.part.related_parts[identity]
            from PIL import Image
            with Image.open(io.BytesIO(part.blob)) as image:
                width, height = image.size
                self.serial += 1
                filename = f"image-{self.serial}.png"
                image.convert("RGB").save(self.work / filename)
            self.images += 1
            # Respect both page axes; figures must never be taller than a page.
            extent = next((a.find(qn('wp:extent')) for a in element.iterancestors() if local(a) in ('inline','anchor')),None)
            if extent is not None:
                shown_width=float(extent.get('cx'))/12700;shown_height=float(extent.get('cy'))/12700
            else:
                scale = min(1, 470 / width, 620 / height);shown_width,shown_height=width*scale,height*scale
            return f'<img src="{filename}" width="{shown_width:.3f}" height="{shown_height:.3f}" style="max-width:100%">'
        if name in ("pPr", "rPr", "bookmarkEnd", "instrText", "del", "delText"):
            return ""
        if name == "bookmarkStart":
            return '<a id="' + html.escape(element.get(qn("w:name"), ""), quote=True) + '"></a>'
        content = "".join(self.inline(x) for x in element)
        if name == "hyperlink" and element.get(qn("w:anchor")):
            return '<a href="#' + html.escape(element.get(qn("w:anchor")), quote=True) + '">' + content + '</a>'
        if name == 'hyperlink' and element.get(qn('r:id')):
            relation=self.document.part.rels.get(element.get(qn('r:id')))
            target=str(relation.target_ref) if relation else ''
            if target.startswith(('https://','http://','mailto:')):
                return '<a href="'+html.escape(target,quote=True)+'">'+content+'</a>'
        if name == "r":
            props = element.find(qn("w:rPr"))
            if props is not None:
                for tag, wrapped in (("b", "b"), ("i", "i")):
                    item = props.find(qn("w:" + tag))
                    if item is not None and item.get(qn("w:val"), "1") not in ("0", "false"):
                        content = f'<{wrapped}>' + content + f'</{wrapped}>'
                alignment = props.find(qn("w:vertAlign"))
                if alignment is not None:
                    tag = "sup" if alignment.get(qn("w:val")) == "superscript" else "sub"
                    content = f'<{tag}>' + content + f'</{tag}>'
        return content

    def paragraph(self, element):
        props = element.find(qn("w:pPr"))
        style = props.find(qn("w:pStyle")) if props is not None else None
        name = style.get(qn("w:val"), "") if style is not None else ""
        tag = {"Title": "h1", "Heading1": "h2", "Heading2": "h3", "Heading3": "h3"}.get(name, "p")
        classes = []
        if name == "Caption":
            classes.append("caption")
        if name == 'NoSpacing':classes.append('code')
        if element.find(".//" + qn("a:blip")) is not None:
            classes.append("figure")
        styles=[]
        if props is not None:
            align=props.find(qn('w:jc'))
            if align is not None and align.get(qn('w:val')) in ('left','center','right','both'):
                styles.append('text-align:'+{'both':'justify'}.get(align.get(qn('w:val')),align.get(qn('w:val'))))
            before=props.find(qn('w:pageBreakBefore'))
            if before is not None and before.get(qn('w:val'),'1') not in ('0','false'):styles.append('page-break-before:always')
            indent=props.find(qn('w:ind'))
            if indent is not None and indent.get(qn('w:left')):styles.append('margin-left:'+str(float(indent.get(qn('w:left')))/20)+'pt')
        attr = (' class="' + " ".join(classes) + '"' if classes else '')+(' style="'+';'.join(styles)+'"' if styles else '')
        return f'<{tag}{attr}>' + self.inline(element) + f'</{tag}>'

    def blocks(self, parent):
        result = []
        for element in parent:
            name = local(element)
            if name == "p":
                result.append(self.paragraph(element))
            elif name == "tbl":
                if element.find(".//"+qn("w:gridSpan")) is not None or element.find(".//"+qn("w:vMerge")) is not None:
                    raise ValueError("此 Word 含合并单元格，请提供其 PDF 阅读版以保留准确表格；原 Word 保留，未将合并表错排")
                self.tables += 1
                caption=element.find('./'+qn('w:tblPr')+'/'+qn('w:tblCaption'))
                layout=caption is not None and caption.get(qn('w:val'))=='physics-layout-columns'
                result.append('<table class="columns">' if layout else '<table>')
                grid=element.find(qn('w:tblGrid'))
                widths=[float(c.get(qn('w:w'),'1')) for c in grid] if grid is not None else []
                for row_index, row in enumerate(x for x in element if local(x) == "tr"):
                    result.append("<tr>")
                    props = row.find(qn("w:trPr"))
                    header = props is not None and props.find(qn("w:tblHeader")) is not None
                    cell_tag = "th" if header else "td"
                    for ci,cell in enumerate(x for x in row if local(x) == "tc"):
                        width=f' style="width:{100*widths[ci]/sum(widths):.5f}%"' if ci<len(widths) and sum(widths)>0 else ''
                        result.extend([f'<{cell_tag}{width}>', self.blocks(cell), f'</{cell_tag}>'])
                    result.append("</tr>")
                result.append("</table>")
            elif name in ("sdt", "sdtContent", "customXml"):
                result.append(self.blocks(element))
            elif name not in ("sectPr", "tblPr", "tblGrid", "tcPr", "bookmarkStart", "bookmarkEnd", "proofErr", "permStart", "permEnd"):
                raise ValueError("此 Word 正文结构尚未支持（"+name+"），请提供 PDF 阅读版；未静默省略")
        return "".join(result)


def docx_to_pdf(source, destination, config=None):
    if (config or {}).get('renderer')=='typst':
        from .typst_pdf import docx_to_typst_pdf
        return docx_to_typst_pdf(source,destination,config)
    import pymupdf
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    with RENDER_LOCK, tempfile.TemporaryDirectory(prefix=".physics-document-", dir=Path(destination).parent) as temporary:
        reader = RichReader(source, temporary)
        body = reader.blocks(reader.document.element.body)
        story = pymupdf.Story("<html><body>" + body + "</body></html>", user_css=CSS + (config or {}).get("css", ""), archive=pymupdf.Archive(temporary))
        config=config or {}
        page_cm=config.get('page_cm')
        page=pymupdf.Rect(0,0,page_cm[0]*72/2.54,page_cm[1]*72/2.54) if page_cm else pymupdf.paper_rect("a4")
        margins=config.get('margins_cm')
        area=page+(margins['left']*72/2.54,margins['top']*72/2.54,-margins['right']*72/2.54,-margins['bottom']*72/2.54) if margins else page + (56, 52, -56, -52)
        positions = []
        def rectangle(number, filled):
            # Each story page is complete; never discard remaining content.
            return page, area, None
        document = story.write_with_links(rectangle, positionfn=lambda p: positions.append(p))
        if not len(document):
            raise ValueError("Word 没有可排版的正文")
        document.set_metadata({"title": reader.document.core_properties.title or "物理实验讲义", "author": "物理实验助手", "creator": "Physics Harness offline rich-document renderer"})
        document.subset_fonts()
        document.save(str(destination), garbage=4, deflate=True)
        count = len(document)
        document.close()
        return {"pages": count, "equations": reader.equations, "images": reader.images, "tables": reader.tables, "pagination": "独立阅读排版，原 Word 字节保留"}
