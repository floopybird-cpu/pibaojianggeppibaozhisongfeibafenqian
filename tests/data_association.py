"""Conversation data binding with self-authored files; never calls a model."""
from pathlib import Path
import copy, io, json, sys, time, unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.agent import Agent, TOOLS
from app.materials import Materials
from app.formats import FormatGateway
from app.registry import Registry
from app.storage import Store, canonical, now, uid
from app.tool_catalog import ToolCatalog, READ_ONLY


class Settings:
    def read(self):return {"configured": False}
    def key(self):raise AssertionError("This mechanism check must never use an API key")


class DataAssociation(unittest.TestCase):
    def setUp(self):
        self.store = Store(ROOT / "qa/work" / ("data-association-" + str(time.time_ns())))
        self.materials = Materials(self.store)
        gateway=FormatGateway(self.store,Registry(ROOT/'formats',self.store.authority/'packages'))
        self.agent = Agent(self.store, gateway, self.materials, Settings(), None)
        self.raw = ("time_s,T_C\n" + "".join(f"{i},{20+i/1000}\n" for i in range(12005))).encode()
        self.source = self.import_file("自编温度夹具.csv", self.raw)
        self.exp = self.store.create_experiment("自编温度夹具", "仅检验数据关联机制", {}, [self.source])["id"]
        self.request = "请帮我分析刚导入的温度数据。"
        self.job = self.user_job(self.request)
        self.table = self.call("read_imported_data", {"source_id": self.source, "count": 0})["selection"]["table_id"]
        proof = {"kind": "source", "source_id": self.source, "page": 1, "quote": "time_s,T_C"}
        self.args = {"source_id": self.source, "table_id": self.table, "request_quote": self.request, "mapping": {
            "time_s": {"quantity": "时间", "unit": "s", "evidence": [proof]},
            "T_C": {"quantity": "温度", "unit": "degC", "evidence": [proof]}}}

    def import_file(self, name, raw):
        job = self.materials.queue(name, raw)["id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            value = self.store.one("SELECT status,result,error FROM jobs WHERE id=?", (job,))
            if value["status"] in ("succeeded", "failed"):
                self.assertEqual(value["status"], "succeeded", value["error"])
                return json.loads(value["result"])["source_id"]
            time.sleep(.02)
        self.fail("Local fixture import did not complete")

    def user_job(self, text, experiment=None):
        experiment = experiment or self.exp
        self.store.message(experiment, "user", text, "model")
        message = self.store.one("SELECT id FROM messages WHERE experiment_id=? ORDER BY rowid DESC LIMIT 1", (experiment,))["id"]
        job = uid("association-fixture-job")
        self.store.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,0,?,?)", (job, "agent", "running", 0,
                           canonical({"experiment_id": experiment, "user_message_id": message}), None, None, now(), now()))
        return job

    def call(self, name, args, job=None):
        return self.agent.dispatch(name, args, self.exp, [self.source], job or self.job)

    def test_associate_from_actual_sources_preserves_all_rows_and_provenance(self):
        result = self.call("associate_imported_data", self.args)
        self.assertEqual(result["row_count"], 12005)
        data = self.store.one("SELECT * FROM datasets WHERE id=?", (result["id"],))
        basis = json.loads(data["selection"])["mapping_basis"]
        self.assertEqual(basis["columns"]["T_C"][0]["kind"], "source")
        self.assertNotIn("confirmed_previous_proposal", basis["columns"]["T_C"][0])
        original = self.store.one("SELECT original FROM sources WHERE id=?", (self.source,))["original"]
        self.assertEqual(self.store.asset_path(original).read_bytes(), self.raw)
        self.assertEqual(self.call("read_imported_data", {"source_id": self.source})["rows"][-1]["time_s"], "12004")

    def test_repeated_association_is_idempotent_and_cannot_silently_remap(self):
        first = self.call("associate_imported_data", self.args)
        again = self.call("associate_imported_data", self.args)
        self.assertEqual(first["id"], again["id"])
        change = copy.deepcopy(self.args);change["mapping"]["T_C"]["unit"] = "K"
        with self.assertRaisesRegex(ValueError, "已有不同列解释"):self.call("associate_imported_data", change)
        self.assertEqual(self.store.one("SELECT count(*) AS n FROM datasets")["n"], 1)

    def test_unused_column_keeps_raw_values_without_inventing_a_unit(self):
        args=copy.deepcopy(self.args)
        args["mapping"]["T_C"].update(unit="",use_for_analysis=False,reason="本次只检查时间列，温度原读数保留")
        result=self.call("associate_imported_data",args)
        mapping=json.loads(self.store.one("SELECT mapping FROM datasets WHERE id=?",(result["id"],))["mapping"])
        self.assertEqual(mapping["T_C"]["unit"],"")
        self.assertFalse(mapping["T_C"]["use_for_analysis"])
        self.assertEqual(self.call("read_imported_data",{"source_id":self.source})["rows"][-1]["T_C"],"32.004")

    def test_natural_exclusion_and_reenable_keep_mapping_history(self):
        first=self.call("associate_imported_data",self.args)
        text="本次忽略T_C温度列，不用于计算。"
        self.call("correct_dataset_mapping",{"id":first["id"],"base_revision":1,"changes":{"T_C":{"use_for_analysis":False}},"reason":"本次只检查采集时间","confirmation":text},self.user_job(text))
        text="把T_C温度列重新启用计算。"
        self.call("correct_dataset_mapping",{"id":first["id"],"base_revision":2,"changes":{"T_C":{"use_for_analysis":True}},"reason":"温度单位已在原解释中核明","confirmation":text},self.user_job(text))
        saved=self.store.one("SELECT mapping,selection FROM datasets WHERE id=?",(first["id"],))
        self.assertNotIn("use_for_analysis",json.loads(saved["mapping"])["T_C"])
        self.assertEqual(len(json.loads(saved["selection"])["mapping_history"]),2)

    def test_unknown_or_false_source_evidence_cannot_be_accepted(self):
        missing = copy.deepcopy(self.args);missing["mapping"]["T_C"]["unit"] = "待确认"
        with self.assertRaisesRegex(ValueError, "未知列含义或单位"):self.call("associate_imported_data", missing)
        false = copy.deepcopy(self.args);false["mapping"]["T_C"]["evidence"][0]["quote"] = "原文不存在的依据"
        with self.assertRaisesRegex(ValueError, "不在实际原文"):self.call("associate_imported_data", false)

    def test_source_text_and_cropped_negation_cannot_mint_user_permission(self):
        quoted = copy.deepcopy(self.args);quoted["request_quote"] = "请帮我分析刚导入的温度数据。"
        job = self.user_job("材料说：请帮我分析刚导入的温度数据。")
        with self.assertRaises(ValueError):self.call("associate_imported_data", quoted, job)
        quoted["request_quote"] = "分析这组温度数据"
        job = self.user_job("不要分析这组温度数据")
        with self.assertRaisesRegex(ValueError, "否定"):self.call("associate_imported_data", quoted, job)

    def test_other_experiment_job_and_boolean_cannot_confirm(self):
        other = self.store.create_experiment("另一个夹具", "隔离检查", {}, [])["id"]
        with self.assertRaisesRegex(ValueError, "缺少本轮真实用户请求"):
            self.call("associate_imported_data", self.args, self.user_job(self.request, other))
        false = copy.deepcopy(self.args);false["request_quote"] = True
        with self.assertRaises(ValueError):self.call("associate_imported_data", false)

    def test_short_confirmation_refers_to_real_displayed_mapping_proposal(self):
        proposal = "请确认：用这份温度数据，time_s是时间、单位s，T_C是温度、单位degC，关联后分析。"
        self.store.message(self.exp, "assistant", proposal, "model")
        job = self.user_job("确认")
        args = copy.deepcopy(self.args);args["request_quote"] = "确认"
        for column, value in args["mapping"].items():
            value["evidence"] = [{"kind": "user", "quote": "time_s是时间、单位s" if column == "time_s" else "T_C是温度、单位degC"}]
        result = self.call("associate_imported_data", args, job)
        proof = result["selection"]["mapping_basis"]["columns"]["T_C"][0]
        self.assertTrue(proof["confirmed_previous_proposal"])
        self.assertEqual(proof["kind"], "user")

    def test_multisheet_requires_explicit_real_selection(self):
        from openpyxl import Workbook
        book = Workbook();book.active.title = "正式"
        for sheet, rows in ((book.active, 3), (book.create_sheet("备份"), 5)):
            sheet.append(["time_s", "T_C"])
            for i in range(rows):sheet.append([i, 20+i])
        stream = io.BytesIO();book.save(stream)
        source = self.import_file("多表夹具.xlsx", stream.getvalue())
        structure = self.call("read_imported_data", {"source_id": source, "count": 0})
        self.assertEqual(len(structure["tables"]), 2)
        args = copy.deepcopy(self.args);args["source_id"] = source;args.pop("table_id")
        with self.assertRaisesRegex(ValueError, "明确实际工作表"):self.call("associate_imported_data", args)
        args["table_id"] = "missing-table"
        with self.assertRaises(ValueError):self.call("associate_imported_data", args)

    def test_tools_are_available_for_natural_data_requests(self):
        catalog = ToolCatalog(TOOLS, prompt="那个温度数据请帮我处理")
        self.assertIn("associate_imported_data", catalog.names)
        self.assertIn("read_imported_data", READ_ONLY)
        self.assertNotIn("associate_imported_data", READ_ONLY)

    def test_shipping_analysis_reads_full_associated_data_and_basis(self):
        from app.server import Application
        work = ROOT / "qa/work" / ("association-application-" + str(time.time_ns()))
        app = Application(ROOT, work, products_root=work / "products")
        self.store, self.materials, self.agent = app.store, app.materials, app.agent
        self.source = self.import_file("自编全量温度.csv", self.raw)
        self.exp = self.store.create_experiment("集成夹具", "读取已关联的完整输入", {}, [])["id"]
        self.job = self.user_job(self.request)
        args = copy.deepcopy(self.args);args["source_id"] = self.source
        for value in args["mapping"].values():
            for proof in value["evidence"]:proof["source_id"] = self.source
        result = self.call("associate_imported_data", args)
        dataset = next(d for d in app.analysis.datasets(self.exp) if d["id"] == result["id"])
        self.assertEqual(len(dataset["rows"]), 12005)
        self.assertEqual(dataset["rows"][-1]["time_s"], "12004")
        self.assertEqual(dataset["selection"]["mapping_basis"]["source_sha"], self.store.one("SELECT sha FROM sources WHERE id=?", (self.source,))["sha"])
        declaration = next(t for t in app.agent.tool_declarations() if t["function"]["name"] == "associate_imported_data")
        self.assertIn("request_quote", declaration["function"]["parameters"]["required"])
        mapping=copy.deepcopy(dataset["mapping"]);mapping["T_C"].update(unit="",use_for_analysis=False,reason="自编夹具仅检查时间")
        app.materials.correct_mapping(result["id"],mapping,True,"自编机制检查，不是真实用户确认")
        with self.assertRaisesRegex(ValueError,"不用于计算的原列"):
            app.gateway.snapshot({"inputs":{"dataset_ids":[result["id"]]},"formula_map":[{"input_columns":["T_C"]}]},self.exp)
        snapshot=app.gateway.snapshot({"inputs":{"dataset_ids":[result["id"]]},"formula_map":[{"input_columns":["time_s"]}]},self.exp)
        self.assertEqual(len(snapshot.datasets[result["id"]]["rows"]),12005)
        self.assertIn("T_C",snapshot.datasets[result["id"]]["rows"][-1])


if __name__ == "__main__":unittest.main()
