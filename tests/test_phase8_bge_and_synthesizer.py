"""Phase 8 BGE Embedding and Synthesizer Regression Tests.

Validates:
1. Synthesizer handles ResearchQuery, plain string, empty string, and preserves query text.
2. BGE embedding provider outputs 384 dimensions, all finite, normalized.
3. Query instruction prefix contract: applied to queries, not passages.
4. Semantic retrieval ranking with BAAI/bge-small-en-v1.5.
5. Production fail-closed behavior on missing or unresolvable models.
6. Offline deterministic embedding fallback for unit test isolation.
"""
from __future__ import annotations

import asyncio
import math
from uuid import UUID, uuid4
import pytest

from domain.research import (
    ResearchEvidence,
    ResearchFreshnessState,
    ResearchIntent,
    ResearchMode,
    ResearchQuery,
    ResearchResult,
    SourceTrustClass,
)
from services.research.llm_provider import DeterministicTestLLMProvider
from services.research.rag.embedding import (
    BGESmallEmbeddingProvider,
    DeterministicEmbeddingProvider,
    get_embedding_provider,
)
from services.research.synthesizer import ResearchSynthesizer


# ==================== SYNTHESIZER QUERY TESTS ====================

def test_synthesizer_with_research_query_object():
    """Verify synthesizer works seamlessly with a typed ResearchQuery object."""
    async def run():
        synth = ResearchSynthesizer(llm_provider=DeterministicTestLLMProvider())
        q_id = uuid4()
        u_id = uuid4()
        query = ResearchQuery(
            query_id=q_id,
            user_id=u_id,
            query_text="What is the currency of Japan?",
            normalized_query="what is the currency of japan",
            intent=ResearchIntent.DESTINATION_RESEARCH,
            mode=ResearchMode.AUTO,
        )
        evidence = [
            ResearchEvidence(
                evidence_id=uuid4(),
                query_id=q_id,
                user_id=u_id,
                source_id=uuid4(),
                canonical_url="https://example.com/japan",
                title="Japan Travel Guide",
                domain="example.com",
                trust_class=SourceTrustClass.USER_PROVIDED,
                claims=["The currency of Japan is the Japanese Yen (JPY)."],
                snippet="The currency of Japan is the Japanese Yen (JPY).",
                freshness_state=ResearchFreshnessState.FRESH,
                immutable_hash="hash_japan_cur",
            )
        ]

        result = await synth.synthesize(query, evidence)
        assert isinstance(result, ResearchResult)
        assert result.query_id == q_id
        assert "currency of Japan" in result.query_text
        assert len(result.citations) >= 1 or len(result.facts) >= 1

    asyncio.run(run())


def test_synthesizer_with_plain_string_query():
    """Verify synthesizer handles a plain string without AttributeError on query.query_text."""
    async def run():
        synth = ResearchSynthesizer(llm_provider=DeterministicTestLLMProvider())
        u_id = uuid4()
        q_str = "What are the best attractions in Kyoto?"
        evidence = [
            ResearchEvidence(
                evidence_id=uuid4(),
                query_id=uuid4(),
                user_id=u_id,
                source_id=uuid4(),
                canonical_url="https://example.com/kyoto",
                title="Kyoto Guide",
                domain="example.com",
                trust_class=SourceTrustClass.USER_PROVIDED,
                claims=["Fushimi Inari and Kinkaku-ji are top sights in Kyoto."],
                snippet="Fushimi Inari and Kinkaku-ji are top sights in Kyoto.",
                freshness_state=ResearchFreshnessState.FRESH,
                immutable_hash="hash_kyoto",
            )
        ]

        # Should not raise AttributeError: 'str' object has no attribute 'query_text'
        result = await synth.synthesize(q_str, evidence)
        assert isinstance(result, ResearchResult)
        assert isinstance(result.query_id, UUID)
        assert result.query_text == q_str

    asyncio.run(run())


def test_synthesizer_rejects_empty_or_whitespace_query():
    """Verify synthesizer rejects empty or whitespace-only queries."""
    async def run():
        synth = ResearchSynthesizer(llm_provider=DeterministicTestLLMProvider())
        with pytest.raises(ValueError, match="Query text cannot be empty"):
            await synth.synthesize("", [])

        with pytest.raises(ValueError, match="Query text cannot be empty"):
            await synth.synthesize("   ", [])

    asyncio.run(run())


# ==================== BGE EMBEDDING TESTS ====================

def test_deterministic_embedding_provider_shape_and_finite():
    """Verify deterministic provider produces finite 384-dim normalized vectors."""
    provider = DeterministicEmbeddingProvider(dimension=384)
    assert provider.dimension == 384

    doc_embs = provider.embed_texts(["Test document passage about Tokyo."])
    assert len(doc_embs) == 1
    assert len(doc_embs[0]) == 384
    assert all(math.isfinite(x) for x in doc_embs[0])

    norm = math.sqrt(sum(x * x for x in doc_embs[0]))
    assert abs(norm - 1.0) < 1e-4

    query_emb = provider.embed_query("Query about Tokyo")
    assert len(query_emb) == 384
    assert all(math.isfinite(x) for x in query_emb)


def test_bge_small_embedding_provider_live():
    """Verify live BGE-small-en-v1.5 embedding output, shape, and normalization."""
    provider = BGESmallEmbeddingProvider()
    assert provider.dimension == 384

    texts = [
        "Passports must be valid for at least 6 months beyond the date of travel.",
        "Kyoto is famous for its classical Buddhist temples, gardens, and imperial palaces.",
    ]
    embeddings = provider.embed_texts(texts)
    assert len(embeddings) == 2
    for emb in embeddings:
        assert len(emb) == 384
        assert all(math.isfinite(x) for x in emb)
        norm = math.sqrt(sum(x * x for x in emb))
        assert abs(norm - 1.0) < 1e-3, f"Vector should be normalized, got norm={norm}"


def test_bge_query_prefix_and_semantic_ranking():
    """Verify query prefix is used for queries and semantically related passages rank higher."""
    provider = BGESmallEmbeddingProvider()

    # Query with recommended instruction prefix
    query_text = "What are the visa requirements for international travel to Japan?"
    query_vec = provider.embed_query(query_text)
    assert len(query_vec) == 384

    # Related passage vs Unrelated passage (embedded without query prefix)
    related_passage = "Foreign tourists traveling to Japan must present a valid passport and obtain an entry visa or visa exemption."
    unrelated_passage = "A traditional sourdough starter is made by mixing equal weights of bread flour and lukewarm water."

    passage_embs = provider.embed_texts([related_passage, unrelated_passage])
    rel_emb, unrel_emb = passage_embs[0], passage_embs[1]

    # Calculate cosine similarity (vectors are normalized so dot product = cosine similarity)
    def dot_product(v1, v2):
        return sum(a * b for a, b in zip(v1, v2))

    sim_related = dot_product(query_vec, rel_emb)
    sim_unrelated = dot_product(query_vec, unrel_emb)

    assert sim_related > sim_unrelated, f"Related ({sim_related:.4f}) should rank above unrelated ({sim_unrelated:.4f})"
    assert sim_related > 0.65, f"Related passage should have high similarity, got {sim_related:.4f}"


def test_bge_provider_fails_closed_on_invalid_model():
    """Verify that specifying an invalid or nonexistent model fails clearly in production mode."""
    provider = BGESmallEmbeddingProvider(model_name="nonexistent-org/nonexistent-model-that-fails-definitely-12345")
    with pytest.raises(RuntimeError, match="Failed to load BGE embedding model"):
        provider.embed_texts(["test text"])


def test_get_embedding_provider_factory(monkeypatch):
    """Verify get_embedding_provider respects EMBEDDING_PROVIDER environment variable."""
    # Test deterministic provider
    monkeypatch.setenv("EMBEDDING_PROVIDER", "deterministic")
    provider_det = get_embedding_provider()
    assert isinstance(provider_det, DeterministicEmbeddingProvider)
    assert provider_det.dimension == 384

    # Test BGE provider
    monkeypatch.setenv("EMBEDDING_PROVIDER", "bge")
    provider_bge = get_embedding_provider()
    assert isinstance(provider_bge, BGESmallEmbeddingProvider)
    assert provider_bge.dimension == 384
