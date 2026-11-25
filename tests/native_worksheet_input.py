"""Offline native input regression with a self-authored carriage experiment; no live app or API."""
import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

os.environ["QT_QPA_PLATFORM"]="offscreen"
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PySide6 import QtCore as C,QtGui as G,QtWidgets as W,QtTest as T
from app.native import STYLE
from app.native_worksheet import ImportColumns,WorksheetWindow,map_rows,numeric_text
from app.worksheet_math import columns_for,compute,effective_fields,number,resolved_window,selected_rows
from app.worksheet_units import read_quantity


def quantity(key,unit,label=None,**extra):
    return dict(id=key,label=label or key,unit=unit,**extra)


def fixture():
    page=dict(id="cart",title="小车位移记录",sources=[],unit_system="SI",check_dimensions=True,
              fields=[quantity("start_s","s","拟合起点"),quantity("end_s","s","拟合终点"),quantity("voltage","V")],
              observations=[dict(id="note",label="观察",meaning="记录实际观察")],
              tables=[dict(id="motion",title="同步记录",rows=0,
                           columns=[quantity("time_s","s"),quantity("position_mm","mm")],derived=[],
                           selection=dict(column="time_s",min="start_s",max="end_s"))],
              results=[quantity("speed","m/s",expr="slope(time_s,position_mm)")],
              plots=[dict(title="位移随时间",table="motion",x="time_s",ys=["position_mm"])])
    state={"cart":{"fields":{},"tables":{"motion":{"rows":[{"time_s":str(i),"position_mm":str(2*i+5)} for i in range(11)]}}}}
    obj=dict(id="fixture",title="小车测量",revision=1,experiment_id="local",content=dict(pages=[page]))
    return obj,state


class MemoryReadings:
    def __init__(self,state):self.data=copy.deepcopy(state);self.version=1;self.saves=[]
    def state(self,_):return self.version,copy.deepcopy(self.data)
    def save(self,_,version,data):
        if version!=self.version:raise ValueError("读数版本冲突")
        self.version+=1;self.data=copy.deepcopy(data);self.saves.append(copy.deepcopy(data));return self.version


class Host(W.QWidget):
    def __init__(self,obj,state):
        super().__init__();self.readings=MemoryReadings(state)
        store=SimpleNamespace(object=lambda _:copy.deepcopy(obj),all=lambda *_:[],
                              one=lambda sql,*_:dict(version=self.readings.version,revision=obj["revision"]),
                              anchor=lambda _:dict(name="自编材料",page=1,source_id="local"))
        self.app=SimpleNamespace(store=store,worksheets=self.readings)
    def follow_source(self,*_):pass
    def open_source(self,*_):pass


class NativeInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt=W.QApplication.instance() or W.QApplication([])
        font=Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts/msyh.ttc"
        if font.is_file():G.QFontDatabase.addApplicationFont(str(font))
        cls.qt.setStyle("Fusion");cls.qt.setStyleSheet(STYLE)

    def setUp(self):self.opened=[]

    def tearDown(self):
        for widget in reversed(self.opened):widget.close();widget.deleteLater()
        self.qt.processEvents()

    def window(self,state=None,obj=None):
        default_obj,default=fixture();host=Host(obj or default_obj,state if state is not None else default)
        obj=obj or default_obj
        self.opened.append(host);win=WorksheetWindow(host,obj["id"]);self.opened.append(win)
        win.timer.stop();win.show();self.qt.processEvents();return win,host.readings

    def enter(self,line,text):
        T.QTest.mouseClick(line,C.Qt.LeftButton);line.selectAll();T.QTest.keyClicks(line,text)
        T.QTest.keyClick(line,C.Qt.Key_Tab)

    def selected(self,win):
        state=win.data["cart"];table=win.page["tables"][0]
        return list(selected_rows(table,state["tables"][table["id"]],effective_fields(win.page,state),win.page))

    def test_plain_keyboard_and_tab_autosave_bounds(self):
        win,readings=self.window()
        self.enter(win.field_inputs["start_s"],"3");self.enter(win.field_inputs["end_s"],"6")
        self.enter(win.field_inputs["voltage"],"4.19")
        T.QTest.qWait(400)
        self.assertEqual(readings.data["cart"]["fields"],dict(start_s="3",end_s="6",voltage="4.19"))
        self.assertEqual(len(self.selected(win)),4)
        self.assertAlmostEqual(compute(win.page,win.data["cart"])[0]["value"],.002)

    def test_curve_view_shows_only_results_linked_to_this_signal(self):
        obj,state=fixture()
        obj["content"]["pages"][0]["plots"][0]["fit"]="linear"
        obj["content"]["pages"][0]["results"].append(quantity("fixed","V","独立电压",expr="voltage"))
        win,_=self.window(state,obj)
        chart=win.charts["motion"]
        self.assertTrue(win.left.isAncestorOf(chart))
        self.assertFalse(win.right_tabs.isAncestorOf(chart))
        self.assertIn("拟合起点",chart.info.text())
        self.enter(win.field_inputs["start_s"],"2")
        self.enter(win.field_inputs["end_s"],"6")
        T.QTest.qWait(400)
        self.assertIn("0.002",win.live_values["speed"].text())
        self.assertIn("=0.002",win.equation_text["speed"])
        self.assertIn("5 /",chart.info.text())
        self.assertIn("斜率 2",chart.info.text())
        self.assertIn("R² 1",chart.info.text())
        self.assertNotIn("独立电压",chart.info.text())

    def test_authored_steps_update_with_only_their_own_readings(self):
        obj,state=fixture()
        p=obj["content"]["pages"][0]
        p.update(fields=[quantity("a","mm","起始刻度",reading_hint="读左侧游标"),
                         quantity("b","mm","终止刻度"),quantity("scale","1","倍率")],
                 tables=[],plots=[],results=[quantity("delta","mm","位移",expr="b-a"),
                                           quantity("corrected","mm","修正位移",expr="delta*scale")],
                 entry_steps=[dict(title="量两次刻度",fields=["a","b"],results=["delta"]),
                              dict(title="倍率修正",fields=["scale"],results=["corrected"])])
        win,_=self.window({"cart":{"fields":{}}},obj)
        a,b=win.field_inputs["a"],win.field_inputs["b"]
        delta,corrected=win.live_values["delta"],win.live_values["corrected"]
        row=a.parentWidget().parentWidget()
        self.assertIs(row,b.parentWidget().parentWidget())
        self.assertIs(row,delta.parentWidget().parentWidget())
        self.assertIsNot(row,corrected.parentWidget().parentWidget())
        self.assertEqual(a.placeholderText(),"读左侧游标")
        self.assertTrue(delta.isReadOnly())
        self.enter(a,"5.0");self.enter(b,"8.0");T.QTest.qWait(400)
        self.assertEqual(float(delta.text()),3);self.assertEqual(corrected.text(),"")
        row.reflow(250);self.assertEqual(row.columns,1)
        row.reflow(600);self.assertEqual(row.columns,3)
        self.enter(win.field_inputs["scale"],"2");T.QTest.qWait(400)
        self.assertEqual(float(corrected.text()),6)
        self.assertEqual(a.text(),"5.0")

    def test_fallback_difference_sign_and_decimal_place(self):
        from app.worksheet_steps import difference_checks,difference_display,measurement_steps
        page=dict(fields=[quantity("initial","°C","始温"),quantity("final","°C","末温")],
                  tables=[],results=[quantity("heat","J","热量",expr="2*(final-initial)")])
        steps,checks=measurement_steps(page)
        check=checks[0]
        self.assertEqual(check["unit"],"K")
        self.assertIn(check["id"],steps[0]["results"])
        fields={f["id"]:f for f in page["fields"]}
        for end,expected in (("10.3","-6.2"),("10.30","-6.20")):
            row={"value":-6.2000000000000455}
            difference_display(check,row,fields,{"initial":"16.50","final":end})
            self.assertEqual(row["display"],expected)
        page["results"].append(quantity("difference","K","温差",expr="final-initial"))
        self.assertEqual(difference_checks(page),[])

    def test_partial_readings_display_at_input_and_in_complete_equation(self):
        obj,state=fixture()
        page=obj["content"]["pages"][0]
        page["fields"].append(quantity("volume","m^3","液体体积",hint="由mL换算为m³",symbol="V"))
        page["results"].append(quantity("capacity","mL","体积读数",expr="volume",symbol="V_c"))
        page["tables"][0]["derived"]=[quantity("doubled","mm","双倍位移",expr="2*position_mm",symbol="d")]
        win,repo=self.window(state,obj)
        model=win.grids[0][1]
        self.assertEqual(model.data(model.index(2,3)),"18")
        self.assertFalse(model.flags(model.index(2,3)) & C.Qt.ItemIsEditable)
        self.assertEqual(win.live_values["capacity"].text(),"")
        self.assertIn("待填",win.live_values["capacity"].toolTip())
        self.enter(win.field_inputs["volume"],"35")
        T.QTest.qWait(440)
        self.assertEqual(repo.data["cart"]["fields"]["volume"],"35 mL")
        self.assertIn("35",win.live_values["capacity"].text())
        self.assertIn("=35",win.equation_text["capacity"])
        self.assertNotIn("capacity",win.equation_text["capacity"])
        self.assertEqual(win.input_units["volume"],"mL")
        self.assertIn("拟合起点",win.live_values["speed"].toolTip())

    def test_native_paste_with_nul_then_tab_is_saved_and_computed(self):
        win,readings=self.window();line=win.field_inputs["start_s"]
        T.QTest.mouseClick(line,C.Qt.LeftButton);T.QTest.keyClicks(line,"3")
        self.qt.clipboard().setText("\x00");T.QTest.keyClick(line,C.Qt.Key_V,C.Qt.ControlModifier)
        T.QTest.keyClick(line,C.Qt.Key_Tab)
        self.enter(win.field_inputs["end_s"],"6");T.QTest.qWait(400)
        self.assertEqual(line.text(),"3");self.assertEqual(readings.data["cart"]["fields"]["start_s"],"3")
        self.assertEqual(len(self.selected(win)),4)
        self.assertNotIn("请先确定取数范围",win.result.toPlainText())

    def test_input_method_controls_are_removed_without_moving_cursor_to_end(self):
        win,_=self.window();line=win.field_inputs["start_s"]
        self.enter(line,"30");line.setFocus();line.setCursorPosition(1)
        event=G.QInputMethodEvent();event.setCommitString("\x00\u200b")
        C.QCoreApplication.sendEvent(line,event)
        self.assertEqual(line.text(),"30");self.assertEqual(line.cursorPosition(),1)
        self.assertEqual(win.data["cart"]["fields"]["start_s"],"30")

    def test_existing_saved_controls_are_read_without_rewriting_raw_state(self):
        _,state=fixture();state["cart"]["fields"]={"start_s":"3\x00","end_s":"\u200e6"}
        state["cart"]["observations"]={"note":"原记录\x00"}
        original=copy.deepcopy(state);win,readings=self.window(state);T.QTest.qWait(400)
        self.assertEqual(win.field_inputs["start_s"].text(),"3")
        self.assertEqual(len(self.selected(win)),4)
        self.assertEqual(readings.data["cart"]["fields"],original["cart"]["fields"])
        self.assertEqual(readings.data["cart"]["tables"],original["cart"]["tables"])
        self.assertEqual(readings.data["cart"]["observations"],original["cart"]["observations"])
        self.assertEqual(state,original);self.assertEqual(readings.version,1)
        host=Host(win.obj,readings.data);self.opened.append(host)
        reopened=WorksheetWindow(host,"fixture");self.opened.append(reopened);reopened.timer.stop()
        self.assertFalse(reopened.dirty);self.assertEqual(len(self.selected(reopened)),4)

    def test_restore_prior_bounds_reads_old_controls_and_preserves_raw(self):
        _,state=fixture();state["cart"]["fields"]={"start_s":"4","end_s":"5"}
        state["cart"]["tables"]["motion"].update(window=dict(column="time_s",min=4,max=5),
                                                 prior_bounds=dict(start_s="3\x00",end_s="6"))
        win,readings=self.window(state);self.assertEqual(len(self.selected(win)),2)
        win.charts["motion"].source_interval();self.assertEqual(len(self.selected(win)),4)
        self.assertEqual(readings.data["cart"]["fields"],dict(start_s="3\x00",end_s="6"))
        self.assertEqual(win.field_inputs["start_s"].text(),"3")
        self.assertNotIn("prior_bounds",readings.data["cart"]["tables"]["motion"])

    def test_chart_bound_input_controls_and_invalid_token_separation(self):
        win,readings=self.window();chart=win.charts["motion"];self.qt.processEvents()
        chart.low.setText("3\x00");chart.high.setText("\u200e6");chart.interval()
        self.assertEqual(readings.data["cart"]["fields"],dict(start_s="3",end_s="6"))
        self.assertEqual(len(self.selected(win)),4)
        prior=copy.deepcopy(readings.data);version=readings.version
        chart.low.setText("3\t6");chart.high.setText("9");chart.interval()
        self.assertIn("递增的有限数值",win.notice.text())
        self.assertEqual(readings.version,version);self.assertEqual(readings.data,prior)

    def test_scope_readout_displays_recorded_digits_and_retains_source_text(self):
        obj,state=fixture()
        state["cart"]["tables"]["motion"]["rows"][0]["time_s"]="1.000000e-01"
        win,_=self.window(state,obj)
        model=win.grids[0][1];index=model.index(0,1)
        self.assertEqual(model.data(index),"0.1000000")
        self.assertEqual(model.data(index,C.Qt.EditRole),"1.000000e-01")
        self.assertIn("1.000000e-01",model.data(index,C.Qt.ToolTipRole))
        self.assertTrue(model.headerData(1,C.Qt.Horizontal).endswith("\n(s)"))

    def test_numeric_cleanup_preserves_valid_quantity_syntax_and_separators(self):
        for raw in ("−3.6e-2 V","360 ms","3\t60","3\n60","3\r60","3\v60"):
            self.assertEqual(numeric_text(raw),raw)
        self.assertAlmostEqual(read_quantity(numeric_text("\u200e−3.6e-2 V\x00"),"V"),-.036)
        self.assertAlmostEqual(read_quantity("360\x00 ms","s"),.36)
        self.assertEqual(number("360\x00"),360)
        for raw in ("3\t60","3\n60","3\r60","3\v60"):
            self.assertTrue(math.isnan(number(raw)));self.assertTrue(math.isnan(read_quantity(raw,"s")))

    def test_legacy_text_fields_keep_format_characters_on_read_and_edit(self):
        obj,state=fixture();page=obj["content"]["pages"][0]
        page["fields"].extend([quantity("word","文字"),quantity("typed_text","1",type="text")])
        state["cart"]["fields"].update(word="A\u200dB",typed_text="A\u200dB")
        win,readings=self.window(state,obj)
        for key in ("word","typed_text"):
            line=win.field_inputs[key];self.assertEqual(line.text(),"A\u200dB")
            line.setFocus();line.setCursorPosition(len(line.text()))
            event=G.QInputMethodEvent();event.setCommitString("\u200dC");C.QCoreApplication.sendEvent(line,event)
            self.assertEqual(line.text(),"A\u200dB\u200dC")
        win.flush()
        for key in ("word","typed_text"):self.assertEqual(readings.data["cart"]["fields"][key],"A\u200dB\u200dC")

    def test_table_controls_and_standalone_analysis_preserve_source_strings(self):
        obj,state=fixture();page=obj["content"]["pages"][0]
        state["cart"]["fields"].update(start_s="3\x00",end_s="\u200e6")
        table=page["tables"][0];table["columns"].append(quantity("tag","文字",type="text"))
        rows=state["cart"]["tables"]["motion"]["rows"]
        for row in rows:
            row["time_s"]+="\x00";row["position_mm"]+="\u200b";row["tag"]="A\u200dB"
        original=copy.deepcopy(state);result=compute(page,state["cart"])
        self.assertAlmostEqual(result[0]["value"],.002)
        self.assertEqual(columns_for(table,rows,{})["tag"].tolist(),["A\u200dB"]*len(rows))
        self.assertEqual(state,original)
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)
            shutil.copyfile(ROOT/"app/worksheet_math.py",target/"analysis.py")
            shutil.copyfile(ROOT/"app/worksheet_units.py",target/"worksheet_units.py")
            (target/"definition.json").write_text(json.dumps(obj["content"]),encoding="utf8")
            raw=json.dumps(state).encode("utf8");(target/"measurements.json").write_bytes(raw)
            subprocess.run([sys.executable,"-B",str(target/"analysis.py")],check=True,capture_output=True)
            self.assertEqual(json.loads((target/"results.json").read_text(encoding="utf8"))["cart"],result)
            self.assertEqual((target/"measurements.json").read_bytes(),raw)

    def test_import_temperature_unit_is_explicit_and_fits_at_default_width(self):
        columns=[quantity("time","s","加热起点后时间"),quantity("center","°C","中心面温度"),quantity("heater","°C","加热面温度")]
        headers=["加热起点后时间_s","中心面温度_摄氏度","加热面温度_摄氏度"]
        parsed=[dict(name="全文数据",columns=headers,rows=[dict(zip(headers,[360,26.76,31.31]))])]
        dialog=ImportColumns(None,dict(columns=columns),parsed);self.opened.append(dialog)
        for column,header in zip(columns,headers):
            box=dialog.choices[column["id"]]
            box.setCurrentIndex(next(i for i in range(box.count()) if tuple(box.itemData(i) or ())==(header,"raw")))
        dialog.show();self.qt.processEvents()
        for key in ("center","heater"):
            field=dialog.units[key];self.assertEqual(field.currentText(),"°C")
            self.assertGreaterEqual(field.lineEdit().contentsRect().width(),field.fontMetrics().horizontalAdvance("摄氏度"))
        dialog.accept();mapped=map_rows(parsed[0],dialog.mapping)
        self.assertEqual(dialog.mapping["center"],(headers[1],"raw","°C","°C"))
        self.assertEqual(float(mapped[0]["center"]),26.76)

    def test_named_scope_channels_and_units_are_matched_without_guessing_between_channels(self):
        from app.native_worksheet import suggested_columns
        cols=[quantity("time","s","采样时刻"),quantity("ch3_voltage","V","第三通道"),quantity("ch4_voltage","V","第四通道")]
        headers=["Time(s)","CH4V","CH3mV"]
        sheet=dict(name="示波器",columns=headers,rows=[dict(zip(headers,["0.5","1.1","250"]))])
        dialog=ImportColumns(None,dict(columns=cols),[sheet]);self.opened.append(dialog)
        dialog.accept()
        self.assertEqual(float(map_rows(sheet,dialog.mapping)[0]["ch3_voltage"]),0.25)
        self.assertEqual(dialog.mapping["time"][0],"Time(s)")
        self.assertNotIn("ch3_voltage",suggested_columns(cols,headers+["CH3V"]))
        self.assertNotIn("ch3_voltage",suggested_columns(cols,["CH3A","CH3 RMS (V)"]))
        self.assertNotIn("ch3_voltage",suggested_columns(cols+[quantity("other","V","CH3参考")],headers))

    def test_import_preserves_other_valid_source_units_and_conversion(self):
        table=dict(columns=[quantity("temperature","°C"),quantity("position","m")])
        parsed=[dict(name="传感器校准",columns=["参考温度(K)","位置(mm)"],rows=[{"参考温度(K)":293.15,"位置(mm)":25}])]
        dialog=ImportColumns(None,table,parsed);self.opened.append(dialog)
        for key,header in (("temperature","参考温度(K)"),("position","位置(mm)")):
            box=dialog.choices[key]
            box.setCurrentIndex(next(i for i in range(box.count()) if tuple(box.itemData(i) or ())==(header,"raw")))
        self.assertEqual(dialog.units["temperature"].currentText(),"K")
        self.assertEqual(dialog.units["position"].currentText(),"mm")
        dialog.accept();row=map_rows(parsed[0],dialog.mapping)[0]
        self.assertAlmostEqual(float(row["temperature"]),20);self.assertAlmostEqual(float(row["position"]),.025)


if __name__=="__main__":unittest.main()
