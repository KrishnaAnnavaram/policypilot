import pytest

from policypilot.rag.embeddings import HashingEmbedder
from policypilot.rag.index import HybridIndex, merge_hits
from policypilot.rag.text import chunk_text, detect_language, tokenize


def test_chunks_respect_size_and_keep_headings():
    text = "# Title\n\n## Deductibles\n\n" + " ".join(f"Sentence number {i} is here." for i in range(60))
    chunks = chunk_text(text, size=200, overlap=40)
    assert all(len(c) <= 200 for c in chunks)
    assert chunks[0].startswith("Deductibles.")
    assert len(chunks) > 5


def test_chunking_rejects_bad_parameters():
    with pytest.raises(ValueError):
        chunk_text("x", size=100, overlap=100)


def test_cjk_tokenization_and_language_detection():
    assert "保险" in tokenize("重复保险如何赔偿")
    assert detect_language("重复保险如何赔偿？") == "zh"
    assert detect_language("What is covered?") == "en"


def test_index_is_incremental_and_deduplicated():
    index = HybridIndex()
    assert index.add_document("The grace period is 15 days.", "a.md") == 1
    assert index.add_document("The grace period is 15 days.", "a-copy.md") == 0    # same content
    assert index.add_document("Theft must be reported within 48 hours.", "b.md") == 1
    assert len(index) == 2 and index.sources == ["a.md", "b.md"]
    assert index.search("When must theft be reported?", k=1)[0].chunk.source == "b.md"


def test_demo_index_retrieval(demo_index):
    hits = demo_index.search("What is the standard deductible?", k=3)
    assert hits[0].chunk.source == "auto_policy_coverage.md"
    zh = demo_index.search("重复保险如何赔偿？", k=1)
    assert zh[0].chunk.source == "faq_zh.md"


def test_merge_hits_prefers_best_scores():
    a, b = HybridIndex(), HybridIndex()
    a.add_document("Collision coverage pays for crash repairs.", "shared.md")
    b.add_document("My uploaded policy covers crash repairs fully.", "upload.pdf")
    merged = merge_hits([a.search("crash repairs", 2), b.search("crash repairs", 2)], k=2)
    assert {h.chunk.source for h in merged} == {"shared.md", "upload.pdf"}
    assert [h.rank for h in merged] == [1, 2]


def test_hashing_embedder_is_normalized_and_deterministic():
    emb = HashingEmbedder(dim=64)
    v1, v2 = emb.embed(["deductible amount"]), emb.embed(["deductible amount"])
    assert v1 == v2
    assert abs(sum(x * x for x in v1[0]) - 1.0) < 1e-9
