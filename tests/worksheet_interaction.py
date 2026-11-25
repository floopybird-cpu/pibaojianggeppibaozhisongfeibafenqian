"""Offline workbench service-entry regressions; no real model or desktop."""
import copy
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"]="offscreen"

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from app.server import Application
from app.demo import demo_outline
from app.storage import canonical,now,uid
from app.worksheet_math import compute
from app.worksheet_interaction import snapshot
from app.worksheet_evidence import web_evidence


def receipt(value,name="submit_worksheet_action",identity="call-action"):
    return {"role":"assistant","reasoning_content":"fixture retained metadata","content":"",
            "tool_calls":[{"id":identity,"type":"function","function":{"name":name,"arguments":canonical(value)}}]},{}


class InteractionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=root/"qa/work")
        self.addCleanup(self.tmp.cleanup)
        self.app=Application(root,Path(self.tmp.name)/"library",products_root=Path(self.tmp.name)/"products")
        self.exp=self.app.store.create_experiment("摆动测时与光学比值","检查已有输入和取数范围",{},[])["id"]
        task=self.app.materials.queue("操作摘录.md","记录摆动时刻与位移，拟合选定区间。光学比值用两次读数相除。".encode())["id"]
        for _ in range(100):
            result=self.app.store.one("SELECT status,result,error FROM jobs WHERE id=?",(task,))
            if result["status"] in ("succeeded","failed"):break
            time.sleep(.02)
        self.assertEqual(result["status"],"succeeded",result["error"])
        source=json.loads(result["result"])["source_id"]
        self.app.store.execute("UPDATE experiments SET source_ids=? WHERE id=?",(canonical([source]),self.exp))
        self.anchor=self.app.materials.make_anchor(source,1)["id"]
        self.page={"id":"swing","title":"摆动取段","unit_system":"SI","check_dimensions":True,"sources":[self.anchor],
            "fields":[{"id":"start","label":"拟合起点","unit":"s"},{"id":"end","label":"拟合终点","unit":"s"},
                      {"id":"ratio","label":"光学比值","unit":"1"},{"id":"length","label":"光屏距离","unit":"cm"}],
            "tables":[{"id":"trace","title":"摆动记录","rows":3,"columns":[{"id":"time","label":"时刻","unit":"s"},{"id":"position","label":"位移","unit":"mm"}],
                       "selection":{"column":"time","min":"start","max":"end"},"derived":[]}],
            "results":[{"id":"position_mean","label":"选区位移均值","unit":"mm","expr":"mean(position)"},
                       {"id":"shift","label":"光学换算位移","unit":"cm","expr":"ratio*length"}],"plots":[]}
        job=self.job("创建离线测试工作台")
        content=demo_outline()
        for stage in content["stages"]:
            for point in stage["points"]:point["sources"]=[self.anchor]
        content["measurement_methods"]=[{"id":"swing","title":"摆动取段","purpose":"检查输入与取段","material_pages":["D1p1"],"source_index":{"D1p1":self.anchor},
            "targets":[{"id":"shift","label":"光学换算位移","unit":"cm","kind":"computed","meaning":"比值换算","source":"D1p1"}],
            "observables":[{**f,"form":"reading","meaning":f["label"],"source":"D1p1"} for f in self.page["fields"]],"equations":[]}]
        self.app.agent.experiment_drafts.save(self.exp,job,content)
        binding,pages=self.app.worksheets.commit_measurement_plan(job,self.exp,[self.page])
        self.obj=self.app.gateway.apply("ExperimentWorksheet",{"title":"离线测量台","draft_ref":binding,"sources":[self.anchor],"pages":pages},experiment_id=self.exp)
        self.page=self.obj["content"]["pages"][0]
        self.initial={"swing":{"fields":{"start":"4","end":"10","ratio":"0.5","length":"20"},"tables":{"trace":{"rows":[
            {"time":"2","position":"3"},{"time":"4","position":"5"},{"time":"6","position":"7"},{"time":"8","position":"9"},{"time":"10","position":"11"}],
            "window":{"column":"time","min":4,"max":10},"prior_bounds":{"start":"2","end":"10"}}}}}
        self.app.worksheets.save(self.obj["id"],0,self.initial)
        self.model=Mock();self.model.scope.side_effect=lambda *args:nullcontext()
        self.app.agent.client=self.model

    def job(self,prompt,options=None):
        message=uid("m");job=uid("j")
        self.app.store.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)",(message,self.exp,"user",prompt,"model",now()))
        payload=canonical(dict(experiment_id=self.exp,initial_user_message_id=message,user_message_id=message,request_options=options or {}))
        self.app.store.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,0,?,?)",(job,"agent","running",0,payload,None,None,now(),now()))
        return job

    def run_action(self,prompt,responses,options=None):
        self.model.complete.side_effect=responses
        job=self.job(prompt,options)
        self.app.worksheets.run(job,self.exp,prompt,"worksheet:swing:trace")
        result=self.app.store.one("SELECT * FROM jobs WHERE id=?",(job,))
        last=self.app.store.one("SELECT content FROM messages WHERE experiment_id=? AND role='assistant' ORDER BY rowid DESC LIMIT 1",(self.exp,))["content"]
        return job,result,last

    def test_existing_bounds_write_readings_recalculate_and_preserve_history(self):
        before=self.app.worksheets.state(self.obj["id"])
        _,result,reply=self.run_action("将摆动拟合起点移到2秒，终点不变",[receipt({"action":"fields","page":"swing","fields":[{"id":"start","value":"2000","unit":"ms"}]})])
        self.assertEqual(result["status"],"succeeded",result["error"])
        version,data=self.app.worksheets.state(self.obj["id"])
        self.assertEqual(version,before[0]+1)
        self.assertEqual(data["swing"]["fields"]["start"],"2")
        self.assertNotIn("window",data["swing"]["tables"]["trace"])
        self.assertEqual(data["swing"]["tables"]["trace"]["rows"],self.initial["swing"]["tables"]["trace"]["rows"])
        self.assertEqual(data["swing"]["edit_history"][0]["before"],before[1]["swing"])
        self.assertAlmostEqual(compute(self.page,data["swing"])[0]["value"],7)
        self.assertEqual(self.app.store.object(self.obj["id"])["revision"],self.obj["revision"])
        self.assertEqual(self.model.complete.call_count,1)
        self.assertIn("起点 = 2 s",reply)
        self.assertTrue(any("起点移到2秒" in v for v in self.app.agent.experiment_drafts.current(self.exp)["content"]["delivery_requests"]))

    def test_optical_parameters_and_units_are_real_values(self):
        version,data=self.app.worksheets.state(self.obj["id"])
        data["swing"]["fields"]["end"]="";data["swing"]["tables"]["trace"].pop("window")
        self.app.worksheets.save(self.obj["id"],version,data)
        _,result,reply=self.run_action("比值取四分之一，屏距改为0.4米",[receipt({"action":"fields","page":"swing","fields":[
            {"id":"ratio","value":"25","unit":"%"},{"id":"length","value":"0.4","unit":"m"}]})])
        self.assertEqual(result["status"],"succeeded",result["error"])
        data=self.app.worksheets.state(self.obj["id"])[1]["swing"]
        self.assertEqual(data["fields"]["ratio"],"0.25")
        self.assertEqual(data["fields"]["length"],"40")
        self.assertEqual(compute(self.page,data)[1]["value"],10)
        self.assertNotIn("拟合终点",reply)
        self.assertNotIn("待核对",reply)

    def test_window_rows_and_cells_preserve_original_rows(self):
        _,job,_=self.run_action("起点改为6秒，另一端不动",[receipt({"action":"window","page":"swing","table":"trace","window":{"mode":"range","min":{"value":"6","unit":"s"}}})])
        self.assertEqual(job["status"],"succeeded",job["error"])
        self.assertEqual(self.app.worksheets.state(self.obj["id"])[1]["swing"]["tables"]["trace"]["window"],{"column":"time","min":6,"max":10})
        _,job,_=self.run_action("取消起点限制",[receipt({"action":"window","page":"swing","table":"trace","window":{"mode":"range","min":{"value":""}}})])
        self.assertEqual(job["status"],"succeeded",job["error"])
        self.assertEqual(self.app.worksheets.state(self.obj["id"])[1]["swing"]["tables"]["trace"]["window"],{"column":"time","min":None,"max":10})
        _,job,_=self.run_action("只取第4到第8秒",[receipt({"action":"window","page":"swing","table":"trace","window":{"mode":"range","column":"time","min":{"value":"4","unit":"s"},"max":{"value":"8","unit":"s"}}})])
        self.assertEqual(job["status"],"succeeded",job["error"])
        data=self.app.worksheets.state(self.obj["id"])[1]["swing"]
        self.assertEqual(data["fields"]["end"],"8")
        self.assertEqual(compute(self.page,data)[0]["value"],7)
        _,job,_=self.run_action("有效记录减少两行",[receipt({"action":"rows","page":"swing","table":"trace","delta":-2})])
        self.assertEqual(job["status"],"succeeded",job["error"])
        rows=self.app.worksheets.state(self.obj["id"])[1]["swing"]["tables"]["trace"]["rows"]
        self.assertEqual(len(rows),5);self.assertEqual(rows[-1]["position"],"11");self.assertTrue(rows[-1]["_excluded"])
        _,job,_=self.run_action("第二行位移应为0.6厘米",[receipt({"action":"cells","page":"swing","table":"trace","cells":[{"row":2,"column":"position","value":"0.6","unit":"cm"}]})])
        self.assertEqual(job["status"],"succeeded",job["error"])
        self.assertEqual(self.app.worksheets.state(self.obj["id"])[1]["swing"]["tables"]["trace"]["rows"][1]["position"],"6")

    def test_cancel_and_concurrent_readings_do_not_overwrite(self):
        original=self.app.worksheets.state(self.obj["id"])
        job=self.job("改变起点")
        def cancel(*args,**kwargs):
            self.app.store.execute("UPDATE jobs SET cancel=1 WHERE id=?",(job,))
            return receipt({"action":"fields","page":"swing","fields":[{"id":"start","value":"2"}]})
        self.model.complete.side_effect=cancel
        self.app.worksheets.run(job,self.exp,"改变起点","worksheet:swing:trace")
        self.assertEqual(self.app.store.one("SELECT status FROM jobs WHERE id=?",(job,))["status"],"cancelled")
        self.assertEqual(self.app.worksheets.state(self.obj["id"]),original)
        def concurrent(*args,**kwargs):
            updated=copy.deepcopy(original[1]);updated["swing"]["fields"]["length"]="50"
            self.app.worksheets.save(self.obj["id"],original[0],updated)
            return receipt({"action":"fields","page":"swing","fields":[{"id":"start","value":"2"}]})
        self.model.complete.side_effect=concurrent
        job=self.job("改变起点")
        self.app.worksheets.run(job,self.exp,"改变起点","worksheet:swing:trace")
        state=self.app.worksheets.state(self.obj["id"])[1]["swing"]
        self.assertEqual(self.app.store.one("SELECT status FROM jobs WHERE id=?",(job,))["status"],"failed")
        self.assertEqual(state["fields"]["start"],"4");self.assertEqual(state["fields"]["length"],"50")

    def test_paraphrased_source_keeps_page_link_at_service_entry(self):
        answer = "两个光学读数相除得到比值。[D1p1]"
        value = {"action": "answer", "answer": answer,
                 "evidence": [{"id": "D1p1", "quote": "光学读数……取两者的比值"}]}
        job, result, reply = self.run_action("应该怎么理解这个比值？", [receipt(value)])
        self.assertEqual(result["status"], "succeeded", result["error"])
        self.assertIn("physlab://page/", reply)
        self.assertIn("两个光学读数相除得到比值", reply)
        self.assertNotIn("没有标出", reply)
        event = self.app.store.one("SELECT blob FROM runtime_events WHERE job_id=? AND event='worksheet_citation_resolution'", (job,))
        resolution = self.app.agent.runtime.read_blob(event["blob"])
        self.assertEqual(resolution["resolved"], ["D1p1"])
        self.assertEqual(resolution["exact_quote"], [])

    def test_source_identity_and_exact_passage_have_distinct_evidence(self):
        from app.worksheet_evidence import answer_sources, quoted_sources, cited_answer
        from app.worksheet_interaction import REFERENCE
        from app.format_program import check_schema
        check_schema({"id": "W1"}, REFERENCE, None, "来源")
        catalog = {
            "W1": {"title": "光学玻璃表", "url": "https://example.org/glass", "web_page": True,
                   "text": "材料 | 波长 | 折射率\n玻璃 A | 589 nm | 1.50"},
            "W2": {"title": "干涉测量", "url": "https://example.org/interference", "web_page": True,
                   "text": "光程差由条纹移动数与波长相乘得到。"},
        }
        refs = [{"id": "W1", "quote": "玻璃 A 的折射率为 1.50（589 nm）"},
                {"id": "W2", "quote": "光程差由条纹移动数与波长相乘得到。"}]
        self.assertEqual(list(quoted_sources(refs, catalog)), ["W2"])
        sources, resolution = answer_sources("材料数据见[W1]，测量原理见[W2]。[W99]", refs, catalog)
        self.assertEqual(sources["W1"]["url"], catalog["W1"]["url"])
        self.assertIn("#:~:text=", sources["W2"]["url"])
        self.assertEqual(resolution["unavailable"], ["W99"])
        text, used = cited_answer("[W1][W2][W99] https://invented.invalid", sources)
        self.assertEqual(used, ["W1", "W2"])
        self.assertNotIn("invented.invalid", text)
        self.assertNotIn("W99", text)

    def test_numeric_answer_calculates_from_given_observations(self):
        answer = {"action":"answer", "answer":"频率为 {{calc_1}} Hz。", "calculations":[{"expr":"1/0.037","digits":3}]}
        _,job,reply = self.run_action("根据周期算频率",[receipt(answer)])
        self.assertEqual(job["status"],"succeeded",job["error"])
        self.assertIn("27.0 Hz",reply)
        self.assertNotIn("calc_1",reply)

    def test_latex_tool_answer_and_real_invalid_receipt_feedback(self):
        answer=r"比值可记为 \(q=x/y\)，需保持同类量单位一致。[D1p1]"
        value={"action":"answer","answer":answer,"evidence":[{"id":"D1p1","quote":"光学比值用两次读数相除"}]}
        bad,_=receipt(value,identity="original-call");bad["tool_calls"][0]["function"]["arguments"]='{"action":"answer","answer":"\\(q=x/y\\)"}'
        _,job,reply=self.run_action("比值的符号是什么意思？",[(bad,{}),receipt(value)])
        self.assertEqual(job["status"],"succeeded",job["error"])
        self.assertIn(r"\(q=x/y\)",reply);self.assertIn("physlab:",reply)
        messages=self.model.complete.call_args_list[1].args[0]
        self.assertEqual(messages[-2],bad)
        self.assertEqual(messages[-1]["tool_call_id"],"original-call")
        self.assertIn("invalid_arguments",messages[-1]["content"])
        from PySide6 import QtCore as C,QtGui as G,QtWidgets as W
        from app.native_markdown import MarkdownView
        type(self).qt=W.QApplication.instance() or W.QApplication([])
        view=MarkdownView(reply,self.app.store)
        formula=view.document().resource(G.QTextDocument.ImageResource,C.QUrl("math-0"))
        self.assertIsInstance(formula,G.QImage);self.assertFalse(formula.isNull())
        self.assertIn("单位一致",view.toPlainText())
        view.deleteLater()

    def test_cancel_after_commit_reports_saved_outcome(self):
        job=self.job("把屏距改为30厘米")
        save=self.app.worksheets.save
        def committed(*args):
            value=save(*args)
            self.app.store.execute("UPDATE jobs SET cancel=1 WHERE id=?",(job,))
            return value
        self.model.complete.return_value=receipt({"action":"fields","page":"swing","fields":[{"id":"length","value":"30","unit":"cm"}]})
        with patch.object(self.app.worksheets,"save",side_effect=committed):
            self.app.worksheets.run(job,self.exp,"把屏距改为30厘米","worksheet:swing:trace")
        status=self.app.store.one("SELECT status,result FROM jobs WHERE id=?",(job,))
        self.assertEqual(status["status"],"succeeded")
        self.assertNotIn("_committed",json.loads(status["result"]))
        self.assertEqual(self.app.worksheets.state(self.obj["id"])[1]["swing"]["fields"]["length"],"30")

    def test_public_reference_search_without_trigger_words_and_multiple_queries(self):
        records={"W1":{"title":"Acoustic reference","text":"Helium at 300 K: 1007 m/s.","url":"https://example.org/sound","web_page":True}}
        with patch("app.worksheet_evidence.web_evidence",return_value=records) as search:
            _,job,reply=self.run_action("氦气在300 K附近的声速参考值是多少？请给出处和条件。",[
                receipt({"action":"search","query":"Helium sound speed 300 K reference"},identity="search-one"),
                receipt({"action":"search","query":"Helium speed measurement conditions"},identity="search-two"),
                receipt({"action":"answer","answer":"氦气在300 K时约为1007 m/s。[W2]","evidence":[{"id":"W2","quote":"Helium at 300 K: 1007 m/s."}]})],{"native_search":True})
        self.assertEqual(job["status"],"succeeded",job["error"])
        self.assertEqual(search.call_count,2)
        self.assertIn("1007 m/s",reply);self.assertIn("https://example.org/sound",reply)
        self.assertTrue(self.model.complete.call_args_list[0].kwargs["tools"])
        self.assertEqual(self.model.complete.call_args_list[1].args[0][-2]["tool_call_id"],"search-one")
        self.assertIn("W2",self.model.complete.call_args_list[2].args[0][-2]["content"])

    def test_local_only_and_disabled_search_do_not_access_network(self):
        for prompt,options in (("只依据我上传的资料解释比值",{"native_search":True}),("介质的声速参考值是多少",{"native_search":False})):
            with patch("app.worksheet_evidence.web_evidence") as search:
                _,job,reply=self.run_action(prompt,[receipt({"action":"search","query":"speed reference"}),receipt({"action":"answer","answer":"现有材料没有所问声速数值。","evidence":[]})],options)
            self.assertEqual(job["status"],"succeeded",job["error"]);search.assert_not_called()
            self.assertIn("没有所问",reply)

    def test_ambiguity_and_cancel_followup_leave_readings_unchanged(self):
        before=self.app.worksheets.state(self.obj["id"])
        _,job,reply=self.run_action("把那个值改一下",[receipt({"action":"clarify","answer":"要改光学比值还是屏距？","page":"swing"})])
        self.assertEqual(job["status"],"succeeded",job["error"])
        self.assertIn("光学比值",reply)
        _,job,reply=self.run_action("取消",[])
        self.assertEqual(job["status"],"succeeded",job["error"])
        self.assertIn("取消",reply);self.assertEqual(self.app.worksheets.state(self.obj["id"]),before)

    def test_method_definition_updates_without_rewriting_readings_and_checks_version(self):
        before=self.app.worksheets.state(self.obj["id"])
        replacement=copy.deepcopy(self.page);replacement["results"][0]["expr"]="first(position)"
        _,job,reply=self.run_action("位移结果改为选区首个值",[receipt({"action":"method","page":"swing"}),receipt({"plan":"按用户要求使用选区首个值。","changes":{"results":[{"id":replacement["results"][0]["id"],"expr":"first(position)"}]}},name="submit_worksheet_method")])
        self.assertEqual(job["status"],"succeeded",job["error"])
        self.assertEqual(self.app.worksheets.state(self.obj["id"]),before)
        self.assertEqual(self.app.store.object(self.obj["id"])["content"]["pages"][0]["results"][0]["expr"],"first(position)")
        self.assertIn("输入值保留",reply)
        # A new reading arriving during definition authoring prevents stale publication.
        obj=self.app.store.object(self.obj["id"]);page=obj["content"]["pages"][0]
        def concurrent(*args,**kwargs):
            changed=copy.deepcopy(before[1]);changed["swing"]["fields"]["length"]="60"
            self.app.worksheets.save(self.obj["id"],before[0],changed)
            return receipt({"plan":"再次修改方法","page":replacement},name="submit_worksheet_method")
        self.model.complete.side_effect=concurrent
        with self.assertRaisesRegex(ValueError,"已更新"):
            self.app.worksheets.change_method(self.job("再次修改"),self.exp,obj,page,"再次修改",{},lambda:None)
        self.assertEqual(self.app.store.object(self.obj["id"])["revision"],obj["revision"])

    def test_method_cannot_claim_a_hint_changed_the_input(self):
        before=self.app.worksheets.state(self.obj["id"])
        proposal=copy.deepcopy(self.page);proposal["fields"][0]["hint"]="起点已改为2秒"
        _,job,reply=self.run_action("起点改为2秒",[receipt({"action":"method","page":"swing"}),receipt({"plan":"起点已改为2秒","page":proposal},name="submit_worksheet_method")])
        self.assertEqual(job["status"],"succeeded",job["error"])
        self.assertEqual(self.app.worksheets.state(self.obj["id"]),before)
        self.assertEqual(self.app.store.object(self.obj["id"])["revision"],self.obj["revision"])
        self.assertIn("没有改变实际输入",reply)

    def test_requested_presentation_changes_keep_execution_and_readings(self):
        before=self.app.worksheets.state(self.obj["id"])
        proposal=copy.deepcopy(self.page)
        proposal["title"]="摆动数据"
        proposal["fields"][0].update(label="取段起始时刻",hint="填写记录的起始时刻")
        _,job,reply=self.run_action("页名改为摆动数据，起点标签写取段起始时刻并提示填写记录时刻",[
            receipt({"action":"method","page":"swing","scope":"presentation"}),
            receipt({"plan":"修改页名、字段标签和提示文字","page":proposal},name="submit_worksheet_method")])
        self.assertEqual(job["status"],"succeeded",job["error"])
        self.assertEqual(self.app.worksheets.state(self.obj["id"]),before)
        page=self.app.store.object(self.obj["id"])["content"]["pages"][0]
        self.assertEqual(page["title"],"摆动数据")
        self.assertEqual(page["fields"][0]["label"],"取段起始时刻")
        self.assertEqual(compute(page,before[1]["swing"]),compute(self.page,before[1]["swing"]))
        self.assertIn("说明文字修改",reply)

    def test_regrouping_steps_keeps_saved_values_and_equations(self):
        before=self.app.worksheets.state(self.obj["id"])
        steps=[dict(title="核对光学读数",fields=["length","ratio"],results=["shift"])]
        _,job,_=self.run_action("把光屏距离、比值和换算结果放在一起",[
            receipt({"action":"method","page":"swing","scope":"presentation"}),
            receipt({"plan":"合并光学测量步骤","changes":{"entry_steps":steps}},name="submit_worksheet_method")])
        self.assertEqual(job["status"],"succeeded",job["error"])
        page=self.app.store.object(self.obj["id"])["content"]["pages"][0]
        self.assertEqual(page["entry_steps"],steps)
        self.assertEqual(page["results"],self.obj["content"]["pages"][0]["results"])
        self.assertEqual(self.app.worksheets.state(self.obj["id"]),before)

    def test_other_pages_remain_addressable_without_repeating_long_notes(self):
        obj=copy.deepcopy(self.obj)
        other=copy.deepcopy(self.page);other.update(id="optics",title="光屏比值",notes="详细来源说明"*200)
        other["fields"][0]["label"]="另页起始时刻"
        obj["content"]["pages"].append(other)
        _,_,context=snapshot(self.app.worksheets,obj,self.page,"当前页终点改一下")
        self.assertEqual([p["definition_detail"] for p in context],["full","catalog"])
        self.assertNotIn("notes",context[1]["definition"])
        self.assertEqual(context[1]["definition"]["fields"][0]["id"],"start")
        self.assertIn("fields",context[1]);self.assertIn("results",context[1])
        _,_,expanded=snapshot(self.app.worksheets,obj,self.page,"光屏比值的原安排是什么意思？")
        self.assertEqual(expanded[1]["definition_detail"],"full")
        self.assertEqual(expanded[1]["definition"]["notes"],other["notes"])


class SearchBoundaryTest(unittest.TestCase):
    def test_cancel_is_not_swallowed_and_network_diagnostics_retained(self):
        agent=Mock();agent.client.provider_search.side_effect=InterruptedError("已取消")
        with patch("app.worksheet_evidence.web_search") as fallback:
            with self.assertRaises(InterruptedError):web_evidence(agent,"gas",{"native_search":True})
            fallback.assert_not_called()
        agent.client.provider_search.side_effect=OSError("provider connection reset")
        diagnostics=[]
        with patch("app.worksheet_evidence.web_search",return_value=[{"url":"https://example.org/gas","title":"Gas","snippet":"source excerpt"}]),patch("app.worksheet_evidence.webpage",side_effect=OSError("page connection reset")):
            result=web_evidence(agent,"gas",{"native_search":True},diagnostics.append)
        self.assertEqual([r["stage"] for r in diagnostics],["provider_search","webpage"])
        self.assertIn("检索摘录",result["W1"]["title"])
        self.assertFalse(result["W1"]["web_page"])


if __name__=="__main__":unittest.main()
