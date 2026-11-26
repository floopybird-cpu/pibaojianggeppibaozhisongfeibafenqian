"""Source-bounded textbook reading. A batch is reusable only after its entries are saved."""
import json
import re
import threading
from collections import defaultdict
from pathlib import Path

from . import knowledge_index as kb
from .storage import canonical, digest, now
from .worksheet_evidence import image_blocks

STRING={"type":"string"}
INTEGER={"type":"integer"}
def array(item):
    return {"type":"array","items":item}
def record(**props):
    return {"type":"object","properties":props,"required":list(props),"additionalProperties":False}

PART=record(heading=STRING, explanation=STRING, pages=array(INTEGER))
NOTE=record(title=STRING, summary=STRING, terms=array(STRING), knowledge=STRING, conditions=array(STRING),
            observable=STRING, domain=STRING, pages=array(INTEGER), parts=array(PART))
EXTRACT=record(notes=array(NOTE), reused=array(record(id=STRING,pages=array(INTEGER))), skipped=array(record(page=INTEGER,reason=STRING)))
PLAN=record(topics=array(record(subject=STRING,source_ids=array(STRING),terms=array(STRING),coverage={"enum":["topic","whole"]})))
CARD=record(cards=array(record(id=STRING,summary=STRING,terms=array(STRING))))

def decode_record(raw):
    """Preserve TeX when a provider emits a literal backslash in a JSON string."""
    def escape(match):
        token=match.group()
        if token in (r'\\',r'\"',r'\/',r'\b',r'\f',r'\n',r'\r',r'\t') or re.fullmatch(r"\\u[0-9a-fA-F]{4}",token):
            return token
        # JSON whitespace escapes can legitimately precede an English word.
        if token[1] in "bfnrt" and not re.fullmatch(
                r"\\(?:frac|tfrac|binom|begin|beta|bar|big[a-zA-Z]*|bot|boxed|bullet|breve|bf|bmod|boldsymbol|backslash|flat|forall|fbox|nabla|nu|neq?|neg|notin|not|newline|newcommand|rm|rho|right[a-zA-Z]*|rangle|rvert|rightarrow|ref|rule|raisebox|text[a-zA-Z]*|theta|times|tau|tan|tanh|tilde|to|top|triangle[a-zA-Z]*|tag|tiny)",token):
            return token
        return "\\"+token
    normalized=re.sub(r'\\(?:[\\/" ]|u[0-9a-fA-F]{4}|[a-zA-Z]+|.)',escape,raw)
    return json.loads(normalized)


def is_save_request(text):
    return bool(re.search(r"(?:加入|加到|添加到|整理(?:到|进|入)|保存(?:到|进|入)|存(?:到|进|入)|写入|归入|收入|收录(?:到|进)?|录入|建成).{0,6}知识库",text)) and not re.search(r"(?:不要|不用|别|无需|不必).{0,4}(?:加入|整理|保存|存入|写入|录入)",text)


class KnowledgeWorkflow:
    def __init__(self, app):
        self.app=app
        self.store=app.store
        self.lock=threading.Lock()

    def ask(self, job, step, schema, instructions, content, opts):
        tool={"type":"function","function":{"name":step,"description":instructions,"parameters":schema}}
        messages=[{"role":"system","content":instructions},{"role":"user","content":content}]
        options={**opts,"_purpose":step,"_structured_output":True,"native_search":False}
        required=[k for k in (schema.get("required") or [])]
        def usable(value):
            return isinstance(value,dict) and bool(value) and all(k in value for k in required)
        def call(conversation):
            with self.app.agent.client.scope(self.app.agent.runtime,job):
                reply,usage=self.app.agent.client.complete(conversation,tools=[tool],options=options)
            calls=reply.get("tool_calls") or []
            return calls[0]["function"]["arguments"] if calls else (reply.get("content") or "")
        def parse(raw):
            try:return decode_record(raw)
            except (ValueError,TypeError):return None
        hint=("上一条回复不是可解析的 "+step+" 结果。只输出一个符合给定 schema 的 JSON 对象，必须包含 "+
            "、".join(required)+"，不要解释，不要 Markdown 代码块。")
        raw=call(messages)
        parsed=parse(raw)
        if usable(parsed):return parsed
        # A provider may answer in prose, hand back truncated arguments, or (with
        # page images attached) return an empty tool call. Re-ask, then re-ask
        # again on the text alone so one bad turn cannot fail the batch.
        attempts=[[*messages,{"role":"assistant","content":(raw or "")[-6000:]},{"role":"user","content":hint}]]
        if isinstance(content,list) and any(item.get("type")=="image_url" for item in content if isinstance(item,dict)):
            plain="\n\n".join(item.get("text","") for item in content if isinstance(item,dict) and item.get("type")=="text")
            attempts.append([{"role":"system","content":instructions},{"role":"user","content":plain},
                {"role":"assistant","content":(raw or "")[-6000:]},
                {"role":"user","content":hint+"原页图不再重复附上，只依据给出的文字作答。"}])
        for conversation in attempts:
            raw=call(conversation)
            parsed=parse(raw)
            if usable(parsed):return parsed
        raise ValueError("模型没有返回可解析的结构化结果。已完成的知识和页段仍保留，可重试；反复出现请在设置里换用支持工具调用的模型。")

    def check(self,job):
        if self.store.one("SELECT cancel FROM jobs WHERE id=?",(job,))["cancel"]:
            raise InterruptedError("已停止整理；已保存的知识和页段可继续使用")

    def sources(self,exp):
        sources=self.store.all("SELECT id,name FROM sources WHERE kind IN ('pdf','docx','md','txt','png','jpg','jpeg') AND archived='' ORDER BY name")
        if exp:
            selected=set(self.store.experiment(exp)["source_ids"])
            if selected:
                return [source for source in sources if source["id"] in selected]
        return sources

    def plan(self,job,topic,sources,opts):
        key=digest(canonical(["topic-plan-v2",topic,sources]))
        cached=self.store.one("SELECT response FROM kb_batches WHERE fingerprint=?",(key,))
        if cached:return json.loads(cached["response"])["topics"]
        result=self.ask(job,"plan_knowledge_search",PLAN,
            "将用户的存库要求分解为可检索主题，为每个主题从候选资料选文件并给中英文专业检索词。仅当用户明确要整理指定资料全书时选 whole；只做某个主题时选 topic。只输出检索意图，不推断教材内容。",
            canonical({"request":topic,"materials":sources}),opts)
        ids={s["id"] for s in sources}
        for t in result["topics"]:
            if not t["subject"].strip() or t["coverage"] not in ("whole","topic") or (t["coverage"]=="topic" and not t["terms"]) or not t["source_ids"] or not set(t["source_ids"])<=ids:
                raise ValueError("主题或来源指向无效")
        self.store.execute("INSERT OR REPLACE INTO kb_batches VALUES(?,?,?)",(key,job,canonical(result)))
        return result["topics"]

    def batches(self,sid,numbers):
        # Keep adjacent original pages together; every requested page appears exactly once.
        wanted=set(numbers);group=[];size=0;last=0;chapter=None
        rows=self.store.all("""SELECT p.number,length(p.text) chars,m.chapter FROM pages p JOIN kb_page_meta m
            ON m.source_id=p.source_id AND m.page=p.number WHERE p.source_id=? ORDER BY p.number""",(sid,))
        for row in rows:
            n=row["number"]
            if n not in wanted:continue
            major=row["chapter"].split(" / ")[0]
            if group and (n!=last+1 or major!=chapter or size+row["chars"]>21000 or len(group)>=9):
                yield group;group=[];size=0
            group.append(n);size+=row["chars"];last=n;chapter=major
        if group:yield group

    def confirm_reuse(self,job,source,proposal,opts):
        obj=self.store.object(proposal["id"])
        pages=sorted(set(proposal["pages"]))
        key=digest(canonical(["compare-original-v2",source["id"],pages,obj["id"],obj["revision"]]))
        receipt=self.store.one("SELECT response FROM kb_batches WHERE fingerprint=?",(key,))
        if receipt:return json.loads(receipt["response"])
        originals={}
        for n in pages:
            originals[f"p{n}"]=self.store.one("SELECT text,image FROM pages WHERE source_id=? AND number=?",(source["id"],n))
        existing={k:v for k,v in obj["content"].items() if k in ("title","definition","conditions","observables","latex","approximations")}
        existing["explanations"]=[c["text"] for c in obj["content"].get("claims",[]) if c["text"]!=existing.get("definition")]
        request=canonical({"existing":existing,
            "pages":{k:v["text"] for k,v in originals.items()}})
        if opts.get("native_vision"):
            request=[{"type":"text","text":request},*image_blocks(self.app,originals,[k for k,v in originals.items() if v["image"]])]
        schema=record(same={"type":"boolean"},notes=array(NOTE))
        instructions=("比较选中条目的实际知识和给定原页。模型、物理命题及条件相同且无独立新增内容才返回 same=true、notes=[]，"
            "有实质差异时 same=false，把独立新知识按教材规范写进 notes，须涵盖给定页码。"+
            (Path(self.app.registry.owner("SciencePoint")["directory"])/"textbook_reader.md").read_text("utf8"))
        value=self.ask(job,"confirm_textbook_reuse",schema,instructions,request,opts)
        if type(value.get("same")) is not bool or not isinstance(value.get("notes"),list) or value["same"]==bool(value["notes"]):
            raise ValueError("知识复用比较未给出明确结论")
        if not value["same"] and self.validate({"notes":value["notes"],"skipped":[]},pages)!=set(pages):
            raise ValueError("差异条目没有覆盖需要核对的原页")
        self.store.execute("INSERT OR IGNORE INTO kb_batches VALUES(?,?,?)",(key,job,canonical(value)))
        return value

    def read_batch(self,job,source,nums,topic,opts):
        from .worksheet_evidence import image_blocks
        book=source["id"];key=digest(canonical(["textbook-notes-v1",book,nums,topic]))
        cached=self.store.one("SELECT response FROM kb_batches WHERE fingerprint=?",(key,))
        if cached:
            result=json.loads(cached["response"])
        else:
            records={};chunks=[];chapters=[]
            for n in nums:
                row=self.store.one("""SELECT p.text,p.image,m.chapter FROM pages p JOIN kb_page_meta m
                    ON m.source_id=p.source_id AND m.page=p.number WHERE p.source_id=? AND p.number=?""",(book,n))
                tag=f"p{n}";records[tag]=row;chapters.append(row["chapter"])
                chunks.append(f"[{tag}] {row['chapter']}\n{row['text']}")
            pack=self.app.registry.owner("SciencePoint")
            instructions=(Path(pack["directory"])/"textbook_reader.md").read_text(encoding="utf8")
            content="文件："+source["name"]+"\n任务："+(topic or "建立这部教材可检索的知识条目")+"\n\n"+"\n\n".join(chunks)
            old=kb.search(self.store," ".join(dict.fromkeys(chapters)),source_id=book,limit=12) if any(chapters) else []
            adjacent=self.store.all("""SELECT DISTINCT c.object_id AS id,c.title,c.summary FROM kb_cards c JOIN objects o
                ON o.id=c.object_id AND o.revision=c.revision JOIN revisions r ON r.object_id=c.object_id AND r.revision=c.revision
                JOIN json_each(r.sources) ref JOIN anchors a ON a.id=ref.value WHERE a.source_id=? AND a.page BETWEEN ? AND ?
                ORDER BY a.page DESC LIMIT 12""",(book,max(1,min(nums)-6),max(nums)+1))
            offered={c["id"]:{"id":c["id"],"title":c["title"],"summary":c["summary"]} for c in [*old,*adjacent] if c["summary"]}
            if offered:
                content+="\n已有知识候选（只在结论与条件均相同才复用）："+canonical(list(offered.values()))
            images=[tag for tag,r in records.items() if r["image"] and (len(r["text"].strip())<80 or re.search(r"[=∫∑∂]|fig(?:ure)?\.|图",r["text"],re.I))]
            if images and opts.get("native_vision",True):
                content=[{"type":"text","text":content},*image_blocks(self.app,records,images)]
            elif any(len(r["text"].strip())<25 and r["image"] for r in records.values()):
                raise ValueError("所选页含仅有图像的原页，请启用原生图像后接续读取")
            result=self.ask(job,"extract_textbook_notes",EXTRACT,instructions,content,opts)
            result["_offered"]=list(offered)
            self.validate(result,nums)
            self.store.execute("INSERT OR REPLACE INTO kb_batches VALUES(?,?,?)",(key,job,canonical(result)))
        reviewed=self.validate(result,nums)
        saved=[];notes=list(result["notes"])
        for item in result.get("reused",[]):
            self.check(job)
            verdict=self.confirm_reuse(job,source,item,opts)
            if not verdict["same"]:
                notes.extend(verdict["notes"])
                continue
            obj=self.store.object(item["id"])
            content=dict(obj["content"])
            anchors=[self.app.materials.make_anchor(book,n)["id"] for n in item["pages"]]
            sources=list(dict.fromkeys([*content["sources"],*anchors]))
            if sources!=content["sources"]:
                content["sources"]=sources
                self.app.gateway.apply(obj["kind"],content,object_id=obj["id"],base_revision=obj["revision"])
            saved.append(obj["id"])
        for note in notes:
            self.check(job)
            origin=(book,digest(canonical([key,note])))
            found=self.store.one("SELECT object_id FROM kb_notes WHERE source_id=? AND identity=?",origin)
            if found:
                saved.append(found["object_id"]);continue
            by_page={n:self.app.materials.make_anchor(book,n)["id"] for n in dict.fromkeys(note["pages"])}
            anchors=list(by_page.values())
            claims=[dict(text=part["heading"]+"\n"+part["explanation"],
                         sources=[by_page[n] for n in dict.fromkeys(part["pages"])],status="source_supported")
                    for part in note.get("parts",[])]
            content=dict(title=note["title"],search_summary=note["summary"],search_terms=note["terms"],
                definition=note["knowledge"],conditions=note["conditions"],observables=[note["observable"]],
                claims=claims or [dict(text=note["knowledge"],sources=anchors,status="source_supported")],sources=anchors,
                domains=[note["domain"]],formula_refs=[],prerequisites=[],measurement_relations=[],error_relations=[])
            gateway=self.app.gateway
            gateway._workflow.knowledge_origin=origin
            try:
                obj=gateway.import_knowledge("SciencePoint",content)
            finally:
                gateway._workflow.knowledge_origin=None
            # A canonical merge can return an existing unchanged revision.
            self.store.execute("INSERT OR IGNORE INTO kb_notes VALUES(?,?,?)",(*origin,obj["id"]))
            saved.append(obj["id"])
        with self.store.transaction() as conn:
            conn.executemany("INSERT OR IGNORE INTO kb_done VALUES(?,?,?,?)",[(book,n,topic,job) for n in reviewed])
        missing=[n for n in nums if n not in reviewed]
        if missing:
            self.check(job)
            if reviewed:
                saved.extend(self.read_batch(job,source,missing,topic,opts))
            elif len(missing)>1:
                mid=len(missing)//2
                for group in (missing[:mid],missing[mid:]):
                    saved.extend(self.read_batch(job,source,group,topic,opts))
            else:
                raise ValueError(f"文件第 {missing[0]} 页没有获得知识或跳过依据，已保留其他进度")
        return saved

    @staticmethod
    def validate(result,nums):
        if not isinstance(result.get("notes"),list) or not isinstance(result.get("skipped"),list):
            raise ValueError("缺少知识条目或原页处理回执")
        seen=set()
        for n in result["notes"]:
            if not all(isinstance(n.get(k),str) and n[k].strip() for k in ("title","summary","knowledge","observable","domain")):
                raise ValueError("条目需有标题、独立说明、知识和可观察量")
            if not n.get("pages") or not n.get("conditions") or not n.get("terms"):
                raise ValueError("请核对原页、术语及适用条件")
            for part in n.get("parts",[]):
                if not all(isinstance(part.get(k),str) and part[k].strip() for k in ("heading","explanation")):
                    raise ValueError("知识段落缺少小标题或讲解")
                if not part.get("pages") or not set(part["pages"])<=set(n["pages"]):
                    raise ValueError("段落须引用本知识单元提供的实际原页")
            seen.update(n["pages"])
        for item in result.get("reused",[]):
            if item["id"] not in result.get("_offered",[]) or not item.get("pages"):
                raise ValueError("复用知识必须对应本批提供的既有条目及实际原页")
            seen.update(item["pages"])
        for item in result["skipped"]:
            if not item.get("reason","").strip():raise ValueError("跳过原页须说明该页内容")
            seen.add(item["page"])
        if not seen<=set(nums):raise ValueError("条目引用了本批未提供的原页")
        return seen

    def run(self,job,exp,prompt,source_id=None):
        agent=self.app.agent
        try:
            with self.lock:
                self.check(job)
                self.store.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(now(),job))
                payload=json.loads(self.store.one("SELECT payload FROM jobs WHERE id=?",(job,))["payload"])
                opts={**agent.settings.read(),**payload.get("request_options",{})}
                topic=prompt if exp else payload.get("topic","")
                self.app.agent.runtime.checkpoint(job,{"knowledge_ingest":True,"activity":"knowledge_ingest","source_id":source_id,"prompt":prompt})
                sources=self.sources(exp)
                if source_id:sources=[s for s in sources if s["id"]==source_id]
                if not sources:raise ValueError("请先导入可阅读的教材")
                plan=self.plan(job,topic,sources,opts) if topic else []
                work=defaultdict(set)
                source_map={s["id"]:s for s in sources}
                for s in sources:
                    sid=s["id"]
                    if topic and not any(sid in t["source_ids"] for t in plan):continue
                    self.store.execute("INSERT OR IGNORE INTO kb_book(source_id) VALUES(?)",(sid,))
                    pages={r["page"] for r in kb.find_pages(self.store,sid)}
                    if payload.get("pages"):pages.intersection_update(payload["pages"])
                    if not topic:
                        work[sid,""]=pages
                    else:
                        for t in plan:
                            if sid not in t["source_ids"]:continue
                            if t["coverage"]=="whole":
                                work[sid,""].update(pages)
                            else:
                                hits=kb.find_pages(self.store,sid," ".join(t["terms"]),limit=8)
                                work[sid,t["subject"]].update(r["page"] for r in hits if r["page"] in pages)
                total=sum(map(len,work.values()));done=0;ids=set();reused=0
                if not total:raise ValueError("有效范围内没有找到相关原页，可换主题或指定页段")
                for (sid,subject),pages in work.items():
                    covered={r["page"] for r in self.store.all("SELECT page FROM kb_done WHERE source_id=? AND (topic='' OR topic=?)",(sid,subject))}
                    old=len(pages&covered);done+=old;reused+=old
                    for batch in self.batches(sid,pages-covered):
                        self.check(job)
                        ids.update(self.read_batch(job,source_map[sid],batch,subject,opts));done+=len(batch)
                        self.store.execute("UPDATE jobs SET progress=?,updated=? WHERE id=?",(int(done*100/total),now(),job))
                # Include entries from earlier attempts of this same resumable job.
                for receipt in self.store.all("SELECT fingerprint,response FROM kb_batches WHERE job_id=?",(job,)):
                    for note in json.loads(receipt["response"]).get("notes",[]):
                        key = digest(canonical([receipt["fingerprint"],note]))
                        entry = self.store.one("SELECT object_id FROM kb_notes WHERE identity=?", (key,))
                        if entry: ids.add(entry["object_id"])
                from .knowledge_catalog import organize
                catalogs={sid:organize(self,job,sid,opts) for sid in {key[0] for key in work}}
                result={"pages":total,"reused_pages":reused,"entries":sorted(ids),"catalogs":catalogs}
                message=f"已处理 {total} 页，其中 {reused} 页沿用已完成记录。本次保存 {len(ids)} 条知识。可在“资料与知识 → 知识库”查看说明和原页。"
                if exp:self.store.message(exp,"assistant",message,"model")
                self.store.execute("UPDATE jobs SET status='succeeded',progress=100,result=?,updated=? WHERE id=?",(canonical(result),now(),job))
        except Exception as exc:
            status="cancelled" if isinstance(exc,InterruptedError) else "failed"
            error=agent.settings.redact(str(exc))
            self.store.execute("UPDATE jobs SET status=?,error=?,updated=? WHERE id=?",(status,error,now(),job))
            if exp:self.store.message(exp,"assistant",error,"system_notice")
        finally:
            if exp:
                with agent._lock:agent._active.discard(exp)

    def queue_description(self,identity,revision):
        from .storage import uid
        card=self.store.one("SELECT summary FROM kb_cards WHERE object_id=? AND revision=?",(identity,revision))
        if not card:raise ValueError("请先选择一条知识")
        if card["summary"]:return {"already_processed":True}
        with self.store.transaction() as conn:
            existing=conn.execute("""SELECT id FROM jobs WHERE kind='knowledge_card' AND status IN ('queued','running')
                AND json_extract(payload,'$.id')=? AND json_extract(payload,'$.revision')=?""",(identity,revision)).fetchone()
            if existing:return {"id":existing["id"]}
            job=uid("kcard")
            conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,0,?,?)",(job,"knowledge_card","queued",0,canonical({"id":identity,"revision":revision}),None,None,now(),now()))
        threading.Thread(target=self.describe,args=(job,identity,revision),daemon=True).start()
        return {"id":job}

    def describe(self,job,identity,revision):
        try:
            self.store.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(now(),job))
            self.check(job)
            obj,pages=kb.verified(self.store,identity,revision)
            if not pages:raise ValueError("原页未找到，无法生成有依据的说明")
            opts={**self.app.agent.settings.read(),"thinking_depth":"low"}
            context=canonical({"id":identity,"title":obj["title"],"content":obj["content"],
                "originals":[{"file_page":p["title"],"text":p["text"]} for p in pages]})
            if opts.get("native_vision"):
                originals={p["title"]:p for p in pages}
                context=[{"type":"text","text":context},*image_blocks(self.app,originals,originals)]
            result=self.ask(job,"describe_saved_knowledge",CARD,"根据原页和已有条目，仅撰写一句能说明适用问题的中文检索说明，附中英文专业术语。保持知识内容与修订。",context,opts)
            cards=result["cards"]
            if len(cards)!=1 or cards[0]["id"]!=identity:raise ValueError("返回的说明没有对应选定条目")
            self.check(job)
            kb.save_description(self.store,identity,revision,cards[0]["summary"],cards[0]["terms"])
            self.store.execute("UPDATE jobs SET status='succeeded',progress=100,updated=? WHERE id=?",(now(),job))
        except Exception as exc:
            self.store.execute("UPDATE jobs SET status=?,error=?,updated=? WHERE id=?",("cancelled" if isinstance(exc,InterruptedError) else "failed",self.app.agent.settings.redact(str(exc)),now(),job))
