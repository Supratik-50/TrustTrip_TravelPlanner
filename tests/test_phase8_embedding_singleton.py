"""Unit tests verifying singleton lifecycle, concurrency safety, lazy loading, and error recovery for BGE-small embedding provider."""
import asyncio
import threading
from unittest.mock import MagicMock, patch
from uuid import uuid4
import numpy as np
import pytest

from services.research.rag.embedding import (
    BGESmallEmbeddingProvider,
    get_embedding_provider,
    reset_embedding_provider_for_testing,
)
from domain.research import ResearchMode


@pytest.fixture(autouse=True)
def clean_embedding_singleton():
    """Ensure each test starts with a clean singleton state."""
    reset_embedding_provider_for_testing()
    yield
    reset_embedding_provider_for_testing()


def test_embedding_provider_is_singleton_across_repeated_calls():
    """Test that repeated calls to get_embedding_provider reuse the same in-memory instance."""
    with patch("sentence_transformers.SentenceTransformer") as mock_st:
        mock_instance = MagicMock()
        mock_instance.encode.return_value = np.array([[0.1] * 384])
        mock_st.return_value = mock_instance

        p1 = get_embedding_provider()
        p2 = get_embedding_provider()
        p3 = get_embedding_provider()
        p4 = get_embedding_provider()

        assert p1 is p2
        assert p2 is p3
        assert p3 is p4

        # Model is loaded lazily on first embedding operation
        p1.embed_query("test query")
        p2.embed_query("another query")
        p3.embed_texts(["doc passage"])
        p4.embed_texts(["second passage"])

        assert mock_st.call_count == 1


def test_concurrent_first_initialization_is_thread_safe():
    """Test that concurrent initializations from multiple threads create exactly one model instance."""
    with patch("sentence_transformers.SentenceTransformer") as mock_st:
        mock_instance = MagicMock()
        mock_instance.encode.return_value = np.array([[0.1] * 384])
        mock_st.return_value = mock_instance

        providers = [None] * 10
        exceptions = [None] * 10

        def worker(idx):
            try:
                p = get_embedding_provider()
                # Trigger lazy model load concurrently
                p.embed_query(f"thread query {idx}")
                providers[idx] = p
            except Exception as e:
                exceptions[idx] = e

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for exc in exceptions:
            assert exc is None, f"Thread raised unexpected exception: {exc}"

        first = providers[0]
        for p in providers:
            assert p is first, "All concurrent threads must receive the identical singleton instance"

        assert mock_st.call_count == 1, "SentenceTransformer must be instantiated exactly once across concurrent threads"


def test_failed_initialization_permits_clean_retry():
    """Test that if model initialization fails, the failure state is cleared so subsequent calls can retry."""
    with patch("sentence_transformers.SentenceTransformer") as mock_st:
        # First attempt fails
        mock_st.side_effect = RuntimeError("Simulated network timeout downloading weights")

        p = get_embedding_provider()
        with pytest.raises(RuntimeError, match="Simulated network timeout"):
            p.embed_query("test query")

        # Second attempt succeeds
        mock_instance = MagicMock()
        mock_instance.encode.return_value = np.array([[0.1] * 384])
        mock_st.side_effect = None
        mock_st.return_value = mock_instance

        # Must not be poisoned by previous failure
        res = p.embed_query("test query")
        assert len(res) == 384
        assert mock_st.call_count == 2


def test_web_only_research_does_not_initialize_embedding_model():
    """Test that executing Web Research does not unnecessarily trigger embedding model initialization."""
    async def _run():
        from services.research.orchestrator import ResearchOrchestrator
        from services.research.ports import ResearchProviderPort
        from services.research.llm_provider import DeterministicTestLLMProvider
        from domain.research import ResearchSource, SourceTrustClass
        from datetime import datetime, timezone

        mock_web = MagicMock(spec=ResearchProviderPort)
        test_src = ResearchSource(
            source_id=uuid4(),
            canonical_url="https://example.com/guide",
            title="Example Guide",
            domain="example.com",
            trust_class=SourceTrustClass.TRUSTED_SECONDARY,
            retrieved_at=datetime.now(timezone.utc),
            metadata={"snippet": "North Goa has bustling beaches and nightlife."},
        )

        async def async_search(*args, **kwargs):
            return [test_src]
        mock_web.search = async_search

        mock_llm = DeterministicTestLLMProvider(fixed_answer="North Goa is lively. [cite:TOKEN]")

        with patch("sentence_transformers.SentenceTransformer") as mock_st:
            orch = ResearchOrchestrator(
                web_provider=mock_web,
                llm_provider=mock_llm,
                research_store=MagicMock(),
            )
            # Execute WEB research
            result = await orch.execute_research(
                user_id=uuid4(),
                query_text="What are the differences between North and South Goa?",
                mode=ResearchMode.WEB,
            )
            assert result.mode == ResearchMode.WEB
            assert mock_st.call_count == 0, "SentenceTransformer must NOT be instantiated for web-only research"

    asyncio.run(_run())
