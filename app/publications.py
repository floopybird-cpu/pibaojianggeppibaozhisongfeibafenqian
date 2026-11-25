"""Human-readable, disposable copies, separate from the authority/backup store."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import queue
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager

from .storage import canonical, digest, now


def filename(value, maximum=54):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")
    if value.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *("COM"+str(i) for i in range(1,10)), *("LPT"+str(i) for i in range(1,10))}:
        value = "文稿 " + value
    return value[:maximum].rstrip(" .") or "未命名成果"


def category(obj):
    if obj["kind"] == "LearningDocument":
        if obj["content"].get("reading_scope"): return "背景阅读"
        return {"prestudy":"预习文稿","short":"预习文稿","deep":"预习文稿","plan":"实验单","record_sheet":"现场记录表"}[obj["content"]["doc_type"]]
    return {"ExperimentDraft":"实验草稿", "ExperimentWorksheet":"数据处理台", "AnalysisProgram": "数据处理程序", "AnalysisResult": "计算结果", "DataWorkbook":"规范数据表"}.get(obj["kind"], "其他成果")


class Publications:
    def __init__(self, store, settings, default_root):
        self.store, self.settings = store, settings
        self.default_root = Path(default_root).resolve()
        self.lock = threading.RLock()
        self.open_requests = queue.SimpleQueue()
        self._work = threading.local()
        store.execute("CREATE TABLE IF NOT EXISTS publications (object_id TEXT NOT NULL,revision INTEGER NOT NULL,root TEXT NOT NULL,folder TEXT,status TEXT NOT NULL,files TEXT NOT NULL,error TEXT,updated TEXT NOT NULL,PRIMARY KEY(object_id,revision,root))")

    @contextmanager
    def working(self):
        previous=getattr(self._work,'active',False)
        self._work.active=True
        try:yield
        finally:self._work.active=previous

    @property
    def root(self):
        configured = self.settings.read().get("products_directory", "")
        root = Path(configured).expanduser().resolve() if configured else self.default_root
        self.check_root(root)
        return root

    def check_root(self, root):
        root = Path(root).resolve()
        if root.is_relative_to(self.store.root) or self.store.authority.is_relative_to(root):
            raise ValueError("产物文件夹须在内部资料记录目录之外，请选择一个单独的文件夹")
        return root

    def experiment_folder(self, experiment_id):
        with self.lock:
            target=self._experiment_folder(experiment_id)
            target.mkdir(parents=True,exist_ok=True)
            return target

    def _experiment_folder(self, experiment_id):
        self.store.experiment(experiment_id)
        root = self.root
        key = "products-folder:" + digest(str(root)) + ":" + experiment_id
        record = self.store.one("SELECT value FROM meta WHERE key=?", (key,))
        if record:
            relative = record["value"]
        else:
            base = filename(self.store.experiment(experiment_id)["title"], 42)
            reserved = {r["value"] for r in self.store.all("SELECT value FROM meta WHERE key LIKE ?", ("products-folder:" + digest(str(root)) + ":%",))}
            relative = base
            count = 1
            while relative in reserved or (root / relative).exists():
                count += 1
                relative = base + f"（{count}）"
            self.store.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, relative))
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or target.is_relative_to(self.store.authority):
            raise ValueError("产物文件夹指向了内部记录或其他位置，请重新选择")
        return target

    def info(self, obj):
        record = self.store.one("SELECT * FROM publications WHERE object_id=? AND revision=? AND root=?", (obj["id"], obj["revision"], str(self.root)))
        latest=self.store.one('SELECT revision FROM objects WHERE id=?',(obj['id'],))['revision']
        if not record:
            return {"status": "not_published", "title": obj["title"],"revision":obj['revision'],"is_current":obj['revision']==latest, "message": "尚未放入阅读文件夹，可随时从内部记录生成"}
        result = {"status": record["status"], "title": obj["title"], "revision":obj['revision'],"is_current":obj['revision']==latest,"folder": record["folder"], "files": json.loads(record["files"]), "error": record["error"]}
        if result['status']=='working':result['message']='写作中，内部可读；本任务完成后更新产物副本'
        if result["status"] == "ready":
            folder = Path(record["folder"])
            result["available"] = folder.is_dir() and all((folder / f["name"]).is_file() for f in result["files"])
            if not result["available"]:
                result["message"] = "外部文件已移走或删除；内部版本保留，可重新放一份"
        return result

    def save_record(self, obj, folder, files, status="ready", error=None):
        self.store.execute("INSERT OR REPLACE INTO publications VALUES(?,?,?,?,?,?,?,?)", (obj["id"], obj["revision"], str(self.root), str(folder) if folder else None, status, canonical(files), error, now()))

    def publish(self, obj):
        if obj["kind"] not in ("ExperimentDraft", "ExperimentWorksheet", "LearningDocument", "AnalysisProgram", "AnalysisResult", "DataWorkbook") or (not obj.get("experiment_id") and obj["kind"]!="DataWorkbook"):
            return {"status": "not_applicable"}
        with self.lock:
            parent = (self.experiment_folder(obj["experiment_id"]) if obj.get("experiment_id") else self.root/"跨实验原始记录整理") / category(obj)
            base = filename(obj["title"])
            # Internal IDs never become the browsing hierarchy. Different
            # objects with the same human title get ordinary numbered folders.
            existing = self.info(obj)
            previous=None;preferred_target=None
            # The reading folder shows the latest file. Historical versions
            # remain internal, rather than accumulating backup folders here.
            # Only byte-identical copies we previously wrote may be refreshed.
            if existing.get("status")!="ready" and obj["revision"]==self.store.object(obj["id"])["revision"]:
                row=self.store.one("SELECT * FROM publications WHERE object_id=? AND root=? AND status='ready' ORDER BY revision DESC LIMIT 1",(obj["id"],str(self.root)))
                if row and row["folder"]:
                    prior_folder=Path(row["folder"]);prior_files=json.loads(row["files"])
                    prior={f["name"]:f["sha256"] for f in prior_files}
                    if prior_folder.resolve().is_relative_to(self.root) and (not prior_folder.exists() or all(not p.is_file() or (p.relative_to(prior_folder).as_posix() in prior and digest(p.read_bytes())==prior[p.relative_to(prior_folder).as_posix()]) for p in prior_folder.rglob("*"))):
                        previous={"folder":str(prior_folder),"files":prior_files,"revision":row["revision"]}
                        existing={"folder":str(prior_folder),"files":prior_files,"status":"refreshing"}
                        desired=(parent/base).resolve()
                        if desired.exists() and desired!=prior_folder.resolve():
                            desired=(parent/(base+f"（第{obj['revision']}版）")).resolve()
                        if prior_folder.exists() and prior_folder.resolve()!=desired and not desired.exists():
                            # Only rename an unchanged disposable copy, within
                            # the selected products root. An open/manual file
                            # keeps its existing location and is never removed.
                            if not desired.is_relative_to(self.root):raise ValueError('新标题的产物目录越界')
                            desired.parent.mkdir(parents=True,exist_ok=True)
                            try:prior_folder.rename(desired)
                            except OSError:
                                preferred_target=desired;previous=None;existing={}
                            else:
                                self.store.execute('UPDATE publications SET folder=?,updated=? WHERE object_id=? AND root=? AND folder=?',
                                                   (str(desired),now(),obj['id'],str(self.root),str(prior_folder)))
                                previous['folder']=str(desired);existing['folder']=str(desired)
            target = Path(existing["folder"]) if existing.get("folder") else preferred_target or parent / base
            if not target.resolve().is_relative_to(self.root):
                raise ValueError("产物目录越界")
            if existing.get("status") == "ready" and existing.get("available"):
                if all(digest((target / f["name"]).read_bytes()) == f["sha256"] for f in existing["files"]):
                    return existing
            if target.exists():
                # Never overwrite someone's edits, even when regenerating the
                # same fixed version. Missing files can be restored in place.
                prior = {f["name"]: f["sha256"] for f in existing.get("files", [])}
                changed = not prior or any(p.is_file() and (p.relative_to(target).as_posix() not in prior or digest(p.read_bytes()) != prior[p.relative_to(target).as_posix()]) for p in target.rglob("*"))
                if changed:
                    number = 1
                    target = parent / (base + f"（第{obj['revision']}版）")
                    while target.exists():
                        number += 1
                        target = parent / (base + f"（第{obj['revision']}版·副本{number}）")
            self._write(obj, target)
            if previous and str(target)==previous["folder"]:
                current={f["name"] for f in self.info(obj)["files"]}
                for file in previous["files"]:
                    path=(target/file["name"]).resolve()
                    if file["name"] not in current and path.is_relative_to(self.root) and path.is_file() and digest(path.read_bytes())==file["sha256"]:
                        try:path.unlink()
                        except OSError:pass  # An open old copy remains readable.
                self.store.execute("UPDATE publications SET status='superseded',folder=NULL,files='[]',updated=? WHERE object_id=? AND revision<>? AND root=? AND folder=?",(now(),obj["id"],obj["revision"],str(self.root),str(target)))
            if obj['revision']==self.store.one('SELECT revision FROM objects WHERE id=?',(obj['id'],))['revision']:
                self.store.execute("UPDATE publications SET status='retained',updated=? WHERE object_id=? AND revision<>? AND root=? AND status='ready' AND folder<>?",(now(),obj['id'],obj['revision'],str(self.root),str(target)))
            return self.info(obj)

    def _write(self, obj, target):
        if target.resolve().is_relative_to(self.store.root):
            raise ValueError("不能将外部产物写回内部记录")
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="physics-products-", dir=self.store.staging) as temporary:
            stage = Path(temporary)
            is_document = obj["kind"] == "LearningDocument"
            document_base=filename(obj['title'])+f"（第{obj['revision']}版）"
            for asset in obj["assets"]:
                source = self.store.asset_path(asset["path"])
                if digest(source.read_bytes()) != asset["sha"]:
                    raise ValueError("内部文件摘要不符，未发布：" + asset["name"])
                name = asset["name"]
                # Markdown/HTML are internal representations, not a substitute
                # for the actual document a student should receive.
                composed=is_document and any(a['name']=='design.json' for a in obj['assets'])
                if is_document and (name=='preview.html' or name=='document.md' and not composed):
                    continue
                if is_document and name in ("document.docx", "document.pdf"):
                    name = document_base + Path(name).suffix
                elif composed and name in ('document.md','design.json','composition.json','sources.json','pagination.json') or composed and name.startswith('figure-'):
                    name='文稿源文件/'+name
                destination = (stage / name).resolve()
                if not destination.is_relative_to(stage):
                    raise ValueError("成果文件路径越界")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            if is_document:
                pdf = stage / (document_base + ".pdf")
                if not pdf.exists():
                    # Old fixed packages required only DOCX. A portable reading
                    # copy can be rebuilt without rewriting that historical
                    # package, revision, original bytes or authority assets.
                    from .rich_documents import docx_to_pdf
                    docx_to_pdf(stage / (document_base + ".docx"), pdf)
                self._document_extras(obj, stage)
            elif obj["kind"]!="DataWorkbook":
                self._fixed_basis(obj, stage)
            basis=obj.get('draft_basis') or obj['content'].get('draft_ref')
            if basis:
                from .experiment_drafts import outline_markdown
                draft=self.store.object(basis['id'],basis['revision'])
                if draft['kind']!='ExperimentDraft' or draft['experiment_id']!=obj['experiment_id']:raise ValueError('成果草稿依据不属于同一实验')
                folder=stage/'工作依据';folder.mkdir(exist_ok=True)
                (folder/'实验草稿.md').write_text(outline_markdown(draft['content']),encoding='utf-8')
                (folder/'实验草稿.json').write_text(json.dumps(draft['content'],ensure_ascii=False,indent=2),encoding='utf-8')
            files = []
            target.mkdir(parents=True, exist_ok=True)
            for source in sorted(stage.rglob("*")):
                if not source.is_file():
                    continue
                name = source.relative_to(stage).as_posix()
                destination = (target / name).resolve()
                if not destination.is_relative_to(target.resolve()) or not destination.is_relative_to(self.root):
                    raise ValueError("产物子目录越界")
                destination.parent.mkdir(parents=True, exist_ok=True)
                # The archive remains independent: always copy bytes, no
                # symlink, junction, hardlink or path back into authority.
                shutil.copyfile(source, destination)
                files.append({"name": name, "sha256": digest(source.read_bytes()), "bytes": source.stat().st_size})
            self.save_record(obj, target, files)

    def _document_extras(self, obj, stage):
        latex = []
        formula_number=0
        tables = stage / "表格"
        count = 0
        from .document_composition import all_blocks, nodes, walk_nodes
        for block in all_blocks(obj['content']):
                if block["type"] == "equation":
                    formula_number+=1
                    latex.extend(["% "+(block.get("label") or str(formula_number))+" "+block.get("symbols",""), "\\[", block["latex"], "\\]", ""])
                if block["type"] in ("worked_example","branch_table","measurement_table"):
                    from .observation_paths import example_rows,branch_rows
                    from .measurement_tables import measurement_table
                    if block["type"]=="measurement_table":
                        block=measurement_table(block,self.store)
                    elif block["type"]=="worked_example":
                        branch,rows=example_rows(block,self.store,obj["content"].get("draft_ref"),human_units=True)
                        block={**block,"type":"table","caption":block["caption"]+"（"+branch["condition"]+"）","columns":["量","怎样读或算","数值"],"rows":rows}
                    else:
                        block={**block,"type":"table","columns":["怎样判断","这一情形能读什么","接下来怎样处理"],"rows":branch_rows(block,self.store,obj["content"].get("draft_ref"))}
                if block["type"] == "table":
                    count += 1
                    tables.mkdir(exist_ok=True)
                    with (tables / (str(count) + " " + filename(block["caption"], 42) + ".csv")).open("w", encoding="utf-8-sig", newline="") as stream:
                        writer = csv.writer(stream)
                        writer.writerow(block["columns"])
                        writer.writerows(block["rows"])
                if block['type']=='markdown':
                    for node in walk_nodes(nodes(block['text'])):
                        if node.type in ('math_inline','math_block','math_block_label'):latex.extend(['\\[',node.content,'\\]',''])
        if latex:
            (stage / "公式.tex").write_text("\n".join(latex), encoding="utf-8")

    def _fixed_basis(self, obj, stage):
        found = {}
        def visit(value):
            if isinstance(value, dict):
                if isinstance(value.get("id"), str) and isinstance(value.get("revision"), int):
                    key = (value["id"], value["revision"])
                    if key not in found:
                        item = self.store.object(*key)
                        if item["experiment_id"] is None:
                            found[key] = {k: item[k] for k in ("id", "revision", "kind", "title", "content", "sources")}
                            visit(item["content"])
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(obj["content"])
        (stage / "fixed-knowledge.json").write_text(json.dumps(list(found.values()), ensure_ascii=False, indent=2), encoding="utf-8")

    def on_saved(self, obj):
        if getattr(self._work,'active',False) and obj['kind'] in ('ExperimentDraft','LearningDocument','AnalysisProgram','AnalysisResult'):
            current=self.info(obj)
            if current['status']=='ready' and current.get('available'):return current
            self.save_record(obj,None,[],'working')
            return self.info(obj)
        return self.deliver(obj)

    def deliver(self,obj):
        try:
            return self.publish(obj)
        except Exception as exc:
            # A removable/unavailable products disk cannot destroy an already
            # committed, recoverable version. Expose delivery failure plainly.
            # This exception comes from a local file copy/build. Keep recovery
            # usable even when no model credential can be opened on this machine.
            error = str(exc)
            try:
                previous=self.info(obj)
                self.save_record(obj,previous.get('folder'),previous.get('files',[]),"failed",error)
            except Exception:
                pass
            return {"status":"failed","revision":obj["revision"],"error":error,"message":"内部版本已保存，产物文件未全部生成，请重新发布"}

    def list(self, experiment_id):
        rows=self.store.all("SELECT * FROM objects WHERE experiment_id=? AND kind IN ('ExperimentDraft','ExperimentWorksheet','LearningDocument','AnalysisProgram','AnalysisResult') ORDER BY created", (experiment_id,)) if experiment_id else self.store.all("SELECT * FROM objects WHERE experiment_id IS NULL AND kind='DataWorkbook' ORDER BY created")
        return [{"id": r["id"], "revision": r["revision"], "kind": r["kind"], "title": r["title"], **self.info(r)} for r in rows]

    def request_open(self, experiment_id, obj=None, file_format="folder"):
        if obj:
            result = self.publish(obj)
            folder = Path(result["folder"])
            if file_format in ("pdf", "docx", "xlsx"):
                name = next((f["name"] for f in result["files"] if Path(f["name"]).suffix.lower() == "." + file_format), None)
                if not name:
                    raise ValueError("此成果没有该种文稿文件")
                path = folder / name
            else:
                path = folder
        else:
            path = self.experiment_folder(experiment_id)
            path.mkdir(parents=True, exist_ok=True)
        self.open_requests.put(path)
        return {"status": "open_requested", "path": str(path), "message": "已请求原生桌面打开；文件存在，实际打开状态由桌面程序处理"}
