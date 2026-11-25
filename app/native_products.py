"""Native PDF reader and the ordinary, removable products folder."""
from pathlib import Path
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W, QtPdf, QtPdfWidgets
from .native_widgets import label, button, row


class PdfReader(W.QWidget):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        layout=W.QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.document=QtPdf.QPdfDocument(self)
        error=self.document.load(str(path))
        if error != QtPdf.QPdfDocument.Error.None_:
            raise ValueError("PDF 无法打开："+str(error))
        self.view=QtPdfWidgets.QPdfView(self)
        self.view.setDocument(self.document)
        self.view.setPageMode(QtPdfWidgets.QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QtPdfWidgets.QPdfView.ZoomMode.FitToWidth)
        self.page=W.QSpinBox();self.page.setRange(1,max(1,self.document.pageCount()))
        self.page.setPrefix("第 ");self.page.setSuffix(" 页")
        def jump(value):self.view.pageNavigator().jump(value-1,C.QPointF())
        self.page.valueChanged.connect(jump)
        def sync(value):
            self.page.blockSignals(True);self.page.setValue(value+1);self.page.blockSignals(False)
        self.view.pageNavigator().currentPageChanged.connect(sync)
        def zoom(factor):
            scale=self.view.zoomFactor();self.view.setZoomMode(QtPdfWidgets.QPdfView.ZoomMode.Custom);self.view.setZoomFactor(scale*factor)
        previous=button("上一页 (PgUp)",lambda:self.page.setValue(self.page.value()-1));previous.setShortcut(G.QKeySequence('PgUp'))
        following=button("下一页 (PgDown)",lambda:self.page.setValue(self.page.value()+1));following.setShortcut(G.QKeySequence('PgDown'))
        layout.addWidget(row(previous,self.page,label("共 "+str(self.document.pageCount())+" 页"),following,button("缩小",lambda:zoom(.8)),button("放大",lambda:zoom(1.25)),button("适应页宽",lambda:self.view.setZoomMode(QtPdfWidgets.QPdfView.ZoomMode.FitToWidth))))
        layout.addWidget(self.view,1)


def open_product_path(ui, path):
    """Both model requests and product clicks use the same native PDF reader."""
    path=Path(path)
    if not path.exists():
        ui.notify('文件已移走，可从内部记录重新生成。');return False
    if path.is_file() and path.suffix.lower()=='.pdf':
        reader=W.QDialog(None);reader.setWindowTitle(path.stem);reader.resize(1080,850)
        layout=W.QVBoxLayout(reader);pdf=PdfReader(path,reader);layout.addWidget(pdf)
        def close_document(_=None):
            pdf.view.setDocument(None);pdf.document.close()
        reader.finished.connect(close_document)
        close=W.QDialogButtonBox(W.QDialogButtonBox.Close);close.rejected.connect(reader.reject);layout.addWidget(close)
        ui.show_reader(reader);return True
    result=G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(path)))
    if not result:ui.notify('系统未能打开文件，请在产物文件夹中选择阅读程序。')
    return result


class ProductsDialog(W.QDialog):
    def __init__(self,ui,experiment_id=None):
        super().__init__(None);self.ui=ui;self.experiment_id=experiment_id
        self.setWindowTitle("生成产物");self.resize(1030,760);layout=W.QVBoxLayout(self)
        layout.addWidget(label("可直接阅读、复制或拿走。删除这里的文件不会删除内部记录；需要时可重新生成。","muted"))
        self.location=label(str(ui.app.publications.root),"muted");layout.addWidget(self.location)
        self.tree=W.QTreeWidget();self.tree.setHeaderLabels(["实验、类型与文稿","文件状态"]);self.tree.setColumnWidth(0,710);layout.addWidget(self.tree,1)
        self.tree.itemDoubleClicked.connect(lambda item,*_:self.open_item(item))
        layout.addWidget(row(button("打开文件",lambda:self.open_item(self.tree.currentItem()),True),button("打开所在文件夹",lambda:self.open_item(self.tree.currentItem(),True)),button("从内部记录重新生成",self.restore),button("打开产物总文件夹",self.open_root)))
        self.refresh()

    def refresh(self):
        self.tree.clear()
        experiments=self.ui.app.state()["experiments"]
        if not self.experiment_id:experiments.append({"id":None,"title":"跨实验原始记录整理"})
        for exp in experiments:
            if self.experiment_id and exp["id"]!=self.experiment_id:continue
            top=W.QTreeWidgetItem([exp["title"],""]);self.tree.addTopLevelItem(top)
            groups={}
            from .publications import category
            for record in self.ui.app.publications.list(exp["id"]):
                obj=self.ui.app.gateway.read(record["id"],record["revision"]);kind=category(obj)
                if kind not in groups:groups[kind]=W.QTreeWidgetItem(top,[kind,""])
                status="已生成" if record.get("status")=="ready" and record.get("available") else "可从内部记录恢复" if record.get("status")!="failed" else "生成失败，可重试"
                if record.get('status')=='working':status='写作中 · 完成后更新副本'
                if status=='已生成' and obj.get('layout',{}).get('pdf_pages'):status+=' · PDF '+str(obj['layout']['pdf_pages'])+' 页'
                title=record['title']+f" · 第{record['revision']}版"+('（当前）' if record.get('is_current') else '')
                item=W.QTreeWidgetItem(groups[kind],[title,status]);item.setData(0,C.Qt.UserRole,record)
                for file in record.get("files",[]):
                    if file["name"].endswith((".docx",".pdf",".xlsx",".py",".csv",".png",".tex",".md")):
                        present=(Path(record["folder"])/file["name"]).is_file()
                        child=W.QTreeWidgetItem(item,[file["name"],"可打开" if present else "已移走"]);child.setData(0,C.Qt.UserRole,{**record,"file":file["name"]})
        self.tree.expandToDepth(2)

    def open_root(self):
        path=self.ui.app.publications.root;path.mkdir(parents=True,exist_ok=True)
        G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(path)))

    def open_item(self,item,folder_only=False):
        if not item:return
        record=item.data(0,C.Qt.UserRole)
        if not record:return
        folder=Path(record["folder"]) if record.get("folder") else None
        if folder is None or not folder.is_dir():
            self.ui.notify("这份外部产物尚未生成或已被移走。点“从内部记录重新生成”即可找回。");return
        chosen=record.get('file')
        if not chosen and not folder_only:
            chosen=next((f['name'] for f in record.get('files',[]) if f['name'].endswith('.pdf')),None)
        path=folder/chosen if chosen and not folder_only else folder
        if path.suffix.lower() not in (".pdf",".docx",".xlsx",".png",".csv",".tex",".md") and path.is_file():path=path.parent
        if not path.exists():self.ui.notify("文件已移走，可从内部记录重新生成。");return
        open_product_path(self.ui,path)

    def restore(self):
        item=self.tree.currentItem();record=item.data(0,C.Qt.UserRole) if item else None
        if not record:self.ui.notify("请先选择要重新生成的文稿或程序。");return
        self.ui.submit("重新生成产物",lambda:self.ui.app.publications.publish(self.ui.app.gateway.read(record["id"],record["revision"])),lambda _:(self.refresh(),self.ui.status.showMessage("完整文件已重新放入产物文件夹",7000)))
