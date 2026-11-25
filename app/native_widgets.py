"""Native Qt controls. No web view, browser, HTML document or JavaScript UI."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import shutil

from PySide6 import QtCore as C, QtGui as G, QtWidgets as W


KINDS={"ExperimentDraft":"实验草稿","SciencePoint":"理学知识","MathFoundation":"数学基础","Formula":"公式","MathUse":"数学应用","KnowledgeRelation":"具名关系","LearningDocument":"学习文档","AnalysisProgram":"分析程序","AnalysisResult":"运行结果","DataWorkbook":"规范数据表"}
KINDS["ExperimentWorksheet"]="交互数据处理台"
STATUS={"queued":"等待中","running":"进行中","waiting_user":"等待你的答复","succeeded":"已完成","failed":"失败","cancelled":"已取消","interrupted":"已中断","timeout":"超时","pending":"待核验","source_supported":"来源支持","inferred":"推断","unverified":"待核对","verified":"已核对","ocr_uncertain":"识别存疑"}


def label(text="",role=None):
    from .scientific_markup import parts
    from .worksheet_presentation import scientific_text
    rich = any(math for math, _ in parts(str(text)))
    widget=W.QLabel(scientific_text(str(text),True) if rich else str(text))
    widget.setTextFormat(C.Qt.RichText if rich else C.Qt.PlainText)
    widget.setWordWrap(True);widget.setTextInteractionFlags(C.Qt.TextSelectableByMouse)
    if role:widget.setProperty("role",role)
    return widget


def button(text,callback=None,primary=False):
    widget=W.QPushButton(text);widget.setCursor(C.Qt.PointingHandCursor)
    if primary:widget.setProperty("primary",True)
    if callback:widget.clicked.connect(callback)
    return widget


def row(*widgets):
    out=W.QWidget();layout=W.QHBoxLayout(out);layout.setContentsMargins(0,0,0,0);layout.setSpacing(10)
    for widget in widgets:
        if isinstance(widget,str):widget=label(widget)
        layout.addWidget(widget)
    return out


def clear(layout):
    while layout.count():
        item=layout.takeAt(0)
        if item.widget():item.widget().deleteLater()
        elif item.layout():clear(item.layout())


def scroll_body():
    scroll=W.QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(W.QFrame.NoFrame)
    body=W.QWidget();layout=W.QVBoxLayout(body);layout.setSpacing(13);layout.setContentsMargins(18,14,18,18)
    scroll.setWidget(body)
    return scroll,body,layout


def table(columns,rows):
    widget=W.QTableWidget(len(rows),len(columns));widget.setHorizontalHeaderLabels([str(c) for c in columns])
    widget.setEditTriggers(W.QAbstractItemView.NoEditTriggers);widget.setSelectionBehavior(W.QAbstractItemView.SelectRows)
    widget.setAlternatingRowColors(True);widget.verticalHeader().hide();widget.setWordWrap(True)
    for i,values in enumerate(rows):
        for j,value in enumerate(values):widget.setItem(i,j,W.QTableWidgetItem(str(value)))
    widget.horizontalHeader().setSectionResizeMode(W.QHeaderView.Stretch);widget.resizeRowsToContents()
    height=widget.horizontalHeader().height()+sum(widget.rowHeight(i) for i in range(widget.rowCount()))+4
    widget.setMinimumHeight(min(390,max(75,height)));widget.setMaximumHeight(min(430,max(95,height+10)))
    return widget


class DataModel(C.QAbstractTableModel):
    def __init__(self,columns,rows,parent=None):
        super().__init__(parent);self.columns=columns;self.rows=rows
    def rowCount(self,parent=C.QModelIndex()):return 0 if parent.isValid() else len(self.rows)
    def columnCount(self,parent=C.QModelIndex()):return 0 if parent.isValid() else len(self.columns)
    def data(self,index,role=C.Qt.DisplayRole):
        if not index.isValid() or role not in (C.Qt.DisplayRole,C.Qt.ToolTipRole):return None
        record=self.rows[index.row()];value=record.get(self.columns[index.column()],"") if isinstance(record,dict) else record[index.column()]
        return str(value)
    def headerData(self,section,orientation,role=C.Qt.DisplayRole):
        if role!=C.Qt.DisplayRole:return None
        return str(self.columns[section]) if orientation==C.Qt.Horizontal else str(section+1)


def dataset_table(columns,rows):
    view=W.QTableView();model=DataModel(columns,rows,view);view.setModel(model)
    view.setAlternatingRowColors(True);view.setSelectionMode(W.QAbstractItemView.ExtendedSelection)
    view.setWordWrap(False);view.horizontalHeader().setSectionResizeMode(W.QHeaderView.Interactive)
    view.horizontalHeader().setStretchLastSection(True);view.setMinimumHeight(230)
    for index in range(len(columns)):view.setColumnWidth(index,190 if "time" in str(columns[index]).lower() else 150)
    def copy_cells():
        selected=view.selectionModel().selectedIndexes()
        if not selected:return
        positions={(i.row(),i.column()):i.data() for i in selected}
        first_row,last_row=min(r for r,c in positions),max(r for r,c in positions)
        first_col,last_col=min(c for r,c in positions),max(c for r,c in positions)
        W.QApplication.clipboard().setText("\n".join("\t".join(positions.get((r,c),"") for c in range(first_col,last_col+1)) for r in range(first_row,last_row+1)))
    action=G.QAction("复制所选数据",view);action.setShortcut(G.QKeySequence.Copy);action.triggered.connect(copy_cells);view.addAction(action);view.setContextMenuPolicy(C.Qt.ActionsContextMenu)
    return view


def show_dataset(parent,title,columns,rows,mapping=None):
    dialog=W.QDialog(parent);dialog.setWindowTitle(title);dialog.resize(1150,780);layout=W.QVBoxLayout(dialog)
    layout.addWidget(label(f"完整数据 · {len(rows)} 行 · {len(columns)} 列 · 可滚动到末行，Ctrl+C 复制选区","muted"))
    if mapping:layout.addWidget(label("；".join(k+"："+v["quantity"]+" / "+v["unit"] for k,v in mapping.items()),"muted"))
    grid=dataset_table(columns,rows);layout.addWidget(grid,1)
    jump=W.QSpinBox();jump.setRange(1,max(1,len(rows)))
    def locate():grid.scrollTo(grid.model().index(jump.value()-1,0),W.QAbstractItemView.PositionAtCenter)
    layout.addWidget(row(label("定位行"),jump,button("跳转",locate),button("关闭",dialog.accept)));dialog.exec()


def plain(text="",readonly=False,height=100):
    widget=W.QPlainTextEdit(str(text));widget.setReadOnly(readonly);widget.setMinimumHeight(height)
    if readonly:widget.setMaximumHeight(max(height,300))
    return widget


def text(widget):
    return widget.toPlainText() if isinstance(widget,W.QPlainTextEdit) else widget.text()


def lines(value):return [v.strip() for v in value.replace("；","\n").splitlines() if v.strip()]


class MathLabel(W.QScrollArea):
    def __init__(self,latex):
        super().__init__();self.setFrameShape(W.QFrame.NoFrame);self.setAlignment(C.Qt.AlignCenter);self.setMinimumHeight(65)
        inner=W.QLabel();inner.setTextFormat(C.Qt.PlainText);inner.setToolTip(latex);inner.setTextInteractionFlags(C.Qt.TextSelectableByMouse);self.setWidget(inner)
        try:
            from .math_display import raster
            size, image = raster(latex, 18)
            pixmap = G.QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(2)
            inner.setPixmap(pixmap)
            inner.adjustSize()
            self.setFixedHeight(min(450,max(65,size.height()+28)))
        except Exception:
            inner.setText("公式原文（本地显示构造尚不支持，未丢弃）：\n"+latex);inner.setWordWrap(True);inner.setFixedWidth(700);inner.adjustSize();self.setFixedHeight(min(400,max(80,inner.height()+20)))
            inner.setProperty("warning",True)


class ImageView(W.QScrollArea):
    def __init__(self,path,region=None):
        super().__init__();self.setWidgetResizable(True);self.setFrameShape(W.QFrame.NoFrame)
        self.image_label=W.QLabel();self.image_label.setAlignment(C.Qt.AlignTop|C.Qt.AlignHCenter)
        self.original=G.QPixmap(str(path))
        if region and not self.original.isNull():
            x0,y0,x1,y1=region;self.original=self.original.copy(int(x0*self.original.width()),int(y0*self.original.height()),int((x1-x0)*self.original.width()),int((y1-y0)*self.original.height()))
        self.setWidget(self.image_label)
        self.setMinimumSize(250,260)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.image_label.setPixmap(self.original.scaledToWidth(max(200,self.viewport().width()-12),C.Qt.SmoothTransformation))
    def change_image(self,path):
        self.original=G.QPixmap(str(path));self.image_label.setPixmap(self.original.scaledToWidth(max(200,self.viewport().width()-12),C.Qt.SmoothTransformation))


def export_file(parent,source,name):
    chosen,_=W.QFileDialog.getSaveFileName(parent,"保存交付文件",name,"所有文件 (*)")
    if chosen:
        destination=Path(chosen).resolve()
        if destination==Path(source).resolve():raise ValueError("导出位置不能覆盖正式资产原件")
        owner=getattr(parent,"ui",None)
        if owner and destination.is_relative_to(owner.app.store.authority):raise ValueError("请另选导出目录；资料库原件和正式修订由专用入口保存。")
        shutil.copyfile(source,destination)
        return destination


def export_bundle(parent,obj):
    chosen=W.QFileDialog.getExistingDirectory(parent,"选择保存完整成果的文件夹")
    if not chosen:return
    root=Path(chosen).resolve();ui=parent.ui
    if root.is_relative_to(ui.app.store.authority):
        raise ValueError("请另选导出目录；资料库原件和正式修订由专用入口保存。")
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]',"_",obj["title"]).strip(" .") or obj["kind"]
    # Keep long titles readable while retaining a unique, stable revision suffix.
    suffix=obj["id"]+"-r"+str(obj["revision"])
    target=root/(name if len(str(root/name/suffix))<190 else obj["kind"])/suffix
    if target.exists():raise ValueError("此版本的导出目录已存在，请选择其他文件夹，以保留已有编辑。")
    assets=[]
    for asset in obj["assets"]:
        source=ui.app.store.asset_path(asset["path"])
        if hashlib.sha256(source.read_bytes()).hexdigest()!=asset["sha"]:
            raise ValueError("正式资产摘要不符："+asset["name"])
        destination=(target/asset["name"]).resolve()
        if not destination.is_relative_to(target.resolve()):raise ValueError("资产路径越界")
        assets.append((source,destination))
    knowledge={};anchors=set(obj["sources"])
    def collect(value):
        if isinstance(value,dict):
            if isinstance(value.get("id"),str) and isinstance(value.get("revision"),int):
                key=(value["id"],value["revision"])
                if key not in knowledge:
                    fixed=ui.app.gateway.read(*key)
                    if fixed["experiment_id"] is None:
                        entry={k:fixed[k] for k in ("id","revision","kind","title","pack_id","pack_version","pack_digest","data_version","content")}
                        knowledge[key]=entry;anchors.update(fixed["sources"]);collect(fixed["content"])
            for child in value.values():collect(child)
        elif isinstance(value,list):
            for child in value:collect(child)
    collect(obj["content"])
    target.mkdir(parents=True)
    for source,destination in assets:
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
    envelope={k:obj[k] for k in ("id","revision","kind","title","pack_id","pack_version","pack_digest","data_version","content")}
    envelope["source_anchors"]=[ui.app.store.anchor(i) for i in sorted(anchors)]
    (target/"artifact.json").write_text(json.dumps(envelope,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (target/"fixed-knowledge.json").write_text(json.dumps(list(knowledge.values()),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    W.QMessageBox.information(parent,"完整成果已导出",str(target)+"\n\n已包含本版本的全部文件与固定版本、来源信息。")
    return target


class SourceDialog(W.QDialog):
    def __init__(self,ui,source_id,page=1,region=None):
        super().__init__(None);self.ui=ui;self.source=ui.app.source(source_id);self.region=region
        self.setWindowTitle(self.source["name"]);self.resize(1200,850)
        layout=W.QVBoxLayout(self);self.pages=W.QComboBox()
        for p in self.source["pages"]:self.pages.addItem(f"第 {p['number']} 页 · 印刷标签 {p['label']}",p["number"])
        self.pages.setCurrentIndex(max(0,page-1));self.pages.currentIndexChanged.connect(self.load_page)
        export=button("导出原件",self.export_original)
        def file_details():
            box=W.QMessageBox(self);box.setWindowTitle("文件详情");box.setText(self.source["name"])
            box.setInformativeText(self.source["kind"].upper()+" · "+str(len(self.source["pages"]))+" 页")
            box.setDetailedText("SHA-256\n"+self.source["sha"]);box.exec()
        controls=row(label("原页"),self.pages,export,button("文件详情",file_details))
        if self.source["kind"] in ("csv","tsv","xlsx","dat"):
            def full_table():
                preview=ui.app.materials.preview_dataset(source_id,{"table_id":str(self.pages.currentIndex())})
                show_dataset(self,self.source["name"],preview["columns"],preview["rows"])
            controls.layout().addWidget(button("查看整表",full_table))
        layout.addWidget(controls)
        split=W.QSplitter();layout.addWidget(split,1)
        self.image=ImageView("");split.addWidget(self.image)
        right=W.QWidget();rl=W.QVBoxLayout(right);self.warnings=label("", "warning");self.warnings.setProperty("warning",True)
        self.original_text=plain(readonly=True,height=220);self.original_text.setMaximumHeight(500)
        self.quote=plain(height=85);self.quote.setPlaceholderText("粘贴原文中的摘录以定位；留空以整页定位。图片公式保留待核对。")
        self.verified=W.QCheckBox("我已对照原页核对这个出处")
        for widget in [label("原文与核对","h2"),self.warnings,self.original_text,label("定位摘录"),self.quote,self.verified,button("用此出处录入知识",self.create_anchor,True)]:rl.addWidget(widget)
        rl.addStretch();split.addWidget(right);split.setSizes([650,500]);self.load_page()
    def load_page(self):
        p=self.source["pages"][self.pages.currentIndex()]
        self.original_text.setPlainText(p["text"] or "此页没有可提取文本，请核对原图。")
        self.warnings.setText("\n".join(p["warnings"]));self.quote.clear();self.verified.setChecked(False)
        self.image.setVisible(bool(p["image"]))
        if p["image"]:self.image.change_image(self.ui.app.store.asset_path(p["image"]))
    def create_anchor(self):
        def action():
            return self.ui.app.materials.make_anchor(self.source["id"],self.pages.currentData(),self.quote.toPlainText(),status="verified" if self.verified.isChecked() else "unverified")
        anchor=self.ui.safe(action)
        if anchor:KnowledgeDialog(self.ui,anchor).exec()
    def export_original(self):
        src=self.ui.app.store.one("SELECT original FROM sources WHERE id=?",(self.source["id"],))
        self.ui.safe(lambda:export_file(self,self.ui.app.store.asset_path(src["original"]),self.source["name"]))


class KnowledgeDialog(W.QDialog):
    def __init__(self,ui,anchor):
        super().__init__(ui);self.ui=ui;self.anchor=anchor;self.fields={}
        from .knowledge_index import search
        self.objects=search(ui.app.store);self.setWindowTitle("核对并录入知识");self.resize(800,820)
        layout=W.QVBoxLayout(self);layout.addWidget(label(f"出处：{anchor['name']} · 第 {anchor['page']} 页 · {STATUS.get(anchor['status'],anchor['status'])}"))
        self.kind=W.QComboBox()
        for key in list(KINDS)[:5]:self.kind.addItem(KINDS[key],key)
        layout.addWidget(self.kind);scroll,_,self.form_body=scroll_body();layout.addWidget(scroll)
        self.kind.currentIndexChanged.connect(self.rebuild);self.rebuild()
        layout.addWidget(button("通过知识格式包保存",self.save,True))
    def field(self,key,title,multiline=True,required=True):
        self.form_body.addWidget(label(title))
        widget=plain(height=65) if multiline else W.QLineEdit()
        self.form_body.addWidget(widget);self.fields[key]=widget
        return widget
    def select(self,key,title,kinds):
        self.form_body.addWidget(label(title));widget=W.QComboBox()
        widget.addItem("请选择",None)
        for obj in self.objects:
            if not kinds or obj["kind"] in kinds:widget.addItem(obj["title"]+f" · r{obj['revision']}",{"id":obj["id"],"revision":obj["revision"]})
        self.form_body.addWidget(widget);self.fields[key]=widget
    def rebuild(self):
        clear(self.form_body);self.fields={};kind=self.kind.currentData()
        self.field("title","标题",False)
        if kind in ("SciencePoint","MathFoundation"):self.field("definition","定义与物理或数学意义")
        if kind in ("SciencePoint","MathFoundation","Formula","KnowledgeRelation"):self.field("conditions","成立条件 每行一项")
        if kind=="SciencePoint":
            self.field("observables","可观测量及单位 每行一项")
            field=self.field("domains","学科或主题标签",False)
            field.setPlaceholderText("例如 光学，多个标签可用逗号分开")
        if kind=="MathFoundation":
            for key,title in [("method","定理或方法"),("symbols","数学符号约定"),("derivation","逐步推导")]:self.field(key,title)
        if kind=="Formula":
            self.field("latex","LaTeX 公式",False)
            symbols=self.field("symbols","符号表 每行：符号 | 物理量 | 单位 | 维度 | 形状")
            symbols.setPlaceholderText("F | 力 | N | M L T^-2 | scalar")
            self.field("approximations","近似与待确认事项")
        if kind in ("SciencePoint","MathUse"):
            self.form_body.addWidget(label("固定公式引用 可多选"));self.formulas=W.QListWidget();self.formulas.setMaximumHeight(130);self.formulas.setSelectionMode(W.QAbstractItemView.MultiSelection)
            for obj in self.objects:
                if obj["kind"]=="Formula":
                    item=W.QListWidgetItem(obj["title"]);item.setData(C.Qt.UserRole,{"id":obj["id"],"revision":obj["revision"]});self.formulas.addItem(item)
            self.form_body.addWidget(self.formulas)
        if kind=="MathUse":
            self.select("science_ref","理学知识",["SciencePoint"]);self.select("math_ref","数学基础",["MathFoundation"])
            for key,title in [("symbol_mapping","符号映射 每行：数学符号 | 物理量 | 单位"),("assumptions","实际应用假设"),("steps","推导步骤")]:self.field(key,title)
        if kind=="KnowledgeRelation":
            self.select("from_ref","起点",[]);self.select("to_ref","终点",[])
            self.relation=W.QComboBox();self.relation.addItems(["先修","推出","适用于","测量","误差来源"]);self.form_body.addWidget(self.relation)
            self.relation_status=W.QComboBox();self.relation_status.addItem("推断","inferred");self.relation_status.addItem("来源明确支持","source_supported");self.relation_status.addItem("待确认","pending");self.form_body.addWidget(self.relation_status)
        self.merge=W.QComboBox();self.merge.addItem("建立新对象 或自动归并相同内容",None)
        if kind in ("SciencePoint","MathFoundation","Formula","KnowledgeRelation"):
            for obj in self.objects:
                if obj["kind"]==kind:self.merge.addItem("归并到 "+obj["title"],obj["id"])
            self.form_body.addWidget(self.merge)
        self.form_body.addWidget(label("定义、条件和符号约定不同会保留冲突；本次读数、通道和仪器编号只属于实验。","muted"))
    def save(self):
        kind=self.kind.currentData();v={k:w.currentData() if isinstance(w,W.QComboBox) else text(w) for k,w in self.fields.items()}
        sources=[self.anchor["id"]];content={"title":v["title"],"sources":sources}
        if kind in ("SciencePoint","MathFoundation"):
            content.update(definition=v["definition"],conditions=lines(v["conditions"]),prerequisites=[],claims=[{"text":v["definition"],"sources":sources,"status":"source_supported"}])
        if kind=="SciencePoint":content.update(observables=lines(v["observables"]),formula_refs=[i.data(C.Qt.UserRole) for i in self.formulas.selectedItems()],measurement_relations=[],error_relations=[],domains=[d.strip() for d in v["domains"].replace("，",",").split(",") if d.strip()],category_ids=[],conflicts=[])
        if kind=="MathFoundation":content.update(method=v["method"],symbols=lines(v["symbols"]),derivation=lines(v["derivation"]))
        if kind=="Formula":
            content.update(latex=v["latex"],symbols=[dict(zip(["symbol","quantity","unit","dimension","shape"],[s.strip() for s in item.split("|")])) for item in lines(v["symbols"])],conditions=lines(v["conditions"]),approximations=lines(v["approximations"]),verification=self.anchor["status"])
        if kind=="MathUse":content.update(science_ref=v["science_ref"],math_ref=v["math_ref"],formula_refs=[i.data(C.Qt.UserRole) for i in self.formulas.selectedItems()],symbol_mapping=[dict(zip(["math","physical","unit"],[s.strip() for s in item.split("|")])) for item in lines(v["symbol_mapping"])],assumptions=lines(v["assumptions"]),steps=lines(v["steps"]))
        if kind=="KnowledgeRelation":content.update(from_ref=v["from_ref"],to_ref=v["to_ref"],relation=self.relation.currentText(),direction="from_to",conditions=lines(v["conditions"]),status=self.relation_status.currentData())
        result=self.ui.safe(lambda:self.ui.app.gateway.import_knowledge(kind,content,self.merge.currentData()))
        if result:self.ui.refresh();self.accept()
