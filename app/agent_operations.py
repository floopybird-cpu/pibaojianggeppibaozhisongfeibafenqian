"""Small host operations; every formal write still uses the format gateway."""
from concurrent.futures import ThreadPoolExecutor
import json
import math

from .storage import canonical

NAMES={'discover_tools','read_batch','apply_artifact_edits','append_document_sections','inspect_dataset','read_tool_result','checkpoint_context'}


def dispatch(agent,name,args,experiment_id,allowed_sources,job_id):
    if name=='checkpoint_context':
        policy=getattr(agent._local,'policy',None);messages=getattr(agent._local,'messages',None)
        if policy is None or messages is None:raise ValueError('当前没有可整理的模型工作上下文')
        return policy.release(messages,args['result_ids'],args['note'])
    if name=='discover_tools':
        from .tool_catalog import ToolCatalog
        catalog=getattr(agent._local,'catalog',None)
        if catalog is None:catalog=ToolCatalog(agent.tool_declarations());agent._local.catalog=catalog
        return catalog.discover(args.get('query',''),args.get('groups'))
    if name=='read_batch':
        from .agent_loop import tool_output
        from .tool_catalog import READ_ONLY,ToolCatalog
        catalog=getattr(agent._local,'catalog',None) or ToolCatalog(agent.tool_declarations())
        calls=args['calls'];parent=getattr(agent._local,'call_id','batch')
        if any(c.get('name') not in READ_ONLY for c in calls):raise ValueError('批量读取只接受只读工具；保存、执行、联网和提问须单独调用')
        converted=[{'id':parent+':'+str(i),'function':{'name':c['name'],'arguments':canonical(c['arguments'])}} for i,c in enumerate(calls)]
        with ThreadPoolExecutor(max_workers=6,thread_name_prefix='physics-batch') as pool:
            pending=[pool.submit(tool_output,agent,job_id,c,experiment_id,allowed_sources,catalog) for c in converted]
            results=[{'tool':c['function']['name'],'result':f.result()[0]} for c,f in zip(converted,pending)]
        return {'results':results,'notice':'每项完整结果已独立保存，单项失败不抹掉其他读取结果。'}
    if name=='apply_artifact_edits':
        results=[]
        for edit in args['edits']:
            try:
                obj=agent.gateway.read(edit['id'])
                if obj['kind']=='DataWorkbook':raise ValueError('规范测量表仍须通过真实问答更正接口')
                if obj['experiment_id'] is not None and obj['experiment_id']!=experiment_id:raise ValueError('不能修改其他实验成果')
                if obj['revision']!=edit['base_revision']:raise ValueError('成果基线已变化，请先读取实际修订')
                if args.get('upgrade_format',False):
                    plan=agent.gateway.plan_upgrade(obj['id'])
                    agent.runtime.event(job_id,'batch_upgrade_planned',plan)
                    if plan['can_upgrade']:
                        if obj['human_baseline']:raise ValueError('这份成果含人工修改，请先用版本预览并明确保留人工内容后升级；批量修改不会代替该选择')
                        obj=agent.gateway.upgrade(obj['id'],plan)
                obj=agent.gateway.edit(obj['id'],obj['revision'],edit['operations'])
                result={k:obj[k] for k in ('id','title','kind','revision','publication','layout') if k in obj};result['committed']=True
                agent.runtime.event(job_id,'batch_edit_committed',result)
            except Exception as exc:
                current=agent.store.one('SELECT revision FROM objects WHERE id=?',(edit.get('id'),))
                result={'id':edit.get('id'),'error':agent.settings.redact(str(exc)),'current_revision':current['revision'] if current else None,
                        'notice':'先核对当前修订；前面已经完成的独立修改保持有效。'}
            results.append(result)
        return {'results':results,'all_succeeded':all(r.get('committed') for r in results)}
    if name=='append_document_sections':
        if not experiment_id:raise ValueError('文稿必须属于当前实验')
        draft=agent.drafts.read(experiment_id,args['id'])
        if draft['revision']!=args['base_revision']:raise ValueError('草稿基线已变化')
        for item in args['sections']:
            draft=agent.drafts.append(experiment_id,draft['id'],draft['revision'],item['section'],item.get('appendix',False),item.get('replace',False))
            agent.runtime.event(job_id,'draft_section_committed',{'id':draft['id'],'revision':draft['revision'],'section':item['section']['id']})
        return agent.drafts.finish(experiment_id,draft['id'],draft['revision']) if args.get('finish') else draft
    if name=='inspect_dataset':
        dataset=next((d for d in agent.analysis.datasets(experiment_id) if d['id']==args['id']),None)
        if not dataset:raise ValueError('数据不属于当前实验')
        statistics={}
        for column in dataset['mapping']:
            low=high=None;finite=missing=non_numeric=non_finite=0
            for row in dataset['rows']:
                value=row.get(column)
                if value is None or str(value).strip()=='':missing+=1;continue
                try:number=float(value)
                except (ValueError,TypeError):non_numeric+=1;continue
                if not math.isfinite(number):non_finite+=1;continue
                finite+=1;low=number if low is None else min(low,number);high=number if high is None else max(high,number)
            statistics[column]={'populated_count':len(dataset['rows'])-missing,'used_for_analysis':dataset['mapping'][column].get('use_for_analysis',True),'finite_count':finite,'missing_count':missing,'non_numeric_count':non_numeric,'non_finite_count':non_finite,'min':low,'max':high}
        return {k:v for k,v in dataset.items() if k!='rows'}|{'row_count':len(dataset['rows']),'statistics':statistics,
               'coverage':'统计遍历全部行；populated_count是有值行，non_numeric_count是非数值文字，不等于缺失；used_for_analysis=false只表示本次不用。逐行内容可read_dataset核对，实际计算输入保持全量。'}
    if name=='read_tool_result':
        from .context_policy import read_runtime_result
        # Validate before combining a legacy relative path.
        import re
        identity=args['receipt_id']
        if not isinstance(identity,str) or not re.fullmatch('[a-f0-9]{64}',identity):raise ValueError('无效读取记录')
        old=agent.store.authority/'tool-results'/job_id/(identity+'.json')
        if old.is_file():
            from .model_context import read_receipt
            return read_receipt(agent.store.authority,job_id,identity,args.get('offset',0),args.get('count'))
        return read_runtime_result(agent.runtime,job_id,identity,args.get('path',''),args.get('offset',0),args.get('count'),args.get('row_offset'),args.get('row_count'))
    raise ValueError('未支持的组合操作')
