"""Durable facts, request accounting and checkpoints; no model/provider policy.

Raw messages/results are immutable blobs. A context projection never edits them.
"""
from __future__ import annotations

import json
import hashlib
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .storage import canonical, digest, now, uid


SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT,event TEXT NOT NULL,
 blob TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runtime_checkpoints(
 job_id TEXT PRIMARY KEY,state TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_requests(
 id TEXT PRIMARY KEY,job_id TEXT,purpose TEXT NOT NULL,model TEXT NOT NULL,
 provider TEXT NOT NULL,thinking_depth TEXT,status TEXT NOT NULL,started TEXT NOT NULL,
 finished TEXT,elapsed_ms INTEGER,phase TEXT,usage TEXT,request_id TEXT,
 error TEXT,response_blob TEXT);
CREATE TABLE IF NOT EXISTS runtime_tools(
 job_id TEXT NOT NULL,call_id TEXT NOT NULL,name TEXT NOT NULL,args_blob TEXT NOT NULL,
 status TEXT NOT NULL,result_blob TEXT,started TEXT NOT NULL,finished TEXT,
 PRIMARY KEY(job_id,call_id));
"""


def normalized_usage(raw):
    if not isinstance(raw, dict) or not raw:
        return None
    # Fixture receipts are never converted into real provider consumption.
    if not any(k in raw for k in ('prompt_tokens','input_tokens','completion_tokens','output_tokens')):
        return None
    input_tokens=int(raw.get('prompt_tokens',raw.get('input_tokens',0)))
    output_tokens=int(raw.get('completion_tokens',raw.get('output_tokens',0)))
    hit=int(raw.get('prompt_cache_hit_tokens',raw.get('prompt_tokens_details',raw.get('input_tokens_details',{})).get('cached_tokens',0)))
    return {'input_tokens':input_tokens,'output_tokens':output_tokens,
            'cache_hit_tokens':hit,'cache_miss_tokens':int(raw.get('prompt_cache_miss_tokens',input_tokens-hit)),
            'reasoning_tokens':int(raw.get('completion_tokens_details',raw.get('output_tokens_details',{})).get('reasoning_tokens',0))}


class SessionRuntime:
    def __init__(self,store):
        self.store=store
        self.blobs=store.authority/'runtime-blobs';self.blobs.mkdir(exist_ok=True)
        with store.connect() as conn:
            conn.executescript(SCHEMA)
            columns={r[1] for r in conn.execute('PRAGMA table_info(model_requests)')}
            for column in ('request_hash','raw_response_blob'):
                if column not in columns:conn.execute('ALTER TABLE model_requests ADD COLUMN '+column+' TEXT')
            conn.execute("UPDATE model_requests SET status='unknown',finished=?,phase='进程中断，用量待核对',error='请求开始后进程中断，没有取得完整服务商回执；不得按零计费' WHERE status='started' AND (job_id IS NULL OR job_id IN (SELECT id FROM jobs WHERE status='interrupted'))",(now(),))
        # A killed process cannot run its finally block. Recover only requests
        # already marked interrupted, never a live writer from another task.
        for row in self.store.all("SELECT id,job_id,raw_response_blob FROM model_requests WHERE status='unknown' AND raw_response_blob IS NOT NULL"):
            metadata=self.read_blob(row['raw_response_blob'])
            if metadata.get('state')=='receiving':ResponseBody.recover(self,row,metadata)

    def blob(self,value):
        raw=canonical(value).encode('utf-8');identity=digest(raw)
        path=self.blobs/(identity+'.json')
        # Publishing the same value must not replace an already published blob:
        # on Windows another worker may already have that immutable file open.
        # Share the Store's lock across runtime instances using this Store.
        with self.store.lock:
            if not path.exists():
                temporary=self.blobs/(identity+'.'+uid('tmp'))
                temporary.write_bytes(raw)
                try:temporary.replace(path)
                finally:temporary.unlink(missing_ok=True)
        return identity

    def read_blob(self,identity):
        import re
        if not isinstance(identity,str) or not re.fullmatch('[a-f0-9]{64}',identity):raise ValueError('无效记录定位')
        raw=(self.blobs/(identity+'.json')).read_bytes()
        if digest(raw)!=identity:raise ValueError('完整记录校验失败')
        return json.loads(raw)

    def response_body(self,request_id,job_id,protocol):
        return ResponseBody(self,request_id,job_id,protocol)

    def event(self,job_id,event,value):
        identity=self.blob(value)
        self.store.execute('INSERT INTO runtime_events(job_id,event,blob,created) VALUES(?,?,?,?)',(job_id,event,identity,now()))
        return identity

    def checkpoint(self,job_id,state):
        # Pixels already have an immutable event. SQLite stores its address.
        state=dict(state)
        if 'messages' in state:
            state['messages']=[({'_image_event':m['_context_image_record'],'_context_image_results':m.get('_context_image_results',[])}
                  if m.get('_context_image_record') and isinstance(m.get('content'),list) else m) for m in state['messages']]
        self.store.execute('INSERT INTO runtime_checkpoints VALUES(?,?,?) ON CONFLICT(job_id) DO UPDATE SET state=excluded.state,updated=excluded.updated',
                           (job_id,canonical(state),now()))

    def load(self,job_id):
        row=self.store.one('SELECT state FROM runtime_checkpoints WHERE job_id=?',(job_id,))
        if not row:return None
        state=json.loads(row['state'])
        for i,m in enumerate(state.get('messages',[])):
            if '_image_event' in m:
                raw=self.read_blob(m['_image_event'])
                state['messages'][i]={**raw,'_context_image_record':m['_image_event'],'_context_image_results':m.get('_context_image_results',[])}
        return state

    def tool_start(self,job_id,call_id,name,args):
        old=self.store.one('SELECT * FROM runtime_tools WHERE job_id=? AND call_id=?',(job_id,call_id))
        identity=self.blob(args)
        if old:
            if old['args_blob']!=identity or old['name']!=name:raise ValueError('已记录工具调用的内容发生变化')
            return old
        self.store.execute('INSERT INTO runtime_tools VALUES(?,?,?,?,?,?,?,?)',(job_id,call_id,name,identity,'started',None,now(),None))
        # INSERT above deliberately names a fixed schema; result persistence is
        # independent for every sibling, even when a parallel batch is interrupted.
        return None

    def tool_finish(self,job_id,call_id,result,success=True):
        result_id=self.blob(result)
        previous=self.store.one('SELECT result_blob FROM runtime_tools WHERE job_id=? AND call_id=?',(job_id,call_id))
        if previous and previous['result_blob'] and previous['result_blob']!=result_id:raise ValueError('同一工具调用的已保存结果不能被覆盖')
        self.event(job_id,'tool_result',{'call_id':call_id,'result':result,'success':success})
        self.store.execute('UPDATE runtime_tools SET status=?,result_blob=?,finished=? WHERE job_id=? AND call_id=?',
                           ('finished' if success else 'failed',result_id,now(),job_id,call_id))
        return result_id

    def tool_result(self,job_id,call_id):
        row=self.store.one('SELECT * FROM runtime_tools WHERE job_id=? AND call_id=?',(job_id,call_id))
        if row and row['result_blob']:return self.read_blob(row['result_blob'])
        return None

    def repair_pending(self,job_id,messages):
        """Close protocol pairs from durable facts; never re-execute a write."""
        result=[];i=0
        while i<len(messages):
            message=messages[i];result.append(message);i+=1
            if message.get('role')!='assistant' or not message.get('tool_calls'):continue
            available={}
            while i<len(messages) and messages[i].get('role')=='tool':
                available[messages[i]['tool_call_id']]=messages[i];i+=1
            for call in message['tool_calls']:
                cid=call['id']
                if cid in available:result.append(available[cid]);continue
                saved=self.store.one('SELECT status,result_blob FROM runtime_tools WHERE job_id=? AND call_id=?',(job_id,cid))
                if saved and saved['result_blob']:
                    value=self.read_blob(saved['result_blob'])
                else:
                    value={'execution_status':'outcome_unknown' if saved else 'not_started',
                           'notice':'没有持久化结果。只读操作可重试；保存、发布或执行可能已经发生，请先核对现有修订和运行，不要盲目重复。'}
                result.append({'role':'tool','tool_call_id':cid,'content':canonical(value)})
        return result

    def request_start(self,job_id,purpose,config,request_hash=None):
        identity=uid('request')
        self.store.execute('INSERT INTO model_requests(id,job_id,purpose,model,provider,thinking_depth,status,started,phase) VALUES(?,?,?,?,?,?,?,?,?)',
                           (identity,job_id,purpose,config['model'],config.get('base_url',''),config.get('thinking_depth'),'started',now(),'连接模型'))
        if request_hash:self.store.execute('UPDATE model_requests SET request_hash=? WHERE id=?',(request_hash,identity))
        return identity

    def recovered_response(self,job_id,purpose,request_hash):
        if not job_id or purpose=='search':return None
        row=self.store.one("SELECT id,response_blob FROM model_requests WHERE job_id=? AND purpose=? AND request_hash=? AND status='returned' ORDER BY started DESC LIMIT 1",(job_id,purpose,request_hash))
        if not row or not row['response_blob']:return None
        response=self.read_blob(row['response_blob'])
        if response.get('error') or response.get('status') in ('failed','incomplete'):return None
        if response.get('choices') and response['choices'][0].get('finish_reason')=='length':return None
        self.event(job_id,'recovered_model_response',{'request_hash':request_hash,'purpose':purpose,'billed_again':False})
        response['_runtime_request_id']=row['id']
        return response

    def request_progress(self,identity,phase):
        self.store.execute('UPDATE model_requests SET phase=? WHERE id=?',(phase,identity))

    def request_end(self,identity,status,elapsed,response=None,usage=None,error=None,request_id=None):
        blob=self.blob(response) if response is not None else None
        self.store.execute('UPDATE model_requests SET status=?,finished=?,elapsed_ms=?,phase=?,usage=?,error=?,request_id=?,response_blob=? WHERE id=?',
                           (status,now(),round(elapsed*1000),'已返回' if status=='returned' else '请求未完成',canonical(usage) if usage is not None else None,error,request_id,blob,identity))

    def summary(self,job_id=None,experiment_id=None):
        condition='';params=()
        if job_id:condition=' WHERE m.job_id=?';params=(job_id,)
        elif experiment_id:
            condition=" WHERE m.job_id IN (SELECT id FROM jobs WHERE json_extract(payload,'$.experiment_id')=?)";params=(experiment_id,)
        rows=self.store.all('SELECT m.* FROM model_requests m'+condition+' ORDER BY started',params)
        totals=dict.fromkeys(('input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens','reasoning_tokens'),0)
        known=0;searches=0
        for row in rows:
            if row['purpose']=='search':
                searches+=int(row['status']=='returned');continue
            values=normalized_usage(json.loads(row['usage'])) if row['usage'] else None
            if values:
                known+=1
                for key,value in values.items():totals[key]+=value
        model_rows=[r for r in rows if r['purpose']!='search']
        return {**totals,'model_requests':len(model_rows),'returned_requests':sum(r['status']=='returned' for r in model_rows),
                'known_usage_requests':known,'unknown_usage_requests':sum(r['status'] not in ('started','streaming') and normalized_usage(json.loads(r['usage']) if r['usage'] else None) is None for r in model_rows),
                'search_requests':searches,'context_requests':sum(r['purpose']=='context_summary' for r in rows),
                'elapsed_ms':sum(r['elapsed_ms'] or 0 for r in rows),
                'phase':next((r['phase'] for r in reversed(rows) if r['status']=='started'),None),
                'cost':'以服务商账单为准；未拿到用量的请求标记未知，不按零计费'}

    def requests(self,job_id=None,*,experiment_id=None):
        criteria,params="",()
        if job_id:criteria,params=" WHERE job_id=?",(job_id,)
        elif experiment_id:criteria,params=" WHERE job_id IN (SELECT id FROM jobs WHERE json_extract(payload,'$.experiment_id')=?)",(experiment_id,)
        rows=self.store.all('SELECT id,job_id,purpose,model,thinking_depth,status,started,finished,elapsed_ms,phase,usage,error,request_id,raw_response_blob FROM model_requests'+criteria+' ORDER BY started',params)
        for row in rows:
            row['usage']=json.loads(row['usage']) if row['usage'] else None
        return rows


class ResponseBody:
    """Append received bytes once, then publish a separate content-addressed binary blob."""
    def __init__(self,runtime,request_id,job_id,protocol):
        self.runtime=runtime;self.request_id=request_id;self.job_id=job_id
        self.path=runtime.blobs/(request_id+'.response.partial')
        self.file=self.path.open('xb',buffering=0)
        self.hash=hashlib.sha256();self.size=0;self.closed=False
        self.metadata={'request_id':request_id,'protocol':protocol,'state':'receiving',
                       'body_file':self.path.name,'body_eof':False}
        self.publish('provider_response_started')

    def publish(self,event):
        blob=self.runtime.event(self.job_id,event,self.metadata)
        self.runtime.store.execute('UPDATE model_requests SET raw_response_blob=? WHERE id=?',(blob,self.request_id))

    def headers(self,response):
        # Only an explicit allowlist from the RESPONSE. Never request/auth headers.
        headers=response.headers or {}
        self.metadata.update(content_type=headers.get('Content-Type',''),
                             provider_request_id=headers.get('x-request-id') or headers.get('request-id'),
                             http_status=getattr(response,'status',None) or getattr(response,'code',None))
        self.publish('provider_response_headers')

    @classmethod
    def recover(cls,runtime,row,metadata):
        expected=row['id']+'.response.partial'
        if metadata.get('body_file')!=expected:return
        path=runtime.blobs/expected
        if not path.is_file():return
        record=cls.__new__(cls)
        record.runtime=runtime;record.request_id=row['id'];record.job_id=row['job_id']
        record.path=path;record.file=None;record.metadata=metadata
        record.hash=hashlib.sha256();record.size=0;record.closed=False
        with path.open('rb') as stream:
            for data in iter(lambda:stream.read(65536),b''):
                record.hash.update(data);record.size+=len(data)
        record.finish('process_interrupted',{'complete':False,'completion_basis':'process_interrupted'})

    def write(self,data):
        remaining=memoryview(data)
        while remaining:
            count=self.file.write(remaining)
            if not count:raise OSError('原始响应存储未能写入')
            self.hash.update(remaining[:count]);self.size+=count;remaining=remaining[count:]

    def finish(self,state,transport=None):
        if self.closed:return
        if self.file:self.file.close()
        self.closed=True
        identity=self.hash.hexdigest();target=self.runtime.blobs/(identity+'.bin')
        with self.runtime.store.lock:
            if target.exists():self.path.unlink()
            else:self.path.replace(target)
        self.metadata.update(state=state,body_file=target.name,body_sha256=identity,byte_count=self.size,
                             transport=transport or {})
        self.publish('provider_response')
