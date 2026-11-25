"""Reusable textbook hierarchy. Organize short cards; retain immutable source evidence underneath."""
from collections import Counter
from pathlib import Path
import json
import threading

from . import knowledge_index as kb
from .storage import canonical, digest, now, uid
from .knowledge_workflow import INTEGER, STRING, array, record

UNIT = record(path=array(STRING), summary=STRING, core=INTEGER, detail=array(INTEGER), overlap=array(INTEGER))
OUTLINE = record(domain=STRING, units=array(UNIT))



def category_paths(store):
    rows=store.all("SELECT id,parent,name FROM categories")
    by_id={r["id"]:r for r in rows}
    paths={}
    for row in rows:
        cur=row;path=[]
        while cur:
            path.append(cur["name"])
            cur=by_id.get(cur["parent"])
        paths[row["id"]]=list(reversed(path))
    return paths


def cards_for_book(store,sid):
    return [c for c in kb.search(store,source_id=sid) if c["summary"]]


def find_topics(store,query,limit=5):
    terms=kb.words(query)
    if not terms:return []
    rows=store.all("""SELECT f.category_id AS id,t.summary,k.name AS title FROM kb_topic_search f
        JOIN kb_topics t ON t.rowid=f.rowid JOIN categories k ON k.id=f.category_id
        WHERE kb_topic_search MATCH ? ORDER BY rank LIMIT ?""",(kb.expression(terms),limit))
    paths=category_paths(store) if rows else {}
    significant=set(terms)
    return [{**r,"path":paths[r["id"]]} for r in rows if
        len(significant & set(kb.words(" ".join(paths[r["id"]])+" "+r["summary"])))>=min(2,len(significant))]


def pending(store,sid,cards):
    done={(x["object_id"],x["revision"]) for x in store.all("SELECT object_id,revision FROM kb_topic_members WHERE source_id=?",(sid,))}
    return [c for c in cards if (c["id"],c["revision"]) not in done]


def node(conn,domain,path):
    root=conn.execute("SELECT id,domain FROM categories WHERE parent IS NULL AND (name=? OR id=?) ORDER BY id LIMIT 1",(domain,domain)).fetchone()
    if root:
        parent=root["id"];domain_key=root["domain"]
    else:
        parent=uid("cat");domain_key=domain
        conn.execute("INSERT INTO categories VALUES(?,NULL,?,?)",(parent,domain,domain_key))
    for name in path:
        row=conn.execute("SELECT id FROM categories WHERE parent=? AND name=?",(parent,name)).fetchone()
        if row:
            parent=row["id"]
        else:
            identity=uid("cat")
            conn.execute("INSERT INTO categories(id,parent,name,domain) VALUES(?,?,?,?)",(identity,parent,name,domain_key))
            parent=identity
    return parent


def filing_path(domain,group,title,existing):
    parts=[p.strip() for p in group["path"]]
    if parts[:1]==[domain]:parts=parts[1:]
    if not parts:raise ValueError("主题需要位于领域目录下")
    # A proposed shelf which only repeats its sole card would add an empty navigation step.
    duplicate=(len(parts)>1 and not group["detail"] and not group["overlap"]
               and parts[-1].casefold()==title.strip().casefold()
               and (domain,*parts) not in existing)
    return (parts[:-1],parts[-2]) if duplicate else (parts,group["summary"])


def classify(workflow,jid,sid,options):
    store=workflow.store
    cards=cards_for_book(store,sid)
    pool=pending(store,sid,cards)
    if not pool:return {"new_members":0}
    chapter={r["page"]:r["chapter"].split(" / ")[0] for r in kb.find_pages(store,sid)}
    def first(card):
        return min(p["page"] for p in card["locations"] if p["source_id"]==sid)
    pool.sort(key=lambda c:(first(c),c["title"]))
    brief=[(first(c),chapter.get(first(c),""),c["title"],c["summary"]) for c in pool]
    # Budget by serialized text, not chapter count: a large atlas can be filed incrementally.
    ranges=[];batch=[];characters=0
    for i,row in enumerate(brief):
        size=len(canonical(row))
        if batch and characters+size>43000:
            ranges.append(batch);batch=[];characters=0
        batch.append(i);characters+=size
    if batch:ranges.append(batch)
    guide=(Path(workflow.app.registry.owner("SciencePoint")["directory"])/"topic_catalog.md").read_text("utf8")+"\n从教材名称初判学科，根据章节及内容确认实际归属，优先从当前入口选根并在 path 中沿用合适的学科分支。目录的顶层可扩展。优先复用适用的现有顶层；domain 为目录名称，知识条目的物理标签不约束目录结构。"
    book=store.one("SELECT name FROM sources WHERE id=?",(sid,))["name"]
    total_saved=0
    while ranges:
        workflow.check(jid)
        indices=ranges.pop(0)
        valid=set(indices)
        paths=category_paths(store)
        used=store.all("SELECT category_id,summary FROM kb_topics WHERE source_id=?",(sid,))
        existing=[[" / ".join(paths[r["category_id"]]),r["summary"]] for r in used]
        material={"教材文件名":book,"当前入口":[p for p in paths.values() if len(p)<=2],"已有主题":existing,"待归类":[[i,*brief[i]] for i in indices]}
        key=digest(canonical(["outline-v2",book,material["当前入口"],sid,[(pool[i]["id"],pool[i]["revision"]) for i in indices],existing]))
        row=store.one("SELECT response FROM kb_batches WHERE fingerprint=?",(key,))
        result=json.loads(row["response"]) if row else workflow.ask(jid,"organize_knowledge_topics",OUTLINE,guide,canonical(material),{**options,"thinking_depth":"low","native_vision":False})
        domain=result["domain"]
        if not isinstance(domain,str) or not domain.strip():raise ValueError("缺少顶层目录名称")
        domain=domain.strip()
        units=result["units"]
        if not isinstance(units,list):raise ValueError("缺少主题目录")
        occurrences=Counter()
        for group in units:
            if not group.get("path") or not all(isinstance(n,str) and n.strip() for n in group["path"]) or not group.get("summary"):
                raise ValueError("返回的主题缺少名称或说明")
            for v in [group["core"],*group["detail"],*group["overlap"]]:
                if type(v) is not int or v not in valid:raise ValueError("目录列出了未提供的知识编号")
                occurrences[v]+=1
        existing_paths={tuple(path) for path in paths.values()}
        saved=set()
        with store.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO kb_batches VALUES(?,?,?)",(key,jid,canonical(result)))
            for group in units:
                main=group["core"]
                if occurrences[main]!=1:continue
                parts,summary=filing_path(domain,group,pool[main]["title"],existing_paths)
                category=node(conn,domain,parts)
                conn.execute("INSERT OR IGNORE INTO kb_topics VALUES(?,?,?)",(sid,category,summary))
                existing_core=conn.execute("SELECT 1 FROM kb_topic_members WHERE source_id=? AND category_id=? AND role='core' LIMIT 1",(sid,category)).fetchone()
                for role,numbers in [("detail" if existing_core else "core",[main]),("detail",group["detail"]),("overlap",group["overlap"])]:
                    for n in numbers:
                        if occurrences[n]!=1:continue
                        c=pool[n]
                        conn.execute("INSERT OR IGNORE INTO kb_topic_members VALUES(?,?,?,?,?,?)",
                            (sid,c["id"],c["revision"],category,pool[main]["id"],role))
                        conn.execute("INSERT OR IGNORE INTO memberships VALUES(?,?)",(c["id"],category))
                        saved.add(n)
            kb.index_topics(conn)
        total_saved+=len(saved)
        missing=[n for n in indices if n not in saved]
        if missing:
            if not saved and len(missing)==1:raise ValueError("一条知识仍未归类，可从该条接续")
            if saved:ranges.insert(0,missing)
            else:
                mid=len(missing)//2;ranges[:0]=[missing[:mid],missing[mid:]]
    return {"new_members":total_saved}


def unchecked(store,sid):
    return store.all("""SELECT m.object_id,m.revision,m.category_id FROM kb_topic_members m LEFT JOIN kb_overlap_checks v
        ON v.source_id=m.source_id AND v.object_id=m.object_id AND v.revision=m.revision
        JOIN objects o ON o.id=m.object_id AND o.revision=m.revision LEFT JOIN objects dest ON dest.id=v.leader
        WHERE m.source_id=? AND m.role='overlap' AND (v.object_id IS NULL OR v.leader_revision!=dest.revision)""",(sid,))


def verify_overlaps(workflow,jid,sid,options):
    store=workflow.store
    suspects=unchecked(store,sid)
    if not suspects:return 0
    schema=record(verdicts=array(record(id=STRING,same_as=STRING,reason=STRING)))
    nodes={r["category_id"] for r in suspects}
    pool=store.all("""SELECT m.object_id,m.revision,m.category_id,m.role FROM kb_topic_members m
        JOIN objects o ON o.id=m.object_id AND o.revision=m.revision WHERE m.source_id=? AND m.category_id IN (SELECT value FROM json_each(?))
        ORDER BY m.category_id,m.role,m.object_id""",(sid,canonical(sorted(nodes))))
    by_id={r["object_id"]:r for r in pool}
    data=[]
    for r in pool:
        c=store.object(r["object_id"],r["revision"])["content"]
        data.append({"id":r["object_id"],"unit":r["category_id"],"title":c["title"],"check":r["role"]=="overlap",
            "knowledge":c.get("definition",c.get("latex",c.get("method",""))),"conditions":c.get("conditions",[]),
            "explanations":[v["text"] for v in c.get("claims",[]) if v["text"]!=c.get("definition")]})
    prompt=("核对需检查的知识项能否与同单元的另一项合并显示。逐一比较物理命题、适用条件和独有内容。"
        "只有核心结论及成立条件实质等价、读者不会丢失独立结论时才填 same_as，直接找真正等价的条目，可以是核心或补充。"
        "从属、类比、特殊情况、不同物理量不能视作等价。没有相应条目填空字符串。每个 check 项给一行及简短理由。不改写教材知识。")
    result=workflow.ask(jid,"verify_knowledge_overlap",schema,prompt,canonical(data),{**options,"thinking_depth":"low","native_vision":False})
    wanted={r["object_id"] for r in suspects}
    votes=[r for r in result["verdicts"] if r["id"] in wanted]
    if Counter(r["id"] for r in votes)!=Counter(r["object_id"] for r in suspects):
        raise ValueError("相近条目核对没有逐项对应，保留原材料供接续")
    workflow.check(jid)
    with store.transaction() as conn:
        for r in votes:
            src=by_id[r["id"]];target=r["same_as"]
            if target and (target not in by_id or by_id[target]["role"]=="overlap" or by_id[target]["category_id"]!=src["category_id"]):
                raise ValueError("等价依据必须指向同主题下的独立知识材料")
            conn.execute("INSERT OR REPLACE INTO kb_overlap_checks(source_id,object_id,revision,leader,reason,leader_revision) VALUES(?,?,?,?,?,?)",
                (sid,r["id"],src["revision"],target or None,r["reason"],by_id[target]["revision"] if target else None))
            conn.execute("UPDATE kb_topic_members SET role=?,leader=? WHERE source_id=? AND object_id=? AND revision=?",
                ("overlap" if target else "detail",target or r["id"],sid,r["id"],src["revision"]))
    return len(votes)


def branches(store,sid):
    return store.all("""WITH RECURSIVE ancestry(node,child) AS (
        SELECT category_id,category_id FROM kb_topics WHERE source_id=?
        UNION SELECT c.parent,a.child FROM categories c JOIN ancestry a ON c.id=a.node WHERE c.parent IS NOT NULL)
        SELECT c.id,c.name,root.name AS domain,group_concat(leaf.name,'、') AS units FROM ancestry a
        JOIN categories c ON c.id=a.node JOIN categories leaf ON leaf.id=a.child
        JOIN categories root ON root.id=c.parent WHERE root.parent IS NULL
        AND c.id NOT IN (SELECT j.value FROM meta m,json_each(m.value) j WHERE m.key='knowledge_directory_seed_v1')
        AND NOT EXISTS (SELECT 1 FROM kb_branch_reviews b WHERE b.source_id=? AND b.branch_id=c.id)
        GROUP BY c.id ORDER BY c.name""",(sid,sid))


def shape_branches(workflow,jid,sid,opts):
    store=workflow.store
    choices=branches(store,sid)
    # An already short menu is cheaper and easier to navigate without another layer.
    if len(choices)<=8:return 0
    schema=record(routes=array(record(branch=INTEGER,parents=array(STRING))))
    request=[[i,r["name"],r["units"]] for i,r in enumerate(choices)]
    message=("把同一领域下现有的教材分支按学科的教学层次归拢。返回每个编号应置于哪些上级目录之下，parents 从宽到窄，"
        "不要包含领域名或编号行中已有分支的名称。可以让多个分支共享上级，确实已足够概括的分支可留在根部。"
        "优先让学生先分清主要理论或工具，再找分问题和具体单元。不要人为凑层数。尽量少用仅有一个孩子的导航目录。逐项列出编号。")
    routes=workflow.ask(jid,"shape_knowledge_directory",schema,message,canonical(request),{**opts,"thinking_depth":"low","native_vision":False})["routes"]
    if Counter(x["branch"] for x in routes)!=Counter(range(len(choices))):raise ValueError("上级目录方案没有覆盖全部待整理分支")
    workflow.check(jid)
    with store.transaction() as conn:
        # Direct branches must not be used as each other's provisional containers.
        top_names={r["name"] for r in choices}
        for route in routes:
            r=choices[route["branch"]]
            steps=route["parents"]
            if not all(isinstance(x,str) and x.strip() for x in steps):raise ValueError("目录名称无效")
            steps=list(dict.fromkeys(x.strip() for x in steps if x.strip() not in (r["name"],r["domain"])))
            if steps and steps[0] in top_names:
                # A pre-existing branch can host siblings only if it stays a root.
                other=next((v for v in routes if choices[v["branch"]]["name"]==steps[0]),None)
                if other and other["parents"]:steps=[]
            if steps:
                outside=conn.execute("""WITH RECURSIVE sub(id) AS (SELECT ? UNION SELECT k.id FROM categories k JOIN sub ON k.parent=sub.id)
                    SELECT 1 FROM kb_topics t JOIN sub ON sub.id=t.category_id WHERE t.source_id!=?
                    UNION ALL SELECT 1 FROM memberships m JOIN sub ON sub.id=m.category_id WHERE NOT EXISTS
                    (SELECT 1 FROM kb_topic_members mt WHERE mt.source_id=? AND mt.object_id=m.object_id) LIMIT 1""",(r["id"],sid,sid)).fetchone()
                if not outside:
                    parent=node(conn,r["domain"],steps)
                    conflict=conn.execute("SELECT 1 FROM categories WHERE parent=? AND name=? AND id!=?",(parent,r["name"],r["id"])).fetchone()
                    if not conflict:
                        conn.execute("UPDATE categories SET parent=? WHERE id=?",(parent,r["id"]))
                    conn.execute("INSERT OR IGNORE INTO kb_branch_reviews VALUES(?,?)",(sid,node(conn,r["domain"],steps[:1])))
            conn.execute("INSERT OR IGNORE INTO kb_branch_reviews VALUES(?,?)",(sid,r["id"]))
        kb.index_topics(conn,rebuild=True)
    return len(routes)


def organize(workflow,jid,sid,options):
    receipt=classify(workflow,jid,sid,options)
    receipt["checked"]=verify_overlaps(workflow,jid,sid,options)
    receipt["branches"]=shape_branches(workflow,jid,sid,options)
    return receipt


def annotated(store,cards):
    if not cards:return []
    keys=canonical([[c["id"],c["revision"]] for c in cards])
    rows=store.all("""SELECT m.object_id,m.revision,m.category_id,m.leader,CASE WHEN m.role='overlap' AND (v.leader IS NULL OR dest.revision!=v.leader_revision) THEN 'detail' ELSE m.role END AS role,t.summary FROM json_each(?) wanted
        JOIN kb_topic_members m ON m.object_id=json_extract(wanted.value,'$[0]') AND m.revision=json_extract(wanted.value,'$[1]')
        JOIN kb_topics t ON t.source_id=m.source_id AND t.category_id=m.category_id LEFT JOIN kb_overlap_checks v
        ON v.source_id=m.source_id AND v.object_id=m.object_id AND v.revision=m.revision LEFT JOIN objects dest ON dest.id=v.leader ORDER BY t.source_id""",(keys,))
    by_key={}
    paths=category_paths(store) if rows else {}
    for r in rows:
        by_key.setdefault((r["object_id"],r["revision"]),{**r,"path":paths[r["category_id"]]})
    return [dict(c,topic=by_key.get((c["id"],c["revision"]))) for c in cards]


def distinct_evidence(store,cards,pins=()):
    tagged=cards if all("topic" in c for c in cards) else annotated(store,cards)
    pinned={(p["id"],p["revision"]) for p in pins}
    seen=set();result=[]
    for card in tagged:
        topic=card.get("topic")
        key=(topic["category_id"],topic["leader"] if topic["role"]=="overlap" else card["id"]) if topic else (card["id"],card["revision"])
        if key in seen and (card["id"],card["revision"]) not in pinned:continue
        seen.add(key);result.append(card)
    return result


def browse(store,cards,category,query):
    tagged=annotated(store,cards)
    if query:return tagged
    paths=category_paths(store)
    by_names={tuple(path):identity for identity,path in paths.items()}
    parent=paths.get(category,[])
    result=[];groups={}
    for c in tagged:
        t=c["topic"]
        if not t or category==t["category_id"]:
            result.append(c);continue
        path=t["path"]
        if path[:len(parent)]!=parent:
            result.append(c);continue
        branch=path[:len(parent)+1]
        next_id=by_names[tuple(branch)]
        group=groups.get(next_id)
        if group is None:
            unit=next_id==t["category_id"]
            group=dict(id="folder/"+next_id,title=branch[-1],summary=t["summary"] if unit else "",kind="",
                       revision="",locations=[],count=0,topics=set(),topic=None)
            groups[next_id]=group
        group["count"]+=1;group["topics"].add(t["category_id"])
    for group in groups.values():
        n=len(group["topics"])
        group["kind"]="主题" if n==1 else "目录"
        if not group["summary"]:group["summary"]=f"{n} 个学习单元，共 {group['count']} 份知识材料"
    priority={"core":0,"detail":1,"overlap":2}
    return sorted(groups.values(),key=lambda g:g["title"])+sorted(result,key=lambda c:(priority.get((c["topic"] or {}).get("role"),0),c["title"]))


def queue(workflow,sid):
    store=workflow.store
    if not pending(store,sid,cards_for_book(store,sid)) and not unchecked(store,sid) and len(branches(store,sid))<=8:return {"already_processed":True}
    with store.transaction() as conn:
        previous=conn.execute("SELECT id FROM jobs WHERE kind='knowledge_catalog' AND json_extract(payload,'$.source_id')=? AND status IN ('queued','running')",(sid,)).fetchone()
        if previous:return dict(previous)
        retry=conn.execute("SELECT id FROM jobs WHERE kind='knowledge_catalog' AND json_extract(payload,'$.source_id')=? AND status IN ('failed','interrupted','cancelled') ORDER BY created DESC LIMIT 1",(sid,)).fetchone()
        if retry:
            jid=retry["id"]
            conn.execute("UPDATE jobs SET status='queued',cancel=0,error=NULL,updated=? WHERE id=?",(now(),jid))
        else:
            jid=uid("catalog")
            conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,0,?,?)",(jid,"knowledge_catalog","queued",0,canonical({"source_id":sid}),None,None,now(),now()))
    def run():
        try:
            with workflow.lock:
                store.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(now(),jid))
                receipt=organize(workflow,jid,sid,workflow.app.settings.read())
                store.execute("UPDATE jobs SET status='succeeded',progress=100,result=?,updated=? WHERE id=?",(canonical(receipt),now(),jid))
        except Exception as err:
            store.execute("UPDATE jobs SET status=?,error=?,updated=? WHERE id=?",("cancelled" if isinstance(err,InterruptedError) else "failed",workflow.app.settings.redact(str(err)),now(),jid))
    threading.Thread(target=run,daemon=True).start()
    return {"id":jid}
