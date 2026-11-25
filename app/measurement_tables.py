"""Render fixed normalized records; prose never becomes the data authority."""
import copy

FIELDS={'quantity':'项目','values':'原始读数','unit':'单位','section':'分组','note':'记录说明','status':'记录性质'}
STATUSES={'recorded':'原文记录','inferred':'据背景解释','pending':'尚未明确','confirmed':'已明确答复'}


def measurement_table(block,snapshot):
    ref=block['workbook_ref'];obj=snapshot.object(ref['id'],ref['revision'])
    if obj['kind']!='DataWorkbook':raise ValueError('测量记录表块须引用固定规范记录')
    body=obj['content'];records={r['id']:r for r in body['records']}
    selected=block.get('record_ids',list(records))
    if not selected or len(selected)!=len(set(selected)) or any(k not in records for k in selected):
        raise ValueError('所选测量记录不存在、为空或重复')
    fields=block.get('fields',['quantity','values','unit'])
    if not fields or len(fields)!=len(set(fields)) or any(k not in FIELDS for k in fields):raise ValueError('测量表列须选实际记录字段')
    headers=block.get('headers',[FIELDS[k] for k in fields])
    if len(headers)!=len(fields) or any(not isinstance(h,str) or not h.strip() for h in headers):raise ValueError('自定表头须与所选记录字段一一对应')
    if not any((a:=snapshot.anchor(identity))['source_id']==body['source_id'] and a['source_sha']==body['source_sha'] for identity in block['sources']):
        raise ValueError('测量表须标明与规范记录原件一致的来源')
    def display(record,field):
        if field=='values':return '、'.join(record['values'])
        if field=='status':return STATUSES[record['status']]
        return record.get(field,'')
    return {'type':'table','caption':block.get('caption','原始测量记录'),'columns':headers,
            'rows':[[display(records[k],field) for field in fields] for k in selected],
            'sources':block['sources'],**{k:block[k] for k in ('column_weights',) if k in block}}


def resolve_measurement_tables(content,snapshot):
    result=copy.deepcopy(content);used={}
    def descend(blocks):
        for index,block in enumerate(blocks):
            if block['type']=='columns':
                for column in block['columns']:descend(column['blocks'])
            elif block['type']=='measurement_table':
                ref=block['workbook_ref'];key=(ref['id'],ref['revision'])
                used[key]=snapshot.object(*key)
                blocks[index]=measurement_table(block,snapshot)
    for section in result['sections']+result['appendices']:descend(section['blocks'])
    return result,list(used.values())
