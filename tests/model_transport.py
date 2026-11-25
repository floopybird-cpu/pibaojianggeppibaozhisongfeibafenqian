"""Local-only transport fixtures: exact wire bytes, framing, snapshots and failures."""
import hashlib
import io
import json
import sys
import tempfile
import unittest
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError,URLError
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.model_stream import consume_sse
from app.network import ModelClient
from app.session_runtime import SessionRuntime
from app.storage import Store,now


class Settings:
    def __init__(self,**extra):self.extra=extra
    def read(self):
        return {'configured':True,'base_url':'https://fixture.invalid','model':'fixture_NOT_live',
                'thinking_depth':'high','protocol':'chat','native_search':False,'native_vision':True,
                'native_search_transport':'provider','timeout':1,'max_output_tokens':123456,**self.extra}
    def key(self):return 'LOCAL-FIXTURE-NOT-A-KEY'
    def redact(self,value):return str(value).replace(self.key(),'[REDACTED]')


class Response(io.BytesIO):
    def __init__(self,data,stream=True,size=65536,fail=None):
        super().__init__(data);self.size=size;self.fail=fail;self.status=200
        self.headers={'Content-Type':'text/event-stream' if stream else 'application/json',
                      'x-request-id':'provider-fixture','Authorization':'must-not-be-recorded','Set-Cookie':'private-cookie'}
    def read1(self,size=-1):
        value=super().read(min(size,self.size))
        if not value and self.fail:raise self.fail
        return value


def frame(value):return ('data: '+json.dumps(value,ensure_ascii=False)+'\n\n').encode()
def choice(delta=None,finish=None,index=0,**extra):return {'index':index,'delta':delta or {},'finish_reason':finish,**extra}
def chunk(*choices,**extra):return {'id':'fixture-response','choices':list(choices),**extra}
def call(args,name=None,index=0,identifier=None):
    value={'index':index,'function':{'arguments':args}}
    if name is not None:value['function']['name']=name
    if identifier is not None:value.update(id=identifier,type='function')
    return value
DONE=b'data: [DONE]\n\n'
USAGE={'prompt_tokens':51,'completion_tokens':21,'completion_tokens_details':{'reasoning_tokens':10}}


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.directory.name));self.runtime=SessionRuntime(self.store)
        self.store.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,0,?,?)',('job','agent','running',0,'{}',None,None,now(),now()))
        self.client=ModelClient(Settings(),self.runtime)
    def tearDown(self):self.directory.cleanup()
    def invoke(self,response,**options):
        with self.client.scope(self.runtime,'job'),patch('app.network.request.urlopen',return_value=response) as opened:
            result=self.client.complete([{'role':'user','content':'local fixture'}],options=options)
        self.assertEqual(opened.call_count,1)
        return result
    def invoke_attempts(self,responses,**options):
        with self.client.scope(self.runtime,'job'),patch('app.network.request.urlopen',side_effect=responses) as opened,patch('app.network.time.sleep'):
            try:return self.client.complete([{'role':'user','content':'local fixture'}],options=options)
            finally:self.assertEqual(opened.call_count,len(responses))
    def captured(self,expected,status='returned',index=None):
        rows=self.runtime.requests('job')
        if index is None:self.assertEqual(len(rows),1)
        row=rows[index or 0];self.assertEqual(row['status'],status)
        row.update(self.store.one('SELECT response_blob FROM model_requests WHERE id=?',(row['id'],)))
        metadata=self.runtime.read_blob(row['raw_response_blob'])
        actual=(self.runtime.blobs/metadata['body_file']).read_bytes()
        self.assertEqual(actual,expected)
        self.assertEqual(metadata['body_sha256'],hashlib.sha256(expected).hexdigest())
        self.assertEqual(metadata['byte_count'],len(expected))
        self.assertEqual(metadata['request_id'],row['id'])
        encoded=json.dumps(metadata)
        for secret in ('must-not-be-recorded','private-cookie','LOCAL-FIXTURE-NOT-A-KEY','Authorization'):
            self.assertNotIn(secret,encoded)
        return metadata,row

    def structured_fixture(self,content='{"title":"optics"}',**extra):
        return json.dumps({'id':'native-fixture','status':'completed','output':[
            {'id':'message-fixture','type':'message','role':'assistant','content':[{'type':'output_text','text':content}]}],
            'usage':USAGE,**extra}).encode()

    def structured_tool(self):
        return {'type':'function','function':{'name':'submit_plan','parameters':{'type':'object',
            'properties':{'title':{'type':'string'},'optional':{'type':'string'}},'required':['title'],'additionalProperties':False}}}

    def test_official_native_schema_preserves_optional_fields_high_budget_and_images(self):
        self.client.settings=Settings(base_url='https://api.deepseek.com/beta',max_output_tokens=0)
        raw=self.structured_fixture();tool=self.structured_tool()
        messages=[{'role':'user','content':[{'type':'text','text':'Optical JSON plan'},
            {'type':'image_url','image_url':{'url':'data:image/png;base64,fixture'}}]}]
        with self.client.scope(self.runtime,'job'),patch('app.network.request.urlopen',return_value=Response(raw,stream=False)) as opened:
            reply,_=self.client.complete(messages,tools=[tool],options={'_structured_output':True,'_stream':False})
        req=opened.call_args.args[0];body=json.loads(req.data)
        self.assertEqual(req.full_url,'https://api.deepseek.com/responses')
        self.assertEqual(body['text']['format']['schema'],tool['function']['parameters'])
        self.assertEqual(body['text']['format']['name'],'submit_plan')
        self.assertEqual(body['reasoning']['effort'],'high');self.assertEqual(body['max_output_tokens'],393216)
        self.assertNotIn('tools',body);self.assertFalse(body['stream'])
        self.assertEqual(body['input'][0]['content'][1]['type'],'input_image')
        self.assertEqual(body['input'][0]['content'][1]['image_url'],messages[0]['content'][1]['image_url']['url'])
        self.assertEqual(reply['tool_calls'],[]);self.assertEqual(reply['content'],'{"title":"optics"}')
        self.assertTrue(reply['_native_output']);self.captured(raw)
        self.assertEqual(self.client.settings.read()['protocol'],'chat')

    def test_native_schema_preserves_explicit_and_responses_default_budgets(self):
        for protocol,budget in [('chat',234567),('responses',0)]:
            self.client.settings=Settings(base_url='https://api.deepseek.com',protocol=protocol,max_output_tokens=budget)
            with patch.object(self.client,'post',return_value=json.loads(self.structured_fixture())) as post:
                self.client.complete([{'role':'user','content':'JSON'}],tools=[self.structured_tool()],options={'_structured_output':True})
            body=post.call_args.args[1]
            if budget:self.assertEqual(body['max_output_tokens'],budget)
            else:self.assertNotIn('max_output_tokens',body)

    def test_unsupported_native_schema_falls_back_with_separate_raw_attempts(self):
        self.client.settings=Settings(base_url='https://api.deepseek.com')
        rejection=b'{"error":{"message":"json_schema is not supported"}}'
        legacy=json.dumps({'id':'chat-fixture','choices':[{'index':0,'finish_reason':'tool_calls','message':{
            'role':'assistant','content':'','tool_calls':[{'id':'call-fixture','type':'function','function':{'name':'submit_plan','arguments':'{"title":"optics"}'}}]}}],'usage':USAGE}).encode()
        failure=HTTPError('https://api.deepseek.com/responses',400,'unsupported',{'Content-Type':'application/json'},io.BytesIO(rejection))
        with self.client.scope(self.runtime,'job'),patch('app.network.request.urlopen',side_effect=[failure,Response(legacy,stream=False)]) as opened:
            message,_=self.client.complete([{'role':'user','content':'JSON'}],tools=[self.structured_tool()],options={'_structured_output':True,'_stream':False})
        self.assertEqual([c.args[0].full_url for c in opened.call_args_list],['https://api.deepseek.com/responses','https://api.deepseek.com/chat/completions'])
        self.assertEqual(message['tool_calls'][0]['id'],'call-fixture')
        self.assertEqual(len(self.runtime.requests('job')),2)
        self.captured(rejection,'rejected',index=0);self.captured(legacy,index=1)
        events=self.store.all("SELECT blob FROM runtime_events WHERE event='structured_output_fallback'")
        self.assertEqual(len(events),1);self.assertIn('not supported',self.runtime.read_blob(events[0]['blob'])['reason'])

    def test_other_provider_is_not_probed_and_authorization_failure_is_not_fallback(self):
        with patch.object(self.client,'post',return_value={'choices':[{'message':{'role':'assistant','content':'{}'}}]}) as post:
            self.client.complete([{'role':'user','content':'JSON'}],tools=[self.structured_tool()],options={'_structured_output':True})
        self.assertEqual(post.call_args.args[0],'/chat/completions')
        self.assertIn('tools',post.call_args.args[1])
        self.client.settings=Settings(base_url='https://api.deepseek.com')
        error_body=b'{"error":{"message":"unauthorized"}}'
        denied=HTTPError('https://api.deepseek.com/responses',401,'unauthorized',{},io.BytesIO(error_body))
        with self.client.scope(self.runtime,'job'),patch('app.network.request.urlopen',side_effect=denied) as opened:
            with self.assertRaisesRegex(ValueError,'401'):
                self.client.complete([{'role':'user','content':'JSON'}],tools=[self.structured_tool()],options={'_structured_output':True,'_stream':False})
        self.assertEqual(opened.call_count,1)

    def test_native_incomplete_is_not_tool_fallback_and_keeps_usage(self):
        self.client.settings=Settings(base_url='https://api.deepseek.com')
        raw=self.structured_fixture(status='incomplete',incomplete_details={'reason':'max_output_tokens'})
        with self.client.scope(self.runtime,'job'),patch('app.network.request.urlopen',return_value=Response(raw,stream=False)) as opened:
            with self.assertRaisesRegex(ValueError,'未完成输出'):
                self.client.complete([{'role':'user','content':'JSON'}],tools=[self.structured_tool()],options={'_structured_output':True,'_stream':False})
        self.assertEqual(opened.call_count,1);_,row=self.captured(raw)
        self.assertEqual(row['usage'],USAGE)

    def test_native_schema_receipt_correction_preserves_native_output_without_fake_call(self):
        from app.worksheet_submission import submit_record
        self.client.settings=Settings(base_url='https://api.deepseek.com')
        first=self.structured_fixture('{}');second=self.structured_fixture()
        cache={};service=SimpleNamespace(store=self.store,agent=SimpleNamespace(client=self.client,runtime=self.runtime))
        with self.client.scope(self.runtime,'job'),patch('app.network.request.urlopen',side_effect=[Response(first,stream=False),Response(second,stream=False)]) as opened:
            value=submit_record(service,'job',self.structured_tool(),[{'role':'user','content':'JSON optical plan'}],
                {'_structured_output':True,'thinking_depth':'high'},'outline',cache.get,cache.__setitem__)
        self.assertEqual(value,{'title':'optics'})
        second_body=json.loads(opened.call_args_list[1].args[0].data)
        self.assertIn(json.loads(first)['output'][0],second_body['input'])
        self.assertFalse(any(i.get('type') in ('function_call','function_call_output') for i in second_body['input']))
        self.assertTrue(any('invalid_arguments' in str(i.get('content','')) for i in second_body['input']))
        self.assertEqual(len(self.runtime.requests('job')),2)

    def test_standard_fragments_and_large_arguments_keep_exact_bytes(self):
        argument=json.dumps({'text':'光电原始记录'*12000},ensure_ascii=False)
        data=frame(chunk(choice({'tool_calls':[call(argument[:17],'submit',identifier='call-0')]})))
        data+=b''.join(frame(chunk(choice({'tool_calls':[call(argument[i:i+257])]}))) for i in range(17,len(argument),257))
        data+=frame(chunk(choice(finish='tool_calls')))+frame(chunk(usage=USAGE))+DONE
        message,usage=self.invoke(Response(data,size=997))
        self.assertEqual(message['tool_calls'][0]['function']['arguments'],argument)
        self.assertEqual(usage['completion_tokens'],21)
        metadata,_=self.captured(data)
        self.assertEqual(metadata['transport']['completion_basis'],'done_and_finish')
        self.assertTrue(metadata['transport']['complete'])

    def test_choice_and_tool_indexes_are_separate(self):
        data=frame(chunk(choice({'tool_calls':[call('{"a":','alpha',identifier='a')]},index=1),
                         choice({'tool_calls':[call('{"b":','beta',identifier='b')]},index=0)))
        data+=frame(chunk(choice({'tool_calls':[call('2}')]},finish='tool_calls',index=0),
                          choice({'tool_calls':[call('1}')]},finish='tool_calls',index=1)))+DONE
        message,_=self.invoke(Response(data))
        self.assertEqual(message['tool_calls'][0]['function'],{'name':'beta','arguments':'{"b":2}'})
        _,row=self.captured(data)
        saved=self.runtime.read_blob(row['response_blob'])
        self.assertEqual([c['index'] for c in saved['choices']],[0,1])
        self.assertEqual(saved['choices'][1]['message']['tool_calls'][0]['function']['name'],'alpha')

    def test_final_message_snapshot_completes_json_siblings(self):
        start=call('{"title":"optics"}','submit',identifier='a')
        full=call('{"title":"optics","stages":["record"]}','submit',identifier='a')
        data=frame(chunk(choice({'tool_calls':[start]})))+frame(chunk(choice(finish='tool_calls',message={'tool_calls':[full]})))+DONE
        message,_=self.invoke(Response(data,size=7))
        self.assertEqual(set(json.loads(message['tool_calls'][0]['function']['arguments'])),{'title','stages'})
        metadata,_=self.captured(data)
        self.assertEqual(metadata['transport']['message_snapshots'],1)

    def test_conflicting_snapshot_is_rejected_and_preserved(self):
        data=frame(chunk(choice({'tool_calls':[call('{"title":"old"}','submit',identifier='a')]}),usage=USAGE))
        data+=frame(chunk(choice(finish='tool_calls',message={'tool_calls':[call('{"title":"different"}','submit',identifier='a')]})))+DONE
        with self.assertRaisesRegex(ValueError,'冲突'):self.invoke(Response(data))
        metadata,row=self.captured(data,'unknown')
        self.assertEqual(metadata['state'],'parse_error');self.assertEqual(row['usage'],USAGE)

    def test_multiline_sse_crlf_bom_and_unicode_fragments(self):
        event=json.dumps(chunk(choice({'content':'中文测量'},finish='stop')),ensure_ascii=False,indent=2)
        data=b'\xef\xbb\xbf: keepalive\r\nevent: message\r\n'+b''.join(('data: '+line+'\r\n').encode() for line in event.splitlines())+b'\r\n'+DONE
        message,_=self.invoke(Response(data,size=1))
        self.assertEqual(message['content'],'中文测量');self.captured(data)

    def test_incomplete_other_choice_does_not_use_first_finish(self):
        data=frame(chunk(choice({'content':'done'},finish='stop'),choice({'content':'unfinished'},index=1)),)+DONE
        with self.assertRaisesRegex(ValueError,'部分选项'):self.invoke(Response(data))
        metadata,_=self.captured(data,'unknown');self.assertFalse(metadata['transport']['complete'])

    def test_eof_with_finish_is_compatible_and_diagnosed(self):
        data=frame(chunk(choice({'content':'complete'},finish='stop')))
        self.invoke(Response(data));metadata,_=self.captured(data)
        self.assertTrue(metadata['body_eof']);self.assertEqual(metadata['transport']['completion_basis'],'eof_and_finish')
        self.assertTrue(metadata['transport']['diagnostics'])

    def test_socket_interruption_keeps_partial_response_and_usage(self):
        data=frame(chunk(choice({'content':'received'}),usage=USAGE))
        with self.assertRaisesRegex(ValueError,'自动重试后仍未收到完整回复') as raised:
            self.invoke_attempts([Response(data,fail=TimeoutError('fixture timeout')) for _ in range(3)])
        self.assertNotIn('fixture timeout',str(raised.exception))
        self.assertEqual(len(self.runtime.requests('job')),3)
        for i in range(3):
            metadata,row=self.captured(data,'unknown',i)
            self.assertEqual(metadata['state'],'transport_interrupted');self.assertEqual(row['usage'],USAGE)
            self.assertEqual(self.runtime.read_blob(row['response_blob'])['choices'][0]['message']['content'],'received')
            self.assertIn('fixture timeout',row['error'])

    def test_incomplete_read_partial_bytes_are_recorded_before_exception(self):
        first=frame(chunk(choice({'content':'first'})))
        last=frame(chunk(usage=USAGE))
        complete=frame(chunk(choice({'content':'recovered'},finish='stop'),usage=USAGE))+DONE
        message,_=self.invoke_attempts([Response(first,fail=IncompleteRead(last,10)),Response(complete)])
        self.assertEqual(message['content'],'recovered')
        _,row=self.captured(first+last,'unknown',0);self.assertEqual(row['usage'],USAGE)
        self.captured(complete,'returned',1)

    def test_cancel_keeps_received_bytes_without_retry(self):
        data=frame(chunk(choice({'content':'received'}),usage=USAGE))+DONE
        def response(*args,**kwargs):
            self.store.execute('UPDATE jobs SET cancel=1 WHERE id=?',('job',))
            return Response(data)
        with self.client.scope(self.runtime,'job'),patch('app.network.request.urlopen',side_effect=response) as opened:
            with self.assertRaises(InterruptedError):self.client.complete([{'role':'user','content':'local fixture'}])
        self.assertEqual(opened.call_count,1)
        metadata,row=self.captured(data,'unknown')
        self.assertEqual(metadata['state'],'cancelled');self.assertEqual(row['usage'],USAGE)

    def test_partial_tool_is_never_returned_or_joined_to_retried_reply(self):
        partial=frame(chunk(choice({'tool_calls':[call('{"partial":','submit_partial',identifier='partial-call')]})))
        complete=frame(chunk(choice({'tool_calls':[call('{"complete":true}','submit_complete',identifier='complete-call')]},finish='tool_calls'),usage=USAGE))+DONE
        message,_=self.invoke_attempts([Response(partial,fail=IncompleteRead(b'',50)),Response(complete)])
        self.assertEqual([c['id'] for c in message['tool_calls']],['complete-call'])
        self.assertEqual(message['tool_calls'][0]['function']['arguments'],'{"complete":true}')
        _,first=self.captured(partial,'unknown',0);self.assertIsNone(first['usage'])
        self.captured(complete,'returned',1)
        meter=self.runtime.summary('job')
        self.assertEqual((meter['model_requests'],meter['known_usage_requests'],meter['unknown_usage_requests']),(2,1,1))
        self.assertEqual(self.store.one('SELECT count(*) AS n FROM runtime_tools')['n'],0)

    def test_nonstream_read_interruption_retries_complete_json(self):
        fragment=b'{"choices":[{"message":{"content":"unfinished'
        complete=json.dumps(chunk(choice(finish='stop',message={'role':'assistant','content':'full JSON'}),usage=USAGE)).encode()
        message,_=self.invoke_attempts([Response(fragment,stream=False,fail=IncompleteRead(b'',20)),Response(complete,stream=False)],_stream=False)
        self.assertEqual(message['content'],'full JSON')
        _,row=self.captured(fragment,'unknown',0);self.assertIsNone(row['usage'])
        metadata,_=self.captured(complete,'returned',1)
        self.assertEqual(metadata['transport']['completion_basis'],'http_json_eof')

    def test_cancel_observed_with_read_failure_does_not_retry(self):
        data=frame(chunk(choice({'content':'partial'})))
        store=self.store
        class CancelAtFailure(Response):
            def read1(self,size=-1):
                value=super().read1(size)
                if not value:
                    store.execute("UPDATE jobs SET cancel=1 WHERE id='job'")
                    raise IncompleteRead(b'',1)
                return value
        with self.assertRaises(InterruptedError):self.invoke(CancelAtFailure(data))
        metadata,_=self.captured(data,'unknown');self.assertEqual(metadata['state'],'cancelled')

    def test_cancel_during_retry_delay_does_not_start_another_attempt(self):
        data=frame(chunk(choice({'content':'incomplete'})))
        def cancel(_):self.store.execute("UPDATE jobs SET cancel=1 WHERE id='job'")
        with self.client.scope(self.runtime,'job'),patch('app.network.request.urlopen',return_value=Response(data,fail=TimeoutError('fixture interrupted'))) as opened,patch('app.network.time.sleep',side_effect=cancel):
            with self.assertRaises(InterruptedError):self.client.complete([{'role':'user','content':'local fixture'}])
        self.assertEqual(opened.call_count,1);self.captured(data,'unknown')

    def test_complete_receipt_with_close_error_is_returned_without_retry(self):
        class CloseFailure(Response):
            def __exit__(self,*args):
                super().__exit__(*args);raise ConnectionResetError('fixture close error')
        data=frame(chunk(choice({'content':'already complete'},finish='stop'),usage=USAGE))+DONE
        message,_=self.invoke(CloseFailure(data))
        self.assertEqual(message['content'],'already complete')
        metadata,_=self.captured(data)
        self.assertTrue(metadata['transport']['complete'])
        self.assertIn('关闭异常',metadata['transport']['diagnostics'][-1])

    def test_complete_response_is_reused_without_another_request(self):
        data=frame(chunk(choice({'content':'complete'},finish='stop'),usage=USAGE))+DONE
        with self.client.scope(self.runtime,'job'),patch('app.network.request.urlopen',return_value=Response(data)) as opened:
            first=self.client.complete([{'role':'user','content':'local fixture'}])
            again=self.client.complete([{'role':'user','content':'local fixture'}])
        self.assertEqual(first,again);self.assertEqual(opened.call_count,1);self.captured(data)

    def test_unknown_payload_does_not_silently_disappear(self):
        data=frame(chunk(choice({'function_call':{'name':'legacy','arguments':'{}'}}),usage=USAGE))+DONE
        with self.assertRaisesRegex(ValueError,'未支持的内容字段'):self.invoke(Response(data))
        metadata,row=self.captured(data,'unknown')
        self.assertEqual(metadata['transport']['unsupported_payloads'],['choices.delta.function_call'])
        self.assertEqual(row['usage'],USAGE)

    def test_http_error_preserves_wire_usage_and_request_identity(self):
        data=json.dumps({'error':{'message':'fixture rate limit'},'usage':USAGE}).encode()
        error=HTTPError('https://fixture.invalid',429,'limited',{'Content-Type':'application/json','x-request-id':'http-fixture'},io.BytesIO(data))
        with self.client.scope(self.runtime,'job'),patch('app.network.request.urlopen',side_effect=error) as opened:
            with self.assertRaisesRegex(ValueError,'HTTP 429'):self.client.complete([{'role':'user','content':'fixture'}])
        self.assertEqual(opened.call_count,1)
        metadata,row=self.captured(data,'rejected')
        self.assertEqual(row['usage'],USAGE);self.assertEqual(metadata['http_status'],429)

    def test_nonstream_json_and_malformed_json_preserve_full_bytes(self):
        data=json.dumps(chunk(choice(finish='stop',message={'role':'assistant','content':'JSON response'})),ensure_ascii=False).encode()
        message,_=self.invoke(Response(data,stream=False,size=3),_stream=False)
        self.assertEqual(message['content'],'JSON response')
        metadata,_=self.captured(data);self.assertEqual(metadata['transport']['completion_basis'],'http_json_eof')
        with tempfile.TemporaryDirectory() as directory:
            runtime=SessionRuntime(Store(Path(directory)))
            client=ModelClient(Settings(),runtime)
            with patch('app.network.request.urlopen',return_value=Response(b'{broken json',stream=False)):
                with self.assertRaises(ValueError):client.complete([{'role':'user','content':'fixture'}])
            row=runtime.requests()[0];meta=runtime.read_blob(row['raw_response_blob'])
            self.assertEqual((runtime.blobs/meta['body_file']).read_bytes(),b'{broken json')
            self.assertEqual(meta['state'],'parse_error')

    def test_responses_and_search_keep_existing_shapes(self):
        output={'id':'response-fixture','status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'done'}]}],'usage':USAGE}
        data=frame({'type':'response.output_text.delta','delta':'done'})+frame({'type':'response.completed','response':output})
        self.client=ModelClient(Settings(protocol='responses'),self.runtime)
        message,_=self.invoke(Response(data,size=17));self.assertEqual(message['content'],'done')
        metadata,_=self.captured(data);self.assertEqual(metadata['protocol'],'responses')
        with tempfile.TemporaryDirectory() as directory:
            runtime=SessionRuntime(Store(Path(directory)));client=ModelClient(Settings(),runtime)
            body={'data':[{'url':'https://example.org','title':'fixture'}]};binary=json.dumps(body).encode()
            with patch('app.network.request.urlopen',return_value=Response(binary,False)):
                result=client.post('/v1/tools/search',{'query':'fixture'})
            self.assertEqual(result['data'],body['data']);self.assertEqual(runtime.summary()['search_requests'],1)
            row=runtime.requests()[0];meta=runtime.read_blob(row['raw_response_blob'])
            self.assertEqual(meta['protocol'],'search');self.assertEqual((runtime.blobs/meta['body_file']).read_bytes(),binary)

    def test_connection_failures_have_empty_captures_and_exact_attempt_count(self):
        with patch('app.network.request.urlopen',side_effect=URLError('fixture refused')) as opened,patch('app.network.time.sleep'):
            with self.assertRaises(ValueError):self.client.complete([{'role':'user','content':'fixture'}])
        self.assertEqual(opened.call_count,3)
        self.assertEqual(self.runtime.summary()['model_requests'],3)
        for row in self.runtime.requests():
            meta=self.runtime.read_blob(row['raw_response_blob'])
            self.assertEqual(meta['byte_count'],0);self.assertEqual(meta['state'],'transport_interrupted')
        self.assertFalse(list(self.runtime.blobs.glob('*.partial')))

    def test_process_interruption_recovery_hashes_existing_bytes_without_a_request(self):
        identity=self.runtime.request_start('job','fixture',Settings().read())
        capture=self.runtime.response_body(identity,'job','chat')
        raw=b'data: {"unfinished":'
        capture.write(raw);capture.file.close()  # Simulate a process exit before its handler.
        self.store.execute("UPDATE jobs SET status='interrupted' WHERE id='job'")
        with patch('app.network.request.urlopen',side_effect=AssertionError('recovery must be offline')):
            recovered=SessionRuntime(self.store)
        row=recovered.requests('job')[0];metadata=recovered.read_blob(row['raw_response_blob'])
        self.assertEqual(row['status'],'unknown');self.assertEqual(metadata['state'],'process_interrupted')
        self.assertEqual(metadata['body_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual((recovered.blobs/metadata['body_file']).read_bytes(),raw)
        self.assertEqual(recovered.summary('job')['model_requests'],1)


if __name__=='__main__':unittest.main()
