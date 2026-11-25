from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def uid(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:20]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: bytes | str) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(
 id TEXT PRIMARY KEY, sha TEXT UNIQUE NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
 original TEXT NOT NULL, created TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pages(
 source_id TEXT NOT NULL REFERENCES sources(id), number INTEGER NOT NULL,
 label TEXT NOT NULL, text TEXT NOT NULL, image TEXT, warnings TEXT NOT NULL,
 PRIMARY KEY(source_id,number));
CREATE TABLE IF NOT EXISTS anchors(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), source_sha TEXT NOT NULL,
 page INTEGER NOT NULL, label TEXT NOT NULL, quote TEXT NOT NULL, region TEXT,
 status TEXT NOT NULL, UNIQUE(source_id,page,quote,region));
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL,
 payload TEXT NOT NULL, result TEXT, error TEXT, cancel INTEGER NOT NULL DEFAULT 0,
 created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS categories(
 id TEXT PRIMARY KEY, parent TEXT REFERENCES categories(id), name TEXT NOT NULL,
 domain TEXT NOT NULL, UNIQUE(parent,name,domain));
CREATE TABLE IF NOT EXISTS objects(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, experiment_id TEXT, revision INTEGER NOT NULL,
 title TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revisions(
 object_id TEXT NOT NULL REFERENCES objects(id), revision INTEGER NOT NULL,
 kind TEXT NOT NULL, pack_id TEXT NOT NULL, pack_version TEXT NOT NULL,
 pack_digest TEXT NOT NULL, data_version INTEGER NOT NULL, content TEXT NOT NULL,
 sources TEXT NOT NULL, assets TEXT NOT NULL, human_baseline TEXT, created TEXT NOT NULL,
 PRIMARY KEY(object_id,revision));
CREATE TABLE IF NOT EXISTS memberships(
 object_id TEXT NOT NULL REFERENCES objects(id), category_id TEXT NOT NULL REFERENCES categories(id),
 PRIMARY KEY(object_id,category_id));
CREATE TABLE IF NOT EXISTS experiments(
 id TEXT PRIMARY KEY, title TEXT NOT NULL, goal TEXT NOT NULL, background TEXT NOT NULL,
 source_ids TEXT NOT NULL, knowledge_refs TEXT NOT NULL, instruments TEXT NOT NULL,
 created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
 id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id), role TEXT NOT NULL,
 content TEXT NOT NULL, mode TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS datasets(
 id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id), source_id TEXT NOT NULL REFERENCES sources(id),
 mapping TEXT NOT NULL, confirmed INTEGER NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id), program_id TEXT NOT NULL,
 program_revision INTEGER NOT NULL, status TEXT NOT NULL, info TEXT NOT NULL,
 created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, details TEXT NOT NULL, created TEXT NOT NULL);
"""


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.authority = self.root / "authority"
        self.assets = self.authority / "assets"
        self.originals = self.authority / "originals"
        self.staging = self.root / "staging"
        for path in (self.assets, self.originals, self.staging):
            path.mkdir(parents=True, exist_ok=True)
        self.db = self.authority / "library.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            from . import knowledge_index
            conn.executescript(knowledge_index.SCHEMA)
            from . import knowledge_notes
            conn.executescript(knowledge_notes.SCHEMA)
            knowledge_index.migrate(conn)
            legacy_anchor_key=any(index[2] and [c[2] for c in conn.execute(
                'PRAGMA index_info("'+index[1]+'")')]==['source_id','page','quote']
                for index in conn.execute('PRAGMA index_list(anchors)'))
            if legacy_anchor_key:
                # Preserve every old identity and region. A different crop is
                # a new source location, never an edit to a historical anchor.
                conn.execute('SAVEPOINT anchor_regions')
                try:
                    conn.execute('CREATE TABLE anchors_regions(id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), source_sha TEXT NOT NULL, page INTEGER NOT NULL, label TEXT NOT NULL, quote TEXT NOT NULL, region TEXT, status TEXT NOT NULL, UNIQUE(source_id,page,quote,region))')
                    conn.execute('INSERT INTO anchors_regions SELECT * FROM anchors')
                    conn.execute('DROP TABLE anchors')
                    conn.execute('ALTER TABLE anchors_regions RENAME TO anchors')
                    conn.execute('RELEASE anchor_regions')
                except Exception:
                    conn.execute('ROLLBACK TO anchor_regions');conn.execute('RELEASE anchor_regions');raise
            columns={r[1] for r in conn.execute("PRAGMA table_info(experiments)")}
            if "notes" not in columns:
                conn.execute("ALTER TABLE experiments ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
            if "agent_options" not in columns:
                conn.execute("ALTER TABLE experiments ADD COLUMN agent_options TEXT NOT NULL DEFAULT '{}'")
            columns={r[1] for r in conn.execute("PRAGMA table_info(datasets)")}
            if "selection" not in columns:conn.execute("ALTER TABLE datasets ADD COLUMN selection TEXT NOT NULL DEFAULT '{}'")
            conn.execute("INSERT OR IGNORE INTO meta VALUES('schema','1')")
            from .knowledge_seed import install
            install(conn)
            conn.execute("UPDATE jobs SET status='interrupted', error='宿主重启；未重放任务，可重试', updated=? WHERE status IN ('queued','running')", (now(),))
            conn.execute("UPDATE runs SET status='interrupted', updated=? WHERE status IN ('queued','running')", (now(),))

    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:yield conn
        finally:conn.close()

    @contextlib.contextmanager
    def transaction(self):
        with self.lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    def all(self, sql, args=()):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, args)]

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.transaction() as conn:
            conn.execute(sql, args)

    def audit(self, event, details):
        self.execute("INSERT INTO audit(event,details,created) VALUES(?,?,?)", (event, canonical(details), now()))

    def object(self, object_id, revision=None):
        obj = self.one("SELECT * FROM objects WHERE id=?", (object_id,))
        if not obj:
            raise ValueError("成果不存在")
        revision = revision or obj["revision"]
        rev = self.one("SELECT * FROM revisions WHERE object_id=? AND revision=?", (object_id, revision))
        if not rev:
            raise ValueError("修订不存在")
        for key in ("content", "sources", "assets"):
            rev[key] = json.loads(rev[key])
        return {**obj, **rev, "id": object_id}

    def anchor(self, anchor_id):
        result = self.one("SELECT a.*,s.name,s.kind,s.metadata FROM anchors a JOIN sources s ON a.source_id=s.id WHERE a.id=?", (anchor_id,))
        if not result:
            raise ValueError("来源锚点不存在："+str(anchor_id)+"；须使用读取工具实际返回的标识")
        result["metadata"] = json.loads(result["metadata"])
        result["region"] = json.loads(result["region"]) if result["region"] else None
        return result

    def experiment(self, experiment_id):
        row = self.one("SELECT * FROM experiments WHERE id=?", (experiment_id,))
        if not row:
            raise ValueError("实验不存在")
        for key in ("background", "source_ids", "knowledge_refs", "instruments", "agent_options"):
            row[key] = json.loads(row[key])
        return row

    def create_experiment(self, title, goal, background, source_ids, notes=""):
        if not title.strip() or not goal.strip():
            raise ValueError("请填写实验名称和目标")
        for source_id in source_ids:
            if not self.one("SELECT id FROM sources WHERE id=?", (source_id,)):
                raise ValueError("选用资料不存在")
        identity = uid("exp")
        self.execute("INSERT INTO experiments(id,title,goal,background,source_ids,knowledge_refs,instruments,created,updated,agent_options,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            identity, title.strip(), goal.strip(), canonical(background), canonical(source_ids), "[]", "[]", now(), now(), "{}", notes.strip(),
        ))
        return self.experiment(identity)

    def message(self, experiment_id, role, content, mode):
        self.experiment(experiment_id)
        self.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)", (uid("msg"), experiment_id, role, content, mode, now()))

    def asset_path(self, relative):
        path = (self.authority / relative).resolve()
        if not (path.is_relative_to(self.assets) or path.is_relative_to(self.originals)) or not path.is_file() or path.is_symlink():
            raise ValueError("资产路径无效")
        return path
