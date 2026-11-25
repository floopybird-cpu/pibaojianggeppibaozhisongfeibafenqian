"""Question lifecycle regression. Protocol fixtures are not live-model evidence."""
from pathlib import Path
import json
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.server import Application
from app.storage import now


class QuestionWorkflow(unittest.TestCase):
    def setUp(self):
        self.work=ROOT/"qa/work"/("questions-"+str(time.time_ns()))
        self.app=Application(ROOT,self.work,products_root=self.work.parent/(self.work.name+"-产物"))
        self.exp=self.app.store.create_experiment("问答机制验证","只检验暂停与接续，不产生物理结论",{"level":"微积分","goal":"确认单位","minutes":5},[])["id"]

    def wait(self,job,status):
        for i in range(200):
            row=self.app.store.one("SELECT * FROM jobs WHERE id=?",(job["id"],))
            if row["status"]==status:return row
            if row["status"]=="failed":raise AssertionError(row["error"])
            time.sleep(.02)
        raise AssertionError("Question lifecycle failed to reach "+status)

    def fixture(self,mutating_tail=False):
        app=self.app
        class Client:
            provider="protocol_fixture_NOT_live_model"
            def __init__(self):self.round=0;self.seen=[]
            def complete(self,messages,*_,**__):
                self.round+=1;self.seen=messages
                if self.round==1:
                    calls=[{"id":"ask-1","function":{"name":"ask_user","arguments":json.dumps({"title":"需要确认温度单位","questions":[{"question":"这个温度读数用摄氏度还是开尔文？","options":["摄氏度","开尔文","未记录"]}]},ensure_ascii=False)}}]
                    if mutating_tail:calls.append({"id":"unsafe-tail","function":{"name":"create_category","arguments":json.dumps({"name":"不应提前执行","parent":"domain-热","domain":"热"})}})
                    return {"content":None,"tool_calls":calls},{"fixture":True}
                if self.round==2:
                    from app.demo import demo_outline
                    outline=demo_outline();outline.pop('request',None)
                    return {'tool_calls':[{'id':'reply-outline','function':{'name':'submit_experiment_draft','arguments':json.dumps({'content':outline},ensure_ascii=False)}}]},{'fixture':True}
                return {"content":"已收到你的实际答复，未记录的单位继续保留未知。"},{"fixture":True}
        app.agent.client=Client()
        return app.agent.client

    def test_pause_does_not_execute_later_tool(self):
        self.fixture(True);job=self.app.agent.queue(self.exp,"先确认温度单位，没记录就保留未知。")
        self.wait(job,"waiting_user")
        self.assertFalse(self.app.store.one("SELECT id FROM categories WHERE name='不应提前执行'"))
        q=self.app.questions.pending(self.exp)[0]
        self.assertIsNone(q["answer"]);self.assertIsNotNone(q["context"])
        self.assertEqual(len(q["context"]["messages"][-1]["content"] )>0,True)

    def test_chat_answer_resumes_same_job(self):
        client=self.fixture();job=self.app.agent.queue(self.exp,"温度单位没写，先问我。")
        self.wait(job,"waiting_user");q=self.app.questions.pending(self.exp)[0]
        reply=self.app.agent.queue(self.exp,"没记下来，先保留未知。")
        self.assertEqual(reply["id"],job["id"]);self.wait(job,"succeeded")
        self.assertEqual(self.app.questions.read(q["id"])["answer"],"没记下来，先保留未知。")
        self.assertIn("没记下来，先保留未知。",str(client.seen))
        self.assertEqual(self.app.store.one("SELECT COUNT(*) n FROM jobs")["n"],1)

    def test_pending_question_survives_reopen(self):
        self.fixture();job=self.app.agent.queue(self.exp,"缺单位，先问。")
        self.wait(job,"waiting_user");identity=self.app.questions.pending(self.exp)[0]["id"]
        reopened=Application(ROOT,self.work,products_root=self.work.parent/(self.work.name+"-产物"))
        self.assertEqual(reopened.questions.pending(self.exp)[0]["id"],identity)
        self.assertEqual(reopened.store.one("SELECT status FROM jobs WHERE id=?",(job["id"],))["status"],"waiting_user")
        self.assertIsNotNone(reopened.questions.read(identity)["context"])

    def test_cancel_waiting_question(self):
        self.fixture();job=self.app.agent.queue(self.exp,"先问清楚。")
        self.wait(job,"waiting_user");identity=self.app.questions.pending(self.exp)[0]["id"]
        self.app.post('/api/jobs/'+job['id']+'/cancel',{})
        self.assertEqual(self.app.questions.read(identity)["status"],"cancelled")
        self.assertEqual(self.app.store.one("SELECT status FROM jobs WHERE id=?",(job["id"],))["status"],"cancelled")
        with self.assertRaises(ValueError):self.app.questions.answer(identity,"是摄氏度")

    def test_batch_answers_are_scoped_to_their_own_issue(self):
        self.app.questions.on_answer=None
        # Use the real parser/receipt service. No model-written assignment is
        # accepted as proof that an answer belongs to a different question.
        from app.storage import canonical,uid
        identity=uid('fixture');self.app.store.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,0,?,?)",(identity,'agent','waiting_user',0,canonical({'experiment_id':self.exp}),None,None,now(),now()))
        q=self.app.questions.ask(identity,self.exp,'两处独立事实',[{'id':'pan','question':'是哪块盘？'},{'id':'unit','question':'温度单位是什么？'}])
        self.app.questions.checkpoint(q['question_id'],{'messages':[],'usage':[],'completed':[],'allowed_sources':[]})
        self.app.questions.answer(q['question_id'],'第一题是上盘，第二题未记录。',{'pan':'上盘','unit':'未记录'})
        record=self.app.questions.read(q['question_id'])
        self.assertEqual(record['context']['submitted_item_answers'],{'pan':'上盘','unit':'未记录'})

    def test_read_formal_record_for_notebook_without_changing_experiment(self):
        queued=self.app.materials.queue('正式温度记录.csv',b'time_s,temperature_C\n0,16.5\n10,10.3\n')
        imported=self.wait(queued,'succeeded');source=json.loads(imported['result'])['source_id']
        before=self.app.store.experiment(self.exp)
        found=self.app.agent.dispatch('list_library_sources',{'query':'正式温度'},None,[],'not-used')
        self.assertEqual([r['id'] for r in found],[source])
        page=self.app.agent.dispatch('read_page',{'source_id':source,'page':1},None,[],'not-used')
        self.assertIn('16.5',page['text']);self.assertIn('10.3',page['text'])
        self.assertEqual(self.app.store.experiment(self.exp),before)
        with self.assertRaises(ValueError):self.app.agent.dispatch('read_page',{'source_id':'missing','page':1},None,[],'not-used')

    def test_provider_context_recovery_preserves_full_result_and_same_job(self):
        text='温度记录\n'+'16.5 C; '*1600+'\n最后一项10.3 C'
        imported=self.wait(self.app.materials.queue('长正式记录.txt',text.encode('utf-8')),'succeeded')
        source=json.loads(imported['result'])['source_id'];owner=self
        class ContextClient:
            provider='protocol_fixture_NOT_live_model'
            def __init__(self):self.calls=0;self.receipt=None
            def complete(self,messages,*_,**__):
                self.calls+=1
                if self.calls==1:return {'tool_calls':[{'id':'read-source','function':{'name':'read_page','arguments':json.dumps({'source_id':source,'page':1})}}]},{}
                if self.calls==2:raise ValueError("模型 HTTP 400: This model's maximum context length is exceeded")
                if self.calls==3:
                    result=next(json.loads(m['content']) for m in messages if m['role']=='tool' and 'pageable_full_tool_result' in m.get('content',''))
                    self.receipt=result['receipt_id']
                    return {'tool_calls':[{'id':'read-tail','function':{'name':'read_tool_result','arguments':json.dumps({'receipt_id':self.receipt,'offset':result['total_characters']-200,'count':200})}}]},{}
                if self.calls==4:
                    owner.assertIn('10.3',messages[-1]['content'])
                    from app.demo import demo_outline
                    outline=demo_outline();outline.pop('request',None)
                    return {'tool_calls':[{'id':'recovered-outline','function':{'name':'submit_experiment_draft','arguments':json.dumps({'content':outline},ensure_ascii=False)}}]},{}
                return {'content':'已保留并核对原文末尾。'},{}
        client=ContextClient();self.app.agent.client=client
        job=self.app.agent.queue(self.exp,'核对长记录，原文和成品必须完整。');self.wait(job,'succeeded')
        full=self.app.agent.dispatch('read_tool_result',{'receipt_id':client.receipt},self.exp,[],job['id'])
        self.assertTrue(full['complete']);self.assertIn(text,json.loads(full['content'])['text'])
        self.assertEqual(self.app.store.one('SELECT COUNT(*) n FROM jobs WHERE kind=?',('agent',))['n'],1)
        with self.assertRaises(ValueError):self.app.agent.dispatch('read_tool_result',{'receipt_id':client.receipt},self.exp,[],'another-job')

    def test_context_recovery_only_after_actual_capacity_error(self):
        from app.model_context import page_rejected_context
        messages=[{'role':'tool','content':'x'*5000}]
        before=json.dumps(messages)
        self.assertFalse(page_rejected_context(messages,'HTTP 401 invalid key',self.app.store.authority,'a-job'))
        self.assertEqual(json.dumps(messages),before)
        self.assertFalse((self.app.store.authority/'tool-results').exists())

    def test_another_questions_answer_cannot_confirm_unknown_unit(self):
        from app.data_workbooks import DataWorkbooks
        service=DataWorkbooks.__new__(DataWorkbooks)
        service.answer_lookup=lambda _: {'status':'answered','answer':'第一题上盘，第二题未记录','targets':[{'target_object_id':'book','issue_id':'pan'},{'target_object_id':'book','issue_id':'unit'}], 'answers':[{'target_object_id':'book','issue_id':'pan','answer':'上盘'},{'target_object_id':'book','issue_id':'unit','answer':'未记录'}]}
        with self.assertRaises(ValueError):service._confirmed_answer('book','unit','上盘','batch')
        self.assertTrue(service._confirmed_answer('book','unit','未记录','batch'))


if __name__=="__main__":
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(QuestionWorkflow)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    (ROOT/"qa/questions-workflow-results.json").write_text(json.dumps({"scope":"question lifecycle fixture; live provider evidence is separate","tests":result.testsRun,"passed":result.wasSuccessful(),"failures":[str(v) for v in result.failures+result.errors],"finished":now()},ensure_ascii=False,indent=2),encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
