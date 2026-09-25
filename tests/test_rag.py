"""Tests for the SON retriever.

Split into two halves by the `integration` marker. Everything that can be checked
without an embedding model — chunk boundaries, citation ids, breadcrumbs, the
fingerprint — runs offline in milliseconds and is what `pytest` runs by default.
Anything needing real vectors is marked `integration` and additionally skips itself
when the model is not cached, so a fresh clone never silently blocks on a 130MB
download in the middle of a test run.

    pytest                  # offline unit tests only
    pytest -m integration   # the retrieval tests, needs the cached model
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.build_db import DEFAULT_SEED, build  # noqa: E402
from src import rag  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent

needs_model = pytest.mark.skipif(
    not rag.model_is_cached(),
    reason=f"embedding model not cached in {rag.model_cache_dir()}; run: python -m src.rag reindex",
)


@pytest.fixture(scope="module")
def conn(tmp_path_factory) -> sqlite3.Connection:
    connection = build(DEFAULT_SEED, tmp_path_factory.mktemp("rag") / "db.sqlite")
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def chunks(conn) -> list[rag.Chunk]:
    return rag.chunk_corpus(conn)


# ------------------------------------------------------------ chunking (offline)


def test_corpus_chunks_into_one_section_each(chunks):
    assert len(chunks) == 28
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert {c.son_id for c in chunks} == {"SON-001", "SON-002", "SON-003"}


def test_citation_ids_are_document_and_section(chunks):
    ids = {c.chunk_id for c in chunks}
    assert "SON-001#3.2" in ids
    assert "SON-002#5" in ids
    assert "SON-003#3.3" in ids
    for chunk in chunks:
        son_id, _, section = chunk.chunk_id.partition("#")
        assert son_id == chunk.son_id
        assert section == chunk.section


def test_stub_sections_are_dropped_but_survive_as_breadcrumb(chunks):
    """'## 3. Statement content rules' is a heading with no body of its own."""
    ids = {c.chunk_id for c in chunks}
    assert "SON-001#3" not in ids
    assert "SON-002#3" not in ids

    child = next(c for c in chunks if c.chunk_id == "SON-001#3.2")
    assert "3 Statement content rules" in child.breadcrumb
    assert child.breadcrumb.startswith("SON-001 ATM Statement Presentation v2.3")
    assert child.breadcrumb.endswith(child.heading)


def test_no_section_is_large_enough_to_need_splitting(chunks):
    """The claim that justifies having no splitter at all. If this fails, write one."""
    largest = max(chunks, key=lambda c: c.token_count)
    assert largest.token_count < 300, f"{largest.chunk_id} is {largest.token_count} tokens"


def test_chunk_text_is_verbatim_from_the_document(chunks):
    """Evidence quotes are grounded against this text, so it cannot be reformatted."""
    source = (PROJECT_ROOT / "data/sons/SON-001-atm-statement-presentation.md").read_text(
        encoding="utf-8"
    )
    chunk = next(c for c in chunks if c.chunk_id == "SON-001#3.2")
    assert chunk.heading == "3.2 Exclusion of non-settling authorisations"
    for line in chunk.text.splitlines():
        if line.strip():
            assert line in source


def test_embed_text_carries_the_breadcrumb(chunks):
    chunk = next(c for c in chunks if c.chunk_id == "SON-002#5")
    assert chunk.embed_text.startswith(chunk.breadcrumb)
    assert chunk.text in chunk.embed_text


def test_section_heading_pattern_handles_both_forms():
    assert rag.SECTION_RE.match("## 1. Scope").groups() == ("##", "1", "Scope")
    assert rag.SECTION_RE.match("### 3.2 Exclusion of x").groups() == ("###", "3.2", "Exclusion of x")
    assert rag.SECTION_RE.match("# SON-001 - Title") is None
    assert rag.SECTION_RE.match("Some body text") is None


def test_fingerprint_is_stable_and_content_sensitive(chunks):
    assert rag.corpus_fingerprint(chunks) == rag.corpus_fingerprint(chunks)

    edited = list(chunks)
    original = edited[0]
    edited[0] = rag.Chunk(
        chunk_id=original.chunk_id,
        son_id=original.son_id,
        section=original.section,
        heading=original.heading,
        breadcrumb=original.breadcrumb,
        text=original.text + " (amended)",
        token_count=original.token_count,
    )
    assert rag.corpus_fingerprint(edited) != rag.corpus_fingerprint(chunks)


def test_model_cache_dir_is_overridable(monkeypatch, tmp_path):
    monkeypatch.setenv("FINREQ_MODEL_CACHE", str(tmp_path))
    assert rag.model_cache_dir() == tmp_path


def test_importing_rag_does_not_load_the_model():
    """The unit suite must stay offline; a stray model load would make it slow and networked."""
    assert rag._model is None or rag.model_is_cached()


# -------------------------------------------------------- retrieval (integration)


@pytest.fixture(scope="module")
def indexed(conn) -> rag.Retriever:
    result = rag.index(conn, force=True)
    assert result["status"] == "rebuilt"
    return rag.Retriever(conn.execute("PRAGMA database_list").fetchone()[2])


@pytest.mark.integration
@needs_model
def test_dropped_authorizations_query_finds_the_exclusion_rule(indexed):
    """Ticket NWB-4471. US spelling against a document that says 'authorisations'."""
    hits = indexed.retrieve("dropped authorizations showing on ATM statement")
    assert [h.chunk_id for h in hits][:2].count("SON-001#3.2") == 1
    assert hits[0].section == "3.2"
    assert "MUST NOT appear as statement" in hits[0].text


@pytest.mark.integration
@needs_model
def test_senior_waiver_query_finds_the_out_of_scope_clause(indexed):
    """Ticket NWB-4488. The CR verdict depends on retrieving the sentence that says no."""
    hits = indexed.retrieve("senior citizen customers should not be charged the monthly fee")
    assert "SON-002#5" in [h.chunk_id for h in hits]


@pytest.mark.integration
@needs_model
def test_first_cycle_query_does_not_invent_a_rule(indexed):
    """Ticket NWB-4502. SON-003 is silent, so nothing may claim to answer it."""
    hits = indexed.retrieve("what period does the first statement of a new account cover")
    assert not any(h.chunk_id == "SON-003#3.1" and h.score > 0.85 for h in hits)


@pytest.mark.integration
@needs_model
def test_off_domain_query_returns_nothing(indexed):
    """The floor has to be able to say no, or CR and NEEDS_INFO become unreachable."""
    assert indexed.retrieve("how do I reset my online banking password") == []
    assert indexed.retrieve("recipe for banana bread") == []


@pytest.mark.integration
@needs_model
def test_client_filter_excludes_other_clients(indexed):
    assert indexed.retrieve("dropped authorisations", client_id="NWB")
    assert indexed.retrieve("dropped authorisations", client_id="SVB") == []


@pytest.mark.integration
@needs_model
def test_top_k_is_respected_and_capped(indexed):
    assert len(indexed.retrieve("statement", top_k=2, min_score=0.0)) == 2
    assert len(indexed.retrieve("statement", top_k=99, min_score=0.0)) == rag.MAX_TOP_K


@pytest.mark.integration
@needs_model
def test_scores_are_descending(indexed):
    hits = indexed.retrieve("fee waiver", top_k=8, min_score=0.0)
    assert [h.score for h in hits] == sorted([h.score for h in hits], reverse=True)


@pytest.mark.integration
@needs_model
def test_stored_vectors_round_trip_as_unit_vectors(conn, indexed):
    rows = conn.execute("SELECT chunk_id, embedding FROM son_chunks").fetchall()
    assert len(rows) == 28
    for row in rows:
        vector = np.frombuffer(row["embedding"], dtype=np.float32)
        assert vector.shape == (rag.EMBED_DIM,)
        assert np.isclose(np.linalg.norm(vector), 1.0, atol=1e-5)


@pytest.mark.integration
@needs_model
def test_reindex_is_idempotent(conn, indexed):
    assert rag.index(conn)["status"] == "unchanged"
    assert rag.index(conn, force=True)["status"] == "rebuilt"


@pytest.mark.integration
@needs_model
def test_a_changed_model_name_forces_a_rebuild(conn, indexed, monkeypatch):
    """Vectors from a different model score against a different geometry."""
    assert rag.index(conn)["status"] == "unchanged"
    conn.execute("UPDATE index_meta SET value = 'some/other-model' WHERE key = 'model'")
    assert rag.index(conn)["status"] == "rebuilt"


@pytest.mark.integration
@needs_model
def test_the_calibrated_floor_still_holds():
    """Guards the number in evals/retrieval_floor.md against silent drift."""
    note = (PROJECT_ROOT / "evals" / "retrieval_floor.md").read_text(encoding="utf-8")
    assert f"Chosen floor: {rag.MIN_SCORE:.2f}" in note
