"""资料、知识条目与实验的归档和删除。

归档只改可见性（写入归档时间戳），行、原件与产物都留在原处，随时可恢复。
删除是真删：连同关联行、原件目录与生成产物一起清理，界面上必须二次确认。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .storage import now

TABLES = {"source": "sources", "object": "objects", "experiment": "experiments"}
LABELS = {"source": "资料", "object": "知识条目", "experiment": "实验"}
FTS = ("kb_card_search", "kb_page_search")


def _row(store, kind, identity):
    table = TABLES.get(kind)
    if not table:
        raise ValueError("不支持的对象类型")
    row = store.one(f"SELECT * FROM {table} WHERE id=?", (identity,))
    if not row:
        raise ValueError(LABELS[kind] + "不存在，可能已被删除")
    return row


def title(kind, row):
    return row["name"] if kind == "source" else row["title"]


def describe(store, kind, identity):
    """Describe what archiving or removing this entry touches."""
    row = _row(store, kind, identity)
    info = {"kind": kind, "id": identity, "title": title(kind, row), "archived": bool(row["archived"])}
    if kind == "source":
        info["pages"] = store.one("SELECT COUNT(*) AS n FROM pages WHERE source_id=?", (identity,))["n"]
        info["knowledge"] = store.one("SELECT COUNT(DISTINCT object_id) AS n FROM kb_notes WHERE source_id=?", (identity,))["n"]
        info["books"] = store.one("SELECT COUNT(*) AS n FROM kb_page_meta WHERE source_id=?", (identity,))["n"]
        info["experiments"] = [r["title"] for r in store.all(
            "SELECT title FROM experiments WHERE EXISTS(SELECT 1 FROM json_each(source_ids) WHERE value=?) ORDER BY updated DESC", (identity,))]
        info["datasets"] = store.one("SELECT COUNT(*) AS n FROM datasets WHERE source_id=?", (identity,))["n"]
    elif kind == "object":
        info["revisions"] = store.one("SELECT COUNT(*) AS n FROM revisions WHERE object_id=?", (identity,))["n"]
        info["categories"] = store.one("SELECT COUNT(*) AS n FROM memberships WHERE object_id=?", (identity,))["n"]
        info["products"] = store.one("SELECT COUNT(*) AS n FROM publications WHERE object_id=?", (identity,))["n"]
    else:
        info["objects"] = store.one("SELECT COUNT(*) AS n FROM objects WHERE experiment_id=?", (identity,))["n"]
        info["messages"] = store.one("SELECT COUNT(*) AS n FROM messages WHERE experiment_id=?", (identity,))["n"]
        info["sources"] = store.one("SELECT COUNT(*) AS n FROM json_each((SELECT source_ids FROM experiments WHERE id=?))", (identity,))["n"]
    return info


def set_archived(store, kind, identity, value=True):
    row = _row(store, kind, identity)
    table = TABLES[kind]
    store.execute(f"UPDATE {table} SET archived=? WHERE id=?", (now() if value else "", identity))
    store.audit("archive" if value else "restore", {"kind": kind, "id": identity, "title": title(kind, row)})
    return {"id": identity, "archived": bool(value), "title": title(kind, row)}


def _rows(conn, sql, args=()):
    return conn.execute(sql, args).fetchall()


def _delete_by(conn, tables, column, value, *, skip=()):
    """Delete every row that points at this identity, in any table that has the column."""
    sql = "DELETE FROM {table} WHERE {column}=?"
    for table in sorted(tables):
        if table in skip or table.startswith("sqlite_"):
            continue
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        if column in columns:
            conn.execute(sql.format(table=f'"{table}"', column=f'"{column}"'), (value,))


def _drop_cards(conn, tables, object_ids):
    if "kb_cards" not in tables or not object_ids:
        return
    ids = [r["id"] for r in _rows(conn, "SELECT id FROM kb_cards WHERE object_id IN (" + ",".join("?" for _ in object_ids) + ")", object_ids)]
    if ids and "kb_card_search" in tables:
        for identity in ids:
            conn.execute("DELETE FROM kb_card_search WHERE rowid=?", (identity,))
    conn.execute("DELETE FROM kb_cards WHERE object_id IN (" + ",".join("?" for _ in object_ids) + ")", object_ids)


def _drop_object_rows(conn, tables, object_ids):
    _drop_cards(conn, tables, object_ids)
    for identity in object_ids:
        if "revisions" in tables:
            conn.execute("DELETE FROM revisions WHERE object_id=?", (identity,))
        for table, column in (("memberships", "object_id"), ("worksheet_readings", "object_id"),
                              ("kb_notes", "object_id"), ("kb_topic_members", "object_id"),
                              ("kb_overlap_checks", "object_id")):
            if table in tables:
                columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
                if column in columns:
                    conn.execute(f'DELETE FROM "{table}" WHERE "{column}"=?', (identity,))
        if "objects" in tables:
            conn.execute("DELETE FROM objects WHERE id=?", (identity,))


def _product_dirs(store, rows):
    paths = []
    for row in rows:
        if not row["folder"]:
            continue
        path = (Path(row["root"]) / row["folder"]).resolve()
        root = Path(row["root"]).resolve()
        if path.parent == root and path.is_dir():
            paths.append(path)
    return paths


def _source_dir(store, original):
    path = (store.authority / original).resolve()
    if path.is_relative_to(store.originals.resolve()) and path.parent.is_dir():
        return path.parent
    return None


def remove(store, kind, identity):
    """Permanently remove one entry with everything that only belongs to it."""
    row = _row(store, kind, identity)
    label = title(kind, row)
    folders = []
    with store.transaction() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if kind == "source":
            if "pages" in tables:
                for entry in _rows(conn, "SELECT id FROM kb_page_meta WHERE source_id=?", (identity,)):
                    if "kb_page_search" in tables:
                        conn.execute("DELETE FROM kb_page_search WHERE rowid=?", (entry["id"],))
            _delete_by(conn, tables, "source_id", identity, skip=FTS)
            if "experiments" in tables:
                for exp in _rows(conn, "SELECT id,source_ids FROM experiments WHERE EXISTS(SELECT 1 FROM json_each(source_ids) WHERE value=?)", (identity,)):
                    kept = [str(v) for v in json.loads(exp["source_ids"]) if v != identity]
                    conn.execute("UPDATE experiments SET source_ids=?,updated=? WHERE id=?", (json.dumps(kept, ensure_ascii=False), now(), exp["id"]))
            if "sources" in tables:
                conn.execute("DELETE FROM sources WHERE id=?", (identity,))
            folder = _source_dir(store, row["original"])
            if folder:
                folders.append(folder)
        elif kind == "object":
            rows = _rows(conn, "SELECT root,folder FROM publications WHERE object_id=?", (identity,)) if "publications" in tables else []
            folders.extend(_product_dirs(store, rows))
            if "publications" in tables:
                conn.execute("DELETE FROM publications WHERE object_id=?", (identity,))
            _drop_object_rows(conn, tables, [identity])
        else:
            identities = [r["id"] for r in _rows(conn, "SELECT id FROM objects WHERE experiment_id=?", (identity,))]
            rows = _rows(conn, "SELECT root,folder FROM publications WHERE object_id IN (" + ",".join("?" for _ in identities or [""]) + ")", identities) if identities and "publications" in tables else []
            folders.extend(_product_dirs(store, rows))
            if "publications" in tables and identities:
                conn.execute("DELETE FROM publications WHERE object_id IN (" + ",".join("?" for _ in identities) + ")", identities)
            _drop_object_rows(conn, tables, identities)
            for table, column in (("messages", "experiment_id"), ("datasets", "experiment_id"), ("runs", "experiment_id"),
                                  ("questions", "experiment_id"), ("experiment_draft_tasks", "experiment_id"),
                                  ("worksheet_builds", "experiment_id"), ("worksheet_followups", "experiment_id")):
                if table in tables:
                    columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
                    if column in columns:
                        conn.execute(f'DELETE FROM "{table}" WHERE "{column}"=?', (identity,))
            if "experiments" in tables:
                conn.execute("DELETE FROM experiments WHERE id=?", (identity,))
    removed = []
    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)
        removed.append(str(folder))
    store.audit("remove", {"kind": kind, "id": identity, "title": label, "paths": removed})
    return {"id": identity, "title": label, "removed": True, "paths": removed}
