"""Responsive entry rows and the plot attached to one native measurement table."""
import math

from PySide6 import QtCore as C, QtWidgets as W
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure

from .worksheet_math import effective_fields, resolved_window
from .worksheet_output import draw
from .worksheet_presentation import field_text, quantity_label, scientific_text
from .worksheet_units import convert, numeric_text
from .worksheets import table_state


class StepCells(W.QWidget):
    """Keep related readings in one logical row; wrap cells when the window narrows."""
    def __init__(self,cells):
        super().__init__()
        self.cells=cells
        self.grid=W.QGridLayout(self)
        self.grid.setContentsMargins(0,0,0,0)
        self.grid.setHorizontalSpacing(10);self.grid.setVerticalSpacing(6)
        self.columns=0
        self.reflow(610)

    def reflow(self,width):
        count=min(len(self.cells),max(1,(width+10)//145))
        if count==self.columns:return
        for i in range(self.columns):self.grid.setColumnStretch(i,0)
        for i,cell in enumerate(self.cells):
            self.grid.removeWidget(cell)
            self.grid.addWidget(cell,i//count,i%count)
        for i in range(count):self.grid.setColumnStretch(i,1)
        self.columns=count
        self.updateGeometry()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.reflow(event.size().width())


class PlotChoice(W.QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class TableChart(W.QGroupBox):
    def __init__(self,host,table,plots):
        super().__init__("曲线与拟合")
        self.host=host;self.table=table;self.plots=plots
        self.bounds_state=None
        body=W.QVBoxLayout(self)
        self.choice=PlotChoice();self.choice.addItems([scientific_text(p["title"]) for p in plots])
        body.addWidget(self.choice)
        self.figure=Figure(figsize=(5,2.65),layout="constrained")
        self.canvas=FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(200)
        self.canvas.setMaximumHeight(310)
        self.ax=self.figure.add_subplot()
        body.addWidget(self.canvas)
        body.addWidget(NavigationToolbar2QT(self.canvas,self))
        self.info=W.QLabel();self.info.setWordWrap(True);body.addWidget(self.info)
        bounds=W.QHBoxLayout();body.addLayout(bounds)
        self.axis=PlotChoice();self.axis.setSizeAdjustPolicy(W.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.axis.setMinimumContentsLength(5)
        for col in table["columns"]+table.get("derived",[]):self.axis.addItem(quantity_label(col),col["id"])
        bounds.addWidget(self.axis,1)
        self.low=W.QLineEdit();self.low.setPlaceholderText("起点");bounds.addWidget(self.low,1)
        self.high=W.QLineEdit();self.high.setPlaceholderText("终点");bounds.addWidget(self.high,1)
        actions=W.QHBoxLayout();body.addLayout(actions)
        host.btn(actions,"应用选区",self.interval)
        host.btn(actions,"全区间",lambda:self.interval(True))
        host.btn(actions,"按讲义",self.source_interval)
        actions.addStretch()
        self.choice.currentIndexChanged.connect(self.redraw)

    def redraw(self,*_):
        if self.host.loading:return
        plot=self.plots[self.choice.currentIndex()]
        page=self.host.page
        state=self.host.data.get(page["id"],{})
        self.info.setTextFormat(C.Qt.RichText)
        self.info.setText(scientific_text(draw(self.ax,page,state,plot), rich=True))
        self.canvas.draw_idle()
        record=state.get("tables",{}).get(self.table["id"],{})
        window=resolved_window(self.table,record,effective_fields(page,state),page) or {}
        values=(window.get("column") or plot["x"],window.get("min"),window.get("max"))
        if values!=self.bounds_state:
            self.bounds_state=values
            self.axis.setCurrentIndex(max(0,self.axis.findData(values[0])))
            self.low.setText("" if values[1] is None else str(values[1]))
            self.high.setText("" if values[2] is None else str(values[2]))

    def interval(self,clear=False):
        host=self.host
        try:
            low=numeric_text(self.low.text()).strip();high=numeric_text(self.high.text()).strip()
            lo=float(low) if low and not clear else None
            hi=float(high) if high and not clear else None
            if any(not math.isfinite(n) for n in (lo,hi) if n is not None) or (lo is not None and hi is not None and hi<lo):
                raise ValueError()
        except ValueError:
            host.notice.setText("选区起止应为递增的有限数值");return
        self.bounds_state=None
        state=host.data[host.page["id"]]
        block=table_state(state,self.table)
        column=self.axis.currentData()
        block["window"]={"column":column,"min":lo,"max":hi} if lo is not None or hi is not None else None
        rule=self.table.get("selection") or {}
        if rule.get("column")==column:
            axis=next(c for c in self.table["columns"]+self.table.get("derived",[]) if c["id"]==column)
            fields=state.setdefault("fields",{})
            prior=block.setdefault("prior_bounds",{})
            for side,bound in (("min",lo),("max",hi)):
                key=rule.get(side)
                if key not in host.field_inputs:continue
                field=next(f for f in host.page["fields"] if f["id"]==key)
                prior.setdefault(key,fields.get(key))
                fields[key]="" if bound is None else format(convert(bound,axis["unit"],field["unit"]),".15g")
                host.field_inputs[key].setText(field_text(fields[key],field,host.input_units[key]))
        host.changed();host.flush()

    def source_interval(self):
        host=self.host
        state=host.data[host.page["id"]];block=table_state(state,self.table)
        block.pop("window",None)
        fields=state.setdefault("fields",{})
        for key,value in block.pop("prior_bounds",{}).items():
            if value is None:fields.pop(key,None)
            else:fields[key]=value
            if key in host.field_inputs:
                field=next(f for f in host.page["fields"] if f["id"]==key)
                raw=effective_fields(host.page,state).get(key,"")
                host.field_inputs[key].setText(field_text(numeric_text(raw),field,host.input_units[key]))
        self.bounds_state=None
        host.changed();host.flush()
