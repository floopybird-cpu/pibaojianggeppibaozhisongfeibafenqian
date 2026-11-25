"""Recover real provider context overflow using lossless, pageable tool receipts.

Nothing is shortened until the provider actually rejects its context. The full
result remains available to the same job; final files and source data are intact.
"""
from pathlib import Path
import hashlib
import json
import re


def _directory(authority, job_id):
    if not job_id or Path(job_id).name != job_id or any(c in job_id for c in '/\\:'):
        raise ValueError('无效任务')
    return Path(authority)/'tool-results'/job_id


def read_receipt(authority, job_id, receipt_id, offset=0, count=None):
    if not isinstance(receipt_id,str) or not re.fullmatch('[a-f0-9]{64}',receipt_id):
        raise ValueError('无效读取记录')
    path=_directory(authority,job_id)/(receipt_id+'.json')
    if not path.is_file():raise ValueError('本任务没有这份完整读取记录')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=receipt_id:raise ValueError('完整读取记录校验失败')
    content=raw.decode('utf-8')
    if type(offset) is not int or offset<0 or offset>len(content):raise ValueError('读取起点超出记录')
    if count is not None and (type(count) is not int or count<1):raise ValueError('读取长度须为正整数')
    end=len(content) if count is None else min(len(content),offset+count)
    return {'receipt_id':receipt_id,'total_characters':len(content),'offset':offset,'next_offset':end,'complete':offset==0 and end==len(content),'content':content[offset:end]}


def page_rejected_context(messages, error, authority, job_id):
    if not re.search(r'maximum context length|context[_ ]length[_ ]exceeded|上下文.{0,12}(?:超出|超限)',str(error),re.I):return False
    candidates=[]
    for i,message in enumerate(messages):
        value=message.get('content')
        if message.get('role')!='tool' or not isinstance(value,str):continue
        try:
            if isinstance(json.loads(value),dict) and json.loads(value).get('pageable_full_tool_result'):continue
        except (ValueError,TypeError):pass
        candidates.append((len(value),i,value))
    if not candidates:return False
    size,index,content=max(candidates)
    # Replacing a result must actually release context, never grow it.
    preview=content[:500];tail=content[-500:] if len(content)>1000 else ''
    raw=content.encode('utf-8');identity=hashlib.sha256(raw).hexdigest()
    receipt={'pageable_full_tool_result':True,'receipt_id':identity,'total_characters':size,'preview_first_characters':preview,'preview_last_characters':tail,'notice':'服务商实际拒绝上下文容量；这是明确标注的预览。完整工具结果已原样保存，用read_tool_result按offset/count分段读取，省略count可读全文。原始文件、成品及分析程序的全量输入均未截断。'}
    replacement=json.dumps(receipt,ensure_ascii=False,separators=(',',':'))
    if len(replacement)>=size:return False
    directory=_directory(authority,job_id);directory.mkdir(parents=True,exist_ok=True)
    (directory/(identity+'.json')).write_bytes(raw)
    messages[index]={**messages[index],'content':replacement}
    messages.append({'role':'user','content':'服务商刚刚报告实际上下文容量不足。一份已读取的大工具结果已原样保存为可分页记录，预览有明确标记。按需用read_tool_result分段查证；核对物理量或单位优先读说明、转录和列名，不必把所有采样点重复塞进同一轮。不能声称预览等于全文，不得因此缩短最终文稿或裁剪分析输入。'})
    return True
