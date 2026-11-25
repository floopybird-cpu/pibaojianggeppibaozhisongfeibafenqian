"""Text composition shared by data-only document packages.

Markdown supplies content structure; package resources supply typography and
flow. No package Python, HTML, CSS imports, file URLs or network reads execute.
"""
from __future__ import annotations

import copy
import html
import json
import math
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.texmath import texmath_plugin

from .documents import formula_omml, math_element, bookmark, internal_link, markdown_table


def parser():
    md=MarkdownIt('commonmark', {'html': False}).enable(['table', 'strikethrough']).use(dollarmath_plugin).use(texmath_plugin,delimiters='brackets')
    # CommonMark flanking leaves common mixed prose literal, e.g.
    # **一句话。**CSV or **效率：**η. Work only on literal text tokens;
    # escaped stars, code, and math remain separate tokens and untouched.
    def cjk_emphasis(state):
        pattern=re.compile(r'\*\*(?=\S)([^*\n]*?\S)\*\*(?=\S|$)')
        for block in state.tokens:
            if block.type!='inline' or not block.children:continue
            result=[];pending=[]
            def flush():
                if not pending:return
                value=''.join(t.content for t in pending);position=0
                for match in pattern.finditer(value):
                    if match.start()>position:
                        t=Token('text','',0);t.content=value[position:match.start()];result.append(t)
                    result.append(Token('strong_open','strong',1));t=Token('text','',0);t.content=match.group(1);result.append(t);result.append(Token('strong_close','strong',-1));position=match.end()
                if position<len(value):t=Token('text','',0);t.content=value[position:];result.append(t)
                pending.clear()
            for token in block.children:
                if token.type=='text':pending.append(token)
                else:flush();result.append(token)
            flush();block.children=result
    md.core.ruler.before('text_join','cjk_emphasis',cjk_emphasis)
    from .scientific_markup import install
    install(md)
    return md


def nodes(text):
    return SyntaxTreeNode(parser().parse(text)).children


def walk_nodes(items):
    for item in items:
        yield item
        yield from walk_nodes(item.children or [])


def block_locations(content):
    """Include nested columns. Malformed containers go to the package schema validator."""
    def descend(blocks, parent):
        if not isinstance(blocks,list):return
        for i,block in enumerate(blocks):
            if not isinstance(block,dict):continue
            path=f'{parent}/{i}'
            yield path,block
            if block.get('type')=='columns' and isinstance(block.get('columns'),list):
                for j,column in enumerate(block['columns']):
                    if isinstance(column,dict):yield from descend(column.get('blocks'),f'{path}/columns/{j}/blocks')
    for group in ('sections','appendices'):
        if isinstance(content.get(group),list):
            for i,section in enumerate(content[group]):
                if isinstance(section,dict):yield from descend(section.get('blocks'),f'/{group}/{i}/blocks')


def all_blocks(content):
    return (block for _,block in block_locations(content))


def missing_evidence(content, schema):
    variants=schema['properties']['sections']['items']['properties']['blocks']['items'].get('oneOf',[])
    types={v['properties']['type']['const'] for v in variants if 'evidence' in v['properties']
           and v['properties'].get('sources',{}).get('minItems',0)==0}
    types.difference_update(('worked_example','branch_table','space','pagebreak','columns'))
    paths=[]
    for path,block in block_locations(content):
        if isinstance(block.get('type'),str) and block['type'] in types and block.get('sources')==[] and block.get('evidence') not in ('derivation','example','explanation'):
            paths.append(path+'/evidence')
    if not paths:return []
    return [{'code':'evidence_required','message':'无直接出处的内容块须说明evidence为derivation/example/explanation，并在正文讲清依据；不能伪引教材','paths':paths}]


def load_design(content, template_path):
    template_path = Path(template_path)
    config = json.loads(template_path.read_text(encoding='utf-8'))
    selected = content.get('style', {}).get('theme') or config['defaults'][content['doc_type']]
    if selected not in config['themes']:
        raise ValueError('文稿主题不存在：' + str(selected) + '；可用：' + '、'.join(config['themes']))
    resource = (template_path.parent / config['themes'][selected]).resolve()
    if not resource.is_relative_to(template_path.parent.resolve()):
        raise ValueError('主题资源越出格式包')
    design = json.loads(resource.read_text(encoding='utf-8'))
    overrides = copy.deepcopy(content.get('style', {}))
    overrides.pop('theme', None)
    for key, value in overrides.items():
        if key == 'margins_cm':
            design[key] = {**design[key], **value}
        else:
            design[key] = value
    design['theme'] = selected
    if config.get('keep_table_rows'):design['keep_table_rows']=True
    if config.get('reading_edition')==2:design['reading_edition']=2
    design.setdefault('source_font_size',8);design.setdefault('source_spacing',2)
    design.setdefault('table_padding_horizontal_pt',4)
    design.setdefault('table_padding_vertical_pt',design['table_padding_pt'])
    design.setdefault('heading_space_before_pt',14);design.setdefault('heading_space_after_pt',7);design.setdefault('title_space_after_pt',13)
    for key in ('font_size', 'line_spacing', 'paragraph_spacing', 'title_size', 'heading_size', 'subheading_size', 'table_font_size', 'table_padding_pt','source_font_size','source_spacing','table_padding_horizontal_pt','table_padding_vertical_pt'):
        if not isinstance(design[key], (int, float)) or not math.isfinite(design[key]) or design[key] < 0 or (key not in ('paragraph_spacing','table_padding_pt','source_spacing','table_padding_horizontal_pt','table_padding_vertical_pt') and design[key] == 0):
            raise ValueError('排版参数必须为有限正数：' + key)
    for key in ('heading_space_before_pt','heading_space_after_pt','title_space_after_pt'):
        if not isinstance(design[key],(int,float)) or not math.isfinite(design[key]) or design[key]<0:raise ValueError('排版间距须为有限非负数：'+key)
    if any(not math.isfinite(v) or v < 0 for v in design['margins_cm'].values()):
        raise ValueError('页边距必须为有限非负数')
    size = {'A4': (21., 29.7), 'Letter': (21.59, 27.94)}[design['page']]
    if design['orientation'] == 'landscape':size = tuple(reversed(size))
    margins = design['margins_cm']
    if size[0] <= margins['left'] + margins['right'] or size[1] <= margins['top'] + margins['bottom']:
        raise ValueError('页边距没有留下正文空间')
    design['page_cm'] = size
    design['width_cm'] = size[0] - margins['left'] - margins['right']
    for key in ('accent', 'table_header', 'table_header_text', 'table_stripe'):
        if not re.fullmatch('[0-9a-fA-F]{6}', design[key]):raise ValueError('主题颜色须为六位色值：' + key)
    return config, design


def pdf_design(content, template_path):
    config, d = load_design(content, template_path)
    css = f'''body {{ font-size: {d['font_size']}pt; line-height: {d['line_spacing']}; color: #202830; }}
    p {{ margin-bottom: {d['paragraph_spacing']}pt; }}
    h1 {{ font-size: {d['title_size']}pt; color: black; }}
    h2 {{ font-size: {d['heading_size']}pt; color: black; }}
    h3 {{ font-size: {d['subheading_size']}pt; color: black; }}
    table {{ font-size: {d['table_font_size']}pt; }}
    td, th {{ padding: {d['table_padding_pt']}pt; }}
    th {{ background: #{d['table_header']}; color: #{d['table_header_text']}; }}
    .caption, .source {{ font-size: {max(8, d['font_size']-1.5)}pt; }}
    a {{ color: #{d['accent']}; }}
    .columns {{ border: 0; margin: 4pt 0 8pt; }}
    .columns > tr > td {{ border: 0; padding: 4pt 9pt 4pt 0; vertical-align: top; }}
    .code {{ font-family: monospace; white-space: pre-wrap; font-size: 9pt; }}
    .quote {{ margin-left: 14pt; color: #48515b; }}
    .list {{ margin-left: 14pt; }}'''
    return {'renderer':config.get('pdf',{}).get('renderer','typst'),'design':d,'css': css + config.get('pdf', {}).get('css', ''), 'page_cm': d['page_cm'], 'margins_cm': d['margins_cm']}


def validate_composition(content, snapshot, pack_path, block_schema):
    from .format_program import check_schema
    anchors = set(content['sources'])
    ids = {s['id'] for s in content['sections'] + content['appendices']}
    config,_=load_design(content, pack_path)
    if content['doc_type']!='record_sheet' and not content['summary'].strip():raise ValueError('预习、讲义和完整实验单需保留学习主线')
    for block in all_blocks(content):
        check_schema(block, block_schema, snapshot, '文稿块')
        kind = block['type']
        if kind in ('worked_example','branch_table'):
            from .observation_paths import example_rows,branch_rows
            (example_rows if kind=='worked_example' else branch_rows)(block,snapshot,content.get('draft_ref'))
        if kind=='measurement_table':
            from .measurement_tables import measurement_table
            block=measurement_table(block,snapshot);kind='table'
        if not set(block.get('sources', [])) <= anchors:raise ValueError('块来源未列入总清单')
        if 'sources' in block and not block['sources'] and block['type'] not in ('columns','space','pagebreak','worked_example','branch_table'):
            if block.get('evidence') not in ('derivation','example','explanation'):
                raise ValueError('无直接出处的内容块须说明evidence为derivation/example/explanation，并在正文讲清依据；不能伪引教材')
        if kind == 'columns':
            weights = block.get('weights', [1] * len(block['columns']))
            if len(weights) != len(block['columns']) or any(not math.isfinite(w) or w <= 0 for w in weights):raise ValueError('分栏权重须与栏数对应且为有限正数')
        if kind == 'crossref' and block['target'] not in ids:raise ValueError('交叉引用目标不存在')
        if kind == 'table':
            if any(len(r) != len(block['columns']) for r in block['rows']):raise ValueError('表格列数不一致')
            weights=block.get('column_weights')
            if weights and (len(weights)!=len(block['columns']) or any(not math.isfinite(w) or w<=0 for w in weights)):raise ValueError('表格列宽权重无效')
        if kind == 'equation':
            formula_omml(block['latex'])
            if block.get('formula_ref') and snapshot.object(**{'identity':block['formula_ref']['id'],'revision':block['formula_ref']['revision']})['content']['latex'] != block['latex']:
                raise ValueError('显示公式与固定引用不一致')
        if kind == 'markdown':
            for node in walk_nodes(nodes(block['text'])):
                if node.type in ('math_inline', 'math_block', 'math_block_label','math_block_eqno'):formula_omml(node.content)
                if node.type == 'image':
                    src=node.attrs.get('src', '')
                    if not src.startswith('source:') or src[7:] not in set(block['sources']):raise ValueError('Markdown 图片用 source:来源锚点，且列入本块来源；不读取任意路径或外网图片')
                    require_image(snapshot, src[7:])
                if node.type == 'link':
                    target=node.attrs.get('href', '')
                    if target.startswith('#') and target[1:] not in ids:raise ValueError('Markdown 内链目标不存在')
                    if not target.startswith(('#', 'https://', 'http://', 'mailto:')):raise ValueError('文稿链接须为章节或公开网址')
        if kind == 'figure':
            if block['source_anchor'] not in set(block['sources']):raise ValueError('图片实际来源须列入该图旁侧来源')
            require_image(snapshot, block['source_anchor'])


def require_image(snapshot, identity):
    anchor=snapshot.anchor(identity)
    page=snapshot.one('SELECT image FROM pages WHERE source_id=? AND number=?',(anchor['source_id'],anchor['page']))
    if not page or not page['image']:raise ValueError('图片来源没有原页图')


class Composer:
    def __init__(self, content, out, snapshot, template_path):
        self.content,self.out,self.snapshot=content,Path(out),snapshot
        self.config,self.d=load_design(content,template_path)
        self.doc=Document();self.doc.core_properties.title=content['title'];self.doc.core_properties.author='物理实验助手'
        self.doc.core_properties.language=self.d['language']
        sec=self.doc.sections[0];sec.page_width,sec.page_height=map(Cm,self.d['page_cm'])
        for grid in sec._sectPr.findall(qn('w:docGrid')):sec._sectPr.remove(grid)
        for name,value in self.d['margins_cm'].items():setattr(sec,name+'_margin',Cm(value))
        for border in self.doc.styles.element.xpath('.//w:pBdr'):border.getparent().remove(border)
        for name,size in [('Normal',self.d['font_size']),('Title',self.d['title_size']),('Heading 1',self.d['heading_size']),('Heading 2',self.d['subheading_size']),('Heading 3',self.d['font_size']),('Caption',self.d['font_size']-1.5)]:
            style=self.doc.styles[name];style.font.name=self.d['font'];style.font.size=Pt(size);style.font.color.rgb=RGBColor(0,0,0)
            style._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),self.d['font'])
            style.paragraph_format.space_before=Pt(self.d['heading_space_before_pt'] if name.startswith('Heading') else 0)
            style.paragraph_format.space_after=Pt(self.d['title_space_after_pt'] if name=='Title' else self.d['heading_space_after_pt'] if name.startswith('Heading') else self.d['paragraph_spacing'])
            style.paragraph_format.line_spacing=Pt(size*self.d['line_spacing']);style.paragraph_format.line_spacing_rule=WD_LINE_SPACING.AT_LEAST
            snap=OxmlElement('w:snapToGrid');snap.set(qn('w:val'),'0');style._element.get_or_add_pPr().append(snap)
        source_style=self.doc.styles.add_style('Source',1);source_style.base_style=self.doc.styles['Caption'];source_style.font.size=Pt(self.d.get('source_font_size',8));source_style.paragraph_format.space_after=Pt(self.d.get('source_spacing',2))
        source_style.paragraph_format.line_spacing=Pt(self.d['source_font_size']*self.d['line_spacing']);source_style.paragraph_format.line_spacing_rule=WD_LINE_SPACING.AT_LEAST
        from .source_citations import Citations
        self.citations=Citations(content['sources'],snapshot);self.numbers=self.citations.numbers
        self.reading=self.config.get('reading_edition')==2;self.equation_number=0
        if self.reading:
            from .reading_design import style_document
            style_document(self.doc,self.d)
        self.serial=0;self.md=[];self.source_index=[];self.export_images={};self.shown_formulas={}
        self.printed_labels=set();self.reserved_labels=set();self.body_formulas={};self.goal_formulas={}
        if self.config.get('structured_plan_edition',0)>=2:
            from collections import Counter
            from .reading_design import equation_label
            numbered=[(b,equation_label(b)) for b in all_blocks(content) if b.get('type')=='equation']
            counts=Counter(label for _,label in numbered if label)
            self.reserved_labels=set(counts)
            for block,label in numbered:
                if label and counts[label]==1:
                    self.body_formulas.setdefault(block['latex'].strip(),label)
        from .reading_design import title_lines
        self.section_depth=1
        title=self.doc.add_paragraph(style='Title')
        for i,line in enumerate(title_lines(content['title'],self.d) if self.config.get('balance_title') else [content['title']]):
            if i:title.add_run().add_break()
            self.rich(title,line)
        self.md+=['# '+content['title'],'']
        if content['simulation']:self.doc.add_paragraph('模拟教学示例　不是真实实测结论');self.md+=['> 模拟教学示例，不是真实实测结论。','']

    def cite(self, p, sources):
        if not sources:return
        if self.reading:
            from .reading_design import page_ranges
            for number in dict.fromkeys(self.numbers[aid] for aid in sources):
                pages=page_ranges(self.citations.anchors[aid]['page'] for aid in sources if self.numbers[aid]==number)
                internal_link(p,f' [{number}·{pages}]','source_'+str(number))
                props=p._p[-1].find('.//'+qn('w:rPr'))
                size=OxmlElement('w:sz');size.set(qn('w:val'),'16');props.append(size)
                align=OxmlElement('w:vertAlign');align.set(qn('w:val'),'superscript');props.append(align)
        else:
            for number,label in self.citations.labels(sources):internal_link(p,' '+label,'source_'+str(number))

    def inline(self, parent, p, flags=None, width=None):
        flags=flags or {}
        for node in parent.children or []:
            kind=node.type
            if kind in ('inline',):self.inline(node,p,flags,width)
            elif kind in ('strong','em','s'):
                self.inline(node,p,{**flags,{'strong':'bold','em':'italic','s':'strike'}[kind]:True},width)
            elif kind=='text' or kind=='code_inline':
                from .reading_design import prose_pieces
                pieces=prose_pieces(node.content,self.config.get('joined_symbols',False)) if self.reading and kind=='text' else [(False,node.content)]
                for is_math,value in pieces:
                    if is_math:p._p.append(formula_omml(value));continue
                    r=p.add_run(value);r.bold=flags.get('bold');r.italic=flags.get('italic');r.font.strike=flags.get('strike')
                    if kind=='code_inline':r.font.name='Consolas'
            elif kind=='math_inline':p._p.append(formula_omml(node.content))
            elif kind in ('softbreak','hardbreak'):p.add_run(' ' if kind=='softbreak' else '\n')
            elif kind=='link':
                target=node.attrs['href'];text=''.join(n.content for n in walk_nodes(node.children or []) if n.type in ('text','code_inline'))
                if target.startswith('#'):internal_link(p,text,'section_'+target[1:])
                else:
                    from docx.opc.constants import RELATIONSHIP_TYPE as RT
                    link=OxmlElement('w:hyperlink');link.set(qn('r:id'),self.doc.part.relate_to(target,RT.HYPERLINK,is_external=True))
                    run=OxmlElement('w:r');props=OxmlElement('w:rPr');color=OxmlElement('w:color');color.set(qn('w:val'),self.d['accent']);props.append(color);run.append(props)
                    t=OxmlElement('w:t');t.text=text;run.append(t);link.append(run);p._p.append(link)
            elif kind=='image':
                self.picture(p,node.attrs['src'][7:],None,width or self.d['width_cm'])
                if node.content:p.add_run('\n'+node.content)
            else:raise ValueError('Markdown 行内结构暂不支持：'+kind+'；原修订保留')

    def rich(self,p,text):
        # Same inline Markdown and native math path for paragraphs/table cells.
        # Parse inline rather than interpreting a leading "1." or "#" as a
        # block: existing paragraph strings must keep their literal meaning.
        parts=re.split(r'(\$\$.+?\$\$|\\\[.+?\\\])',str(text),flags=re.S)
        for part in parts:
            if part.startswith(('$$','\\[')) and part.endswith(('$$','\\]')):p._p.append(formula_omml(part[2:-2]))
            elif part:self.inline(SyntaxTreeNode(parser().parseInline(part)),p)
        return p

    def markdown(self,parent,text,width,indent=0):
        last_paragraph=None
        def emit(items,depth=0,prefix=None):
            nonlocal last_paragraph
            for index,node in enumerate(items):
                kind=node.type
                if kind in ('paragraph','heading'):
                    level=int(node.tag[1:]) if kind=='heading' else 0
                    if not level and self.config.get('promote_list_leads') and not depth and index+1<len(items):
                        runs=node.children[0].children if node.children and node.children[0].type=='inline' else []
                        if len(runs)==1 and runs[0].type=='strong' and items[index+1].type in ('ordered_list','bullet_list'):
                            level=self.section_depth
                    p=parent.add_paragraph(style='Heading '+str(min(level+1,3)) if level else None)
                    if depth:p.paragraph_format.left_indent=Cm(depth*.45)
                    if prefix:p.add_run(prefix);prefix=None
                    self.inline(node,p,width=width)
                    last_paragraph=p
                elif kind in ('bullet_list','ordered_list'):
                    start=int(node.attrs.get('start',1))
                    for i,child in enumerate(node.children):emit(child.children,depth+1,('• ' if kind=='bullet_list' else str(start+i)+'. '))
                elif kind=='blockquote':emit(node.children,depth+1)
                elif kind in ('fence','code_block'):
                    p=parent.add_paragraph(style='No Spacing');r=p.add_run(node.content.rstrip('\n'));r.font.name='Consolas';r.font.size=Pt(9)
                    last_paragraph=p
                elif kind in ('math_block','math_block_label','math_block_eqno'):
                    p=parent.add_paragraph();p._p.append(math_element('oMathPara',formula_omml(node.content)))
                    if node.type in ('math_block_label','math_block_eqno'):p.add_run(' ('+str(node.info)+')')
                    last_paragraph=p
                elif kind=='table':
                    data=[]
                    for row in walk_nodes([node]):
                        if row.type=='tr':data.append(row.children)
                    table=self.table(parent,['']*len(data[0]),[['']*len(r) for r in data[1:]],width)
                    for ri,(row,cells) in enumerate(zip(table.rows,data)):
                        for cell,source in zip(row.cells,cells):
                            cell.paragraphs[0].clear();self.inline(source,cell.paragraphs[0],width=cell.width/Cm(1))
                            if ri==0:
                                for run in cell.paragraphs[0].runs:run.bold=True;run.font.color.rgb=RGBColor.from_string(self.d['table_header_text'])
                    last_paragraph=table.rows[-1].cells[-1].paragraphs[-1]
                elif kind=='hr':parent.add_paragraph('')
                else:raise ValueError('Markdown 结构暂不支持：'+kind+'；没有静默省略')
        emit(nodes(text),indent)
        return last_paragraph

    def table(self,parent,headers,rows,width,weights=None):
        table=parent.add_table(rows=1,cols=len(headers)) if parent is self.doc else parent.add_table(rows=1,cols=len(headers),width=Cm(width))
        table.autofit=False;table.style='Table Grid'
        if not weights:
            import unicodedata
            def length(x):return sum(2 if unicodedata.east_asian_width(c) in ('W','F') else 1 for c in str(x))
            weights=[math.sqrt(max([length(headers[i]),1]+[length(r[i]) for r in rows])) for i in range(len(headers))]
        widths=[width*w/sum(weights) for w in weights]
        for col,w in zip(table.columns,widths):col.width=Cm(w)
        for cell,value in zip(table.rows[0].cells,headers):self.rich(cell.paragraphs[0],str(value))
        repeat=OxmlElement('w:tblHeader');table.rows[0]._tr.get_or_add_trPr().append(repeat)
        for values in rows:
            for cell,value in zip(table.add_row().cells,values):self.rich(cell.paragraphs[0],str(value))
        for ri,row in enumerate(table.rows):
            if self.reading:row._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
            for ci,cell in enumerate(row.cells):
                cell.width=Cm(widths[ci]);cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
                props=cell._tc.get_or_add_tcPr();margins=OxmlElement('w:tcMar')
                for side in ('top','bottom','left','right'):
                    padding=self.d['table_padding_vertical_pt'] if side in ('top','bottom') else self.d['table_padding_horizontal_pt']
                    node=OxmlElement('w:'+side);node.set(qn('w:w'),str(round(padding*20)));node.set(qn('w:type'),'dxa');margins.append(node)
                props.append(margins)
                shade=OxmlElement('w:shd');shade.set(qn('w:fill'),self.d['table_header'] if ri==0 else self.d['table_stripe'] if ri%2==0 else 'FFFFFF');props.append(shade)
                borders=OxmlElement('w:tcBorders')
                for side in ('top','bottom','left','right'):
                    edge=OxmlElement('w:'+side);edge.set(qn('w:val'),'single');edge.set(qn('w:sz'),'4');edge.set(qn('w:color'),'D9D9D9');borders.append(edge)
                props.append(borders)
                for p in cell.paragraphs:
                    p.paragraph_format.space_after=Pt(3)
                    if ri==0 or self.reading and ri==len(table.rows)-2:p.paragraph_format.keep_with_next=True
                    if ci < len(headers) and all(len(str(r[ci]))<18 for r in rows):p.alignment=WD_ALIGN_PARAGRAPH.CENTER
                    for r in p.runs:
                        r.font.size=Pt(self.d['table_font_size'])
                        if ri==0:r.bold=True;r.font.color.rgb=RGBColor.from_string(self.d['table_header_text'])
        if not self.reading:parent.add_paragraph('')
        return table

    def picture(self,p,identity,width_cm,width,framing='exact'):
        from PIL import Image
        anchor=self.snapshot.anchor(identity);page=self.snapshot.one('SELECT image FROM pages WHERE source_id=? AND number=?',(anchor['source_id'],anchor['page']))
        self.serial+=1;name=f'figure-{self.serial}.png'
        with Image.open(self.snapshot.asset_path(page['image'])) as image:
            region=anchor.get('region')
            if region and framing!='exact' and self.config.get('complete_figures'):
                from .page_navigation import illustration_bounds
                region=illustration_bounds(region,page.get('figure_geometry',{}))
                pixels=(math.floor(region[0]*image.width), math.floor(region[1]*image.height),
                        math.ceil(region[2]*image.width), math.ceil(region[3]*image.height))
                image=image.crop(pixels)
            elif region:image=image.crop(tuple(int(v*(image.width if i%2==0 else image.height)) for i,v in enumerate(region)))
            image.save(self.out/name);w=min(width_cm or width,width, (self.d['page_cm'][1]-self.d['margins_cm']['top']-self.d['margins_cm']['bottom']-2)*image.width/image.height)
        p.add_run().add_picture(str(self.out/name),width=Cm(w))
        self.export_images[identity]=name
        return name

    def blocks(self,parent,blocks,width):
        from .reading_design import citation_groups,display_equation
        visible=citation_groups(blocks,self.citations) if self.reading else [b.get('sources',[]) for b in blocks]
        for block,sources in zip(blocks,visible):
            k=block['type'];suffix=' '.join(label for _,label in self.citations.labels(sources))
            if k=='paragraph':self.cite(self.rich(parent.add_paragraph(),block['text']),sources);self.md += [block['text']+' '+suffix,'']
            elif k=='markdown':
                last=self.markdown(parent,block['text'],width);self.cite(last if last is not None else parent.add_paragraph(style='Caption'),sources)
                exported=block['text']
                for identity,name in self.export_images.items():exported=exported.replace('source:'+identity,name)
                self.md += [exported+'\n\n'+suffix,'']
            elif k=='equation':
                if self.reading:
                    number,prose=display_equation(self,parent,block,width,sources)
                    self.md+=['$$'+block['latex']+'$$','',f'({number}) '+prose+' '+suffix,'']
                    if block.get('formula_ref'):self.shown_formulas[(block['formula_ref']['id'],block['formula_ref']['revision'])]=number
                    continue
                p=parent.add_paragraph();p._p.append(math_element('oMathPara',formula_omml(block['latex'])));p.add_run(' ('+block['label']+')');self.cite(p,sources)
                self.rich(parent.add_paragraph(),'符号：'+block['symbols']);self.md += ['$$'+block['latex']+'$$','', '('+block['label']+') '+block['symbols']+' '+suffix,'']
                if block.get('formula_ref'):self.shown_formulas[(block['formula_ref']['id'],block['formula_ref']['revision'])]=block['label']
            elif k=='branch_table':
                from .observation_paths import branch_rows
                rows=branch_rows(block,self.snapshot,self.content.get('draft_ref'));headers=['怎样判断','这一情形能读什么','接下来怎样处理']
                caption=self.rich(parent.add_paragraph(style='Caption'),block['caption']);caption.paragraph_format.keep_with_next=True
                self.cite(caption,sources);self.table(parent,headers,rows,width,[1,1.5,2])
                self.md += [block['caption'],markdown_table(headers,rows),'']
            elif k=='worked_example':
                from .observation_paths import example_rows
                branch,rows=example_rows(block,self.snapshot,self.content.get('draft_ref'),human_units=self.config.get('human_units',False));headers=['量','怎样读或算','数值']
                caption=self.rich(parent.add_paragraph(style='Caption'),block['caption']+'（'+branch['condition']+'）');caption.paragraph_format.keep_with_next=True
                self.cite(caption,list(dict.fromkeys(sources+[i['source_anchor'] for i in block['inputs'] if i.get('source_anchor')])))
                self.table(parent,headers,rows,width,[1,2.7,1.25]);self.md += [block['caption']+'（'+branch['condition']+'）',markdown_table(headers,rows),'']
            elif k=='table':
                caption=self.rich(parent.add_paragraph(style='Caption'),block['caption']);caption.paragraph_format.keep_with_next=True
                self.cite(caption,sources);self.table(parent,block['columns'],block['rows'],width,block.get('column_weights'));self.md += [block['caption']+' '+suffix,'',markdown_table(block['columns'],block['rows']),'']
            elif k=='figure':
                p=parent.add_paragraph();p.alignment={'left':0,'center':1,'right':2}[block.get('align','center')];name=self.picture(p,block['source_anchor'],block.get('width_cm'),width,block.get('framing','auto'))
                p.paragraph_format.keep_with_next=True
                self.cite(self.rich(parent.add_paragraph(style='Caption'),block['caption']),sources);self.md += ['!['+block['caption']+']('+name+') '+suffix,'']
            elif k=='code':
                r=parent.add_paragraph().add_run(block['text']);r.font.name='Consolas';r.font.size=Pt(9);self.cite(parent.add_paragraph(style='Caption'),sources);self.md += ['```'+block.get('language','python'),block['text'],'```',suffix,'']
            elif k=='crossref':internal_link(parent.add_paragraph(),block['text'],'section_'+block['target']);self.md += ['['+block['text']+'](#'+block['target']+')','']
            elif k=='pagebreak':parent.add_paragraph().add_run().add_break(7);self.md += ['<!-- pagebreak -->','']
            elif k=='space':
                p=parent.add_paragraph();p.paragraph_format.space_after=Pt(block.get('height_pt',18));self.md += ['','']
            elif k=='columns':
                weights=block.get('weights',[1]*len(block['columns']));table=parent.add_table(rows=1,cols=len(weights)) if parent is self.doc else parent.add_table(rows=1,cols=len(weights),width=Cm(width));table.autofit=False
                props=table._tbl.tblPr;caption=OxmlElement('w:tblCaption');caption.set(qn('w:val'),'physics-layout-columns');props.append(caption)
                borders=OxmlElement('w:tblBorders')
                for side in ('top','left','bottom','right','insideH','insideV'):
                    edge=OxmlElement('w:'+side);edge.set(qn('w:val'),'nil');borders.append(edge)
                props.append(borders)
                for col,cell,weight,column in zip(table.columns,table.rows[0].cells,weights,block['columns']):
                    cw=width*weight/sum(weights);col.width=cell.width=Cm(cw);cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.TOP
                    self.md += ['<!-- column -->',''];self.blocks(cell,column['blocks'],max(.1,cw-.6))
                parent.add_paragraph('')
            elif k=='goal_summary':self.goals(parent,width,block.get('show_title',True))
            elif k=='procedure':self.steps(parent,block.get('show_title',True))
            else:raise ValueError('不支持的文稿块：'+k)

    def goals(self,parent,width,show_title=True):
        from .display_text import goal_labels,display_unit
        if not self.content.get('goals'):return
        heading='实验目标与观察记录' if any(g.get('kind')=='qualitative' for g in self.content['goals']) else '测量目标与需要记录的量'
        if show_title:parent.add_heading(heading,1);self.md += ['## '+heading,'']
        labels=goal_labels(self.content['goals'])
        for goal in self.content['goals']:
            prefix=(labels[goal['id']]+' · ') if self.d.get('concise_plan',False) else ''
            if goal.get('kind')=='qualitative':
                self.cite(self.rich(parent.add_paragraph(),prefix+goal['objective']+'（定性观察）'),goal['sources'])
                if goal.get('model'):self.rich(parent.add_paragraph(),goal['model'])
                self.rich(parent.add_paragraph(),'观察条件：'+'；'.join(goal['conditions']))
                self.markdown(parent,goal['observation_plan'],width)
                self.md += [prefix+goal['objective']+'（定性观察）',goal.get('model',''),'观察条件：'+'；'.join(goal['conditions']),goal['observation_plan'],'']
                continue
            self.cite(self.rich(parent.add_paragraph(),prefix+goal['quantity']+' / '+display_unit(goal['unit'])+'　'+goal['model']),goal['sources'])
            self.rich(parent.add_paragraph(),'适用条件：'+'；'.join(goal['conditions']))
            formula=self.snapshot.object(goal['formula_ref']['id'],goal['formula_ref']['revision'])['content']
            key=(goal['formula_ref']['id'],goal['formula_ref']['revision'])
            markdown_formula='$$'+formula['latex']+'$$'
            if self.config.get('structured_plan_edition',0)>=2:
                from .reading_design import display_equation
                number=self.shown_formulas.get(key) or self.body_formulas.get(formula['latex'].strip())
                if number:
                    symbols='计算式见式（'+number+'）。'
                    internal_link(parent.add_paragraph(),symbols,'equation_'+number)
                    markdown_formula=''
                elif key in self.goal_formulas:
                    symbols='计算式见'+self.goal_formulas[key]+'。';parent.add_paragraph(symbols)
                    markdown_formula=''
                else:
                    symbols='；'.join('$'+s['symbol'].strip('$')+'$：'+s['quantity']+' / '+display_unit(s['unit']) for s in formula['symbols'])
                    display_equation(self,parent,{'latex':formula['latex'],'symbols':symbols},width,[],numbered=False)
                    self.goal_formulas[key]=labels[goal['id']]
            elif key not in self.shown_formulas and self.config.get('structured_plan_edition'):
                from .reading_design import display_equation
                symbols='；'.join('$'+s['symbol'].strip('$')+'$：'+s['quantity']+' / '+display_unit(s['unit']) for s in formula['symbols'])
                number,_=display_equation(self,parent,{'latex':formula['latex'],'symbols':symbols},width,[])
                self.shown_formulas[key]=number
            elif key not in self.shown_formulas:
                parent.add_paragraph()._p.append(formula_omml(formula['latex']))
                symbols='；'.join('$'+s['symbol']+'$：'+s['quantity']+' / '+display_unit(s['unit']) for s in formula['symbols']);self.rich(parent.add_paragraph(),symbols)
            else:
                symbols='计算式见公式（'+self.shown_formulas[key]+'）。';parent.add_paragraph(symbols)
            # Readable paired fields rather than a forced six-column grid.
            headers=['需要测什么','现场怎样记录']
            rows=[[r['quantity']+' / '+display_unit(r['unit']),'时机：'+r['timing']+'；条件：'+r['condition']+'；仪器：'+r['instrument']+('；精度：'+r['precision'] if r.get('precision') else '')] for r in goal['measurements']]
            self.table(parent,headers,rows,width,[1,3]);self.md += [prefix+goal['quantity']+' / '+display_unit(goal['unit']),'适用条件：'+'；'.join(goal['conditions']),markdown_formula,symbols,markdown_table(headers,rows),'']

    def steps(self,parent,show_title=True):
        from .display_text import goal_labels,goal_name
        if not self.content.get('steps'):return
        if show_title:parent.add_heading('操作顺序与完成判据',1);self.md += ['## 操作顺序与完成判据','']
        names={g['id']:goal_name(g) for g in self.content.get('goals',[])}
        labels=goal_labels(self.content.get('goals',[]))
        concise=self.d.get('concise_plan',False)
        def sentence(value):return value if value.endswith(('。','！','？','.','!','?')) else value+'。'
        steps=self.content['steps']
        from .reading_design import step_citations
        locators=step_citations(steps,self.citations) if self.config.get('structured_plan_edition') else [s['sources'] for s in steps]
        for i,(step,sources) in enumerate(zip(steps,locators),1):
            title=str(i)+'　'+step['action']+('（'+'、'.join(labels[g] for g in step['goal_ids'])+'）' if concise else '');self.rich(parent.add_heading('',2),title)
            if concise:
                text=sentence(step['reason'])+'\n条件：'+sentence(step['conditions'])+'\n完成：'+sentence(step['completion'])
            else:text='理由：'+step['reason']+'。条件：'+step['conditions']+'。完成判据：'+step['completion']+'。对应目标：'+'、'.join(names[g] for g in step['goal_ids'])+'。'
            self.cite(self.rich(parent.add_paragraph(),text),sources);self.md += ['### '+title,text,'']
            if not concise:
                internal_link(parent.add_paragraph(),'查看仪器操作附录','section_instruments');self.md += ['[查看仪器操作附录](#instruments)','']
        if concise:
            internal_link(parent.add_paragraph(),'查看仪器操作附录','section_instruments');self.md += ['[查看仪器操作附录](#instruments)','']

    def sections(self,which):
        for i,section in enumerate(self.content[which]):
            self.section_depth=section.get('heading_level',1)
            if section.get('page_break_before',which=='appendices' and self.config.get('appendix_new_page',False)):self.doc.add_page_break()
            p=self.rich(self.doc.add_heading('',section.get('heading_level',1)),section['title']) if section.get('show_heading',True) else self.doc.add_paragraph()
            bookmark(p,'section_'+section['id'],100+sum(len(self.content[n]) for n in ('sections',) if which=='appendices')+i)
            self.md += ['<a id="'+section['id']+'"></a>','## '+section['title'],''];self.blocks(self.doc,section['blocks'],self.d['width_cm'])

    def sources(self):
        self.doc.add_heading('参考来源',1);self.md+=['## 参考来源','']
        # Number originals, not every full-page/quote/crop anchor. Precise
        # regions, checks and hashes remain in the complete sources.json.
        for _,identities in self.citations.groups.items():
            anchor=self.citations.anchors[identities[0]];num=self.numbers[identities[0]]
            pages=sorted({self.citations.anchors[aid]['page'] for aid in identities})
            self.source_index.extend({'number':num,**self.citations.anchors[aid]} for aid in identities)
            from .reading_design import page_ranges
            text=f"[{num}] {anchor['name']}，第"+(page_ranges(pages) if self.reading else '、'.join(map(str,pages)))+'页。'
            if anchor.get('metadata',{}).get('url'):text+=' '+anchor['metadata']['url']
            p=self.doc.add_paragraph(text,'Source');bookmark(p,'source_'+str(num),1000+num);self.md += [text,'']

    def build(self):
        embedded={b['type'] for b in all_blocks(self.content)}
        allowed={'summary','background','warnings','sections','goals','steps','appendices','sources'}
        flow=self.d['flow']
        if len(flow)!=len(set(flow)) or not set(flow)<=allowed:raise ValueError('主题 flow 有未知或重复部件')
        # Required science and pending facts are never hidden by a layout choice.
        flow=flow+[n for n in ('summary','warnings','sections','goals','steps','appendices','sources') if n not in flow]
        for item in flow:
            if item=='summary' and self.content['summary'].strip():self.markdown(self.doc,self.content['summary'],self.d['width_cm']);self.md += [self.content['summary'],'']
            elif item=='background' and self.d.get('show_background',True):
                bg=self.content['background'];text='学习背景：'+bg['level']+'　目标：'+bg['goal']
                if bg.get('minutes') is not None:text+='　可用时间：'+str(bg['minutes'])+' 分钟'
                self.rich(self.doc.add_paragraph(style='Caption'),text);self.md += [text,'']
            elif item=='warnings' and self.content.get('warnings'):
                self.doc.add_heading('使用说明',1)
                for text in self.content['warnings']:self.rich(self.doc.add_paragraph(),text);self.md += ['> '+text,'']
            elif item in ('sections','appendices'):self.sections(item)
            elif item=='goals' and 'goal_summary' not in embedded:self.goals(self.doc,self.d['width_cm'])
            elif item=='steps' and 'procedure' not in embedded:self.steps(self.doc)
            elif item=='sources':self.sources()
        if self.reading:
            from .reading_design import finish_flow
            finish_flow(self.doc,self.d)
        self.doc.save(self.out/'document.docx')
        (self.out/'document.md').write_text('\n'.join(self.md),encoding='utf-8')
        (self.out/'design.json').write_text(json.dumps(self.d,ensure_ascii=False,indent=2),encoding='utf-8')
        (self.out/'sources.json').write_text(json.dumps(self.source_index,ensure_ascii=False,indent=2),encoding='utf-8')
        (self.out/'composition.json').write_text(json.dumps(self.content,ensure_ascii=False,indent=2),encoding='utf-8')
        (self.out/'preview.html').write_text('<!doctype html><meta charset="utf-8"><title>'+html.escape(self.content['title'])+'</title>'+parser().render('\n'.join(self.md)),encoding='utf-8')


def build_composed_document(content,out,snapshot,template_path):
    from .measurement_tables import resolve_measurement_tables
    resolved,used=resolve_measurement_tables(content,snapshot)
    Composer(resolved,out,snapshot,template_path).build()
    if used:
        (Path(out)/'authored-composition.json').write_text(json.dumps(content,ensure_ascii=False,indent=2),encoding='utf-8')
        (Path(out)/'measurement-sources.json').write_text(json.dumps(used,ensure_ascii=False,indent=2),encoding='utf-8')
