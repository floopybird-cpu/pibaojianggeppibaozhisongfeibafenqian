"""One experimental Agent, durable attempts, dependency-aware tools, Qt events."""
from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

from .context_policy import ContextPolicy,wire_messages
from .storage import canonical,now
from .tool_catalog import ToolCatalog,BASE_INSTRUCTIONS,READ_ONLY


def protected_state(agent,experiment_id,source_id,prompt):
    state={'current_user_request':prompt,'experiment_id':experiment_id}
    state['available_library_sources']=agent.store.all('SELECT id,name,kind,created AS imported_at,(SELECT count(*) FROM pages p WHERE p.source_id=s.id) page_count FROM sources s ORDER BY created,id')
    if experiment_id:
        exp=agent.store.experiment(experiment_id)
        state['experiment']={k:exp[k] for k in ('title','goal','notes','background','source_ids','instruments')}
        state['datasets']=[{k:d[k] for k in ('id','source_id','mapping','confirmed','selection') if k in d}
                           for d in agent.store.all('SELECT * FROM datasets WHERE experiment_id=?',(experiment_id,))]
        for dataset in state['datasets']:
            for key in ('mapping','selection'):
                if isinstance(dataset.get(key),str):dataset[key]=json.loads(dataset[key])
        state['experiment_draft']=agent.experiment_drafts.context(experiment_id)
        state['artifacts']=agent.dispatch('list_saved_artifacts',{},experiment_id,exp['source_ids'],'context-read')
        from .record_context import source_records
        record_sources=list(exp['source_ids'])+[d['source_id'] for d in state['datasets'] if d.get('source_id')]
        state['related_data_records']=source_records(agent,record_sources)
        # Organizing notes is a global task and can precede a new experiment.
        # Keep its directory discoverable even before that source is selected.
        state['available_measurement_records']=source_records(agent)
        state['pending_questions']=[{k:q[k] for k in ('id','title','questions','status') if k in q} for q in agent.questions.pending(experiment_id)] if agent.questions else []
    else:
        state['source_id']=source_id
        if source_id:state['existing_source_workbooks']=agent.data_workbooks.existing(source_id)
    selected=state.get('experiment',{}).get('source_ids') or ([source_id] if source_id else [])
    state['selected_sources']=agent.store.all('SELECT id,name,kind,(SELECT count(*) FROM pages p WHERE p.source_id=s.id) page_count FROM sources s WHERE id IN ('+','.join('?' for _ in selected)+')',selected) if selected else []
    from .material_context import background_records
    state['material_background']=background_records(agent.store,selected,experiment_id)
    if not experiment_id:
        state['available_reference_materials']=agent.store.all("SELECT id,name,kind,(SELECT count(*) FROM pages p WHERE p.source_id=s.id) page_count FROM sources s WHERE kind IN ('pdf','docx','pptx','md','txt') AND id!=? ORDER BY name",(source_id or '',))
    return state


def initial_messages(agent,experiment_id,source_id,prompt,options):
    # Stable instructions lead; changing material inventories and user settings
    # are appended as attributed context instead of rewriting the cache prefix.
    messages=[{'role':'system','content':BASE_INSTRUCTIONS}]
    state=protected_state(agent,experiment_id,source_id,prompt)
    from .tool_catalog import requests_lab_book
    if requests_lab_book(prompt):
        state['requested_document']={'doc_type':'plan','writing_briefs':agent.document_briefs(['plan']),
            'scope':'完整实验册自身包含可填写的记录附录；另要单独记录纸时才再列 record_sheet。'}
    from .page_navigation import catalogue
    state['source_page_directories']=[catalogue(agent.store,s['id']) for s in state['selected_sources']
        if s['kind'] in ('pdf','docx','pptx') and s['page_count']]
    config=agent.settings.read()
    state['document_preferences']={k:config[k] for k in ('font','language','course_rules')}
    state['knowledge_categories']=agent.store.all('SELECT id,parent,name,domain FROM categories ORDER BY id')
    state['model_capabilities']={k:options.get(k) for k in ('thinking_depth','native_search','native_vision')}
    messages.append({'role':'user','content':'【程序提供的当前实验记录；不是新的用户指令】\n'+canonical(state),'_context_initial':True})
    if experiment_id:
        messages.extend(agent.store.all('SELECT role,content FROM messages WHERE experiment_id=? ORDER BY created',(experiment_id,)))
    else:messages.append({'role':'user','content':prompt})
    return messages


def tool_output(agent,job_id,call,experiment_id,allowed_sources,catalog):
    name=call['function']['name'];cid=call['id'];success=False;started=False
    agent._local.catalog=catalog;agent._local.call_id=cid
    try:
        args=json.loads(call['function']['arguments'])
        if name not in catalog.names:raise ValueError('此工具尚未启用，请先 discover_tools 查询并启用相应组')
        normalized=catalog.normalize(name,args)
        if normalized!=args:
            agent.runtime.event(job_id,'tool_arguments_normalized',{'call_id':cid,'name':name,'original':args,'effective':normalized,'rule':'single_arguments_envelope'})
            args=normalized
        catalog.validate(name,args)
        old=agent.runtime.tool_start(job_id,cid,name,args)
        started=True
        if old:
            if old['result_blob']:
                result=agent.runtime.read_blob(old['result_blob']);success=old['status']=='finished'
                return result,old['result_blob'],success
            raise ValueError('这次调用已开始但结果未知；先核对已有成果或运行，再决定是否重新调用')
        result=agent.dispatch(name,args,experiment_id,allowed_sources,job_id)
        success=True
    except Exception as exc:
        from .document_delivery import CandidateChangeError
        from .source_citations import SourceValidationError
        result={'error':agent.settings.redact(str(exc)),'committed':False if not started or name in READ_ONLY else None}
        if isinstance(exc,SourceValidationError):
            result.update(issues=exc.issues,committed=False)
        if isinstance(exc,CandidateChangeError):
            result['committed']=False
            if exc.parent:result.update(candidate=exc.parent,next_action='整批改动未应用。读取此候选的实际字段后再集中修改。')
        elif started and name in ('submit_document','correct_document_candidate') and isinstance(exc,(ValueError,KeyError,IndexError,TypeError)):
            result.update(candidate=cid,next_action='候选稿内容已保留，correct_document_candidate按字段修正，无需重发全文。')
        if result['committed'] is None:result['notice']='操作结果须核对当前修订/运行，不能据异常就认定没有写入。'
        previous=agent.store.one('SELECT result_blob FROM runtime_tools WHERE job_id=? AND call_id=?',(job_id,cid))
        if previous and previous['result_blob']:
            identity=agent.runtime.event(job_id,'tool_identity_conflict',result)
            return result,identity,False
        # Invalid arguments also receive a durable result, without claiming a
        # tool actually started or a write was rolled back.
        if not agent.store.one('SELECT 1 FROM runtime_tools WHERE job_id=? AND call_id=?',(job_id,cid)):
            agent.runtime.tool_start(job_id,cid,name,{'invalid_arguments':call['function'].get('arguments','')})
    identity=agent.runtime.tool_finish(job_id,cid,result,success)
    agent.store.audit('agent_tool',{'job_id':job_id,'experiment_id':experiment_id,'tool':name,'success':success})
    return result,identity,success


def execute_tools(agent,job_id,calls,experiment_id,allowed_sources,catalog):
    """Independent reads run together; every write/interaction forms a barrier."""
    outputs=[];i=0
    with ThreadPoolExecutor(max_workers=6,thread_name_prefix='physics-read') as pool:
        while i<len(calls):
            if agent.store.one('SELECT cancel FROM jobs WHERE id=?',(job_id,))['cancel']:raise InterruptedError('用户取消；已完成工具结果保留')
            call=calls[i]
            if call['function']['name'] in READ_ONLY:
                end=i+1
                while end<len(calls) and calls[end]['function']['name'] in READ_ONLY:end+=1
                pending=[pool.submit(tool_output,agent,job_id,c,experiment_id,allowed_sources,catalog) for c in calls[i:end]]
                # Tool results are persisted inside each worker as it finishes,
                # independently of collection order or a stalled sibling.
                for item,future in zip(calls[i:end],pending):outputs.append((item,*future.result()))
                i=end
            else:
                value=tool_output(agent,job_id,call,experiment_id,allowed_sources,catalog)
                outputs.append((call,*value));i+=1
                if call['function']['name']=='ask_user' and value[0].get('status')=='waiting_user':
                    for skipped in calls[i:]:
                        result={'not_executed':True,'reason':'等待真实用户答复；后续工具没有开始'}
                        agent.runtime.tool_start(job_id,skipped['id'],skipped['function']['name'],json.loads(skipped['function']['arguments']))
                        identity=agent.runtime.tool_finish(job_id,skipped['id'],result,False)
                        outputs.append((skipped,result,identity,False))
                    break
    return outputs


def image_parts(result):
    images=[]
    if isinstance(result,dict):
        if result.get('_image'):
            # A crop can also carry its full-page context image. Never serialize
            # that nested binary as ordinary text beside the real image blocks.
            images.append({'type':'text','text':'工具原页图，须真实核查：'+canonical(without_images(result))})
            images.append({'type':'image_url','image_url':{'url':result['_image']}})
        for key,value in result.items():
            if key!='_image':images.extend(image_parts(value))
    elif isinstance(result,list):
        for value in result:images.extend(image_parts(value))
    return images


def without_images(result):
    if isinstance(result,dict):return {k:without_images(v) for k,v in result.items() if k!='_image'}
    if isinstance(result,list):return [without_images(v) for v in result]
    return result


def run_agent(agent,job_id,experiment_id,prompt,activity,source_id=None,resume=None):
    paused=False;messages=[];usage=[];completed=[];allowed_sources=[]
    runtime=agent.runtime
    previous=runtime.load(job_id) if resume and resume.get('_checkpoint') else None
    state=previous or resume or {}
    delivery_message=state.get('delivery_message')
    publication=state.get('publication')
    announced={tuple(item) for item in state.get('quality_ready_notices',[])}
    if state.get('prompt'):prompt=state['prompt']
    catalog=ToolCatalog(agent.tool_declarations(),activity,prompt,state.get('catalog'))
    policy=ContextPolicy(runtime,job_id,state.get('context_policy'))
    agent._local.catalog=catalog
    row=agent.store.one('SELECT payload,result FROM jobs WHERE id=?',(job_id,));payload=json.loads(row['payload'])
    options=payload.get('request_options') or agent.settings.read()
    full_options={**agent.settings.read(),**options}
    def cancelled():return bool(agent.store.one('SELECT cancel FROM jobs WHERE id=?',(job_id,))['cancel'])
    def checkpoint(phase):
        runtime.checkpoint(job_id,{'messages':messages,'usage':usage,'completed':completed,'allowed_sources':allowed_sources,
            'catalog':catalog.names,'context_policy':policy.state(),'phase':phase,'prompt':prompt,'activity':activity,'source_id':source_id,
            'quality_ready_notices':[list(item) for item in sorted(announced)],'delivery_message':delivery_message,'publication':publication})
    def save_progress():
        agent.store.execute('UPDATE jobs SET result=?,updated=? WHERE id=?',
            (canonical({'usage':usage,'tools':completed,'provider':agent.client.provider,'request_options':options,
                        'native_sources':agent.native_sources.get(job_id,[]),'runtime':runtime.summary(job_id)}),now(),job_id))
    def add_usage(items):
        known={u.get('_runtime_request_id') for u in usage if u.get('_runtime_request_id')}
        for item in items:
            identity=item.get('_runtime_request_id')
            if not identity or identity not in known:
                usage.append(item)
                if identity:known.add(identity)
    def finish_publication(plan,prior=()):
        ready={(p['id'],p.get('revision')):p for p in prior if p.get('status')=='ready'}
        deliveries=[]
        for target in plan['targets']:
            if cancelled():raise InterruptedError('用户取消；已保存内容可继续交付')
            key=(target['id'],target['revision'])
            if key in ready:
                deliveries.append(ready[key]);continue
            obj=agent.gateway.read(*key)
            delivery=agent.gateway.publications.deliver(obj)
            deliveries.append({**target,**delivery})
        failed=[d for d in deliveries if d.get('status')=='failed']
        error='；'.join(str(d.get('error') or '写出失败') for d in failed) or None
        text=plan['message']
        if error:text+='\n\n产物文件夹未完整写出。可接续此任务重新导出已保存的版本。原因：'+error
        if deliveries:
            runtime.event(job_id,'delivery_failed' if error else 'verified_delivery',
                          {'outputs':[{k:d.get(k) for k in ('id','revision','status','error')} for d in deliveries]})
        if experiment_id:
            notice=('重新导出未完成：'+error if error else '本次已保存的成果已重新放入产物文件夹。') if prior else text
            agent.store.message(experiment_id,'assistant',notice,'system_notice' if prior else 'model')
        result={'message':text,'publications':deliveries,'usage':usage,'tools':completed,'provider':agent.client.provider,
                'request_options':options,'native_sources':agent.native_sources.get(job_id,plan.get('native_sources',[])),'runtime':runtime.summary(job_id)}
        for receipt in reversed(completed):
            if receipt.get('worksheet'):
                result.update(worksheet=receipt['worksheet'],open=True,page=receipt.get('worksheet_page'))
                break
        agent.store.execute("UPDATE jobs SET status=?,progress=100,result=?,error=?,updated=? WHERE id=?",
            ('failed' if error else 'succeeded',canonical(result),error,now(),job_id))
        checkpoint('publication_failed' if error else 'finished')
    previous_result=json.loads(row['result'] or '{}') if state.get('messages') else {}
    previous_deliveries=previous_result.get('publications',[])
    from .document_delivery import has_failed_publication
    retry_publication=bool(publication) or (state.get('phase') in ('publication_failed','finished') and has_failed_publication(previous_result))
    scope=agent.client.scope(runtime,job_id) if not retry_publication and hasattr(agent.client,'scope') else nullcontext()
    agent.store.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(now(),job_id))
    try:
        with scope, (agent.gateway.publications.working() if agent.gateway.publications else nullcontext()):
            allowed_sources=list(state.get('allowed_sources') or (agent.store.experiment(experiment_id)['source_ids'] if experiment_id else [source_id]))
            if state.get('messages'):
                if retry_publication:
                    from .document_delivery import publication_targets
                    messages=state['messages'];usage=list(state.get('usage',[]));completed=list(state.get('completed',[]))
                    if not publication:
                        message=next((m['content'] for m in reversed(messages) if m.get('role')=='assistant' and m.get('content') and not m.get('tool_calls')), '本次实验成果已保存。')
                        publication={'targets':publication_targets(agent,job_id,completed,previous_deliveries),'message':message,'native_sources':previous_result.get('native_sources',[])}
                    checkpoint('publishing')
                    finish_publication(publication,previous_deliveries);return
                messages=runtime.repair_pending(job_id,copy.deepcopy(state['messages']))
                if messages and messages[0].get('role')=='system' and messages[0].get('content')!=BASE_INSTRUCTIONS:
                    runtime.event(job_id,'workflow_instructions_updated',{'previous':messages[0]['content'],'current':BASE_INSTRUCTIONS,'reason':'接续任务采用当前草稿工作流；真实用户要求和原始历史不变'})
                    messages[0]['content']=BASE_INSTRUCTIONS
                usage=list(state.get('usage',[]));completed=list(state.get('completed',[]))
                from .document_delivery import restore_saved_outputs
                restore_saved_outputs(agent,job_id,completed)
                if state.get('answer'):
                    answer={'role':'user','content':'【本任务真实用户对提问的答复】\n'+state['answer']+'\n未答复部分仍待确认。提问记录：'+state['question_id']}
                    messages.append(answer);runtime.event(job_id,'user_answer',answer)
                else:messages.append({'role':'user','content':'【本机任务接续】继续当前请求；已持久化结果保留，结果未知的保存或执行先核查再继续。'})
            else:
                messages=initial_messages(agent,experiment_id,source_id,prompt,options)
                runtime.event(job_id,'initial_messages',messages)
            while True:
                if cancelled():raise InterruptedError('用户取消；已完成成果与完整记录保留')
                agent._local.catalog=catalog
                from .document_delivery import missing_deliverables
                host_finish=bool(delivery_message) and not missing_deliverables(agent,experiment_id,job_id,completed,activity)
                declarations=catalog.declarations()
                if not host_finish:
                    protected=protected_state(agent,experiment_id,source_id,prompt)
                    if state.get('answer'):protected['latest_real_user_answer']=state['answer']
                    messages=policy.prepare(messages,declarations,full_options,
                        lambda m,t,o:agent.client.complete(wire_messages(m),t,options=o),protected)
                    if policy.extra_usage:add_usage(policy.extra_usage);policy.extra_usage=[];save_progress()
                    checkpoint('before_model_request')
                try:
                    if host_finish:reply={'content':delivery_message}
                    else:reply,spent=agent.client.complete(wire_messages(messages),declarations,options=full_options)
                except ValueError as exc:
                    if policy.provider_rejected(messages,exc,declarations):checkpoint('provider_context_recovery');continue
                    raise
                # Persist before cancellation checks, JSON/tool parsing or any
                # other operation which could fail after a paid model response.
                if host_finish:runtime.event(job_id,'document_assets_ready',{'message':delivery_message})
                else:
                    add_usage([spent]);runtime.event(job_id,'model_response',{'reply':reply,'usage':spent,'provider':agent.client.provider})
                    policy.observed(messages,declarations,spent)
                calls=reply.get('tool_calls') or []
                # Identifiers label complete tool results; no raw body is
                # silently replaced here. They enable deliberate stage release.
                assistant={'role':'assistant','content':reply.get('content')}
                if calls:assistant['tool_calls']=calls
                for key in ('reasoning_content','reasoning_details','_native_output'):
                    if key in reply:assistant[key]=reply[key]
                messages.append(assistant);save_progress();checkpoint('response_received')
                if cancelled():raise InterruptedError('用户取消；模型返回已记账，未再执行工具')
                if not calls:
                    text=reply.get('content') or '模型没有返回正文。'
                    # Authorship follows a saved outline. Independent audits
                    # report once after generation; no automatic critic/rewrite loop.
                    operational_tools={'read_experiment_draft','open_product','publish_artifacts','list_products','list_saved_artifacts','discover_tools','list_task_contexts','reference_prior_task'}
                    successful=[c['tool'] for c in completed if c.get('success')]
                    retrieval_only=bool(set(successful)&{'open_product','publish_artifacts'}) and set(successful)<=operational_tools
                    if experiment_id and not retrieval_only:
                        try:agent.experiment_drafts.require(experiment_id,job_id)
                        except ValueError as exc:
                            messages.append({'role':'user','content':'【宿主草稿流程提醒；不是用户新要求】'+str(exc)+'。保留真实用户的交付范围，接续简要底稿后完成本次已要求的成果与说明；不把此内部提醒写成用户改变意愿。'})
                            continue
                    gaps=missing_deliverables(agent,experiment_id,job_id,completed,activity) if not retrieval_only else []
                    if gaps:
                        delivery_message=None
                        messages.append({'role':'user','content':'【宿主交付核对】本任务尚缺已要求的成果：'+', '.join(gaps)+'。请接续本次任务。'})
                        continue
                    if activity=='organize_data':
                        sheets=[o for o in agent.gateway.query(kind='DataWorkbook') if o['content']['source_id'] in allowed_sources]
                        if not sheets:
                            messages.append({'role':'user','content':'【宿主交付检查；不是用户新要求】规范数据表尚未实际保存。请保存全部已确定和待确认的原始记录及Excel，不能只交聊天描述。'})
                            continue
                        target=sheets[0];issues=[q for q in target['content']['issues'] if q['status']=='open']
                        if issues:
                            q=agent.questions.ask(job_id,experiment_id,'这些原始记录需要你确认',[{'question':q['question'],'target_object_id':target['id'],'issue_id':q['id']} for q in issues])
                            paused=True;checkpoint('waiting_user')
                            agent._pause_question(job_id,experiment_id,q['question_id'],runtime.load(job_id));return
                    from .document_delivery import publication_targets
                    targets=publication_targets(agent,job_id,completed) if agent.gateway.publications else []
                    publication={'targets':targets,'message':text,'native_sources':agent.native_sources.get(job_id,[])}
                    checkpoint('publishing');finish_publication(publication);return
                agent._local.policy=policy;agent._local.messages=messages
                outputs=execute_tools(agent,job_id,calls,experiment_id,allowed_sources,catalog)
                images=[];waiting=None
                for call,result,identity,success in outputs:
                    name=call['function']['name']
                    try:
                        args=json.loads(call['function'].get('arguments') or '{}')
                        if name in catalog.names:args=catalog.normalize(name,args)
                    except (ValueError,TypeError):args={}
                    if name=='ask_user' and isinstance(result,dict) and result.get('status')=='waiting_user':waiting=result['question_id']
                    if name in ('submit_document','correct_document_candidate','finish_document_draft') and success and args.get('final_message'):
                        delivery_message=args['final_message']
                    images.extend(image_parts(result));visible=without_images(result)
                    if name in ('submit_experiment_draft','apply_knowledge','submit_document','correct_document_candidate','submit_program','finish_document_draft','append_document_sections','edit_artifact','upgrade_artifact','submit_data_workbook','resolve_data_issue','resolve_data_issues') and isinstance(visible,dict):
                        visible={k:visible[k] for k in ('id','kind','revision','title','pack_id','pack_version','data_version','publication','layout','source_structure_check','draft_basis','workbook_sheets','answer_history_locations','material_background','resolution_receipts','resolution_errors','committed','all_succeeded') if k in visible} if success else visible
                    if success and experiment_id and name in ('submit_experiment_draft','submit_document','submit_program','finish_document_draft','append_document_sections','edit_artifact','apply_artifact_edits') and isinstance(visible,dict):
                        from .task_scope import job_task
                        task=job_task(agent,job_id,experiment_id)
                        visible['task_basis']={'request':task.get('initial_request'),'latest_user_message':task.get('latest_user_message'),'meaning':'宿主绑定的真实要求；工具/内部提醒和Agent自己的改正不是用户新指令。'}
                    if isinstance(visible,dict):visible={**visible,'complete_result_id':identity}
                    else:visible={'result':visible,'complete_result_id':identity}
                    message=policy.receipt(name,args,visible,identity);message['tool_call_id']=call['id'];messages.append(message)
                    receipt={'tool':name,'round':len(usage),'success':success,'call_id':call['id']}
                    if success and name=='open_measurement_workbench' and isinstance(result,dict):
                        receipt['worksheet']=result.get('worksheet')
                        receipt['worksheet_page']=result.get('page')
                        receipt['description']='已建立可录入的数据处理台'
                    if success and name in ('inspect_document','read_batch'):
                        from .document_review import inspection_receipts
                        receipt['document_inspections']=inspection_receipts(result)
                    if success and name in ('review_document_content','read_batch'):
                        from .content_review import review_receipts
                        receipt['content_reviews']=review_receipts(result)
                    if success and name=='run_program' and isinstance(result,dict) and result.get('status')=='succeeded' and result.get('program_ref'):
                        receipt['generated_runs']=[{'id':result['id'],'program_id':result['program_ref']['id'],'program_revision':result['program_ref']['revision']}]
                    if success and name in ('inspect_run','read_batch'):
                        from .analysis import run_image_receipts
                        receipt['run_image_inspections']=run_image_receipts(result)
                    if success and name in ('submit_document','correct_document_candidate','finish_document_draft','append_document_sections',
                                            'edit_artifact','apply_artifact_edits','upgrade_artifact'):
                        candidates=[result]+(result.get('results',[]) if isinstance(result,dict) else [])
                        receipt['generated_documents']=[{'id':v['id'],'revision':v['revision']}
                            for v in candidates if isinstance(v,dict) and v.get('kind')=='LearningDocument'
                            and 'id' in v and 'revision' in v]
                    if success and name in ('submit_experiment_draft','submit_document','correct_document_candidate','finish_document_draft','append_document_sections','submit_program','edit_artifact','apply_artifact_edits','upgrade_artifact','run_program','submit_data_workbook','resolve_data_issue','resolve_data_issues'):
                        candidates=[result]+(result.get('results',[]) if isinstance(result,dict) else [])
                        receipt['saved_artifact_ids']=[v['id'] for v in candidates if isinstance(v,dict) and v.get('kind') in ('ExperimentDraft','LearningDocument','AnalysisProgram','AnalysisResult','DataWorkbook')]
                        if name=='run_program' and isinstance(result,dict) and result.get('artifact_id'):receipt['saved_artifact_ids'].append(result['artifact_id'])
                    if name=='inspect_document':
                        receipt['checked_document']=args.get('id')
                        receipt['checked_revision']=args.get('revision') or (result.get('revision') if isinstance(result,dict) else None)
                    if isinstance(result,dict):
                        receipt.update({k:result[k] for k in ('id','kind','revision','title','error') if k in result})
                    if name in ('read_page','view_page'):receipt['description']=('核对原页图：' if name=='view_page' else '阅读：')+str(args.get('page',''))+' 页'
                    elif name=='ask_user':receipt['description']='等待你确认事实'
                    elif name=='review_document_content':receipt['description']='对照原件核对文稿内容'
                    elif isinstance(result,dict) and result.get('title'):receipt['description']='已保存：'+result['title']
                    completed.append(receipt)
                if images:
                    image_message={'role':'user','content':images}
                    image_message['_context_image_record']=runtime.event(job_id,'source_images',image_message)
                    image_message['_context_image_results']=[identity for _,result,identity,_ in outputs if image_parts(result)]
                    messages.append(image_message)
                if waiting or any(not ok for _,_,_,ok in outputs):delivery_message=None
                outline_saved=any(ok and isinstance(value,dict) and value.get('kind')=='ExperimentDraft' and c['function']['name'] in ('submit_experiment_draft','edit_artifact') for c,value,_,ok in outputs)
                if not waiting and outline_saved:
                    from .document_delivery import unfinished_candidate
                    latest=protected_state(agent,experiment_id,source_id,prompt)
                    deliverables=latest['experiment_draft']['content'].get('deliverables', [])
                    latest['writing_briefs']=agent.document_briefs(deliverables)
                    candidate=unfinished_candidate(agent,job_id)
                    messages=policy.after_outline(messages,latest,candidate)
                save_progress();checkpoint('tools_complete')
                if waiting:
                    paused=True;agent._pause_question(job_id,experiment_id,waiting,runtime.load(job_id));return
    except Exception as exc:
        status='cancelled' if isinstance(exc,InterruptedError) or cancelled() else 'failed';error=agent.settings.redact(str(exc))
        # Also retain summary-call usage when summary failed after a paid return.
        if policy.extra_usage:add_usage(policy.extra_usage);policy.extra_usage=[]
        save_progress();checkpoint(status)
        if experiment_id:
            from .user_errors import task_failure_message
            agent.store.message(experiment_id,'assistant',task_failure_message(error),'system_notice')
        agent.store.execute('UPDATE jobs SET status=?,error=?,updated=? WHERE id=?',(status,error,now(),job_id))
    finally:
        if experiment_id and not paused:
            with agent._lock:agent._active.discard(experiment_id)
