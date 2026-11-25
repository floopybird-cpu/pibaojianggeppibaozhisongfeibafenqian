from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import re
import shutil

from .storage import digest


class Registry:
    def __init__(self,directory:Path,cache:Path|None=None):
        self.directory=directory.resolve();self.cache=cache
        self.packs={};self.snapshots={};self.errors=[]
        if cache:
            cache.mkdir(parents=True,exist_ok=True)
            for manifest in cache.glob("*/*/*/format.json"):
                try:
                    pack=self.load(manifest.parent)
                    if pack["digest"]!=manifest.parent.name:
                        raise ValueError("固定包缓存摘要不符")
                    self.snapshots[(pack["id"],pack["version"],pack["digest"])]=pack
                except Exception as exc:
                    self.errors.append({"package":manifest.parent.name,"error":str(exc)})
        for manifest in sorted(directory.glob("*/format.json")):
            try:
                pack=self.load(manifest.parent)
                old_hashes={key[2] for key in self.snapshots if key[:2]==(pack["id"],pack["version"])}
                if old_hashes and pack["digest"] not in old_hashes:
                    raise ValueError("已发布同版本字节发生变化；请发布新版本，不能覆盖固定包")
                if cache:
                    target=cache/pack["id"]/pack["version"]/pack["digest"]
                    if not target.exists():shutil.copytree(manifest.parent,target)
                    pack=self.load(target)
                self.snapshots[(pack["id"],pack["version"],pack["digest"])]=pack
                existing=self.packs.get(pack["id"])
                if not existing or tuple(map(int,pack["version"].split(".")))>tuple(map(int,existing["version"].split("."))):
                    self.packs[pack["id"]]=pack
            except Exception as exc:
                self.errors.append({"package":manifest.parent.name,"error":str(exc)})

    def load(self,directory):
        from .format_program import Program
        from .sandbox import output_files
        directory=directory.resolve()
        pack=json.loads((directory/"format.json").read_text(encoding="utf-8"))
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9.-]{1,90}",pack["id"]) or not re.fullmatch(r"\d+\.\d+\.\d+",pack["version"]):
            raise ValueError("包标识或发布版本无效")
        supported_data={1,2,3,4,5,6,7,8,9} if pack['id']=='physics.documents' else {1,2} if pack['id']=='physics.analysis' else {1,2,3} if pack['id'] in ('physics.data','physics.draft') else {1}
        supported_runtime={1,2,3,4,5,6,7} if pack['id']=='physics.documents' else {1,2} if pack['id']=='physics.analysis' else {1,2,3} if pack['id']=='physics.data' else {1,2,3,4} if pack['id']=='physics.draft' else {1}
        if pack["runtime_version"] not in supported_runtime or pack["data_version"] not in supported_data or pack.get("execution")!="bounded_declarative":
            raise ValueError("不兼容接口／数据版本或未隔离的执行类型")
        if not pack.get("object_types") or len(set(pack["object_types"]))!=len(pack["object_types"]):
            raise ValueError("对象声明缺失或重复")
        for entry in ("read","query","apply","build","finish","planUpgrade","upgrade"):
            if pack["entries"].get(entry)!="program.json#"+entry:
                raise ValueError("关键程序入口缺失 "+entry)
        icons={button.get("icon") for button in pack.get("ui",[])}
        for required in ["FORMAT.md","schema.json","program.json",*pack.get("resources",[])]:
            path=(directory/required).resolve()
            if not path.is_relative_to(directory) or (not path.is_file() and required not in icons):
                raise ValueError("包资源缺失或越界 "+required)
        for dependency,version in pack.get("dependencies",{}).items():
            if importlib.metadata.version(dependency)!=version:
                raise ValueError("依赖版本不符 "+dependency+"，需要 "+version)
        if not pack.get("ui"):raise ValueError("包缺用途与界面声明")
        for button in pack["ui"]:
            if not all(button.get(k) for k in ("label","description","location","activity","object_type")):
                raise ValueError("界面声明不完整")
            if button["location"] not in ("library","experiment","artifact") or button["activity"] not in ("distill","organize_data","prestudy","short","deep","plan","program","export","methods","outline","worksheet"):
                raise ValueError("宿主不支持此界面入口")
            icon=(directory/button.get("icon", "missing.svg")).resolve()
            if not icon.is_relative_to(directory):raise ValueError("图标越界")
            button["icon_available"]=icon.is_file()
        files=sorted(p for p,_ in output_files(directory))
        pack["digest"]=digest(b"".join(str(p.relative_to(directory)).encode()+b"\0"+p.read_bytes() for p in files))
        pack["directory"]=str(directory)
        program=Program(pack)
        if set(program.schemas)!=set(pack["object_types"]):raise ValueError("包结构与对象类型声明不一致")
        return pack

    def owner(self,kind):
        owners=[p for p in self.packs.values() if kind in p["object_types"]]
        if len(owners)!=1:raise ValueError("此对象类型没有唯一可用格式包")
        pack=owners[0]
        fixed=self.pinned(pack["id"],pack["version"],pack["digest"])
        if not fixed:raise ValueError("格式包字节已变化，拒绝使用旧摘要写入")
        return fixed

    def get(self,pack_id):
        if pack_id not in self.packs:raise ValueError("格式包不可用："+str(pack_id))
        pack=self.packs[pack_id]
        fixed=self.pinned(pack_id,pack["version"],pack["digest"])
        if not fixed:raise ValueError("格式包固定摘要不符")
        return fixed

    def pinned(self,pack_id,version,sha):
        pack=self.snapshots.get((pack_id,version,sha))
        if not pack:return None
        try:
            current=self.load(Path(pack["directory"]))
            return current if current["digest"]==sha else None
        except Exception:
            return None

    def public(self):
        return {"packages":[{**{k:v for k,v in p.items() if k!="directory"},"instructions":(Path(p["directory"])/"FORMAT.md").read_text(encoding="utf-8")} for p in self.packs.values()],"errors":self.errors}
