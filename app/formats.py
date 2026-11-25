from __future__ import annotations

import json
from pathlib import Path
import copy
import shutil
import threading
import math

from .storage import Store, canonical, digest, now, uid


class RevisionConflict(ValueError):
    pass


from .registry import Registry
from .format_program import Program, Snapshot


class FormatGateway:
    def __init__(self, store: Store, registry: Registry):
        self.store, self.registry = store, registry
        self.document_config = {}
        self.runtime_environment = None
        self.publications = None
        self._workflow = threading.local()
        self.draft_manager = None
        self.store.execute('''CREATE TABLE IF NOT EXISTS artifact_draft_basis (
            object_id TEXT NOT NULL, revision INTEGER NOT NULL, draft_id TEXT NOT NULL,
            draft_revision INTEGER NOT NULL, PRIMARY KEY(object_id,revision))''')

    def read(self, object_id, revision=None):
        obj = self.store.object(object_id, revision)
        basis = self.store.one('SELECT draft_id,draft_revision FROM artifact_draft_basis WHERE object_id=? AND revision=?', (object_id,obj['revision']))
        if basis:obj['draft_basis']={'id':basis['draft_id'],'revision':basis['draft_revision']}
        pack = self.registry.pinned(obj["pack_id"],obj["pack_version"],obj["pack_digest"])
        obj["package_state"] = "ready" if pack else "ordinary_read_only"
        if pack:
            obj["semantic_view"]=Program(pack).execute("read",kind=obj["kind"],content=obj["content"])
        obj["history"] = self.store.all("SELECT revision,pack_version,pack_digest,created,human_baseline FROM revisions WHERE object_id=? ORDER BY revision DESC", (object_id,))
        pagination=next((a for a in obj['assets'] if a['name']=='pagination.json'),None)
        if pagination:
            path=self.store.asset_path(pagination['path'])
            if path.is_file() and digest(path.read_bytes())==pagination['sha']:obj['layout']=json.loads(path.read_text('utf-8'))
        if self.publications and (obj["experiment_id"] or obj["kind"]=="DataWorkbook"):
            obj["publication"] = self.publications.info(obj)
        return obj

    def query(self, query="", kind=None, category_id=None, offset=0, limit=None):
        conditions, args = ["1=1"], []
        if kind:
            conditions.append("o.kind=?"); args.append(kind)
        if category_id:
            conditions.append("EXISTS(SELECT 1 FROM memberships m WHERE m.object_id=o.id AND m.category_id=?)"); args.append(category_id)
        if query:
            conditions.append("(o.title LIKE ? OR r.content LIKE ?)"); args.extend(["%" + query + "%"] * 2)
        args.extend([-1 if limit is None else max(1,int(limit)), max(0, int(offset))])
        rows = self.store.all("SELECT o.id,o.kind,o.title,o.revision,o.experiment_id,r.content FROM objects o JOIN revisions r ON o.id=r.object_id AND o.revision=r.revision WHERE " + " AND ".join(conditions) + " ORDER BY o.created DESC LIMIT ? OFFSET ?", args)
        for row in rows:
            row["content"] = json.loads(row["content"])
            row["categories"] = self.store.all("SELECT c.* FROM memberships m JOIN categories c ON c.id=m.category_id WHERE m.object_id=?", (row["id"],))
            fixed=self.store.object(row["id"])
            pack=self.registry.pinned(fixed["pack_id"],fixed["pack_version"],fixed["pack_digest"])
            if pack:row["semantic_view"]=Program(pack).execute("query",kind=row["kind"],content=row["content"])
        return rows

    def _ref(self, value, expected=None):
        if not isinstance(value, dict) or not value.get("id") or not isinstance(value.get("revision"), int):
            raise ValueError("引用必须固定 id 和 revision")
        obj = self.store.object(value["id"], value["revision"])
        if expected and obj["kind"] not in expected:
            raise ValueError("引用对象类型不符")
        return obj

    def find_text(self,object_id,text):
        if not text:raise ValueError("请输入要定位的原文")
        found=[]
        def walk(value,path):
            if isinstance(value,dict):
                for key,child in value.items():walk(child,path+"/"+str(key).replace("~","~0").replace("/","~1"))
            elif isinstance(value,list):
                for i,child in enumerate(value):walk(child,path+"/"+str(i))
            elif isinstance(value,str) and text in value:found.append({"path":path,"value":value,"matches":value.count(text)})
        obj=self.read(object_id);walk(obj["content"],"")
        return {"id":object_id,"revision":obj["revision"],"occurrences":found}

    def edit(self,object_id,base_revision,operations,*,human=False):
        old=self.read(object_id)
        if old["kind"]=="DataWorkbook":raise ValueError("规范测量表须通过资料整理服务或真实用户问题答复更新，不能直接编辑确认历史")
        if old["revision"]!=base_revision:raise RevisionConflict("基线修订已变化，请重读后编辑")
        content=self.edited_content(old,operations)
        return self.apply(old["kind"],content,experiment_id=old["experiment_id"],object_id=object_id,base_revision=base_revision,human=human)

    def edited_content(self,old,operations):
        content=copy.deepcopy(old["content"])
        pack=self.registry.pinned(old["pack_id"],old["pack_version"],old["pack_digest"])
        if not pack:raise ValueError("固定包缺失；仍可普通阅读，不能编辑")
        program=Program(pack)
        roots=set(program.schemas[old["kind"]]["properties"])
        for operation in operations:
            pointer=operation.get("path","")
            if not pointer.startswith("/"):raise ValueError("编辑路径须用 /sections/0/blocks/1/text 这类 JSON Pointer")
            parts=[v.replace("~1","/").replace("~0","~") for v in pointer[1:].split("/")]
            if parts[0] not in roots:raise ValueError("编辑根字段未在固定包中声明")
            if operation["op"]=="increase_writing_space":
                if old["kind"]!="LearningDocument" or parts!=["style"]:
                    raise ValueError("增加手写空间只用于文稿的 /style；value 是每行增加的毫米数")
                increment=operation["value"]
                if isinstance(increment,bool) or not isinstance(increment,(int,float)) or not math.isfinite(increment) or increment<=0:
                    raise ValueError("每行增加的毫米数必须为有限正数")
                from .document_composition import load_design
                template=next((s["template"] for s in program.definition["build"] if s["op"]=="docx"),None)
                if not template:raise ValueError("本固定格式未提供可相对调整的文稿样式；请先预览格式升级")
                _,design=load_design(content,Path(pack["directory"])/template)
                # Two vertical cell margins contribute to each row's height.
                # Resolve the actual pinned theme and overrides, not a guessed default.
                content.setdefault("style",{})["table_padding_vertical_pt"]=design["table_padding_vertical_pt"]+increment*72/25.4/2
                continue
            parent=content
            try:
                for part in parts[:-1]:parent=parent[int(part)] if isinstance(parent,list) else parent[part]
                key=int(parts[-1]) if isinstance(parent,list) else parts[-1]
                if operation["op"]=="set":parent[key]=copy.deepcopy(operation["value"])
                elif operation["op"]=="append":
                    if not isinstance(parent[key],list):raise ValueError("append 的目标须为包声明的列表")
                    parent[key].append(copy.deepcopy(operation["value"]))
                elif operation["op"]=="replace_text":
                    value=parent[key];before=operation["old"]
                    if not isinstance(value,str) or not before:raise ValueError("替换目标和原文必须是非空文本")
                    count=value.count(before)
                    if count!=operation.get("expected_matches",1):raise RevisionConflict(f"原文匹配次数为{count}，请先定位原文并固定次数")
                    parent[key]=value.replace(before,operation["value"])
                else:raise ValueError("仅支持 set、append、replace_text 和 increase_writing_space 领域内容编辑")
            except (KeyError,IndexError,TypeError) as exc:raise ValueError("编辑路径或值无效："+pointer) from exc
        return content

    def snapshot(self, content, experiment_id, *, source_issues=()):
        references, anchor_ids = {}, {}
        issues=list(source_issues)
        def walk(value,path=''):
            if isinstance(value, dict):
                if isinstance(value.get("id"),str) and isinstance(value.get("revision"),int):
                    key=(value["id"],value["revision"])
                    references[key]=self.store.object(*key)
                for key,item in value.items():
                    if key=="sources" and isinstance(item,list):
                        for i,v in enumerate(item):
                            if isinstance(v,str):anchor_ids.setdefault(v,[]).append(f'{path}/sources/{i}')
                    elif key=="source_anchor" and isinstance(item,str):
                        anchor_ids.setdefault(item,[]).append(path+'/source_anchor')
                    else:walk(item,path+'/'+key.replace('~','~0').replace('/','~1'))
            elif isinstance(value,list):
                for i,item in enumerate(value):walk(item,f'{path}/{i}')
        walk(content)
        experiment=self.store.experiment(experiment_id) if experiment_id else None
        for obj in references.values():
            if obj["experiment_id"] and obj["experiment_id"]!=experiment_id:
                raise ValueError("固定引用属于其他实验")
        anchors={}
        for anchor_id in anchor_ids:
            try:anchors[anchor_id]=self.store.anchor(anchor_id)
            except (KeyError,ValueError):
                issues.append({'code':'unknown_anchor','anchor':anchor_id,'message':'无效来源锚点 '+anchor_id,'paths':anchor_ids[anchor_id]})
                continue
            anchor=anchors[anchor_id]
            if anchor.get("quote") and anchor.get("region"):
                original=self.store.one("SELECT text FROM pages WHERE source_id=? AND number=?",(anchor["source_id"],anchor["page"]))
                anchor["quote_in_original"]=bool(original and anchor["quote"] in original["text"])
        # A background reading can cite verified pages of the fixed library entries it uses.
        library_anchors=set()
        if content.get("reading_scope") and content.get("doc_type")=="prestudy":
            from .knowledge_index import KINDS, within_scope
            for ref in content.get("knowledge_refs",[]):
                card=references[(ref["id"],ref["revision"])]
                if card["experiment_id"] is None and card["kind"] in KINDS:
                    library_anchors.update(card["content"].get("sources",[]))
        pages,images={},{}
        for anchor in anchors.values():
            if experiment and anchor["source_id"] not in experiment["source_ids"] and anchor["metadata"].get("origin")!="公开联网补充":
                # Data sources are also permitted for analysis outputs, when
                # explicitly associated with this same experiment.
                linked=self.store.one("SELECT id FROM datasets WHERE experiment_id=? AND source_id=? AND confirmed=1",(experiment_id,anchor["source_id"]))
                verified_library = (anchor["id"] in library_anchors and
                                    within_scope(self.store,anchor["source_id"],anchor["page"]))
                if not linked and not verified_library:
                    aid=anchor['id']
                    issues.append({'code':'unselected_source','anchor':aid,'message':'请先在实验设置选用此教材／确认本实验数据：'+aid,'paths':anchor_ids[aid]})
                    continue
        if issues:
            from .source_citations import SourceValidationError
            raise SourceValidationError(issues)
        for key in dict.fromkeys((a["source_id"],a["page"]) for a in anchors.values()):
            page=self.store.one("SELECT image FROM pages WHERE source_id=? AND number=?",key)
            pages[key]=page
            if page and page["image"]:images[page["image"]]=self.store.asset_path(page["image"])
        # Freeze only geometry for requested illustrations. Rendering receives
        # the same finite page/anchor snapshot, never a path to a course PDF.
        from .document_composition import all_blocks
        from .page_navigation import figure_regions
        for block in all_blocks(content):
            if block.get('type') != 'figure' or block.get('framing') == 'exact':
                continue
            anchor=anchors.get(block.get('source_anchor'))
            if not anchor or not anchor['region']:
                continue
            key=(anchor['source_id'], anchor['page'])
            page=pages.get(key)
            if page and 'figure_geometry' not in page:
                try:page['figure_geometry']=figure_regions(self.store,*key)
                except (OSError, ValueError, RuntimeError):page['figure_geometry']={}
        # Experiment identity and data ownership are host-envelope rules. The
        # format program receives this finite snapshot, never the Store object.
        datasets={}
        if isinstance(content.get("inputs"),dict):
            for dataset_id in content["inputs"].get("dataset_ids",[]):
                data=self.store.one("SELECT * FROM datasets WHERE id=?",(dataset_id,))
                if not data or data["experiment_id"]!=experiment_id or not data["confirmed"]:
                    raise ValueError("数据不属于当前实验或尚未确认列含义与单位")
                source=self.store.one("SELECT * FROM sources WHERE id=?",(data["source_id"],))
                from .tabular import select_table
                selection=json.loads(data["selection"])
                table,_=select_table(self.store.asset_path(source["original"]).read_bytes(),source["kind"],selection)
                datasets[dataset_id]={"id":dataset_id,"source_id":source["id"],"name":source["name"],"source_sha":source["sha"],"mapping":json.loads(data["mapping"]),"selection":selection,"confirmed":True,"rows":table["rows"]}
        active_columns={column for data in datasets.values() for column,value in data["mapping"].items() if value.get("use_for_analysis",True)}
        unused_columns={column for data in datasets.values() for column,value in data["mapping"].items() if not value.get("use_for_analysis",True)}
        declared_columns={column for item in content.get("formula_map",[]) for column in item.get("input_columns",[])}
        if declared_columns & (unused_columns-active_columns):raise ValueError("公式映射引用了本次不用于计算的原列；先核明并重新启用该列")
        return Snapshot(references,anchors,pages,images,experiment,datasets)

    def apply(self, kind, content, *, experiment_id=None, object_id=None, base_revision=0, human=False, extra_assets=None, _target_pack=None):
        if not isinstance(content,dict):raise ValueError("content 必须是完整字段对象；修改部分内容可用 edit_artifact")
        previous = self.read(object_id) if object_id else None
        if previous:
            if previous["package_state"] != "ready":
                raise ValueError("固定包缺失／摘要变化：仍可普通阅读，恢复固定包后才能修改")
            if previous["kind"] != kind or previous["experiment_id"] != experiment_id:
                raise ValueError("不能改变成果类型或实验绑定")
            if previous["revision"] != base_revision:
                raise RevisionConflict("基线修订已变化，请重读后再提交")
        elif base_revision:
            raise RevisionConflict("修改已有成果必须在工具顶层同时提供 id 和 base_revision；新增成果基线为 0")
        pack = _target_pack or (self.registry.pinned(previous["pack_id"],previous["pack_version"],previous["pack_digest"]) if previous else self.registry.owner(kind))
        if not pack:raise ValueError("固定格式程序不可用")
        if kind == "LearningDocument":
            preferences={k:v for k,v in self.document_config.items() if v}
            content={**content,"style":{**preferences,**content.get("style",{})}}
        pack_id=pack["id"]
        if not isinstance(content,dict) or not isinstance(content.get("title"),str) or not content["title"].strip():
            raise ValueError("正式成果缺少标题")
        program=Program(pack)
        binding=getattr(self._workflow,'draft_binding',None)
        if binding and kind in ('LearningDocument','AnalysisProgram') and self.draft_manager:
            # New packages carry the link in their declared content. Legacy
            # fixed packages keep their bytes/schema; the host envelope records
            # the same fixed basis without rewriting historical contracts.
            if 'draft_ref' in program.schemas[kind].get('properties',{}):
                content=self.draft_manager.attach(content,binding,kind)
        source_issues=[]
        if kind=='LearningDocument' and any(rule.get('check')=='composition' for step in program.definition['apply'] for rule in step.get('checks',[])):
            from .document_composition import missing_evidence
            source_issues=missing_evidence(content,program.schemas[kind])
        snapshot=self.snapshot(content,experiment_id,source_issues=source_issues)
        content=program.execute("apply",kind=kind,content=content,snapshot=snapshot,extra={"kind":kind,"runtime_environment":self.runtime_environment})
        sources=list(dict.fromkeys(content.get("sources",[])))
        identity = object_id or uid("obj")
        # Content-addressed revisions: identical replay never creates a new revision.
        if previous and canonical(previous["content"]) == canonical(content) and not extra_assets and not _target_pack and (not binding or previous.get('draft_basis')==binding):
            if self.publications:
                previous["publication"] = self.publications.on_saved(previous)
            return previous
        revision = base_revision + 1
        stage = self.store.staging / uid("build")
        stage.mkdir()
        try:
            program.execute("build",kind=kind,content=content,snapshot=snapshot,out=stage)
            if extra_assets:
                for name, data in extra_assets.items():
                    relative=Path(name)
                    if relative.is_absolute() or relative.drive or ".." in relative.parts:
                        raise ValueError("非法暂存资产名")
                    target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            program.execute("finish",kind=kind,content=content,snapshot=snapshot,out=stage)
            assets = []
            from .sandbox import output_files
            for file,_ in output_files(stage):
                data = file.read_bytes()
                sha = digest(data)
                target = self.store.assets / (sha + file.suffix)
                if not target.exists():
                    target.write_bytes(data)
                assets.append({"name": file.relative_to(stage).as_posix(), "path": str(target.relative_to(self.store.authority)), "sha": sha, "bytes": len(data)})
            with self.store.transaction() as conn:
                current = conn.execute("SELECT revision FROM objects WHERE id=?", (identity,)).fetchone()
                if (current[0] if current else 0) != base_revision:
                    raise RevisionConflict("提交时发现修订冲突，旧成果未变化")
                if current:
                    conn.execute("UPDATE objects SET revision=?,title=? WHERE id=?", (revision, content["title"], identity))
                else:
                    conn.execute("INSERT INTO objects VALUES(?,?,?,?,?,?)", (identity, kind, experiment_id, revision, content["title"], now()))
                conn.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (identity, revision, kind, pack_id, pack["version"], pack["digest"],
                             pack["data_version"], canonical(content), canonical(sources), canonical(assets), digest(canonical(content)) if human else (previous or {}).get("human_baseline"), now()))
                from . import knowledge_index as kb
                if experiment_id is None and kind in kb.KINDS:
                    kb.write_card(conn,identity,revision,content)
                    origin=getattr(self._workflow,"knowledge_origin",None)
                    if origin:
                        conn.execute("INSERT INTO kb_notes VALUES(?,?,?)",(*origin,identity))
                if binding and kind in ('LearningDocument','AnalysisProgram','AnalysisResult'):
                    conn.execute('INSERT INTO artifact_draft_basis VALUES(?,?,?,?)',(identity,revision,binding['id'],binding['revision']))
                if experiment_id is None:
                    category_ids = content.get("category_ids", [])
                    if not category_ids and content.get("domains"):
                        from .knowledge_seed import LEGACY, physics_id
                        labels=content["domains"]
                        category_ids=[r["id"] for r in conn.execute("""
                            SELECT MIN(c.id) AS id FROM categories c LEFT JOIN categories p ON p.id=c.parent
                            WHERE (c.parent IS NULL OR p.parent IS NULL) AND c.name IN (SELECT value FROM json_each(?))
                            GROUP BY c.name HAVING COUNT(*)=1""", (canonical(labels),))]
                        if not category_ids and any(tag in LEGACY for tag in labels):
                            category_ids=[physics_id(conn)]
                    conn.execute("DELETE FROM memberships WHERE object_id=?", (identity,))
                    for category in category_ids:
                        if not conn.execute("SELECT id FROM categories WHERE id=?", (category,)).fetchone():
                            raise ValueError("目录不存在")
                        conn.execute("INSERT INTO memberships VALUES(?,?)", (identity, category))
                    if kind in kb.KINDS:
                        conn.execute("""INSERT OR IGNORE INTO memberships SELECT object_id,category_id FROM kb_topic_members
                            WHERE object_id=? AND revision=?""",(identity,revision))
                if experiment_id:
                    exp = self.store.experiment(experiment_id)
                    refs = {canonical(ref): ref for ref in exp["knowledge_refs"] + content.get("knowledge_refs", [])}
                    conn.execute("UPDATE experiments SET knowledge_refs=?,updated=? WHERE id=?", (canonical(list(refs.values())), now(), experiment_id))
            result = self.read(identity)
            if self.publications:
                result["publication"] = self.publications.on_saved(result)
            return result
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def import_knowledge(self, kind, content, merge_target=None):
        """Host merge preserves old assertions and explicitly exposes differences."""
        from .knowledge_index import signature as knowledge_signature
        ids=self.store.all("""SELECT c.object_id FROM kb_cards c JOIN objects o ON o.id=c.object_id
            AND o.revision=c.revision WHERE o.kind=? AND c.signature=?""",(kind,knowledge_signature(content)))
        candidates = [self.store.object(row["object_id"]) for row in ids]
        policy=Program(self.registry.owner(kind)).definition.get("merge",{})
        excluded=policy.get("identity_exclude",[])
        signature = {k:v for k,v in content.items() if k not in excluded}
        for candidate in candidates:
            other = {k:v for k,v in candidate["content"].items() if k not in excluded}
            if canonical(signature) == canonical(other):
                merge_target = candidate["id"]
                break
        if merge_target:
            old = self.read(merge_target)
            if old["kind"] != kind or old["experiment_id"]:
                raise ValueError("此类型不能归并到该对象")
            merged = dict(old["content"])
            for field in policy.get("union",[]):
                if field in content:
                    combined = merged.get(field, []) + content[field]
                    merged[field] = list({canonical(v):v for v in combined}.values())
            for field in policy.get("conflict_fields",[]):
                if field in content and canonical(content[field]) != canonical(merged.get(field)):
                    conflict = {"field": field, "existing": merged.get(field), "incoming": content[field], "incoming_sources": content["sources"], "status": "pending"}
                    if canonical(conflict) not in [canonical(c) for c in merged.get("conflicts", [])]:
                        merged.setdefault("conflicts", []).append(conflict)
            return self.apply(kind, merged, object_id=old["id"], base_revision=old["revision"])
        return self.apply(kind, content)

    def category(self, name, parent=None, domain=None, category_id=None):
        """Names and parentage are editable. Object references use stable IDs."""
        name=name.strip()
        if not name:raise ValueError("请填写目录名称")
        with self.store.transaction() as conn:
            current=conn.execute("SELECT * FROM categories WHERE id=?",(category_id,)).fetchone() if category_id else None
            if category_id and not current:raise ValueError("要修改的目录不存在")
            if parent:
                from .knowledge_seed import legacy_parent
                parent=legacy_parent(conn,parent)
            dest=conn.execute("SELECT * FROM categories WHERE id=?",(parent,)).fetchone() if parent else None
            if parent and not dest:raise ValueError("上级目录不存在")
            if current and parent:
                cycle=conn.execute("""WITH RECURSIVE sub(id) AS (SELECT ? UNION SELECT c.id FROM categories c JOIN sub ON c.parent=sub.id)
                    SELECT 1 FROM sub WHERE id=?""",(category_id,parent)).fetchone()
                if cycle:raise ValueError("不能将目录移入自己或自己的下级")
            same=conn.execute("SELECT id FROM categories WHERE parent IS ? AND name=?",(parent,name)).fetchone()
            if same and current and same["id"]!=category_id:raise ValueError("目标位置已有同名目录")
            if same and not current:return {"id":same["id"]}
            identity=category_id or uid("cat")
            domain_key=dest["domain"] if dest else (current["domain"] if current else name)
            if current:
                conn.execute("UPDATE categories SET name=?,parent=?,domain=? WHERE id=?",(name,parent,domain_key,identity))
                if domain_key!=current["domain"]:
                    conn.execute("""WITH RECURSIVE sub(id) AS (SELECT ? UNION SELECT c.id FROM categories c JOIN sub ON c.parent=sub.id)
                        UPDATE categories SET domain=? WHERE id IN (SELECT id FROM sub)""",(identity,domain_key))
                from .knowledge_index import index_topics
                index_topics(conn,rebuild=True)
            else:
                conn.execute("INSERT INTO categories(id,parent,name,domain) VALUES(?,?,?,?)",(identity,parent,name,domain_key))
        return {"id":identity}

    def plan_upgrade(self, object_id):
        old = self.read(object_id)
        pack = self.registry.packs.get(old["pack_id"])
        preview=Program(pack).execute("planUpgrade",kind=old["kind"],content=old["content"]) if pack else {}
        plan={"object_id": object_id, "baseline_revision": old["revision"], "old_version": old["pack_version"],
                "target_version": pack["version"] if pack else None, "human_modified": bool(old["human_baseline"]),
                "old_digest":old["pack_digest"],"target_digest":pack["digest"] if pack else None,
                "can_upgrade": bool(pack and old["package_state"]=="ready" and pack["digest"]!=old["pack_digest"]),
                "preview":preview,"reason": "已是当前固定版本" if pack and pack["digest"]==old["pack_digest"] else "将保留原内容与旧修订，按目标包重生成；新增事实需另做内容工作" if pack else "固定包不可用，可普通阅读原交付"}
        plan["plan_digest"]=digest(canonical(plan))
        if preview.get("requires_content_work"):
            plan["can_upgrade"]=False;plan["reason"]="目标格式需要新增内容，不能当样式迁移补造："+", ".join(preview["missing_fields"])
            plan.pop("plan_digest");plan["plan_digest"]=digest(canonical(plan))
        return plan

    def upgrade(self,object_id,plan,preserve_human=False):
        expected=self.plan_upgrade(object_id)
        if canonical(expected)!=canonical(plan):raise RevisionConflict("升级方案或成果基线已变化，请重新预览")
        if not plan["can_upgrade"]:raise ValueError(plan["reason"])
        old=self.read(object_id)
        if old["human_baseline"] and not preserve_human:raise ValueError("含人工修改，需明确选择保留后再升级")
        target=self.registry.pinned(old["pack_id"],plan["target_version"],plan["target_digest"])
        if not target:raise ValueError("目标包固定字节不可用")
        migrated=Program(target).execute("upgrade",kind=old["kind"],content=old["content"])
        return self.apply(old["kind"],migrated,experiment_id=old["experiment_id"],object_id=object_id,base_revision=old["revision"],_target_pack=target)
