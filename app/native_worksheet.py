"""Native data entry, plots, sourced results and conversation on one experiment workbench."""
import csv
import io
import json
import math
import os
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from PySide6 import QtCore as C, QtGui as G, QtWidgets as W

from .native_markdown import MarkdownView
from .tabular import read_tables
from .worksheet_math import compute, display_series, effective_fields, number
from .worksheet_units import convert, numeric_text, read_quantity
from .worksheet_output import configure_fonts, export, formula, label
from .worksheets import table_state
from .worksheet_presentation import (entry_unit, equation_symbols, field_text, missing_readings, quantity_label,
                                     row_equation, scientific_text, unit_text)
from .worksheet_steps import difference_display, measurement_steps, reading_hint
from .native_worksheet_layout import StepCells, TableChart


def numeric_field(field):
    return field.get("type")!="text" and field.get("unit") not in ("文字","文本","类别","名称","描述")


def clean_numeric_line(line):
    text=line.text();clean=numeric_text(text)
    if clean!=text:
        cursor=len(numeric_text(text[:line.cursorPosition()]))
        line.setText(clean);line.setCursorPosition(cursor)
    return clean


def compact_reading(raw):
    """Shorten instrument notation for display while retaining every recorded decimal place."""
    match=re.fullmatch(r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))[eE]([-+]?\d{1,2})",raw)
    if not match:
        return raw
    exponent=int(match[2])
    compact=f"{match[1]}e{exponent}"
    if abs(exponent)<=12:
        value=Decimal(raw)
        if value.as_tuple().exponent<=0:
            fixed=format(value,"f")
            if len(fixed)<=len(compact):
                return fixed
    return compact


class ReadingsModel(C.QAbstractTableModel):
    def __init__(self, table, record, change):
        super().__init__();self.table=table;self.record=record;self.change=change
        self.columns=table["columns"]+table.get("derived",[]);self.series={}

    def rowCount(self,parent=C.QModelIndex()):
        return 0 if parent.isValid() else len(self.record["rows"])

    def columnCount(self,parent=C.QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)+1

    def headerData(self,section,orientation,role=C.Qt.DisplayRole):
        if orientation==C.Qt.Horizontal and section and role==C.Qt.ToolTipRole:
            item=self.columns[section-1]
            return quantity_label(item)+" · "+(reading_hint(item) if section<=len(self.table["columns"]) else "由本行读数计算")
        if role!=C.Qt.DisplayRole:
            return None
        if orientation==C.Qt.Vertical:return str(section+1)
        if section==0:return "选用"
        item=self.columns[section-1]
        name=quantity_label(item,unit="")
        suffix=quantity_label(item)[len(name):].strip()
        return name+"\n"+suffix if suffix else name

    def data(self,index,role=C.Qt.DisplayRole):
        if not index.isValid():return None
        r=self.record["rows"][index.row()];col=index.column()
        if col==0 and role==C.Qt.CheckStateRole:
            return C.Qt.Unchecked if r.get("_excluded") else C.Qt.Checked
        if col>len(self.table["columns"]):
            derived=self.columns[col-1]
            if role==C.Qt.ToolTipRole:return derived["label"]+" · 按本行读数自动计算"
            if role==C.Qt.ForegroundRole:return G.QColor("#17675c")
            if role==C.Qt.DisplayRole:
                values=self.series.get(derived["id"],())
                if index.row()<len(values) and math.isfinite(values[index.row()]):
                    return format(values[index.row()],".6g")
                return "—"
            return None
        if col:
            field=self.table["columns"][col-1];raw=str(r.get(field["id"],""))
            if role==C.Qt.EditRole:return raw
            if role==C.Qt.DisplayRole:
                if not raw:return reading_hint(field)
                return compact_reading(raw) if numeric_field(field) else raw
            if role==C.Qt.ForegroundRole and not raw:return G.QColor("#63767a")
            if role==C.Qt.ToolTipRole:return (raw+"\n" if raw else "")+reading_hint(field)
            if role==C.Qt.ForegroundRole and raw.strip() and not math.isfinite(read_quantity(raw,field["unit"])) and field["unit"] not in ("文字","文本","描述"):
                return G.QColor("#ae5413")
        if role==C.Qt.BackgroundRole and r.get("_excluded"):return G.QColor("#eeefef")
        return None

    def flags(self,index):
        basic=super().flags(index)
        if index.column()>len(self.table["columns"]):return basic
        return basic | (C.Qt.ItemIsUserCheckable if index.column()==0 else C.Qt.ItemIsEditable)

    def setData(self,index,value,role=C.Qt.EditRole):
        if not index.isValid():return False
        row=self.record["rows"][index.row()]
        if index.column()==0 and role==C.Qt.CheckStateRole:
            row["_excluded"]=value not in (C.Qt.Checked,C.Qt.Checked.value)
        elif 0<index.column()<=len(self.table["columns"]) and role==C.Qt.EditRole:
            row[self.table["columns"][index.column()-1]["id"]]=str(value).strip()
        else:return False
        self.dataChanged.emit(index,index);self.change();return True

    def refresh_computed(self,page,fields):
        if not self.table.get("derived"):return
        self.series=display_series(page,self.table,self.record["rows"],fields,self.record.get("directions"))
        if self.rowCount():
            self.dataChanged.emit(self.index(0,len(self.table["columns"])+1),self.index(self.rowCount()-1,len(self.columns)))

    def paste(self,start,text):
        lines=list(csv.reader(io.StringIO(text),delimiter="\t"))
        if not lines:return
        row=start.row() if start.isValid() else 0
        col=max(0,start.column()-1) if start.isValid() else 0
        keys=[c["id"] for c in self.table["columns"]][col:]
        if any(len(values)>len(keys) for values in lines):
            raise ValueError("粘贴列数超过当前表；请从相应起始列粘贴，或使用“导入并对应列”。")
        self.beginResetModel()
        rows=self.record["rows"]
        while len(rows)<row+len(lines):rows.append({})
        for i,values in enumerate(lines):
            rows[row+i].update(dict(zip(keys,values)))
        self.endResetModel();self.change()


class QuantityHeader(W.QHeaderView):
    """Native header geometry and selection, with rich quantity labels."""
    def paintSection(self, painter, rect, section):
        model = self.model()
        if not section or not hasattr(model, "columns"):
            return super().paintSection(painter, rect, section)
        item = model.columns[section-1]
        from .scientific_markup import parts
        if not any(math for math, _ in parts(item["label"])):
            return super().paintSection(painter, rect, section)
        option = W.QStyleOptionHeader()
        self.initStyleOption(option)
        option.rect = rect
        option.text = ""
        self.style().drawControl(W.QStyle.CE_HeaderSection, option, painter, self)
        doc = G.QTextDocument()
        doc.setDocumentMargin(2)
        doc.setDefaultFont(self.font())
        doc.setHtml('<div align="center">'+quantity_label(item, True)+'</div>')
        doc.setTextWidth(rect.width())
        painter.save()
        painter.setClipRect(rect)
        painter.translate(rect.x(), rect.y()+max(0, (rect.height()-doc.size().height())/2))
        doc.drawContents(painter)
        painter.restore()

    def sectionSizeFromContents(self, index):
        size = super().sectionSizeFromContents(index)
        model = self.model()
        if index and hasattr(model, "columns"):
            doc = G.QTextDocument()
            doc.setDefaultFont(self.font())
            doc.setHtml(quantity_label(model.columns[index-1], True))
            size.setHeight(max(size.height(), math.ceil(doc.size().height())+4))
        return size


class ReadingGrid(W.QTableView):
    def __init__(self):
        super().__init__()
        self.setHorizontalHeader(QuantityHeader(C.Qt.Horizontal, self))
        self.custom_widths=False
        self.horizontalHeader().sectionResized.connect(self.remember_width)

    def remember_width(self,*_):
        if self.horizontalHeader().underMouse() and W.QApplication.mouseButtons() & C.Qt.LeftButton:
            self.custom_widths=True

    def resizeEvent(self,event):
        super().resizeEvent(event)
        model=self.model()
        if model is None or self.custom_widths:return
        available=max(0,self.viewport().width()-self.columnWidth(0))
        metrics=self.horizontalHeader().fontMetrics()
        widths=[max(90,min(240,metrics.horizontalAdvance(quantity_label(c,unit=""))+20)) for c in model.columns]
        if widths:
            spare=max(0,available-sum(widths))//len(widths)
            for section,width in enumerate(widths,1):
                self.setColumnWidth(section,width+spare)

    def keyPressEvent(self,event):
        if event.matches(G.QKeySequence.Paste):
            try:self.model().paste(self.currentIndex(),W.QApplication.clipboard().text())
            except ValueError as err:W.QMessageBox.information(self,"粘贴读数",str(err))
            return
        super().keyPressEvent(event)


def instrument_header(header):
    """Return a named acquisition channel or heading and its explicit unit."""
    text = header.strip().replace("（","(").replace("）",")")
    match = re.fullmatch(r"(.+?)\s*[\[(]([^()\[\]]+)[)\]]", text)
    if match:
        return match[1].strip(), match[2].strip()
    match = re.fullmatch(r"((?:ch(?:annel)?|通道)[ _-]*\d+)[ _-]*(mV|uV|µV|μV|V|mA|A|°C|℃|K)",text,re.I)
    return (match[1], match[2]) if match else (text, "")


def suggested_columns(columns, headers):
    def channel(name):
        match = re.search(r"(?<![A-Za-z0-9])(?:ch(?:annel)?|通道)[ _-]*(\d+)(?!\d)", name, re.I)
        return int(match[1]) if match else None

    suggested = {}
    for field in columns:
        names = [str(v).strip().casefold() for v in (field["id"], field["label"], label(field), *field.get("source_headers", []))]
        matches = [h for h in headers if h.strip().casefold() in names]
        if not matches:
            numbers = {n for name in names if (n := channel(name)) is not None}
            for header in headers:
                name, unit = instrument_header(header)
                number = channel(name) if re.fullmatch(r"(?:ch(?:annel)?|通道)[ _-]*\d+",name,re.I) else None
                time_axis = (name.casefold() in {"time","t","时间","时刻"} and
                             re.search(r"采样时刻|采样时间|时间序列|时间戳|记录时刻|^(?:时间|时刻)$", field["label"]) and
                             not re.search(r"间隔|周期|延迟|开始|结束|起点|终点", field["label"]))
                if name.casefold() not in names and not time_axis and (number is None or numbers != {number}):
                    continue
                if unit:
                    try:convert(1,unit,field["unit"])
                    except ValueError:continue
                matches.append(header)
        if len(matches) == 1:
            suggested[field["id"]] = matches[0]
    from collections import Counter
    counts = Counter(suggested.values())
    return {key:value for key,value in suggested.items() if counts[value] == 1}


class ImportColumns(W.QDialog):
    def __init__(self,parent,table,parsed):
        super().__init__(parent);self.setWindowTitle("把文件列对应到实验量");self.resize(690,520)
        self.table=table;self.parsed=parsed
        layout=W.QVBoxLayout(self)
        self.sheets=W.QComboBox()
        for sheet in parsed:self.sheets.addItem(f"{sheet['name']} · {len(sheet['rows'])} 行")
        layout.addWidget(self.sheets)
        self.preview=W.QTableWidget();self.preview.setEditTriggers(W.QAbstractItemView.NoEditTriggers);layout.addWidget(self.preview,1)
        self.form=W.QFormLayout();layout.addLayout(self.form)
        self.mode=W.QComboBox();self.mode.addItems(["追加到现有读数","替换当前表的读数"]);self.form.addRow("导入方式",self.mode)
        self.choices={}
        self.units={}
        for c in table["columns"]:
            box=W.QComboBox();source_unit=W.QComboBox();source_unit.setEditable(True)
            source_unit.setSizeAdjustPolicy(W.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            source_unit.setMinimumContentsLength(6)
            from .worksheet_units import FAMILIES,equivalent_form
            choices=next((family for family in FAMILIES if equivalent_form(c["unit"]) in family),())
            source_unit.addItems(list(dict.fromkeys([c["unit"],*("°C" if u=="degC" else u for u in choices)])))
            source_unit.setToolTip("选择或输入原始单位，导入时自动换算")
            line=W.QHBoxLayout();line.addWidget(box,1);line.addWidget(W.QLabel("原单位"));line.addWidget(source_unit)
            self.form.addRow(label(c),line);self.choices[c["id"]]=box;self.units[c["id"]]=source_unit
            box.currentIndexChanged.connect(lambda _,box=box,inp=source_unit,target=c["unit"]:self.unit_hint(box,inp,target))
        buttons=W.QDialogButtonBox(W.QDialogButtonBox.Ok|W.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.sheets.currentIndexChanged.connect(self.refresh);self.refresh()

    def refresh(self):
        part=self.parsed[self.sheets.currentIndex()]
        self.preview.setColumnCount(len(part["columns"]));self.preview.setHorizontalHeaderLabels(part["columns"])
        rows=part["rows"][:4];self.preview.setRowCount(len(rows))
        for r,row in enumerate(rows):
            for i,c in enumerate(part["columns"]):self.preview.setItem(r,i,W.QTableWidgetItem(str(row.get(c,""))))
        suggested=suggested_columns(self.table["columns"],part["columns"])
        for c in self.table["columns"]:
            box=self.choices[c["id"]];box.clear();box.addItem("留空",None)
            for original in part["columns"]:
                box.addItem(original,(original,"raw"))
                if any(_date(r.get(original)) is not None for r in rows):
                    box.addItem(original+" → 自首个时间起的秒数",(original,"seconds"))
            assigned=suggested.get(c["id"])
            if assigned:
                box.setCurrentIndex(next(i for i in range(1,box.count()) if tuple(box.itemData(i))==(assigned,"raw")))

    def accept(self):
        self.mapping={k:w.currentData() for k,w in self.choices.items() if w.currentData()}
        if not self.mapping:
            W.QMessageBox.information(self,"对应列","至少选一列要使用的读数。");return
        try:
            for c in self.table["columns"]:
                key=c["id"]
                if key in self.mapping:
                    original,mode=self.mapping[key];unit="s" if mode=="seconds" else self.units[key].currentText().strip() or c["unit"]
                    convert(1.,unit,c["unit"]);self.mapping[key]=(original,mode,unit,c["unit"])
        except ValueError as err:
            W.QMessageBox.information(self,"核对单位",str(err));return
        super().accept()

    def unit_hint(self,box,field,target):
        choice=box.currentData()
        if not choice:return
        column,mode=choice
        match=re.search(r"摄氏度|℃|°C|\(([^()]+)\)|_(mm|cm|mL|mV|ms|s|K|V)$",column)
        guessed="s" if mode=="seconds" else instrument_header(column)[1] or ((match[1] or match[2] or match[0]) if match else target)
        try:
            convert(1.,guessed,target)
            from .worksheet_units import equivalent_form
            field.setCurrentText("°C" if equivalent_form(guessed)=="degC" else guessed)
        except ValueError:field.setCurrentText(target)


def _date(value):
    try:return datetime.fromisoformat(str(value))
    except (ValueError,TypeError):return None


def map_rows(sheet,mapping):
    baseline={}
    result=[]
    for i,raw in enumerate(sheet["rows"],1):
        row={}
        for key,rule in mapping.items():
            col,mode,*units=rule
            value=raw.get(col,"")
            if mode=="seconds" and value not in (None,""):
                when=_date(value)
                if not when:raise ValueError(f"第 {i} 行「{col}」无法按日期时间换算：{value}")
                baseline.setdefault(col,when)
                value=(when-baseline[col]).total_seconds()
            if len(units)==2 and units[0]!=units[1] and math.isfinite(number(value)):
                value=convert(number(value),*units)
            row[key]="" if value is None else str(value)
        result.append(row)
    return result


class WorksheetWindow(W.QDialog):
    def __init__(self,host,identity,page_id=None):
        super().__init__(host)
        self.host=host;self.app=host.app;self.service=self.app.worksheets;self.identity=identity
        self.obj=self.app.store.object(identity);self.version,self.data=self.service.state(identity)
        self.setWindowTitle(self.obj["title"]+" · 数据处理台");self.resize(1270,850)
        self.setWindowFlag(C.Qt.Tool,True)
        self.setWindowFlag(C.Qt.WindowMaximizeButtonHint,True)
        self.loading=False;self.dirty=False;self.job=None;self.group_positions={}
        self.saver=C.QTimer(self);self.saver.setSingleShot(True);self.saver.timeout.connect(self.flush)
        self.preview_timer=C.QTimer(self);self.preview_timer.setSingleShot(True);self.preview_timer.timeout.connect(self.update_readings)
        layout=W.QVBoxLayout(self)
        top=W.QHBoxLayout();layout.addLayout(top)
        top.addWidget(W.QLabel("子实验"));self.selector=W.QComboBox();self.selector.setSizeAdjustPolicy(W.QComboBox.AdjustToContents);top.addWidget(self.selector)
        for page in self.obj["content"]["pages"]:self.selector.addItem(scientific_text(page["title"]),page["id"])
        from .measurement_plan import opening_page
        page_id=opening_page(self.obj["content"]["pages"],page_id)
        if page_id and self.selector.findData(page_id)>=0:self.selector.setCurrentIndex(self.selector.findData(page_id))
        top.addStretch();self.notice=W.QLabel();top.addWidget(self.notice)
        self.retry_button=self.btn(top,"补全当前页",self.retry_page)
        self.import_button=self.btn(top,"导入测量数据…",lambda:self.import_file())
        self.btn(top,"导出数据和结果",self.publish)
        spl=W.QSplitter();layout.addWidget(spl,1)
        self.left=W.QScrollArea();self.left.setWidgetResizable(True);spl.addWidget(self.left)
        right=W.QWidget();rl=W.QVBoxLayout(right);spl.addWidget(right);spl.setSizes([690,550])
        sourcebar=W.QHBoxLayout();rl.addLayout(sourcebar)
        self.links=W.QComboBox();self.links.setSizeAdjustPolicy(W.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        sourcebar.addWidget(self.links,1);self.source_button=self.btn(sourcebar,"看原页",self.show_source)
        self.right_tabs=W.QTabWidget();rl.addWidget(self.right_tabs,1)
        self.result=MarkdownView("",self.app.store,auto_height=False,strict=False,math_size=20);self.result.anchorClicked.connect(self.host.follow_source)
        detail=W.QWidget();detail_layout=W.QVBoxLayout(detail);detail_layout.setContentsMargins(3,3,3,3)
        detail_layout.addWidget(self.result,1)
        self.explain=W.QCheckBox("显示计算说明与符号");detail_layout.addWidget(self.explain)
        self.explain.toggled.connect(self.render)
        self.right_tabs.addTab(detail,"公式与结果")
        configure_fonts()
        self.conversation=MarkdownView("",self.app.store,auto_height=False,strict=False);self.conversation.anchorClicked.connect(self.host.follow_source);self.right_tabs.addTab(self.conversation,"本实验问答")
        self.ask=W.QLineEdit();self.ask.setPlaceholderText("问这个结果的出处，或说“多测两组”“改用加权拟合”");self.ask.returnPressed.connect(self.send)
        bottom=W.QHBoxLayout();rl.addLayout(bottom);bottom.addWidget(self.ask,1);self.ask_btn=self.btn(bottom,"发送",self.send)
        self.selector.currentIndexChanged.connect(self.build)
        W.QApplication.instance().focusChanged.connect(self.remember_table)
        self.timer=C.QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(850)
        self.build();self.chat()

    def btn(self,layout,text,fn):
        b=W.QPushButton(text);b.setAutoDefault(False);b.clicked.connect(fn);layout.addWidget(b);return b

    @property
    def page(self):
        return self.obj["content"]["pages"][self.selector.currentIndex()]

    def show_source(self):
        location=self.links.currentData()
        if location:self.host.open_source(*location)

    def remember_table(self,previous,current):
        while current and current is not self:
            key=current.property("measurementTable")
            if key and self.isAncestorOf(current):
                self.active_table=key
                break
            current=current.parentWidget()

    @staticmethod
    def quantity_cell(item,widget,unit=None):
        cell=W.QWidget()
        col=W.QVBoxLayout(cell);col.setContentsMargins(0,0,0,0);col.setSpacing(3)
        name=W.QLabel(quantity_label(item,True,unit));name.setTextFormat(C.Qt.RichText)
        name.setWordWrap(True);name.setBuddy(widget)
        name.setSizePolicy(W.QSizePolicy.Ignored,W.QSizePolicy.Preferred)
        col.addWidget(name);col.addWidget(widget)
        widget.setMinimumWidth(38);widget.setAccessibleName(item["label"])
        return cell

    def input_cell(self,f,state,values):
        unit=entry_unit(f) if numeric_field(f) else f["unit"]
        self.input_units[f["id"]]=unit
        raw=values.get(f["id"],"")
        shown=field_text(numeric_text(raw),f,unit) if numeric_field(f) else str(raw)
        line=W.QLineEdit(shown);self.field_inputs[f["id"]]=line
        hint=reading_hint(f)
        line.setPlaceholderText(scientific_text(hint))
        line.setToolTip(scientific_text(hint if unit!=f["unit"] else f.get("hint") or hint, True))
        source=self.prior_links.get(self.page["id"],{}).get(f["id"])
        if source:
            title=next((p["title"] for p in self.obj["content"]["pages"] if p["id"]==source[0]),source[0])
            line.setReadOnly(True);line.setPlaceholderText("等候「"+title+"」的结果")
            line.setToolTip("自动取自「"+title+"」，随上游读数更新")
        else:
            line.textEdited.connect(lambda text:self.field(state,f["id"],text))
        return self.quantity_cell(f,line,unit)

    def result_cell(self,item):
        value=W.QLineEdit()
        value.setReadOnly(True)
        value.setStyleSheet("QLineEdit {color:#0a6056;background:#e8f4f0;border:1px solid #afcfc4;font-weight:600;}")
        self.live_values[item["id"]]=value
        return self.quantity_cell(item,value)

    def reading_table(self,layout,table,state):
        key=table["id"]
        from .native_reading_groups import ReadingGroups
        from .worksheet_entry import samples
        model=ReadingsModel(table,table_state(state,table),self.changed)
        if not samples(table,self.page):
            editor=ReadingGroups(self,table,model)
            self.table_widgets[key]=(editor,model)
            layout.addWidget(editor)
            return
        grid=ReadingGrid();grid.setProperty("measurementTable",key)
        grid.setModel(model);grid.horizontalHeader().setSectionResizeMode(W.QHeaderView.Interactive)
        grid.horizontalHeader().setDefaultSectionSize(145);grid.setColumnWidth(0,50)
        grid.setAlternatingRowColors(True);grid.setMinimumHeight(145);grid.setMaximumHeight(215)
        grid.setToolTip("可直接粘贴 Excel 的矩形区域；取消选用可保留原始读数。")
        bar=W.QHBoxLayout();layout.addLayout(bar)
        self.btn(bar,"导入此表…",lambda:self.import_file(key))
        self.btn(bar,"添加一行",lambda:self.add_row(key))
        self.btn(bar,"通道方向…",lambda:self.channel_direction(key));bar.addStretch()
        layout.addWidget(grid)
        self.table_widgets[key]=(grid,model)

    def build(self):
        self.loading=True
        from .worksheet_math import resolve_prior_results
        self.prior_links=self.service.prior_links(self.obj) if self.obj["content"].get("draft_ref") else {}
        resolve_prior_results(self.obj["content"]["pages"],self.data,self.prior_links)
        page=self.page;state=self.data.setdefault(page["id"],{})
        self.retry_button.setVisible(bool(page.get("unresolved")))
        self.import_button.setVisible(bool(page["tables"]))
        self.links.clear()
        for source in page["sources"]:
            a=self.app.store.anchor(source)
            self.links.addItem(f"{a['name']} · 第 {a['page']} 页",(a["source_id"],a["page"]))
        self.source_button.setEnabled(self.links.count()>0)
        panel=W.QWidget();pl=W.QVBoxLayout(panel);pl.setContentsMargins(5,5,6,5);pl.setSpacing(10)
        self.field_inputs={};self.input_units={};self.live_values={};self.charts={};self.table_widgets={}
        self.active_table=page["tables"][0]["id"] if page["tables"] else ""
        self.symbols=equation_symbols(page)
        steps,checks=measurement_steps(page)
        self.display_page={**page,"results":page["results"]+checks}
        values=effective_fields(page,state)
        fields={f["id"]:f for f in page["fields"]}
        results={r["id"]:r for r in self.display_page["results"]}
        tables={t["id"]:t for t in page["tables"]}
        from .worksheet_identity import FormulaGraph
        display_graph = FormulaGraph(self.display_page)
        for step in steps:
            group=W.QGroupBox(scientific_text(step["title"]));stack=W.QVBoxLayout(group);pl.addWidget(group)
            inputs=[self.input_cell(fields[key],state,values) for key in step["fields"]]
            outputs=[self.result_cell(results[key]) for key in step["results"]]
            if not step["tables"]:
                stack.addWidget(StepCells(inputs+outputs));continue
            before, after = [], []
            for key, cell in zip(step["results"], outputs):
                if display_graph.dependencies(results[key]["expr"]) & display_graph.columns.keys():
                    after.append(cell)
                else:
                    before.append(cell)
            if inputs or before:stack.addWidget(StepCells(inputs+before))
            for key in step["tables"]:
                if tables[key]["title"]!=step["title"]:stack.addWidget(W.QLabel(scientific_text(tables[key]["title"], True)))
                self.reading_table(stack,tables[key],state)
            if after:stack.addWidget(StepCells(after))
            for key in step["tables"]:
                plots=[p for p in page["plots"] if p["table"]==key]
                if plots:
                    chart=TableChart(self,tables[key],plots);chart.setProperty("measurementTable",key)
                    self.charts[key]=chart;stack.addWidget(chart)
        self.grids=[self.table_widgets[t["id"]] for t in page["tables"]]
        if page.get("observations"):
            group=W.QGroupBox("现象记录");observed=W.QFormLayout(group);pl.addWidget(group)
            for entry in page["observations"]:
                box=W.QLineEdit(state.get("observations",{}).get(entry["id"],""))
                box.setPlaceholderText(entry.get("meaning","记录你实际看到的现象"))
                box.textEdited.connect(lambda value,key=entry["id"]:self.observation(state,key,value))
                observed.addRow(entry["label"],box)
        pl.addStretch()
        self.left.setWidget(panel)
        self.loading=False;self.update_readings()

    def observation(self,state,key,value):
        state.setdefault("observations",{})[key]=value
        self.changed()

    def field(self,state,key,text):
        field=next((f for f in self.page["fields"] if f["id"]==key),None)
        if field is not None and numeric_field(field):
            text=numeric_text(text)
            if key in self.field_inputs:clean_numeric_line(self.field_inputs[key])
            unit=self.input_units.get(key,field["unit"])
            if unit!=field["unit"] and math.isfinite(number(text)):text=text.strip()+" "+unit
        for table in self.page["tables"]:
            rule=table.get("selection") or {}
            if key in (rule.get("min"),rule.get("max")):
                block=table_state(state,table)
                block.pop("window",None)
                block.pop("prior_bounds",None)
        state.setdefault("fields",{})[key]=text;self.changed()

    def changed(self):
        self.dirty=True;self.notice.setText("正在保存");self.preview_timer.start(150);self.saver.start(330)

    def update_readings(self):
        self.render()
        for chart in self.charts.values():chart.redraw()

    def flush(self):
        if not self.dirty:return True
        if self.preview_timer.isActive():
            self.preview_timer.stop();self.update_readings()
        try:
            self.version=self.service.save(self.identity,self.version,self.data)
        except ValueError as err:
            self.notice.setText(str(err));return False
        self.dirty=False;self.notice.setText("读数已保存")
        return True

    def render(self,*_):
        page=self.page;state=self.data.get(page["id"],{})
        rows=compute(self.display_page,state)
        missing_readings(self.display_page,state,rows)
        self.computed_rows=rows[:len(page["results"])]
        values=effective_fields(page,state)
        fields_by_id={f["id"]:f for f in page["fields"]}
        for check,row in zip(self.display_page["results"][len(page["results"]):],rows[len(page["results"]):]):
            difference_display(check,row,fields_by_id,values)
        for row,item in zip(rows,self.display_page["results"]):
            inline=self.live_values[item["id"]]
            pending=row.get("pending","")
            inline.setText(row.get("display",""))
            if pending.startswith("待填"):
                inline.setPlaceholderText("自动计算")
            else:
                inline.setPlaceholderText(pending or "自动计算")
            inline.setToolTip(pending or row.get("interval_note") or scientific_text(item.get("note") or item["label"], True))

        lines=[]
        for _,model in self.grids:model.refresh_computed(page,values)
        pairs=sorted(zip(rows,page["results"]),key=lambda pair:{"primary":0,"intermediate":1,"check":2}.get(pair[1].get("role"),1))
        self.equation_text={}
        for r,item in pairs:
            self.equation_text[item["id"]]=row_equation(item,r,self.symbols)
            lines.extend(["### "+item["label"],"$"+self.equation_text[item["id"]]+"$"])
            if r.get("pending"):lines.append(r["pending"])
            if r.get("interval_note"):lines.append(r["interval_note"])
            if item.get("uncertainty"):lines.append("标准不确定度　$u="+formula(item["uncertainty"],display_names=self.symbols)+"$")
            if self.explain.isChecked() and item.get("note"):lines.append(item["note"])
            if item.get("source_anchor"):
                anchor=self.app.store.anchor(item["source_anchor"])
                lines.append(f"[{anchor['name']} · 第{anchor['page']}页](physlab://page/{anchor['source_id']}/{anchor['page']})")
        if page.get("unresolved"):
            from .worksheet_presentation import unresolved_messages
            lines.append("### 计算关系需要核对")
            lines.extend("- " + message for message in unresolved_messages(page))
            lines.append("可用“补全当前页”重新核对这些计算。")
        if page.get("source_check_note"):lines.append(page["source_check_note"])
        blocked=[r for r in rows if "定义范围" in r.get("pending","")]
        if blocked:
            lines.append("若仪器的电压、电流或位移参考方向与公式相反，可在读数表下点“通道方向”，按实物接线确认。")
        if self.explain.isChecked() and (page["fields"] or page["tables"]):
            variables=[*page["fields"],*(c for t in page["tables"] for c in t["columns"])]
            lines.extend(["### 式中各量", "　".join("$"+self.symbols[v["id"]]+"$ — "+quantity_label(v) for v in variables)])
        self.result.set_markdown("\n\n".join(lines) or "本页用于记录观察。")

    def add_row(self,key):
        self.active_table=key
        grid,model=self.table_widgets[key]
        from .native_reading_groups import ReadingGroups
        if isinstance(grid,ReadingGroups):
            grid.add_group();return
        row=model.rowCount();model.beginInsertRows(C.QModelIndex(),row,row)
        model.record["rows"].append({});model.endInsertRows();self.changed();grid.scrollToBottom()

    def channel_direction(self,table_key):
        self.active_table=table_key
        _,model=self.table_widgets[table_key]
        choices=[label(c) for c in model.table["columns"]]
        name,ok=W.QInputDialog.getItem(self,"计算使用的通道方向","选一个需要核对参考方向的量",choices,0,False)
        if not ok:return
        key=model.table["columns"][choices.index(name)]["id"]
        directions=model.record.setdefault("directions",{})
        options=["与记录方向一致","与记录方向相反（计算时乘以 −1）"]
        value,ok=W.QInputDialog.getItem(self,name,"依据接线或仪器约定选择；原始单元格保留。",options,int(directions.get(key)==-1),False)
        if ok:
            directions[key]=1 if value==options[0] else -1
            self.changed();self.flush();self.notice.setText(name+" · "+value)

    def import_file(self,table_key=None):
        if table_key is None and len(self.table_widgets)>1:
            tables=self.page["tables"];titles=[t["title"] for t in tables]
            i=next((i for i,t in enumerate(tables) if t["id"]==self.active_table),0)
            # Include table identity when titles are repeated.
            options=[f"{title} ({t['id']})" if titles.count(title)>1 else title for title,t in zip(titles,tables)]
            selected,ok=W.QInputDialog.getItem(self,"导入测量数据","录入哪张表",options,i,False)
            if not ok:return
            table_key=tables[options.index(selected)]["id"]
        self.active_table=table_key or self.active_table
        path,_=W.QFileDialog.getOpenFileName(self,"导入测量数据","","测量数据 (*.csv *.tsv *.xlsx *.dat)")
        if not path:return
        from .storage import digest
        try:
            raw=Path(path).read_bytes();sheets=read_tables(raw,Path(path).suffix.lower()[1:])
            if not sheets:raise ValueError("文件中没有可选的数据表")
            _,model=self.table_widgets[self.active_table]
            dialog=ImportColumns(self,model.table,sheets)
            if not dialog.exec():return
            sheet=sheets[dialog.sheets.currentIndex()]
            self.notice.setText(f"正在导入 {len(sheet['rows'])} 行并更新计算…")
            self.notice.repaint()
            rows=map_rows(sheet,dialog.mapping)
            replace=dialog.mode.currentIndex()==1
            original=digest(raw)
            target=self.app.store.originals/("worksheet-"+original+Path(path).suffix.lower())
            if not target.exists():target.write_bytes(raw)
            model.beginResetModel();before=model.record["rows"]
            if replace and any(r for r in before):
                model.record.setdefault("previous_imports",[]).append({"rows":before,"imports":model.record.get("imports",[])})
                model.record["imports"]=[]
            prefix=[] if replace or not any(r for r in before) else before
            model.record["rows"]=prefix+rows
            model.record.setdefault("imports",[]).append({"file":Path(path).name,"sha256":original,"table":sheet["name"],"mapping":dialog.mapping,"start":len(prefix),"count":len(rows)})
            model.endResetModel();self.changed();self.flush()
            self.notice.setText(f"已导入 {len(rows)} 行 · {sheet['name']}")
        except Exception as err:W.QMessageBox.information(self,"导入数据",str(err))

    def chat(self):
        messages=self.app.store.all("SELECT role,content FROM messages WHERE experiment_id=? ORDER BY rowid",(self.obj["experiment_id"],))
        self.conversation.set_markdown("\n\n".join(("**你**　" if m["role"]=="user" else "**助手**　")+m["content"] for m in messages))
        self.conversation.verticalScrollBar().setValue(self.conversation.verticalScrollBar().maximum())

    def send(self, *, activity="worksheet"):
        text=self.ask.text().strip()
        if not text or not self.flush():return
        table=self.active_table
        try:self.job=self.app.agent.queue(self.obj["experiment_id"],text,activity+":"+self.page["id"]+":"+table)["id"]
        except ValueError as err:self.notice.setText(str(err));return
        self.ask.clear();self.ask_btn.setEnabled(False);self.notice.setText("正在处理…")
        self.right_tabs.setCurrentWidget(self.conversation);self.chat()

    def retry_page(self):
        if self.job:return
        self.ask.setText("请补全当前数据页，沿用已保存的测量目录和原表读数。")
        self.send(activity="worksheet_repair")

    def poll(self):
        current=self.app.store.one("SELECT revision FROM objects WHERE id=?",(self.identity,))["revision"]
        row=self.app.store.one("SELECT version FROM worksheet_readings WHERE object_id=?",(self.identity,))
        if current!=self.obj["revision"] or (row and row["version"]!=self.version):
            if not self.dirty:
                table_key=self.active_table
                scroll=self.left.verticalScrollBar().value()
                plots={k:c.choice.currentText() for k,c in self.charts.items()}
                self.obj=self.app.store.object(self.identity);self.version,self.data=self.service.state(self.identity)
                self.build()
                if table_key in self.table_widgets:self.active_table=table_key
                for k,title in plots.items():
                    if k in self.charts:self.charts[k].choice.setCurrentText(title)
                self.left.verticalScrollBar().setValue(scroll)
        if self.job:
            job=self.app.store.one("SELECT status,error FROM jobs WHERE id=?",(self.job,))
            if job and job["status"] not in ("queued","running"):
                self.job=None;self.ask_btn.setEnabled(True);self.chat();self.notice.setText(job["error"] or "处理完成")

    def publish(self):
        if not self.flush():return
        folder=self.app.publications.experiment_folder(self.obj["experiment_id"])/"数据处理台"
        folder.mkdir(parents=True,exist_ok=True)
        from .publications import filename
        name=folder/(filename(self.obj["title"])+".zip")
        path,_=W.QFileDialog.getSaveFileName(self,"导出可复算的测量与结果",str(name),"ZIP 数据包 (*.zip)")
        if path:
            try:
                target=export(self.app,self.obj,path);self.notice.setText("已导出 "+target.name)
                box=W.QMessageBox(self);box.setWindowTitle("导出完成");box.setText(str(target));b=box.addButton("打开所在文件夹",W.QMessageBox.ActionRole);box.addButton(W.QMessageBox.Close);box.exec()
                if box.clickedButton()==b:G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(target.parent)))
            except Exception as err:W.QMessageBox.warning(self,"导出失败",str(err))

    def done(self,result):
        if self.flush():super().done(result)

    def closeEvent(self,event):
        if self.flush():super().closeEvent(event)
        else:event.ignore()
