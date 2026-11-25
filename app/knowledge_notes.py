"""Page-bound reading notes discovered while answering, separate from textbook content."""
import json
import re
import unicodedata

from .storage import canonical, now


SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_reading_notes (
    source_id TEXT NOT NULL REFERENCES sources(id), source_sha TEXT NOT NULL, page INTEGER NOT NULL,
    scope TEXT NOT NULL, location_key TEXT NOT NULL, kind TEXT NOT NULL, location TEXT NOT NULL,
    finding TEXT NOT NULL, basis TEXT NOT NULL, conclusion TEXT NOT NULL, object_id TEXT NOT NULL,
    revision INTEGER NOT NULL, job_id TEXT, created TEXT NOT NULL,
    PRIMARY KEY(source_id,source_sha,page,scope,location_key,kind),
    FOREIGN KEY(object_id,revision) REFERENCES revisions(object_id,revision));
"""


def location_key(value):
    text=unicodedata.normalize("NFKC",value).casefold()
    text=re.sub(r"\s+","",text)
    match=re.fullmatch(r"(?:公式|式|equation|eq\.?)?\(?([0-9]+(?:\.[0-9]+)+[a-z]?)\)?",text)
    return match[1] if match else text


def for_revision(store, identity, revision):
    return store.all("""SELECT DISTINCT n.kind,n.location,n.finding,n.basis,n.conclusion,n.page,n.source_id,s.name
        FROM revisions r JOIN json_each(r.sources) ref JOIN anchors a ON a.id=ref.value
        JOIN sources s ON s.id=a.source_id AND s.sha=a.source_sha
        JOIN kb_reading_notes n ON n.source_id=a.source_id AND n.source_sha=a.source_sha AND n.page=a.page
        LEFT JOIN kb_book b ON b.source_id=s.id
        WHERE r.object_id=? AND r.revision=? AND n.object_id=r.object_id AND (n.kind='source' OR n.revision=r.revision)
        AND (b.first_page IS NULL OR a.page BETWEEN b.first_page AND b.last_page)
        ORDER BY n.page,n.location,n.kind""",(identity,revision))


def save(store, proposed, cited, job):
    """Bind to a knowledge revision and original page actually supplied and cited in this answer."""
    if not isinstance(proposed,list):return {"saved":0,"rejected":["备注须逐项提交"]}
    saved=0
    rejected=[]
    with store.transaction() as conn:
        for note in proposed:
            if not isinstance(note,dict) or not isinstance(note.get("entry"),str):
                rejected.append("未指定所引用知识")
                continue
            card=cited.get(note["entry"])
            original=next((p for p in (card or {}).get("originals",[]) if p["url"]==note.get("page_url")),None)
            if not original or not card.get("object_id"):
                rejected.append("本次答复未引用相应知识及原页")
                continue
            kind=note.get("kind")
            if kind not in ("source","transcription") or not all(isinstance(note.get(k),str) and note[k].strip() for k in ("location","finding","basis")):
                rejected.append("备注缺少定位、问题或可复核依据")
                continue
            source=original["source"]
            page=original["number"]
            oid,rev=card["object_id"],card["revision"]
            origin=conn.execute("""SELECT s.sha FROM revisions r JOIN json_each(r.sources) j JOIN anchors a ON a.id=j.value
                JOIN sources s ON s.id=a.source_id AND s.sha=a.source_sha WHERE r.object_id=? AND r.revision=? AND s.id=? AND a.page=?""",
                (oid,rev,source,page)).fetchone()
            if not origin:
                rejected.append("该知识版本与原页不符")
                continue
            scope=oid if kind=="source" else canonical([oid,rev])
            location=note["location"].strip()
            conclusion=note.get("conclusion") or ""
            if not isinstance(conclusion,str):
                rejected.append("备注结论须为文字")
                continue
            conclusion=conclusion.strip()
            row=conn.execute("""INSERT INTO kb_reading_notes
                (source_id,source_sha,page,scope,location_key,kind,location,finding,basis,conclusion,object_id,revision,job_id,created)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id,source_sha,page,scope,location_key,kind) DO UPDATE SET
                basis=kb_reading_notes.basis||char(10)||excluded.basis,conclusion=excluded.conclusion
                WHERE kb_reading_notes.conclusion='' AND excluded.conclusion!=''""",
                (source,origin["sha"],page,scope,location_key(location),kind,location,note["finding"].strip(),
                 note["basis"].strip(),conclusion,oid,rev,job,now()))
            saved+=row.rowcount
    return {"saved":saved,"rejected":rejected}


def describe(note):
    kind="原书疑点" if note["kind"]=="source" else "条目转录问题"
    return f"{kind} · {note['location']}\n{note['finding']}\n核对依据：{note['basis']}\n"+(
        "据此采用："+note["conclusion"] if note["conclusion"] else "目前仅确定存在差异。")

def for_worksheet(store, urls):
    if not urls:return ""
    seen=set()
    lines=[]
    for url in urls:
        match=re.fullmatch(r"physlab://knowledge/([^/]+)/(\d+)",url)
        if not match:continue
        for note in for_revision(store,match[1],int(match[2])):
            text=f"{note['name']} · 第{note['page']}页 "+describe(note)
            if text not in seen:
                lines.append(text)
                seen.add(text)
    return "\n已选知识的答疑核对备注，结合当前方法使用：\n"+"\n".join(lines) if lines else ""


def worksheet_references(store, experiment, binding):
    row=store.one("""SELECT k.content FROM worksheet_builds d JOIN worksheet_builds k
        ON k.experiment_id=d.experiment_id AND k.fingerprint=d.fingerprint AND k.step='knowledge_references'
        WHERE d.experiment_id=? AND d.step='draft' AND json_extract(d.content,'$.id')=?
        AND json_extract(d.content,'$.revision')<=? ORDER BY json_extract(d.content,'$.revision') DESC,d.rowid DESC LIMIT 1""",
        (experiment,binding["id"],binding["revision"]))
    return json.loads(row["content"]) if row else []
