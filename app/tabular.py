"""Readonly tabular decoders; original bytes are never converted in place."""
from __future__ import annotations

import csv
from datetime import datetime
import io
import math
import struct


def decode_text(data):
    if data.startswith((b"\xff\xfe",b"\xfe\xff")):return data.decode("utf-16")
    try:return data.decode("utf-8-sig")
    except UnicodeDecodeError:return data.decode("gb18030")


def workbook_tables(data):
    import openpyxl
    book=openpyxl.load_workbook(io.BytesIO(data),read_only=True,data_only=True)
    tables=[]
    for index,sheet in enumerate(book):
        rows=sheet.iter_rows(values_only=True);header=next(rows,())
        columns=[str(v) if v is not None else "" for v in header]
        if not columns or not any(columns):continue
        if len(columns)!=len(set(columns)) or any(not c for c in columns):raise ValueError("XLSX 表头为空或重复，请选择有明确列名的工作表")
        content=[]
        for row in rows:
            content.append({c:(v.isoformat(sep=" ") if isinstance(v,datetime) else "" if v is None else v) for c,v in zip(columns,row)})
        tables.append({"id":str(index),"name":sheet.title,"columns":columns,"rows":content,"warnings":["工作表中的缺失／未接通通道保留原值；不把热电偶类型 K 当成开尔文单位。"]})
    book.close();return tables


def qt_temperature_tables(data):
    from PySide6.QtCore import QByteArray,QBuffer,QIODevice,QDataStream,QDateTime
    if len(data)<40 or struct.unpack(">I",data[:4])[0]!=len(data)-4:
        raise ValueError("未知 DAT 格式；不将二进制强行按文本或温度解析")
    buffer=QBuffer();buffer.setData(QByteArray(data));buffer.open(QIODevice.ReadOnly)
    stream=QDataStream(buffer);stream.setVersion(QDataStream.Qt_5_15)
    stream.readUInt32();title=stream.readQString();created=QDateTime();stream>>created
    if title!="新建档案":raise ValueError("DAT 不匹配已验证的 Qt 温度采集布局")
    count=stream.readUInt32()
    if not 1<=count<=100:raise ValueError("DAT 采集段数量异常")
    tables=[]
    for index in range(count):
        start=buffer.pos();block_size=stream.readUInt32();name=stream.readQString();stamp=QDateTime();stream>>stamp
        n=stream.readUInt32()
        if not 1<=n<=100000:raise ValueError("DAT 记录数异常")
        rows=[]
        for row_index in range(n):
            byte_array=QByteArray();stream>>byte_array;packet=bytes(byte_array)
            if len(packet)!=32:raise ValueError("DAT 帧长变化；拒绝套用已验证布局")
            try:
                timestamp=datetime(2000+packet[10],*packet[11:16]).isoformat(sep=" ")
                t1,t2=struct.unpack_from("<ff",packet,16)
                if not all(math.isfinite(v) and -270<v<1500 for v in (t1,t2)):raise ValueError("温度超范围")
            except Exception as exc:raise ValueError("DAT 帧日期／温度无法核验："+str(exc)) from None
            rows.append({"row":row_index+1,"time":timestamp,"channel_1_C":t1,"channel_2_C":t2})
        if buffer.pos()!=start+4+block_size or stream.status()!=QDataStream.Ok:raise ValueError("DAT 子块字节长度或流状态不符")
        tables.append({"id":str(index),"name":f"采集段 {index+1} {name} {rows[0]['time']}","columns":["row","time","channel_1_C","channel_2_C"],"rows":rows,
                       "warnings":["专用 Qt DAT 布局；物理测点需结合原始说明与导出 CSV 核验。第 3／4 个浮点字段含义未确认，不作为温度使用。"]})
    if buffer.pos()!=len(data):raise ValueError("DAT 存在未识别尾部，不静默忽略")
    return tables


def read_tables(data,kind):
    if kind=="xlsx":return workbook_tables(data)
    if kind=="dat":return qt_temperature_tables(data)
    if kind not in ("csv","tsv"):raise ValueError("请选择 CSV、TSV、XLSX 或已验证格式的采集 DAT")
    reader=csv.DictReader(io.StringIO(decode_text(data)),delimiter="\t" if kind=="tsv" else ",")
    columns=reader.fieldnames or []
    if not columns or len(columns)!=len(set(columns)) or any(not c for c in columns):raise ValueError("表头为空或重复")
    rows=list(reader)
    return [{"id":"0","name":"全文数据","columns":columns,"rows":rows,"warnings":[]}]


def select_table(data,kind,selection=None):
    tables=read_tables(data,kind);wanted=str((selection or {}).get("table_id","0"))
    current=next((table for table in tables if table["id"]==wanted),None)
    if not current:raise ValueError("工作表／采集段不存在")
    return current,[{"id":t["id"],"name":t["name"],"count":len(t["rows"])} for t in tables]
