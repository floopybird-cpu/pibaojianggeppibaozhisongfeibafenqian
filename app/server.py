from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import subprocess
import os
import threading
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .agent import Agent
from .analysis import Analysis
from .formats import FormatGateway, Registry, RevisionConflict
from .materials import Materials
from .network import ModelClient
from .settings import Settings
from .storage import Store, canonical, now


class Application:
    def __init__(self, directory: Path, data: Path, *, products_root=None):
        self.directory = directory.resolve()
        self.store = Store(data)
        self.registry = Registry(directory / "formats", self.store.authority / "packages")
        self.gateway = FormatGateway(self.store, self.registry)
        probe="import sys,json,importlib.metadata as m; print(json.dumps({'python':sys.version.split()[0],'dependencies':{d.metadata['Name']:d.version for d in m.distributions()}}))"
        observed=subprocess.check_output([str(directory/"runtime/python.exe"),"-I","-c",probe],creationflags=0x08000000 if os.name=="nt" else 0)
        self.gateway.runtime_environment=json.loads(observed)
        self.gateway.runtime_environment["application_lock_sha256"]=__import__("hashlib").sha256((directory/"requirements.lock").read_bytes()).hexdigest()
        self.materials = Materials(self.store)
        self.settings = Settings(self.store.authority)
        # An explicit launch/test destination wins over a copied preference.
        # Ordinary launches without this argument retain the user's setting.
        if products_root is not None:
            self.settings.update({'products_directory':str(Path(products_root).resolve())})
        from .publications import Publications
        self.publications = Publications(self.store, self.settings, products_root or directory / "生成产物")
        self.gateway.publications = self.publications
        self.gateway.document_config = {k:self.settings.read()[k] for k in ("font","language")}
        self.analysis = Analysis(self.store, self.gateway, directory / "runtime")
        self.agent = Agent(self.store,self.gateway,self.materials,self.settings,self.analysis)
        from .questions import Questions
        self.questions=Questions(self.store);self.agent.questions=self.questions;self.questions.on_answer=self.agent.resume_question
        from .data_workbooks import DataWorkbooks
        self.data_workbooks=DataWorkbooks(self.store,self.gateway,self.materials)
        self.data_workbooks.answer_lookup=self.questions.answer_lookup
        self.agent.data_workbooks=self.data_workbooks
        from .worksheets import Worksheets
        self.worksheets=Worksheets(self)
        self.agent.worksheets=self.worksheets
        from .knowledge_workflow import KnowledgeWorkflow
        self.knowledge=KnowledgeWorkflow(self)
        self.agent.knowledge=self.knowledge
        self.token = secrets.token_urlsafe(32)
        self.demo_lock=threading.Lock()

    def state(self, *, include_usage=True, include_archived=False):
        scope = "" if include_archived else " WHERE s.archived=''"
        sources=self.store.all("SELECT s.id,s.name,s.kind,s.sha,s.created,s.metadata,s.archived,(SELECT COUNT(*) FROM pages p WHERE p.source_id=s.id) AS page_count FROM sources s"+scope+" ORDER BY s.created DESC")
        for s in sources:
            s["metadata"]=json.loads(s["metadata"])
        jobs=self.store.all("SELECT id,kind,status,progress,payload,result,error,created,updated FROM jobs ORDER BY created DESC")
        for j in jobs:
            j["payload"]=json.loads(j["payload"]);j["payload"].pop("upload",None)
            j["result"]=json.loads(j["result"]) if j["result"] else None
        experiments=self.store.all("SELECT id,title,goal,updated,archived FROM experiments"+("" if include_archived else " WHERE archived=''")+" ORDER BY updated DESC")
        knowledge_count=self.store.one("SELECT COUNT(*) AS n FROM objects WHERE experiment_id IS NULL AND archived='' AND kind IN ('SciencePoint','MathFoundation','Formula','MathUse','KnowledgeRelation')")["n"]
        return {"version":__version__,"milestone":"PH-M2",**({'usage':self.agent.runtime.summary()} if include_usage else {}), "sources":sources,"experiments":experiments,
                "categories":self.store.all("SELECT * FROM categories"),"jobs":jobs,"settings":self.settings.read(),"formats":self.registry.public(),
                "knowledge_count":knowledge_count, "products_directory": str(self.publications.root),"pending_questions":[{k:v for k,v in q.items() if k!='context'} for q in self.questions.pending(include_all=True)]}

    def source(self, identity):
        src=self.store.one("SELECT * FROM sources WHERE id=?",(identity,))
        if not src:
            raise ValueError("来源不存在")
        src.pop("original");src["metadata"]=json.loads(src["metadata"])
        src["pages"]=self.store.all("SELECT * FROM pages WHERE source_id=? ORDER BY number",(identity,))
        for p in src["pages"]:
            p["warnings"]=json.loads(p["warnings"])
            if src["kind"] in ("xlsx","dat"):p["text"]=self.materials.page_text(identity,p["number"])
        src["anchors"]=self.store.all("SELECT * FROM anchors WHERE source_id=?",(identity,))
        if src["kind"] in ("csv","tsv","xlsx","dat"):
            src["dataset_preview"]=self.materials.preview_dataset(identity)
        return src

    def experiment(self, identity, *, include_usage=True):
        exp=self.store.experiment(identity)
        exp["messages"]=self.store.all("SELECT * FROM messages WHERE experiment_id=? ORDER BY created",(identity,))
        exp["artifacts"]=self.store.all("SELECT o.* FROM objects o JOIN revisions r ON r.object_id=o.id AND r.revision=o.revision WHERE o.experiment_id=? ORDER BY r.created DESC",(identity,))
        exp["runs"]=self.store.all("SELECT * FROM runs WHERE experiment_id=? ORDER BY created DESC",(identity,))
        for r in exp["runs"]:
            r["info"]=json.loads(r["info"])
        exp["datasets"]=self.analysis.datasets(identity)
        if include_usage:exp['usage']=self.agent.runtime.summary(experiment_id=identity)
        return exp

    def post(self, path, body):
        parts=path.strip("/").split("/")
        if path=="/api/import":
            return self.materials.queue(body["name"],base64.b64decode(body["data"],validate=True))
        if path=="/api/anchor":
            return self.materials.make_anchor(body["source_id"],body["page"],body.get("quote", ""),body.get("region"),body.get("status", "unverified"))
        if path=="/api/knowledge":
            if body["kind"] not in ("SciencePoint","MathFoundation","Formula","MathUse","KnowledgeRelation"):
                raise ValueError("此入口仅写全局知识")
            return self.gateway.import_knowledge(body["kind"],body["content"],body.get("merge_target"))
        if path=="/api/category":
            return self.gateway.category(body["name"],body.get("parent") or None,body.get("domain"),body.get("id"))
        if path=="/api/experiments":
            return self.store.create_experiment(body["title"],body["goal"],body["background"],body.get("source_ids", []),body.get("notes",""))
        if path=="/api/settings":
            if body.get("products_directory"):
                self.publications.check_root(Path(body["products_directory"]))
            result = self.settings.update(body)
            self.gateway.document_config = {k:result[k] for k in ("font","language")}
            return result
        if path=="/api/model-test":
            reply,usage=ModelClient(self.settings,self.agent.runtime).complete([{"role":"user","content":"请只回答连接成功。"}])
            return {"message":reply.get("content"),"usage":usage,"provider":"live_api"}
        if path=="/api/demo":
            from .demo import install_demo
            with self.demo_lock:
                return install_demo(self)
        if len(parts)==4 and parts[1]=="jobs":
            job=self.store.one("SELECT * FROM jobs WHERE id=?",(parts[2],))
            if not job:
                raise ValueError("任务不存在")
            if parts[3]=="cancel":
                self.store.execute("UPDATE jobs SET cancel=1,updated=? WHERE id=?",(now(),parts[2]))
                self.questions.cancel_job(parts[2])
                return {"requested":True}
            if parts[3]=="retry":
                return self.materials.retry(parts[2])
            if parts[3]=='resume':return self.agent.resume_job(parts[2])
        if len(parts)==4 and parts[1]=="sources" and parts[3]=="distill":
            return self.agent.distill(parts[2],body.get("pages"),body.get("topic",""))
        if len(parts)==4 and parts[1]=="sources" and parts[3]=="organize":
            return self.agent.organize(parts[2],body.get("prompt"))
        if len(parts)==4 and parts[1]=="questions" and parts[3]=="answer":
            return self.questions.answer(parts[2],body["answer"])
        if len(parts)==4 and parts[1]=="objects":
            if parts[3]=="publish":
                return self.publications.publish(self.gateway.read(parts[2], body.get("revision")))
            if parts[3]=="upgrade-plan":
                return self.gateway.plan_upgrade(parts[2])
            if parts[3]=="upgrade":
                return self.gateway.upgrade(parts[2],body["plan"],body.get("preserve_human",False))
            if parts[3]=="edit":
                old=self.gateway.read(parts[2])
                if old["kind"]=="DataWorkbook":raise ValueError("规范测量表通过整理服务与用户问答更新")
                if old["experiment_id"]!=body.get("experiment_id"):
                    raise ValueError("实验绑定不一致")
                return self.gateway.apply(old["kind"],body["content"],experiment_id=old["experiment_id"],object_id=old["id"],base_revision=body["base_revision"],human=True)
        if len(parts)==4 and parts[1]=="experiments":
            exp_id=parts[2]; exp=self.store.experiment(exp_id); action=parts[3]
            if action=="settings":
                source_ids=body.get("source_ids",exp["source_ids"])
                for sid in source_ids:
                    if not self.store.one("SELECT id FROM sources WHERE id=?",(sid,)):
                        raise ValueError("来源不存在")
                title=str(body.get("title",exp["title"])).strip()
                if not title:raise ValueError("实验名称不能为空")
                with self.store.transaction() as conn:
                    current=json.loads(conn.execute("SELECT source_ids FROM experiments WHERE id=?",(exp_id,)).fetchone()[0])
                    if "baseline_source_ids" in body:
                        baseline=set(body["baseline_source_ids"])
                        removed=baseline-set(source_ids)
                        added=[sid for sid in source_ids if sid not in baseline]
                        source_ids=list(dict.fromkeys([sid for sid in current if sid not in removed]+added))
                    elif "source_ids" not in body:
                        source_ids=current
                    conn.execute("UPDATE experiments SET title=?,background=?,source_ids=?,goal=?,notes=?,instruments=?,updated=? WHERE id=?",(
                        title,canonical(body.get("background",exp["background"])),canonical(source_ids),body.get("goal",exp["goal"]),body.get("notes",exp["notes"]),canonical(body.get("instruments",exp["instruments"])),now(),exp_id))
                return self.experiment(exp_id)
            if action=="agent-options":
                options={k:v for k,v in body.items() if k in ("thinking_depth","native_search","native_vision","worksheet_quick")}
                if "worksheet_quick" in options and type(options["worksheet_quick"]) is not bool:raise ValueError("工作台快速模式需为开或关")
                if options.get("thinking_depth","high") not in ("off","low","high","max"):raise ValueError("无效思考深度")
                self.store.execute("UPDATE experiments SET agent_options=?,updated=? WHERE id=?",(canonical({**exp["agent_options"],**options}),now(),exp_id))
                return self.experiment(exp_id)
            if action=="chat":
                return self.agent.queue(exp_id,body["prompt"],body.get("activity","chat"))
            if action=="products":
                return self.publications.list(exp_id)
            if action=="reference":
                return self.agent.reference_document(exp_id,body["doc_type"])
            if action=="dataset":
                return self.materials.associate_dataset(exp_id,body["source_id"],body["mapping"],body.get("confirmed",False),body.get("selection"))
            if action=="run":
                return self.analysis.queue(exp_id,body["program_id"],body["revision"],body.get("parameters"))
        if len(parts)==4 and parts[1]=="runs" and parts[3]=="cancel":
            row=self.analysis.inspect(body["experiment_id"],parts[2])
            self.analysis.cancellations.add(row["id"])
            return {"requested":True}
        raise ValueError("未支持的操作")


def make_server(app, port=0):
    class Handler(BaseHTTPRequestHandler):
        server_version="PhysicsHarness/"+__version__

        def log_message(self, fmt, *args):
            # URLs may contain an ephemeral local session token. Never log them.
            pass

        def send(self, code, data, content_type="application/json; charset=utf-8", disposition=None):
            if isinstance(data,(dict,list)):
                data=json.dumps(data,ensure_ascii=False).encode()
            elif isinstance(data,str):
                data=data.encode()
            self.send_response(code)
            self.send_header("Content-Type",content_type)
            self.send_header("Content-Length",str(len(data)))
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Referrer-Policy","no-referrer")
            self.send_header("Cache-Control","no-store" if self.path.startswith("/api/") else "no-cache")
            self.send_header("Content-Security-Policy","default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            if disposition:
                from urllib.parse import quote
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(disposition))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):
                pass

        def authenticate(self):
            host=self.headers.get("Host", "")
            if host not in (f"127.0.0.1:{self.server.server_port}",f"localhost:{self.server.server_port}"):
                raise PermissionError("拒绝非本地 Host")
            origin=self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{self.server.server_port}",f"http://localhost:{self.server.server_port}"):
                raise PermissionError("拒绝跨站请求")
            token=self.headers.get("X-Session-Token") or parse_qs(urlsplit(self.path).query).get("token", [""])[0]
            if not secrets.compare_digest(token,app.token):
                raise PermissionError("本地会话失效，请从启动入口重开")

        def do_GET(self):
            parsed=urlsplit(self.path);path=unquote(parsed.path);query=parse_qs(parsed.query)
            try:
                if path.startswith("/api/"):
                    self.authenticate()
                    parts=path.strip("/").split("/")
                    if path=="/api/state":
                        return self.send(200,app.state())
                    if path=="/api/format-icon":
                        pack=app.registry.get(query["pack"][0]);name=query["name"][0]
                        if name not in {b.get("icon") for b in pack["ui"]}:raise ValueError("未声明的图标")
                        file=(Path(pack["directory"])/name).resolve()
                        if not file.is_relative_to(Path(pack["directory"])) or not file.is_file():
                            raise ValueError("图标不可用")
                        return self.send(200,file.read_bytes(),mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                    if path=="/api/knowledge/cards":
                        from .knowledge_index import search
                        return self.send(200, search(app.store,query.get("q",[""])[0],kind=query.get("kind",[None])[0],
                                                     category=query.get("category",[None])[0]))
                    if path=="/api/knowledge":
                        return self.send(200,app.gateway.query(query.get("q",[""])[0],query.get("kind",[None])[0],query.get("category",[None])[0],int(query.get("offset",[0])[0])))
                    if len(parts)==3 and parts[1]=="sources":
                        return self.send(200,app.source(parts[2]))
                    if len(parts)==3 and parts[1]=="anchors":
                        return self.send(200,app.store.anchor(parts[2]))
                    if len(parts)==3 and parts[1]=="experiments":
                        return self.send(200,app.experiment(parts[2]))
                    if len(parts)==3 and parts[1]=="objects":
                        revision=int(query["revision"][0]) if "revision" in query else None
                        return self.send(200,app.gateway.read(parts[2],revision))
                    if path=="/api/asset":
                        file=app.store.asset_path(query["path"][0])
                        mime=mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                        if file.suffix in (".py", ".lock", ".md"):
                            mime="text/plain; charset=utf-8"
                        return self.send(200,file.read_bytes(),mime,query.get("download",[None])[0])
                    if len(parts)==4 and parts[1]=="sources" and parts[3]=="original":
                        source=app.store.one("SELECT * FROM sources WHERE id=?",(parts[2],))
                        if not source:
                            raise ValueError("来源不存在")
                        return self.send(200,app.store.asset_path(source["original"]).read_bytes(),"application/octet-stream",source["name"])
                    raise ValueError("入口不存在")
                file=(app.directory / "web" / ("index.html" if path=="/" else path.lstrip("/"))).resolve()
                if not file.is_relative_to(app.directory/"web") or not file.is_file():
                    self.send(404,{"error":"文件不存在"});return
                return self.send(200,file.read_bytes(),mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            except PermissionError as exc:
                self.send(403,{"error":str(exc)})
            except Exception as exc:
                self.send(400,{"error":app.settings.redact(str(exc))})

        def do_POST(self):
            try:
                self.authenticate()
                length=int(self.headers.get("Content-Length","0"))
                if length<=0:
                    raise ValueError("请求为空")
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise ValueError("必须提交 JSON 请求")
                body=json.loads(self.rfile.read(length))
                result=app.post(unquote(urlsplit(self.path).path),body)
                self.send(200,result)
            except PermissionError as exc:
                self.send(403,{"error":str(exc)})
            except RevisionConflict as exc:
                self.send(409,{"error":str(exc)})
            except Exception as exc:
                self.send(400,{"error":app.settings.redact(str(exc))})

    server=ThreadingHTTPServer(("127.0.0.1",port),Handler)
    server.daemon_threads=True
    return server
