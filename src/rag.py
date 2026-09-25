"""Retrieval over the Statement of Need documents.

One chunk per numbered section. The corpus is three documents, about 2,200 tokens
and 28 chunks in total, which drives most of the decisions here: no splitter (no
section comes close to a size worth splitting), no vector database (a dot product
against a 28x384 matrix is exact and instant), and no approximate index.

Three things in here are less obvious than they look.

* **The query prefix is applied by hand.** BGE models are trained with an
  instruction on the query side only. `fastembed.TextEmbedding.query_embed` looks
  like it handles that, but for `bge-small-en-v1.5` in fastembed 0.8 it returns a
  vector identical to `embed` — the prefix is never applied. Relying on it would
  have cost recall silently, with nothing failing.

* **Retrieval can return nothing.** `min_score` is a real filter, not decoration.
  The agent's CR and NEEDS_INFO verdicts rest on a section being *absent* from the
  spec, so a retriever that always returns its four least-bad chunks would make
  those verdicts unreachable.

* **The floor is calibrated, not guessed.** BGE cosines sit in a narrow high band —
  two completely unrelated queries score about 0.60 against each other — so an
  intuitive threshold like 0.3 admits everything. See `evals/retrieval_floor.md`
  for the measured separation and how the constant below was chosen.

Usage:
    python -m src.rag reindex [--force]
    python -m src.rag search "dropped authorisations on ATM statements"
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "db.sqlite"

MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

DEFAULT_TOP_K = 4
MAX_TOP_K = 8

# Calibrated on 12 relevant and 12 irrelevant queries; see evals/retrieval_floor.md.
# The relevant and irrelevant score bands overlap, so this is a trade rather than a
# clean split: it is set at the highest-recall point that admits no off-topic chunk.
MIN_SCORE = 0.63

# A heading with no body of its own ("## 3. Fee rules") is not worth retrieving. It
# still becomes breadcrumb context for its children.
STUB_TOKEN_FLOOR = 15

SECTION_RE = re.compile(r"^(#{2,3})\s+(\d+(?:\.\d+)*)\.?\s+(.+?)\s*$")
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


# ------------------------------------------------------------------ model cache


def model_cache_dir() -> Path:
    """Where the ONNX model lives. Outside the repo: it is a 130MB build artifact."""
    override = os.environ.get("FINREQ_MODEL_CACHE")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "fastembed_cache"
    return Path.home() / ".cache" / "fastembed"


def model_is_cached() -> bool:
    """True when the model has been downloaded.

    Tests that need real vectors check this and skip rather than reaching for the
    network, so the default suite stays offline and fast.
    """
    cache = model_cache_dir()
    return cache.is_dir() and any(cache.glob("**/*.onnx"))


_model = None


def _get_model():
    """Load the embedding model once, lazily.

    Import of this module must stay free of model loading: the chunking tests and the
    slice 1 suite run without ever touching ONNX.
    """
    global _model
    if _model is None:
        from fastembed import TextEmbedding  # imported late, it is slow

        _model = TextEmbedding(MODEL_NAME, cache_dir=str(model_cache_dir()))
    return _model


def embed_documents(texts: Sequence[str]) -> np.ndarray:
    """Embed passages. No prefix: BGE wants the instruction on the query side only."""
    vectors = np.array(list(_get_model().embed(list(texts))), dtype=np.float32)
    return _normalise(vectors)


def embed_query(query: str, *, use_prefix: bool = True) -> np.ndarray:
    """Embed one query, applying the BGE instruction prefix by hand.

    `use_prefix` exists so the calibration script can measure what the prefix is
    actually worth on this corpus rather than taking the model card's word for it.
    """
    text = (BGE_QUERY_PREFIX + query) if use_prefix else query
    vector = np.array(list(_get_model().embed([text]))[0], dtype=np.float32)
    return _normalise(vector.reshape(1, -1))[0]


def _normalise(vectors: np.ndarray) -> np.ndarray:
    """L2 normalise so cosine similarity is a plain dot product.

    fastembed already returns unit vectors, but normalising here means the stored
    blobs are unit vectors by construction rather than by the current library's
    convenience.
    """
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1.0, norms)


# ---------------------------------------------------------------------- chunking


@dataclass(frozen=True)
class Chunk:
    chunk_id: str      # 'SON-001#3.2'
    son_id: str
    section: str       # '3.2'
    heading: str       # '3.2 Exclusion of non-settling authorisations'
    breadcrumb: str    # 'SON-001 ATM Statement Presentation v2.3 > 3. Statement content rules > ...'
    text: str          # section body, verbatim
    token_count: int

    @property
    def embed_text(self) -> str:
        """What actually gets embedded.

        The breadcrumb is prepended so a rule section carries its document identity
        into the vector. Without it, SON-001 3.2 embeds as free-floating prose with
        no indication of which spec, or even which subject area, it governs.
        """
        return f"{self.breadcrumb}\n\n{self.text}"


def estimate_tokens(text: str) -> int:
    """Rough token count. Good enough to spot a stub or an oversized section."""
    return int(len(text.split()) * 1.3)


def chunk_document(son_id: str, title: str, version: str, markdown: str) -> list[Chunk]:
    """Split one SON into one chunk per numbered section.

    Sections are the document's own boundaries, which is what makes 'SON-001#3.2' a
    citation that survives re-chunking. Stub headings are dropped as chunks and kept
    as breadcrumb context for the sections beneath them.
    """
    body = FRONTMATTER_RE.sub("", markdown)
    doc_label = f"{son_id} {title} v{version}"

    chunks: list[Chunk] = []
    ancestors: list[tuple[int, str]] = []  # (heading level, 'N. Title')
    current: tuple[int, str, str] | None = None  # (level, section, heading)
    buffer: list[str] = []

    def flush() -> None:
        if current is None:
            return
        level, section, heading = current
        text = "\n".join(buffer).strip()
        token_count = estimate_tokens(f"{heading} {text}")
        if token_count < STUB_TOKEN_FLOOR:
            return  # a stub; it lives on in the breadcrumb only
        trail = [label for depth, label in ancestors if depth < level]
        breadcrumb = " > ".join([doc_label, *trail, heading])
        chunks.append(
            Chunk(
                chunk_id=f"{son_id}#{section}",
                son_id=son_id,
                section=section,
                heading=heading,
                breadcrumb=breadcrumb,
                text=text,
                token_count=token_count,
            )
        )

    for line in body.splitlines():
        match = SECTION_RE.match(line)
        if not match:
            if current is not None:
                buffer.append(line)
            continue

        flush()
        hashes, number, title_text = match.groups()
        level = len(hashes)
        heading = f"{number} {title_text}"
        ancestors = [(depth, label) for depth, label in ancestors if depth < level]
        ancestors.append((level, heading))
        current = (level, number, heading)
        buffer = []

    flush()
    return chunks


def chunk_corpus(conn: sqlite3.Connection, root: Path = PROJECT_ROOT) -> list[Chunk]:
    """Chunk every SON registered in the database, in son_id order."""
    chunks: list[Chunk] = []
    rows = conn.execute("SELECT son_id, title, version, path FROM son_docs ORDER BY son_id")
    for son_id, title, version, path in rows.fetchall():
        markdown = (root / path).read_text(encoding="utf-8")
        chunks.extend(chunk_document(son_id, title, version, markdown))
    return chunks


def corpus_fingerprint(chunks: Sequence[Chunk]) -> str:
    """Hash of the chunked corpus. Lets a reindex skip work when nothing changed."""
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk.chunk_id.encode())
        digest.update(chunk.embed_text.encode())
    return digest.hexdigest()


# ----------------------------------------------------------------------- indexing


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM index_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO index_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def index(conn: sqlite3.Connection, *, force: bool = False, root: Path = PROJECT_ROOT) -> dict:
    """Chunk, embed and store the corpus. Idempotent.

    Re-running with unchanged documents and the same model does nothing. A changed
    document, or a changed model, forces a rebuild — stale vectors from a different
    model would silently score against a different geometry.
    """
    chunks = chunk_corpus(conn, root)
    fingerprint = corpus_fingerprint(chunks)

    stored_fingerprint = _meta_get(conn, "corpus_sha")
    stored_model = _meta_get(conn, "model")
    stored_count = conn.execute("SELECT COUNT(*) FROM son_chunks").fetchone()[0]

    unchanged = (
        not force
        and stored_fingerprint == fingerprint
        and stored_model == MODEL_NAME
        and stored_count == len(chunks)
    )
    if unchanged:
        return {"status": "unchanged", "chunks": len(chunks), "model": MODEL_NAME}

    vectors = embed_documents([chunk.embed_text for chunk in chunks])
    if vectors.shape != (len(chunks), EMBED_DIM):
        raise RuntimeError(f"unexpected embedding shape {vectors.shape}")

    conn.execute("DELETE FROM son_chunks")
    conn.executemany(
        "INSERT INTO son_chunks "
        "(chunk_id, son_id, section, heading, breadcrumb, text, token_count, embedding) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                chunk.chunk_id,
                chunk.son_id,
                chunk.section,
                chunk.heading,
                chunk.breadcrumb,
                chunk.text,
                chunk.token_count,
                vector.astype(np.float32).tobytes(),
            )
            for chunk, vector in zip(chunks, vectors)
        ],
    )
    _meta_set(conn, "corpus_sha", fingerprint)
    _meta_set(conn, "model", MODEL_NAME)
    _meta_set(conn, "dim", str(EMBED_DIM))
    conn.commit()

    return {"status": "rebuilt", "chunks": len(chunks), "model": MODEL_NAME}


# ---------------------------------------------------------------------- retrieval


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    son_id: str
    title: str
    version: str
    section: str
    heading: str
    text: str
    score: float


class Retriever:
    """Cosine search over the stored chunk vectors.

    Vectors are loaded once into a single matrix. At 28 chunks the whole index is
    about 43KB, so there is nothing to be gained from an approximate structure and
    something to lose: the exact score is what the floor is applied to.
    """

    def __init__(self, db_path: Path | str = DEFAULT_DB, *, min_score: float = MIN_SCORE):
        self.db_path = Path(db_path)
        self.min_score = min_score
        self._rows: list[sqlite3.Row] | None = None
        self._matrix: np.ndarray | None = None

    def _load(self) -> tuple[list[sqlite3.Row], np.ndarray]:
        if self._rows is None or self._matrix is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT c.chunk_id, c.son_id, c.section, c.heading, c.text, c.embedding,
                           d.title, d.version, d.client_id
                    FROM son_chunks c
                    JOIN son_docs d ON d.son_id = c.son_id
                    ORDER BY c.chunk_id
                    """
                ).fetchall()
            finally:
                conn.close()

            if not rows:
                raise RuntimeError("no chunks indexed; run: python -m src.rag reindex")

            matrix = np.vstack(
                [np.frombuffer(row["embedding"], dtype=np.float32) for row in rows]
            )
            if matrix.shape[1] != EMBED_DIM:
                raise RuntimeError(f"stored vectors have dim {matrix.shape[1]}, expected {EMBED_DIM}")

            self._rows, self._matrix = rows, matrix
        return self._rows, self._matrix

    def retrieve(
        self,
        query: str,
        *,
        client_id: str | None = None,
        top_k: int = DEFAULT_TOP_K,
        min_score: float | None = None,
    ) -> list[RetrievedChunk]:
        """Return the best-matching sections, or an empty list if none clears the floor."""
        top_k = max(1, min(top_k, MAX_TOP_K))
        floor = self.min_score if min_score is None else min_score

        rows, matrix = self._load()
        scores = matrix @ embed_query(query)

        order = np.argsort(-scores)
        results: list[RetrievedChunk] = []
        for i in order:
            row = rows[i]
            if client_id is not None and row["client_id"] != client_id:
                continue
            score = float(scores[i])
            if score < floor:
                break  # sorted, so everything after this is worse
            results.append(
                RetrievedChunk(
                    chunk_id=row["chunk_id"],
                    son_id=row["son_id"],
                    title=row["title"],
                    version=row["version"],
                    section=row["section"],
                    heading=row["heading"],
                    text=row["text"],
                    score=round(score, 4),
                )
            )
            if len(results) == top_k:
                break
        return results


# ----------------------------------------------------------------------- the CLI


def _cmd_reindex(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(args.db)
    try:
        result = index(conn, force=args.force)
    finally:
        conn.close()
    print(f"{result['status']}: {result['chunks']} chunks, model {result['model']}")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    retriever = Retriever(args.db, min_score=args.min_score)
    hits = retriever.retrieve(args.query, top_k=args.top_k, client_id=args.client)
    if not hits:
        print(f"no section scored above {args.min_score}")
        return 0
    for hit in hits:
        print(f"{hit.score:.4f}  {hit.chunk_id:<14} {hit.heading}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retrieval over the SON corpus.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)

    reindex = sub.add_parser("reindex", help="chunk and embed the corpus")
    reindex.add_argument("--force", action="store_true", help="rebuild even if unchanged")
    reindex.set_defaults(func=_cmd_reindex)

    search = sub.add_parser("search", help="query the index")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    search.add_argument("--min-score", type=float, default=MIN_SCORE)
    search.add_argument("--client", default=None)
    search.set_defaults(func=_cmd_search)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
