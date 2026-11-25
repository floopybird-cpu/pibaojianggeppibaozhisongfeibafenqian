from PySide6 import QtWidgets as W

from .knowledge_index import set_scope
from .native_widgets import button, label, row
from .page_navigation import select_page_numbers


class KnowledgeSourceDialog(W.QDialog):
    def __init__(self,ui,sid):
        super().__init__(ui)
        self.ui=ui
        self.sid=sid
        record=ui.app.store.one("SELECT name,(SELECT count(*) FROM pages WHERE source_id=s.id) AS n FROM sources s WHERE id=?",(sid,))
        self.count=record["n"]
        scope=ui.app.store.one("SELECT first_page,last_page FROM kb_book WHERE source_id=?",(sid,))
        self.setWindowTitle("整理到知识库")
        layout=W.QVBoxLayout(self)
        layout.addWidget(label(record["name"],"h2"))
        progress=ui.app.store.one("""SELECT count(DISTINCT page) AS n FROM kb_done
            WHERE source_id=? AND topic='' AND page BETWEEN ? AND ?""",(sid,(scope or {}).get("first_page") or 1,(scope or {}).get("last_page") or self.count))
        notes=ui.app.store.one("SELECT count(DISTINCT object_id) AS n FROM kb_notes WHERE source_id=?",(sid,))
        if progress["n"] or notes["n"]:
            layout.addWidget(label(f"已整理 {progress['n']} 页 · 已存 {notes['n']} 条知识","muted"))
        form=W.QFormLayout()
        self.mode=W.QComboBox()
        self.mode.addItem("按主题找原页","topic")
        self.mode.addItem("为已有知识建立多级目录","catalog")
        self.mode.addItem("整理指定页段","pages")
        self.mode.addItem("整理有效范围内的整本教材","book")
        self.topic=W.QLineEdit();self.topic.setPlaceholderText("想让助手掌握什么？")
        self.pages=W.QLineEdit();self.pages.setPlaceholderText("文件页序，例如 8-16，23")
        form.addRow("整理方式",self.mode);form.addRow("主题",self.topic);form.addRow("页码",self.pages)
        self.first=W.QSpinBox();self.last=W.QSpinBox()
        for field in (self.first,self.last):field.setRange(1,self.count)
        self.first.setValue((scope or {}).get("first_page") or 1)
        self.last.setValue((scope or {}).get("last_page") or self.count)
        self.role=W.QComboBox()
        self.role.addItem("长期参考教材","reference");self.role.addItem("本实验讲义","handout")
        prior=ui.app.store.one("SELECT role FROM kb_source_roles WHERE source_id=?",(sid,))
        if prior:self.role.setCurrentIndex(self.role.findData(prior["role"]))
        form.addRow("资料用途",self.role)
        form.addRow("教材有效范围",row(self.first,label("至"),self.last))
        layout.addLayout(form)
        layout.addWidget(label("有效范围按 PDF 文件页序保存。已完成的页段可接续；原件仍可完整阅读。","muted"))
        self.mode.currentIndexChanged.connect(self.update_fields);self.update_fields()
        layout.addWidget(button("开始整理",self.start,True));self.result_job=None
        self.resize(580,300)

    def update_fields(self):
        self.topic.setEnabled(self.mode.currentData()=="topic")
        self.pages.setEnabled(self.mode.currentData()=="pages")
        for control in (self.first,self.last,self.role):control.setEnabled(self.mode.currentData()!="catalog")

    def start(self):
        def submit():
            mode=self.mode.currentData()
            if mode=="catalog":
                from .knowledge_catalog import queue
                return queue(self.ui.app.knowledge,self.sid)
            topic=self.topic.text().strip() if mode=="topic" else ""
            if mode=="topic" and not topic:raise ValueError("请填写想整理的主题")
            nums=select_page_numbers(self.pages.text(),self.count) if mode=="pages" else None
            if mode=="pages" and not nums:raise ValueError("请填写所需页码")
            first,last=self.first.value(),self.last.value()
            if nums and (min(nums)<first or max(nums)>last):raise ValueError("所选页超出教材有效范围")
            set_scope(self.ui.app.store,self.sid,first,last,self.role.currentData())
            return self.ui.app.agent.distill(self.sid,nums,topic)
        self.result_job=self.ui.safe(submit)
        if self.result_job:self.accept()
