"""Regression of publication, physical observation identity and reading notation."""
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
import copy
import json
import sys
import unittest
import pymupdf

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.server import Application
from app.demo import demo_outline,wait_import
from app.storage import canonical,uid,now
from app.agent_loop import tool_output
from app.tool_catalog import ToolCatalog
from app.reading_design import prose_pieces
from app.document_delivery import missing_deliverables


class ReadingWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work=ROOT/'qa/work';work.mkdir(parents=True,exist_ok=True)
        cls.folder=TemporaryDirectory(prefix='reader-',dir=work);base=Path(cls.folder.name)
        cls.app=Application(ROOT,base/'library',products_root=base/'files')
        with pymupdf.open() as pdf:
            for line in ('Optical gates','Pulse duration 0.01 s','Two gate transit'):
                pdf.new_page().insert_text((44,80),line)
            cls.sid=wait_import(cls.app,cls.app.materials.queue('Optics.pdf',pdf.tobytes())['id'])
        cls.exp=cls.app.store.create_experiment('光电门','判断速度',{'level':'本科','goal':'读数'},[cls.sid])['id']
        cls.job=uid('fixture');mid=uid('msg')
        cls.app.store.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(mid,cls.exp,'user','请做预习文稿','model',now()))
        cls.app.store.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,0,?,?)',(cls.job,'agent','running',0,canonical({'experiment_id':cls.exp,'initial_user_message_id':mid,'user_message_id':mid}),None,None,now(),now()))
        cls.a=cls.app.materials.make_anchor(cls.sid,2,'Pulse duration 0.01 s')['id']
        cls.catalog=ToolCatalog(cls.app.agent.tool_declarations(),'prestudy')
        outline=demo_outline()
        outline.update(deliverables=['prestudy'],observations=[
            {'id':'pulse','setup_id':'gate','quantity':'遮光时间','unit':'s','setup':'同一探头','criterion':'前后缘各通过一次','sources':[cls.a]},
            {'id':'flight','setup_id':'transit','quantity':'飞行时间','unit':'s','setup':'两只探头','criterion':'前缘先后触发两门'}],
            branches=[{'id':'one','setup_id':'gate','condition':'仅一只探头触发','observables':['pulse'],'action':'用已量挡片宽度求局部速度'}])
        cls.app.agent.dispatch('submit_experiment_draft',{'content':outline},cls.exp,[cls.sid],cls.job)

    @classmethod
    def tearDownClass(cls):cls.folder.cleanup()

    def test_catalogue_and_range_preserve_original(self):
        def read(name,args):return self.app.agent.dispatch(name,{'source_id':self.sid,**args},self.exp,[self.sid],self.job)
        self.assertEqual([p['page'] for p in read('source_page_index',{'terms':['pulse']})['pages']],[2])
        pages=read('read_source_pages',{'ranges':[{'first':1,'last':3}]})['pages']
        self.assertEqual([p['page'] for p in pages],[1,2,3]);self.assertIn('0.01',pages[1]['text'])

    def test_writing_guides_follow_the_selected_deliverable(self):
        def guide(**options):
            return self.app.agent.dispatch('read_format_contract', {'kind':'LearningDocument', **options},
                                           self.exp, [self.sid], self.job)
        self.assertEqual(set(guide(view='guide')['writing_briefs']), {'prestudy','plan','record_sheet'})
        for kind in ('prestudy','plan','record_sheet'):
            self.assertEqual(guide(view='guide',doc_type=kind)['writing_briefs'], self.app.agent.document_briefs([kind]))
            self.assertEqual(list(self.app.agent.document_briefs([kind])), [kind])
        self.assertEqual(set(self.app.agent.document_briefs(['program','prestudy','plan'])), {'prestudy','plan'})
        self.assertEqual(self.app.agent.document_briefs(['analysis_result']), {})
        self.assertIn('action', guide(block_type='step')['properties'])
        for prompt in ('帮我做本实验册', '想带一本实验书去实验室'):
            catalog=ToolCatalog(self.app.agent.tool_declarations(),'chat',prompt)
            self.assertIn('submit_document', catalog.names)

    def test_whole_book_and_detached_records_in_ordinary_chat(self):
        from app.agent_loop import initial_messages
        from app.tool_catalog import requests_lab_book
        request='请做一本我能在实验台上边做边记的实验册，最后能用这些记录计算周期。'
        self.assertTrue(requests_lab_book(request))
        for other in ('只要一张空白记录表', '照实验书帮我预习', '请做实验册里的记录表',
                      '原定做实验册，现在只需要单独的记录页', '我想知道怎么写实验册'):
            self.assertFalse(requests_lab_book(other),other)
        exp=self.app.store.create_experiment('摆的周期','测量周期',{'level':'本科','goal':'现场使用'},[self.sid])['id']
        jid,mid=uid('fixture'),uid('msg')
        self.app.store.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(mid,exp,'user',request,'model',now()))
        self.app.store.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,0,?,?)',(jid,'agent','running',0,canonical(
            {'experiment_id':exp,'activity':'chat','initial_user_message_id':mid,'user_message_id':mid}),None,None,now(),now()))
        initial=next(m for m in initial_messages(self.app.agent,exp,None,request,{}) if m.get('_context_initial'))
        state=json.loads(initial['content'].split('\n',1)[1])
        self.assertEqual(state['requested_document']['doc_type'],'plan')
        outline={**demo_outline(),'deliverables':['record_sheet']}
        with self.assertRaisesRegex(ValueError,'完整|实验册'):
            self.app.agent.dispatch('submit_experiment_draft',{'content':outline},exp,[self.sid],jid)
        self.assertIsNone(self.app.agent.experiment_drafts.current(exp))
        outline['deliverables']=['plan']
        self.app.agent.dispatch('submit_experiment_draft',{'content':outline},exp,[self.sid],jid)
        self.assertEqual(missing_deliverables(self.app.agent,exp,jid,[],'chat'),['plan'])

    def test_whole_diagram_preserves_both_panels_and_exact_detail_stays_local(self):
        from app.page_navigation import illustration_bounds
        geometry={'regions':[{'kind':'嵌入图片','region':[.1,.2,.5,.6]},
                             {'kind':'嵌入图片','region':[.52,.2,.90,.6]}],
                  'captions':[{'region':[.16,.61,.85,.645],'text':'图二 双光路'}]}
        fixed=illustration_bounds([.08,.19,.92,.54],geometry)
        self.assertAlmostEqual(fixed[3],.6,places=6)
        self.assertAlmostEqual(illustration_bounds([.08,.19,.92,.66],geometry)[3],.61,places=6)
        detail=[.14,.27,.36,.4]
        self.assertEqual(illustration_bounds(detail,geometry),detail)
        self.assertEqual(illustration_bounds(detail,{}),detail)

    def test_plan_uses_shared_typesetting_and_offline_prestudy_uses_its_outline(self):
        from app.demo import install_demo
        from docx import Document
        from docx.oxml.ns import qn
        demo=install_demo(self.app)
        def read_files(key):
            obj=self.app.gateway.read(demo[key])
            return obj['content'], {a['name']:self.app.store.asset_path(a['path']) for a in obj['assets']}
        plan,_=read_files('plan_id')
        crop=self.app.materials.make_anchor(demo['source_ids'][0],1,region=[0.06,0.04,0.94,0.30])['id']
        plan['sections'][0]['blocks'].append({'type':'figure','source_anchor':crop,
                                              'caption':'节选的教学原页','sources':[crop]})
        obj=self.app.gateway.apply('LearningDocument',plan,experiment_id=demo['experiment_id'])
        files={a['name']:self.app.store.asset_path(a['path']) for a in obj['assets']}
        doc=Document(files['document.docx'])
        formulas=[p for p in doc.paragraphs if p.style.name=='Display Formula']
        self.assertTrue(any(p._p.xpath('.//m:oMath') for p in formulas))
        goal=plan['goals'][0]
        formula=self.app.gateway.read(goal['formula_ref']['id'],goal['formula_ref']['revision'])
        # This formula is used only in the structured plan goal.
        self.assertNotIn(goal['formula_ref'], [b.get('formula_ref') for s in plan['sections'] for b in s['blocks']])
        self.assertTrue(formula['content']['latex'])
        self.assertGreaterEqual(len(formulas),2)
        # The goal-only equation is still native math, without reusing a
        # local reference number reserved by the main text.
        number_labels=[p.text.split(')')[0].strip() for p in formulas if '(' in p.text]
        self.assertEqual(len(number_labels),len(set(number_labels)))
        self.assertTrue(any(not p.text.strip() and p._p.xpath('.//m:oMath') for p in formulas))
        steps=plan['steps']
        self.assertGreater(len(steps),1)
        located=[]
        for step in steps:
            para=next(p for p in doc.paragraphs if step['reason'] in p.text)
            located.append(len(para._p.xpath('.//w:hyperlink[@w:anchor]')))
        self.assertFalse(any(located[:-1]))
        self.assertGreater(located[-1],0)
        self.assertTrue(doc.element.body.findall('.//'+qn('wp:inline')))
        with pymupdf.open(files['document.pdf']) as pdf:
            self.assertIn('操作顺序与完成判据', ''.join(p.get_text() for p in pdf).replace(' ','').replace('\n',''))
        for key in ('short_id','deep_id'):
            content,_=read_files(key)
            self.assertEqual(content['doc_type'],'prestudy')
            self.assertEqual(content['warnings'],[])
            check=next(s for s in content['sections'] if s['id']=='course_check')
            prep=next(s for s in demo_outline()['stages'] if s['key']=='prestudy')
            self.assertIn(prep['points'][0]['interpretations'][0]['question'],[b['text'] for b in check['blocks']])

    def test_publication_failure_preserves_files_without_claiming_delivery(self):
        from app.agent_loop import run_agent
        from unittest.mock import patch
        app=self.app;exp=app.store.create_experiment('光电门导出','预习',{'level':'本科','goal':'读数'},[self.sid])['id']
        job=uid('fixture');mid=uid('msg');request='帮我做一份光电门预习。'
        app.store.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(mid,exp,'user',request,'model',now()))
        app.store.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,0,?,?)',(job,'agent','running',0,canonical({'experiment_id':exp,'initial_user_message_id':mid,'user_message_id':mid}),None,None,now(),now()))
        doc={'title':'光电门','doc_type':'prestudy','background':{'level':'本科','goal':'读数'},
             'summary':'读脉冲。','sources':[self.a],'appendices':[],'knowledge_refs':[],'warnings':[],'simulation':False,
             'sections':[{'id':'pulse','title':'脉冲','blocks':[{'type':'paragraph','text':'观测边沿。','sources':[self.a]}]}]}
        outline=demo_outline();outline.pop('request',None);outline['deliverables']=['prestudy']
        calls=[('submit_experiment_draft',{'content':outline}),('submit_document',{'content':doc}),(None,'光电门预习稿已写好。')]
        class Local:
            provider='local-test'
            def complete(self,messages,tools,options=None):
                name,args=calls.pop(0)
                if name is None:return {'content':args},{}
                return {'tool_calls':[{'id':uid('call'),'type':'function','function':{'name':name,'arguments':canonical(args)}}]},{}
        with patch.object(app.agent,'client',Local()),patch.object(app.publications,'publish',side_effect=OSError('只读目标目录')):
            run_agent(app.agent,job,exp,request,'prestudy')
        row=app.store.one("SELECT status,result,error FROM jobs WHERE id=?",(job,))
        self.assertEqual(row['status'],'failed');self.assertIn('只读目标目录',row['error'])
        self.assertEqual(calls,[]);self.assertIn('重新导出',json.loads(row['result'])['message'])
        saved=app.store.one("SELECT id FROM objects WHERE kind='LearningDocument' AND experiment_id=?",(exp,))
        first=app.gateway.read(saved['id'],1)
        self.assertEqual(first['publication']['status'],'failed')
        newer=app.gateway.edit(saved['id'],1,[{'op':'set','path':'/title','value':'另存的第二版'}],human=True)
        def resume():
            import time
            app.agent.resume_job(job)
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                current=app.store.one('SELECT status,result FROM jobs WHERE id=?',(job,))
                if current['status'] not in ('running','queued') and exp not in app.agent._active:return current
                time.sleep(.05)
            self.fail('未完成本地重发')
        from unittest.mock import Mock
        model=Mock(provider='local-test');model.complete.side_effect=AssertionError('文件重发不应调用模型')
        with patch.object(app.agent,'client',model):
            with patch.object(app.publications,'publish',side_effect=OSError('目标仍未就绪')):
                self.assertEqual(resume()['status'],'failed')
            # Early releases wrote a finished checkpoint and a succeeded job even
            # when the captured external delivery receipt said it failed.
            state=app.agent.runtime.load(job);state.pop('publication');state['phase']='finished'
            app.agent.runtime.checkpoint(job,state);app.store.execute("UPDATE jobs SET status='succeeded' WHERE id=?",(job,))
            done=resume()
        model.complete.assert_not_called()
        self.assertEqual(done['status'],'succeeded')
        delivery=next(p for p in json.loads(done['result'])['publications'] if p['id']==saved['id'])
        self.assertEqual(delivery['revision'],1)
        import hashlib
        for ext in ('pdf','docx'):
            original=next(a['sha'] for a in first['assets'] if a['name']=='document.'+ext)
            self.assertEqual(hashlib.sha256(next(Path(delivery['folder']).glob('*.'+ext)).read_bytes()).hexdigest(),original)
        self.assertEqual(app.gateway.read(saved['id'])['revision'],newer['revision'])

    def test_ratio_and_verbatim(self):
        pieces=list(prose_pieces('测 V_A/V_ref，保留 R_L.csv 和 sample_index'))
        self.assertEqual([s for math,s in pieces if math],['V_{A}',r'V_{\mathrm{ref}}'])
        self.assertIn('R_L.csv',''.join(s for math,s in pieces if not math))

    def test_source_locations_candidate_recovery_and_inherited_readings(self):
        import csv
        from docx import Document
        from app.document_delivery import unfinished_candidate
        def call(name,args):
            identity=uid('call')
            result,_,ok=tool_output(self.app.agent,self.job,{'id':identity,'function':{'name':name,'arguments':canonical(args)}},self.exp,[self.sid],self.catalog)
            return identity,result,ok
        old=self.app.agent.experiment_drafts.current(self.exp)
        observations=copy.deepcopy(old['content']['observations'])
        observations.append({'id':'width','setup_id':'caliper','quantity':'遮光片宽度','unit':'mm','setup':'卡尺外量爪','criterion':'读两边缘间距'})
        branch={**old['content']['branches'][0],'prior_observations':['width']}
        _,changed,ok=call('edit_artifact',{'id':old['id'],'base_revision':old['revision'],'operations':[
            {'op':'set','path':'/observations','value':observations},{'op':'set','path':'/branches/0','value':branch}]})
        self.assertTrue(ok,changed.get('error'))
        section={'id':'p','title':'测速','blocks':[{'type':'paragraph','text':'辨认前后缘。','sources':['missing-a']},
            {'type':'columns','columns':[{'blocks':[{'type':'paragraph','text':'一个脉冲。','sources':[]}]},
             {'blocks':[{'type':'paragraph','text':'两个边沿。','sources':['missing-b']}]}]},
            {'type':'branch_table','caption':'遮光观测','branches':['one'],'sources':[self.a]}]}
        body={'title':'门宽与独立标定','doc_type':'prestudy','background':{'level':'本科','goal':'测速'},'summary':'一只门上能得到什么。',
            'sources':['missing-a','missing-b',self.a],'sections':[section],'appendices':[],'knowledge_refs':[],'warnings':[],'simulation':False}
        base,error,ok=call('submit_document',{'content':body})
        self.assertFalse(ok);self.assertFalse(error['committed']);self.assertEqual(error['candidate'],base)
        grouped={i.get('anchor',i['code']):set(i['paths']) for i in error['issues']}
        self.assertEqual(grouped,{'missing-a':{'/sources/0','/sections/0/blocks/0/sources/0'},
            'missing-b':{'/sources/1','/sections/0/blocks/1/columns/1/blocks/0/sources/0'},
            'evidence_required':{'/sections/0/blocks/1/columns/0/blocks/0/evidence'}})
        _,wrong,ok=call('correct_document_candidate',{'candidate':base,'changes':[
            {'op':'set','path':'/title','value':'半途改名'},{'op':'set','path':'/sections/8/title','value':'过期位置'}]})
        self.assertFalse(ok);self.assertEqual(wrong['candidate'],base)
        self.assertEqual(unfinished_candidate(self.app.agent,self.job)['candidate'],base)
        self.assertEqual(self.app.agent.dispatch('read_document_candidate',{'candidate':base,'path':'/title'},self.exp,[self.sid],self.job)['value'],body['title'])
        edits=[{'op':'set','path':p,'value':'explanation' if k=='evidence_required' else self.a} for k,paths in grouped.items() for p in sorted(paths)]
        _,obj,ok=call('correct_document_candidate',{'candidate':base,'changes':edits})
        self.assertTrue(ok,obj.get('error'));self.assertEqual(obj['revision'],1)
        rows=[]
        for path in (Path(obj['publication']['folder'])/'表格').glob('*.csv'):
            with path.open(encoding='utf-8-sig',newline='') as stream:rows.extend(csv.reader(stream))
        decision=next(row for row in rows if row[0]==branch['condition'])
        self.assertIn('同一探头',decision[1]);self.assertNotIn('卡尺',decision[1])
        self.assertIn('遮光片宽度 / mm（卡尺外量爪，读两边缘间距）',decision[2])
        assets={a['name']:self.app.store.asset_path(a['path']) for a in obj['assets']}
        tables=Document(assets['document.docx']).tables
        self.assertTrue(any('卡尺外量爪' in cell.text for table in tables for row in table.rows for cell in row.cells))
        with pymupdf.open(assets['document.pdf']) as pdf:
            self.assertIn('卡尺外量爪',''.join(p.get_text().replace(' ','').replace('\n','') for p in pdf))
        self.assertEqual([s for math,s in prose_pieces('单元串 A-B-X_ref，保留 B-X_ref.csv',joined=True) if math],[r'X_{\mathrm{ref}}'])

    def test_candidate_repairs_one_field_then_builds_once(self):
        example={'type':'worked_example','caption':'光电门算例','branch':'one','sources':[self.a],
           'inputs':[{'key':'t','observation':'flight','origin':'reference','value':0.01,'source_anchor':self.a}],
           'calculations':[{'key':'v','quantity':'速度','method':'挡片宽度取 $0.012$ m，除以遮光时间。','expression':'0.012/t','unit':'m/s'}]}
        doc={'title':'光电门预习','sources':[],'background':{'level':'本科','goal':'读数'},'summary':'观察遮光脉冲。',
             'doc_type':'prestudy','appendices':[],'knowledge_refs':[],'warnings':[],'simulation':False,
             'sections':[{'id':'g','title':'观察','blocks':[{'type':'paragraph','text':'在门上读取 t_g。','sources':[self.a]},
              {'type':'paragraph','text':'把 t_g 与挡片宽度联系起来。','sources':[self.a]},
              {'type':'equation','latex':r'v=\frac{w}{t_g}','sources':[self.a]},
              {'type':'branch_table','branches':['one'],'caption':'条件与读数','sources':[self.a]},example]}]}
        def run(name,args):
            cid=uid('call');call={'id':cid,'function':{'name':name,'arguments':canonical(args)}}
            result,_,ok=tool_output(self.app.agent,self.job,call,self.exp,[self.sid],self.catalog)
            return cid,result,ok
        cid,error,ok=run('submit_document',{'content':doc});self.assertFalse(ok)
        self.assertEqual(error.get('candidate'),cid);self.assertIn('无法读取',error['error'])
        self.assertTrue(missing_deliverables(self.app.agent,self.exp,self.job,[],'prestudy'))
        old=self.app.agent.experiment_drafts.current(self.exp)
        def edit(path,value):
            return self.app.agent.dispatch('edit_artifact',{'id':old['id'],'base_revision':old['revision'],
                'operations':[{'op':'set','path':path,'value':value}]},self.exp,[self.sid],self.job)
        with self.assertRaisesRegex(ValueError,'不能直接读'):edit('/branches/0/observables',['pulse','flight'])
        with self.assertRaisesRegex(ValueError,'真实请求'):edit('/request/initial','other')
        updated=edit('/branches/0/action','按单门遮光求局部速度')
        self.assertEqual(updated['content']['observations'],old['content']['observations'])
        self.assertEqual(self.app.agent.dispatch('read_document_candidate',{'candidate':cid,'path':'/title'},self.exp,[self.sid],self.job)['value'],doc['title'])
        from app.context_policy import ContextPolicy
        pol=ContextPolicy(self.app.agent.runtime,self.job)
        prior=[{'role':'user','_context_handoff':True,'content':'原页全文和裁图位置'}]
        after=pol.after_outline(prior,{'experiment_draft':updated['content']},{'candidate':cid})
        self.assertEqual(after[:-1],prior);self.assertIn(cid,after[-1]['content'])
        _,obj,ok=run('correct_document_candidate',{'candidate':cid,'changes':[{'op':'set','path':'/sections/0/blocks/4/inputs/0/observation','value':'pulse'}]})
        self.assertTrue(ok,obj.get('error'));self.assertEqual(obj['revision'],1);self.assertEqual(obj['content']['sources'],[self.a])
        files={a['name']:self.app.store.asset_path(a['path']) for a in obj['assets']}
        self.assertEqual(obj['publication']['status'],'ready')
        published=Path(obj['publication']['folder'])
        self.assertTrue((published/'公式.tex').is_file())
        self.assertTrue(list((published/'表格').glob('*.csv')))
        import csv
        rows=[]
        for path in (published/'表格').glob('*.csv'):
            with path.open(encoding='utf-8-sig',newline='') as stream:rows.extend(csv.reader(stream))
        self.assertTrue(any('1.2 m/s' in row for row in rows))
        with ZipFile(files['document.docx']) as z:
            from lxml import etree
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main','m':'http://schemas.openxmlformats.org/officeDocument/2006/math'}
            root=etree.fromstring(z.read('word/document.xml'))
            self.assertGreaterEqual(len(root.xpath('//m:oMath',namespaces=ns)),3)
            self.assertFalse(root.xpath('//w:t[contains(.,"t_g")]',namespaces=ns))
            self.assertTrue(root.xpath('//w:ind[@w:firstLine]',namespaces=ns))
        with pymupdf.open(files['document.pdf']) as pdf:
            self.assertIn('1.2',''.join(page.get_text() for page in pdf))
        self.assertEqual(missing_deliverables(self.app.agent,self.exp,self.job,[{'success':True,'saved_artifact_ids':[obj['id']]}],'prestudy'),[])


if __name__=='__main__':unittest.main(verbosity=2)
