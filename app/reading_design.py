"""Typesetting policies shared by the editable and fixed reading editions."""
import re
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, Cm
from docx.enum.text import WD_TAB_ALIGNMENT


def page_ranges(numbers):
    groups=[]
    for page in sorted(set(numbers)):
        if groups and groups[-1][-1]+1==page:groups[-1].append(page)
        else:groups.append([page])
    return "、".join(str(g[0]) if len(g)==1 else f"{g[0]}–{g[-1]}" for g in groups)


def step_citations(steps, citations):
    """Keep independent page locators, sharing one for adjacent steps on the same pages."""
    visible = [[] for step in steps]
    previous = None
    for i, step in enumerate(steps):
        ids = step['sources']
        pages = {(citations.numbers[a], citations.anchors[a]['page']) for a in ids}
        if i and pages == previous:
            ids = list(dict.fromkeys(visible[i - 1] + ids))
            visible[i - 1] = []
        visible[i] = ids
        previous = pages
    return visible


def citation_groups(blocks,citations):
    """Adjacent narrative sharing a source gets one locator at its last claim."""
    visible=[[] for _ in blocks]; pending=[]; last=None; owners=set()
    for i,block in enumerate(blocks):
        ids=block.get("sources",[])
        mode=block.get("citation","auto")
        narrative=block["type"] in ("paragraph","markdown") and mode=="auto" and bool(ids)
        current={citations.numbers[a] for a in ids}
        if not narrative or current!=owners:
            if last is not None:visible[last]=list(dict.fromkeys(pending))
            pending=[];last=None
        if narrative:
            pending.extend(ids);last=i;owners=current
        elif mode!="bibliography" or block["type"]=="figure":
            visible[i]=ids
    if last is not None:visible[last]=list(dict.fromkeys(pending))
    return visible


# Only unambiguous single-symbol scientific subscripts in prose. Verbatim
# code, paths, email, URLs, compound identifiers and filenames stay verbatim.
SUBSCRIPT=re.compile(r"(?<![A-Za-z0-9_.\\@-])([A-Za-zα-ωΑ-Ω])_(?:\{([A-Za-z0-9]+)\}|([A-Za-z0-9]+))(?![A-Za-z0-9_.\\@-])")
LITERAL=re.compile(r"(?:https?://|[A-Za-z]:[\\/]|(?<!\S)[\\/])\S+|[\w.-]+@[\w.-]+|[A-Za-z0-9_.-]+\.[A-Za-z]{2,5}\b")
JOINED_SUBSCRIPT=re.compile(SUBSCRIPT.pattern.replace(r'(?<![A-Za-z0-9_.\\@-])',r'(?:(?<![A-Za-z0-9_.\\@-])|(?<=[A-Za-z]-))',1))

def prose_pieces(value,joined=False):
    pos=0;protected=[m.span() for m in LITERAL.finditer(value)]
    for m in (JOINED_SUBSCRIPT if joined else SUBSCRIPT).finditer(value):
        if any(start<=m.start()<end for start,end in protected):continue
        if m.start()>pos:yield False,value[pos:m.start()]
        sub=m.group(2) or m.group(3)
        yield True,m.group(1)+"_{"+(r"\mathrm{"+sub+"}" if len(sub)>1 and sub.isalpha() else sub)+"}"
        pos=m.end()
    if pos<len(value):yield False,value[pos:]


def write_fonts(props,latin,cjk,lang):
    fonts=props.find(qn("w:rFonts"))
    if fonts is None:fonts=OxmlElement("w:rFonts");props.insert(0,fonts)
    for key in list(fonts.attrib):
        if "theme" in key.lower():del fonts.attrib[key]
    for slot,family in (("ascii",latin),("hAnsi",latin),("eastAsia",cjk),("cs",latin)):
        fonts.set(qn("w:"+slot),family)
    language=props.find(qn("w:lang"))
    if language is None:language=OxmlElement("w:lang");props.append(language)
    language.set(qn("w:eastAsia"),lang)


def style_document(doc,d):
    settings=doc.settings.element
    for language in settings.findall(qn("w:themeFontLang")):language.set(qn("w:eastAsia"),d["language"])
    for font in settings.iter(qn("m:mathFont")):font.set(qn("m:val"),d["math_font"])
    for props in doc.styles.element.xpath("./w:docDefaults/w:rPrDefault/w:rPr"):
        write_fonts(props,d["latin_font"],d["font"],d["language"])
    for name in ("Normal","Title","Heading 1","Heading 2","Heading 3","Caption","Source"):
        style=doc.styles[name];heading=name=="Title" or name.startswith("Heading")
        write_fonts(style._element.get_or_add_rPr(),d["heading_font"] if heading else d["latin_font"],
                    d["heading_font"] if heading else d["font"],d["language"])
        style.font.bold=heading;style.font.italic=False
        style.paragraph_format.widow_control=True
        if heading:style.paragraph_format.keep_with_next=True
    display=doc.styles.add_style("Display Formula",1);display.base_style=doc.styles["Normal"]
    display.paragraph_format.keep_together=True
    display.paragraph_format.space_before=Pt(3)
    doc.styles["Caption"].paragraph_format.keep_together=True


def title_lines(title,design):
    """Use a semantic break in an overlong title when it has a subtitle."""
    import unicodedata
    # Chinese glyphs are approximately one em; a conservative Latin estimate
    # only decides whether to offer a break. The two renderers shape the text.
    width=sum(1 if unicodedata.east_asian_width(c) in ('W','F') else .56 for c in title)
    if width*design['title_size']<=design['width_cm']*72/2.54:return [title]
    for sep in ('：','——',' — ',': '):
        first,found,second=title.partition(sep)
        if found and first.strip() and second.strip():return [first+sep.rstrip(),second.strip()]
    return [title]


def equation_label(block):
    match=re.fullmatch(r"(?:公式|式)?\s*[（(]?\s*(\d+[a-z]?)\s*[)）]?",block.get("label","").strip())
    return match[1] if match else None


def display_equation(composer,parent,block,width,sources,*,numbered=True):
    from .documents import formula_omml
    label=block.get("label","").strip()
    match=re.fullmatch(r"(?:公式|式)?\s*[（(]?\s*(\d+[a-z]?)\s*[)）]?",label)
    number=""
    if numbered:
        composer.equation_number+=1
        number=match[1] if match else str(composer.equation_number)
        if composer.config.get('structured_plan_edition',0)>=2:
            if not match or number in composer.printed_labels:
                while str(composer.equation_number) in composer.reserved_labels | composer.printed_labels:
                    composer.equation_number+=1
                number=str(composer.equation_number)
            composer.printed_labels.add(number)
    para=parent.add_paragraph(style="Display Formula")
    para.paragraph_format.tab_stops.add_tab_stop(Cm(width/2),WD_TAB_ALIGNMENT.CENTER)
    para.paragraph_format.tab_stops.add_tab_stop(Cm(width),WD_TAB_ALIGNMENT.RIGHT)
    para.add_run("\t");para._p.append(formula_omml(block["latex"]))
    if number:para.add_run("\t("+number+")")
    if number and composer.config.get('structured_plan_edition',0)>=2:
        from .documents import bookmark
        bookmark(para,'equation_'+number,20000+composer.equation_number)
    prose=block.get("symbols","").strip()
    if label and not match:
        desc=re.sub(r"^(?:公式|式)?\s*[（(]\d+[a-z]?[)）]\s*[：:、—-]?\s*","",label)
        if desc and desc not in prose:prose=desc+("。"+prose if prose else "")
    anchor=para
    if prose:
        para.paragraph_format.keep_with_next=True
        anchor=composer.rich(parent.add_paragraph(),prose)
    composer.cite(anchor,sources)
    return number,prose


def finish_flow(doc,d):
    """Use actual Word image extents to keep short adjacent panels together."""
    previous_heading=True
    body=doc.element.body
    from docx.text.paragraph import Paragraph
    for element in body:
        if element.tag!=qn("w:p"):
            previous_heading=False
            continue
        para=Paragraph(element,doc);name=para.style.name
        if (name=="Normal" and not previous_heading and para.text.strip() and
            not element.xpath(".//w:drawing|.//w:tab|.//w:br|./w:pPr/w:ind") and
            not re.match(r"^\s*(?:[•●▪]|[0-9]+[.、)]|[（(][0-9]+[)）])",para.text)):
            para.paragraph_format.first_line_indent=Pt(d["font_size"]*d["first_line_chars"])
        previous_heading=name=="Title" or name.startswith("Heading")
    area=Cm(d["page_cm"][1]-d["margins_cm"]["top"]-d["margins_cm"]["bottom"])
    paras=doc.paragraphs
    for i in range(len(paras)-3):
        a,cap,b,cap2=paras[i:i+4]
        if cap.style.name!="Caption" or cap2.style.name!="Caption" or a._p.getnext() is not cap._p or cap._p.getnext() is not b._p:continue
        x=a._p.xpath(".//wp:extent");y=b._p.xpath(".//wp:extent")
        if x and y and int(x[0].get("cy"))+int(y[0].get("cy"))+Pt(90)<area:
            cap.paragraph_format.keep_with_next=True
