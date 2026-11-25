from __future__ import annotations

from html.parser import HTMLParser
from http.client import IncompleteRead
import ipaddress
import json
import copy
import re
import socket
import ssl
import time
import threading
from contextlib import contextmanager
from urllib import request, parse, error
import xml.etree.ElementTree as ET


def public_url(url):
    parsed = parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("公开资料仅接受 HTTPS 网址，不允许凭据或自定义端口")
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("公开网页工具不能访问本机、私网或保留地址")
    return url


class SafeRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_public(url, max_bytes=None):
    public_url(url)
    opener = request.build_opener(SafeRedirect())
    with opener.open(request.Request(url, headers={"User-Agent": "PhysicsHarness/0.2 (educational source reader)"}), timeout=25) as response:
        public_url(response.url)
        data = response.read() if max_bytes is None else response.read(max_bytes + 1)
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError("网页过大；请在资料库导入授权 PDF")
        return data, response.url, response.headers.get("Content-Type", ""), response.headers.get_content_charset() or "utf-8"


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip = 0; self.parts=[]
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "noscript"):
            self.skip += 1
        if tag in ("p", "div", "h1", "h2", "h3", "li", "br","tr"):
            self.parts.append("\n")
        if tag in ("td","th"):self.parts.append("  ")
    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "noscript"):
            self.skip = max(0, self.skip-1)
    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def webpage(url):
    data, final_url, content_type, encoding = fetch_public(url)
    if "html" not in content_type and "text/" not in content_type:
        raise ValueError("该地址不是可读网页；PDF 请通过资料库导入")
    decoded = data.decode(encoding, errors="replace")
    parser = TextHTML(); parser.feed(decoded)
    text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
    return data, text, final_url


def web_search(query):
    data, _, _, _ = fetch_public("https://www.bing.com/search?format=rss&q=" + parse.quote(query))
    root = ET.fromstring(data)
    return [{"title": n.findtext("title"), "url": n.findtext("link"), "snippet": n.findtext("description"),
             "warning": "检索摘要不是证据；须打开原网页后引用"} for n in root.findall(".//item")]


def received_chunks(response,capture=None,progress=None):
    """Tee bytes before decoding. read1 avoids waiting for a whole buffer on a live stream."""
    read=getattr(response,'read1',None) or response.read
    while True:
        try:data=read(65536)
        except IncompleteRead as err:
            if capture and err.partial:capture.write(err.partial)
            # Let the parser consume complete events in the bytes already returned
            # by the transport before reporting the interrupted read.
            if err.partial:yield err.partial
            raise
        if not data:
            remaining=getattr(response,'length',None)
            if remaining:raise IncompleteRead(b'',remaining)
            if capture:capture.metadata['body_eof']=True
            return
        if capture:capture.write(data)
        yield data
        if progress:progress('模型正在接收回复')


class ProviderHTTPError(ValueError):
    def __init__(self,status,detail):
        self.status=status;self.detail=detail
        super().__init__(f"模型 HTTP {status}: "+detail)


class ModelClient:
    provider = "live_api"
    def __init__(self, settings, runtime=None):
        self.settings = settings
        self.runtime=runtime
        self._local=threading.local()

    @contextmanager
    def scope(self,runtime,job_id=None):
        previous=getattr(self._local,'scope',None)
        self._local.scope={'runtime':runtime,'job_id':job_id}
        try:yield
        finally:self._local.scope=previous

    def post(self,path,body,base_url=None):
        config = self.settings.read()
        if base_url:
            if parse.urlsplit(base_url).netloc!=parse.urlsplit(config["base_url"]).netloc:
                raise ValueError("模型请求地址须属于已配置的服务商")
            config={**config,"base_url":base_url}
        if not config["configured"]:
            raise ValueError("尚未配置模型；请在设置填写兼容 Chat Completions 的 HTTPS 地址、模型名称与自己的 API 密钥。离线演示不会伪装成模型回答。")
        key = self.settings.key()
        req = request.Request(config["base_url"] + path, data=json.dumps(body, ensure_ascii=False).encode(),
                              headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        scope=getattr(self._local,'scope',None) or {'runtime':self.runtime,'job_id':None}
        runtime=scope.get('runtime');job_id=scope.get('job_id')
        purpose='search' if path.endswith('/tools/search') else getattr(self._local,'purpose','model')
        config={**config,'thinking_depth':getattr(self._local,'thinking_depth',config.get('thinking_depth'))}
        from .storage import digest,canonical
        request_hash=digest(canonical({'url':config['base_url']+path,'body':body}))
        recovered=runtime.recovered_response(job_id,purpose,request_hash) if runtime else None
        if recovered is not None:return recovered
        def cancelled():
            return bool(runtime and job_id and runtime.store.one('SELECT cancel FROM jobs WHERE id=?',(job_id,))['cancel'])
        # Each attempt is accounted independently. Credentials and headers never
        # enter the ledger. A lost response has unknown usage, not zero usage.
        for attempt in range(3):
            if cancelled():raise InterruptedError('用户取消了当前模型返回')
            identity=runtime.request_start(job_id,purpose,config,request_hash) if runtime else None
            if runtime:
                # Exact wire inputs are reconstructable from immutable parts.
                # Stable messages/schemas share blobs instead of being copied
                # wholesale on every round. Authorization headers are excluded.
                items=body.get('messages',body.get('input',[]))
                runtime.event(job_id,'provider_request',{'request_id':identity,'purpose':purpose,'endpoint':path,
                    'parameters':{k:v for k,v in body.items() if k not in ('messages','input','tools')},
                    'messages':[runtime.blob(m) for m in items] if isinstance(items,list) else [runtime.blob(items)],
                    'tools':runtime.blob(body.get('tools',[])),'request_hash':request_hash})
            protocol='responses' if path=='/responses' else 'search' if path.endswith('/tools/search') else 'chat'
            capture=runtime.response_body(identity,job_id,protocol) if runtime else None
            start=time.monotonic();partial={};last_progress=[0,''];provider_id=None;result=None
            def progress(phase):
                moment=time.monotonic()
                if cancelled():raise InterruptedError('用户取消了当前模型返回')
                if runtime and (phase!=last_progress[1] or moment-last_progress[0]>.75):
                    runtime.request_progress(identity,phase);last_progress[:]=[moment,phase]
            def finish(status,reason=None,result=None,state=None):
                if result is not None:
                    partial['response']=result;partial['usage']=result.get('usage') or partial.get('usage')
                if capture:capture.finish(state or status,partial.get('transport'))
                if runtime:
                    response_value=partial.get('response')
                    runtime.request_end(identity,status,time.monotonic()-start,response_value,partial.get('usage'),reason,
                                        request_id=(response_value or {}).get('id') or provider_id)
            try:
                with request.urlopen(req, timeout=config["timeout"] or None) as response:
                    provider_id=response.headers.get('x-request-id') or response.headers.get('request-id')
                    if capture:capture.headers(response)
                    chunks=received_chunks(response,capture,progress)
                    if body.get('stream') and 'text/event-stream' in response.headers.get('Content-Type',''):
                        from .model_stream import consume_sse
                        result=consume_sse(chunks,protocol,progress,partial)
                    else:
                        raw=b''.join(chunks)
                        partial['transport']={'protocol':protocol,'format':'json','eof':True,'complete':False}
                        result=json.loads(raw)
                        if not isinstance(result,dict):raise ValueError('模型响应必须是 JSON 对象')
                        partial['transport'].update(complete=True,completion_basis='http_json_eof')
            except error.HTTPError as exc:
                provider_id=(exc.headers or {}).get('x-request-id') or (exc.headers or {}).get('request-id')
                if capture:capture.headers(exc)
                collected=[];read_error=None
                try:
                    for chunk in received_chunks(exc,capture):collected.append(chunk)
                except Exception as interrupted:read_error=interrupted
                raw=b''.join(collected);partial['transport']={'protocol':protocol,'format':'http_error','complete':read_error is None}
                try:
                    parsed=json.loads(raw)
                    if isinstance(parsed,dict):partial.update(response=parsed,usage=parsed.get('usage'))
                except (ValueError,UnicodeError):pass
                reason=self.settings.redact(raw.decode(errors='replace'))
                if read_error:reason+='；错误响应读取中断：'+self.settings.redact(str(read_error))
                finish('rejected',reason,state='http_error' if read_error is None else 'http_error_interrupted')
                raise ProviderHTTPError(exc.code,reason) from None
            except (error.URLError,ssl.SSLError,ConnectionError,TimeoutError,IncompleteRead) as exc:
                # No tools have been executed from this incomplete response.
                # Recover a transient connection drop without restarting the
                # whole experiment or discarding already saved artifacts.
                reason=self.settings.redact(str(exc))
                if result is not None and partial.get('transport',{}).get('complete'):
                    # A failure while closing an already completed response must
                    # not resend its request or turn it into an incomplete call.
                    partial['transport'].setdefault('diagnostics',[]).append('完整响应后的连接关闭异常：'+reason)
                else:
                    is_cancelled=cancelled()
                    finish('unknown',reason,state='cancelled' if is_cancelled else 'transport_interrupted')
                    if is_cancelled:raise InterruptedError('用户取消了当前模型返回') from None
                    if attempt<2:
                        if runtime:runtime.request_progress(identity,'连接中断，正在重新请求完整回复')
                        time.sleep(attempt+1);continue
                    raise ValueError("模型连接暂时失败，自动重试后仍未收到完整回复。已保存的文稿、数据和问题仍保留，可以稍后继续当前任务。") from None
            except Exception as exc:
                finish('unknown',self.settings.redact(str(exc)),state='cancelled' if isinstance(exc,InterruptedError) else 'parse_error')
                if isinstance(exc,InterruptedError):raise
                raise ValueError("模型请求失败：" + self.settings.redact(str(exc))) from None
            finish('returned',result=result,state='complete')
            if runtime:result['_runtime_request_id']=identity
            return result

    def complete(self,messages,tools=None,options=None):
        config=self.settings.read();opts={**config,**(options or {})}
        structured=bool(opts.get('_structured_output') and tools and len(tools)==1)
        server=parse.urlsplit(config['base_url'])
        native=structured and server.scheme=='https' and server.hostname=='api.deepseek.com'
        schema=tools[0]['function'] if native else None
        if native:
            # Select a per-request data transport on the configured provider;
            # never rewrite the user's settings or lower their effective budget.
            budget=opts.get('max_output_tokens') or (393216 if opts['protocol']=='chat' else 0)
            opts={**opts,'protocol':'responses','max_output_tokens':budget}
            messages=copy.deepcopy(messages)
            messages.append({'role':'system','content':'本次是结构对象提交，直接输出 text.format 指定的 JSON 对象，不调用工具。前文的提交工具名表示该对象的用途。字段及必填要求依原生 schema 和正式保存校验。'})
        elif structured:
            self.structured_event('structured_output_fallback',{'reason':'当前服务商未声明原生 schema 输出能力，沿用已配置的工具协议。','protocol':opts['protocol']})
        effort=opts["thinking_depth"]
        self._local.purpose=opts.get('_purpose','model');self._local.thinking_depth=effort
        streaming=bool(opts.get('_stream',bool((getattr(self._local,'scope',None) or {}).get('job_id'))))
        if effort not in ("off","low","high","max"):raise ValueError("不支持的思考深度")
        if opts["protocol"]=="responses":
            items=[]
            for message in messages:
                if message.get("_native_output") is not None:
                    items.extend(message["_native_output"]);continue
                if message["role"]=="tool":
                    items.append({"type":"function_call_output","call_id":message["tool_call_id"],"output":message["content"]});continue
                content=message.get("content") or ""
                if isinstance(content,list):
                    converted=[]
                    for block in content:
                        if block["type"]=="text":converted.append({"type":"input_text","text":block["text"]})
                        elif block["type"]=="image_url":
                            if not opts["native_vision"]:raise ValueError("原生图像能力已关闭")
                            converted.append({"type":"input_image","image_url":block["image_url"]["url"],"detail":"original"})
                    content=converted
                items.append({"role":message["role"],"content":content})
                for call in message.get("tool_calls",[]):
                    items.append({"type":"function_call","call_id":call["id"],**call["function"]})
            declarations=[{"type":"function",**t["function"]} for t in (tools or [])] if not native else []
            if not native and opts.get("native_search_transport")=="builtin" and opts["native_search"]:
                declarations.append({"type":"web_search"})
            body={"model":config["model"],"input":items,"stream":streaming,"store":False,"reasoning":{"effort":"none" if effort=="off" else effort}}
            if opts.get("max_output_tokens"):body["max_output_tokens"]=opts["max_output_tokens"]
            if native:body['text']={'format':{'type':'json_schema','name':schema['name'],'schema':schema['parameters']}}
            if declarations:body.update(tools=declarations,tool_choice="auto")
            try:
                if native:
                    self.structured_event('structured_output_selected',{'protocol':'responses','format':'json_schema','name':schema['name'],'max_output_tokens':body.get('max_output_tokens'),'thinking_depth':effort})
                    origin=parse.urlunsplit((server.scheme,server.netloc,'','',''))
                    response=self.post('/responses',body,base_url=origin)
                else:response=self.post("/responses",body)
            except ProviderHTTPError as err:
                unsupported=err.status in (404,405) or err.status in (400,422) and re.search(
                    r'(?:not.support|unsupported|not.implemented|unknown|不支持).*(?:json_schema|text[. ]format|responses)|(?:json_schema|text[. ]format|responses).*(?:not.support|unsupported|not.implemented|unknown|不支持)',err.detail,re.I|re.S)
                if not native or not unsupported:raise
                self.structured_event('structured_output_fallback',{'reason':str(err),'from':'responses_json_schema','to':config['protocol']+'_tools'})
                # The rejected attempt already has its own raw body and usage.
                # No structured result was received or applied from that attempt.
                return self.complete(messages[:-1],tools=tools,options={**(options or {}),'_structured_output':False})
            if response.get("status") in ("failed","incomplete"):
                raise ValueError("模型未完成输出："+str(response.get("error") or response.get("incomplete_details")))
            output=response.get("output",[]);texts=[];calls=[];citations=[];searches=[]
            def find_urls(value):
                if isinstance(value,dict):
                    if value.get("url"):citations.append({"url":value["url"],"title":value.get("title",value["url"])})
                    for child in value.values():find_urls(child)
                elif isinstance(value,list):
                    for child in value:find_urls(child)
            for item in output:
                if item.get("type")=="message":
                    for block in item.get("content",[]):
                        if block.get("type")=="output_text":texts.append(block.get("text",""));find_urls(block.get("annotations",[]))
                elif item.get("type")=="function_call":
                    calls.append({"id":item["call_id"],"type":"function","function":{"name":item["name"],"arguments":item["arguments"]}})
                elif item.get("type")=="web_search_call":searches.append(item);find_urls(item)
            return {"role":"assistant","content":"\n".join(texts),"tool_calls":calls,"_native_output":output,"_web_citations":citations,"_web_search_calls":searches},self.accounted_usage(response)
        body={"model":config["model"],"messages":[{k:v for k,v in m.items() if not k.startswith('_')} for m in messages],"stream":streaming}
        if streaming:body['stream_options']={'include_usage':True}
        if "deepseek" in config["base_url"] or config["model"].lower().startswith("deepseek"):
            body["max_tokens"]=opts.get("max_output_tokens") or 393216
            body["thinking"]={"type":"disabled" if effort=="off" else "enabled"}
            if effort!="off":body["reasoning_effort"]=effort
            for message in body["messages"]:
                if message["role"]=="assistant":message.setdefault("reasoning_content","")
        else:
            if effort!="off":body["reasoning_effort"]=effort
            if opts.get("max_output_tokens"):body["max_tokens"]=opts["max_output_tokens"]
        endpoint=None
        if tools:
            body.update(tools=tools,tool_choice="auto")
            server=parse.urlsplit(config["base_url"])
            if opts.get("_strict_tools") and server.hostname=="api.deepseek.com":
                from .tool_receipts import strict_declarations
                body["tools"]=strict_declarations(tools)
                endpoint=parse.urlunsplit((server.scheme,server.netloc,"/beta","",""))
        result=self.post("/chat/completions",body,base_url=endpoint) if endpoint else self.post("/chat/completions",body)
        if result["choices"][0].get("finish_reason")=="length":raise ValueError("模型输出达到上限；未把截断内容当成完整成果")
        if result["choices"][0].get("finish_reason") in ('aborted','content_filter','insufficient_system_resource'):
            raise ValueError('模型未完成回复：'+result['choices'][0]['finish_reason'])
        return result["choices"][0]["message"],self.accounted_usage(result)

    def structured_event(self,name,value):
        scope=getattr(self._local,'scope',None) or {'runtime':self.runtime,'job_id':None}
        if scope.get('runtime'):scope['runtime'].event(scope.get('job_id'),name,value)

    @staticmethod
    def accounted_usage(result):
        usage=dict(result.get('usage') or {})
        if result.get('_runtime_request_id'):usage['_runtime_request_id']=result['_runtime_request_id']
        return usage

    def provider_search(self,query,count=10,options=None):
        config={**self.settings.read(),**(options or {})}
        if not config["native_search"]:raise ValueError("原生联网已关闭")
        if not (1<=len(query.strip())<=128):raise ValueError("原生检索词需为 1–128 字符")
        # Provider search is separate from Bing/host browsing; credentials stay
        # inside this client and are never returned in tool results.
        if "deepseek.com" not in parse.urlsplit(config["base_url"]).hostname:
            raise ValueError("当前服务商的原生搜索端点未配置；可选择 Responses 内置联网")
        prefix="" if config["base_url"].rstrip('/').endswith('/v1') else "/v1"
        return self.post(prefix+"/tools/search",{"query":query,"count":count,"need_content":True})
