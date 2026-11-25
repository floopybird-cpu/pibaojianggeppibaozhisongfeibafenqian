from __future__ import annotations

import json
from pathlib import Path
import threading

from PySide6 import QtCore as C, QtGui as G, QtWidgets as W, QtNetwork as N

from .native_widgets import KINDS,STATUS,label,button,row,plain,clear,SourceDialog
from .native_forms import SettingsDialog,ExperimentDialog,DatasetDialog,CategoryDialog
from .native_objects import ObjectDialog
from .storage import canonical,now,digest
from . import __version__
from .native_home import build_home, home_styles, small_button


STYLE="""
QWidget {font-family:'Microsoft YaHei UI','Segoe UI';font-size:13px;color:#294136;background:#f6f8f2;}
QMainWindow,QDialog {background:#f6f8f2;}
QLabel {background:transparent;line-height:1.7;}
QLabel[role='h1'] {font-size:27px;font-weight:650;color:#244333;}
QLabel[role='h2'] {font-size:19px;font-weight:620;color:#3d6247;margin-top:10px;margin-bottom:5px;}
QLabel[role='h3'] {font-size:15px;font-weight:600;margin-top:8px;}
QLabel[role='muted'] {font-size:13px;color:#526652;}
QLabel[warning='true'] {background:#f7efdc;color:#917239;padding:10px;border-left:3px solid #c5ae77;}
QPushButton {background:#fff;border:1px solid #d5e0ce;border-radius:7px;padding:8px 14px;min-height:21px;}
QPushButton:hover {background:#edf4e6;border-color:#a6bf99;}
QPushButton:focus {border:2px solid #427d57;}
QPushButton:disabled {color:#a9b1a1;background:#f0f3e9;}
QPushButton[primary='true'] {background:#285e4c;color:white;border-color:#285e4c;}
QPushButton[nav='true'] {text-align:left;background:transparent;border:0;padding:14px 16px;}
QPushButton[nav='true']:checked {background:#e3eddc;color:#285e4c;font-weight:650;}
QPushButton[depth='true'] {padding:6px 10px;min-width:35px;}
QPushButton[depth='true']:checked {background:#285e4c;color:white;border-color:#285e4c;}
QPushButton[citation='true'] {background:#edf4e4;color:#74935e;font-size:10px;padding:4px 8px;}
QLineEdit,QPlainTextEdit,QSpinBox,QComboBox {background:white;border:1px solid #cfdcc5;border-radius:6px;padding:7px;selection-background-color:#bdd3ab;selection-color:#244333;}
QLineEdit:focus,QPlainTextEdit:focus,QComboBox:focus {border:1px solid #75a06c;}
QListWidget,QTreeWidget,QTableWidget,QTableView {background:white;border:1px solid #dce5d3;border-radius:7px;alternate-background-color:#f2f6eb;selection-background-color:#e0ecd6;selection-color:#294f30;gridline-color:#e1e9d8;}
QListWidget::item {padding:9px 10px;}
QTreeWidget::item {padding:6px;}
QTreeWidget#knowledgeTree,QTableWidget {font-size:14px;}
QHeaderView::section:horizontal {padding:9px 7px;}
QHeaderView::section {background:#eaf1e2;color:#365343;font-weight:600;border:0;border-bottom:1px solid #d7e2cc;padding:9px;text-align:left;}
QGroupBox {border:1px solid #dce6d2;border-radius:9px;margin-top:10px;padding:12px;}
QGroupBox::title {subcontrol-origin:margin;left:15px;padding:0 7px;color:#678556;}
QScrollArea,QSplitter {border:0;background:transparent;}
QSplitter::handle {background:#e3e9dc;}
QTabWidget::pane {border:1px solid #d9e5ce;}
QTabBar::tab {background:#edf3e6;padding:10px 20px;border:0;}
QTabBar::tab:selected {background:#dceacc;color:#285e4c;}
QProgressBar {border:0;border-radius:4px;background:#e4ecdc;min-height:7px;text-align:center;font-size:10px;}
QProgressBar::chunk {background:#85a571;border-radius:4px;}
QStatusBar {background:#ecf2e5;color:#829676;font-size:11px;}
QToolTip {background:white;color:#3b633a;border:1px solid #becfae;padding:7px;}
"""


STYLE += home_styles()


class Dispatch(C.QObject):
    finished=C.Signal(object,object,object)


class MainWindow(W.QMainWindow):
    def __init__(self,app):
        super().__init__();self.app=app;self.current_exp=None;self.state={};self.last_fingerprint="";self.session_fingerprint="";self.working=0
        self.reader_windows=set()
        self.worksheet_pending={}
        from . import __version__
        self.setWindowTitle("物理实验助手 "+__version__);self.resize(1440,950);self.setMinimumSize(720,560)
        self.code_font=G.QFont("Consolas",10);self.dispatch=Dispatch(self);self.dispatch.finished.connect(self.received)
        central=W.QWidget();self.setCentralWidget(central);root=W.QHBoxLayout(central);root.setContentsMargins(0,0,0,0);root.setSpacing(0)
        sidebar=W.QWidget();sidebar.setObjectName("sidebar");sidebar.setFixedWidth(170)
        side=W.QVBoxLayout(sidebar);side.setContentsMargins(14,22,14,20);side.setSpacing(7)
        side.addWidget(label("物理实验助手","h2"));side.addSpacing(24)
        self.nav_buttons={};self.stack=W.QStackedWidget();nav=W.QButtonGroup(self);nav.setExclusive(True)
        for key,title in [("experiments","我的实验"),("library","资料与知识")]:
            b=button(title,lambda checked=False,k=key:self.switch(k));b.setCheckable(True);b.setProperty("nav",True);nav.addButton(b);side.addWidget(b);self.nav_buttons[key]=b
        self.questions_button=small_button("待回答的问题",lambda:self.open_questions(True));side.addWidget(self.questions_button)
        side.addStretch();side.addWidget(small_button("操作说明",self.open_manual));side.addWidget(small_button("设置",lambda:SettingsDialog(self).exec()))
        self.jobs=None
        root.addWidget(sidebar);root.addWidget(self.stack,1)
        self.pages={}
        self.build_experiments();self.build_library();self.build_knowledge();build_home(self)
        self.status=self.statusBar();self.status.setSizeGripEnabled(False)
        self.refresh();self.switch("experiments")
        last=app.store.one("SELECT value FROM meta WHERE key='last_native_experiment'")
        if last and app.store.one("SELECT id FROM experiments WHERE id=?",(last["value"],)):self.open_experiment(last["value"])
        self.timer=C.QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(1800)
        self.ipc=N.QLocalServer(self);self.ipc_name="PhysicsHarness."+digest(str(app.store.root))[:24]
        N.QLocalServer.removeServer(self.ipc_name);self.ipc.listen(self.ipc_name);self.ipc.newConnection.connect(self.activate_instance)
    def open_manual(self):
        path=Path(__file__).resolve().parents[1]/"docs"/"物理实验助手操作说明.pdf"
        if not path.is_file():
            local = C.QSettings(str(self.app.store.authority / "manual.ini"), C.QSettings.IniFormat)
            saved = local.value("pdf", "", type=str)
            if saved and Path(saved).is_file():
                path = Path(saved)
            else:
                selected, _ = W.QFileDialog.getOpenFileName(self, "从已解压的使用手册包选择 PDF", "", "PDF 使用手册 (*.pdf)")
                if not selected:
                    return
                path = Path(selected)
                local.setValue("pdf", str(path))
        if not G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(path))):
            self.notify("系统未能打开 PDF，请用本机的 PDF 阅读器打开所选文件。")

    def activate_instance(self):
        socket=self.ipc.nextPendingConnection()
        if socket:socket.disconnectFromServer();socket.deleteLater()
        self.showNormal();self.raise_();self.activateWindow()
    def page(self,key,title,subtitle):
        widget=W.QWidget();layout=W.QVBoxLayout(widget);layout.setContentsMargins(30,28,30,25);layout.setSpacing(17)
        layout.addWidget(label(title,"h1"))
        if subtitle:layout.addWidget(label(subtitle,"muted"))
        self.pages[key]=widget;self.stack.addWidget(widget)
        return layout
    def switch(self,key):
        self.stack.setCurrentWidget(self.pages[key]);self.nav_buttons["library" if key in ("library","knowledge") else "experiments"].setChecked(True)
        if key in ("library","knowledge"):
            tabs=self.pages[key].findChild(W.QTabBar)
            with C.QSignalBlocker(tabs):tabs.setCurrentIndex(int(key=="knowledge"))
        if key=="knowledge":self.refresh_knowledge()
        if key=="session":self.refresh_session()
    def safe(self,fn):
        try:return fn()
        except Exception as exc:self.notify(self.app.settings.redact(str(exc)),error=True);return None
    def notify(self,message,error=False):
        self.status.showMessage(str(message),20000)
        if error:W.QMessageBox.warning(self,"操作未完成",str(message))
    def submit(self,title,fn,callback=None):
        self.working+=1;self.status.showMessage(title+"…")
        def task():
            try:result=fn();self.dispatch.finished.emit(callback,result,None)
            except Exception as exc:self.dispatch.finished.emit(callback,None,self.app.settings.redact(str(exc)))
        threading.Thread(target=task,daemon=True).start()
    @C.Slot(object,object,object)
    def received(self,callback,result,error):
        self.working=max(0,self.working-1)
        self.status.showMessage("操作完成。" if not error else "操作未完成，可查看错误详情。")
        if error:self.notify(error,True)
        elif callback:
            try:callback(result)
            except Exception as exc:self.notify(str(exc),True)
        self.refresh()
    def build_experiments(self):
        layout=self.page("experiments","我的实验","")
        layout.addWidget(button("新建实验",lambda:ExperimentDialog(self).exec(),True),0,C.Qt.AlignLeft)
        self.experiments=W.QListWidget();self.experiments.setObjectName("experimentList");self.experiments.setSpacing(3);self.experiments.itemDoubleClicked.connect(lambda item:self.open_experiment(item.data(C.Qt.UserRole)));layout.addWidget(self.experiments,1)
        self.experiment_open=button("打开所选实验",lambda:self.open_experiment(self.experiments.currentItem().data(C.Qt.UserRole)) if self.experiments.currentItem() else None)
        layout.addWidget(self.experiment_open,0,C.Qt.AlignLeft)
    def build_library(self):
        layout=self.page("library","资料与知识","");self.resource_tabs(layout,0)
        layout.addWidget(row(button("＋ 导入资料",self.import_files,True),button("查看原页",self.selected_source),button("已整理的数据表",self.open_data_workbooks),button("全部生成文件",lambda:self.open_products())))
        layout.addWidget(label("选中一份资料可整理知识或数据，双击可查看原页。","muted"));self.library_activities=W.QHBoxLayout();layout.addLayout(self.library_activities)
        self.sources=self.data_table(["资料","类型","页数","来源"]);self.sources.itemDoubleClicked.connect(lambda _:self.selected_source());self.sources.itemSelectionChanged.connect(self.update_library_actions);layout.addWidget(self.sources,3)
    def update_library_actions(self):
        selected=bool(self.selected_id(self.sources))
        for index in range(self.library_activities.count()):
            widget=self.library_activities.itemAt(index).widget()
            if widget:widget.setEnabled(selected)

    def resource_tabs(self,layout,index):
        tabs=W.QTabBar();tabs.addTab("资料库");tabs.addTab("知识库");tabs.setCurrentIndex(index)
        tabs.currentChanged.connect(lambda i:self.switch("knowledge" if i else "library"))
        layout.addWidget(tabs,0,C.Qt.AlignLeft)

    def build_knowledge(self):
        layout=self.page("knowledge","资料与知识","");self.resource_tabs(layout,1)
        self.search=W.QLineEdit();self.search.setPlaceholderText("搜索知识、公式、条件或出处…");self.search.returnPressed.connect(self.refresh_knowledge)
        self.kind=W.QComboBox();self.kind.addItem("全部类型",None)
        from .knowledge_index import KINDS as knowledge_kinds
        for key in knowledge_kinds:self.kind.addItem(KINDS[key],key)
        self.kind.currentIndexChanged.connect(self.refresh_knowledge)
        self.describe_button=button("补建条目说明",self.describe_selected);self.describe_button.setEnabled(False)
        layout.addWidget(row(self.search,self.kind,button("搜索",self.refresh_knowledge,True),self.describe_button,button("管理动态目录",lambda:CategoryDialog(self).exec())))
        split=W.QSplitter();self.categories=W.QTreeWidget();self.categories.setObjectName("knowledgeTree");self.categories.setHeaderLabel("知识目录");self.categories.itemClicked.connect(self.refresh_knowledge)
        self.points=self.data_table(["知识","检索说明","来源与文件页","类型","修订"]);self.points.itemDoubleClicked.connect(lambda _:self.selected_point())
        self.points.itemSelectionChanged.connect(self.update_description_action)
        layout.addWidget(label("选择目录可浏览条目，双击知识可查看正文和出处。","muted"))
        split.setHandleWidth(5);split.addWidget(self.categories);split.addWidget(self.points);split.setSizes([220,900]);layout.addWidget(split,1)
    def data_table(self,headers):
        widget=W.QTableWidget(0,len(headers));widget.setHorizontalHeaderLabels(headers);widget.setEditTriggers(W.QAbstractItemView.NoEditTriggers);widget.setSelectionBehavior(W.QAbstractItemView.SelectRows);widget.setSelectionMode(W.QAbstractItemView.SingleSelection);widget.verticalHeader().hide();widget.setAlternatingRowColors(True);widget.horizontalHeader().setSectionResizeMode(W.QHeaderView.Stretch);return widget
    def fill_table(self,widget,rows):
        selected=self.selected_id(widget);position=widget.verticalScrollBar().value()
        widget.setRowCount(len(rows))
        for i,(identity,values) in enumerate(rows):
            for j,value in enumerate(values):
                item=W.QTableWidgetItem(str(value));item.setData(C.Qt.UserRole,identity);widget.setItem(i,j,item)
            if identity==selected:widget.setCurrentCell(i,0)
        widget.resizeRowsToContents()
        widget.verticalScrollBar().setValue(position)
    def selected_id(self,widget):
        row_index=widget.currentRow()
        return widget.item(row_index,0).data(C.Qt.UserRole) if row_index>=0 and widget.item(row_index,0) else None
    def refresh(self):
        self.state=self.app.state(include_usage=False)
        question_count=len(self.state.get("pending_questions",[]));self.questions_button.setText("回答助手问题"+(f"（{question_count}）" if question_count else ""));self.questions_button.setVisible(bool(question_count))
        rows=[(exp["id"],f"{exp['title']}\n最近保存 {exp['updated'][:10]}",exp["id"]) for exp in self.state["experiments"]]
        self.sync_session_items(self.experiments,rows)
        self.experiment_open.setEnabled(bool(rows))
        if rows and not self.experiments.currentItem():self.experiments.setCurrentRow(0)
        self.fill_table(self.sources,[(s["id"],[s["name"],s["kind"].upper(),s["page_count"],s["metadata"]["origin"]]) for s in self.state["sources"]])
        self.more_menu.clear();clear(self.library_activities)
        for pack in self.state["formats"]["packages"]:
            for entry in pack["ui"]:
                if entry["activity"]=="worksheet":continue
                if entry["location"] not in ("experiment","library"):continue
                def invoke(checked=False,a=entry["activity"],location=entry["location"]):
                    if location=="library" and a=="distill":self.distill_source()
                    elif location=="library" and a=="organize_data":self.organize_source()
                    elif location=="experiment":self.activity(a)
                if entry["location"]=="experiment":
                    action=self.more_menu.addAction(entry["label"],invoke)
                    action.setToolTip(entry["description"])
                else:
                    b=button(entry["label"],invoke);b.setToolTip(entry["description"])
                    self.library_activities.addWidget(b)
        self.update_library_actions()
        self.more_menu.addSeparator()
        self.more_menu.addAction("编辑实验备注与讲义…", self.edit_experiment)
        self.more_menu.addAction("关联测量数据…", self.associate_data)
        self.refresh_jobs();self.refresh_categories()
        if self.stack.currentWidget() is self.pages["knowledge"]:self.refresh_knowledge()
        self.refresh_session()
        for job in self.state.get("jobs",[]):
            if job["id"] in self.worksheet_pending and job["status"] not in ("queued","running"):
                exp=self.worksheet_pending.pop(job["id"])
                result=job.get("result") or {}
                if exp==self.current_exp and result.get("open") and result.get("worksheet"):
                    C.QTimer.singleShot(0,lambda i=result["worksheet"],p=result.get("page"):self.open_object(i,page=p))
    def poll(self):
        try:
            while not self.app.publications.open_requests.empty():
                path=self.app.publications.open_requests.get_nowait()
                from .native_products import open_product_path
                open_product_path(self,path)
            jobs=self.app.store.all("SELECT id,status,progress,updated FROM jobs ORDER BY created DESC")
            runs=self.app.store.all("SELECT id,status,updated FROM runs ORDER BY created DESC")
            requests=self.app.store.all("SELECT id,status,phase,finished FROM model_requests WHERE status='started' OR id=(SELECT id FROM model_requests ORDER BY started DESC LIMIT 1)")
            fingerprint=canonical([jobs,runs,requests,self.app.settings.read()["configured"]])
            if fingerprint!=self.last_fingerprint:self.last_fingerprint=fingerprint;self.refresh()
        except Exception as exc:self.status.showMessage(str(exc),5000)
    def open_products(self,current_only=False):
        from .native_products import ProductsDialog
        self.show_reader(ProductsDialog(self,self.current_exp if current_only else None))
    def open_jobs(self):
        dialog=W.QDialog(self);dialog.setWindowTitle("任务记录");dialog.resize(880,540)
        layout=W.QVBoxLayout(dialog)
        self.jobs=self.data_table(["任务","类型","状态","进度","操作"])
        self.jobs.itemDoubleClicked.connect(self.job_details);layout.addWidget(self.jobs)
        self.refresh_jobs()
        try:dialog.exec()
        finally:self.jobs=None

    def refresh_jobs(self):
        if self.jobs is None:return
        from .document_delivery import has_failed_publication
        jobs=self.state.get("jobs",[]);self.jobs.setRowCount(len(jobs))
        for i,job in enumerate(jobs):
            payload=job["payload"];status=STATUS.get(job["status"],job["status"])
            if job['status'] in ('failed','succeeded') and has_failed_publication(job.get('result') or {}):status='文件导出待完成'
            values=[payload.get("name") or payload.get("activity") or payload.get("source_id") or job["id"],job["kind"],status]
            for j,value in enumerate(values):item=W.QTableWidgetItem(str(value));item.setData(C.Qt.UserRole,job["id"]);self.jobs.setItem(i,j,item)
            progress=W.QProgressBar();progress.setRange(0,0 if job["kind"]!="import" and job["status"] in ("queued","running") else 100);progress.setValue(job["progress"]);self.jobs.setCellWidget(i,3,progress)
            if job["status"] in ("queued","running","waiting_user"):
                self.jobs.setCellWidget(i,4,button("取消",lambda checked=False,j=job:self.safe(lambda:self.app.post('/api/jobs/'+j['id']+'/cancel',{}))))
            elif job["kind"]=="import" and job["status"] in ("failed","cancelled","interrupted"):
                self.jobs.setCellWidget(i,4,button("重试",lambda checked=False,j=job:self.safe(lambda:self.app.post('/api/jobs/'+j['id']+'/retry',{}))))
            else:self.jobs.setItem(i,4,W.QTableWidgetItem("双击看回执"))
        self.jobs.resizeRowsToContents()
    def refresh_categories(self):
        current=self.categories.currentItem();selected=current.data(0,C.Qt.UserRole) if current else None
        previous=getattr(self,"category_items",{})
        expanded={k for k,v in previous.items() if v.isExpanded()}
        self.categories.clear();all_item=W.QTreeWidgetItem(["全部知识"]);all_item.setData(0,C.Qt.UserRole,None);self.categories.addTopLevelItem(all_item)
        mapping={};pending=list(self.state.get("categories",[]))
        while pending:
            progress=False
            for cat in pending[:]:
                if cat["parent"] and cat["parent"] not in mapping:continue
                item=W.QTreeWidgetItem([cat["name"]]);item.setData(0,C.Qt.UserRole,cat["id"])
                if cat["parent"]:mapping[cat["parent"]].addChild(item)
                else:self.categories.addTopLevelItem(item)
                mapping[cat["id"]]=item;pending.remove(cat);progress=True
            if not progress:break
        self.category_items=mapping
        for cat in (expanded if previous else [k for k,item in mapping.items() if item.parent() is None]):
            if cat in mapping:mapping[cat].setExpanded(True)
        self.categories.setCurrentItem(mapping.get(selected,all_item))
    def refresh_knowledge(self,*args):
        if not hasattr(self,"points"):return
        item=self.categories.currentItem();category=item.data(0,C.Qt.UserRole) if item else None
        from .knowledge_index import search
        cards=self.safe(lambda:search(self.app.store,self.search.text(),kind=self.kind.currentData(),category=category))
        if cards is not None:
            from .worksheet_presentation import scientific_text
            rows=[]
            from .knowledge_catalog import browse
            for c in browse(self.app.store,cards,category,self.search.text().strip()):
                by_file={}
                for anchor in c["locations"]:
                    by_file.setdefault(anchor["file"],[]).append(str(anchor["page"]))
                location="；".join(f"第 {', '.join(pages)} 页 · {name}" for name,pages in by_file.items())
                role=(c.get("topic") or {}).get("role")
                kind={"core":"核心知识","detail":"补充知识","overlap":"相近表述"}.get(role,KINDS.get(c["kind"],c["kind"]))
                rows.append((c["id"],[scientific_text(c["title"]),scientific_text(c["summary"]) or "待生成说明",location,kind,c["revision"]]))
            header=self.points.horizontalHeader()
            for column in (3,4):
                header.setSectionResizeMode(column,W.QHeaderView.ResizeToContents)
            self.fill_table(self.points,rows)
            self.update_description_action()
            for index,(_,values) in enumerate(rows):
                for column in (0,1,2):
                    self.points.item(index,column).setToolTip(str(values[column]))
    def open_experiment(self,identity):
        self.current_exp=identity;self.app.store.execute("INSERT OR REPLACE INTO meta VALUES('last_native_experiment',?)",(identity,));self.session_fingerprint="";self.refresh_session();self.switch("session")
    @staticmethod
    def sync_session_items(widget,rows):
        """Background progress must not destroy unchanged readable items."""
        role=C.Qt.UserRole+1
        existing={widget.item(i).data(role):widget.item(i) for i in range(widget.count())}
        chosen=widget.currentItem();selected=chosen.data(role) if chosen else None
        position=widget.verticalScrollBar().value();wanted={r[0] for r in rows}
        for index,(identity,title,data) in enumerate(rows):
            item=existing.get(identity)
            if item is None:
                item=W.QListWidgetItem();item.setData(role,identity);widget.insertItem(index,item)
            elif widget.row(item)!=index:widget.insertItem(index,widget.takeItem(widget.row(item)))
            if item.text()!=title:item.setText(title)
            if item.data(C.Qt.UserRole)!=data:item.setData(C.Qt.UserRole,data)
        for i in range(widget.count()-1,-1,-1):
            if widget.item(i).data(role) not in wanted:widget.takeItem(i)
        if selected in wanted:
            widget.setCurrentItem(next(widget.item(i) for i in range(widget.count()) if widget.item(i).data(role)==selected))
        widget.verticalScrollBar().setValue(position)
    def refresh_session(self):
        if not self.current_exp:return
        exp=self.app.experiment(self.current_exp,include_usage=False)
        options={**self.app.settings.read(),**exp["agent_options"]}
        self.exp_title.setText(exp["title"])
        self.depth.setCurrentIndex(self.depth.findData(options["thinking_depth"]))
        scoped=[j for j in self.state.get("jobs",[]) if j["payload"].get("experiment_id")==self.current_exp]
        sources={s["id"]:s for s in self.state["sources"]}
        linked=list(dict.fromkeys(exp["source_ids"]+[ds["source_id"] for ds in exp["datasets"]]))
        source_rows=[(sid,sources[sid]["name"]+"\n"+sources[sid]["kind"].upper()+" · "+str(sources[sid]["page_count"])+" 页",sid) for sid in linked if sid in sources]
        self.sync_session_items(self.materials_list,source_rows)
        if source_rows and not self.materials_list.currentItem():self.materials_list.setCurrentRow(0)
        self.materials_list.setFixedHeight(min(196, max(57,sum(self.materials_list.sizeHintForRow(i) for i in range(min(4,len(source_rows))))+4)))
        self.materials_list.setVisible(bool(source_rows));self.source_open.setVisible(bool(source_rows));self.no_material.setVisible(not source_rows)
        self.refresh_import_state(scoped)
        from datetime import datetime
        messages="\n\n---\n\n".join('### '+("你" if m["role"]=="user" else "实验助手" if m["mode"]=="model" else "离线编排／系统记录")+" · "+datetime.fromisoformat(m['created']).astimezone().strftime('%m-%d %H:%M:%S')+"\n\n"+m["content"] for m in exp["messages"])
        if self.shown_messages!=messages:
            bar=self.messages.verticalScrollBar();position=bar.value();follow=position>=bar.maximum()-4
            self.messages.set_markdown(messages);self.shown_messages=messages;bar.setValue(bar.maximum() if follow else position)
            if follow:C.QTimer.singleShot(0,lambda:bar.setValue(bar.maximum()))
        self.messages.setVisible(bool(messages))
        active=[j for j in scoped if j["kind"]=="agent" and j["status"] in ("queued","running")]
        self.refresh_home_action(exp, linked, sources, scoped, active)

        pending_documents={d['id'] for j in active for receipt in (j.get('result') or {}).get('tools',[]) for d in receipt.get('generated_documents',[])}
        self.sync_session_items(self.artifacts,[(obj['id'],obj["title"]+f"\n{KINDS.get(obj['kind'],obj['kind'])} · r{obj['revision']}"+(' · 写作中' if obj['id'] in pending_documents else ''),obj['id']) for obj in exp['artifacts'] if obj['kind'] not in ('ExperimentDraft','ExperimentWorksheet')])
        self.output_panel.setVisible(bool(self.artifacts.count()))
        if self.artifacts.count() and not self.artifacts.currentItem():self.artifacts.setCurrentRow(0)
        rows=[]
        for dataset in exp["datasets"]:
            rows.append(('dataset:'+dataset['id'],dataset["name"]+f" · {len(dataset['rows'])} 行 · 单位已确认",{"dataset":dataset["id"]}))
        for run in exp["runs"]:
            rows.append(('run:'+run['id'],STATUS.get(run["status"],run["status"])+" · "+run["id"][-8:],run))
        self.sync_session_items(self.data_runs,rows)
        self.data_panel.setVisible(bool(rows));self.send.setEnabled(not active);self.cancel.setVisible(bool(active))
        if active:
            receipts=(active[0].get("result") or {}).get("tools",[])
            progress=next((r["description"] for r in reversed(receipts) if r.get("description")),"助手正在思考和处理当前请求…")
            running=self.app.store.one("SELECT name FROM runtime_tools WHERE job_id=? AND status='started' ORDER BY started DESC LIMIT 1",(active[0]['id'],))
            if running and running['name']=='review_document_content':progress='正在对照原件核对文稿内容，成果列表中的版本仍在校对'
            builds=self.app.store.all("SELECT purpose,status FROM model_requests WHERE job_id=? AND purpose LIKE 'worksheet_%'",(active[0]["id"],))
            if active[0]["payload"].get("activity")=="worksheet" or any(r["purpose"]=="worksheet_outline" for r in builds):
                pages=[r for r in builds if r["purpose"].startswith("worksheet_page_")]
                done=sum(r["status"]=="returned" for r in pages)
                progress=(f"正在建立测量页，已收到 {done}/{len(pages)} 页" if pages else "正在按讲义梳理测量方法与取数条件")
                if any(r["purpose"]=="worksheet_source_relations" for r in builds):
                    progress="正在对照原页，核实各页的测量关系"
            self.status.showMessage(progress)
            if self.home_mode=='working':self.exp_context.setText(progress)
        elif self.app.questions.pending(self.current_exp):self.status.showMessage("助手需要你确认一些事实，可直接在聊天里回答，或点“回答助手问题”。")
        else:self.status.clearMessage()
    def refresh_import_state(self, jobs):
        imports=[j for j in jobs if j["kind"]=="import"]
        pending=[j for j in imports if j["status"] in ("queued","running")]
        latest=imports[0] if imports else None
        failed=latest if latest and latest["status"] in ("failed","cancelled","interrupted") else None
        self.failed_import=failed["id"] if failed else None
        note="正在导入 "+pending[0]["payload"]["name"] if pending else ""
        if failed:note=failed["payload"]["name"]+"："+str(failed.get("error") or "导入中断")
        self.import_note.setText(note);self.import_note.setVisible(bool(note))
        self.retry_import.setVisible(bool(failed))

    def retry_home_import(self):
        if self.failed_import:
            self.safe(lambda:self.app.materials.retry(self.failed_import));self.refresh()

    def refresh_home_action(self, exp, linked, sources, jobs, active):
        obj=next((o for o in exp["artifacts"] if o["kind"]=="ExperimentWorksheet"), None)
        self.home_sheet=obj["id"] if obj else None
        self.resume_id=None
        task=next((j for j in jobs if j["kind"]=="agent" and
                   str(j["payload"].get("activity","")).split(":")[0] in ("worksheet","worksheet_build")),None)
        has_lecture=any(sources[s]["kind"] not in ("csv","tsv","xlsx","dat","zip") for s in linked if s in sources)
        self.home_page=None
        if task and task["status"] in ("failed","interrupted","cancelled") and self.app.agent.runtime.load(task["id"]):
            self.resume_id=task["id"]
        if active and (not obj or task and task["id"]==active[0]["id"]):
            mode,title,note,caption="working","助手正在处理","正在处理当前请求…","处理中…"
        elif obj:
            spec=self.app.store.object(obj["id"],obj["revision"])["content"]
            pages=spec.get("pages",[])
            missing=[p for p in pages if p.get("generation_error")]
            gaps=[(p,x) for p in pages for x in p.get("target_coverage",[]) if x["status"]=="missing"]
            if missing:
                mode,title,note,caption="complete_pages","部分实验页已建立","尚待建立："+ "、".join(p["title"] for p in missing),"继续生成"
            elif gaps:
                self.home_page=gaps[0][0]["id"]
                mode,title,note,caption="open","数据台可录入","待核对的结果："+ "、".join(x["label"] for _,x in gaps),"打开对应页"
            else:
                mode,title,note,caption="open","数据处理台已就绪","查看已保存的读数、曲线和计算结果。","继续录入"
        elif self.resume_id:
            mode,title,note,caption="resume","上次生成已中断",str(task.get("error") or "已保存本次任务，可以接着完成。"),"继续生成"
        elif has_lecture:
            mode,title,note,caption="generate","讲义已选好","根据所选讲义，准备本实验的读数、公式和曲线。","生成数据处理台"
            if task and task["status"] in ("failed","interrupted","cancelled"):
                title, note, caption="上次生成已中断",str(task.get("error") or "可重新发起生成。"),"重新生成"
        else:
            mode,title,note,caption="add",("实测数据已添加" if linked else "添加本实验的材料"),("补充讲义可生成对应的数据处理台。" if linked else "可以先放讲义，也可以从实测数据开始。"),("添加讲义" if linked else "添加讲义或数据")
        self.home_mode=mode
        self.card_title.setText(title);self.exp_context.setText(note)
        self.worksheet_button.setText(caption)
        self.worksheet_button.setEnabled(mode!="working" and (not active or mode=="open"))
        self.open_partial.setVisible(bool(obj) and mode in ("working","complete_pages"))

    def home_action(self):
        if self.home_mode=="add":self.import_into_experiment()
        elif self.home_mode=="open":self.open_object(self.home_sheet,page=self.home_page)
        elif self.home_mode=="resume":
            result=self.safe(lambda:self.app.agent.resume_job(self.resume_id))
            if result:self.worksheet_pending[result["id"]]=self.current_exp;self.refresh()
        elif self.home_mode=="complete_pages":
            self.send_message("接着完成尚未建立的实验数据页，沿用已有草稿和读数。","worksheet_build:resume")
        elif self.home_mode=="generate":self.activity("worksheet")

    def open_home_source(self):
        item=self.materials_list.currentItem()
        if item:self.open_source(item.data(C.Qt.UserRole))

    def open_home_folder(self):
        from .native_products import open_product_path
        item=self.artifacts.currentItem()
        if item:
            obj=self.app.store.object(item.data(C.Qt.UserRole))
            record=self.app.publications.info(obj)
            if record and record.get("folder") and Path(record["folder"]).is_dir():
                open_product_path(self,record["folder"]);return
        self.open_products(True)

    def import_into_experiment(self):
        if self.current_exp:self.import_files(experiment_id=self.current_exp)

    def set_options(self,**options):
        if not self.current_exp:return
        self.safe(lambda:self.app.post(f"/api/experiments/{self.current_exp}/agent-options",options));self.refresh_session()
    def open_drafts(self):
        if not self.current_exp:return
        drafts=self.app.agent.drafts.list(self.current_exp)
        if not drafts:self.notify("本实验没有未完成的文档草稿。");return
        dialog=W.QDialog(self);dialog.setWindowTitle("接续完整文档");dialog.resize(1000,780);layout=W.QVBoxLayout(dialog)
        choices=W.QComboBox()
        for draft in drafts:choices.addItem(draft["title"]+f" · 草稿 r{draft['revision']}",draft["id"])
        layout.addWidget(choices);preview=plain(readonly=True,height=500);preview.setMaximumHeight(2000);layout.addWidget(preview,1)
        def changed():
            record=self.app.agent.drafts.read(self.current_exp,choices.currentData());content=record["content"]
            lines=[content["title"],content.get("summary",""),"未完成草稿；完成格式生成后才登记为正式成果。"]
            for section in content.get("sections",[])+content.get("appendices",[]):
                lines.extend(["",section["title"]])
                for block in section["blocks"]:
                    if block["type"] in ("paragraph","code","crossref"):lines.append(block.get("text",""))
                    elif block["type"]=="equation":lines.extend([block["latex"],block.get("symbols","")])
                    elif block["type"]=="table":lines.extend([block["caption"],"\t".join(block["columns"]),*("\t".join(map(str,row)) for row in block["rows"])])
                    else:lines.append(block.get("caption",""))
            preview.setPlainText("\n".join(lines))
        def resume():
            identity=choices.currentData();dialog.accept();self.send_message("请先 read_document_draft 阅读草稿 "+identity+" 的完整已保存内容，接续补齐本次请求的所有章节和附录，最终 finish_document_draft 生成完整正式文件。保留原有实质内容、出处和未确认项。","continue_document")
        choices.currentIndexChanged.connect(changed);changed();layout.addWidget(row(button("继续写完并生成",resume,True),button("关闭",dialog.accept)));dialog.exec()
    def send_message(self,prompt=None,activity="chat"):
        if not self.current_exp:self.notify("请先选择一个实验。");return
        value=prompt if isinstance(prompt,str) else self.prompt.toPlainText()
        if not value.strip():return
        result=self.safe(lambda:self.app.agent.queue(self.current_exp,value,activity))
        if result:
            self.worksheet_pending[result['id']]=self.current_exp
            if prompt is None or isinstance(prompt,bool):self.prompt.clear()
            self.refresh()
    def activity(self,activity):
        if activity=="worksheet":
            if not self.current_exp:self.notify("请先选择一个实验。");return
            obj=self.app.worksheets.current(self.current_exp)
            if obj:self.open_object(obj["id"])
            else:self.send_message("请按本次讲义生成能填读数、看曲线和计算结果的数据处理台。","worksheet")
            return
        if activity=="methods":
            pack=self.app.registry.get("physics.analysis");catalog=json.loads((Path(pack["directory"])/"methods.json").read_text(encoding="utf-8"))
            dialog=W.QDialog(self);dialog.setWindowTitle("固定分析包的基础方法");dialog.resize(1000,720);layout=W.QHBoxLayout(dialog);choices=W.QListWidget();detail=plain(readonly=True,height=650);detail.setMaximumHeight(2000);layout.addWidget(choices,1);layout.addWidget(detail,3)
            for method in catalog["methods"]:
                item=W.QListWidgetItem(method["title"]);item.setData(C.Qt.UserRole,method);choices.addItem(item)
            def show(item,*_):
                if item:
                    m=item.data(C.Qt.UserRole);detail.setPlainText("\n\n".join(str(k)+"\n"+("\n".join(map(str,v)) if isinstance(v,list) else "；".join(str(a)+"："+str(b) for a,b in v.items()) if isinstance(v,dict) else str(v)) for k,v in m.items()))
            choices.currentItemChanged.connect(show);choices.setCurrentRow(0);dialog.exec();return
        prompts={"outline":"请先根据本实验讲义和我的目标梳理实验草稿：从预习到数据处理顺着逻辑走一遍，列知识点和选材，另列每点的诠释要点；非当前重点简写即可。","prestudy":"请按本实验的讲义和备注界定对应实验，从教材选择物理概念与关系，按我的已学知识生成背景阅读，给 Word 和 PDF。","program":"请先梳理或补充实验草稿，核明本次数据和方法，现场写完整分析程序并实际运行，交付结果及解释。"}
        if activity in prompts:self.send_message(prompts[activity],activity)
    def cancel_agent(self):
        for job in self.state.get("jobs",[]):
            if job["kind"]=="agent" and job["payload"].get("experiment_id")==self.current_exp and job["status"] in ("queued","running","waiting_user"):self.app.post('/api/jobs/'+job['id']+'/cancel',{})
        self.notify("已请求取消；模型返回后不再执行后续工具，脚本作业会停止。")
    def assemble(self,kind):
        if self.current_exp:self.submit("离线编排",lambda:self.app.agent.reference_document(self.current_exp,kind),lambda obj:self.open_object(obj["id"]))
    def edit_experiment(self):
        if self.current_exp:ExperimentDialog(self,self.app.store.experiment(self.current_exp)).exec()
    def associate_data(self):
        if self.current_exp:DatasetDialog(self,self.current_exp).exec()
    def open_source(self,identity,page=1,region=None):self.safe(lambda:self.show_reader(SourceDialog(self,identity,page,region)))
    def show_reader(self,reader):
        reader.setAttribute(C.Qt.WA_DeleteOnClose);self.reader_windows.add(reader)
        reader.finished.connect(lambda _=None:self.reader_windows.discard(reader))
        reader.show();reader.raise_();reader.activateWindow()
    def open_object(self,identity,revision=None,page=None):
        obj=self.app.store.one("SELECT kind FROM objects WHERE id=?",(identity,))
        if obj and obj["kind"]=="ExperimentWorksheet" and not revision:
            for window in self.reader_windows:
                if getattr(window,"identity",None)==identity:
                    if page and window.selector.findData(page)>=0:window.selector.setCurrentIndex(window.selector.findData(page))
                    window.showNormal();window.raise_();window.activateWindow();return
            from .native_worksheet import WorksheetWindow
            self.safe(lambda:self.show_reader(WorksheetWindow(self,identity,page)));return
        self.safe(lambda:self.show_reader(ObjectDialog(self,identity,revision)))
    def follow_source(self,url):
        if url.scheme()!="physlab":return
        parts=url.path().strip("/").split("/")
        if len(parts)!=2 or not parts[1].isdigit():return
        if url.host()=="page":self.open_source(parts[0],int(parts[1]))
        if url.host()=="knowledge":self.open_object(parts[0],int(parts[1]))
    def selected_source(self):
        identity=self.selected_id(self.sources)
        if identity:self.open_source(identity)
    def update_description_action(self):
        identity=self.selected_id(self.points)
        self.describe_button.setEnabled(bool(identity and not identity.startswith("folder/")))

    def describe_selected(self):
        identity=self.selected_id(self.points)
        if not identity:self.notify("请先选择一条知识");return
        if identity.startswith("folder/"):self.selected_point();return
        revision=int(self.points.item(self.points.currentRow(),4).text())
        result=self.safe(lambda:self.app.knowledge.queue_description(identity,revision))
        if result:self.notify("已有检索说明。" if result.get("already_processed") else "助手正在核对原页并撰写检索说明。")
    def selected_point(self):
        identity=self.selected_id(self.points)
        if identity and identity.startswith("folder/"):
            node=self.category_items.get(identity.split("/",1)[1])
            if node:
                self.categories.setCurrentItem(node);self.categories.scrollToItem(node);node.setExpanded(True)
                self.refresh_knowledge()
        elif identity:self.open_object(identity)
    def open_run(self,item):
        run=item.data(C.Qt.UserRole)
        if not run:return
        if run.get("dataset"):
            from .native_widgets import show_dataset
            data=next(d for d in self.app.analysis.datasets(self.current_exp) if d["id"]==run["dataset"])
            show_dataset(self,data["name"],list(data["mapping"]),data["rows"],data["mapping"]);return
        if run["info"].get("artifact_id"):self.open_object(run["info"]["artifact_id"])
        elif run["status"] in ("queued","running"):
            if W.QMessageBox.question(self,"运行中","取消这次运行？") ==W.QMessageBox.Yes:self.app.analysis.cancellations.add(run["id"])
        else:self.notify(run["info"].get("error",STATUS.get(run["status"],run["status"])))
    def correct_dataset(self):
        if not self.current_exp:return
        datasets=self.app.analysis.datasets(self.current_exp)
        if not datasets:self.notify("本实验尚未关联数据。");return
        names=[d["name"]+" · "+d["id"][-6:] for d in datasets]
        chosen,ok=W.QInputDialog.getItem(self,"更正数据列解释","选择本实验的数据",names,0,False)
        if ok:DatasetDialog(self,self.current_exp,datasets[names.index(chosen)]).exec()
    def import_files(self,checked=False,*,experiment_id=None):
        files,_=W.QFileDialog.getOpenFileNames(self,"选择讲义、Word、PDF 版 PPT 或原始数据","","讲义与数据 (*.pdf *.docx *.png *.jpg *.jpeg *.csv *.tsv *.xlsx *.dat *.zip);;文字资料 (*.txt *.md)")
        if files:
            self.submit("导入资料",lambda:[self.app.materials.queue(Path(p).name,Path(p).read_bytes(),experiment_id=experiment_id) for p in files],lambda _:self.notify("资料已入队。原件保留，重复文件沿用已有来源。"))
    def distill_source(self):
        identity=self.selected_id(self.sources)
        if not identity:self.notify("请先在资料列表选择要处理的完整资料。");return
        from .native_knowledge import KnowledgeSourceDialog
        dialog=KnowledgeSourceDialog(self,identity)
        if dialog.exec():
            result=dialog.result_job
            self.notify("这些页已有整理记录。" if result.get("already_processed") else "已登记，可在知识库查看陆续保存的条目。")
            self.refresh()
    def organize_source(self):
        identity=self.selected_id(self.sources)
        if not identity:self.notify("请先选择已导入的零散测量记录，再点“整理测量记录”。");return
        prompt,ok=W.QInputDialog.getMultiLineText(self,"整理测量记录","希望怎样整理？可说明分组、用途和你记得的现场情况；留空按原始记录整理。")
        if not ok:return
        result=self.safe(lambda:self.app.agent.organize(identity,prompt=prompt if prompt.strip() else None))
        if result and result.get('object_id'):
            self.open_object(result['object_id'],result['revision']);return
        if result:self.notify("助手正在整理完整记录。有问题会暂停并请你回答；已确定和待确认的内容都会保存到规范数据表。");self.refresh()
    def open_questions(self,include_all=False):
        from .native_questions import QuestionsDialog
        QuestionsDialog(self,self.current_exp,include_all).exec()
    def open_data_workbooks(self):
        records=self.app.gateway.query(kind="DataWorkbook")
        if not records:self.notify("还没有整理好的数据表。选一份已导入的零散记录，再点“整理测量记录”。");return
        dialog=W.QDialog(self);dialog.setWindowTitle("已整理的数据表");dialog.resize(850,570);layout=W.QVBoxLayout(dialog);choices=W.QListWidget();layout.addWidget(choices)
        for record in records:
            item=W.QListWidgetItem(record["title"]);item.setData(C.Qt.UserRole,record["id"]);choices.addItem(item)
        def open_record(item):dialog.accept();self.open_object(item.data(C.Qt.UserRole))
        choices.itemDoubleClicked.connect(open_record);layout.addWidget(button("打开所选数据表",lambda:open_record(choices.currentItem()) if choices.currentItem() else None,True));dialog.exec()
    def demo(self):
        from .demo import install_demo
        self.submit("准备教学模拟",lambda:install_demo(self.app),lambda d:self.open_experiment(d["experiment_id"]))
    def formats(self):
        dialog=W.QDialog(self);dialog.setWindowTitle("预置格式与说明");dialog.resize(900,750);layout=W.QVBoxLayout(dialog);tabs=W.QTabWidget();layout.addWidget(tabs)
        for pack in self.app.registry.public()["packages"]:
            page=W.QWidget();pl=W.QVBoxLayout(page);pl.addWidget(label(f"{pack['id']} / {pack['version']} · 数据 {pack['data_version']} · 接口 {pack['runtime_version']}\n摘要 {pack['digest']}","muted"));pl.addWidget(plain(pack["instructions"],True,600));tabs.addTab(page,pack["id"].split('.')[-1])
        for error in self.app.registry.errors:layout.addWidget(label(error["error"],"warning"))
        dialog.exec()
    def job_details(self,item):
        job_id=item.data(C.Qt.UserRole)
        if not job_id:return
        job=next((j for j in self.state.get("jobs",[]) if j["id"]==job_id),None)
        if not job:return
        from .document_delivery import has_failed_publication
        result=job.get("result") or {};delivery_failed=has_failed_publication(result)
        status='文件导出待完成' if delivery_failed and job['status'] in ('failed','succeeded') else STATUS.get(job['status'],job['status'])
        message=f"状态：{status}\n"+str(job.get("error") or result.get("message") or result.get("duplicate") or "")
        if result.get("tools"):message+="\n实际工具："+" → ".join(t["tool"] for t in result["tools"])
        if result.get("request_options"):message+="\n本次思考："+result["request_options"]["thinking_depth"]
        details=self.app.agent.runtime.summary(job_id)
        message+=f"\n模型请求 {details['model_requests']} 次；输入 {details['input_tokens']:,}，输出 {details['output_tokens']:,} token；未知用量 {details['unknown_usage_requests']} 次。"
        dialog=W.QMessageBox(self);dialog.setWindowTitle('任务回执');dialog.setText(message)
        resume=None
        if job['status'] in ('interrupted','failed','cancelled') or (job['status']=='succeeded' and delivery_failed):
            checkpoint=self.app.agent.runtime.load(job_id)
            if checkpoint:
                caption='重新导出已保存的版本' if delivery_failed or checkpoint.get('publication') else '从已保存步骤接续'
                resume=dialog.addButton(caption,W.QMessageBox.ActionRole)
        dialog.addButton('关闭',W.QMessageBox.RejectRole);dialog.exec()
        if resume and dialog.clickedButton()==resume:self.safe(lambda:self.app.agent.resume_job(job_id));self.refresh()

    def open_usage(self):
        from .native_usage import UsageDialog
        UsageDialog(self).exec()
    def closeEvent(self,event):
        active=self.app.store.one("SELECT COUNT(*) n FROM jobs WHERE status IN ('queued','running')")["n"]
        running=self.app.store.one("SELECT COUNT(*) n FROM runs WHERE status IN ('queued','running')")["n"]
        if active or running:
            if W.QMessageBox.question(self,"退出助手","仍有任务进行中。退出将中断工作，已保存的会话和阶段成果仍保留。确定退出？")!=W.QMessageBox.Yes:event.ignore();return
        for reader in list(self.reader_windows):reader.close()
        self.ipc.close();event.accept()


def run_native(app,smoke=None):
    qt=W.QApplication.instance() or W.QApplication([]);qt.setApplicationName("物理实验助手");qt.setOrganizationName("PhysicsHarness");qt.setStyle("Fusion");qt.setStyleSheet(STYLE)
    window=MainWindow(app);window.show()
    if smoke:
        def finish():
            window.grab().save(str(smoke));window.close();qt.quit()
        C.QTimer.singleShot(800,finish)
    return qt.exec()


def activate_existing(data):
    qt=W.QApplication.instance() or W.QApplication([])
    socket=N.QLocalSocket();socket.connectToServer("PhysicsHarness."+digest(str(data))[:24])
    if socket.waitForConnected(1500):socket.write(b"activate");socket.flush();socket.waitForBytesWritten(500);socket.disconnectFromServer()
    else:W.QMessageBox.information(None,"物理实验助手","同一资料库的助手已经运行。请返回已有窗口。")
