"""Page- and revision-addressable evidence for creating worksheets and answering questions."""
import base64
import re
from collections import Counter
from pathlib import Path
from urllib.parse import quote as urlquote, urlsplit
from concurrent.futures import ThreadPoolExecutor

from .network import TextHTML, web_search, webpage


def materials(app, exp, *, include_references=False, reference_cards=()):
    catalog = {}
    for index, source in enumerate(exp["source_ids"], 1):
        src = app.store.one("SELECT name,kind FROM sources WHERE id=?", (source,))
        if src["kind"] in ("csv","tsv","xlsx","dat"):
            continue
        scope = app.store.one("""SELECT b.first_page,b.last_page,coalesce(r.role,'reference') AS role
            FROM kb_book b LEFT JOIN kb_source_roles r ON r.source_id=b.source_id WHERE b.source_id=?""",(source,))
        relevant_pages = []
        if scope and scope["role"]=="reference" and not include_references:
            for card in reference_cards:
                relevant_pages.extend(loc["page"] for loc in card["locations"] if loc["file"]==src["name"] and loc["source_id"]==source)
            if not relevant_pages:
                continue
        query = "SELECT number,label,text,image FROM pages WHERE source_id=?"
        args = [source]
        if relevant_pages:
            query += " AND number IN ("+",".join("?" for _ in relevant_pages)+")"
            args.extend(relevant_pages)
        if scope and scope["first_page"] is not None:
            query += " AND number BETWEEN ? AND ?"
            args.extend([scope["first_page"],scope["last_page"]])
        for page in app.store.all(query+" ORDER BY number", args):
            n = page["number"]
            from .source_math import formulas_for_source
            printed = formulas_for_source(app.store, source, n) if "=" in (page["text"] or "") else {}
            catalog[f"D{index}p{n}"] = dict(title=f"{src['name']} · 第{n}页", text=app.materials.page_text(source,n),
                url=f"physlab://page/{source}/{n}",source=source,number=n,image=page["image"],printed_formulas=list(dict.fromkeys(printed.values())))
    return catalog


def knowledge_text(value):
    if isinstance(value, dict):
        return "\n".join(f"{key}：{knowledge_text(item)}" for key,item in value.items())
    if isinstance(value, list):
        return "\n".join(knowledge_text(item) for item in value)
    return str(value)


QUERY_GUIDE = ("把读者的问法写成适合查物理教材的检索式，返回 queries 字符串数组。先辨认研究对象、"
    "可观察现象、所求的量或关系和会改变答案的条件。根据上下文解析指代，给出教材常用的中英文术语或等价问法。"
    "保留原问题，未确定的实验方法只作为可能的检索方向；多个独立问题分开搜。搜词无需包含答案、套话或无关的同义词。")


def valid_queries(value):
    if not isinstance(value,list):
        return []
    return list(dict.fromkeys(q.strip() for q in value if isinstance(q,str) and q.strip()))


def query_context(service,project):
    store=service.store
    recent=store.all("SELECT content FROM messages WHERE experiment_id=? AND role='user' ORDER BY rowid DESC LIMIT 3",(project["id"],))
    prior=store.one("""SELECT e.blob FROM runtime_events e JOIN jobs j ON j.id=e.job_id
        WHERE e.event='knowledge_selected' AND json_extract(j.payload,'$.experiment_id')=?
        ORDER BY e.rowid DESC LIMIT 1""",(project["id"],))
    previous=[]
    if prior:
        for url in service.agent.runtime.read_blob(prior["blob"]).get("entries",[]):
            identity,revision=url.rsplit("/",2)[-2:]
            card=store.one("SELECT title FROM kb_cards WHERE object_id=? AND revision=?",(identity,revision))
            if card:previous.append(card["title"])
    return {"experiment":project.get("title",""),"goal":project.get("goal",""),"notes":project.get("notes",""),"previous_knowledge":previous,
            "recent_questions":[r["content"] for r in reversed(recent)]}


def discover(service,job,project,question,options):
    from .storage import canonical
    from .worksheets import decode_json
    if not service.store.one("SELECT 1 FROM kb_cards LIMIT 1"):
        return []
    plan=None
    if job:
        last=service.store.one("SELECT blob FROM runtime_events WHERE job_id=? AND event='knowledge_queries' ORDER BY rowid DESC LIMIT 1",(job,))
        if last:
            data=service.agent.runtime.read_blob(last["blob"])
            if data["question"]==question:plan=data["queries"]
    if plan is None:
        result=decode_json(service.ask_model(QUERY_GUIDE+"只输出 JSON。",canonical({"question":question,
            "context":query_context(service,project)}),{**options,"thinking_depth":"low"},"plan_knowledge_lookup"))
        plan=valid_queries(result.get("queries"))
        if job:service.agent.runtime.event(job,"knowledge_queries",{"question":question,"queries":plan})
    return ranked_cards(service.store,project,[question,*plan])


def ranked_cards(store,project,queries):
    from .knowledge_index import search
    # Combine separate search intents without letting one verbose synonym list dominate.
    from .knowledge_catalog import distinct_evidence,find_topics
    scores={};all_cards={};topic_scores={}
    for query in dict.fromkeys(queries):
        topics=find_topics(store,query,limit=3)
        for rank,topic in enumerate(topics,1):
            topic_scores[topic["id"]]=topic_scores.get(topic["id"],0)+1/(5+rank)
        rows=search(store,query,pins=project.get("knowledge_refs",[]),limit=12)
        if not rows and topics:
            # The unit name may express a concept none of its individual cards names.
            leaders=store.all("""SELECT DISTINCT o.id,o.revision,c.title,c.summary,c.keywords,o.kind FROM kb_topic_members m
                JOIN objects o ON o.id=m.object_id AND o.revision=m.revision JOIN kb_cards c
                ON c.object_id=o.id AND c.revision=o.revision WHERE m.category_id=? AND m.role='core'""",(topics[0]["id"],))
            rows=kb_locations(store,leaders,project.get("knowledge_refs",[]))
        for rank,card in enumerate(rows,1):
            key=(card["id"],card["revision"])
            scores[key]=scores.get(key,0)+1/(5+rank)
            all_cards[key]=card
    from .knowledge_catalog import annotated
    ordered=annotated(store,list(all_cards.values()))
    ordered.sort(key=lambda c:scores[c["id"],c["revision"]]+0.25*topic_scores.get((c.get("topic") or {}).get("category_id"),0),reverse=True)
    return distinct_evidence(store,ordered,project.get("knowledge_refs",[]))[:8]


def kb_locations(store,rows,pins):
    import json
    from .knowledge_index import with_locations
    fixed={r["id"]:r["revision"] for r in pins}
    return with_locations(store,[dict(r,keywords=json.loads(r["keywords"])) for r in rows
        if fixed.get(r["id"],r["revision"])==r["revision"]])


def card_options(cards):
    return [{"number":i,"title":c["title"],"summary":c["summary"],**({"topic":c["topic"]["path"]} if c.get("topic") else {})} for i,c in enumerate(cards,1)]


CHOOSE = "从简短说明中选择足以直接支持本问题的最少条目。仅因同领域、共用术语或举例场景相同应忽略；优先能支撑核心因果关系的条目。教材例题若依赖题目未给的装置、几何或量级，只可帮助解释对应条件，优先寻找一般判据。多个条目可互补。同义重复只选最贴切的一条。entries 返回候选编号数组；均无关时为空数组。"


def save_selection(service,jid,query,cards,numbers):
    if not isinstance(numbers,list) or not all(type(v) is int and 1<=v<=len(cards) for v in numbers):
        raise ValueError("知识选择含未知条目编号")
    chosen=[cards[n-1] for n in dict.fromkeys(numbers)]
    if jid:
        service.agent.runtime.event(jid,"knowledge_choice",{"query":query,
            "candidates":[[c["id"],c["revision"]] for c in cards],
            "chosen":[[c["id"],c["revision"]] for c in chosen]})
    return chosen


def confirmed_cards(service,jid,query,cards,options,project=None):
    if not cards:
        return []
    if jid:
        receipt=service.store.one("SELECT blob FROM runtime_events WHERE job_id=? AND event='knowledge_choice' ORDER BY rowid DESC LIMIT 1",(jid,))
        if receipt:
            previous=service.agent.runtime.read_blob(receipt["blob"])
            if previous["query"]==query and previous["candidates"]==[[c["id"],c["revision"]] for c in cards]:
                by_key={(c["id"],c["revision"]):c for c in cards}
                return [by_key[tuple(key)] for key in previous["chosen"]]
    from .worksheets import decode_json
    from .storage import canonical
    def choose(candidates,may_refine):
        instruction=CHOOSE
        if may_refine:
            instruction+=" 候选明显搜错义项且有更合适的专业术语时，另给 refine 检索式数组。已选条目足够或本库没有该主题时留空。"
        return decode_json(service.ask_model(instruction+"只输出 JSON。",
            canonical({"request":query,"cards":card_options(candidates)}),
            {**options,"thinking_depth":"low"},"choose_knowledge_cards"))
    choice=choose(cards,project is not None)
    revised=valid_queries(choice.get("refine"))
    if project and revised and not choice.get("entries"):
        prior_keys={(c["id"],c["revision"]) for c in cards}
        extra=[c for c in ranked_cards(service.store,project,revised) if (c["id"],c["revision"]) not in prior_keys]
        if jid:service.agent.runtime.event(jid,"knowledge_refinement",{"queries":revised,"new_candidates":[c["id"] for c in extra]})
        if extra:
            selected=save_selection(service,jid,query,extra,choose(extra,False).get("entries"))
            return selected
    return save_selection(service,jid,query,cards,choice.get("entries"))



def selected_knowledge(app, exp, query, *, service=None, jid=None, options=None):
    from . import knowledge_index as kb
    if service is None:
        cards=kb.search(app.store,query,pins=exp.get("knowledge_refs",[]),limit=8)
    else:
        candidates=discover(service,jid,exp,query,options or {})
        cards=confirmed_cards(service,jid,query,candidates,options or {},exp)
    return kb.hydrate(app.store,cards,vision=(options or {}).get("native_vision",True))


def terms(s):
    result = Counter(re.findall(r"[a-z0-9]+",s.lower()))
    for word in re.findall(r"[\u3400-\u9fff]+",s):
        result.update(word[i:i+2] for i in range(len(word)-1))
        if len(word)==1:
            result[word]+=1
    return result


def relevant(records,query,count):
    import math
    wanted=terms(query)
    bags={k:terms(v["title"]+" "+v["text"]) for k,v in records.items()}
    df=Counter(t for b in bags.values() for t in b.keys() & wanted.keys())
    def score(k):
        title=terms(records[k]["title"])
        return sum((1+math.log((len(bags)+1)/(1+df[t]))) * (min(bags[k][t],3)+2*min(title[t],2)) for t in wanted)
    ranked=sorted(records,key=score,reverse=True)
    return {k:records[k] for k in ranked[:count] if score(k)>0}


def texts(records):
    lines=[]
    originals={}
    for key,item in records.items():
        text=f"[{key}] {item['title']}\n{item['text']}"
        if item.get("printed_formulas"):text+="\n原页上下标排印　"+"　".join(item["printed_formulas"])
        if item.get("originals"):
            text+="\n须对照原页："+"、".join(p["title"]+" ("+p["url"]+")" for p in item["originals"])
            originals.update({p["url"]:p for p in item["originals"]})
        if item.get("reading_notes"):
            from .knowledge_notes import describe
            text+="\n助手以往的核对备注（与教材原文分别引用）：\n"+"\n".join(
                n["name"]+f" · 第{n['page']}页 "+describe(n) for n in item["reading_notes"])
        lines.append(text)
    lines.extend("核对原文 · "+p["title"]+"\n"+p["text"] for p in originals.values())
    return "\n\n".join(lines)


def knowledge_images(app,records):
    originals={p["url"]:p for item in records.values() for p in item.get("originals",[])
               if p["image"] and (not p["text"].strip() or re.search(r"[=∑∫∂]|fig(?:ure)?\.|图",p["text"],re.I))}
    return image_blocks(app,originals,originals)


def outline_figure_pages(catalog):
    """Rank formula-bearing originals within each source; all page text is sent independently."""
    from collections import defaultdict
    grouped=defaultdict(list)
    selected=[]
    for key,page in catalog.items():
        if not page.get("image"):continue
        text=page["text"]
        if len(text.strip())<12:
            selected.append(key)
            continue
        score=sum(bool(re.search(p,text,re.M)) for p in (
            r"[=＝≈∫∑]",r"\([0-9]+\)|（[0-9]+）",r"数据处理|测量公式|计算公式|测量原理|实验原理",
            r"式中|由式|代入|公式|推导",r"示意图|接线图|电路图|结构图"))
        if score>=2:grouped[page["source"]].append((score,key))
    for entries in grouped.values():
        selected.extend(k for _,k in sorted(entries,key=lambda v:-v[0])[:3])
    return list(dict.fromkeys(selected))


def image_blocks(app, catalog, keys):
    from PIL import Image
    from io import BytesIO
    blocks=[]
    for k in dict.fromkeys(keys):
        item=catalog.get(k,{})
        if not item.get("image"):
            continue
        with Image.open(app.store.asset_path(item["image"])) as im:
            im.thumbnail((1600,1600))
            buf=BytesIO(); im.convert("RGB").save(buf,"JPEG",quality=85)
        blocks.extend([{"type":"text","text":k+" 原页图（含公式与上下标）"},{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+base64.b64encode(buf.getvalue()).decode("ascii")}}])
    return blocks


def web_evidence(agent, query, options, diagnostics=None, check=None):
    check=check or (lambda:None)
    def failed(stage,err,url=""):
        if diagnostics:diagnostics({"stage":stage,"query":query,"url":url,"error":str(err)})
    candidates=[]
    check()
    if options.get("native_search"):
        try:
            response=agent.client.provider_search(query,count=5,options=options)
            candidates=response.get("results",[])
        except InterruptedError:raise
        except (ValueError, OSError) as err:
            failed("provider_search",err)
    check()
    if not candidates:
        try:candidates=web_search(query)
        except InterruptedError:raise
        except (ValueError,OSError) as err:
            failed("web_search",err);raise
    check()
    def open_result(item):
        check()
        url=item.get("url","")
        if urlsplit(url).scheme!="https":return None
        snippet=item.get("content") or item.get("snippet") or ""
        title_parser = TextHTML()
        title_parser.feed(item.get("title") or "")
        title_parser.close()
        title = " ".join("".join(title_parser.parts).split()) or urlsplit(url).hostname
        try:
            _,body,url=webpage(url)
            check()
            lines=body.splitlines()
            sections={i:dict(title=title,text="\n".join(lines[max(0,i-1):i+4])) for i in range(0,len(lines),3)}
            picked=relevant(sections,query+" "+snippet,5)
            content="\n\n".join(sections[i]["text"] for i in sorted(picked))
            if content:return dict(title=title,text=content,url=url,web_page=True)
        except InterruptedError:raise
        except (OSError,ValueError) as err:failed("webpage",err,url)
        if snippet:return dict(title=title+" · 检索摘录",text=snippet,url=url,web_page=False)
        return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        opened=[r for r in pool.map(open_result,candidates[:5]) if r]
    check()
    opened.sort(key=lambda r:not r["web_page"])
    return {"W"+str(i+1):item for i,item in enumerate(opened[:3])}


def quoted_sources(references, catalog):
    def normalized(text):
        return "".join(text.split()).casefold()
    accepted={}
    for ref in references:
        if not isinstance(ref,dict):
            continue
        key=str(ref.get("id","")).strip("[]")
        quote=ref.get("quote")
        if key in catalog and isinstance(quote,str) and quote.strip() and normalized(quote) in normalized(catalog[key]["text"]):
            source=catalog[key]
            accepted[key]=source
            if source.get("web_page") and "#" not in source["url"]:
                text=" ".join(quote.split())
                accepted[key]={**source,"url":source["url"]+"#:~:text="+urlquote(text,safe="")}
    return accepted


def answer_sources(answer, references, catalog):
    """Resolve cited source identities; exact quotations additionally locate a passage."""
    keys = list(dict.fromkeys(re.findall(r"\[((?:D\d+p\d+|K\d+|W\d+))\]", answer)))
    anchored = quoted_sources(references, catalog)
    sources = {key: anchored.get(key, catalog[key]) for key in keys if key in catalog}
    resolution = {
        "cited": keys,
        "resolved": list(sources),
        "exact_quote": [key for key in keys if key in anchored],
        "unavailable": [key for key in keys if key not in catalog],
    }
    return sources, resolution


def matched_passage(text, quote):
    """Return the actual text-layer passage, retaining PDF line breaks for an anchor."""
    if not isinstance(quote,str) or not quote.strip():return ""
    if quote in text:return quote
    letters=[re.escape(char) for char in quote if not char.isspace()]
    match=re.search(r"\s*".join(letters),text)
    return match.group(0) if match else ""


def reported_web_values(items,catalog):
    """Show only the values a source explicitly pairs with its named subject."""
    lines=[]
    for item in items or []:
        if not isinstance(item,dict):continue
        key=str(item.get("id","")).strip("[]");record=catalog.get(key)
        if not record or not key.startswith("W"):continue
        subject=item.get("subject");value=item.get("value");quote=item.get("quote")
        if not all(isinstance(v,str) and v.strip() for v in (subject,value,quote)):continue
        original=matched_passage(record["text"],quote)
        if not original:continue
        normalized=lambda s:" ".join(s.casefold().split())
        # Each value and its species/material must occupy the same table row or clause.
        clauses=re.split(r"[;；。\n]",original)
        if not any(normalized(subject) in normalized(line) and re.search(r"(?<![\d.])"+re.escape(value)+r"(?![\d.])",line) for line in clauses):continue
        unit=str(item.get("unit","")).strip()
        if unit and normalized(unit) not in normalized(record["text"]):
            unit=""
        text=f"{subject}：{value}"+(" "+unit if unit else "")+f" [{key}]"
        if text not in lines:lines.append(text)
    return lines


def cited_answer(answer,catalog):
    """Only trusted host URLs can become links in this evidence view."""
    used=[]
    answer=re.sub(r"\[([^\]\n]+)\]\((?:https?://|physlab:)[^)\n]+\)",r"\1",answer)
    answer=re.sub(r"(?<!\()\bhttps?://[^\s<>\u3000-\u303f]+","",answer)
    def reference(match):
        key=match.group(1)
        if key not in catalog:
            return ""
        if key not in used:
            used.append(key)
        n=used.index(key)+1
        return f"[[{n}]]({catalog[key]['url']})"
    body=re.sub(r"\[((?:D\d+p\d+|K\d+|W\d+))\]",reference,answer)
    if used:
        footnotes=[]
        for n,key in enumerate(used,1):
            item=catalog[key]
            link=f"[{n} · {item['title'].replace(']','）')}]({item['url']})"
            for page in item.get("originals",[]):
                link+=f" [{page['title'].replace(']','）')}]({page['url']})"
            footnotes.append(link)
        body+="\n\n"+"　".join(footnotes)
    return body,used
