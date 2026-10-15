"""LLM Provider Implementations for TrustTrip Phase 8 Research.

CRITICAL INVARIANT (Guardrail 4):
On production LLM failure or timeout, the system fails cleanly and safely:
Evidence collected -> LLM unavailable -> return structured evidence + citations + explicit notice.
Never fall back to a synthetic/test LLM provider in production to fake operational status.
"""
from __future__ import annotations

import os
from typing import Any
import httpx

from services.research.ports import (
    LLMProviderPort,
    ResearchProviderError,
    LLMProviderUnavailableError,
)


class GroqLLMProvider(LLMProviderPort):
    """Production LLM provider calling Groq API via native async HTTP."""

    BASE_URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GROQ_API_KEY", "")
        self.model = model or os.getenv("RESEARCH_GROQ_MODEL", "openai/gpt-oss-20b")

    async def synthesize(
        self,
        system_instruction: str,
        untrusted_evidence_prompt: str,
        call_timeout: float = 25.0,
    ) -> str:
        if not self.api_key:
            raise LLMProviderUnavailableError("GROQ_API_KEY is not configured.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        candidate_models = [self.model]
        for fb in ("openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"):
            if fb not in candidate_models:
                candidate_models.append(fb)

        import asyncio
        last_error_text = ""
        for mod in candidate_models:
            for attempt in range(2):
                payload = {
                    "model": mod,
                    "messages": [
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": untrusted_evidence_prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 4096,
                }

                try:
                    async with httpx.AsyncClient(timeout=call_timeout) as client:
                        response = await client.post(self.BASE_URL, headers=headers, json=payload)

                    if response.status_code == 429:
                        retry_after = 1.5
                        try:
                            ra_header = response.headers.get("retry-after")
                            if ra_header:
                                retry_after = max(1.0, min(5.0, float(ra_header)))
                        except Exception:
                            pass
                        last_error_text = f"Groq API rate limit (429) on model '{mod}'"
                        await asyncio.sleep(retry_after)
                        continue

                    if response.status_code in (400, 404) and "model" in response.text.lower():
                        last_error_text = response.text
                        break  # Try next candidate model

                    if not response.is_success:
                        last_error_text = f"Groq API returned HTTP {response.status_code}: {response.text}"
                        break  # Try next candidate model

                    data = response.json()
                    choices = data.get("choices") or []
                    if not choices:
                        last_error_text = "Groq returned empty choices."
                        break

                    content = (choices[0].get("message") or {}).get("content") or ""
                    if not content.strip():
                        last_error_text = f"Model '{mod}' returned empty content (reasoning exhausted tokens)."
                        break  # Try next candidate model

                    return content.strip()

                except httpx.TimeoutException as exc:
                    last_error_text = f"Groq API timed out after {call_timeout}s: {exc}"
                    break
                except httpx.RequestError as exc:
                    last_error_text = f"Groq network connection failed: {exc}"
                    break

        raise LLMProviderUnavailableError(f"Groq candidate models failed: {last_error_text}")


class DeterministicTestLLMProvider(LLMProviderPort):
    """Deterministic offline synthesizer used strictly for hermetic unit and integration testing."""

    __test__ = False

    def __init__(self, fixed_answer: str | None = None) -> None:
        self.fixed_answer = fixed_answer
        self.should_fail = False
        self.call_count = 0

    async def synthesize(
        self,
        system_instruction: str,
        untrusted_evidence_prompt: str,
        call_timeout: float = 20.0,
    ) -> str:
        self.call_count += 1
        if self.should_fail:
            raise LLMProviderUnavailableError("Simulated LLM provider outage.")

        if self.fixed_answer:
            return self.fixed_answer

        # Generate a structured answer with verified citations using canonical full-UUID cite_tokens
        import re
        tokens = re.findall(r'cite_token="([^"]+)"', untrusted_evidence_prompt)
        if not tokens:
            tokens = re.findall(r'evidence_id="([^"]+)"', untrusted_evidence_prompt)

        lines: list[str] = []
        if tokens:
            seen: set[str] = set()
            ordered_tokens = [t for t in tokens if not (t in seen or seen.add(t))]
            for token in ordered_tokens:
                snip_match = re.search(
                    rf'<untrusted_evidence_data [^>]*cite_token="{re.escape(token)}"[^>]*>\s*(.*?)\s*</untrusted_evidence_data>',
                    untrusted_evidence_prompt,
                    re.DOTALL,
                )
                if not snip_match:
                    snip_match = re.search(
                        rf'<untrusted_evidence_data [^>]*evidence_id="{re.escape(token)}"[^>]*>\s*(.*?)\s*</untrusted_evidence_data>',
                        untrusted_evidence_prompt,
                        re.DOTALL,
                    )
                if not snip_match:
                    snip_match = re.search(
                        rf'<item [^>]*cite_token="{re.escape(token)}"[^>]*>.*?<snippet>(.*?)</snippet>',
                        untrusted_evidence_prompt,
                        re.DOTALL,
                    )
                if snip_match:
                    s = snip_match.group(1).strip()
                    # Strip any inner XML tags if present
                    s = re.sub(r"<[^>]+>", " ", s).strip()
                    first_sent = s.split('.')[0].strip() if '.' in s else s[:80].strip()
                    lines.append(f"{first_sent} [cite:{token}].")
                else:
                    lines.append(f"Official policy and travel rules verified for reference item {token} [cite:{token}].")
        else:
            lines.append("According to official policy, cabin baggage allowance is 1 piece up to 23kg.")
        return "\n".join(lines)
