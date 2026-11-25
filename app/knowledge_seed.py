"""Install the editable starter directory once, preserving existing topic and source identities."""
import json
from pathlib import Path

from .storage import canonical, uid

MARKER = "knowledge_directory_seed_v1"
LEGACY = ("声", "光", "力", "电", "热")


def install(conn):
    if conn.execute("SELECT 1 FROM meta WHERE key=?", (MARKER,)).fetchone():
        return
    seed = json.loads(Path(__file__).with_name("knowledge_seed.json").read_text("utf8"))
    identities = {}

    def visit(nodes, parent=None, path=(), domain=None):
        for node in nodes:
            name = node["name"]
            branch = domain or name
            row = conn.execute("SELECT id FROM categories WHERE parent IS ? AND name=?", (parent,name)).fetchone()
            identity = row["id"] if row else uid("cat")
            if not row:
                conn.execute("INSERT INTO categories VALUES(?,?,?,?)", (identity,parent,name,branch))
            full = (*path,name)
            identities["/".join(full)] = identity
            visit(node.get("children",[]),identity,full,branch)

    visit(seed)
    physics = identities["自然科学/物理学"]
    for name in LEGACY:
        old = conn.execute("SELECT id,parent,name FROM categories WHERE id=?", ("domain-"+name,)).fetchone()
        if not old or old["parent"] is not None or old["name"] != name:
            continue
        collision = conn.execute("SELECT id FROM categories WHERE parent=? AND name=?", (physics,name)).fetchone()
        if not collision:
            conn.execute("UPDATE categories SET parent=? WHERE id=?", (physics,old["id"]))
    conn.execute("INSERT INTO meta(key,value) VALUES(?,?)", (MARKER,canonical(identities)))
    from .knowledge_index import index_topics
    index_topics(conn,rebuild=True)


def physics_id(conn):
    return json.loads(conn.execute("SELECT value FROM meta WHERE key=?", (MARKER,)).fetchone()["value"])["自然科学/物理学"]


def legacy_parent(conn, identity):
    if identity not in {"domain-"+name for name in LEGACY}:
        return identity
    if conn.execute("SELECT 1 FROM categories WHERE id=?", (identity,)).fetchone():
        return identity
    parent=physics_id(conn)
    name=identity.removeprefix("domain-")
    match=conn.execute("SELECT id FROM categories WHERE parent=? AND name=?", (parent,name)).fetchone()
    if match:return match["id"]
    conn.execute("INSERT INTO categories VALUES(?,?,?,?)", (identity,parent,name,name))
    return identity
