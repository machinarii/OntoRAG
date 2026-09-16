"""Workspace-local exact visual index with authoritative chunk associations.

Vectors are normalized float32. Search streams batches to bound working memory;
this deliberately targets document figure galleries, not billion-image corpora.
"""

import heapq

import numpy as np

from ontorag.retrieval.index import LexicalIndex, _run_io


def normalized(vectors):
    array = np.asarray(vectors, dtype=np.float32)
    if (
        array.ndim != 2
        or not 1 <= array.shape[1] <= 8192
        or not np.isfinite(array).all()
    ):
        raise ValueError("Invalid visual embedding shape or values")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    if not np.isfinite(norms).all() or (norms <= 0).any():
        raise ValueError("Visual embeddings must have finite nonzero norms")
    return array / norms


class VisualIndex(LexicalIndex):
    async def initialize(self):
        await super().initialize()

        def setup():
            with self._connect() as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS visual_space (id INTEGER PRIMARY KEY CHECK(id=1), name TEXT, dimension INTEGER)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS images (id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, drawing_id TEXT NOT NULL, digest TEXT NOT NULL, vector BLOB NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS links (chunk_id TEXT NOT NULL, image_id TEXT NOT NULL, PRIMARY KEY(chunk_id,image_id))"
                )
                db.execute("CREATE INDEX IF NOT EXISTS links_image ON links(image_id)")

        await _run_io(setup)

    async def replace_chunks(self, chunk_ids, assets, links, space, vectors):
        embeddings = normalized(vectors) if assets else None
        if assets and len(embeddings) != len(assets):
            raise ValueError("Visual vector count mismatch")

        def write():
            with self._connect() as db:
                if assets:
                    existing = db.execute(
                        "SELECT name,dimension FROM visual_space WHERE id=1"
                    ).fetchone()
                    identity = (space, embeddings.shape[1])
                    if existing and existing != identity:
                        raise ValueError(
                            "Visual embedding model changed; rebuild the visual index"
                        )
                    db.execute(
                        "INSERT OR REPLACE INTO visual_space VALUES (1,?,?)", identity
                    )
                db.executemany(
                    "DELETE FROM links WHERE chunk_id=?", ((key,) for key in chunk_ids)
                )
                for asset, vector in zip(assets, embeddings if assets else []):
                    db.execute(
                        "INSERT OR REPLACE INTO images VALUES (?,?,?,?,?)",
                        (
                            asset["id"],
                            asset["doc_id"],
                            asset["drawing_id"],
                            asset["digest"],
                            vector.astype("<f4").tobytes(),
                        ),
                    )
                db.executemany("INSERT OR IGNORE INTO links VALUES (?,?)", links)
                db.execute(
                    "DELETE FROM images WHERE id NOT IN (SELECT image_id FROM links)"
                )
                db.execute("UPDATE revision SET value=value+1 WHERE id=1")

        await _run_io(write)

    async def delete(self, ids):
        await self.replace_chunks(ids, [], [], None, [])

    async def clear(self):
        def write():
            with self._connect() as db:
                db.execute("DELETE FROM links")
                db.execute("DELETE FROM images")
                db.execute("DELETE FROM visual_space")
                db.execute("UPDATE revision SET value=value+1 WHERE id=1")

        await _run_io(write)

    async def search_vectors(self, vector, space, limit):
        query = normalized([vector])[0]

        def search():
            with self._connect() as db:
                if not db.execute("SELECT ready FROM readiness WHERE id=1").fetchone()[
                    0
                ]:
                    raise RuntimeError("Visual index requires a successful rebuild")
                identity = db.execute(
                    "SELECT name,dimension FROM visual_space WHERE id=1"
                ).fetchone()
                if identity is None:
                    return []
                if identity != (space, len(query)):
                    raise ValueError(
                        "Visual embedding model changed; rebuild the visual index"
                    )
                cursor = db.execute(
                    "SELECT id,doc_id,drawing_id,digest,vector FROM images ORDER BY id"
                )
                best = []
                while rows := cursor.fetchmany(512):
                    matrix = np.stack(
                        [np.frombuffer(row[4], dtype="<f4") for row in rows]
                    )
                    for row, score in zip(rows, matrix @ query):
                        item = (float(score), row[0], row[1], row[2], row[3])
                        if len(best) < limit:
                            heapq.heappush(best, item)
                        elif item > best[0]:
                            heapq.heapreplace(best, item)
                results = []
                for score, key, doc_id, drawing_id, digest in sorted(
                    best, reverse=True
                ):
                    chunks = [
                        row[0]
                        for row in db.execute(
                            "SELECT chunk_id FROM links WHERE image_id=? ORDER BY chunk_id",
                            (key,),
                        )
                    ]
                    results.append(
                        dict(
                            id=key,
                            full_doc_id=doc_id,
                            drawing_id=drawing_id,
                            digest=digest,
                            visual_score=score,
                            chunk_ids=chunks,
                        )
                    )
                return results

        return await _run_io(search)
