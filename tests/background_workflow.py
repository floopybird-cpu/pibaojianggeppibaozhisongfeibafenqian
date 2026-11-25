"""Confirmed material context and mixed answers; self-authored circuit case."""
from pathlib import Path
import sys,json,time,unittest,copy
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.server import Application
from app.demo import wait_import,demo_outline
from app.storage import uid,canonical,now
from app.agent_loop import protected_state
from app.material_context import background_records


class MaterialBackground(unittest.TestCase):
    def setUp(self):
        self.app=Application(ROOT,ROOT/'qa/work'/('background-'+str(time.time_ns())))
        self.app.questions.on_answer=None
        self.network=patch.object(self.app.agent.client,'complete',side_effect=AssertionError('No live model in mechanism checks'));self.network.start();self.addCleanup(self.network.stop)
        self.notes=wait_import(self.app,self.app.materials.queue('自编电路记录.txt','电压0.80 0.85\n电流3.0mA\n100档\n'.encode())['id'])
        self.guide=wait_import(self.app,self.app.materials.queue('自编电路讲义.txt','本模拟电路实验按时间记录电容电压，示波器CH1接电容，时间以s、电压以V记。'.encode())['id'])
        self.anchor=self.app.materials.make_anchor(self.guide,1,'')['id']
        self.exp=self.app.store.create_experiment('电路机制夹具','不评价教学效果',{},[self.guide,self.notes])['id']

    def job(self,experiment_id=None):
        identity=uid('fixture');payload={'source_id':self.notes,'activity':'organize_data'}
        if experiment_id:
            mid=uid('msg');self.app.store.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(mid,experiment_id,'user','请接续已核对背景处理这次记录。','model',now()))
            payload={'experiment_id':experiment_id,'initial_user_message_id':mid,'user_message_id':mid}
        self.app.store.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,0,?,?)',(identity,'agent' if experiment_id else 'organize','running',0,canonical(payload),None,None,now(),now()))
        return identity

    def ask(self,question,answer,experiment_id=None):
        q=self.app.questions.ask(self.job(experiment_id),experiment_id,'确认背景',[question]);self.app.questions.checkpoint(q['question_id'],{'source_id':self.notes})
        self.app.questions.answer(q['question_id'],answer)
        return q['question_id']

    def workbook(self):
        return self.app.data_workbooks.submit(self.notes,{'title':'自编电路整理表','records':[
            {'id':'u','section':'电路','quantity':'电压','values':['0.80','0.85'],'unit':'','source_line':1,'source_quote':'电压0.80 0.85','status':'pending','note':''},
            {'id':'i','section':'电路','quantity':'电流','values':['3.0'],'unit':'mA','source_line':2,'source_quote':'电流3.0mA','status':'recorded','note':''}],
            'observations':[{'section':'电路','text':'100档','source_line':3}],
            'issues':[{'id':'unit-range','record_ids':['u'],'field':'单位和量程','question':'电压单位与仪表量程是什么？'}]})

    def test_background_reply_and_difference_reach_next_task_and_fixed_draft(self):
        obj=self.workbook();old=self.app.gateway.read(obj['id'])['content']
        answer='按讲义，但CH2临时接电阻，电流表量程没记。'
        qid=self.ask({'question':'这批记录按电路讲义的装置和流程完成吗，有哪些不同？','material_basis':{'sources':[self.anchor],'record_source_ids':[self.notes]}},answer)
        state=protected_state(self.app.agent,self.exp,None,'继续分析')
        self.assertEqual(state['material_background'][0]['answer'],answer)
        self.assertEqual(state['material_background'][0]['references'][0]['source_id'],self.guide)
        read=self.app.data_workbooks.read(obj['id'],1)
        self.assertEqual(read['content'],old)
        self.assertEqual(read['material_background'][0]['question_id'],qid)
        body=copy.deepcopy(old)
        body['records'][0].update(unit='V',status='inferred',note='按本批记录实际核对的电路讲义及差异理解单位',evidence_lines=[1])
        contextual=self.app.data_workbooks.submit(self.notes,body)
        self.assertEqual(contextual['content']['material_background'][0]['answer'],answer)
        self.assertIn('实验背景',contextual['workbook_sheets'])
        self.assertEqual(contextual['content']['records'][0]['unit'],'V')
        job=self.job(self.exp)
        draft=self.app.agent.dispatch('submit_experiment_draft',{'content':demo_outline()},self.exp,[self.guide,self.notes],job)
        self.assertEqual(draft['content']['request']['material_background'][0]['answer'],answer)
        other=self.app.store.create_experiment('别的实验','不套用',{},[self.guide])['id']
        self.assertEqual(background_records(self.app.store,[self.guide],other),[])

    def test_negative_background_reply_is_preserved_without_auto_acceptance(self):
        self.ask({'question':'是否采用这份电路讲义？','material_basis':{'sources':[self.anchor],'record_source_ids':[self.notes]}},'不是，这是另一种接线。')
        rows=background_records(self.app.store,[self.notes])
        self.assertEqual(rows[0]['answer'],'不是，这是另一种接线。')
        self.assertNotIn('confirmed',rows[0])

    def test_background_requires_real_material_and_record_scope(self):
        with self.assertRaises(ValueError):self.app.questions.ask(self.job(),None,'背景',[{'question':'照此做吗？','material_basis':{'sources':['invented'],'record_source_ids':[self.notes]}}])
        with self.assertRaises(ValueError):self.app.questions.ask(self.job(),None,'背景',[{'question':'照此做吗？','material_basis':{'sources':[self.anchor]}}])

    def test_mixed_answer_saves_known_fields_and_retains_unknown_once(self):
        obj=self.workbook();answer='电压是充电前后，单位V；电流表量程未记录。'
        qid=self.ask({'question':'电压单位与仪表量程是什么？','target_object_id':obj['id'],'issue_id':'unit-range'},answer)
        saved=self.app.data_workbooks.resolve_issue(obj['id'],1,'unit-range',answer,[{'record_id':'u','quantity':'充电前后电压','unit':'V','status':'confirmed','answer_quote':'电压是充电前后，单位V'}],qid)
        self.assertEqual(saved['revision'],2)
        self.assertEqual(saved['content']['issues'][0]['status'],'partial')
        self.assertEqual(saved['content']['records'][0]['unit'],'V')
        self.assertEqual(saved['content']['records'][0]['values'],['0.80','0.85'])
        self.assertEqual(saved['content']['history'][0]['answer'],answer)
        self.assertIn('待确认',saved['workbook_sheets'])
        self.assertEqual(saved['answer_history_locations'],[{'sheet':'待确认','column':'G'}])

    def test_unrecorded_or_forged_clause_cannot_confirm_a_field(self):
        obj=self.workbook();answer='电压单位未记录，量程不记得。'
        qid=self.ask({'question':'电压单位与仪表量程是什么？','target_object_id':obj['id'],'issue_id':'unit-range'},answer)
        for quote in ('单位V','电压单位未记录'):
            with self.assertRaises(ValueError):self.app.data_workbooks.resolve_issue(obj['id'],1,'unit-range',answer,[{'record_id':'u','unit':'V','status':'confirmed','answer_quote':quote}],qid)
        self.assertEqual(self.app.gateway.read(obj['id'])['revision'],1)


if __name__=='__main__':unittest.main(verbosity=2)
