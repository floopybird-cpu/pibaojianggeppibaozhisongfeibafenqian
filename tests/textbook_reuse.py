"""Textbook page reuse and evidence shared between experiments, without remote services."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import knowledge_index as kb, worksheet_evidence as evidence
from app.page_navigation import select_page_numbers
from app.server import Application
from app.storage import canonical, now


class TextbookTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / "qa/work"
        work.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.TemporaryDirectory(dir=work)
        self.addCleanup(tmp.cleanup)
        self.app = Application(ROOT, Path(tmp.name)/"library", products_root=Path(tmp.name)/"products")
        self.store = self.app.store
        import pymupdf
        original = self.store.originals / "optics.pdf"
        with pymupdf.open() as doc:
            for _ in range(6):
                doc.new_page().insert_text((40,40),"Optics")
            doc.save(original)
        import hashlib
        checksum = hashlib.sha256(original.read_bytes()).hexdigest()
        self.store.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?)",
                           ("book",checksum,"Optics.pdf","pdf","originals/optics.pdf",now(),"{}"))
        for number in range(1, 7):
            self.store.execute("INSERT INTO pages VALUES(?,?,?,?,?,?)", ("book",number,str(number),"Optics",None,"[]"))

    def test_completed_overlapping_active_and_failed_ranges(self):
        agent = self.app.agent
        with patch("app.agent.threading.Thread.start") as start:
            first = agent.distill("book", [1, 2])
            self.assertTrue(agent.distill("book", [2])["already_active"])
            self.store.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (first["id"],))
            for n in [1,2]: self.store.execute("INSERT INTO kb_done VALUES(?,?,?,?)", ("book",n,"",first["id"]))
            second = agent.distill("book", [1, 2, 3])
            payload = self.store.one("SELECT payload FROM jobs WHERE id=?", (second["id"],))
            self.assertEqual(json.loads(payload["payload"])["pages"], [3])
            self.assertTrue(agent.distill("book", [2])["already_processed"])
            self.store.execute("UPDATE jobs SET status='failed' WHERE id=?", (second["id"],))
            third = agent.distill("book", [2, 3])
            self.assertNotEqual(second["id"], third["id"])
            self.store.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (third["id"],))
            for n in [3]: self.store.execute("INSERT INTO kb_done VALUES(?,?,?,?)", ("book",n,"",third["id"]))
            self.assertEqual(set(agent.distill("book", [1, 2, 3])["prior_jobs"]), {first["id"], third["id"]})
            self.assertEqual(start.call_count, 3)

    def test_bilingual_entry_is_shared_and_pinned_revision_is_preserved(self):
        self.store.execute("INSERT INTO objects VALUES(?,?,?,?,?,?)", ("lens","SciencePoint",None,2,"空间滤波",now()))
        anchor = self.app.materials.make_anchor("book",2)["id"]
        for rev, text in [(1,"空间滤波 spatial filtering"), (2,"空间滤波 spatial filtering in optics")]:
            self.store.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("lens",rev,"SciencePoint","fixture","1","digest",1,canonical({"title":"空间滤波","definition":text,"search_summary":"图像的空间频率筛选","search_terms":["spatial filtering"],"sources":[anchor]}),canonical([anchor]),"[]",None,now()))
        with self.store.transaction() as conn: kb.migrate(conn)
        for title, query, pins, rev in [("光学","空间滤波",[],2),("Imaging","spatial filtering",[{"id":"lens","revision":1}],1)]:
            exp = self.store.create_experiment(title,"理解成像",{},[])
            exp["knowledge_refs"] = pins
            matches = evidence.selected_knowledge(self.app,exp,query)
            self.assertEqual([v["url"] for v in matches.values()], [f"physlab://knowledge/lens/{rev}"])
            item = next(iter(matches.values()))
            self.assertEqual("in optics" in item["text"], rev==2)
            self.assertEqual(item["originals"][0]["url"],"physlab://page/book/2")
            reply,_ = evidence.cited_answer("解释 [K1]", matches)
            self.assertIn("Optics.pdf · PDF 第 2 页",reply)
            self.assertIn("空间滤波",reply)

    def test_original_pages_are_read_only_after_confirming_cards(self):
        from app.knowledge_workflow import decode_record
        kb.set_scope(self.store,"book",2,4)
        from app.worksheet_evidence import materials
        exp = self.store.create_experiment("独立问题","查看依据",{},["book"])
        self.assertEqual(materials(self.app,exp),{})
        self.assertEqual([r["number"] for r in materials(self.app,exp,include_references=True).values()],[2,3,4])
        kb.set_scope(self.store,"book",2,4,role="handout")
        self.assertEqual(len(materials(self.app,exp)),3)
        tex=chr(92)
        raw='{"x":"'+tex+'('+tex+'frac{a}{b}'+tex+')"}'
        self.assertEqual(decode_record(raw)["x"],tex+'('+tex+'frac{a}{b}'+tex+')')
        self.assertEqual(decode_record(json.dumps({"x":tex+"beta"})),{"x":tex+"beta"})
        with patch("app.agent.threading.Thread.start"):
            first=self.app.agent.distill("book",[2,3])
        job=first["id"]
        model_note={"title":"光学系统的空间滤波","summary":"通过频域掩模改变成像。","terms":["空间滤波","spatial filtering"],
                    "knowledge":"傅里叶面掩模选择空间频率。","conditions":["近轴成像"],"observable":"像面明暗分布变化。",
                    "pages":[2],"domain":"光"}
        with patch("app.knowledge_catalog.organize",return_value={}),patch.object(self.app.knowledge,"ask",side_effect=[
            {"notes":[model_note],"skipped":[]},{"notes":[],"skipped":[{"page":3,"reason":"目录"}]}]) as model:
            self.app.knowledge.run(job,None,"","book")
            self.assertEqual(model.call_count,2)
        status=self.store.one("SELECT status,error FROM jobs WHERE id=?",(job,))
        self.assertEqual(status["status"],"succeeded",status["error"])
        self.assertTrue(self.app.agent.distill("book",[2,3])["already_processed"])
        with patch.object(self.store,"object",side_effect=AssertionError("检索候选时读了正文")):
            cards=kb.search(self.store,"spatial filtering")
            self.assertEqual(cards[0]["locations"][0]["page"],2)
        service=self.app.worksheets
        with patch.object(service,"ask_model",side_effect=['{"queries":["spatial filtering"]}','{"entries":[1]}']):
            found=evidence.selected_knowledge(self.app,exp,"怎样挡掉图像的一种细纹",service=service,jid=job)
            self.assertEqual(next(iter(found.values()))["originals"][0]["number"],2)
        with patch.object(service,"ask_model",side_effect=['{"queries":["空间滤波"]}','{"entries":[]}']),patch.object(self.store,"object",side_effect=AssertionError("拒选后读了正文")):
            self.assertFalse(evidence.selected_knowledge(self.app,exp,"这是否能确定质量",service=service,jid=job))

        source={"id":"book","name":"Optics.pdf"}
        current=cards[0]["id"]
        scope={"native_vision":False}
        def reuse(n):
            return {"notes":[],"skipped":[],"reused":[{"id":current,"pages":[n]}]}
        with patch.object(self.app.knowledge,"ask",side_effect=[reuse(4),{"same":True,"notes":[] }]):
            self.assertEqual(self.app.knowledge.read_batch(job,source,[4],"",scope),[current])
        self.assertEqual(self.store.object(current)["revision"],2)
        kb.set_scope(self.store,"book",2,5)
        different=dict(model_note,title="不同的成像条件",conditions=["另一成立条件"],pages=[5],knowledge="另一条件下的独立结论。")
        with patch.object(self.app.knowledge,"ask",side_effect=[reuse(5),{"same":False,"notes":[different]}]):
            ids=self.app.knowledge.read_batch(job,source,[5],"",scope)
        self.assertNotEqual(ids,[current])
        self.assertEqual(self.store.object(current)["revision"],2)

    def test_hierarchy_and_conditions_control_equivalence(self):
        from app import knowledge_catalog as directory
        from app.knowledge_workflow import KnowledgeWorkflow
        store=self.store
        anchor=self.app.materials.make_anchor("book",2)["id"]
        for i,title in enumerate(["成像位置","像距的确定","光圈的作用"]):
            body={"title":title,"definition":title,"search_summary":"成像教学材料："+title,"search_terms":[title],
                  "conditions":["限制条件"],"sources":[anchor]}
            store.execute("INSERT INTO objects VALUES(?,?,?,?,?,?)",(f"item{i}","SciencePoint",None,1,title,now()))
            store.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(f"item{i}",1,"SciencePoint","fixture","1","digest",1,canonical(body),canonical([anchor]),"[]",None,now()))
        with store.transaction() as conn:kb.migrate(conn)
        flow=self.app.knowledge
        ids=[r["id"] for r in sorted(directory.cards_for_book(store,"book"),key=lambda c:(2,c["title"]))]
        indices={v:k for k,v in enumerate(ids)}
        outlines={"domain":"自然科学","units":[{"path":["物理学","几何光学","近轴成像","位置与孔径"],"summary":"观察成像位置及孔径。",
                    "core":indices["item0"],"detail":[],"overlap":[indices["item1"],indices["item2"]]}]}
        with patch.object(flow,"check"),patch.object(flow,"ask",side_effect=[outlines,{"verdicts":[
            {"id":"item1","same_as":"item0","reason":"条件和命题相同"},{"id":"item2","same_as":"","reason":"改变的是另一物理量"}]}]) as model:
            receipt=directory.organize(flow,"test","book",{})
            self.assertEqual(receipt["checked"],2)
            self.assertEqual(directory.organize(flow,"test","book",{})["new_members"],0)
            self.assertEqual(model.call_count,2)
        gateway=self.app.gateway
        natural=gateway.category("自然科学")["id"]
        physics=gateway.category("物理学",natural)["id"]
        old_parent=gateway.category("测试下一级","domain-光","光")["id"]
        self.assertEqual(directory.category_paths(store)[old_parent],["自然科学","物理学","光","测试下一级"])
        gateway.category("已归类光学",physics,category_id="domain-光")
        self.assertEqual(directory.category_paths(store)["domain-光"],["自然科学","物理学","已归类光学"])
        with self.assertRaises(ValueError):gateway.category("自然科学","domain-光",category_id=natural)
        self.assertEqual(gateway.category("物理学",natural)["id"],physics)
        root=store.one("SELECT id FROM categories WHERE name='几何光学'")["id"]
        cards=kb.search(store,category=root)
        self.assertEqual(len(cards),3)
        self.assertEqual([r["title"] for r in directory.browse(store,cards,root,"")],["近轴成像"])
        self.assertEqual(len(directory.distinct_evidence(store,cards)),2)
        self.assertTrue(directory.find_topics(store,"位置与孔径"))
        self.assertTrue(directory.queue(flow,"book")["already_processed"])

    def test_public_seed_and_preserved_legacy_paths(self):
        from app import knowledge_seed as seed, knowledge_catalog as catalog
        paths=catalog.category_paths(self.store)
        self.assertEqual(sorted(v[0] for v in paths.values() if len(v)==1),sorted(["自然科学","工程与技术","数学与计算"]))
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM meta WHERE key=?",(seed.MARKER,))
            conn.execute("INSERT INTO categories VALUES('domain-热',NULL,'热','热')")
            conn.execute("INSERT INTO categories VALUES('old-heat','domain-热','热传导','热')")
            seed.install(conn)
        self.assertEqual(catalog.category_paths(self.store)["old-heat"],["自然科学","物理学","热","热传导"])
        self.app.gateway.category("自选热学位置",None,category_id="domain-热")
        with self.store.transaction() as conn:seed.install(conn)
        self.assertEqual(catalog.category_paths(self.store)["old-heat"],["自选热学位置","热传导"])

    def test_explanations_keep_their_own_page_sources(self):
        from app.knowledge_workflow import KnowledgeWorkflow
        source={"id":"book","name":"Optics.pdf"}
        kb.index_source(self.store,"book")
        kb.set_scope(self.store,"book",2,3)
        note=dict(title="相邻原页讲解",summary="检索短说明",terms=["检索短说明"],domain="物理学",
                  knowledge="一个核心结论。",observable="量值",conditions=["前提"],pages=[2,3],
                  parts=[dict(heading="推理",explanation="第二页支持的推理。",pages=[2]),
                         dict(heading="特例",explanation="第三页的特殊情况。",pages=[3])])
        with patch.object(self.app.knowledge,"ask",return_value=dict(notes=[note],skipped=[],reused=[])),patch.object(self.app.knowledge,"check"):
            ids=self.app.knowledge.read_batch("fixture",source,[2,3],"",{"native_vision":False})
        self.assertEqual(len(ids),1)
        c=self.store.object(ids[0])["content"]
        self.assertEqual([(part["text"].splitlines()[0],[self.store.anchor(a)["page"] for a in part["sources"]])
                          for part in c["claims"]],[("推理",[2]),("特例",[3])])
        search=kb.search(self.store,"检索短说明")
        self.assertEqual(len(search),1)
        self.assertNotIn("第二页支持的推理",str(search))
        self.assertIn("第二页支持的推理",kb.hydrate(self.store,search)["K1"]["text"])
        bad=dict(note,parts=[dict(heading="错误出处",explanation="说明。",pages=[4])])
        with self.assertRaises(ValueError):
            KnowledgeWorkflow.validate(dict(notes=[bad],skipped=[]),[2,3])

    def test_single_card_with_the_same_title_as_its_proposed_folder(self):
        from app.knowledge_catalog import filing_path
        group=dict(path=["物理学","示例教材","周期估计"],detail=[],overlap=[],summary="估计周期")
        self.assertEqual(filing_path("自然科学",group,"周期估计",set()),(["物理学","示例教材"],"示例教材"))
        existing={("自然科学",*group["path"])}
        self.assertEqual(filing_path("自然科学",group,"周期估计",existing)[0],group["path"])
        self.assertEqual(filing_path("自然科学",dict(group,detail=[2]),"周期估计",set())[0],group["path"])

    def test_answer_attaches_notes_once_and_only_to_cited_pages(self):
        from app import knowledge_notes,worksheet_interaction as interaction
        store=self.app.store
        kb.index_source(store,"book")
        source={"id":"book","name":"Optics.pdf"}
        note=dict(title="示例周期",summary="用周期计算频率",terms=["周期"],domain="物理学",
                  knowledge="周期与频率互为倒数。",observable="频率",conditions=["周期运动"],pages=[2])
        with patch.object(self.app.knowledge,"ask",return_value=dict(notes=[note],skipped=[],reused=[])),patch.object(self.app.knowledge,"check"):
            oid=self.app.knowledge.read_batch("fixture",source,[2],"",{"native_vision":False})[0]
        project=store.create_experiment("独立复核","理解示例的量值",{},[])
        ref=kb.search(store,"周期")[0]
        def selected():return kb.hydrate(store,[ref])
        correction=dict(entry="K1",page_url="physlab://page/book/2",location="式（2.1）",kind="source",
                        finding="两种代法不一致。",basis="周期两秒时倒数为半赫兹。")
        answer={"action":"answer","answer":"周期应取倒数。[K1]","knowledge_notes":[correction]}
        with patch.object(evidence,"selected_knowledge",side_effect=lambda *args,**kw:selected()),patch.object(interaction,"submit",return_value=(answer,{})) as model:
            for n in range(2):
                text=interaction.answer(self.app.worksheets,project["id"],None,None,"什么是周期？",{},lambda:None)
                self.assertIn("physlab://page/book/2",text)
                self.assertEqual(model.call_count,n+1)
            self.assertIn("两种代法不一致",str(model.call_args.args[3]))
        self.assertEqual(len(knowledge_notes.for_revision(store,oid,1)),1)
        self.assertNotIn("两种代法",str(kb.search(store,"周期")))
        verified=self.app.agent._dispatch("read_knowledge",dict(id=oid,revision=1),project["id"],set(),None)
        self.assertEqual(verified["reading_notes"],selected()["K1"]["reading_notes"])
        self.assertIn("两种代法",knowledge_notes.for_worksheet(store,[selected()["K1"]["url"]]))
        self.assertFalse(knowledge_notes.for_worksheet(store,[]))
        resolved=dict(correction,location="eq. (2.1)",conclusion="按倒数计算。")
        self.assertEqual(knowledge_notes.save(store,[resolved],selected(),None)["saved"],1)
        self.assertEqual(knowledge_notes.save(store,[resolved],selected(),None)["saved"],0)
        for invalid in (dict(correction,entry="K9"),dict(correction,page_url="physlab://page/book/3")):
            self.assertEqual(knowledge_notes.save(store,[invalid],selected(),None)["saved"],0)
        self.assertEqual(knowledge_notes.save(store,[dict(correction,kind="transcription")],selected(),None)["saved"],1)
        old=store.object(oid)
        self.app.gateway.apply(old["kind"],dict(old["content"],definition="另行订正的定义。"),object_id=oid,base_revision=1)
        self.assertEqual([r["kind"] for r in knowledge_notes.for_revision(store,oid,2)],["source"])
        self.assertEqual(len(knowledge_notes.for_revision(store,oid,1)),2)
        kb.set_scope(store,"book",3,4)
        self.assertFalse(knowledge_notes.for_revision(store,oid,2))

    def test_file_page_ranges(self):
        self.assertIsNone(select_page_numbers("  ", 30))
        self.assertEqual(select_page_numbers("1，8-12、10–13, 20", 30), [1,8,9,10,11,12,13,20])
        for invalid in ("0", "18-15", "15-3000000000", "2, x"):
            with self.assertRaises(ValueError):
                select_page_numbers(invalid, 30)


if __name__ == "__main__":
    unittest.main()
