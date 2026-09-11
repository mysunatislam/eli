"""Memory Agent: local, encrypted, searchable memory plus a small personal knowledge graph.

- SQLite database in backend/data/eli.db; every memory, transcript line, entity name and photo
  caption is Fernet-encrypted at rest. The key lives in Windows Credential Manager.
- Vector search: 384-d embedding per memory (hash embedder by default, `fastembed` optional;
  sqlite-vec accelerates KNN when it loads).
- Knowledge graph: entities (project, file, tool, person, place, topic) and edges between them,
  so "open my CPAP project" can pull files, notes and conversations together.
- World frames: captions of phone photos, stored encrypted and searchable like any memory.
- Forgetting is a hard delete.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("eli.memory")

SERVICE = "eli-companion"
KEY_NAME = "memory-key"
DIM = 384
TOKEN_RE = re.compile(r"[a-z0-9]+")
STOP = set("the a an and or of to in on for with is are was were be been i my me you your it this that at by as from "
           "do does did have has had will would can could should please just really very always usually".split())
KINDS = ("preference", "fact", "episode", "task", "solution", "workflow", "project")
ENTITY_KINDS = ("project", "file", "folder", "tool", "person", "place", "topic", "device")


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOP]


class HashEmbedder:
    """Feature-hashing embedding: unigrams + bigrams + 4-char prefixes, signed, L2-normalised."""
    name = "hash-384"

    def embed(self, text: str) -> np.ndarray:
        v = np.zeros(DIM, dtype=np.float32)
        toks = tokens(text)
        feats = [(t, 1.0) for t in toks]
        feats += [(a + "_" + b, 0.6) for a, b in zip(toks, toks[1:])]
        feats += [(t[:4] + "~", 0.4) for t in toks if len(t) > 4]
        for f, w in feats:
            h = hashlib.blake2b(f.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % DIM
            sign = 1.0 if (h[4] & 1) else -1.0
            v[idx] += sign * w
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v


class FastEmbedder:
    """Optional semantic embedder (BAAI/bge-small-en-v1.5, 384-d, ~130 MB download on first use)."""
    name = "bge-small-en-v1.5"

    def __init__(self):
        from fastembed import TextEmbedding  # type: ignore
        self._m = TextEmbedding("BAAI/bge-small-en-v1.5")

    def embed(self, text: str) -> np.ndarray:
        v = next(iter(self._m.embed([text]))).astype(np.float32)
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v


def make_embedder():
    if os.getenv("ELI_EMBEDDER", "hash").lower() == "fastembed":
        try:
            return FastEmbedder()
        except Exception as e:
            log.warning("fastembed unavailable (%s); falling back to hash embedder", e)
    return HashEmbedder()


@dataclass
class Memory:
    id: int
    ts: float
    kind: str
    content: str
    importance: float
    score: float = 0.0

    def as_dict(self) -> dict:
        return {"id": self.id, "ts": self.ts, "kind": self.kind, "content": self.content,
                "importance": self.importance, "score": round(self.score, 3)}


@dataclass
class Entity:
    id: int
    kind: str
    name: str
    meta: dict
    ts: float
    last_seen: float


@dataclass
class KnowledgeChunk:
    id: int
    doc_id: int
    chunk_index: int
    section_title: str
    content: str
    doc_path: str = ""
    doc_title: str = ""
    category: str = "general"
    score: float = 0.0

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "doc_id": self.doc_id,
            "chunk_index": self.chunk_index,
            "section_title": self.section_title,
            "content": self.content,
            "doc_path": self.doc_path,
            "doc_title": self.doc_title,
            "category": self.category,
            "score": round(self.score, 3)
        }


def chunk_text(text: str, chunk_size: int = 600, overlap: int = 120) -> list[tuple[str, str]]:
    """Splits text into (section_title, chunk_content) pairs.
    Handles Markdown headers, function boundaries, and sliding windows.
    """
    lines = text.splitlines()
    sections: list[tuple[str, str]] = []
    current_title = "General"
    current_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("# ", "## ", "### ", "#### ")):
            if current_lines:
                sec_text = "\n".join(current_lines).strip()
                if sec_text:
                    sections.append((current_title, sec_text))
                current_lines = []
            current_title = stripped.lstrip("#").strip()
        else:
            current_lines.append(line)

    if current_lines:
        sec_text = "\n".join(current_lines).strip()
        if sec_text:
            sections.append((current_title, sec_text))

    if not sections:
        sections = [("General", text)]

    chunks: list[tuple[str, str]] = []
    for title, sec_content in sections:
        if len(sec_content) <= chunk_size:
            if sec_content.strip():
                chunks.append((title, sec_content.strip()))
            continue

        start = 0
        idx = 1
        while start < len(sec_content):
            end = start + chunk_size
            if end < len(sec_content):
                split_at = max(sec_content.rfind("\n\n", start, end), sec_content.rfind(". ", start, end))
                if split_at > start + chunk_size // 2:
                    end = split_at + 1
            chunk_sub = sec_content[start:end].strip()
            if chunk_sub:
                sub_title = f"{title} (part {idx})" if idx > 1 else title
                chunks.append((sub_title, chunk_sub))
                idx += 1
            start = end - overlap if (end - overlap) > start else end

    return chunks


class MemoryAgent:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = data_dir / "eli.db"
        self.key_source = ""
        self._key = self._load_key(data_dir)
        self.fernet = Fernet(self._key)
        self.db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()
        self.embedder = make_embedder()
        self._init_schema()
        self.vec_ok = self._init_vec()
        self.private = False  # set by the server when private mode is on: no writes
        log.info("memory ready: %s (embedder=%s, sqlite-vec=%s, key=%s)", self.db_path, self.embedder.name, self.vec_ok, self.key_source)

    # -- crypto ----------------------------------------------------------------------------------
    def _load_key(self, data_dir: Path) -> bytes:
        try:
            import keyring  # type: ignore
            key = keyring.get_password(SERVICE, KEY_NAME)
            if not key:
                key = Fernet.generate_key().decode()
                keyring.set_password(SERVICE, KEY_NAME, key)
            self.key_source = "Windows Credential Manager"
            return key.encode()
        except Exception as e:
            log.warning("keyring unavailable (%s); using a key file next to the database", e)
            kf = data_dir / ".memory.key"
            if not kf.exists():
                kf.write_bytes(Fernet.generate_key())
            self.key_source = "key file (data/.memory.key)"
            return kf.read_bytes().strip()

    def enc(self, text: str) -> bytes:
        return self.fernet.encrypt(text.encode("utf-8"))

    def dec(self, blob) -> str:
        try:
            return self.fernet.decrypt(bytes(blob)).decode("utf-8")
        except (InvalidToken, TypeError):
            return ""

    def _hash(self, text: str) -> str:
        """Keyed hash for exact-match lookups without storing plaintext."""
        return hashlib.blake2b(text.encode("utf-8"), key=self._key[:32], digest_size=16).hexdigest()

    # -- schema ----------------------------------------------------------------------------------
    def _init_schema(self) -> None:
        with self._lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories(
                    id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL,
                    content BLOB NOT NULL, importance REAL DEFAULT 0.5, source TEXT DEFAULT 'user');
                CREATE TABLE IF NOT EXISTS memory_vectors(
                    memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
                    model TEXT, vec BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS conversations(
                    id INTEGER PRIMARY KEY, ts REAL NOT NULL, role TEXT NOT NULL, source TEXT, content BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(
                    id INTEGER PRIMARY KEY, ts REAL NOT NULL, goal BLOB NOT NULL, steps BLOB, status TEXT);
                CREATE TABLE IF NOT EXISTS audit_log(
                    id INTEGER PRIMARY KEY, ts REAL NOT NULL, actor TEXT, action TEXT, target TEXT, detail TEXT);
                CREATE TABLE IF NOT EXISTS entities(
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name BLOB NOT NULL, name_hash TEXT NOT NULL UNIQUE,
                    meta BLOB, ts REAL NOT NULL, last_seen REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS edges(
                    id INTEGER PRIMARY KEY, src INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    rel TEXT NOT NULL, dst INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    weight REAL DEFAULT 1.0, ts REAL NOT NULL, UNIQUE(src, rel, dst));
                CREATE TABLE IF NOT EXISTS jobs(
                    id INTEGER PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL, text BLOB NOT NULL,
                    every REAL, next_run REAL NOT NULL, last_run REAL, runs INTEGER DEFAULT 0, max_runs INTEGER DEFAULT 0,
                    until_done INTEGER DEFAULT 0, status TEXT DEFAULT 'active', last_result BLOB, source TEXT);
                CREATE TABLE IF NOT EXISTS world_frames(
                    id INTEGER PRIMARY KEY, ts REAL NOT NULL, source TEXT, caption BLOB NOT NULL, thumb BLOB,
                    memory_id INTEGER);
                CREATE TABLE IF NOT EXISTS documents(
                    id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                    category TEXT DEFAULT 'general', checksum TEXT NOT NULL, chunk_count INTEGER DEFAULT 0,
                    mtime REAL NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS knowledge_chunks(
                    id INTEGER PRIMARY KEY, doc_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL, section_title TEXT, content BLOB NOT NULL,
                    tokens INTEGER DEFAULT 0, importance REAL DEFAULT 0.6);
                CREATE TABLE IF NOT EXISTS knowledge_vectors(
                    chunk_id INTEGER PRIMARY KEY REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
                    model TEXT, vec BLOB NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_conv_ts ON conversations(ts);
                CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind);
                CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
                CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
                CREATE INDEX IF NOT EXISTS idx_doc_path ON documents(path);
                CREATE INDEX IF NOT EXISTS idx_chunk_doc ON knowledge_chunks(doc_id);
                """
            )
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.commit()

    def _init_vec(self) -> bool:
        try:
            import sqlite_vec  # type: ignore
            self.db.enable_load_extension(True)
            sqlite_vec.load(self.db)
            self.db.enable_load_extension(False)
            with self._lock:
                self.db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[{DIM}])")
                self.db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_knowledge USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[{DIM}])")
                self.db.commit()
            return True
        except Exception as e:
            log.info("sqlite-vec not active (%s); using numpy search", e)
            return False

    # -- memories --------------------------------------------------------------------------------
    def remember(self, content: str, kind: str = "fact", importance: float = 0.5, source: str = "user") -> Memory:
        content = content.strip()
        kind = kind if kind in KINDS else "fact"
        vec = self.embedder.embed(content)
        for m in self._scored(vec, tokens(content), kinds=[kind], k=1):
            if m.score >= 0.92:
                with self._lock:
                    self.db.execute("UPDATE memories SET ts=?, importance=MAX(importance, ?) WHERE id=?", (time.time(), importance, m.id))
                    self.db.commit()
                return m
        if self.private:
            return Memory(id=0, ts=time.time(), kind=kind, content=content, importance=importance)
        with self._lock:
            cur = self.db.execute("INSERT INTO memories(ts, kind, content, importance, source) VALUES(?,?,?,?,?)",
                                  (time.time(), kind, self.enc(content), float(importance), source))
            mid = cur.lastrowid
            self.db.execute("INSERT INTO memory_vectors(memory_id, model, vec) VALUES(?,?,?)", (mid, self.embedder.name, vec.tobytes()))
            if self.vec_ok:
                self.db.execute("INSERT INTO vec_memories(memory_id, embedding) VALUES(?, ?)", (mid, vec.tobytes()))
            self.db.commit()
        self.audit("memory", "remember", str(mid), kind)
        return Memory(id=mid, ts=time.time(), kind=kind, content=content, importance=importance, score=1.0)

    def _all(self, kinds: Optional[list[str]] = None) -> list[tuple[Memory, np.ndarray]]:
        q = "SELECT m.id, m.ts, m.kind, m.content, m.importance, v.vec FROM memories m JOIN memory_vectors v ON v.memory_id=m.id"
        args: tuple = ()
        if kinds:
            q += " WHERE m.kind IN (%s)" % ",".join("?" * len(kinds))
            args = tuple(kinds)
        with self._lock:
            rows = self.db.execute(q, args).fetchall()
        out = []
        for mid, ts, kind, blob, imp, vec in rows:
            text = self.dec(blob)
            if text:
                out.append((Memory(mid, ts, kind, text, imp), np.frombuffer(vec, dtype=np.float32)))
        return out

    def _scored(self, qvec: np.ndarray, qtok: list[str], kinds=None, k: int = 5) -> list[Memory]:
        qset = set(qtok)
        scored: list[Memory] = []
        for m, vec in self._all(kinds):
            cos = float(np.dot(qvec, vec)) if vec.shape == qvec.shape else 0.0
            mset = set(tokens(m.content))
            jac = len(qset & mset) / max(1, len(qset | mset)) if qset else 0.0
            overlap = len(qset & mset) / max(1, len(qset)) if qset else 0.0
            m.score = 0.5 * cos + 0.2 * jac + 0.25 * overlap + 0.05 * m.importance
            scored.append(m)
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:k]

    def recall(self, query: str, k: int = 5, kinds: Optional[list[str]] = None, min_score: float = 0.12) -> list[Memory]:
        qvec = self.embedder.embed(query)
        return [m for m in self._scored(qvec, tokens(query), kinds, k) if m.score >= min_score]

    def preferences(self, limit: int = 20) -> list[Memory]:
        with self._lock:
            rows = self.db.execute("SELECT id, ts, kind, content, importance FROM memories WHERE kind='preference' ORDER BY importance DESC, ts DESC LIMIT ?", (limit,)).fetchall()
        return [Memory(r[0], r[1], r[2], self.dec(r[3]), r[4]) for r in rows if self.dec(r[3])]

    def recent(self, limit: int = 10, kinds: Optional[list[str]] = None) -> list[Memory]:
        q = "SELECT id, ts, kind, content, importance FROM memories"
        args: tuple = ()
        if kinds:
            q += " WHERE kind IN (%s)" % ",".join("?" * len(kinds))
            args = tuple(kinds)
        q += " ORDER BY ts DESC LIMIT ?"
        with self._lock:
            rows = self.db.execute(q, args + (limit,)).fetchall()
        return [Memory(r[0], r[1], r[2], self.dec(r[3]), r[4]) for r in rows if self.dec(r[3])]

    def forget(self, query: Optional[str] = None, memory_id: Optional[int] = None, everything: bool = False,
               since: Optional[float] = None) -> int:
        ids: list[int] = []
        if everything:
            with self._lock:
                ids = [r[0] for r in self.db.execute("SELECT id FROM memories").fetchall()]
                self.db.execute("DELETE FROM edges")
                self.db.execute("DELETE FROM entities")
                self.db.execute("DELETE FROM world_frames")
                self.db.execute("DELETE FROM conversations")
                self.db.commit()
        elif memory_id is not None:
            ids = [memory_id]
        elif since is not None:
            with self._lock:
                ids = [r[0] for r in self.db.execute("SELECT id FROM memories WHERE ts>=?", (since,)).fetchall()]
                self.db.execute("DELETE FROM conversations WHERE ts>=?", (since,))
                self.db.execute("DELETE FROM world_frames WHERE ts>=?", (since,))
                self.db.commit()
        elif query:
            ids = [m.id for m in self.recall(query, k=10, min_score=0.35)]
        if not ids:
            return 0
        with self._lock:
            for mid in ids:
                self.db.execute("DELETE FROM memory_vectors WHERE memory_id=?", (mid,))
                if self.vec_ok:
                    self.db.execute("DELETE FROM vec_memories WHERE memory_id=?", (mid,))
                self.db.execute("DELETE FROM world_frames WHERE memory_id=?", (mid,))
                self.db.execute("DELETE FROM memories WHERE id=?", (mid,))
            self.db.commit()
        self.audit("memory", "forget", ",".join(map(str, ids)), query or ("all" if everything else ""))
        return len(ids)

    # -- knowledge graph -----------------------------------------------------------------------------
    def upsert_entity(self, kind: str, name: str, meta: Optional[dict] = None) -> Entity:
        kind = kind if kind in ENTITY_KINDS else "topic"
        name = name.strip()
        h = self._hash(f"{kind}:{name.lower()}")
        now = time.time()
        with self._lock:
            row = self.db.execute("SELECT id, meta, ts FROM entities WHERE name_hash=?", (h,)).fetchone()
            if row:
                merged = {}
                try:
                    merged = json.loads(self.dec(row[1]) or "{}")
                except Exception:
                    pass
                merged.update(meta or {})
                self.db.execute("UPDATE entities SET last_seen=?, meta=? WHERE id=?", (now, self.enc(json.dumps(merged)), row[0]))
                self.db.commit()
                return Entity(row[0], kind, name, merged, row[2], now)
            if self.private:
                return Entity(0, kind, name, meta or {}, now, now)
            cur = self.db.execute("INSERT INTO entities(kind, name, name_hash, meta, ts, last_seen) VALUES(?,?,?,?,?,?)",
                                  (kind, self.enc(name), h, self.enc(json.dumps(meta or {})), now, now))
            self.db.commit()
            return Entity(cur.lastrowid, kind, name, meta or {}, now, now)

    def link(self, src: Entity, rel: str, dst: Entity, weight: float = 1.0) -> None:
        if not src.id or not dst.id or self.private:
            return
        with self._lock:
            self.db.execute("INSERT INTO edges(src, rel, dst, weight, ts) VALUES(?,?,?,?,?) "
                            "ON CONFLICT(src, rel, dst) DO UPDATE SET weight=weight+excluded.weight, ts=excluded.ts",
                            (src.id, rel, dst.id, weight, time.time()))
            self.db.commit()

    def _row_entity(self, r) -> Entity:
        try:
            meta = json.loads(self.dec(r[3]) or "{}")
        except Exception:
            meta = {}
        return Entity(r[0], r[1], self.dec(r[2]), meta, r[4], r[5])

    def entities(self, kind: Optional[str] = None) -> list[Entity]:
        q = "SELECT id, kind, name, meta, ts, last_seen FROM entities"
        args: tuple = ()
        if kind:
            q += " WHERE kind=?"
            args = (kind,)
        with self._lock:
            rows = self.db.execute(q + " ORDER BY last_seen DESC", args).fetchall()
        return [self._row_entity(r) for r in rows]

    def find_entities(self, query: str, kind: Optional[str] = None, k: int = 5) -> list[Entity]:
        qt = set(tokens(query)) or {query.lower()}
        scored = []
        for e in self.entities(kind):
            et = set(tokens(e.name)) | {e.name.lower()}
            score = len(qt & et) / max(1, len(qt))
            if query.lower() in e.name.lower():
                score += 0.5
            if score > 0:
                scored.append((score, e))
        scored.sort(key=lambda x: (x[0], x[1].last_seen), reverse=True)
        return [e for _, e in scored[:k]]

    def neighbors(self, ent: Entity, rel: Optional[str] = None) -> list[tuple[str, Entity]]:
        q = ("SELECT e.id, e.kind, e.name, e.meta, e.ts, e.last_seen, g.rel FROM edges g JOIN entities e ON e.id=g.dst "
             "WHERE g.src=?" + (" AND g.rel=?" if rel else "") + " ORDER BY g.weight DESC, g.ts DESC")
        args = (ent.id, rel) if rel else (ent.id,)
        with self._lock:
            rows = self.db.execute(q, args).fetchall()
        return [(r[6], self._row_entity(r[:6])) for r in rows]

    def remember_project(self, name: str, paths: Optional[list[str]] = None, notes: str = "", tools: Optional[list[str]] = None) -> Entity:
        proj = self.upsert_entity("project", name, {"notes": notes} if notes else None)
        for p in paths or []:
            pp = Path(p)
            kind = "folder" if pp.is_dir() else "file"
            f = self.upsert_entity(kind, pp.name, {"path": str(pp)})
            self.link(proj, "has_" + kind, f)
        for t in tools or []:
            tool = self.upsert_entity("tool", t)
            self.link(proj, "uses", tool)
        if notes:
            self.remember(f"Project {name}: {notes}", kind="project", importance=0.7)
        elif not self.recall(f"project {name}", k=1, kinds=["project"]):
            self.remember(f"The user has a project called {name}.", kind="project", importance=0.6)
        return proj

    def project_bundle(self, query: str) -> dict:
        ents = self.find_entities(query, kind="project", k=1)
        if not ents:
            return {"found": False, "query": query, "memories": [m.as_dict() for m in self.recall(query, k=5)],
                    "conversation": self.search_conversation(query, 5)}
        proj = ents[0]
        files, folders, tools = [], [], []
        for rel, e in self.neighbors(proj):
            if e.kind == "file":
                files.append(e.meta.get("path") or e.name)
            elif e.kind == "folder":
                folders.append(e.meta.get("path") or e.name)
            elif e.kind == "tool":
                tools.append(e.name)
        return {"found": True, "project": proj.name, "notes": proj.meta.get("notes", ""), "files": files, "folders": folders,
                "tools": tools, "memories": [m.as_dict() for m in self.recall(proj.name, k=6)],
                "conversation": self.search_conversation(proj.name, 6)}

    # -- world frames (phone photos) --------------------------------------------------------------------
    def add_world_frame(self, caption: str, thumb: Optional[bytes] = None, source: str = "phone") -> Memory:
        when = time.strftime("%A %Y-%m-%d %H:%M")
        mem = self.remember(f"Photo from {source} on {when}: {caption}", kind="episode", importance=0.6, source=source)
        if self.private or not mem.id:
            return mem
        with self._lock:
            self.db.execute("INSERT INTO world_frames(ts, source, caption, thumb, memory_id) VALUES(?,?,?,?,?)",
                            (time.time(), source, self.enc(caption), self.fernet.encrypt(thumb) if thumb else None, mem.id))
            self.db.commit()
        return mem

    def world_frames(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT id, ts, source, caption FROM world_frames ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "ts": r[1], "source": r[2], "caption": self.dec(r[3])} for r in rows]

    # -- scheduled jobs ---------------------------------------------------------------------------------
    def job_add(self, kind: str, text: str, next_run: float, every: Optional[float] = None, max_runs: int = 0,
                until_done: bool = False, source: str = "user") -> dict:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO jobs(created, kind, text, every, next_run, runs, max_runs, until_done, status, source) VALUES(?,?,?,?,?,0,?,?,'active',?)",
                (time.time(), kind, self.enc(text), every, next_run, max_runs, int(until_done), source))
            self.db.commit()
            jid = cur.lastrowid
        self.audit("scheduler", "add", str(jid), kind)
        return self.job_get(jid)

    def _job_row(self, r) -> dict:
        return {"id": r[0], "created": r[1], "kind": r[2], "text": self.dec(r[3]), "every": r[4], "next_run": r[5], "last_run": r[6],
                "runs": r[7], "max_runs": r[8], "until_done": bool(r[9]), "status": r[10], "last_result": self.dec(r[11]) if r[11] else "",
                "source": r[12]}

    def job_get(self, jid: int) -> Optional[dict]:
        with self._lock:
            r = self.db.execute("SELECT id, created, kind, text, every, next_run, last_run, runs, max_runs, until_done, status, last_result, source FROM jobs WHERE id=?", (jid,)).fetchone()
        return self._job_row(r) if r else None

    def jobs(self, active_only: bool = True, limit: int = 50) -> list[dict]:
        q = "SELECT id, created, kind, text, every, next_run, last_run, runs, max_runs, until_done, status, last_result, source FROM jobs"
        if active_only:
            q += " WHERE status='active'"
        q += " ORDER BY next_run ASC LIMIT ?"
        with self._lock:
            rows = self.db.execute(q, (limit,)).fetchall()
        return [self._job_row(r) for r in rows]

    def job_update(self, jid: int, **fields) -> bool:
        if not fields:
            return False
        cols, vals = [], []
        for k, v in fields.items():
            if k in ("text", "last_result"):
                v = self.enc(str(v))
            cols.append(f"{k}=?")
            vals.append(v)
        vals.append(jid)
        with self._lock:
            cur = self.db.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id=?", vals)
            self.db.commit()
        return cur.rowcount > 0

    # -- conversations & tasks --------------------------------------------------------------------------
    def log_message(self, role: str, text: str, source: str = "desktop") -> None:
        if self.private:
            return
        with self._lock:
            self.db.execute("INSERT INTO conversations(ts, role, source, content) VALUES(?,?,?,?)", (time.time(), role, source, self.enc(text)))
            self.db.commit()

    def recent_messages(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT ts, role, source, content FROM conversations ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "role": r[1], "source": r[2], "text": self.dec(r[3])} for r in reversed(rows)]

    def search_conversation(self, query: str, limit: int = 6, scan: int = 400) -> list[dict]:
        qt = set(tokens(query))
        if not qt:
            return []
        with self._lock:
            rows = self.db.execute("SELECT ts, role, content FROM conversations ORDER BY ts DESC LIMIT ?", (scan,)).fetchall()
        hits = []
        for ts, role, blob in rows:
            text = self.dec(blob)
            if text and qt & set(tokens(text)):
                hits.append({"ts": ts, "role": role, "text": text[:240]})
            if len(hits) >= limit:
                break
        return hits

    def log_task(self, goal: str, steps: list[str], status: str = "done") -> None:
        if self.private:
            return
        with self._lock:
            self.db.execute("INSERT INTO tasks(ts, goal, steps, status) VALUES(?,?,?,?)",
                            (time.time(), self.enc(goal), self.enc(json.dumps(steps)), status))
            self.db.commit()

    def audit(self, actor: str, action: str, target: str = "", detail: str = "") -> None:
        with self._lock:
            self.db.execute("INSERT INTO audit_log(ts, actor, action, target, detail) VALUES(?,?,?,?,?)", (time.time(), actor, action, target, detail))
            self.db.commit()

    def stats(self) -> dict:
        with self._lock:
            n_mem = self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            n_conv = self.db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            n_task = self.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            n_ent = self.db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            n_edge = self.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            n_wf = self.db.execute("SELECT COUNT(*) FROM world_frames").fetchone()[0]
            n_jobs = self.db.execute("SELECT COUNT(*) FROM jobs WHERE status='active'").fetchone()[0]
            n_docs = self.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            n_chunks = self.db.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
        return {"memories": n_mem, "conversation_lines": n_conv, "tasks": n_task, "entities": n_ent, "edges": n_edge,
                "photos": n_wf, "jobs": n_jobs, "documents": n_docs, "knowledge_chunks": n_chunks,
                "embedder": self.embedder.name, "sqlite_vec": self.vec_ok, "key_source": self.key_source,
                "db": str(self.db_path)}

    # -- Knowledge Base & Document RAG -------------------------------------------------------------
    def ingest_file(self, path: str | Path, category: str = "general", title: str = "") -> dict:
        p = Path(path).resolve()
        if not p.exists() or not p.is_file():
            return {"ok": False, "error": f"File not found: {path}", "chunks": 0}

        try:
            raw_bytes = p.read_bytes()
            text = ""
            for enc in ("utf-8", "latin-1", "cp1252"):
                try:
                    text = raw_bytes.decode(enc)
                    break
                except Exception:
                    continue
            if not text:
                return {"ok": False, "error": "Unable to decode file text", "chunks": 0}
        except Exception as e:
            return {"ok": False, "error": str(e), "chunks": 0}

        checksum = hashlib.sha256(raw_bytes).hexdigest()
        doc_title = title or p.stem.replace("_", " ").title()
        path_str = str(p)
        mtime = p.stat().st_mtime

        with self._lock:
            row = self.db.execute("SELECT id, checksum, chunk_count FROM documents WHERE path=?", (path_str,)).fetchone()
            if row and row[1] == checksum:
                return {"ok": True, "status": "unchanged", "doc_id": row[0], "title": doc_title, "chunks": row[2]}

            if row:
                doc_id = row[0]
                old_chunk_ids = [r[0] for r in self.db.execute("SELECT id FROM knowledge_chunks WHERE doc_id=?", (doc_id,)).fetchall()]
                for cid in old_chunk_ids:
                    self.db.execute("DELETE FROM knowledge_vectors WHERE chunk_id=?", (cid,))
                    if self.vec_ok:
                        self.db.execute("DELETE FROM vec_knowledge WHERE chunk_id=?", (cid,))
                self.db.execute("DELETE FROM knowledge_chunks WHERE doc_id=?", (doc_id,))
                self.db.execute("UPDATE documents SET checksum=?, mtime=?, title=?, category=? WHERE id=?",
                                (checksum, mtime, doc_title, category, doc_id))
            else:
                cur = self.db.execute("INSERT INTO documents(path, title, category, checksum, chunk_count, mtime, created) VALUES(?,?,?,?,0,?,?)",
                                      (path_str, doc_title, category, checksum, mtime, time.time()))
                doc_id = cur.lastrowid

        chunks = chunk_text(text)
        chunk_count = len(chunks)

        with self._lock:
            for idx, (sec_title, chunk_content) in enumerate(chunks):
                cur = self.db.execute(
                    "INSERT INTO knowledge_chunks(doc_id, chunk_index, section_title, content, tokens, importance) VALUES(?,?,?,?,?,?)",
                    (doc_id, idx, sec_title, self.enc(chunk_content), len(chunk_content) // 4, 0.6)
                )
                cid = cur.lastrowid
                vec = self.embedder.embed(f"{doc_title} {sec_title}: {chunk_content}")
                self.db.execute("INSERT INTO knowledge_vectors(chunk_id, model, vec) VALUES(?,?,?)", (cid, self.embedder.name, vec.tobytes()))
                if self.vec_ok:
                    self.db.execute("INSERT INTO vec_knowledge(chunk_id, embedding) VALUES(?,?)", (cid, vec.tobytes()))

            self.db.execute("UPDATE documents SET chunk_count=? WHERE id=?", (chunk_count, doc_id))
            self.db.commit()

        self.audit("knowledge", "ingest", path_str, f"{chunk_count} chunks")
        log.info("Ingested document '%s' (%d chunks) into knowledge base", doc_title, chunk_count)
        return {"ok": True, "status": "ingested", "doc_id": doc_id, "title": doc_title, "chunks": chunk_count}

    def ingest_directory(self, dir_path: str | Path, extensions: Optional[list[str]] = None,
                         recursive: bool = True, max_files: int = 150) -> dict:
        dp = Path(dir_path).resolve()
        if not dp.exists() or not dp.is_dir():
            return {"ok": False, "error": f"Directory not found: {dir_path}", "files": 0}

        exts = set(e.lower().lstrip(".") for e in (extensions or ["md", "txt", "py", "m", "js", "ts", "json", "yml", "yaml", "html"]))
        files = []
        pattern = "**/*" if recursive else "*"
        for f in dp.glob(pattern):
            if f.is_file() and f.suffix.lstrip(".").lower() in exts:
                p_str = str(f).lower()
                if any(ignored in p_str for ignored in ("\\.git\\", "\\node_modules\\", "\\.venv\\", "\\__pycache__\\", "\\brain\\")):
                    continue
                files.append(f)
                if len(files) >= max_files:
                    break

        ingested = 0
        unchanged = 0
        total_chunks = 0
        for f in files:
            res = self.ingest_file(f, category="codebase" if f.suffix in (".py", ".m", ".js", ".ts") else "documentation")
            if res.get("ok"):
                if res.get("status") == "ingested":
                    ingested += 1
                else:
                    unchanged += 1
                total_chunks += res.get("chunks", 0)

        return {"ok": True, "files_found": len(files), "ingested": ingested, "unchanged": unchanged, "total_chunks": total_chunks}

    def ingest_text(self, text: str, title: str, category: str = "note") -> dict:
        if self.private or not text.strip():
            return {"ok": False, "error": "Empty text or private mode", "chunks": 0}
        virtual_path = f"note://{time.strftime('%Y%m%d_%H%M%S')}_{re.sub(r'[^a-zA-Z0-9_]', '_', title.lower()[:30])}"
        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        now = time.time()
        with self._lock:
            cur = self.db.execute("INSERT INTO documents(path, title, category, checksum, chunk_count, mtime, created) VALUES(?,?,?,?,0,?,?)",
                                  (virtual_path, title, category, checksum, now, now))
            doc_id = cur.lastrowid
        chunks = chunk_text(text)
        chunk_count = len(chunks)
        with self._lock:
            for idx, (sec_title, chunk_content) in enumerate(chunks):
                cur = self.db.execute(
                    "INSERT INTO knowledge_chunks(doc_id, chunk_index, section_title, content, tokens, importance) VALUES(?,?,?,?,?,?)",
                    (doc_id, idx, sec_title, self.enc(chunk_content), len(chunk_content) // 4, 0.7)
                )
                cid = cur.lastrowid
                vec = self.embedder.embed(f"{title} {sec_title}: {chunk_content}")
                self.db.execute("INSERT INTO knowledge_vectors(chunk_id, model, vec) VALUES(?,?,?)", (cid, self.embedder.name, vec.tobytes()))
                if self.vec_ok:
                    self.db.execute("INSERT INTO vec_knowledge(chunk_id, embedding) VALUES(?,?)", (cid, vec.tobytes()))
            self.db.execute("UPDATE documents SET chunk_count=? WHERE id=?", (chunk_count, doc_id))
            self.db.commit()
        return {"ok": True, "status": "ingested", "doc_id": doc_id, "title": title, "chunks": chunk_count}

    def search_knowledge(self, query: str, limit: int = 5, category: Optional[str] = None, min_score: float = 0.15) -> list[KnowledgeChunk]:
        if self.private:
            return []

        qvec = self.embedder.embed(query)
        qtok = tokens(query)
        qset = set(qtok)

        q = ("SELECT c.id, c.doc_id, c.chunk_index, c.section_title, c.content, c.importance, v.vec, d.path, d.title, d.category "
             "FROM knowledge_chunks c "
             "JOIN knowledge_vectors v ON v.chunk_id=c.id "
             "JOIN documents d ON d.id=c.doc_id")
        args = ()
        if category:
            q += " WHERE d.category=?"
            args = (category,)

        with self._lock:
            rows = self.db.execute(q, args).fetchall()

        scored: list[KnowledgeChunk] = []
        for cid, doc_id, c_idx, sec_title, blob, imp, vec_bytes, path, title, cat in rows:
            text = self.dec(blob)
            if not text:
                continue
            vec = np.frombuffer(vec_bytes, dtype=np.float32)
            cos = float(np.dot(qvec, vec)) if vec.shape == qvec.shape else 0.0
            cset = set(tokens(f"{title} {sec_title} {text}"))
            jac = len(qset & cset) / max(1, len(qset | cset)) if qset else 0.0
            overlap = len(qset & cset) / max(1, len(qset)) if qset else 0.0
            score = 0.5 * cos + 0.25 * jac + 0.2 * overlap + 0.05 * (imp or 0.5)

            if score >= min_score:
                scored.append(KnowledgeChunk(
                    id=cid,
                    doc_id=doc_id,
                    chunk_index=c_idx,
                    section_title=sec_title or "General",
                    content=text,
                    doc_path=path,
                    doc_title=title,
                    category=cat,
                    score=score
                ))

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:limit]

    def list_documents(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT id, path, title, category, chunk_count, mtime, created FROM documents ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "path": r[1], "title": r[2], "category": r[3], "chunks": r[4], "mtime": r[5], "created": r[6]} for r in rows]

    def delete_document(self, doc_id: int) -> bool:
        with self._lock:
            cids = [r[0] for r in self.db.execute("SELECT id FROM knowledge_chunks WHERE doc_id=?", (doc_id,)).fetchall()]
            for cid in cids:
                self.db.execute("DELETE FROM knowledge_vectors WHERE chunk_id=?", (cid,))
                if self.vec_ok:
                    self.db.execute("DELETE FROM vec_knowledge WHERE chunk_id=?", (cid,))
            self.db.execute("DELETE FROM knowledge_chunks WHERE doc_id=?", (doc_id,))
            self.db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            self.db.commit()
        return True

    def rag_context(self, query: str, project: str = "", limit_memories: int = 5) -> dict:
        """Retrieval-Augmented Generation context: combines vector memory search,
        user preferences, past problem solutions, knowledge graph project context,
        and chunked local document knowledge."""
        if self.private:
            return {"preferences": [], "solutions": [], "facts": [], "knowledge": [], "project": None, "formatted": ""}

        # 1. User preferences (always prioritized for personalized alignment)
        prefs = self.preferences(limit=4)
        pref_texts = [p.content for p in prefs]

        # 2. Query-specific vector memories
        recalled = self.recall(query, k=limit_memories, min_score=0.25)
        solutions = [m.content for m in recalled if m.kind == "solution"]
        facts = [m.content for m in recalled if m.kind in ("fact", "preference", "episode", "task") and m.content not in pref_texts]

        if not solutions:
            recent_solutions = [m.content for m in self.recent(limit=4) if m.kind == "solution"]
            solutions.extend(recent_solutions[:2])

        # 3. Knowledge graph project context
        proj_data = None
        proj_name = project
        if not proj_name:
            for ent in self.entities(kind="project")[:10]:
                if ent.name.lower() in query.lower():
                    proj_name = ent.name
                    break

        if proj_name:
            proj_data = self.bundle(proj_name)

        # 4. Search chunked knowledge base documents
        kb_chunks = self.search_knowledge(query, limit=3, min_score=0.2)
        kb_data = [c.as_dict() for c in kb_chunks]

        # 5. Formatted prompt string for injection
        sections = []
        if pref_texts:
            sections.append("User Preferences:\n" + "\n".join(f"- {p}" for p in pref_texts))
        if solutions:
            sections.append("Past Verified Solutions / Fixes:\n" + "\n".join(f"- {s}" for s in solutions))
        if facts:
            sections.append("Relevant Context & Memory:\n" + "\n".join(f"- {f}" for f in facts[:4]))
        if kb_chunks:
            kb_lines = []
            for c in kb_chunks:
                kb_lines.append(f"From '{c.doc_title}' ({c.section_title}):\n{c.content[:400]}")
            sections.append("Local Document & Code Knowledge:\n" + "\n\n".join(kb_lines))
        if proj_data and proj_data.get("found"):
            proj_info = [f"Project: {proj_data.get('name')}"]
            if proj_data.get("folders"):
                proj_info.append("Folders: " + ", ".join(proj_data["folders"]))
            if proj_data.get("tools"):
                proj_info.append("Tools: " + ", ".join(proj_data["tools"]))
            if proj_data.get("notes"):
                proj_info.append("Notes: " + proj_data["notes"])
            sections.append("Project Knowledge:\n" + "\n".join(f"- {i}" for i in proj_info))

        formatted = "\n\n".join(sections)
        return {
            "preferences": pref_texts,
            "solutions": solutions,
            "facts": facts,
            "knowledge": kb_data,
            "project": proj_data,
            "formatted": formatted
        }

    def store_solution(self, goal: str, solution_summary: str, tools_used: list[str] = None) -> int:
        """Stores a verified resolution into memory with high importance so future tasks can recall it."""
        if self.private:
            return 0
        tools_str = f" (using {', '.join(tools_used)})" if tools_used else ""
        content = f"Solution for '{goal}': {solution_summary}{tools_str}"
        return self.remember(content, kind="solution", importance=0.85)

