"""Experiment-specific workbench. Definitions use format revisions, measurements use a separate journal."""
from __future__ import annotations

import copy
import json
import re

from . import worksheet_evidence as evidence
from .experiment_drafts import PHASES
from .storage import canonical, digest, now
from .worksheet_math import compute, validate_page


def decode_json(text):
    text=text.strip()
    if text.startswith("~~~") or text.startswith(chr(96)*3):
        text="\n".join(text.splitlines()[1:-1])
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        if err.msg!="Extra data":raise
    decoder=json.JSONDecoder()
    result,end=decoder.raw_decode(text)
    tail=text[end:].strip()
    if not isinstance(result,dict):raise ValueError("模型交回了多个不同的结果")
    while tail:
        # Some providers split one tool's named arguments into adjacent JSON objects,
        # or close its object just before the final named arguments.
        if tail.startswith(","):tail="{"+tail[1:].lstrip()
        other,end=decoder.raw_decode(tail)
        if not isinstance(other,dict) or result.keys() & other.keys():
            raise ValueError("模型交回了冲突的同名字段")
        result.update(other)
        tail=tail[end:].strip()
    return result


def requests_worksheet(prompt):
    objects=r"数据处理(?:界面|台|页面|页|表)|处理读数的(?:界面|页面)|工作台|可填写的(?:数据)?(?:表|界面)|数据页|工作表"
    request=r"请|帮我|给我|我想|我要|做|建|生成|打开|制作|设计|弄|来一"
    interface=re.search(r"界面|窗口|工作台|工作表|数据页",prompt)
    measurement=re.search(r"实验读数|测量值|录入|拟合|画图|曲线|计算结果|处理数据",prompt)
    if not re.search(objects,prompt) and not (interface and measurement):return False
    if re.search(r"为什么|是怎么|有什么区别|如何理解",prompt) and not re.search(r"帮我|给我|请.{0,8}(?:做|建|打开)",prompt):return False
    return bool(re.search(request,prompt))


def row_change(text):
    m=re.search(r"(再加|多加|增加|加上|追加|加测|补测|多测|多留|加|补|扩充|添|少测|少加|减少|减去|减掉|去掉|删去|删掉)\s*([零一二两三四五六七八九十百\d]+)\s*(?:组|行|次)",text)
    if not m:
        return None
    value=m[2].replace("两","二")
    if value.isdecimal():
        n=int(value)
    else:
        digits="零一二三四五六七八九"
        parts=re.fullmatch(r"([一二三四五六七八九]?)十([一二三四五六七八九]?)",value)
        if parts:
            n=(digits.index(parts[1]) if parts[1] else 1)*10 + (digits.index(parts[2]) if parts[2] else 0)
        elif value in digits:
            n=digits.index(value)
        else:
            return None
    return -n if m[1] in ("少测","少加","减少","减去","减掉","去掉","删去","删掉") else n


def normalize_outline_kinds(outline):
    from .experiment_drafts import KINDS
    labels={label:key for key,label in KINDS.items()}
    labels.update({"事实":"fact","公式":"formula","步骤":"procedure","操作":"procedure"})
    for stage in outline["stages"]:
        for point in stage.get("points",[]):
            for item in point.get("knowledge",[]):
                raw=str(item.get("kind","")).strip()
                item["kind"]=labels.get(raw,raw.lower())
                if item["kind"] == "observation" and re.match(r"^(?:记录|观察|测量|记下)", str(item.get("text", "")).strip()):
                    item["kind"] = "procedure"
                if item["kind"] not in KINDS:
                    raise ValueError("草稿中“"+point["title"]+"”的知识类别无法识别："+raw)
    return outline


def table_state(state, table):
    return state.setdefault("tables",{}).setdefault(table["id"],{"rows":[{} for _ in range(table.get("rows",3))]})


def linked_sources(app, records, keys):
    ids=[]
    for key in dict.fromkeys(keys):
        if key not in records:
            raise ValueError("材料页编号不存在："+str(key))
        source=records[key]
        ids.append(app.materials.make_anchor(source["source"],source["number"])["id"])
    return ids


class Worksheets:
    def __init__(self,app):
        self.app=app
        self.agent=app.agent
        self.store=app.store
        self.store.execute("CREATE TABLE IF NOT EXISTS worksheet_readings (object_id TEXT PRIMARY KEY REFERENCES objects(id), version INTEGER NOT NULL, content TEXT NOT NULL, updated TEXT NOT NULL)")
        self.store.execute("CREATE TABLE IF NOT EXISTS worksheet_builds (experiment_id TEXT NOT NULL, fingerprint TEXT NOT NULL, step TEXT NOT NULL, content TEXT NOT NULL, PRIMARY KEY(experiment_id,fingerprint,step))")
        self.store.execute("CREATE TABLE IF NOT EXISTS worksheet_followups (experiment_id TEXT PRIMARY KEY, request TEXT NOT NULL, page TEXT NOT NULL, table_id TEXT NOT NULL)")

    def current(self, exp):
        record=self.store.one("SELECT id FROM objects WHERE experiment_id=? AND kind='ExperimentWorksheet' ORDER BY created DESC LIMIT 1",(exp,))
        return self.store.object(record["id"]) if record else None

    def prior_links(self,obj):
        from .measurement_plan import prior_result_links
        binding=obj["content"].get("draft_ref")
        if not binding:return {}
        draft=self.store.object(binding["id"],binding["revision"])
        return prior_result_links(draft["content"].get("measurement_methods",[]))

    def state(self, obj):
        row=self.store.one("SELECT version,content FROM worksheet_readings WHERE object_id=?",(obj,))
        version,data=(row["version"],json.loads(row["content"])) if row else (0,{})
        if row:
            from .worksheet_math import resolve_prior_results
            spec=self.store.object(obj)
            resolve_prior_results(spec["content"]["pages"],data,self.prior_links(spec))
        return version,data

    def save(self,obj,version,data):
        with self.store.transaction() as c:
            row=c.execute("SELECT version FROM worksheet_readings WHERE object_id=?",(obj,)).fetchone()
            if (row[0] if row else 0)!=version:
                raise ValueError("读数已由其他窗口更改，请重新打开后录入")
            c.execute("INSERT OR REPLACE INTO worksheet_readings VALUES(?,?,?,?)",(obj,version+1,canonical({p:{k:v for k,v in values.items() if k!="linked_fields"} for p,values in data.items()}),now()))
        return version+1

    @staticmethod
    def missing_pages(obj):
        return [p['id'] for p in obj['content']['pages'] if p.get('generation_error') or not (p.get('fields') or p.get('tables'))] if obj else []

    def pending_build(self,exp):
        return self.store.one("SELECT fingerprint,content FROM worksheet_builds WHERE experiment_id=? AND step='in_progress' ORDER BY rowid DESC LIMIT 1",(exp,))

    def select_activity(self,jid,exp,prompt,activity):
        if activity.startswith("worksheet"):
            return activity
        if activity!="chat" or not exp:
            return None
        if re.search(r"(新建|新增|改名|重命名|移动|挪动|移到|改成).{0,20}目录|目录.{0,20}(新建|改名|重命名|移到|移动|改成)",prompt):
            return None
        current=self.current(exp)
        pending=self.pending_build(exp) or self.missing_pages(current)
        if current and (row_change(prompt) or self.store.one("SELECT 1 FROM worksheet_followups WHERE experiment_id=?",(exp,))):
            return "worksheet:"
        if not pending and requests_worksheet(prompt) and not (current and re.search(r"改用|修改|改成|切换|换成|换用|添加|新增",prompt)):
            return "worksheet"
        if not pending and re.search(r"(写|生成|制作|编写|整理|导出|修改|修订|补充).{0,18}(报告|预习|讲义|文稿|实验册|记录单|程序|脚本|规范表|草稿)|整理.{0,12}(笔记|数据)",prompt):
            return None
        payload=json.loads(self.store.one("SELECT payload FROM jobs WHERE id=?",(jid,))["payload"])
        options=payload["request_options"]
        instruction=("根据实验用户要拿到或操作的东西选择动作，只输出 JSON {action:动作}。"
            "create：要一套能边填读数边看结果或图的交互工具，或打开数据台。interact：操作已经有的数据台。"
            "knowledge：要从资料提取并存入可复用的全局知识库。question：询问实验知识或来源。general：写文稿、生成程序文件、整理笔记或其它实验任务。"
            "resume：接着完成当前尚未建立的数据台或缺失页，包括自然重述原要求。已有页的新参数、方法或说明修改属于interact，不能因接续而忽略新要求。"
            "请判别句意，区分需要制作可操作东西与请解释做法。")
        try:
            pending_request=None
            if isinstance(pending,dict):
                prior=self.store.one("SELECT content FROM worksheet_builds WHERE experiment_id=? AND fingerprint=? AND step='request'",(exp,json.loads(pending['content'])))
                if prior:pending_request=json.loads(prior['content'])
            route_context={'pending_request':pending_request,'pages':[{'id':p['id'],'title':p['title']} for p in current['content']['pages']] if current else []}
            from .knowledge_index import search
            project=self.store.experiment(exp)
            has_library=self.store.one("SELECT 1 FROM kb_cards LIMIT 1")
            candidates=search(self.store,prompt,pins=project.get("knowledge_refs",[]),limit=8) if has_library else []
            if has_library:
                instruction+=" 若是知识问题，请同时生成 queries。"+evidence.QUERY_GUIDE
                route_context["question_context"]=evidence.query_context(self,project)
            if candidates:
                instruction+=" 若是解答问题，同时返回 entries。"+evidence.CHOOSE
                route_context["cards"]=evidence.card_options(candidates)
            with self.agent.client.scope(self.agent.runtime,jid):
                result=decode_json(self.ask_model(instruction,("当前有数据台。" if current else "当前还没有数据台。")+
                    ("当前有未完成的建台任务。缺页："+canonical(self.missing_pages(current)) if pending else "")+canonical(route_context)+"\n用户原话："+prompt,options,"worksheet_intent"))
            choice=result.get("action")
            if has_library and choice=="question" and isinstance(result.get("queries"),list):
                self.agent.runtime.event(jid,"knowledge_queries",{"question":prompt,"queries":evidence.valid_queries(result["queries"])})
            if candidates and choice=="question" and result.get("entries"):
                evidence.save_selection(self,jid,prompt,candidates,result["entries"])
        except (OSError,RuntimeError,ValueError,TypeError,AttributeError):
            choice=None
        if choice=="resume":return "worksheet_build:resume"
        if choice=="create":return "worksheet:" if current else "worksheet_build:new" if pending else "worksheet"
        if choice=="interact" and not current:return "worksheet_build:new" if pending else "worksheet"
        if choice in ("question","interact"):return "worksheet:"
        if choice=="knowledge":return "knowledge_ingest"
        if choice=="general":return None
        return "worksheet:" if current or re.search(r"[？?]|为什么|解释|原理|什么是",prompt) else None

    def ask_model(self,system,content,opts,purpose):
        response,_=self.agent.client.complete([{"role":"system","content":system},{"role":"user","content":content}],
            options={**opts,"_purpose":purpose,"native_search":False})
        return response["content"]

    def run(self,jid,exp,prompt,activity):
        payload=json.loads(self.store.one("SELECT payload FROM jobs WHERE id=?",(jid,))["payload"])
        opts=payload["request_options"]
        self.store.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(now(),jid))
        def cancelled():
            if self.store.one("SELECT cancel FROM jobs WHERE id=?",(jid,))["cancel"]:
                raise InterruptedError("已取消")
        try:
            with self.agent.client.scope(self.agent.runtime,jid):
                obj=self.current(exp)
                args=activity.split(":")
                create=activity=="worksheet" or args[0]=="worksheet_build"
                if create:
                    self.agent.runtime.checkpoint(jid,{'worksheet_build':True,'prompt':prompt,'activity':'worksheet_build:resume'})
                if create and (not obj or self.missing_pages(obj)):
                    reply,result=self.generate(jid,exp,prompt,opts,cancelled,mode=args[1] if args[0]=='worksheet_build' and len(args)>1 else 'resume')
                elif args[0] == "worksheet_repair" and obj:
                    page = next(p for p in obj["content"]["pages"] if p["id"] == args[1])
                    reply,result = self.retry_page(jid,exp,obj,page,prompt,opts,cancelled)
                elif create:
                    gaps=[t["label"] for p in obj["content"]["pages"] for t in p.get("target_coverage",[]) if t["status"]=="missing"]
                    reply="打开后可以接着录入。"+(" 尚待补全："+"、".join(gaps)+"。" if gaps else "")
                    result={"worksheet":obj["id"],"open":True,"complete":not gaps,"missing_pages":[],"missing_targets":gaps}
                else:
                    page_id=args[1] if len(args)>1 else ""
                    table_id=args[2] if len(args)>2 else ""
                    reply,result=self.interact(jid,exp,obj,page_id,table_id,prompt,opts,cancelled)
                # A cancellation arriving after the state commit cannot undo it;
                # report the actual saved outcome instead of claiming cancellation.
                if not result.pop("_committed",False):cancelled()
                self.store.message(exp,"assistant",reply,"model")
                incomplete=result.pop('_incomplete',None)
                self.store.execute("UPDATE jobs SET status=?,progress=100,result=?,error=?,updated=? WHERE id=?",
                    ('failed' if incomplete else 'succeeded',canonical(result),incomplete,now(),jid))
        except Exception as err:
            reason=self.app.settings.redact(str(err))
            status="cancelled" if isinstance(err,InterruptedError) else "failed"
            self.store.message(exp,"assistant",reason,"system")
            self.store.execute("UPDATE jobs SET status=?,error=?,updated=? WHERE id=?",(status,reason,now(),jid))
        finally:
            with self.agent._lock:
                self.agent._active.discard(exp)

    def generate(self,jid,exp,prompt,opts,check,mode='resume'):
        project=self.store.experiment(exp)
        catalog=evidence.materials(self.app,project)
        if not catalog:
            topic = " ".join((project["title"],project["goal"],project.get("notes",""),prompt))
            candidates = evidence.discover(self,jid,project,topic,opts)
            selected = evidence.confirmed_cards(self,jid,topic,candidates,opts,project)
            catalog=evidence.materials(self.app,project,reference_cards=selected)
        if not catalog:
            raise ValueError("请在本实验材料中添加或选用讲义，再生成数据处理台。")
        alltext=evidence.texts(catalog)
        draft=self.agent.experiment_drafts
        previous=draft.current(exp)
        current=self.current(exp)
        if current and not self.missing_pages(current):
            return "数据处理台已准备好，可以接着录入或修改。",{'worksheet':current['id'],'open':True,'complete':True}
        pack=self.app.registry.owner("ExperimentWorksheet")
        context_id=digest(canonical([project["source_ids"],project["goal"],project.get("notes",""),project["background"],opts,pack["digest"],self.agent.settings.read()["model"]]))
        fingerprint=digest(canonical([context_id,prompt]))
        prior=self.store.one("SELECT content FROM worksheet_builds WHERE experiment_id=? AND fingerprint=? AND step='in_progress'",(exp,context_id))
        earlier_request=None
        if prior:
            prior_fingerprint=json.loads(prior["content"])
            earlier=self.store.one("SELECT content FROM worksheet_builds WHERE experiment_id=? AND fingerprint=? AND step='request'",(exp,prior_fingerprint))
            earlier_request=json.loads(earlier["content"]) if earlier else None
            if mode!='new':fingerprint=prior_fingerprint
        self.store.execute("INSERT OR REPLACE INTO worksheet_builds VALUES(?,?,?,?)",(exp,context_id,"in_progress",canonical(fingerprint)))
        def saved(step):
            r=self.store.one("SELECT content FROM worksheet_builds WHERE experiment_id=? AND fingerprint=? AND step=?",(exp,fingerprint,step))
            return json.loads(r["content"]) if r else None
        def keep(step,value):
            self.store.execute("INSERT OR REPLACE INTO worksheet_builds VALUES(?,?,?,?)",(exp,fingerprint,step,canonical(value)))
        if saved("request") is None:keep("request",prompt)
        keep('latest_request',prompt)
        missing=set(self.missing_pages(current))
        known_methods={p['id'] for p in (previous or {}).get('content',{}).get('measurement_methods',[])}
        if current and {p['id'] for p in current['content']['pages']}<=known_methods:
            # Published usable pages are authoritative, even for legacy builds
            # that discarded their in_progress pointer after a partial publish.
            binding=self.note_request(jid,exp,prompt)
            pinned=self.store.object(binding['id'],binding['revision'])['content']
            parts=[p for p in pinned.get('measurement_methods',[]) if p['id'] in missing]
            if {p['id'] for p in parts}!=missing:raise ValueError('缺失页没有对应的正式测量草稿，请先明确需要补建的方法。')
            from .worksheet_design import design_pages
            replacements=design_pages(self,jid,parts,catalog,[],opts,prompt,pack,saved,keep,check,draft_content=pinned)
            for page in replacements:self.bind_page(page,catalog)
            by_id={p['id']:p for p in replacements}
            pages=[by_id.get(p['id'],copy.deepcopy(p)) for p in current['content']['pages']]
            return self.publish_build(jid,exp,project,pages,current,catalog,context_id,fingerprint,keep,check)
        ctx="用户："+prompt+"\n实验："+project["title"]+" "+project["goal"]+"\n实验备注："+project.get("notes","")+"\n选用原件：\n"+alltext
        knowledge_evidence={}
        if saved("outline") is None:
            urls=saved("knowledge_references")
            if urls is None:
                titles = dict.fromkeys(v["title"].rsplit(" · 第", 1)[0] for v in catalog.values())
                headings = [next((line.strip() for line in v["text"].splitlines() if line.strip()), "") for v in catalog.values()]
                topic = " ".join([project["title"], project["goal"], project.get("notes",""), prompt, *titles, *headings])
                candidates=evidence.discover(self,jid,project,topic,opts)
                cards=evidence.confirmed_cards(self,jid,topic,candidates,opts,project)
                urls=[f"physlab://knowledge/{c['id']}/{c['revision']}" for c in cards]
                keep("knowledge_references",urls)
            if urls:
                from .knowledge_index import hydrate
                cards=[]
                for url in urls:
                    identity,rev=url.rsplit("/",2)[-2:]
                    title=self.store.one("SELECT title FROM kb_cards WHERE object_id=? AND revision=?",(identity,int(rev)))
                    if title:cards.append({"id":identity,"revision":int(rev),"title":title["title"]})
                knowledge_evidence=hydrate(self.store,cards)
                ctx+="\n已存知识及需核对的原页：\n"+evidence.texts(knowledge_evidence)
                ctx+="\n实验任务、仪器、步骤及必交量以本次课程原页为准。教材补充原理时标明知识条目的标题与修订，课程原页出处仍按 D 标签填写。"
        if earlier_request and earlier_request!=prompt:
            ctx+="\n尚未完成的原任务："+earlier_request+"\n本轮按用户最新要求调整。"
        from pathlib import Path
        outline_prompt=(Path(pack["directory"])/"measurement_draft.md").read_text(encoding="utf8")
        outline_prompt+="\n全部字段依 submit_workbench_outline 的参数结构。四阶段依次 prestudy、procedure、recording、analysis，知识点与诠释分开。引用使用原文段首的 D1p2 等完整标签。每一方法的 targets 和 observables 完整声明，对应关系在 equations 中解释；id 用英文字母、数字及下划线。formula_pages 指所选原式或装置图。requested_method 指用户想先操作的方法。"
        records=self.store.all("SELECT s.name,d.mapping FROM datasets d JOIN sources s ON s.id=d.source_id WHERE d.experiment_id=? AND d.confirmed=1",(exp,))
        if records:ctx+="\n学生已确认的数据和列对应："+canonical([{"name":r["name"],"mapping":json.loads(r["mapping"])} for r in records])
        if previous:
            ctx+="\n本实验已有草稿，保留其有效安排，补充本次数据处理要求：\n"+canonical(previous["content"])
        if current:
            ctx+='\n旧数据台尚缺测量目录。沿用下列各页 id 建立完整方法目录，宿主只补缺页；已有可用页和读数保持。缺页 id：'+canonical(sorted(missing))+'\n已有定义：'+canonical(current['content']['pages'])
        # PDF text layers commonly lose stacked fractions even when the prose is complete.
        # Read original formula/diagram pages before committing the outline, once per method.
        if opts.get("native_vision"):
            candidates=evidence.outline_figure_pages(catalog)
            images=evidence.image_blocks(self.app,catalog,candidates)+evidence.knowledge_images(self.app,knowledge_evidence)
            if images:ctx=[{"type":"text","text":ctx},*images]
        params=json.loads((Path(pack["directory"])/"outline_contract.json").read_text(encoding="utf8"))
        from .tool_receipts import named_parameters,project_empty_extras
        from .format_program import Program,check_schema
        formal=Program(self.app.registry.owner("ExperimentDraft")).schemas["ExperimentDraft"]["properties"]
        declared_points=params["properties"]["stages"]["items"]["properties"]["points"]["items"]["properties"]
        formal_points=formal["stages"]["items"]["properties"]["points"]["items"]["properties"]
        for name in ("title","knowledge","material_plan","interpretations"):
            declared_points[name]=copy.deepcopy(formal_points[name])
        for name in ("title","purpose","source_strategy"):
            params["properties"][name]=copy.deepcopy(formal[name])
        def accept_outline(value):
            if current and {p['id'] for p in value.get('experiments',[])}!={p['id'] for p in current['content']['pages']}:
                raise ValueError('补建旧测量目录须沿用已有各页 id；已有页面不能被删除或换名。')
            # A legacy draft without the directory cannot satisfy this stage
            # merely because its saved binding still points to the same revision.
            binding=self.save_outline(jid,exp,value,catalog,prompt,opts,check,
                (lambda step:None if step=='draft' else saved(step)) if current else saved,
                require_calculations='calculations' in params['properties']['experiments']['items']['properties']['equations']['items']['required'])
            keep("draft",binding)
        def prepare_outline(value):
            value=named_parameters(value,params)
            if isinstance(value,dict) and isinstance(value.get("stages"),list):
                value=normalize_outline_kinds(value)
            value,removed=project_empty_extras(value,params)
            if removed:
                self.agent.runtime.event(jid,"worksheet_empty_fields_projected",{"step":"outline","paths":removed})
            return value
        raw=saved("outline")
        if raw is not None:
            try:
                raw=prepare_outline(raw)
                check_schema(raw,params,None,"实验草稿")
                if not current and saved('draft') and previous and saved('draft')['id']==previous['id']:
                    keep('draft',self.note_request(jid,exp,prompt))
                else:accept_outline(raw)
                keep("outline",raw)
            except (ValueError,TypeError,KeyError,AttributeError) as err:
                self.agent.runtime.event(jid,"worksheet_invalid_checkpoint",{
                    "step":"outline","value":raw,"error":str(err)})
                raw=None
        if raw is None:
            tool={"type":"function","function":{"name":"submit_workbench_outline","description":"按课程方法提交工作草稿及原页标签。","parameters":params}}
            from .worksheet_submission import submit_record
            raw=submit_record(self,jid,tool,
                [{"role":"system","content":outline_prompt},{"role":"user","content":ctx}],
                {**opts,"_purpose":"worksheet_outline","_strict_tools":True,"_structured_output":True},
                "outline",saved,keep,prepare_outline,accept_outline,continuation="本次用户要求："+prompt)
        check()
        binding=draft.require(exp,jid)
        pictures=raw.get("formula_pages",[])
        check()
        from .worksheet_design import design_pages
        pinned=self.store.object(binding["id"],binding["revision"])["content"]
        plan=pinned["measurement_methods"]
        parts=[p for p in plan if p['id'] in missing] if current else plan
        pages=design_pages(self,jid,parts,catalog,pictures,opts,prompt,pack,saved,keep,check,draft_content=pinned)
        check()
        for page in pages:
            self.bind_page(page,catalog)
        if current:
            replacements={p['id']:p for p in pages}
            pages=[replacements.get(p['id'],copy.deepcopy(p)) for p in current['content']['pages']]
        return self.publish_build(jid,exp,project,pages,current,catalog,context_id,fingerprint,keep,check,raw.get('requested_method',''))

    def publish_build(self,jid,exp,project,pages,current,catalog,context_id,fingerprint,keep,check,focus=''):
        missing=[p['id'] for p in pages if p.get('generation_error') or not (p.get('fields') or p.get('tables'))]
        if len(missing)==len(pages) and current is None:
            from .worksheet_submission import SubmissionIncomplete
            raise SubmissionIncomplete('测量页尚未建立：'+'、'.join(p['title'] for p in pages)+'。草稿与提交回执已保存，可以接续补页。')
        check()
        if current and self.store.object(current['id'])['revision']!=current['revision']:
            raise ValueError('数据台在补页期间已有新的定义，请按最新版本接续。读数和已保存页仍保留。')
        binding,pages=self.commit_measurement_plan(jid,exp,pages)
        keep("draft",binding)
        spec={"title":project["title"]+" · 数据处理台","pages":pages}
        keep("design",spec)
        value=dict(title=spec["title"],pages=spec["pages"],draft_ref=binding,sources=list(dict.fromkeys(s for p in spec["pages"] for s in p["sources"])))
        check()
        obj=self.app.gateway.apply("ExperimentWorksheet",value,experiment_id=exp,
            **({'object_id':current['id'],'base_revision':current['revision']} if current else {}))
        keep('object',obj['id'])
        if not missing:
            self.store.execute("DELETE FROM worksheet_builds WHERE experiment_id=? AND fingerprint=? AND step='in_progress' AND content=?",(exp,context_id,canonical(fingerprint)))
        unresolved=[v["label"] for p in value["pages"] for v in p.get("unresolved",[])]
        required_gaps = [entry["label"] for p in value["pages"] for entry in p.get("target_coverage", []) if entry["status"] == "missing"]
        extra=" 以下项目的公式仍需核对，已在对应页标出："+ "、".join(unresolved)+"。" if unresolved else ""
        available=[p['title'] for p in pages if p['id'] not in missing]
        from .measurement_plan import opening_page
        result={"worksheet":obj["id"],"open":True,"page":opening_page(pages,focus),"unresolved":unresolved,
                'complete':not (missing or required_gaps),'missing_pages':missing,'missing_targets':required_gaps,'_committed':True}
        if missing:
            reason='尚未建立录入与计算页：'+'、'.join(p['title'] for p in pages if p['id'] in missing)+'。可以接续补齐，已有页面和读数会保留。'
            result['_incomplete']=reason
            return ('可先使用：'+'、'.join(available)+'。' if available else '')+reason,result
        if required_gaps:
            return "数据页可录入。尚不能计算的课程目标有"+"、".join(required_gaps)+"。打开对应页可继续补全。",result
        return "数据页已建立："+"、".join(available)+"。可以直接填读数、导入表格，或在页内提问。"+extra,result

    def save_outline(self,jid,exp,raw,catalog,prompt,opts,check,saved,require_calculations=False):
        """Convert and persist through the same boundary that accepts a model submission."""
        check()
        draft=self.agent.experiment_drafts
        previous=draft.current(exp)
        if raw.get("experiments") and not re.search(r"先.{1,25}再|按我.{0,8}顺序",prompt):
            from .worksheet_relations import lecture_order
            raw["experiments"]=lecture_order(raw["experiments"],catalog)
        groups=[(p["title"],p,"sources") for st in raw["stages"] for p in st["points"]]
        groups.extend((e["title"],e,"material_pages") for e in raw.get("experiments",[]))
        groups.append(("公式原页",raw,"formula_pages"))
        wrong=[(label,obj,key) for label,obj,key in groups if any(k not in catalog for k in obj.get(key,[]))]
        if wrong:
            index="\n".join("["+k+"] "+v["title"]+" "+v["text"].strip().split("\n")[0] for k,v in catalog.items())
            tasks={str(i):[entry[0],entry[1][entry[2]]] for i,entry in enumerate(wrong)}
            prompt_refs="按页索引核对每项应选的实际原页，每项只保留支持该要点的材料。所有值为完整标签字符串数组，如 [\"D2p4\"]。只返回以给出的序号为键的 JSON 对象。"
            corrected=decode_json(self.ask_model(prompt_refs,canonical(tasks)+"\n"+index,opts,"worksheet_source_binding"))
            check()
            for i,(_,item,key) in enumerate(wrong):
                ids=corrected[str(i)]
                if any(k not in catalog for k in ids):raise ValueError("无法把草稿出处定位到具体原页")
                item[key]=ids
        for e in raw.get("experiments",[]):
            e["id"]=e["id"].replace("-","_")
        content={k:raw[k] for k in ("title","purpose","source_strategy")}
        stages=[]
        for stage in raw["stages"]:
            key=stage["key"]
            points=[]
            for i,item in enumerate(stage["points"]):
                p={k:item[k] for k in ("title","knowledge","material_plan","interpretations")}
                p.update(id=f"{key}_{i+1}",sources=linked_sources(self.app,catalog,item["sources"]))
                points.append(p)
            stages.append(dict(key=key,title=PHASES[key],scope="developed" if key in ("recording","analysis") else "overview",points=points))
        content["stages"]=stages
        plan=copy.deepcopy(raw.get("experiments") or [])
        if not plan:raise ValueError("草稿没有列出实验方法，请确认本次采用的测量方法。")
        from .measurement_plan import bind_equation_outputs, target_outputs, validate_calculations
        from .worksheet_conversions import bind_si_conversions
        for part in plan:
            conversions = bind_si_conversions(part)
            if conversions:
                self.agent.runtime.event(jid, "worksheet_si_conversions_bound", {"method":part["id"], "changes":conversions})
            aliases = bind_equation_outputs(part)
            if aliases:
                self.agent.runtime.event(jid,"worksheet_equation_outputs_bound",{"method":part["id"],"aliases":aliases})
            validate_calculations(part, required=require_calculations)
            if not isinstance(part.get("targets"),list) or not part["targets"]:
                raise ValueError(part["title"]+"的草稿缺少实验要交的结果，可接续本次草稿。")
            pages=list(part.get("material_pages",[]))
            pages.extend(x.get("source") for x in part["targets"]+part.get("observables",[]) if isinstance(x,dict))
            pages.extend(output.get("source") for target in part["targets"] for output in target_outputs(target))
            for eq in part.get("equations",[]):
                if isinstance(eq,dict):pages.extend([eq.get("source"),*eq.get("evidence_pages",[])])
            part["material_pages"]=list(dict.fromkeys(k for k in pages if isinstance(k,str) and k in catalog))
            if not part["material_pages"]:raise ValueError(part["title"]+"的测量草稿没有讲义页可供核对")
            part["source_index"]=dict(zip(part["material_pages"],linked_sources(self.app,catalog,part["material_pages"])))
        content["measurement_methods"]=plan
        content["delivery_requests"]=[p["title"]+"："+p["purpose"] for p in plan]
        old_binding=saved("draft")
        resume=old_binding and previous and old_binding["id"]==previous["id"] and old_binding["revision"]==previous["revision"]
        if resume:
            content=previous["content"]
        elif previous:
            retained=copy.deepcopy(previous["content"])
            for s in retained["stages"]:
                matching=next((stage for stage in stages if stage["key"]==s["key"]),None)
                if matching:
                    ids={p["id"] for st in retained["stages"] for p in st["points"]}
                    for p in matching["points"]:
                        while p["id"] in ids:
                            p["id"]+="_w"
                        s["points"].append(p);ids.add(p["id"])
            retained["delivery_requests"]=list(dict.fromkeys(retained.get("delivery_requests",[])+[prompt]))
            retained["measurement_methods"]=plan
            content=retained
        if resume:
            request=draft.request(jid,exp)
            self.store.execute("INSERT OR REPLACE INTO experiment_draft_tasks VALUES(?,?,?,?,?,?)",(jid,exp,previous["id"],previous["revision"],digest(canonical(request)),now()))
            self.agent.runtime.event(jid,"worksheet_resumed",{"draft":old_binding,"reason":prompt})
            return old_binding
        result=draft.save(exp,jid,content,previous["id"] if previous else None,previous["revision"] if previous else None)
        return {"id":result["id"],"revision":result["revision"]}

    def review_measurement_draft(self,jid,draft_content,receipt,catalog):
        from .worksheet_plan_review import review
        return review(self,jid,draft_content,receipt,catalog)

    def note_request(self,jid,exp,prompt,detail=None):
        drafts=self.agent.experiment_drafts
        current=drafts.current(exp)
        content=copy.deepcopy(current["content"])
        content["delivery_requests"]=list(dict.fromkeys(content.get("delivery_requests",[])+[detail or prompt]))
        drafts.save(exp,jid,content,current["id"],current["revision"])
        return drafts.require(exp,jid)

    def commit_measurement_plan(self,jid,exp,pages,changed_method=None):
        from .measurement_plan import attach_required_targets,worksheets_from_draft
        drafts=self.agent.experiment_drafts
        draft=drafts.current(exp)
        content=copy.deepcopy(draft["content"])
        methods=content.get("measurement_methods")
        if not methods:return drafts.require(exp,jid),pages
        by_id={p["id"]:p for p in pages}
        for method in methods:
            if method["id"] in by_id:
                prepared=copy.deepcopy(by_id[method["id"]])
                from .measurement_plan import source_keys
                obligations=method["targets"]+method.get("observables",[])
                anchors=[method["source_index"][k] for x in obligations for k in source_keys(x.get("source")) if k in method["source_index"]]
                prepared["sources"]=list(dict.fromkeys(prepared["sources"]+anchors))
                if changed_method and method["id"] == changed_method[0]:
                    from .measurement_plan import planned_calculations
                    from .worksheet_identity import formula_items
                    values = {item["id"]:item for item in formula_items(prepared)}
                    old_values = {item["id"]:item for item in formula_items(method.get("page", {}))}
                    for key, (step, _) in planned_calculations(method).items():
                        item = values.get(key)
                        if item is None:
                            raise ValueError("方法修改遗漏了已有计算量 " + key)
                        old_item = old_values.get(key, {})
                        if any(item.get(k) != old_item.get(k) for k in ("expr","uncertainty")):
                            step["expr"] = item["expr"]
                            step["analysis_change"] = changed_method[1]
                            if item.get("uncertainty"):
                                step["uncertainty"] = item["uncertainty"]
                            else:
                                step.pop("uncertainty", None)
                prepared["observations"]=[{k:v[k] for k in ("id","label","meaning","source") if k in v} for v in method["targets"] if v.get("kind")=="observation"]
                method["page"]=attach_required_targets(prepared,method)
        if content!=draft["content"]:
            drafts.save(exp,jid,content,draft["id"],draft["revision"])
        binding=drafts.require(exp,jid)
        exact=self.store.object(binding["id"],binding["revision"])
        projected=worksheets_from_draft(exact["content"])
        return binding,projected

    def bind_page(self,page,catalog):
        from .worksheet_literals import verified_reference
        from .worksheet_units import convert
        field_anchors=[]
        for field in page.get("fields",[]):
            ref=field.pop("reference",None)
            if not isinstance(ref,dict):continue
            source=catalog.get(ref.get("page"))
            verified=verified_reference(ref,field["unit"],source["text"]) if source else None
            if verified:
                converted=convert(verified["value"],verified["unit"],field["unit"])
                quote=verified["quote"]
                anchor=self.app.materials.make_anchor(source["source"],source["number"],quote,status="verified")
                field["reference"]={"value":converted,"source_anchor":anchor["id"],"quote":quote}
                field_anchors.append(anchor["id"])
        page["sources"]=list(dict.fromkeys(linked_sources(self.app,catalog,page["sources"])+field_anchors))
        for result in page.get("results",[]):
            for ref in result.get("literal_sources",[]):
                source=catalog.get(ref.get("page"))
                quote=evidence.matched_passage(source["text"],ref.get("quote")) if source else ""
                if quote:
                    anchor=self.app.materials.make_anchor(source["source"],source["number"],quote,status="verified")
                    ref["source_anchor"]=anchor["id"]
                    if anchor["id"] not in page["sources"]:page["sources"].append(anchor["id"])
            result.pop("source_anchor",None)
            basis=result.pop("basis",None)
            if not isinstance(basis,dict) or basis.get("page") not in catalog:continue
            source=catalog[basis["page"]]
            quote=evidence.matched_passage(source["text"],basis.get("quote"))
            anchor=self.app.materials.make_anchor(source["source"],source["number"],quote,status="verified" if quote else "unverified")
            result["source_anchor"]=anchor["id"]
            if anchor["id"] not in page["sources"]:page["sources"].append(anchor["id"])

    def recover_saved_page(self, exp, obj, page, method, catalog):
        from .worksheet_conversions import bind_si_conversions
        from .worksheet_design import page_from_receipt
        if not page.get("unresolved") or not bind_si_conversions(copy.deepcopy(method)):
            return None
        ref = obj["content"]["draft_ref"]
        checkpoint = self.store.one("""SELECT r.content FROM worksheet_builds r
            JOIN worksheet_builds o USING(experiment_id,fingerprint)
            JOIN worksheet_builds d USING(experiment_id,fingerprint)
            WHERE r.experiment_id=? AND r.step=? AND o.step='object' AND o.content=?
            AND d.step='draft' AND d.content=? ORDER BY r.rowid DESC LIMIT 1""",
            (exp, "page:"+page["id"], canonical(obj["id"]), canonical(ref)))
        if not checkpoint:
            return None
        rebuilt = page_from_receipt(json.loads(checkpoint["content"]), method, catalog)
        def inputs(p):
            return ([(f["id"], f["unit"]) for f in p["fields"]],
                    [(t["id"], [(c["id"], c["unit"]) for c in t["columns"]]) for t in p["tables"]])
        if inputs(rebuilt) != inputs(page) or rebuilt.get("unresolved"):
            return None
        previous = {r["id"]:r["expr"] for r in page["results"]}
        new = {r["id"]:r["expr"] for r in rebuilt["results"]}
        if not previous.items() <= new.items() or new.keys() == previous.keys():
            return None
        self.bind_page(rebuilt, catalog)
        return rebuilt

    def retry_page(self,jid,exp,obj,page,prompt,opts,check):
        if page['id'] in self.missing_pages(obj):
            return self.generate(jid,exp,prompt,opts,check)
        from .worksheet_design import design_pages
        old_ref=obj["content"]["draft_ref"]
        draft_content=self.store.object(old_ref["id"],old_ref["revision"])["content"]
        part=next((m for m in draft_content.get("measurement_methods",[]) if m["id"]==page["id"]),None)
        if not part:
            rows=self.store.all("SELECT content FROM worksheet_builds WHERE experiment_id=? AND step='outline' ORDER BY rowid DESC",(exp,))
            part=next((e for r in rows for e in json.loads(r["content"]).get("experiments",[]) if e["id"]==page["id"]),None)
        if not part:return "本页没有可接续的测量目录，请先确认本实验的讲义和测量方法。",{}
        version,measurements=self.state(obj["id"])
        state=measurements.get(page["id"],{})
        catalog=evidence.materials(self.app,self.store.experiment(exp))
        recovered = self.recover_saved_page(exp, obj, page, part, catalog)
        if recovered is not None:
            check()
            if self.state(obj["id"])[0] != version:
                raise ValueError("读数已更新，请再打开当前页")
            self.note_request(jid, exp, prompt, "依照已存仪器单位及草稿重算公式。")
            spec = copy.deepcopy(obj["content"])
            pages = [recovered if p["id"] == page["id"] else p for p in spec["pages"]]
            spec["draft_ref"], spec["pages"] = self.commit_measurement_plan(jid, exp, pages, (page["id"], "仪器读数统一按 SI 计算"))
            spec["sources"] = list(dict.fromkeys(a for p in spec["pages"] for a in p["sources"]))
            self.app.gateway.apply("ExperimentWorksheet",spec,experiment_id=exp,object_id=obj["id"],base_revision=obj["revision"])
            return "已按原先保存的公式和读数恢复计算。", {"worksheet":obj["id"]}
        if any(str(v).strip() for v in state.get("fields",{}).values()) or any(any(r for r in t.get("rows",[])) for t in state.get("tables",{}).values()):
            return self.change_method(jid,exp,obj,page,prompt,opts,check)
        binding=self.note_request(jid,exp,prompt,"补全 "+page["title"]+" 的数据定义，沿用原测量配置。")
        catalog=evidence.materials(self.app,self.store.experiment(exp))
        pack=self.app.registry.owner("ExperimentWorksheet")
        from .worksheet_design import page_from_receipt
        fixed=None
        if page.get("generation_error"):
            receipt=self.store.one("SELECT content FROM worksheet_builds WHERE experiment_id=? AND step=? ORDER BY rowid DESC LIMIT 1",(exp,"page:"+part["id"]))
            if receipt:
                try:fixed=page_from_receipt(json.loads(receipt["content"]),part,catalog)
                except (KeyError,ValueError,TypeError):pass
        if fixed is None:
            from .knowledge_notes import worksheet_references
            fixed=design_pages(self,jid,[part],catalog,part.get("material_pages",[]),opts,prompt,pack,lambda _:None,lambda *_:None,check,draft_content=draft_content,knowledge_urls=worksheet_references(self.store,exp,obj["content"]["draft_ref"]))[0]
        if fixed.get("generation_error"):
            return "本次仍未能补全 "+page["title"]+"。其他页可继续填写。",{}
        self.bind_page(fixed,catalog)
        spec=copy.deepcopy(obj["content"])
        spec["draft_ref"],spec["pages"]=self.commit_measurement_plan(jid,exp,[fixed if v["id"]==page["id"] else v for v in spec["pages"]])
        spec["sources"]=list(dict.fromkeys(v for pg in spec["pages"] for v in pg["sources"]))
        if self.state(obj["id"])[0]!=version:raise ValueError("这页在生成时收到了新读数，请按最新读数接续修改")
        self.app.gateway.apply("ExperimentWorksheet",spec,experiment_id=exp,object_id=obj["id"],base_revision=obj["revision"])
        return "已更新 "+page["title"]+"。",{"worksheet":obj["id"]}

    def interact(self,jid,exp,obj,pid,tid,prompt,opts,check,intent=None):
        from .worksheet_interaction import answer, apply_edit
        pages=obj["content"]["pages"] if obj else []
        pending=self.store.one("SELECT * FROM worksheet_followups WHERE experiment_id=?",(exp,))
        if pending:
            self.store.execute("DELETE FROM worksheet_followups WHERE experiment_id=?",(exp,))
            if prompt.strip() in ("取消","不用了","算了"):return "已取消这项修改。",{}
            prompt=pending["request"]+"\n补充："+prompt
            pid=pid or pending["page"];tid=tid or pending["table_id"]
        page=next((p for p in pages if p["id"]==pid),None)
        if not page and len(pages)==1:page=pages[0]
        if page and (page.get("generation_error") or page.get("unresolved")) and re.search(r"补全|补完|重新生成|重做|修复|重试|再生成",prompt):
            return self.retry_page(jid,exp,obj,page,prompt,opts,check)
        reply=intent or answer(self,exp,obj,page,prompt,opts,check,jid,tid)
        if isinstance(reply,str):return reply,{}
        action=reply.get("action")
        if action=="cancel":return "已取消这项修改，读数没有改变。",{}
        if action=="clarify":
            self.store.execute("INSERT OR REPLACE INTO worksheet_followups VALUES(?,?,?,?)",(exp,prompt,reply.get("page") or pid,reply.get("table") or tid))
            return reply["answer"],{}
        # Preserve the old explicit intent API; normal model replies carry their snapshot versions.
        reply.setdefault("page",page["id"] if page else "")
        reply.setdefault("table",tid)
        if action=="method":
            target=next((p for p in pages if p["id"]==reply["page"]),None)
            if not target:return "需要修改哪一个实验页？",{}
            if self.state(obj["id"])[0]!=reply.get("_state_version",self.state(obj["id"])[0]):
                raise ValueError("读数在理解请求期间已更新，请按最新状态修改方法。")
            result,updated=self.change_method(jid,exp,obj,target,prompt,opts,check,scope=reply.get("scope","calculation"))
            if not updated:self.store.execute("INSERT OR REPLACE INTO worksheet_followups VALUES(?,?,?,?)",(exp,prompt,target["id"],tid))
            return result,updated
        if action in ("fields","cells","window","rows","direction"):
            return apply_edit(self,jid,exp,obj,prompt,reply,check)
        return "请说明需要解释的问题或要修改的已有输入。",{}

    def change_method(self,jid,exp,obj,page,prompt,opts,check,scope="calculation"):
        from pathlib import Path
        from .worksheet_interaction import object_schema, submit, snapshot, execution_definition
        version,measurements,live=snapshot(self,obj,page,prompt)
        guide=(Path(self.app.registry.owner("ExperimentWorksheet")["directory"])/"authoring.md").read_text(encoding="utf-8")
        context=self.app.store.experiment(exp)
        catalog=evidence.materials(self.app,context)
        source_pages={(a["source_id"],a["page"]) for a in (self.store.anchor(i) for i in page["sources"])}
        refs={k:v for k,v in catalog.items() if (v["source"],v["number"]) in source_pages}
        text=guide+"\n仅处理指定页，通过 submit_worksheet_method 提交 plan（分析安排）和 page（复杂布局所需的完整修改页），或 changes（局部更新），或 question（需确认的选择）。保持各输入和列的 id、单位与物理含义；新单位或不同物理量另设字段。按请求同步结果、误差与图。此工具只修改定义，不能修改实际读数、选区或已有参数；如果用户只要这些状态变化，应提出澄清，不要改提示冒充已录入。"
        text+="\n本次 scope="+scope+("：只按用户要求修改呈现文字，保持计算和输入定义。" if scope=="presentation" else "：需要实际改变执行定义，不能只改文字说明。")
        draft=self.agent.experiment_drafts.current(exp)
        current=draft["content"] if draft else self.store.object(obj["content"]["draft_ref"]["id"],obj["content"]["draft_ref"]["revision"])["content"]
        part=next((m for m in current.get("measurement_methods",[]) if m["id"]==page["id"]),{})
        requirements={k:v for k,v in part.items() if k not in ("source_index","page")}
        contract=json.loads((Path(self.app.registry.owner("ExperimentWorksheet")["directory"])/"page_contract.json").read_text(encoding="utf-8"))
        change_properties = copy.deepcopy(contract["properties"]["results"]["items"]["properties"])
        result_changes = {"type":"array","items":object_schema(change_properties, ("id",))}
        plot_changes = {"type":"array","items":contract["properties"]["plots"]["items"]}
        local_changes={"results":result_changes, "plots":plot_changes}
        if "entry_steps" in contract["properties"]:
            local_changes["entry_steps"]=contract["properties"]["entry_steps"]
        changes=object_schema(local_changes)
        text += "\n更换现有拟合或公式时优先提交 changes.results，每项只给 id 和改动字段。图线变动用 changes.plots，以已有标题标识替换对应图或添加新图。只重排步骤时提交 changes.entry_steps；增加输入或更改表结构时才提交完整 page。实际方法变化在 plan 指出测量依据与不确定度处理。"
        tool={"type":"function","function":{"name":"submit_worksheet_method","description":"提交完整的方法定义修改，或需要澄清的问题；不能借此改实际读数。",
              "parameters":object_schema({"plan":{"type":"string"},"changes":changes,"page":contract,"question":{"type":"string"}})}}
        def accept(value):
            if not value.get("question") and (not value.get("plan") or bool(value.get("page")) == bool(value.get("changes"))):
                raise ValueError("方法修改需要 plan，并提交 changes 或 page，或提出明确 question")
        from .knowledge_notes import for_worksheet, worksheet_references
        note_text=for_worksheet(self.store,worksheet_references(self.store,exp,obj["content"]["draft_ref"]))
        new,_=submit(self,jid,tool,[{"role":"system","content":text},{"role":"user","content":canonical(live)+"\n正式草稿的本方法："+canonical(requirements)+"\n本次分析要求："+prompt+"\n相关原页："+evidence.texts(refs)+note_text}],opts,"method",check,accept)
        check()
        if new.get("question"):
            return new["question"],{}
        if new.get("changes"):
            replacement=copy.deepcopy(page)
            if "entry_steps" in new["changes"]:
                replacement["entry_steps"]=new["changes"]["entry_steps"]
            results={r["id"]:r for r in replacement["results"]}
            for edit in new["changes"].get("results",[]):
                if edit["id"] not in results:
                    raise ValueError("局部修改找不到本页结果 " + edit["id"])
                result=results[edit["id"]]
                if edit.get("expr",result["expr"]) != result["expr"]:
                    if "uncertainty" not in edit and result.get("uncertainty"):
                        raise ValueError("更换此结果的公式时须同时说明新的不确定度表达式")
                    if "note" not in edit:
                        result["note"]=new["plan"]
                result.update(edit)
            for plot in new["changes"].get("plots",[]):
                index=next((i for i,v in enumerate(replacement["plots"]) if v["title"]==plot["title"]),None)
                if index is None:
                    replacement["plots"].append(plot)
                else:
                    replacement["plots"][index]=plot
        else:
            replacement=new["page"]
        if len(replacement.get("pages",[]))==1:replacement=replacement["pages"][0]
        if "entry_steps" in page:
            replacement.setdefault("entry_steps",copy.deepcopy(page["entry_steps"]))
        replacement.update(id=page["id"],sources=list(page["sources"]))
        replacement.setdefault("title",page["title"])
        if page.get("unit_system"):replacement["unit_system"]=page["unit_system"]
        if page.get("check_dimensions"):
            replacement["check_dimensions"]=True
            updated={r["id"]:r for r in page["results"]}
            for result in replacement["results"]:
                previous=updated.get(result["id"],{})
                result.pop("source_anchor",None)
                if previous.get("source_anchor") and (previous["expr"]==result["expr"] or new.get("changes")):
                    result["source_anchor"]=previous["source_anchor"]
                basis=result.pop("basis",{}) or {}
                entry=refs.get(basis.get("page")) if isinstance(basis,dict) else None
                if entry:
                    quote=evidence.matched_passage(entry["text"],basis.get("quote"))
                    anchor=self.app.materials.make_anchor(entry["source"],entry["number"],quote,status="verified" if quote else "unverified")
                    result["source_anchor"]=anchor["id"]
                    if anchor["id"] not in replacement["sources"]:replacement["sources"].append(anchor["id"])
        before={p["id"]:p["unit"] for p in page["fields"]}
        after={p["id"]:p["unit"] for p in replacement["fields"]}
        def columns(p):
            return {(t["id"],c["id"]):c["unit"] for t in p["tables"] for c in t["columns"]}
        if not before.items()<=after.items() or not columns(page).items()<=columns(replacement).items():
            return "这个修改会改变已有读数的物理量或单位。请指定要新增的量和单位，原始列将继续保留。",{}
        validate_page(replacement)
        check()
        if self.state(obj["id"])[0]!=version or self.store.object(obj["id"])["revision"]!=obj["revision"]:
            raise ValueError("数据台在修改方法期间已更新，请按最新读数接续修改。")
        execution_changed=execution_definition(replacement)!=execution_definition(page)
        if scope=="calculation" and not execution_changed:
            return "这份方案只改变了说明，没有改变实际输入、取数范围或计算方法。需要写入哪个输入值或哪段范围？",{}
        if scope=="presentation" and execution_changed:
            return "这份文字修改还改变了计算定义，尚未保存。请确认是否也需要改变计算方法。",{}
        check()
        with self.store.lock:
            if self.state(obj["id"])[0]!=version or self.store.object(obj["id"])["revision"]!=obj["revision"]:
                raise ValueError("数据台在修改方法期间已更新，请按最新读数接续修改。")
            self.note_request(jid,exp,prompt,f"{page['title']} 的分析安排：{prompt}。{new['plan']}")
            spec=copy.deepcopy(obj["content"])
            spec["draft_ref"],spec["pages"]=self.commit_measurement_plan(jid,exp,[replacement if p["id"]==page["id"] else p for p in spec["pages"]],changed_method=(page["id"],new["plan"]))
            spec["sources"]=list(dict.fromkeys(s for p in spec["pages"] for s in p["sources"]))
            check()
            self.app.gateway.apply("ExperimentWorksheet",spec,experiment_id=exp,object_id=obj["id"],base_revision=obj["revision"])
        results=compute(replacement,measurements.get(page["id"],{}))
        self.agent.runtime.event(jid,"worksheet_method_applied",{"object":obj["id"],"page":page["id"],"readings_version":version,"results":results})
        reply="已保存本页的标题、标签或说明文字修改，实际输入值和计算定义保持原样。" if scope=="presentation" else "已更新本页的分析方法，并按现有读数重新计算。原始读数和输入值保留。"
        return reply,{"worksheet":obj["id"],"page":page["id"],"_committed":True}

    def answer(self,exp,obj,page,prompt,opts,check,jid=None):
        from .worksheet_interaction import answer
        return answer(self,exp,obj,page,prompt,opts,check,jid)
