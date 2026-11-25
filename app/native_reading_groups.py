"""A native form for one repeat of a measurement, backed by its original readings model."""
from PySide6 import QtCore as C, QtWidgets as W

from .native_worksheet_layout import StepCells
from .worksheet_entry import entry_groups, pairing
from .worksheet_presentation import scientific_text
from .worksheet_steps import reading_hint


class ReadingGroups(W.QWidget):
    def __init__(self, host, table, model):
        super().__init__(host)
        self.host, self.table, self.model = host, table, model
        self.pair = pairing(table)
        self.outputs = []
        self.setProperty("measurementTable",table["id"])
        self.layout = W.QVBoxLayout(self)
        self.layout.setContentsMargins(0,0,0,0)
        bar = W.QHBoxLayout()
        self.layout.addLayout(bar)
        self.prev = host.btn(bar,"上一组",lambda:self.choose(-1))
        self.picker = W.QComboBox()
        self.picker.setAccessibleName("测量组")
        bar.addWidget(self.picker)
        self.next = host.btn(bar,"下一组",lambda:self.choose(1))
        bar.addStretch()
        host.btn(bar,"添加一组",self.add_group)
        more=W.QToolButton()
        more.setText("更多")
        more.setPopupMode(W.QToolButton.InstantPopup)
        menu=W.QMenu(more)
        menu.addAction("导入测量记录…",lambda:host.import_file(table["id"]))
        menu.addAction("通道方向…",lambda:host.channel_direction(table["id"]))
        more.setMenu(menu);bar.addWidget(more)
        self.body = None
        self.picker.currentIndexChanged.connect(self.show_group)
        self.model.modelReset.connect(self.reload)
        self.model.dataChanged.connect(self.update_outputs)
        self.reload()

    def choose(self,offset):
        self.picker.setCurrentIndex(self.picker.currentIndex()+offset)

    def reload(self,select_last=False):
        rows=self.model.record["rows"]
        self.groups=entry_groups(rows,self.pair)
        old=self.picker.currentIndex()
        if old<0:old=self.host.group_positions.get((self.host.page["id"],self.table["id"]),0)
        self.picker.blockSignals(True)
        self.picker.clear()
        self.picker.addItems([f"第 {i+1} 组 / 共 {len(self.groups)} 组" for i in range(len(self.groups))])
        index=len(self.groups)-1 if select_last else min(max(old,0),len(self.groups)-1)
        self.picker.setCurrentIndex(index)
        self.picker.blockSignals(False)
        self.show_group(index)

    def add_group(self):
        rows=self.model.record["rows"]
        new=[{self.pair["variant"]:v} for v in self.pair["variants"]] if self.pair else [{}]
        self.model.beginInsertRows(C.QModelIndex(),len(rows),len(rows)+len(new)-1)
        rows.extend(new)
        self.model.endInsertRows()
        self.reload(True)
        self.host.changed()
        first=self.body.findChild(W.QLineEdit)
        if first:first.setFocus()

    def field(self,indices,column,slots):
        item=self.table["columns"][column]
        box=W.QLineEdit(str(self.model.record["rows"][indices[0]].get(item["id"],"")))
        box.setPlaceholderText(scientific_text(reading_hint(item)))
        box.setToolTip(scientific_text(item.get("hint") or reading_hint(item)))
        def edit(text):
            # Positional labels on a blank paired form are saved only on actual input.
            for row,tag in slots:
                self.model.record["rows"][row][self.pair["variant"]]=tag
            for row in indices:
                self.model.setData(self.model.index(row,column+1),text)
        box.textEdited.connect(edit)
        return self.host.quantity_cell(item,box)

    def use_row(self,row,caption):
        check=W.QCheckBox(caption)
        check.setChecked(not self.model.record["rows"][row].get("_excluded"))
        check.setToolTip("取消选用会保留读数，并从汇总计算和拟合中排除。")
        check.toggled.connect(lambda yes:self.model.setData(self.model.index(row,0),
            C.Qt.Checked if yes else C.Qt.Unchecked,C.Qt.CheckStateRole))
        return check

    def show_group(self,index):
        if self.body is not None:
            self.layout.removeWidget(self.body)
            self.body.hide()
            self.body.deleteLater()
        self.body=W.QWidget()
        self.body.setObjectName("measurementGroup")
        self.layout.addWidget(self.body)
        body=W.QVBoxLayout(self.body)
        body.setContentsMargins(10,7,10,9)
        self.outputs=[]
        self.prev.setEnabled(index>0)
        self.next.setEnabled(0<=index<len(self.groups)-1)
        if index<0:
            body.addWidget(W.QLabel("添加一组即可填写读数。"))
            return
        self.host.group_positions[self.host.page["id"],self.table["id"]]=index
        indices=self.groups[index]
        pair=self.pair if len(indices)>1 else None
        columns=self.table["columns"]
        by_id={c["id"]:i for i,c in enumerate(columns)}
        rows=self.model.record["rows"]
        slots=[(r,str(rows[r].get(pair["variant"]) or tag)) for r,tag in zip(indices,pair["variants"])] if pair else []
        active=list(range(len(columns)))
        if pair:
            shared=by_id[pair["condition"]]
            body.addWidget(StepCells([self.field(indices,shared,slots)]))
            active=[i for i in active if i not in (shared,by_id[pair["variant"]])]
        for offset,row in enumerate(indices):
            body.addWidget(self.use_row(row,slots[offset][1]+" · 参与计算" if pair else "本组参与计算"))
            cells=[self.field([row],i,slots) for i in active]
            for i,definition in enumerate(self.table.get("derived",[]),len(columns)+1):
                box=W.QLineEdit()
                box.setReadOnly(True)
                box.setStyleSheet("QLineEdit {background:#e8f4f0;color:#0a6056;font-weight:600;}")
                box.setPlaceholderText("随读数计算")
                self.outputs.append((row,i,box))
                cells.append(self.host.quantity_cell(definition,box))
            body.addWidget(StepCells(cells))
        self.update_outputs()

    def update_outputs(self,*_):
        for row,column,box in self.outputs:
            value=self.model.data(self.model.index(row,column),C.Qt.DisplayRole)
            box.setText("" if value in (None,"—") else str(value))
