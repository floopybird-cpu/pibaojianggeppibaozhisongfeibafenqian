"""Recover unpublished content by its journal receipt and verify requested outputs."""
import copy
from collections import Counter


def has_failed_publication(result):
    return any(p.get('status')=='failed' for p in result.get('publications',[]))


class CandidateChangeError(ValueError):
    def __init__(self,message,parent=None):
        super().__init__(message)
        self.parent=parent


def pointer_parts(path):
    if path=='':return []
    if not isinstance(path,str) or not path.startswith('/'):raise ValueError('须给出 JSON Pointer')
    return [part.replace('~1','/').replace('~0','~') for part in path[1:].split('/')]


def member(container,key):
    if isinstance(container,list):
        if not key.isascii() or not key.isdecimal() or (len(key)>1 and key.startswith('0')):
            raise ValueError('无效的数组下标')
        return int(key)
    if not isinstance(container,dict):raise ValueError('上一级字段不是对象或数组')
    return key


def apply_candidate_changes(content,changes,parent):
    for change in changes:
        path=change.get('path','')
        try:
            keys=pointer_parts(path)
            if not keys or path=='/':raise ValueError('须指定要修改的字段')
            target=content
            for key in keys[:-1]:target=target[member(target,key)]
            op=change['op']
            if op=='set' and keys[-1]=='-' and isinstance(target,list):
                target.append(copy.deepcopy(change['value']))
            elif op=='set':target[member(target,keys[-1])]=copy.deepcopy(change['value'])
            elif op=='remove':del target[member(target,keys[-1])]
            else:raise ValueError('未知字段操作')
        except (KeyError,TypeError,IndexError,ValueError) as exc:
            raise CandidateChangeError(f'这批字段修改未应用，位置 {path!r}：{exc}',parent) from exc


def candidate_content(agent,job_id,call_id,changes=()):
    trail=[];seen=set()
    while True:
        if call_id in seen:raise CandidateChangeError('候选记录未能回溯至已提交的原稿')
        seen.add(call_id)
        row=agent.store.one("SELECT name,status,args_blob FROM runtime_tools WHERE job_id=? AND call_id=?",(job_id,call_id))
        if not row or row["status"]!="failed" or row["name"] not in ("submit_document","correct_document_candidate"):
            raise CandidateChangeError("只能接续本任务已失败且保留的文稿候选")
        args=agent.runtime.read_blob(row["args_blob"])
        if row["name"]=="submit_document":
            if not isinstance(args.get('content'),dict):raise CandidateChangeError('这次提交没有可读取的内容对象')
            break
        if not isinstance(args.get('candidate'),str) or not isinstance(args.get('changes'),list):
            raise CandidateChangeError('这次调用没有完整的候选或修改字段')
        trail.append((call_id,args['changes']));call_id=args['candidate']
    content=copy.deepcopy(args["content"])
    for child,group in reversed(trail):
        apply_candidate_changes(content,group,call_id);call_id=child
    apply_candidate_changes(content,changes,call_id)
    return args,content


def unfinished_candidate(agent,job_id):
    for row in agent.store.all("SELECT status,result_blob FROM runtime_tools WHERE job_id=? AND name IN ('submit_document','correct_document_candidate','finish_document_draft') AND result_blob IS NOT NULL ORDER BY finished DESC,rowid DESC",(job_id,)):
        if row['status']=='finished':return None
        error=agent.runtime.read_blob(row['result_blob'])
        if error.get('candidate'):
            return {k:error[k] for k in ('candidate','error','issues') if k in error}
    return None


def read_candidate(agent,job_id,identity,path):
    _,body=candidate_content(agent,job_id,identity)
    if path is None:
        return {'candidate':identity,'title':body.get('title'),'fields':list(body),
                'sections':[{'path':f'/{key}/{i}','id':s.get('id'),'title':s.get('title')} for key in ('sections','appendices') for i,s in enumerate(body.get(key,[]))]}
    value=body
    for key in pointer_parts(path):value=value[member(value,key)]
    return {'candidate':identity,'path':path,'value':value}


def patch_candidate(agent,job_id,experiment_id,call_id,changes):
    args,content=candidate_content(agent,job_id,call_id,changes)
    return agent.gateway.apply("LearningDocument",content,experiment_id=experiment_id,object_id=args.get("id"),base_revision=args.get("base_revision",0))


def missing_deliverables(agent,experiment_id,job_id,completed,activity):
    if not experiment_id:return []
    row=agent.store.one("SELECT draft_id,revision FROM experiment_draft_tasks WHERE job_id=? AND experiment_id=?",(job_id,experiment_id))
    expected=Counter()
    if row:
        from .tool_catalog import requests_lab_book
        outline=agent.gateway.read(row['draft_id'],row['revision'])['content']
        expected.update(outline.get('deliverables',[]))
        request=outline.get('request',{})
        if activity=='chat' and not request.get('answers') and requests_lab_book(request.get('initial','')):
            expected['plan']=max(1,expected['plan'])
    if activity in ('short','deep','prestudy'):expected['prestudy']=max(1,expected['prestudy'])
    elif activity=='plan':expected['plan']=max(1,expected['plan'])
    available=Counter()
    ids=dict.fromkeys(i for item in completed if item.get("success") for i in item.get("saved_artifact_ids",[]))
    for identity in ids:
        obj=agent.gateway.read(identity)
        if obj["kind"]=="LearningDocument":
            files={a['name'] for a in obj['assets'] if agent.store.asset_path(a['path']).is_file()}
            if {'document.pdf','document.docx'}<=files:available[obj["content"]["doc_type"]]+=1
        elif obj["kind"] in ("AnalysisProgram","AnalysisResult","DataWorkbook"):
            available[{"AnalysisProgram":"program","AnalysisResult":"analysis_result","DataWorkbook":"workbook"}[obj["kind"]]]+=1
    return sorted((expected-available).elements())


def publication_targets(agent,job_id,completed,prior=None):
    """Pin the revisions actually saved by this task, including nested edit results."""
    versions={}
    for item in completed:
        if not item.get('success'):continue
        ids=item.get('saved_artifact_ids',[])
        if not ids:continue
        known={o['id']:o['revision'] for o in [item,*item.get('generated_documents',[])] if o.get('id') and o.get('revision')}
        if set(ids)-known.keys() and item.get('call_id'):
            row=agent.store.one('SELECT result_blob FROM runtime_tools WHERE job_id=? AND call_id=?',(job_id,item['call_id']))
            if row and row['result_blob']:
                result=agent.runtime.read_blob(row['result_blob'])
                if isinstance(result,dict):
                    known.update({o['id']:o['revision'] for o in [result,*result.get('results',[])] if isinstance(o,dict) and o.get('id') and o.get('revision')})
        for rid in ids:versions[rid]=known.get(rid)
    if prior is not None:
        versions={p['id']:p.get('revision') or versions.get(p['id']) for p in prior}
    targets=[]
    for rid,rev in versions.items():
        if not rev:
            obj=agent.store.one('SELECT revision FROM objects WHERE id=?',(rid,))
            if not obj or (prior is not None and obj['revision']!=1):
                raise ValueError('缺少原交付的固定修订，请从成果版本列表选择该次版本重新导出')
            rev=obj['revision']
        targets.append({'id':rid,'revision':rev})
    return targets


def restore_saved_outputs(agent,job_id,completed):
    """A write may commit between the reply checkpoint and the batch checkpoint."""
    known={i.get('call_id') for i in completed}
    seen={identity for item in completed for identity in item.get('saved_artifact_ids',[])}
    for row in agent.store.all("SELECT * FROM runtime_tools WHERE job_id=? AND status='finished' ORDER BY finished",(job_id,)):
        name=row['name']
        if row['call_id'] in known or name not in ('submit_experiment_draft','submit_document','correct_document_candidate','finish_document_draft','append_document_sections','edit_artifact','apply_artifact_edits','submit_program','run_program','submit_data_workbook','resolve_data_issue','resolve_data_issues','upgrade_artifact'):continue
        result=agent.runtime.read_blob(row['result_blob'])
        if not isinstance(result,dict):continue
        ids=[o['id'] for o in [result,*result.get('results',[])] if isinstance(o,dict) and o.get('kind') in
             ('ExperimentDraft','LearningDocument','AnalysisProgram','AnalysisResult','DataWorkbook') and o.get('id')]
        if name=='run_program' and result.get('artifact_id'):ids.append(result['artifact_id'])
        if ids and (not set(ids)<=seen or not any(i.get('tool')==name for i in completed)):
            completed.append({'tool':name,'call_id':row['call_id'],'success':True,'saved_artifact_ids':ids,'recovered':True})
            seen.update(ids)
