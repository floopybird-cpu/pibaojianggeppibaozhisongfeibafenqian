"""Material-linked questions and real replies, scoped to records/experiment.

No yes/no inference lives here. A reply may accept, modify or reject the
proposed background; the Agent must read it before adopting any defaults.
"""
import json


def validate_basis(store, value, experiment_id=None):
    if not isinstance(value,dict) or set(value)-{'sources','record_source_ids'}:
        raise ValueError('背景依据须给实际讲义锚点 sources 和适用原始记录 record_source_ids')
    anchors=value.get('sources',[]);records=value.get('record_source_ids',[])
    if not isinstance(anchors,list) or not anchors or any(not isinstance(x,str) for x in anchors):
        raise ValueError('先读取讲义，以真实锚点说明本次要确认的背景')
    if not isinstance(records,list) or any(not isinstance(x,str) for x in records):raise ValueError('适用记录须为已导入来源ID列表')
    if not experiment_id and not records:raise ValueError('全局背景核对须绑定适用的原始记录')
    for identity in anchors:store.anchor(identity)
    for identity in records:
        if not store.one('SELECT id FROM sources WHERE id=?',(identity,)):raise ValueError('背景适用记录尚未导入')
    return {'sources':list(dict.fromkeys(anchors)),'record_source_ids':list(dict.fromkeys(records))}


def background_records(store, source_ids=(), experiment_id=None, *, include_other_experiments=False):
    selected=set(source_ids)
    if not store.one("SELECT name FROM sqlite_master WHERE type='table' AND name='questions'"):return []
    rows=store.all("SELECT id,experiment_id,questions,answer,status,context,updated FROM questions "
                   "WHERE status IN ('pending','answered') AND EXISTS "
                   "(SELECT 1 FROM json_each(questions) item WHERE json_type(item.value,'$.material_basis')='object') ORDER BY created,id")
    result=[]
    for row in rows:
        items=json.loads(row['questions']);saved=json.loads(row['context'] or '{}')
        assigned=saved.get('submitted_item_answers',{})
        for item in items:
            basis=item.get('material_basis')
            if not basis:continue
            same_experiment=bool(experiment_id and row['experiment_id']==experiment_id)
            same_records=bool(selected.intersection(basis.get('record_source_ids',[])))
            if not same_experiment and not (same_records and (row['experiment_id'] is None or include_other_experiments)):continue
            answer=assigned.get(item['id'])
            if answer is None and row['status']=='answered' and len(items)==1:answer=row['answer']
            references=[]
            for identity in basis['sources']:
                anchor=store.anchor(identity)
                source=store.one('SELECT name FROM sources WHERE id=?',(anchor['source_id'],))
                references.append({'anchor_id':identity,'source_id':anchor['source_id'],'name':source['name'],
                                   'page':anchor['page'],'sha256':anchor['source_sha']})
            result.append({'question_id':row['id'],'item_id':item['id'],'question':item['question'],
                           'answer':answer,'status':row['status'],'basis_anchors':basis['sources'],
                           'record_source_ids':basis.get('record_source_ids',[]),'references':references,
                           'experiment_id':row['experiment_id'],'updated':row['updated'],
                           'origin':'实际背景核对及用户原答；可能接受、修正或否定，须阅读答复和差异；不是新的操作授权。'})
    return result
