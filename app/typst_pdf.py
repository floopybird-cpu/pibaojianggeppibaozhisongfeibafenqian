"""Offline, faithful layout of generated Word content using a pinned Typst CLI.

All text is emitted as string data. The generated source never includes user
Typst code, packages, imports or arbitrary file paths. Equations stay OMML in
Word and use independently rendered vector paths in PDF.
"""
from __future__ import annotations
import json, os, re, shutil, subprocess, tempfile
from pathlib import Path
from docx.oxml.ns import qn
from .rich_documents import RichReader, local, RENDER_LOCK

ROOT=Path(__file__).resolve().parents[1]
EXE=ROOT/'runtime/typst/typst-x86_64-pc-windows-msvc/typst.exe'

def string(value):return json.dumps(str(value),ensure_ascii=False)
def text(value):return '#text('+string(value)+')'


class TypstReader(RichReader):
    def __init__(self,source,work,design):
        super().__init__(source,work);self.design=design

    def image_markup(self,markup,scale=1):
        # RichReader returns one local image after validating/retaining math.
        match=re.fullmatch(r'<img src="([^"/\\]+)" width="([\d.]+)" height="([\d.]+)"[^>]*>',markup)
        if not match:raise ValueError('数学或图像排版回执无效')
        name,width,height=match.groups()
        if scale!=1:width,height=str(float(width)*scale),str(float(height)*scale)
        baseline='20%'
        if self.design.get('reading_edition')==2 and name.startswith('equation-'):
            from xml.etree import ElementTree
            _,top,_,box_height=map(float,ElementTree.parse(self.work/name).getroot().get('viewBox').split())
            baseline=f'{(box_height+top)*float(height)/box_height:.3f}pt'
        return '#box(baseline: '+baseline+')[#image('+string(name)+', width: '+width+'pt, height: '+height+'pt)]'

    def inline_typst(self,element,math_size=None):
        name=local(element)
        if name in ('footnoteReference','endnoteReference','altChunk','object','pict'):
            raise ValueError('当前 Word 阅读不支持 '+name+'；原件保留，请提供 PDF')
        if element.tag==qn('m:oMath'):return self.image_markup(self.formula(element),math_size/13 if math_size is not None else 1)
        if name=='t':return text(element.text or '')
        if name=='tab':return text('　')
        if name in ('br','cr'):return '\n#pagebreak()\n' if element.get(qn('w:type'))=='page' else '#linebreak()'
        if name=='blip':return self.image_markup(super().inline(element))
        if name in ('pPr','rPr','bookmarkEnd','instrText','del','delText'):return ''
        if name=='bookmarkStart':return ''
        result=''.join(self.inline_typst(x,math_size) for x in element)
        if name=='hyperlink':
            anchor=element.get(qn('w:anchor'))
            if anchor:return '#link(label('+string(anchor)+'))['+result+']'
            relation=self.document.part.rels.get(element.get(qn('r:id')))
            if relation and str(relation.target_ref).startswith(('https://','http://','mailto:')):return '#link('+string(relation.target_ref)+')['+result+']'
        if name=='r':
            props=element.find(qn('w:rPr'))
            if props is not None:
                for key,fn in [('b','strong'),('i','emph'),('strike','strike')]:
                    prop=props.find(qn('w:'+key))
                    if prop is not None and prop.get(qn('w:val'),'1') not in ('0','false'):result='#'+fn+'['+result+']'
                va=props.find(qn('w:vertAlign'))
                if va is not None:result='#'+('super' if va.get(qn('w:val'))=='superscript' else 'sub')+'['+result+']'
        return result

    def paragraph_typst(self,element):
        props=element.find(qn('w:pPr'));style=props.find(qn('w:pStyle')) if props is not None else None
        name=style.get(qn('w:val'),'') if style is not None else ''
        modern=self.design.get('reading_edition')==2
        math_size=self.design['font_size'] if modern else None
        if modern and element.xpath('ancestor::w:tc'):math_size=self.design['table_font_size']
        if name=='Title':math_size=self.design['title_size']
        elif name.startswith('Heading'):
            level=int(name[-1]) if name[-1:].isdigit() else 1
            math_size=self.design['heading_size'] if level==1 else self.design['subheading_size'] if level==2 else self.design['font_size']
        elif name=='Caption':math_size=max(8,self.design['font_size']-1.5)
        elif name=='Source':math_size=self.design.get('source_font_size',8)
        value=None
        if modern and name=='DisplayFormula':
            eq=next(element.iter(qn('m:oMath')),None)
            if eq is not None:
                number=''.join(t.text or '' for child in element if child.tag==qn('w:r') for t in child.iter(qn('w:t')))
                value='#grid(columns:(1fr,auto,1fr),align:horizon,[],['+self.inline_typst(eq,math_size)+'],[#align(right)['+text(number)+']])'
                for link in element.iter(qn('w:hyperlink')):value+=self.inline_typst(link,math_size)
        if value is None:value=self.inline_typst(element,math_size)
        if '\n#pagebreak()\n' in value:
            # Word's explicit page-break paragraph must be emitted at page
            # level, never inside the paragraph block used for spacing.
            parts=value.split('\n#pagebreak()\n')
            return '\n#pagebreak()\n'.join('#block['+part+']' if part.strip() else '' for part in parts)+'\n'
        if not value:
            spacing=props.find(qn('w:spacing')) if props is not None else None
            height=float(spacing.get(qn('w:after'),'80'))/20 if spacing is not None else 4
            return '\n#v('+str(height)+'pt, weak: true)\n'
        if name=='Title':result='#block(above: 0pt, below: '+str(self.design.get('title_space_after_pt',13))+'pt)[#text(size: '+str(self.design['title_size'])+'pt, weight: "bold")['+value+']]'
        elif name.startswith('Heading'):
            level=int(name[-1]) if name[-1:].isdigit() else 1
            size=self.design['heading_size'] if level==1 else self.design['subheading_size'] if level==2 else self.design['font_size']
            result='#heading(level: '+str(level)+')[#text(size: '+str(size)+'pt, weight: "bold")['+value+']]'
        elif name=='Caption':result='#text(size: '+str(max(8,self.design['font_size']-1.5))+'pt, fill: rgb("52606B"))['+value+']'
        elif name=='Source':result='#text(size:'+str(self.design.get('source_font_size',8))+'pt,fill:rgb("52606B"))['+value+']'
        elif name=='NoSpacing':
            raw=''.join(t.text or '' for t in element.iter(qn('w:t')));result='#raw('+string(raw)+', block: true)'
        else:result=value
        if name=='Title' and modern:result='#text(font:'+string(self.design['heading_font'])+')['+result+']'
        if props is not None:
            align=props.find(qn('w:jc'))
            if align is not None and align.get(qn('w:val')) in ('center','right'):result='#align('+align.get(qn('w:val'))+')['+result+']'
            indent=props.find(qn('w:ind'))
            if modern and indent is not None and indent.get(qn('w:firstLine')):
                result='#h('+str(float(indent.get(qn('w:firstLine')))/20)+'pt)'+result
            if indent is not None and indent.get(qn('w:left')):result='#pad(left: '+str(float(indent.get(qn('w:left')))/20)+'pt)['+result+']'
            before=props.find(qn('w:pageBreakBefore'))
            if before is not None and before.get(qn('w:val'),'1') not in ('0','false'):result='#pagebreak()\n'+result
        labels=[x.get(qn('w:name')) for x in element.iter(qn('w:bookmarkStart')) if x.get(qn('w:name'))]
        if name!='Title' and not name.startswith('Heading'):
            spacing=self.design.get('source_spacing',2) if name=='Source' else self.design['paragraph_spacing']
            keep=props.find(qn('w:keepNext')) if props is not None else None
            sticky=keep is not None and keep.get(qn('w:val'),'1') not in ('0','false')
            result='#block(above:0pt,below:'+str(spacing)+'pt,breakable:true,sticky:'+str(sticky).lower()+')['+result+']'
        if len(labels)>1:
            result=''.join('#metadata(none)#label('+string(label)+')\n' for label in labels)+result
        elif labels:result+='#label('+string(labels[0])+')'
        return '\n'+result+'\n\n'

    def table_typst(self,element):
        if element.find('.//'+qn('w:gridSpan')) is not None or element.find('.//'+qn('w:vMerge')) is not None:raise ValueError('当前 Word 阅读不支持合并单元格，原件保留')
        self.tables+=1
        caption=element.find('./'+qn('w:tblPr')+'/'+qn('w:tblCaption'))
        layout=caption is not None and caption.get(qn('w:val'))=='physics-layout-columns'
        grid=element.find(qn('w:tblGrid'));weights=[float(c.get(qn('w:w'),'1')) for c in grid] if grid is not None else []
        columns='('+','.join(str(w)+'fr' for w in weights)+',)' if weights else 'auto'
        rows=[x for x in element if local(x)=='tr'];cells=[]
        for ri,row in enumerate(rows):
            values=['['+self.blocks_typst(cell)+']' for cell in row if local(cell)=='tc']
            props=row.find(qn('w:trPr'));header=props is not None and props.find(qn('w:tblHeader')) is not None
            keep_row=(not layout and not header and self.design.get('keep_table_rows') and weights
                      and props is not None and props.find(qn('w:cantSplit')) is not None)
            if keep_row:
                cells.append('..print-row(('+','.join(values)+',))')
                continue
            if layout:cells+=values
            elif header:
                colored=['[#text(fill:rgb('+string(self.design['table_header_text'])+'),weight:"bold")'+v+']' for v in values]
                cells.append('table.header(repeat:true,'+','.join(colored)+')')
            else:cells+=values
        if layout:return '\n#grid(columns:'+columns+',gutter:12pt,'+','.join(cells)+')\n\n'
        d=self.design
        table='\n#text(size:'+str(d['table_font_size'])+'pt)[#table(columns:'+columns+',inset:(x:'+str(d.get('table_padding_horizontal_pt',4))+'pt,y:'+str(d.get('table_padding_vertical_pt',d['table_padding_pt']))+'pt),stroke:0.5pt+rgb("D9D9D9"),fill:(x,y)=>if y==0 {rgb('+string(d['table_header'])+')} else if calc.even(y) {rgb('+string(d['table_stripe'])+')} else {none},'+','.join(cells)+')]\n\n'
        if not any(item.startswith('..print-row(') for item in cells):
            return table
        # Match Word's keep-row intent when it fits. Measure actual cell
        # content so a long observation can still flow over several pages.
        inset=d.get('table_padding_horizontal_pt',4)
        widths=','.join(f'{max(1,w/20-2*inset-1):.2f}pt' for w in weights)
        height=(d['page_cm'][1]-d['margins_cm']['top']-d['margins_cm']['bottom'])*72/2.54
        height-=2*d.get('table_padding_vertical_pt',d['table_padding_pt'])+2*d['table_font_size']
        helper=('#let print-row(items) = {\n  let widths=('+widths+',)\n'
                '  let tall=range(items.len()).any(i => measure(block(width:widths.at(i),items.at(i))).height > '+f'{height:.2f}pt)\n'
                '  items.map(x => table.cell(breakable:tall,x))\n}\n')
        return '\n#context [\n'+helper+table+']\n\n'

    def blocks_typst(self,parent):
        result=[]
        for element in parent:
            kind=local(element)
            if kind=='p':result.append(self.paragraph_typst(element))
            elif kind=='tbl':result.append(self.table_typst(element))
            elif kind in ('sdt','sdtContent','customXml'):result.append(self.blocks_typst(element))
            elif kind not in ('sectPr','tblPr','tblGrid','tcPr','bookmarkStart','bookmarkEnd','proofErr','permStart','permEnd'):raise ValueError('Word 结构暂不支持：'+kind+'；没有静默省略')
        return ''.join(result)


def docx_to_typst_pdf(source,destination,config):
    if not EXE.is_file():raise ValueError('缺少随应用提供的 Typst 离线排版器，请恢复完整发行包')
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    d=config['design']
    with RENDER_LOCK,tempfile.TemporaryDirectory(prefix='.physics-typst-',dir=destination.parent) as temporary:
        work=Path(temporary);reader=TypstReader(source,work,d);body=reader.blocks_typst(reader.document.element.body)
        margins=', '.join(k+':'+str(v)+'cm' for k,v in d['margins_cm'].items())
        font=(d['font'],'Microsoft YaHei','SimSun','Arial')
        if d.get('reading_edition')==2:font=(d['latin_font'],*font)
        preamble='\n'.join([
            '#set document(title:'+string(reader.document.core_properties.title)+',author:"物理实验助手")',
            '#set page(width:'+str(d['page_cm'][0])+'cm,height:'+str(d['page_cm'][1])+'cm,margin:('+margins+'),footer:context align(right,counter(page).display("1")))',
            '#set text(font:('+','.join(string(f) for f in dict.fromkeys(font))+',),size:'+str(d['font_size'])+'pt,lang:"zh",top-edge:"bounds",bottom-edge:"bounds")',
            '#set par(leading:'+str(max(0,d['line_spacing']-1))+'em,spacing:'+str(d['paragraph_spacing'])+'pt)',
            '#set heading(numbering:none)',
            '#show heading: set block(above:'+str(d.get('heading_space_before_pt',14))+'pt,below:'+str(d.get('heading_space_after_pt',7))+'pt)',
            '#show link: set text(fill:rgb('+string(d['accent'])+'))',
            '#show raw: set text(font:("Consolas","Microsoft YaHei"),size:9pt)',
        ])
        if d.get('reading_edition')==2:
            preamble+='\n#show heading: set text(font:'+string(d['heading_font'])+')'
        (work/'document.typ').write_text(preamble+'\n\n'+body,encoding='utf-8')
        environment={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP','LOCALAPPDATA','APPDATA','USERPROFILE')}
        run=subprocess.run([str(EXE),'compile','--root',str(work),str(work/'document.typ'),str(work/'document.pdf')],cwd=work,env=environment,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if run.returncode:raise ValueError('文稿排版未完成；原修订保持不变。\n'+run.stderr.decode('utf-8',errors='replace'))
        shutil.copyfile(work/'document.pdf',destination)
        # A complete text source with its local vectors/images accompanies the
        # editable Word and PDF, so package changes remain reviewable.
        assets=destination.parent/'排版源文件';assets.mkdir(exist_ok=True)
        for path in work.iterdir():
            if path.name!='document.pdf':shutil.copy2(path,assets/path.name)
        return {'renderer':'Typst 0.15.1','pages_complete':True,'equations':reader.equations,'images':reader.images,'tables':reader.tables}
