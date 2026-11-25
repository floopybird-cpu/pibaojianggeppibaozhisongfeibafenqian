"""Resumable document composition, with formal publication through the gateway."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import threading

from .storage import canonical,now,uid
from .format_program import Program


class DocumentDrafts:
    def __init__(self,store,gateway):
        self.store,self.gateway=store,gateway
        self.directory=store.staging/"document-drafts"
        self.directory.mkdir(exist_ok=True)
        self.lock=threading.RLock()

    def _path(self,identity):
        if not isinstance(identity,str) or not identity.startswith("draft-") or not identity[6:].isalnum():
            raise ValueError("文档草稿标识无效")
        return self.directory/(identity+".json")

    def _write(self,record):
        target=self._path(record["id"]);temporary=target.with_suffix(".tmp")
        temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
        temporary.replace(target)

    def read(self,experiment_id,identity):
        record=json.loads(self._path(identity).read_text(encoding="utf-8"))
        if record["experiment_id"]!=experiment_id:raise ValueError("草稿不属于当前实验")
        return record

    def list(self,experiment_id):
        records=[]
        for file in self.directory.glob("draft-*.json"):
            record=json.loads(file.read_text(encoding="utf-8"))
            if record["experiment_id"]==experiment_id and record["status"]=="draft":
                records.append({k:record[k] for k in ("id","title","revision","updated","status")})
        return sorted(records,key=lambda r:r["updated"])

    def begin(self,experiment_id,content):
        self.store.experiment(experiment_id)
        pack=self.gateway.registry.owner("LearningDocument")
        allowed=Program(pack).schemas['LearningDocument']['properties']['doc_type']['enum']
        if not isinstance(content,dict) or not content.get("title") or content.get("doc_type") not in allowed:
            raise ValueError("草稿需有标题和格式包声明的文档类型："+'/'.join(allowed))
        record={"id":uid("draft"),"experiment_id":experiment_id,"title":content["title"],"revision":1,
                "pack_id":pack["id"],"pack_version":pack["version"],"pack_digest":pack["digest"],
                "content":copy.deepcopy(content),"status":"draft","updated":now()}
        record["content"].setdefault("sections",[]);record["content"].setdefault("appendices",[])
        with self.lock:self._write(record)
        return self.receipt(record)

    @staticmethod
    def receipt(record):
        return {k:record[k] for k in ("id","title","revision","status")}|{
            "sections":[s["id"] for s in record["content"]["sections"]],
            "appendices":[s["id"] for s in record["content"]["appendices"]]}

    def append(self,experiment_id,identity,base_revision,section,appendix=False,replace=False):
        with self.lock:
            record=self.read(experiment_id,identity)
            if record["status"]!="draft":raise ValueError("草稿已完成；请修改正式成果的新修订")
            if record["revision"]!=base_revision:raise ValueError("草稿基线已变化，请重新读取")
            from .format_program import Program,check_schema
            pack=self.gateway.registry.pinned(record["pack_id"],record["pack_version"],record["pack_digest"])
            if not pack:raise ValueError("草稿固定包缺失，保留内容等待恢复")
            schema=Program(pack).schemas["LearningDocument"]["properties"]["appendices" if appendix else "sections"]["items"]
            check_schema(section,schema,self.gateway.snapshot(section,experiment_id),"section")
            content=record["content"];items=content["appendices" if appendix else "sections"]
            old=next((i for i,s in enumerate(items) if s["id"]==section["id"]),None)
            if old is not None and not replace:raise ValueError("章节已存在；修改须显式 replace=true 并固定基线")
            if old is None:items.append(copy.deepcopy(section))
            else:items[old]=copy.deepcopy(section)
            # These are explicitly supplied source/reference identities from the
            # sections, not model-generated facts or guessed metadata.
            sources=set(content.get("sources",[]));refs={canonical(r):r for r in content.get("knowledge_refs",[])}
            def collect(value):
                if isinstance(value,dict):
                    sources.update(value.get("sources",[]))
                    if isinstance(value.get("id"),str) and isinstance(value.get("revision"),int):refs[canonical(value)]=value
                    for child in value.values():collect(child)
                elif isinstance(value,list):
                    for child in value:collect(child)
            collect(section)
            content["sources"]=sorted(sources);content["knowledge_refs"]=list(refs.values())
            record["revision"]+=1;record["updated"]=now();self._write(record)
            return self.receipt(record)

    def finish(self,experiment_id,identity,base_revision):
        with self.lock:
            record=self.read(experiment_id,identity)
            if record["status"]!="draft":return self.gateway.read(record["artifact_id"])
            if record["revision"]!=base_revision:raise ValueError("草稿基线已变化，请重新读取")
            pack=self.gateway.registry.pinned(record["pack_id"],record["pack_version"],record["pack_digest"])
            if not pack:raise ValueError("草稿固定包缺失，不能提交正式成果")
            result=self.gateway.apply("LearningDocument",record["content"],experiment_id=experiment_id,_target_pack=pack)
            record.update(status="published",artifact_id=result["id"],updated=now());self._write(record)
            return result
