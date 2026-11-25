from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W

from .native_widgets import KINDS,STATUS,label,button,row,plain,text,table,MathLabel,ImageView,scroll_body,export_file,export_bundle
from .display_text import goal_labels,display_unit


class ObjectDialog(W.QDialog):
    def __init__(self,ui,identity,revision=None):
        super().__init__(None);self.ui=ui;self.identity=identity;self.object=ui.app.gateway.read(identity,revision)
        self.setWindowTitle(self.object["title"]);self.resize(1120,880);self.layout=W.QVBoxLayout(self)
        self.toolbar=W.QHBoxLayout();self.layout.addLayout(self.toolbar)
        self.toolbar.addWidget(label(KINDS.get(self.object["kind"],self.object["kind"])))
        self.versions=W.QComboBox()
        for rev in self.object["history"]:self.versions.addItem(f"r{rev['revision']} · 包 {rev['pack_version']} · {rev['created'][:19]}",rev["revision"])
        self.versions.setCurrentIndex(self.versions.findData(self.object["revision"]));self.toolbar.addWidget(self.versions)
        self.toolbar.addWidget(button("更新版本",self.upgrade));self.toolbar.addWidget(button("查看最新修订",self.latest_revision));self.toolbar.addStretch()
        self.actions=W.QHBoxLayout();self.layout.addLayout(self.actions)
        if self.object["experiment_id"] or self.object["kind"]=="DataWorkbook":
            self.actions.addWidget(button("产物文件夹",lambda:self.open_product("folder")))
        if self.object["kind"]=="DataWorkbook":self.actions.addWidget(button("打开 Excel",lambda:self.open_product("xlsx")))
        if self.object["kind"]=="LearningDocument":
            word=button("打开 Word (Alt+W)",lambda:self.open_product("docx"));word.setShortcut(G.QKeySequence('Alt+W'));self.actions.addWidget(word)
            pdf=button("阅读 PDF (Alt+P)",self.show_pdf);pdf.setShortcut(G.QKeySequence('Alt+P'));pdf.setToolTip('在本窗口阅读完整排版PDF');self.actions.addWidget(pdf)
        self.actions.addWidget(button("导出完整成果",lambda:self.ui.safe(lambda:export_bundle(self,self.object))))
        if self.object["kind"] in ("LearningDocument","AnalysisProgram","SciencePoint") and self.object["package_state"]=="ready":self.actions.addWidget(button("编辑并另存修订",self.edit))
        self.versions.currentIndexChanged.connect(self.change_version)
        self.split=W.QSplitter();self.layout.addWidget(self.split,1);self.navigator=W.QListWidget();self.navigator.setMaximumWidth(215);self.split.addWidget(self.navigator)
        self.scroll,self.body,self.content=scroll_body();self.read_tabs=W.QTabWidget();self.read_tabs.addTab(self.scroll,"内容与出处");self.split.addWidget(self.read_tabs);self.split.setSizes([195,880]);self.targets={}
        self.navigator.currentItemChanged.connect(self.navigate)
        self.render()
        if self.object["kind"]=="LearningDocument":self.load_pdf()

    def latest_revision(self):
        latest=self.ui.app.gateway.read(self.identity)
        self.versions.blockSignals(True);self.versions.clear()
        for rev in latest['history']:
            self.versions.addItem(f"r{rev['revision']} · 包 {rev['pack_version']} · {rev['created'][:19]}",rev['revision'])
        self.versions.setCurrentIndex(self.versions.findData(latest['revision']));self.versions.blockSignals(False)
        self.change_version()

    def show_pdf(self):
        if hasattr(self,'pdf_reader'):self.read_tabs.setCurrentWidget(self.pdf_reader)
        else:self.open_product('pdf')

    def open_product(self,file_format):
        self.ui.submit("准备可阅读文件",lambda:self.ui.app.publications.request_open(self.object["experiment_id"],self.object,file_format),lambda _:self.ui.status.showMessage("已请求打开产物",5000))

    def load_pdf(self):
        from .native_products import PdfReader
        asset=next((a for a in self.object["assets"] if a["name"]=="document.pdf"),None)
        path=self.ui.app.store.asset_path(asset["path"]) if asset else None
        if path is None:
            published=self.ui.app.publications.info(self.object)
            name=next((f["name"] for f in published.get("files",[]) if f["name"].endswith(".pdf")),None)
            if name:path=Path(published["folder"])/name
        if path and path.is_file():
            self.pdf_reader=PdfReader(path,self);self.read_tabs.insertTab(0,self.pdf_reader,"排版阅读 PDF");self.read_tabs.setCurrentIndex(0)
        else:
            self.toolbar.addWidget(button("生成 PDF 阅读版",lambda:self.ui.submit("生成完整 PDF",lambda:self.ui.app.publications.publish(self.object),lambda _:self.load_pdf())))
    def heading(self,text_value,identity=None,level=1):
        widget=self.prose_label(text_value,"h2" if level==1 else "h3");self.content.addWidget(widget)
        if identity:
            self.targets[identity]=widget;item=W.QListWidgetItem(text_value);item.setData(C.Qt.UserRole,identity);self.navigator.addItem(item)
        return widget
    def prose_label(self,value,role=None):
        from .scientific_markup import parts
        if any(math for math, _ in parts(str(value))):
            from .native_markdown import MarkdownView
            return MarkdownView(('## ' if role=='h2' else '### ' if role=='h3' else '')+str(value),self.ui.app.store)
        return label(value,role) if role else label(value)
    def navigate(self,item,*args):
        if item and item.data(C.Qt.UserRole) in self.targets:self.read_tabs.setCurrentWidget(self.scroll);self.scroll.ensureWidgetVisible(self.targets[item.data(C.Qt.UserRole)],0,15)
    def citations(self,anchors):
        layout=W.QGridLayout();layout.setContentsMargins(0,0,0,0)
        from .source_citations import Citations
        numbers=Citations(self.object['content'].get('sources',[]),self.ui.app.store).numbers
        for index,identity in enumerate(anchors):
            anchor=self.ui.app.store.anchor(identity)
            name=anchor["name"] if self.object["experiment_id"] is None else anchor["name"][:18]
            title=f"[{numbers.get(identity,index+1)}] {name} · 第 {anchor['page']} 页"
            b=button(title,lambda checked=False,a=anchor:self.ui.open_source(a["source_id"],a["page"],a["region"]));b.setToolTip(anchor['name']);b.setProperty("citation",True);layout.addWidget(b,index//3,index%3)
        self.content.addLayout(layout)
    def reference(self,reference,title):
        try:name=self.ui.app.gateway.read(reference["id"],reference["revision"])["title"]
        except Exception:name=reference["id"]
        self.content.addWidget(button(title+" · "+name+f" · r{reference['revision']} ↗",lambda:self.ui.open_object(reference["id"],reference["revision"])))
    def block(self,block,visible_sources=None):
        kind=block["type"]
        if kind in ('worked_example','branch_table'):
            from .observation_paths import branch_rows,example_rows
            ref=self.object['content'].get('draft_ref')
            if not hasattr(self,'reading_snapshot'):
                self.reading_snapshot=self.ui.app.gateway.snapshot(self.object['content'],self.object['experiment_id'])
            if kind=='worked_example':
                branch,rows=example_rows(block,self.reading_snapshot,ref,human_units=True)
                caption=block['caption']+'（'+branch['condition']+'）';columns=['量','怎样读或算','数值']
            else:
                rows=branch_rows(block,self.reading_snapshot,ref);caption=block['caption'];columns=['怎样判断','这一情形能读什么','接下来怎样处理']
            block={**block,'type':'table','columns':columns,'rows':rows,'caption':caption};kind='table'
        if kind=='measurement_table':
            from .measurement_tables import measurement_table
            from .format_program import Snapshot
            ref=block['workbook_ref'];store=self.ui.app.store
            try:
                snapshot=Snapshot({(ref['id'],ref['revision']):store.object(ref['id'],ref['revision'])},
                                  {a:store.anchor(a) for a in block['sources']},{},{},None)
                block=measurement_table(block,snapshot);kind='table'
            except (ValueError,KeyError):
                self.content.addWidget(label('此表的固定记录暂不可读；可阅读已生成的PDF。','muted'))
                self.reference(ref,'固定测量记录');return
        if kind=="paragraph":
            from .native_markdown import MarkdownView
            self.content.addWidget(MarkdownView(block['text'],self.ui.app.store,strict=False))
        elif kind=='markdown':
            from .native_markdown import MarkdownView
            self.content.addWidget(MarkdownView(block['text'],self.ui.app.store))
        elif kind=='columns':
            for column in block['columns']:
                for child in column['blocks']:self.block(child)
        elif kind in ('pagebreak','space'):
            self.content.addSpacing(18)
        elif kind in ('goal_summary','procedure'):
            self.content.addWidget(label('测量目标与步骤见上方内容和完整 PDF。','muted'))
        elif kind=="equation":
            self.content.addWidget(MathLabel(block["latex"]))
            self.equation_number+=1
            descriptor=(block.get("label") or str(self.equation_number))+"　"+block.get("symbols","")
            self.content.addWidget(self.prose_label(descriptor))
            if block.get("formula_ref"):self.reference(block["formula_ref"],"固定公式")
        elif kind=="table":
            from .native_markdown import MarkdownView
            from .documents import markdown_table
            self.content.addWidget(self.prose_label(block["caption"],"h3"))
            self.content.addWidget(MarkdownView(markdown_table(block["columns"],block["rows"]),self.ui.app.store,strict=False))
        elif kind=="figure":
            anchor=self.ui.app.store.anchor(block["source_anchor"])
            page=self.ui.app.store.one("SELECT image FROM pages WHERE source_id=? AND number=?",(anchor["source_id"],anchor["page"]))
            if page and page["image"]:
                image=ImageView(self.ui.app.store.asset_path(page["image"]),anchor.get("region"));image.setMinimumHeight(430);self.content.addWidget(image)
            self.content.addWidget(self.prose_label(block["caption"]))
        elif kind=="code":self.content.addWidget(plain(block["text"],True,180))
        elif kind=="crossref":self.content.addWidget(button(block["text"]+" ↓",lambda:self.scroll.ensureWidgetVisible(self.targets[block["target"]],0,15)))
        local=block.get("sources",[]) if visible_sources is None else visible_sources
        if local:self.citations(local)
    def markdown(self,value):
        from .document_composition import nodes
        def rich(node):
            output=[]
            for child in node.children or []:
                if child.type in ('math_inline','math_block','math_block_label'):
                    if output:self.content.addWidget(label(''.join(output)));output=[]
                    self.content.addWidget(MathLabel(child.content))
                elif child.type=='image':
                    anchor=self.ui.app.store.anchor(child.attrs['src'][7:]);page=self.ui.app.store.one('SELECT image FROM pages WHERE source_id=? AND number=?',(anchor['source_id'],anchor['page']))
                    self.content.addWidget(ImageView(self.ui.app.store.asset_path(page['image']),anchor.get('region')))
                elif child.children:
                    if output:self.content.addWidget(label(''.join(output)));output=[]
                    rich(child)
                elif child.type in ('text','code_inline'):output.append(child.content)
                elif child.type in ('softbreak','hardbreak'):output.append('\n')
            if output:self.content.addWidget(label(''.join(output)))
        for node in nodes(value):
            if node.type in ('math_block','math_block_label'):self.content.addWidget(MathLabel(node.content))
            elif node.type in ('fence','code_block'):self.content.addWidget(plain(node.content,True,180))
            else:rich(node)
    def render(self):
        self.equation_number=0
        if hasattr(self,'reading_snapshot'):del self.reading_snapshot
        obj=self.object;c=obj["content"];kind=obj["kind"]
        self.heading(obj["title"])
        if not obj["experiment_id"]:
            card=self.ui.app.store.one("SELECT summary FROM kb_cards WHERE object_id=? AND revision=?",(obj["id"],obj["revision"]))
            if card and card["summary"]:self.content.addWidget(self.prose_label(card["summary"],"muted"))
            from .knowledge_notes import for_revision, describe
            notes=for_revision(self.ui.app.store,obj["id"],obj["revision"])
            if notes:self.heading("答疑时的核对备注")
            for note in notes:
                self.content.addWidget(self.prose_label(describe(note)))
                self.content.addWidget(button(f"{note['name']} · 第 {note['page']} 页",
                    lambda checked=False,n=note:self.ui.open_source(n['source_id'],n['page'],None)))
        if c.get("simulation"):
            note=label("教学模拟 不是真实课程测量或实测结论");note.setProperty("warning",True);self.content.addWidget(note)
        if obj["package_state"]!="ready":self.content.addWidget(label("固定包不可用；当前为普通只读。原字节与历史仍保留。","warning"))
        if kind=='ExperimentDraft':
            from .experiment_drafts import outline_markdown
            self.markdown(outline_markdown(c,include_title=False))
        elif kind=="LearningDocument":self.document(c)
        elif kind=="Formula":
            self.content.addWidget(MathLabel(c["latex"]));self.content.addWidget(label("核对状态："+STATUS[c["verification"]]))
            self.content.addWidget(table(["符号","物理量","单位","维度","形状"],[[s[k] for k in ("symbol","quantity","unit","dimension","shape")] for s in c["symbols"]]))
            self.heading("条件与近似");self.content.addWidget(label("；".join(c["conditions"]+c["approximations"])))
        elif kind=="SciencePoint":
            self.heading("知识要点")
            self.content.addWidget(self.prose_label(c["definition"]))
            for claim in c["claims"]:
                if claim["text"]==c["definition"] and claim["status"]=="source_supported" and set(claim["sources"])==set(obj["sources"]):
                    continue
                heading,sep,body=claim["text"].partition("\n")
                if sep:self.heading(heading,level=2)
                self.content.addWidget(self.prose_label(body if sep else heading))
                if claim["status"]!="source_supported":self.content.addWidget(label(STATUS.get(claim["status"],claim["status"])))
                self.citations(claim["sources"])
            self.heading("适用条件")
            self.content.addWidget(self.prose_label("\n".join("• "+s for s in c["conditions"])))
            self.heading("可观测量")
            self.content.addWidget(self.prose_label("；".join(c["observables"])))
            for reference in c["formula_refs"]:self.reference(reference,"公式")
            self.related(obj["id"])
        elif kind=="MathFoundation":
            for title,value in [("定义",c["definition"]),("方法",c["method"]),("成立条件","\n".join(c["conditions"])),("符号约定","；".join(c["symbols"])),("推导","\n".join(c["derivation"]))]:self.heading(title);self.content.addWidget(label(value))
            self.related(obj["id"])
        elif kind=="MathUse":
            self.reference(c["science_ref"],"理学知识");self.reference(c["math_ref"],"数学基础")
            self.content.addWidget(table(["数学符号","物理量","单位"],[[r["math"],r["physical"],r["unit"]] for r in c["symbol_mapping"]]))
            self.heading("应用假设");self.content.addWidget(label("\n".join(c["assumptions"])));self.heading("逐步推导")
            for i,step in enumerate(c["steps"]):self.content.addWidget(label(f"{i+1}．{step}"))
        elif kind=="KnowledgeRelation":
            self.reference(c["from_ref"],"起点");self.content.addWidget(label("→ "+c["relation"]+" →  ["+STATUS[c["status"]]+"]"));self.reference(c["to_ref"],"终点");self.content.addWidget(label("；".join(c["conditions"])))
        elif kind=="AnalysisProgram":self.program(c)
        elif kind=="AnalysisResult":self.result(c)
        elif kind=="DataWorkbook":self.workbook(c)
        else:
            for field in obj.get("semantic_view",{}).get("fields",[]):self.heading(field["label"]);self.content.addWidget(label(self.pretty(field["values"])))
        if c.get("conflicts"):
            self.heading("来源条件差异 保留待选择")
            for conflict in c["conflicts"]:
                self.content.addWidget(table(["原有约定","新来源约定"],[[self.pretty(conflict["existing"]),self.pretty(conflict["incoming"])]]));self.citations(conflict["incoming_sources"])
        if c.get("knowledge_refs"):
            self.heading("本稿引用的知识修订")
            for reference in c["knowledge_refs"]:self.reference(reference,"知识依据")
        if c.get('parent_document'):
            self.heading('完整原稿');self.reference(c['parent_document'],'原预习与实验单')
        basis=obj.get('draft_basis') or c.get('draft_ref')
        if basis:
            self.heading('依据的实验草稿');self.reference(basis,'实验主线与诠释安排')
        self.heading("原页出处" if obj["experiment_id"] is None else "来源与正式交付");self.citations(obj["sources"])
        pack=self.ui.app.registry.pinned(obj["pack_id"],obj["pack_version"],obj["pack_digest"])
        package_export=False
        for entry in (pack or {}).get("ui",[]):
            if entry["location"]=="artifact" and entry["object_type"]==obj["kind"] and entry["activity"]=="export":
                preferred=next((a for a in obj["assets"] if a["name"]=="document.docx"),None)
                if preferred:
                    control=button(entry["label"],lambda checked=False,a=preferred:self.ui.safe(lambda:export_file(self,self.ui.app.store.asset_path(a["path"]),obj["title"]+".docx")),True)
                    control.setToolTip(entry["description"])
                    if entry.get("icon_available"):control.setIcon(G.QIcon(str(Path(pack["directory"])/entry["icon"])))
                    self.content.addWidget(control);package_export=True
        for asset in obj["assets"]:
            if package_export and asset["name"]=="document.docx":continue
            self.content.addWidget(button("下载可编辑 Word" if asset["name"]=="document.docx" else "导出 "+asset["name"],lambda checked=False,a=asset:self.ui.safe(lambda:export_file(self,self.ui.app.store.asset_path(a["path"]),obj["title"]+".docx" if a["name"]=="document.docx" else a["name"]))))
        self.content.addWidget(label(f"格式 {obj['pack_id']} / {obj['pack_version']} · 数据版本 {obj['data_version']} · 摘要 {obj['pack_digest'][:20]} · 修订 {obj['revision']}","muted"));self.content.addStretch()
        self.navigator.setVisible(self.navigator.count()>0)
    def workbook(self,c):
        self.content.addWidget(label("原始读数完整保存；空缺、待确认与用户明确确认分开。原始备注不会自动删除任何采集数据。","muted"))
        for section in dict.fromkeys(r["section"] for r in c["records"]):
            self.heading(section)
            rows=[]
            for r in c["records"]:
                if r["section"]!=section:continue
                for i,value in enumerate(r["values"]):rows.append([r["quantity"] or "待确认",i+1,value,r["unit"] or "待确认",{"recorded":"原文明确","pending":"待确认","inferred":"上下文推断","confirmed":"用户已确认"}.get(r["status"],r["status"]),r["source_line"],r.get("note","")])
            self.content.addWidget(table(["物理量","序号","原始读数","单位","状态","原文行","备注"],rows))
        self.heading("原始观察与操作备注")
        for observation in c["observations"]:self.content.addWidget(label(observation["section"]+" · "+observation["text"]+"（原文第"+str(observation["source_line"])+"行）"))
        self.heading("待确认事项与真实答复")
        for issue in c["issues"]:self.content.addWidget(label(issue["question"]+"\n"+{"open":"等待确认","answered":"已答复","unknown":"未记录，继续保留未知","partial":"部分已明确，其余仍未知"}.get(issue["status"],issue["status"])+("："+issue.get("answer","") if issue.get("answer") else "")))
        self.heading("完整原始记录")
        self.content.addWidget(plain("\n".join(str(r["number"])+"  "+r["text"] for r in c["original_lines"]),True,300))
    def document(self,c):
        self.content.addWidget(label(c["summary"]));bg=c["background"]
        time_hint=('　'+str(bg['minutes'])+' 分钟') if bg.get('minutes') is not None else ''
        self.content.addWidget(label(f"背景：{bg['level']}　目标：{bg['goal']}"+time_hint,"muted"))
        for warning in c["warnings"]:
            w=label(warning);w.setProperty("warning",True);self.content.addWidget(w)
        if c.get("goals"):
            self.heading("实验目标与观察记录" if any(g.get('kind')=='qualitative' for g in c['goals']) else "目标量到必需读数","goals")
            labels=goal_labels(c['goals'])
            fixed=self.ui.app.registry.pinned(self.object["pack_id"],self.object["pack_version"],self.object["pack_digest"])
            config=json.loads((Path(fixed["directory"])/"template.json").read_text(encoding="utf-8")) if fixed else {};shown=set()
            for goal in c["goals"]:
                if goal.get('kind')=='qualitative':
                    self.content.addWidget(self.prose_label(labels[goal['id']]+'　'+goal['objective']+'（定性观察）','h3'))
                    if goal.get('model'):self.content.addWidget(self.prose_label(goal['model']))
                    self.content.addWidget(self.prose_label('观察条件：'+'；'.join(goal['conditions'])))
                    from .native_markdown import MarkdownView
                    self.content.addWidget(MarkdownView(goal['observation_plan'],self.ui.app.store,strict=False));self.citations(goal['sources']);continue
                self.content.addWidget(self.prose_label(labels[goal["id"]]+"　"+goal["quantity"]+" / "+display_unit(goal["unit"]),"h3"));self.content.addWidget(self.prose_label(goal["model"]+"；条件："+"；".join(goal["conditions"])))
                self.reference(goal["formula_ref"],"计算公式")
                key=(goal["formula_ref"]["id"],goal["formula_ref"]["revision"])
                if config.get("show_goal_equations") and key not in shown:
                    formula=self.ui.app.gateway.read(*key)["content"];self.content.addWidget(MathLabel(formula["latex"]));shown.add(key)
                keys=["quantity","unit","timing","condition","instrument"]
                headings=["直接读数","单位","时机","条件","仪器"]
                if any(m.get('precision') for m in goal['measurements']):keys.append('precision');headings.append('精度')
                self.content.addWidget(table(headings,[[display_unit(m.get(k,'')) if k=='unit' else m.get(k,'') for k in keys] for m in goal["measurements"]]));self.citations(goal["sources"])
        if c.get("steps"):
            self.heading("顺序 理由与完成判据","steps")
            labels=goal_labels(c.get('goals',[]))
            for index,step in enumerate(c["steps"],1):
                self.content.addWidget(self.prose_label(str(index)+"　"+step["action"],"h3"))
                self.content.addWidget(self.prose_label("理由："+step["reason"]+"\n条件："+step["conditions"]+"\n完成判据："+step["completion"]+"\n对应目标："+"、".join(labels[g] for g in step["goal_ids"])))
                self.content.addWidget(button("直接打开仪器操作附录 ↓",lambda:self.scroll.ensureWidgetVisible(self.targets["instruments"],0,15)));self.citations(step["sources"])
        for section in c["sections"]+c["appendices"]:
            self.heading(section["title"],section["id"],section.get('heading_level',1))
            blocks=section["blocks"]
            if self.object['data_version']>=7:
                from .source_citations import Citations
                from .reading_design import citation_groups
                visible=citation_groups(blocks,Citations(c['sources'],self.ui.app.store))
                for block,refs in zip(blocks,visible):self.block(block,refs)
            else:
                for block in blocks:self.block(block)
    def program(self,c):
        self.content.addWidget(label("固定教学模拟代码，未由实时模型生成。" if "固定示例" in c["title"] else "针对任务保存的分析代码；运行成功后仍需核验物理结论。","muted"))
        self.content.addWidget(table(["步骤","操作","假设","输出"],[[s["id"],s["operation"],self.pretty(s["assumptions"]),self.pretty(s["outputs"])] for s in c["steps"]]))
        self.heading("公式到代码映射")
        for mapping in c["formula_map"]:
            self.content.addWidget(label(f"{mapping['step_id']} · 代码行 {mapping['code_lines']} · 输入列 {mapping['input_columns']} · 单位 {mapping['units']}"));self.reference(mapping["formula_ref"],"公式")
        source=plain("\n".join(f"{i+1:3}  {line}" for i,line in enumerate(c["code"].splitlines())),True,340);source.setFont(self.ui.code_font);self.content.addWidget(source)
        self.heading("运行参数");self.parameters={}
        for key,value in c["parameters"].items():
            field=W.QLineEdit(json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list,bool)) or value is None else str(value));self.parameters[key]=(field,value);self.content.addWidget(row(label(key),field))
        self.content.addWidget(button("实际隔离运行",self.run,True));self.content.addWidget(label("零网络能力 AppContainer · 每次生成新运行 · 输入只读 · 不继承 API 凭据","muted"))
    def result(self,c):
        info=c["run_info"];self.content.addWidget(label(STATUS.get(c["status"],c["status"])+f" · 退出码 {info.get('exit_code')} · {info.get('elapsed_seconds')} 秒"))
        self.content.addWidget(label(info.get("backend","")));self.content.addWidget(label(c["verification"].get("note","结果待独立核验"),"warning"))
        if info.get("result"):self.content.addWidget(table(["结果","值"],[[k,self.pretty(v)] for k,v in info["result"].items()]))
        for asset in self.object["assets"]:
            if asset["name"].endswith(".png"):
                image=ImageView(self.ui.app.store.asset_path(asset["path"]));image.setMinimumHeight(560);self.content.addWidget(image)
        self.heading("原始 stdout");self.content.addWidget(plain(info.get("stdout",""),True,160));self.heading("原始 stderr");self.content.addWidget(plain(info.get("stderr") or "无 stderr 输出",True,140))
    def related(self,identity):
        from .knowledge_index import related_to
        found=related_to(self.ui.app.store,[identity])
        if found:self.heading("数学应用与具名关系")
        for obj in found:self.content.addWidget(button(KINDS[obj["kind"]]+" · "+obj["title"],lambda checked=False,o=obj:self.ui.open_object(o["id"],o["revision"])))
    def pretty(self,value):
        if isinstance(value,list):return "；".join(self.pretty(v) for v in value)
        if isinstance(value,dict):return "；".join(str(k)+"："+self.pretty(v) for k,v in value.items())
        return str(value)
    def run(self):
        parameters={}
        try:
            for key,(field,old) in self.parameters.items():
                if isinstance(old,(dict,list,bool)) or old is None:parameters[key]=json.loads(field.text())
                elif isinstance(old,int):parameters[key]=int(field.text())
                elif isinstance(old,float):parameters[key]=float(field.text())
                else:parameters[key]=field.text()
        except ValueError:self.ui.notify("运行参数需要有效数值或 JSON 对象／数组。");return
        result=self.ui.safe(lambda:self.ui.app.analysis.queue(self.object["experiment_id"],self.identity,self.object["revision"],parameters))
        if result:self.ui.notify("隔离运行已启动，将保留输入、参数、图和原始错误。 ");self.ui.refresh_session();self.accept()
    def change_version(self):
        revision=self.versions.currentData()
        if revision==self.object["revision"]:return
        identity=self.identity;self.accept();self.ui.open_object(identity,revision)
    def upgrade(self):
        plan=self.ui.safe(lambda:self.ui.app.gateway.plan_upgrade(self.identity))
        if not plan:return
        message=f"{plan['old_version']} → {plan['target_version']}\n{plan['reason']}\n人工修改：{'有，将保留原内容' if plan['human_modified'] else '未记录'}"
        if not plan["can_upgrade"]:W.QMessageBox.information(self,"更新版本",message);return
        if W.QMessageBox.question(self,"确认固定升级方案",message+"\n按此方案重生成新修订？")!=W.QMessageBox.Yes:return
        self.ui.submit("按包升级",lambda:self.ui.app.gateway.upgrade(self.identity,plan,True),lambda obj:(self.accept(),self.ui.open_object(obj["id"])))
    def edit(self):EditDialog(self.ui,self.object).exec();self.ui.refresh()


class EditDialog(W.QDialog):
    def __init__(self,ui,obj):
        super().__init__(ui);self.ui=ui;self.obj=obj;self.draft=copy.deepcopy(obj["content"]);self.fields=[]
        self.setWindowTitle("编辑并另存修订");self.resize(850,780);layout=W.QVBoxLayout(self);scroll,_,body=scroll_body();layout.addWidget(scroll)
        self.title=W.QLineEdit(self.draft["title"]);body.addWidget(label("标题"));body.addWidget(self.title)
        if obj["kind"]=="LearningDocument":
            body.addWidget(label("摘要"));self.summary=plain(self.draft["summary"]);body.addWidget(self.summary)
            for section in self.draft["sections"]+self.draft["appendices"]:
                body.addWidget(label(section["title"],"h2"))
                def editable(blocks):
                    for item in blocks:
                        yield item
                        if item['type']=='columns':
                            for column in item['columns']:yield from editable(column['blocks'])
                for block in editable(section["blocks"]):
                    if block["type"] in ("paragraph","markdown"):
                        field=plain(block["text"]);body.addWidget(field);self.fields.append((block,"text",field))
        elif obj["kind"]=="AnalysisProgram":
            field=plain(self.draft["code"],height=500);field.setFont(ui.code_font);body.addWidget(field);self.fields.append((self.draft,"code",field))
        elif obj["kind"]=="SciencePoint":
            field=plain(self.draft["definition"]);body.addWidget(field);self.fields.append((self.draft,"definition",field))
            body.addWidget(label("目录归属 可多选，稳定 ID 不改变"));self.categories=W.QListWidget();self.categories.setSelectionMode(W.QAbstractItemView.MultiSelection)
            for cat in ui.app.state()["categories"]:
                item=W.QListWidgetItem(cat["domain"]+" / "+cat["name"]);item.setData(C.Qt.UserRole,cat["id"]);self.categories.addItem(item);item.setSelected(cat["id"] in self.draft.get("category_ids",[]))
            body.addWidget(self.categories)
        layout.addWidget(label("提交仍经过包内程序；基线冲突、公式不支持或生成失败时，旧修订保持可读。","muted"));layout.addWidget(button("保存新修订",self.save,True))
    def save(self):
        self.draft["title"]=self.title.text()
        if self.obj["kind"]=="LearningDocument":self.draft["summary"]=self.summary.toPlainText()
        for owner,key,field in self.fields:owner[key]=field.toPlainText()
        if self.obj["kind"]=="SciencePoint":
            self.draft["category_ids"]=[i.data(C.Qt.UserRole) for i in self.categories.selectedItems()]
            if self.draft["definition"]!=self.obj["content"]["definition"]:self.draft["claims"].append({"text":self.draft["definition"],"sources":self.draft["sources"],"status":"pending"})
        self.ui.submit("保存人工修订",lambda:self.ui.app.gateway.apply(self.obj["kind"],self.draft,experiment_id=self.obj["experiment_id"],object_id=self.obj["id"],base_revision=self.obj["revision"],human=True),lambda result:(self.accept(),self.ui.notify("新修订已保存，旧版可从历史列表找回。")))
