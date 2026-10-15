"""Tavily Research Provider Adapter implementing ResearchProviderPort.

Performs native asynchronous HTTP requests directly via httpx.AsyncClient.
Enforces bounded concurrency, timeouts, 429 rate limit handling, and clean cancellation.
"""
from __future__ import annotations

import asyncio
import os
import random
from typing import Any
import httpx

from domain.research import ResearchSource, SourceType
from services.research.ports import (
    ResearchProviderError,
    ResearchProviderPort,
    ResearchRateLimitError,
    ResearchTimeoutError,
)
from services.research.source_normalizer import (
    build_normalized_source,
    clean_web_snippet,
    is_community_or_video_domain,
    is_substantive_content,
)

_EXCLUDED_COMMUNITY_DOMAINS: list[str] = [
    "youtube.com", "youtu.be", "instagram.com", "tiktok.com",
    "pinterest.com", "facebook.com", "twitter.com", "x.com",
    "quora.com", "reddit.com",
]


class TavilyResearchProvider(ResearchProviderPort):
    """Production web research provider adapter using Tavily Search API."""

    BASE_URL = "https://api.tavily.com"

    def __init__(
        self,
        api_key: str | None = None,
        max_concurrent: int = 3,
        default_timeout: float = 10.0,
        timeout_seconds: float | None = None,
        max_retries: int = 2,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("TAVILY_API_KEY", "")
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self.default_timeout = timeout_seconds if timeout_seconds is not None else default_timeout
        self.timeout_seconds = self.default_timeout
        self.max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def search(
        self,
        query: str,
        max_results: int = 5,
        call_timeout: float = 10.0,
    ) -> list[ResearchSource]:
        """Perform native async web search via Tavily REST API."""
        if not self.api_key:
            raise ResearchProviderError("TAVILY_API_KEY is not configured.")

        timeout_sec = min(call_timeout, self.default_timeout)
        candidate_count = min(12, max(max_results * 2, 8))

        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "advanced",
            "include_answer": False,
            "include_raw_content": False,
            "max_results": candidate_count,
            "exclude_domains": _EXCLUDED_COMMUNITY_DOMAINS,
        }

        async with self._semaphore:
            attempt = 0
            while attempt <= self.max_retries:
                attempt += 1
                try:
                    client = await self._get_client()
                    response = await client.post(
                        f"{self.BASE_URL}/search",
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )

                    if response.status_code == 429:
                        if attempt <= self.max_retries:
                            await asyncio.sleep(0.1 * (2 ** attempt) + random.uniform(0.05, 0.1))
                            continue
                        raise ResearchRateLimitError("Tavily API rate limit exceeded (HTTP 429).")

                    if response.status_code >= 500:
                        if attempt <= self.max_retries:
                            await asyncio.sleep(0.1 * (2 ** attempt))
                            continue
                        raise ResearchProviderError(f"Tavily server error HTTP {response.status_code}.")

                    if not response.is_success:
                        raise ResearchProviderError(f"Tavily returned HTTP {response.status_code}: {response.text}")

                    data = response.json()
                    raw_results = data.get("results") or []
                    sources: list[ResearchSource] = []

                    seen_urls: set[str] = set()
                    for item in raw_results:
                        url = item.get("url") or ""
                        if not url or url in seen_urls:
                            continue
                        if is_community_or_video_domain(url):
                            continue
                        seen_urls.add(url)
                        title = item.get("title")
                        content = item.get("content") or ""
                        cleaned_snippet = clean_web_snippet(content)
                        # Require substantive content (Requirement 2 & 3)
                        if not is_substantive_content(cleaned_snippet, min_chars=100):
                            continue
                        source = build_normalized_source(
                            raw_url=url,
                            title=title,
                            snippet=cleaned_snippet[:2500],
                            source_type=SourceType.WEB_PAGE,
                        )
                        sources.append(source)
                        if len(sources) >= max_results:
                            break

                    return sources

                except asyncio.CancelledError:
                    # Clean cancellation, sockets closed by context manager
                    raise
                except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
                    if attempt <= self.max_retries:
                        await asyncio.sleep(0.1)
                        continue
                    raise ResearchTimeoutError(f"Tavily request timed out after {timeout_sec}s: {exc}") from exc
                except httpx.RequestError as exc:
                    raise ResearchProviderError(f"Tavily HTTP connection failed: {exc}") from exc

            return []


class TestResearchProvider(ResearchProviderPort):
    """Deterministic offline provider for hermetic testing."""

    __test__ = False

    def __init__(self, predefined_results: list[dict[str, Any]] | None = None) -> None:
        self.predefined_results = predefined_results or []
        self.call_count = 0
        self.should_fail = False
        self.should_rate_limit = False
        self.should_timeout = False

    async def search(
        self,
        query: str,
        max_results: int = 5,
        call_timeout: float = 10.0,
    ) -> list[ResearchSource]:
        self.call_count += 1
        if self.should_rate_limit:
            raise ResearchRateLimitError("Mock rate limit exceeded (HTTP 429).")
        if self.should_timeout:
            raise ResearchTimeoutError("Mock provider timeout.")
        if self.should_fail:
            raise ResearchProviderError("Mock provider error.")

        if self.predefined_results:
            sources: list[ResearchSource] = []
            for item in self.predefined_results[:max_results]:
                sources.append(
                    build_normalized_source(
                        raw_url=item.get("url", "https://example.com"),
                        title=item.get("title", "Test Title"),
                        snippet=item.get("content", "Test Content"),
                        source_type=SourceType.WEB_PAGE,
                    )
                )
            return sources

        # Default synthetic results
        return [
            build_normalized_source(
                raw_url="https://www.britishairways.com/baggage/policy",
                title="British Airways Baggage Policy",
                snippet="Economy cabin baggage allowance is 1 cabin bag (up to 23kg) plus 1 small personal item.",
                source_type=SourceType.AIRLINE_POLICY,
            ),
            build_normalized_source(
                raw_url="https://www.gov.uk/foreign-travel-advice/france",
                title="UK Foreign Office Travel Advisory: France",
                snippet="Passport must be valid for at least 3 months after departure from the Schengen area.",
                source_type=SourceType.GOVERNMENT_ADVISORY,
            ),
        ]
