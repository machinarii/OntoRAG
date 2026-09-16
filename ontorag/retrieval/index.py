"""Rebuildable, workspace-local SQLite FTS5 index. Source chunks remain authoritative.

Use a persistent local working directory shared by API workers on one host.
SQLite WAL is deliberately not a distributed search backend.
"""

import asyncio
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path


async def _run_io(function):
    # A cancelled worker must not release its pipeline reservation while a
    # background SQLite transaction can still commit behind a later delete.
    task = asyncio.create_task(asyncio.to_thread(function))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


def search_text(row: dict) -> str:
    from ontorag.chunk_schema import format_heading_context

    return "\n".join(
        str(value)
        for value in (
            row.get("file_path", ""),
            format_heading_context(row),
            row.get("content", ""),
        )
        if value
    )


class LexicalIndex:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    async def initialize(self):
        def setup():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(id UNINDEXED, text, tokenize='unicode61')"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS chunk_keys (id TEXT PRIMARY KEY, rowid INTEGER UNIQUE)"
                )
                db.execute(
                    "INSERT OR IGNORE INTO chunk_keys SELECT id, rowid FROM chunks"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS revision (id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL)"
                )
                db.execute("INSERT OR IGNORE INTO revision VALUES (1, 0)")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS readiness (id INTEGER PRIMARY KEY CHECK(id=1), ready INTEGER NOT NULL)"
                )
                db.execute("INSERT OR IGNORE INTO readiness VALUES (1, 1)")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, metadata TEXT NOT NULL)"
                )

        await _run_io(setup)

    async def revision(self) -> int:
        def read():
            with self._connect() as db:
                return db.execute("SELECT value FROM revision WHERE id=1").fetchone()[0]

        return await _run_io(read)

    async def set_ready(self, ready: bool):
        def write():
            with self._connect() as db:
                db.execute("UPDATE readiness SET ready=? WHERE id=1", (int(ready),))
                db.execute("UPDATE revision SET value=value+1 WHERE id=1")

        await _run_io(write)

    async def touch(self):
        def write():
            with self._connect() as db:
                db.execute("UPDATE revision SET value=value+1 WHERE id=1")

        await _run_io(write)

    async def upsert(self, rows: dict):
        def write():
            with self._connect() as db:
                for key, row in rows.items():
                    existing = db.execute(
                        "SELECT rowid FROM chunk_keys WHERE id=?", (key,)
                    ).fetchone()
                    if existing:
                        db.execute("DELETE FROM chunks WHERE rowid=?", existing)
                        db.execute(
                            "INSERT INTO chunks(rowid,id,text) VALUES (?,?,?)",
                            (existing[0], key, search_text(row)),
                        )
                    else:
                        cursor = db.execute(
                            "INSERT INTO chunks VALUES (?, ?)", (key, search_text(row))
                        )
                        db.execute(
                            "INSERT INTO chunk_keys VALUES (?, ?)",
                            (key, cursor.lastrowid),
                        )
                db.execute("UPDATE revision SET value=value+1 WHERE id=1")

        await _run_io(write)

    async def delete(self, ids):
        def write():
            with self._connect() as db:
                for key in ids:
                    db.execute(
                        "DELETE FROM chunks WHERE rowid=(SELECT rowid FROM chunk_keys WHERE id=?)",
                        (key,),
                    )
                    db.execute("DELETE FROM chunk_keys WHERE id=?", (key,))
                db.execute("UPDATE revision SET value=value+1 WHERE id=1")

        await _run_io(write)

    async def clear_chunks(self):
        def write():
            with self._connect() as db:
                db.execute("DELETE FROM chunks")
                db.execute("DELETE FROM chunk_keys")
                db.execute("UPDATE revision SET value=value+1 WHERE id=1")

        await _run_io(write)

    async def clear(self):
        def write():
            with self._connect() as db:
                db.execute("DELETE FROM chunks")
                db.execute("DELETE FROM chunk_keys")
                db.execute("DELETE FROM documents")
                db.execute("UPDATE revision SET value=value+1 WHERE id=1")

        await _run_io(write)

    async def search(self, query: str, limit: int) -> list[str]:
        # Never pass user input through the FTS expression parser. Preserve
        # identifier pieces, support Unicode, and bound expression complexity.
        terms = list(dict.fromkeys(re.findall(r"\w+", query, re.UNICODE)))[:64]
        if not terms:
            return []
        expression = " OR ".join('"' + term + '"' for term in terms)

        def read():
            with self._connect() as db:
                if not db.execute("SELECT ready FROM readiness WHERE id=1").fetchone()[
                    0
                ]:
                    raise RuntimeError("Lexical index requires a successful rebuild")
                return [
                    r[0]
                    for r in db.execute(
                        "SELECT id FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks), id LIMIT ?",
                        (expression, limit),
                    )
                ]

        return await _run_io(read)

    async def document_metadata(self, ids: list[str]) -> dict:
        def read():
            with self._connect() as db:
                return {
                    key: json.loads(row[0])
                    for key in ids
                    if (
                        row := db.execute(
                            "SELECT metadata FROM documents WHERE id=?", (key,)
                        ).fetchone()
                    )
                }

        return await _run_io(read)

    async def set_document_metadata(self, doc_id: str, metadata: dict):
        def write():
            with self._connect() as db:
                db.execute(
                    "INSERT OR REPLACE INTO documents VALUES (?, ?)",
                    (doc_id, json.dumps(metadata)),
                )
                db.execute("UPDATE revision SET value=value+1 WHERE id=1")

        await _run_io(write)


class IndexedChunks:
    """Mirror every chunk mutation, including purge and custom-chunk rollback.

    Deletion removes derived hits first; insertion writes authoritative rows first.
    Failures propagate so ingestion cannot report a successful incomplete index.
    Reads must still hydrate hits: a crash can leave a stale derived row.
    """

    def __init__(self, storage, index: LexicalIndex):
        self.storage = storage
        self.index = index

    def __getattr__(self, name):
        return getattr(self.storage, name)

    async def upsert(self, rows):
        try:
            await self.storage.upsert(rows)
            await self.index.upsert(rows)
        except BaseException:
            await self.index.set_ready(False)
            raise

    async def delete(self, ids):
        try:
            await self.index.delete(ids)
            return await self.storage.delete(ids)
        except BaseException:
            await self.index.set_ready(False)
            raise

    async def drop(self):
        await self.index.set_ready(False)
        await self.index.clear()
        result = await self.storage.drop()
        if isinstance(result, dict) and result.get("status") == "error":
            return result
        await self.index.set_ready(True)
        return result


class ContextualVectors:
    """Enrich embedding input without changing the authoritative source text."""

    def __init__(self, storage):
        self.storage = storage

    def __getattr__(self, name):
        return getattr(self.storage, name)

    async def upsert(self, rows):
        tokenizer = self.storage.global_config.get("tokenizer")
        limit = getattr(self.storage.embedding_func, "max_token_size", None)
        enriched = {}
        for key, row in rows.items():
            content = search_text(row)
            if tokenizer and limit and len(tokenizer.encode(content)) > limit:
                # Never truncate source content to make room for optional context.
                content = row["content"]
            enriched[key] = {**row, "content": content}
        return await self.storage.upsert(enriched)
