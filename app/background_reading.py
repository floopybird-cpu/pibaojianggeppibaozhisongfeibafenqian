"""Course-scoped reading based on selected textbook pages and a fixed experiment draft."""
import copy
import json
import re
from pathlib import Path

from . import knowledge_index
from . import worksheet_evidence as evidence
from .experiment_drafts import PHASES
from .storage import canonical, digest, now
from .worksheets import decode_json


def is_reading_request(prompt):
    if re.search(r"不要.{0,5}(背景阅读|预习文稿)|实验册|记录表|同时.{0,12}(程序|处理台)", prompt):
        return False
    return bool(re.search(r"(写|做|给|生成|整理|导出|来).{0,30}(背景阅读|阅读材料|预习文稿)|^(背景阅读|阅读材料)(吧|一下|。)?$", prompt))


def reading_outline(project, current, plan):
    content = copy.deepcopy(current["content"]) if current else {
        "title": project["title"] + "实验草稿", "purpose": plan["purpose"],
        "source_strategy": plan["selection"], "stages": [], "sources": []}
    existing = {s["key"]: s for s in content["stages"]}
    ids = []
    for key, title in PHASES.items():
        if key in existing and key != "prestudy":
            continue
        stage = existing.get(key)
        if stage is None:
            stage = {"key": key, "title": title, "scope": "developed" if key == "prestudy" else "overview", "points": []}
            content["stages"].append(stage)
        points = plan["topics"] if key == "prestudy" else [
            {"name": title, "knowledge": plan["overview"][key], "question": "", "explain": ""}]
        for p in points:
            identity = "reading_" + digest(canonical([key, p]))[:14]
            if key == "prestudy":
                ids.append(identity)
            if any(old["id"] == identity for old in stage["points"]):
                continue
            stage["points"].append({"id": identity, "title": p["name"],
                "knowledge": [{"kind": "fact" if key == "prestudy" else "procedure", "text": p["knowledge"]}],
                "material_plan": plan["selection"] if key == "prestudy" else "采用本次讲义与实验备注。",
                "interpretations": [{"question": p["question"], "emphasis": p["explain"]}] if p["question"] else []})
    content["deliverables"] = list(dict.fromkeys([*content.get("deliverables", []), "prestudy"]))
    return content, ids


def generate(service, jid, exp, prompt, opts, check):
    project = service.store.experiment(exp)
    drafts = service.agent.experiment_drafts
    previous = drafts.current(exp)
    pack = service.app.registry.owner("LearningDocument")
    brief = (Path(pack["directory"]) / "background_reading.md").read_text("utf8")
    material = evidence.materials(service.app, project)
    plan_context = {"user": prompt, "experiment": project["title"], "notes": project["notes"],
                    "background": project["background"], "goal": project["goal"],
                    "course_material": evidence.texts(material)}
    if previous:
        plan_context["draft"] = [{"stage": s["title"], "points": [p["title"] for p in s["points"]]}
                                 for s in previous["content"]["stages"]]
    plan = decode_json(service.ask_model(brief + """
先做选题草稿。以用户要求和备注限定选材范围。只输出 JSON：
{experiments:[各项实际实验的名称，同一项的不同读数只列一次],purpose:阅读主旨,selection:材料选择理由,
queries:[精确的中英文教材检索词，按主题拆开],topics:[{name:概念名称,knowledge:定义或知识关系,question:需要解释的问题,explain:解释角度}],
overview:{procedure:按讲义概述实施,recording:按讲义概述读数,analysis:按讲义概述分析}}。""",
        canonical(plan_context), opts, "background_reading_outline"))
    names = list(dict.fromkeys(n.strip() for n in plan["experiments"] if isinstance(n, str) and n.strip()))
    if not names or not plan["topics"]:
        raise ValueError("阅读草稿缺少对应实验或物理主题。")
    check()
    draft, ids = reading_outline(project, previous, plan)
    drafts.save(exp, jid, draft, previous["id"] if previous else None, previous["revision"] if previous else None)
    query = "、".join(names) + "。" + plan["purpose"] + " " + "；".join(p["name"] for p in plan["topics"])
    # A reading covers several concepts. A single ranking of similar query variants
    # tends to displace the mathematical model by repeated matches to the apparatus.
    queries=evidence.valid_queries(plan["queries"])
    candidates=evidence.ranked_cards(service.store,project,[query,*queries])
    seen={(c["id"],c["revision"]) for c in candidates}
    for q in [p["question"]+" "+p["knowledge"] for p in plan["topics"]]+queries:
        for card in knowledge_index.search(service.store,q,pins=project.get("knowledge_refs",[]),limit=3):
            key=(card["id"],card["revision"])
            if key not in seen:
                candidates.append(card);seen.add(key)
    books={r["source_id"] for r in service.store.all("""SELECT b.source_id FROM kb_book b
        LEFT JOIN kb_source_roles r ON r.source_id=b.source_id WHERE coalesce(r.role,'reference')='reference'
        UNION SELECT source_id FROM kb_source_roles WHERE role='reference'""")}
    def textbook(card):
        return any(loc["source_id"] in books for loc in card.get("locations",[]))
    candidates=[card for card in candidates if textbook(card)]
    if not candidates:
        raise ValueError("未找到相关教材条目。请在资料库整理所需教材，或调整阅读主题。")
    selection=decode_json(service.ask_model(
        "为教材阅读草稿的每个主题选支撑原理的条目。区分研究对象、观测量与相邻概念。"
        "若候选只写特殊点或另一种观测，换查上位理论、几何模型或其数学表示；可以从更一般的模型推导。"
        "返回 JSON {entries:[候选编号],refine:[仍缺的专门检索词]}。已有条目足够时 refine 为空。",
        canonical({"outline":plan["topics"],"request":prompt,"cards":evidence.card_options(candidates)}),opts,"background_reading_sources"))
    cards=evidence.save_selection(service,jid,query,candidates,selection.get("entries",[]))
    refined=evidence.valid_queries(selection.get("refine"))
    if refined:
        extra=evidence.ranked_cards(service.store,project,refined)
        extra=[c for c in extra if textbook(c) and (c["id"],c["revision"]) not in {(v["id"],v["revision"]) for v in cards}]
        cards+=evidence.confirmed_cards(service,jid," ".join(refined),extra,opts)
    records = knowledge_index.hydrate(service.store, cards, vision=opts.get("native_vision", True))
    pages, anchors, owners, notes = {}, {}, {}, {}
    for record in records.values():
        ref = {"id": record["object_id"], "revision": record["revision"]}
        card = service.store.object(ref["id"], ref["revision"])
        keys = {}
        for aid in card["content"].get("sources", []):
            a = service.store.anchor(aid)
            keys.setdefault((a["source_id"], a["page"]), aid)
        for pg in record["originals"]:
            if pg["source"] not in books:
                continue
            k = pg["url"]
            if k not in owners:
                label = f"P{len(pages)+1}"
                pages[label] = pg
                anchors[label] = keys[(pg["source"], pg["number"])]
                owners[k] = (label, ref)
        if record["reading_notes"]:
            notes[record["title"]] = record["reading_notes"]
    if not pages:
        raise ValueError("知识库中未找到可核对原页的相关教材条目。请先在资料库整理相关教材，再生成背景阅读。")
    check()
    message = canonical({"course_scope": names, "request": prompt, "level": project["background"],
        "reading_outline": plan["topics"], "textbook_pages": evidence.texts(pages), "reading_notes": notes})
    images = evidence.image_blocks(service.app, pages, [k for k,p in pages.items() if not p["text"].strip() or re.search(r"[=∫∑]|fig(?:ure)?[ .]",p["text"],re.I)]) if opts.get("native_vision") else []
    answer = decode_json(service.ask_model(brief + """
只输出 JSON {title:物理主题名,lead:正文导言,sections:[{title:小节的物理主题,blocks:[{type:paragraph或equation,
value:中文段落或纯LaTeX数学式,pages:[支持本段的教材页代号如P1],evidence:source或derivation或explanation}]}]}。
先讲与学生所问观测匹配的物理关系。教材给出更一般的场或强度时，可自行求极值或推得所需结论并标为推导。标题宜简短，导言直接从物理内容写起；范围由宿主另行署明。编辑过程、证据缺口与其他文档的分工无需写进正文。区分物理不变量和每次均可重新测量的参数。
同一原页多次出现仍用同一个代号。每段给真实依据，推导交代物理假设。公式单列，行内量用 $...$。不输出工具名、页代号或脚注编号到正文，宿主依 pages 排版。""",
        [{"type":"text","text":message},*images] if images else message, opts, "background_reading_write"))
    sections, used = [], []
    for section in answer["sections"]:
        blocks = []
        for block in section["blocks"]:
            labels = list(dict.fromkeys(block["pages"]))
            if any(k not in pages for k in labels) or block["type"] not in ("paragraph", "equation"):
                raise ValueError("背景阅读含未核对的教材页或未知内容类型。")
            used.extend(labels)
            key = "latex" if block["type"] == "equation" else "text"
            blocks.append({"type": block["type"], key: block["value"], "evidence": block["evidence"],
                           "sources": [anchors[k] for k in labels]})
        sections.append({"id": f"section_{len(sections)+1}", "title": section["title"], "blocks": blocks})
    refs = {canonical(ref):ref for label,ref in owners.values() if label in used}
    scope = {"kind": "single" if len(names)==1 else "group", "experiments": names}
    byline = ("对应实验 · " if len(names)==1 else "对应实验组 · ") + "、".join(names)
    content = {"title": answer["title"], "doc_type": "prestudy", "reading_scope": scope,
        "summary": byline + "\n\n" + answer["lead"], "background": {"level":"未指定","goal":"理解相关物理概念",**project["background"]},
        "sections": sections, "appendices": [], "knowledge_refs": list(refs.values()), "warnings": [],
        "sources": list(dict.fromkeys(anchors[k] for k in used)), "draft_points": ids,
        "simulation": any(service.store.anchor(anchors[k])["metadata"].get("simulation") for k in used)}
    check()
    with drafts.work("submit_document", {}, exp, jid):
        obj = service.app.gateway.apply("LearningDocument", content, experiment_id=exp)
    return obj


def run_reading(service, jid, exp, prompt):
    store = service.store
    opts = json.loads(store.one("SELECT payload FROM jobs WHERE id=?", (jid,))["payload"])["request_options"]
    def check():
        if store.one("SELECT cancel FROM jobs WHERE id=?", (jid,))["cancel"]:
            raise InterruptedError("已取消背景阅读生成。")
    try:
        store.execute("UPDATE jobs SET status='running',updated=? WHERE id=?", (now(), jid))
        with service.agent.client.scope(service.agent.runtime, jid):
            obj = generate(service, jid, exp, prompt, opts, check)
        store.message(exp, "assistant", "已生成背景阅读《" + obj["title"] + "》，可从实验成果打开。", "model")
        store.execute("UPDATE jobs SET status='succeeded',progress=100,result=?,updated=? WHERE id=?",
                      (canonical({"document": obj["id"]}), now(), jid))
    except Exception as exc:
        error = service.agent.settings.redact(str(exc))
        store.message(exp, "assistant", error, "system")
        store.execute("UPDATE jobs SET status=?,error=?,updated=? WHERE id=?",
                      ("cancelled" if isinstance(exc, InterruptedError) else "failed", error, now(), jid))
    finally:
        with service.agent._lock:
            service.agent._active.discard(exp)
