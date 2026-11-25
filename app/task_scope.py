"""Host-grounded task lineage for focused review, with full history retained."""
import json


WRITES=('submit_document','finish_document_draft','append_document_sections','edit_artifact','apply_artifact_edits','upgrade_artifact')


def job_task(agent,job_id,experiment_id=None):
    row=agent.store.one('SELECT payload,created FROM jobs WHERE id=?',(job_id,))
    if not row:return {'job_id':job_id,'initial_request':None,'latest_user_message':None,'answers':[]}
    payload=json.loads(row['payload']);eid=payload.get('experiment_id') or experiment_id
    def message(identity):
        if not identity:return None
        return agent.store.one("SELECT id,content,created FROM messages WHERE id=? AND experiment_id=? AND role='user' AND mode='model'",(identity,eid))
    latest=message(payload.get('user_message_id'))
    initial=message(payload.get('initial_user_message_id'))
    if initial is None:
        state=agent.runtime.load(job_id) or {};prompt=state.get('prompt')
        if prompt and eid:
            initial=agent.store.one("SELECT id,content,created FROM messages WHERE experiment_id=? AND role='user' AND mode='model' AND content=? AND created<=? ORDER BY created DESC LIMIT 1",(eid,prompt,row['created']))
        if initial is None and latest and latest['created']<=row['created']:initial=latest
    answers=[]
    for question in agent.store.all("SELECT id,title,questions,answer,status FROM questions WHERE job_id=? ORDER BY created",(job_id,)):
        question['questions']=json.loads(question['questions']);answers.append(question)
    return {'job_id':job_id,'activity':payload.get('activity'),'initial_request':initial,'latest_user_message':latest,'answers':answers,'references':payload.get('task_references',[]),
            'source_action':{'source_id':payload.get('source_id'),'prompt':payload.get('prompt')} if payload.get('source_id') else None}


def _saved_documents(value):
    if isinstance(value,dict):
        if value.get('kind')=='LearningDocument' and value.get('id') and isinstance(value.get('revision'),int):yield value
        for key in ('results','result'):
            if key in value:yield from _saved_documents(value[key])
    elif isinstance(value,list):
        for item in value:yield from _saved_documents(item)


def artifact_origin(agent,object_id):
    cache=getattr(agent,'_artifact_origin_tasks',None)
    if cache is None:cache={};agent._artifact_origin_tasks=cache
    if object_id in cache:return cache[object_id]
    placeholders=','.join('?' for _ in WRITES)
    rows=agent.store.all("SELECT job_id,result_blob FROM runtime_tools WHERE status='finished' AND result_blob IS NOT NULL AND name IN ("+placeholders+") ORDER BY finished,started",WRITES)
    for row in rows:
        value=agent.runtime.read_blob(row['result_blob'])
        for document in _saved_documents(value):
            if document['revision']==1:cache.setdefault(document['id'],row['job_id'])
        if object_id in cache:return cache[object_id]
    # No invented origin for offline/imported artifacts. A later real creation
    # receipt can still be discovered; absence is deliberately not cached.
    return None


def review_scope(agent,object_id,job_id,experiment_id):
    origin_job=artifact_origin(agent,object_id)
    origin=job_task(agent,origin_job,experiment_id) if origin_job else None
    current=job_task(agent,job_id,experiment_id)
    related=[];seen_tasks={task['job_id'] for task in [origin,current] if task}
    pending=[ref['job_id'] for task in [origin,current] if task for ref in task.get('references',[])]
    while pending:
        previous=pending.pop(0)
        if previous in seen_tasks:continue
        seen_tasks.add(previous);task=job_task(agent,previous,experiment_id);related.append(task)
        pending.extend(ref['job_id'] for ref in task.get('references',[]))
    messages=[];seen=set()
    for task in [origin,current]+related:
        if not task:continue
        for key in ('initial_request','latest_user_message'):
            message=task.get(key)
            if message and message['id'] not in seen:messages.append(message);seen.add(message['id'])
    return {'artifact_creation_task':origin,'current_task':current,'referenced_tasks':related,
            'relevant_user_requests':[message['content'] for message in sorted(messages,key=lambda m:(m['created'],m['id']))],
            'scope_rule':'原始文稿创建要求界定任务范围，当前真实要求补充或明确修改它；配套稿按用途分工。共享教材/混合笔记含其它实验，不构成新增要求。其它对话历史保留在程序内，不作为本次审阅的待交付清单。'}
