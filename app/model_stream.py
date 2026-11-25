"""Frame SSE events and assemble complete provider choices before any tool executes."""
import codecs
import copy
import json
import re


def event_lines(chunks):
    decoder=codecs.getincrementaldecoder('utf-8-sig')()
    parts=[];after_cr=False
    for raw in chunks:
        text=decoder.decode(raw)
        if not text:continue
        if after_cr and text.startswith('\n'):text=text[1:]
        after_cr=False;start=0
        for match in re.finditer(r'\r\n|\r|\n',text):
            parts.append(text[start:match.start()])
            yield ''.join(parts)
            parts=[];start=match.end()
            after_cr=match.group()=='\r' and start==len(text)
        if start<len(text):parts.append(text[start:])
    tail=decoder.decode(b'',final=True)
    if tail:parts.append(tail)
    if parts:yield ''.join(parts)


def sse_events(chunks,state):
    data=[];kind='message'
    for line in event_lines(chunks):
        if not line:
            if data:
                state['event_count']+=1
                yield kind,'\n'.join(data)
            data=[];kind='message'
            continue
        if line.startswith(':'):continue
        name,colon,value=line.partition(':')
        if value.startswith(' '):value=value[1:]
        if name=='data':data.append(value)
        elif name=='event':kind=value
        elif name not in ('id','retry'):
            state.setdefault('unknown_sse_fields',{})[name]=state.get('unknown_sse_fields',{}).get(name,0)+1
    state['eof']=True
    if data:
        state['unterminated_event']=True
        raise ValueError('模型流事件未以空行结束；已保留收到的字节，未执行工具')


def _text(value,label):
    if value is None:return ''
    if not isinstance(value,str):raise ValueError('模型流 '+label+' 不是字符串')
    return value


def _snapshot(previous,current,label,json_arguments=False):
    """A full snapshot may complete a prefix or add JSON siblings, never rewrite facts."""
    if not previous:return current
    if current==previous or isinstance(current,str) and isinstance(previous,str) and current.startswith(previous):return current
    if json_arguments:
        def subset(a,b):
            if isinstance(a,dict) and isinstance(b,dict):return all(k in b and subset(v,b[k]) for k,v in a.items())
            if isinstance(a,list) and isinstance(b,list):return len(a)<=len(b) and all(subset(x,y) for x,y in zip(a,b))
            return a==b
        try:
            if subset(json.loads(previous),json.loads(current)):return current
        except (ValueError,TypeError):pass
    raise ValueError('模型完整消息与已收分片冲突：'+label)


class ChatChoice:
    def __init__(self,index):
        self.index=index;self.finish=None;self.text=[];self.reasoning=[];self.refusal=[]
        self.calls={};self.extra={};self.role='assistant';self.snapshot_extra={}

    def call(self,value,index,snapshot=False):
        if not isinstance(value,dict):raise ValueError('模型工具分片结构无效')
        if snapshot and value.get('id'):
            matching=[k for k,v in self.calls.items() if v['id']==value['id']]
            if matching:index=matching[0]
        if type(index) is not int or index<0:raise ValueError('模型工具分片缺少有效索引')
        saved=self.calls.setdefault(index,{'id':'','type':'function','name':[],'arguments':[]})
        kind=value.get('type','function')
        if kind!='function':raise ValueError('模型流含未支持的工具类型：'+str(kind))
        identifier=value.get('id')
        if identifier:
            if saved['id'] and saved['id']!=identifier:raise ValueError('同一工具索引出现不同调用标识')
            saved['id']=identifier
        function=value.get('function') or {}
        if not isinstance(function,dict):raise ValueError('模型函数分片结构无效')
        for key in value:
            if key not in ('index','id','type','function') and value[key] not in (None,'',[],{}):
                raise ValueError('模型工具分片含未支持字段：'+key)
        for key in function:
            if key not in ('name','arguments') and function[key] not in (None,'',[],{}):
                raise ValueError('模型函数分片含未支持字段：'+key)
        for key in ('name','arguments'):
            if key not in function or function[key] is None:continue
            text=_text(function[key],key)
            if snapshot:saved[key]=[_snapshot(''.join(saved[key]),text,key,key=='arguments')]
            elif key=='name' and saved[key]==[text]:continue
            else:saved[key].append(text)

    def update(self,choice,state):
        delta=choice.get('delta') or {}
        if not isinstance(delta,dict):raise ValueError('模型流 delta 结构无效')
        # Compatible providers may stream the thinking summary as `reasoning`
        # instead of `reasoning_content`; it is the same channel, not a payload
        # this client cannot account for.
        if delta.get('reasoning') not in (None,'',[],{}):
            delta=dict(delta)
            if not delta.get('reasoning_content'):delta['reasoning_content']=delta['reasoning']
            delta.pop('reasoning',None)
            choice=dict(choice);choice['delta']=delta
        if self.finish is not None and any(v not in (None,'',[],{}) for v in delta.values()):
            raise ValueError('模型流在此选项结束后仍追加内容')
        known={'role','content','reasoning_content','tool_calls','refusal'}
        for key,value in delta.items():
            if key not in known and value not in (None,'',[],{}):
                state.setdefault('unsupported_payloads',[]).append('choices.delta.'+key)
                raise ValueError('模型流含未支持的内容字段：'+key)
        if delta.get('role'):self.role=delta['role']
        for key,parts in (('content',self.text),('reasoning_content',self.reasoning),('refusal',self.refusal)):
            if delta.get(key) is not None:parts.append(_text(delta[key],key))
        for position,call in enumerate(delta.get('tool_calls') or []):self.call(call,call.get('index',position))
        if choice.get('message') is not None:
            state['message_snapshots']=state.get('message_snapshots',0)+1
            message=choice['message']
            if not isinstance(message,dict):raise ValueError('模型完整消息结构无效')
            if message.get('reasoning') not in (None,'',[],{}):
                message=dict(message)
                if not message.get('reasoning_content'):message['reasoning_content']=message['reasoning']
                message.pop('reasoning',None)
                choice=dict(choice);choice['message']=message
            for key,parts in (('content',self.text),('reasoning_content',self.reasoning),('refusal',self.refusal)):
                if message.get(key) is not None:parts[:]=[_snapshot(''.join(parts),_text(message[key],key),key)]
            if message.get('role'):self.role=message['role']
            if 'tool_calls' in message:
                old_ids={v['id'] for v in self.calls.values() if v['id']}
                incoming={v.get('id') for v in (message['tool_calls'] or [])}
                if not old_ids<=incoming:raise ValueError('模型完整消息缺少先前已接收的工具调用')
                for position,call in enumerate(message['tool_calls'] or []):self.call(call,call.get('index',position),True)
            for key,value in message.items():
                if key not in known:
                    if key in ('function_call','output') and value:raise ValueError('模型完整消息含未支持的调用字段：'+key)
                    self.snapshot_extra[key]=copy.deepcopy(value)
        finish=choice.get('finish_reason')
        if finish is not None:
            if self.finish is not None and self.finish!=finish:raise ValueError('模型流给出相互冲突的结束原因')
            self.finish=finish
        self.extra.update({k:copy.deepcopy(v) for k,v in choice.items() if k not in ('delta','message','finish_reason','index')})

    def result(self):
        message={**self.snapshot_extra,'role':self.role,'content':''.join(self.text),'reasoning_content':''.join(self.reasoning)}
        if self.refusal:message['refusal']=''.join(self.refusal)
        if self.calls:message['tool_calls']=[{'id':v['id'],'type':v['type'],'function':{'name':''.join(v['name']),'arguments':''.join(v['arguments'])}}
                                           for _,v in sorted(self.calls.items())]
        return {**self.extra,'index':self.index,'message':message,'finish_reason':self.finish}


def consume_sse(response,protocol,progress,partial):
    usage=None;metadata={};choices={};final=None;last_response=None
    state=partial.setdefault('transport',{})
    state.update(protocol=protocol,event_count=0,done=False,eof=False,complete=False)
    def chat_result():return {**metadata,'choices':[v.result() for _,v in sorted(choices.items())],'usage':usage or {}}
    try:
        for name,data in sse_events(response,state):
            if data.strip()=='[DONE]':state['done']=True;break
            event=json.loads(data)
            if not isinstance(event,dict):raise ValueError('模型流事件必须是 JSON 对象')
            if event.get('usage') is not None:usage=event['usage']
            partial['usage']=usage
            if event.get('error'):raise ValueError('模型流返回错误：'+str(event['error']))
            if protocol=='responses':
                kind=event.get('type') or name
                state.setdefault('event_types',{})[kind]=state.get('event_types',{}).get(kind,0)+1
                if kind=='response.reasoning_summary_text.delta':progress('模型正在思考')
                elif kind in ('response.output_text.delta','response.function_call_arguments.delta'):progress('模型正在生成内容')
                if isinstance(event.get('response'),dict):
                    last_response=event['response'];usage=last_response.get('usage') or usage
                    partial['usage']=usage
                    if kind in ('response.completed','response.failed','response.incomplete'):final=last_response
                continue
            for field in ('message','output','delta','tool_calls','function_call','text'):
                if event.get(field):
                    state.setdefault('unsupported_payloads',[]).append(field)
                    raise ValueError('模型流含未归属到选项的内容字段：'+field)
            metadata.update({k:copy.deepcopy(v) for k,v in event.items() if k not in ('choices','usage','error')})
            for position,choice in enumerate(event.get('choices') or []):
                if not isinstance(choice,dict):raise ValueError('模型选项结构无效')
                index=choice.get('index',position)
                if type(index) is not int or index<0:raise ValueError('模型选项缺少有效索引')
                current=choices.setdefault(index,ChatChoice(index));current.update(choice,state)
                delta=choice.get('delta') or {};message=choice.get('message') or {}
                if delta.get('reasoning_content'):progress('模型正在思考')
                if delta.get('content') or message.get('content'):progress('模型正在撰写回复')
                if delta.get('tool_calls') or message.get('tool_calls'):progress('模型正在准备操作')
        if protocol=='responses':
            if final is None:raise ValueError('模型返回流中断，未取得完整结束回执；未执行其中的工具')
            if usage is not None and not final.get('usage'):final={**final,'usage':usage}
            state['completion_basis']='responses_terminal_event';state['complete']=True
            return final
        if not choices or any(c.finish is None for c in choices.values()):
            raise ValueError('模型返回流中断，部分选项未取得完整结束回执；未执行其中的工具')
        state['completion_basis']='done_and_finish' if state['done'] else 'eof_and_finish'
        if not state['done']:state.setdefault('diagnostics',[]).append('结束原因完整，但流以 EOF 而非 DONE 结束')
        state['complete']=True
        return chat_result()
    finally:
        # Materialize once, including on interruption; large arguments are not
        # rejoined/copied for every network fragment.
        partial['usage']=usage
        partial['response']=(final or last_response) if protocol=='responses' else chat_result()
