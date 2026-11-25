"""Persistent discovery cards, immutable-page lookup, and original evidence for selected revisions."""
import json
import math
import re
import unicodedata

from .storage import canonical, digest

KINDS = ("SciencePoint", "MathFoundation", "Formula", "MathUse", "KnowledgeRelation")
KIND_SQL = "('SciencePoint','MathFoundation','Formula','MathUse','KnowledgeRelation')"

SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_cards (
    id INTEGER PRIMARY KEY, object_id TEXT NOT NULL, revision INTEGER NOT NULL,
    title TEXT NOT NULL, summary TEXT NOT NULL, keywords TEXT NOT NULL, signature TEXT NOT NULL,
    UNIQUE(object_id, revision), FOREIGN KEY(object_id,revision) REFERENCES revisions(object_id,revision));
CREATE INDEX IF NOT EXISTS kb_signature ON kb_cards(signature);
CREATE VIRTUAL TABLE IF NOT EXISTS kb_card_search USING fts5(terms,content='');
CREATE VIRTUAL TABLE IF NOT EXISTS kb_card_vocab USING fts5vocab(kb_card_search,'row');
CREATE TABLE IF NOT EXISTS kb_page_meta (
    id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), page INTEGER NOT NULL,
    heading TEXT NOT NULL, chapter TEXT NOT NULL, UNIQUE(source_id,page));
CREATE VIRTUAL TABLE IF NOT EXISTS kb_page_search USING fts5(terms,content='');
CREATE TABLE IF NOT EXISTS kb_book (
    source_id TEXT PRIMARY KEY REFERENCES sources(id), first_page INTEGER, last_page INTEGER);
CREATE TABLE IF NOT EXISTS kb_source_roles (
    source_id TEXT PRIMARY KEY REFERENCES sources(id), role TEXT NOT NULL CHECK(role IN ('reference','handout')));
CREATE TABLE IF NOT EXISTS kb_done (
    source_id TEXT NOT NULL REFERENCES sources(id), page INTEGER NOT NULL, topic TEXT NOT NULL,
    job_id TEXT NOT NULL, PRIMARY KEY(source_id,page,topic));
CREATE TABLE IF NOT EXISTS kb_notes (
    source_id TEXT NOT NULL, identity TEXT NOT NULL, object_id TEXT NOT NULL REFERENCES objects(id),
    PRIMARY KEY(source_id,identity));
CREATE TABLE IF NOT EXISTS kb_topics (
    source_id TEXT NOT NULL REFERENCES sources(id), category_id TEXT NOT NULL REFERENCES categories(id),
    summary TEXT NOT NULL, PRIMARY KEY(source_id,category_id));
CREATE VIRTUAL TABLE IF NOT EXISTS kb_topic_search USING fts5(source_id UNINDEXED,category_id UNINDEXED,terms);
CREATE TABLE IF NOT EXISTS kb_topic_members (
    source_id TEXT NOT NULL, object_id TEXT NOT NULL, revision INTEGER NOT NULL, category_id TEXT NOT NULL,
    leader TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('core','detail','overlap')),
    PRIMARY KEY(source_id,object_id,revision), FOREIGN KEY(source_id,category_id) REFERENCES kb_topics(source_id,category_id),
    FOREIGN KEY(object_id,revision) REFERENCES revisions(object_id,revision));
CREATE TABLE IF NOT EXISTS kb_branch_reviews (
    source_id TEXT NOT NULL, branch_id TEXT NOT NULL, PRIMARY KEY(source_id,branch_id));
CREATE TABLE IF NOT EXISTS kb_overlap_checks (
    source_id TEXT NOT NULL, object_id TEXT NOT NULL, revision INTEGER NOT NULL, leader TEXT, reason TEXT NOT NULL,
    PRIMARY KEY(source_id,object_id,revision));
CREATE INDEX IF NOT EXISTS kb_topic_node ON kb_topic_members(category_id);
CREATE TABLE IF NOT EXISTS kb_batches (
    fingerprint TEXT PRIMARY KEY, job_id TEXT NOT NULL, response TEXT NOT NULL);
"""

# Navigation words cannot provide evidence that a scientific topic matches.
NOISE = re.compile(r"本地知识库|知识库|本地知识|已存知识|知识条目|知识|请问|请解释|根据|只用|为什么|是什么|怎么|怎样|如何|请给|能不能|会不会|是不是|有什么|这份|这本|本次|实验中|实验|讲义|教材|出处|解释|原理是什么|告诉我|谢谢|的时候|一下")
STOP = set("the and of for to from by in is are with on this that what why how does using use a an or at as be it".split())


def words(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    result = []
    for group in re.findall(r"[a-zα-ω][a-zα-ω0-9]*|[\u3400-\u9fff]+", text):
        if group.isascii() or not "\u3400" <= group[0] <= "\u9fff":
            if group not in STOP:
                result.append(group)
        else:
            for part in NOISE.split(group):
                if len(part) == 1:
                    result.append(part)
                else:
                    result.extend(part[i:i+2] for i in range(len(part)-1))
    return result


def index_terms(text):
    return " ".join(dict.fromkeys(words(text)))


def expression(terms):
    return " OR ".join('"' + t + '"' for t in sorted(set(terms)))


def signature(content):
    # Same identity fields as the physics.knowledge merge contract.
    return digest(canonical({k:v for k,v in content.items() if k not in
        ("sources","claims","category_ids","conflicts","search_summary","search_terms")}))


def write_card(conn, identity, rev, content):
    summary = content.get("search_summary", "").strip()
    keys = content.get("search_terms", [])
    cur = conn.execute("INSERT OR IGNORE INTO kb_cards(object_id,revision,title,summary,keywords,signature) VALUES(?,?,?,?,?,?)",
                       (identity,rev,content["title"],summary,canonical(keys),signature(content)))
    if cur.rowcount:
        terms = index_terms(" ".join([content["title"],summary,*keys]))
        conn.execute("INSERT INTO kb_card_search(rowid,terms) VALUES(?,?)", (cur.lastrowid,terms))
        previous=conn.execute("SELECT signature FROM kb_cards WHERE object_id=? AND revision=?",(identity,rev-1)).fetchone()
        if previous and previous["signature"]==signature(content):
            previous_body=conn.execute("SELECT content FROM revisions WHERE object_id=? AND revision=?",(identity,rev-1)).fetchone()
            def explanations(body):
                return {(v["text"],v.get("status")) for v in body.get("claims",[]) if v["text"]!=body.get("definition")}
            if previous_body and explanations(json.loads(previous_body["content"]))!=explanations(content):
                return
            conn.execute("""INSERT OR IGNORE INTO kb_topic_members SELECT source_id,object_id,?,category_id,leader,role
                FROM kb_topic_members WHERE object_id=? AND revision=?""",(rev,identity,rev-1))
            conn.execute("""INSERT OR IGNORE INTO kb_overlap_checks(source_id,object_id,revision,leader,reason,leader_revision)
                SELECT source_id,object_id,?,leader,reason,leader_revision FROM kb_overlap_checks WHERE object_id=? AND revision=?""",(rev,identity,rev-1))


def index_topics(conn,rebuild=False):
    if rebuild:conn.execute("DELETE FROM kb_topic_search")
    missing=conn.execute("""SELECT t.rowid,t.source_id,t.category_id,t.summary FROM kb_topics t
        LEFT JOIN kb_topic_search f ON f.rowid=t.rowid WHERE f.rowid IS NULL""").fetchall()
    if not missing:return
    parents={r["id"]:(r["parent"],r["name"]) for r in conn.execute("SELECT id,name,parent FROM categories")}
    for r in missing:
        names=[];node=r["category_id"]
        while node:
            node,name=parents[node];names.append(name)
        conn.execute("INSERT INTO kb_topic_search(rowid,source_id,category_id,terms) VALUES(?,?,?,?)",
            (r["rowid"],r["source_id"],r["category_id"]," ".join(words(" ".join(names)+" "+r["summary"]))))


def migrate(conn):
    if "leader_revision" not in {r[1] for r in conn.execute("PRAGMA table_info(kb_overlap_checks)")}:
        conn.execute("ALTER TABLE kb_overlap_checks ADD COLUMN leader_revision INTEGER")
        conn.execute("UPDATE kb_overlap_checks SET leader_revision=(SELECT revision FROM objects WHERE id=leader) WHERE leader IS NOT NULL")
    index_topics(conn)
    pending = conn.execute(f"""SELECT r.object_id,r.revision,r.content,o.title FROM revisions r
        JOIN objects o ON o.id=r.object_id LEFT JOIN kb_cards c ON c.object_id=r.object_id AND c.revision=r.revision
        WHERE o.experiment_id IS NULL AND o.kind IN {KIND_SQL} AND c.id IS NULL""")
    for row in pending:
        content = json.loads(row["content"])
        content.setdefault("title",row["title"])
        write_card(conn,row["object_id"],row["revision"],content)


def with_locations(store, cards):
    if not cards:
        return []
    pairs = canonical([[c["id"],c["revision"]] for c in cards])
    rows = store.all("""SELECT DISTINCT r.object_id, r.revision, s.id AS source_id, s.name AS file, a.page
        FROM json_each(?) wanted
        JOIN revisions r ON r.object_id=json_extract(wanted.value,'$[0]') AND r.revision=json_extract(wanted.value,'$[1]')
        JOIN json_each(r.sources) ref JOIN anchors a ON a.id=ref.value
        JOIN sources s ON s.id=a.source_id AND a.source_sha=s.sha
        LEFT JOIN kb_book b ON b.source_id=s.id
        WHERE b.first_page IS NULL OR a.page BETWEEN b.first_page AND b.last_page
        ORDER BY r.object_id,s.name,a.page""",(pairs,))
    locations = {}
    for row in rows:
        locations.setdefault((row["object_id"], row["revision"]),[]).append({"file":row["file"],"page":row["page"],"source_id":row["source_id"]})
    return [dict(card,locations=locations.get((card["id"],card["revision"]),[])) for card in cards]


def search(store, query="", *, pins=(), kind=None, category=None, limit=None, source_id=None):
    params = [canonical({p["id"]:p["revision"] for p in pins if isinstance(p,dict) and "id" in p and "revision" in p})]
    cond = []
    if kind:
        cond.append("o.kind=?"); params.append(kind)
    if source_id:
        cond.append("""EXISTS (SELECT 1 FROM revisions sr JOIN json_each(sr.sources) ref JOIN anchors a ON a.id=ref.value
            WHERE sr.object_id=c.object_id AND sr.revision=c.revision AND a.source_id=?)""")
        params.append(source_id)
    if category:
        cond.append("""EXISTS (WITH RECURSIVE children(id) AS (SELECT ? UNION SELECT k.id FROM categories k
            JOIN children p ON k.parent=p.id) SELECT 1 FROM memberships m JOIN children sub ON m.category_id=sub.id
            WHERE m.object_id=o.id UNION ALL SELECT 1 FROM kb_topic_members mt JOIN children ch ON ch.id=mt.category_id
            WHERE mt.object_id=c.object_id AND mt.revision=c.revision)""")
        params.append(category)
    tokens = set(words(query))
    if query.strip():
        if not tokens:
            return []
        cond.append("c.id IN (SELECT rowid FROM kb_card_search WHERE kb_card_search MATCH ?)")
        params.append(expression(tokens))
    rows = store.all("""SELECT c.object_id AS id,c.revision,c.title,c.summary,c.keywords,o.kind FROM kb_cards c
        JOIN objects o ON o.id=c.object_id LEFT JOIN json_each(?) pin ON pin.key=c.object_id
        WHERE c.revision=coalesce(pin.value,o.revision)""" +
        "".join(" AND "+v for v in cond)+" ORDER BY c.title,c.object_id",params)
    for row in rows:
        row["keywords"]=json.loads(row["keywords"])
    if not query.strip():
        return with_locations(store, rows[:limit])
    count=store.one("SELECT count(*) AS n FROM kb_cards")["n"]
    freq=store.all("SELECT term,doc FROM kb_card_vocab WHERE term IN (SELECT value FROM json_each(?))",(canonical(sorted(tokens)),))
    weights={r["term"]:math.log1p((count-r["doc"]+0.5)/(r["doc"]+0.5)) for r in freq}
    ranked=[]
    for row in rows:
        title=set(words(row["title"]+" "+" ".join(row["keywords"])))
        matched=tokens & (title | set(words(row["summary"])))
        named=tokens & title
        exact=any(k.casefold() in query.casefold() for k in [row["title"],*row["keywords"]]
                  if len(k.strip())>=3)
        # A lone accidental two-character overlap in a sentence is too weak.
        if not matched or not (exact or tokens==matched or
                (len(matched)>=2 and (len(named)>=2 or len(matched)>=3))):
            continue
        score=sum(weights.get(t,0) * (3 if t in named else 1) for t in matched)/math.sqrt(len(tokens)) + 3*exact
        ranked.append((score,row))
    ranked.sort(key=lambda x:-x[0])
    if limit is not None and ranked:
        ranked=[r for r in ranked if r[0]>=ranked[0][0]*.38]
    return with_locations(store, [r for _,r in ranked[:limit]])


def related_to(store, identities, kind=None):
    kinds = [kind] if kind else ["MathUse","KnowledgeRelation"]
    return store.all("""SELECT o.id,o.kind,o.title,o.revision FROM objects o
        JOIN revisions r ON r.object_id=o.id AND r.revision=o.revision
        WHERE o.kind IN (SELECT value FROM json_each(?)) AND EXISTS (
            SELECT 1 FROM json_tree(r.content) ref WHERE ref.key='id' AND ref.value IN
            (SELECT value FROM json_each(?))) ORDER BY o.title""",(canonical(kinds),canonical(list(identities))))


def within_scope(store, sid, page):
    scope=store.one("SELECT first_page,last_page FROM kb_book WHERE source_id=?",(sid,))
    return not scope or scope["first_page"] is None or scope["first_page"]<=page<=scope["last_page"]


def set_scope(store, sid, first, last, role="reference"):
    n=store.one("SELECT count(*) n FROM pages WHERE source_id=?",(sid,))["n"]
    if not 1<=first<=last<=n:
        raise ValueError(f"有效页段应在文件第 1 至 {n} 页之间")
    if role not in ("reference","handout"):
        raise ValueError("请选择资料用途")
    with store.transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO kb_book VALUES(?,?,?)",(sid,first,last))
        conn.execute("INSERT OR REPLACE INTO kb_source_roles VALUES(?,?)",(sid,role))


def index_source(store, sid):
    """Build once. Page text is immutable; later searches read the postings only."""
    if store.one("SELECT 1 FROM kb_page_meta WHERE source_id=? LIMIT 1",(sid,)):
        return
    with store.transaction() as conn:
        if conn.execute("SELECT 1 FROM kb_page_meta WHERE source_id=? LIMIT 1",(sid,)).fetchone():
            return
        src=conn.execute("SELECT kind,original FROM sources WHERE id=?",(sid,)).fetchone()
        if not src:
            raise ValueError("资料不存在")
        sections={}
        if src["kind"]=="pdf":
            import pymupdf
            with pymupdf.open(store.asset_path(src["original"])) as document:
                trail=[]
                for level,name,page in document.get_toc():
                    if page<1:continue
                    trail=trail[:level-1]+[name.strip()]
                    sections[page]=" / ".join(trail)
        chapter=""
        for row in conn.execute("SELECT number,text FROM pages WHERE source_id=? ORDER BY number",(sid,)):
            n=row["number"];chapter=sections.get(n,chapter)
            head=" / ".join(v.strip() for v in row["text"].splitlines() if v.strip())[:120]
            cur=conn.execute("INSERT INTO kb_page_meta(source_id,page,heading,chapter) VALUES(?,?,?,?)",(sid,n,head,chapter))
            # Repeated occurrences inform FTS ranking.
            tokens=" ".join(words(row["text"]+" "+head+" "+chapter))
            conn.execute("INSERT INTO kb_page_search(rowid,terms) VALUES(?,?)",(cur.lastrowid,tokens))


def find_pages(store,sid,query="",limit=None):
    index_source(store,sid)
    params=[sid]
    sql="""SELECT p.page,p.heading,p.chapter FROM kb_page_meta p LEFT JOIN kb_book b ON b.source_id=p.source_id
           WHERE p.source_id=? AND (b.first_page IS NULL OR p.page BETWEEN b.first_page AND b.last_page)"""
    if query:
        tokens=words(query)
        if not tokens:return []
        sql=sql.replace("FROM kb_page_meta p","FROM kb_page_search JOIN kb_page_meta p ON p.id=kb_page_search.rowid")
        sql+=" AND kb_page_search MATCH ? ORDER BY rank,p.page";params.append(expression(tokens))
    else:
        sql+=" ORDER BY p.page"
    if limit is not None:sql+=" LIMIT ?";params.append(limit)
    return store.all(sql,params)


def verified(store, identity, rev):
    """Only this second stage opens a full knowledge revision and its cited original pages."""
    obj=store.object(identity,rev)
    if obj["kind"] not in KINDS or obj["experiment_id"] is not None:
        raise ValueError("请选择一条全局知识")
    evidence={}
    for anchor_id in obj["content"].get("sources",[]):
        anchor=store.anchor(anchor_id);sid,n=anchor["source_id"],anchor["page"]
        if (sid,n) in evidence or not within_scope(store,sid,n):continue
        row=store.one("""SELECT s.sha,s.name,s.original,s.kind,p.number,p.text,p.image,p.label FROM sources s
            JOIN pages p ON p.source_id=s.id WHERE s.id=? AND p.number=?""",(sid,n))
        if not row or row["sha"]!=anchor["source_sha"]:continue
        try:
            store.asset_path(row["original"])
        except (ValueError, OSError):
            continue
        kind = "PDF" if row["kind"] == "pdf" else "文件"
        evidence[sid,n]=dict(source=sid,number=n,title=f"{row['name']} · {kind} 第 {n} 页",
            text=row["text"],image=row["image"],url=f"physlab://page/{sid}/{n}",printed_label=row["label"])
    from .knowledge_notes import for_revision
    obj["reading_notes"]=for_revision(store,identity,rev)
    return obj,list(evidence.values())


def hydrate(store, cards, *, vision=True):
    result={}
    for i,card in enumerate(cards,1):
        obj,pages=verified(store,card["id"],card["revision"])
        pages=[p for p in pages if p["text"].strip() or (vision and p["image"])]
        if not pages:continue
        content={k:v for k,v in obj["content"].items() if k not in ("category_ids","sources","search_terms","search_summary")}
        result["K"+str(i)]=dict(title=card["title"],object_id=card["id"],revision=card["revision"],text=canonical(content),originals=pages,reading_notes=obj["reading_notes"],
            url=f"physlab://knowledge/{card['id']}/{card['revision']}")
    return result

def save_description(store,identity,rev,summary,terms):
    summary=summary.strip()
    if not summary or not terms or not all(isinstance(x,str) and x.strip() for x in terms):
        raise ValueError("缺少检索说明或关键词")
    with store.transaction() as conn:
        row=conn.execute("SELECT * FROM kb_cards WHERE object_id=? AND revision=?",(identity,rev)).fetchone()
        if not row:raise ValueError("知识修订不存在")
        if row["summary"]:return
        old=index_terms(" ".join([row["title"],row["summary"],*json.loads(row["keywords"])]))
        conn.execute("INSERT INTO kb_card_search(kb_card_search,rowid,terms) VALUES('delete',?,?)",(row["id"],old))
        conn.execute("UPDATE kb_cards SET summary=?,keywords=? WHERE id=?",(summary,canonical(terms),row["id"]))
        conn.execute("INSERT INTO kb_card_search(rowid,terms) VALUES(?,?)",(row["id"],index_terms(" ".join([row["title"],summary,*terms]))))
