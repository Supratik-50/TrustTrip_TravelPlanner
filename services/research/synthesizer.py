"""Citation-Aware Research Synthesizer for TrustTrip Phase 8.

Coordinates evidence packaging, prompt-injection isolation, LLM invocation,
claim-level citation extraction, conflict isolation, and coverage validation.
"""
from __future__ import annotations

import re
from enum import Enum
from uuid import uuid4
from domain.research import (
    EvidenceStatus,
    ResearchCitation,
    ResearchConflict,
    ResearchEvidence,
    ResearchFact,
)
from services.research.ports import LLMProviderPort
from services.research.security import (
    SYSTEM_SYNTHESIZER_PROMPT,
    build_secure_evidence_envelope,
    sanitize_for_display,
)


class ClaimVerificationOutcome(str, Enum):
    SUPPORTED = "SUPPORTED"
    INSUFFICIENT = "INSUFFICIENT"
    CONTRADICTED = "CONTRADICTED"


_SYN_GROUPS = [
    {"allowed", "permitted", "accepted", "free", "authorized", "valid", "allow", "permit"},
    {"prohibited", "banned", "not allowed", "forbidden", "disallowed", "restricted", "prohibit"},
    {"baggage", "luggage", "bags", "suitcase", "carry-on", "cabin bag", "checked bag", "cabin"},
    {"fee", "charge", "cost", "price", "penalty"},
    {"mandatory", "required", "compulsory", "necessary", "obligation"},
    {"exempt", "waived", "free", "not required", "optional"},
    {"curfew", "night ban", "operating restrictions", "closure", "closed at night"},
    {"visa", "entry permit", "travel authorization", "eta", "esta"},
]

SYNONYM_MAP: dict[str, set[str]] = {}
for _group in _SYN_GROUPS:
    for _item in _group:
        SYNONYM_MAP[_item] = _group

CONTRADICTION_PAIRS: list[tuple[set[str], set[str]]] = [
    ({"curfew", "night ban", "closed at night"}, {"operates after", "operates 24/7", "open 24 hours", "24-hour", "no curfew", "flights after"}),
    ({"prohibited", "banned", "not allowed"}, {"allowed", "permitted", "welcome", "free"}),
    ({"mandatory", "required"}, {"optional", "exempt", "not required", "voluntary"}),
    ({"direct", "non-stop"}, {"stopover", "layover", "connection", "connecting"}),
    ({"free", "no fee"}, {"fee", "charge", "costs", "priced at"}),
]


def verify_claim_support(sentence: str, evidence: ResearchEvidence) -> tuple[ClaimVerificationOutcome, str]:
    """Verify whether a claim sentence is factually supported by cited evidence.

    Returns (SUPPORTED | INSUFFICIENT | CONTRADICTED, reason).
    """
    clean_sent = re.sub(r"\[cite:[a-zA-Z0-9_\-]+\]", "", sentence).strip()
    evidence_text = f"{evidence.title} {evidence.snippet} {' '.join(evidence.claims)}".lower()
    clean_lower = clean_sent.lower()

    # 1. Contradiction detection
    for set_a, set_b in CONTRADICTION_PAIRS:
        claim_has_a = any(t in clean_lower for t in set_a)
        ev_has_b = any(t in evidence_text for t in set_b)
        if claim_has_a and ev_has_b:
            return ClaimVerificationOutcome.CONTRADICTED, f"Contradiction detected: claim contains {set_a} while evidence contains {set_b}"
        claim_has_b = any(t in clean_lower for t in set_b)
        ev_has_a = any(t in evidence_text for t in set_a)
        if claim_has_b and ev_has_a:
            return ClaimVerificationOutcome.CONTRADICTED, f"Contradiction detected: claim contains {set_b} while evidence contains {set_a}"

    # 2. Numerical fact check
    claim_numbers = re.findall(r"\b(?:\d{1,2}:\d{2}|\d+(?:\.\d+)?(?:kg|lbs|h|m|am|pm|days?|hours?|%|usd|inr|eur|gbp)?)\b", clean_lower)
    significant_nums = [n for n in claim_numbers if not (n.isdigit() and len(n) == 1 and n in ["1", "2"])]
    for num in significant_nums:
        if num not in evidence_text:
            return ClaimVerificationOutcome.INSUFFICIENT, f"Numerical assertion '{num}' not found in cited evidence"

    # 3. Date fact check
    date_matches = re.findall(r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}\b", clean_lower)
    for d in date_matches:
        if d not in evidence_text:
            return ClaimVerificationOutcome.INSUFFICIENT, f"Date assertion '{d}' not found in cited evidence"

    # 4. Informative predicate & relationship check
    stop_words = {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "with",
        "by", "about", "against", "between", "into", "through", "during", "before",
        "after", "above", "below", "from", "up", "down", "is", "are", "was", "were",
        "be", "been", "being", "have", "has", "had", "do", "does", "did", "can",
        "could", "should", "would", "may", "might", "must", "it", "its", "they",
        "their", "them", "this", "that", "these", "those", "some", "any", "all",
    }
    words = [w.strip(".,;:?!'\"()[]") for w in clean_lower.split() if w.strip(".,;:?!'\"()[]")]
    informative_words = [w for w in words if len(w) > 2 and w not in stop_words]

    if not informative_words:
        return ClaimVerificationOutcome.SUPPORTED, "General non-factual statement"

    matched = 0
    for w in informative_words:
        if w in evidence_text:
            matched += 1
        else:
            syns = SYNONYM_MAP.get(w, set())
            if any(syn in evidence_text for syn in syns):
                matched += 1

    ratio = matched / len(informative_words)
    if ratio >= 0.6:
        return ClaimVerificationOutcome.SUPPORTED, f"Sufficient relational support ({ratio:.0%})"
    else:
        return ClaimVerificationOutcome.INSUFFICIENT, f"Insufficient factual support ({ratio:.0%}): key tokens missing from evidence"


class ResearchSynthesizer:
    """Produces grounded answers with verified claim-level citations from retrieved evidence."""

    def __init__(self, llm_provider: LLMProviderPort) -> None:
        self.llm_provider = llm_provider

    async def synthesize_answer_with_facts(
        self,
        query: str,
        evidence_list: list[ResearchEvidence],
        conflicts: list[ResearchConflict] | None = None,
        call_timeout: float = 20.0,
    ) -> tuple[str, list[ResearchCitation], list[ResearchFact], EvidenceStatus]:
        """Synthesize a citation-grounded answer with verified claim-level facts and citations."""
        if not evidence_list:
            return (
                "Evidence not found / source unavailable.",
                [],
                [],
                EvidenceStatus.NOT_FOUND,
            )

        envelope = build_secure_evidence_envelope(evidence_list)

        # Enclose conflicts inside untrusted envelope (Blocker 9)
        conflict_notice = ""
        if conflicts:
            conflict_lines = "\n".join(
                f"  <conflict topic=\"{c.topic}\" resolution=\"{c.resolution_summary}\">\n"
                f"    <claim_a>{c.claim_a}</claim_a>\n"
                f"    <claim_b>{c.claim_b}</claim_b>\n"
                f"  </conflict>"
                for c in conflicts
            )
            conflict_notice = f"\n<untrusted_conflicts>\n{conflict_lines}\n</untrusted_conflicts>\n"

        user_content = (
            f"USER QUERY: {query}\n\n"
            f"RETRIEVED EVIDENCE:\n{envelope}\n"
            f"{conflict_notice}\n"
            f"Provide a clear, fact-based synthesis answering the query. "
            f"Include inline citations like [cite:TOKEN] for every factual statement using each block's cite_token."
        )

        raw_answer = await self.llm_provider.synthesize(
            system_instruction=SYSTEM_SYNTHESIZER_PROMPT,
            untrusted_evidence_prompt=user_content,
            call_timeout=call_timeout,
        )

        clean_answer = sanitize_for_display(raw_answer)

        # Build explicit citation token -> ResearchEvidence map (collision-safe full UUID provenance)
        token_to_evidence: dict[str, ResearchEvidence] = {}
        for ev in evidence_list:
            canonical_token = str(ev.evidence_id).lower()
            if canonical_token in token_to_evidence and token_to_evidence[canonical_token].evidence_id != ev.evidence_id:
                # Collision / duplicate identity defense: fail-closed / do not overwrite
                continue
            token_to_evidence[canonical_token] = ev

        CANONICAL_UUID_RE = re.compile(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        )

        # Conservative Claim Verification
        citations_map: dict[str, ResearchCitation] = {}
        facts_list: list[ResearchFact] = []
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", clean_answer) if s.strip()]
        validated_sentences: list[str] = []
        quarantined_claims: list[str] = []
        detected_conflicts: list[ResearchConflict] = list(conflicts or [])
        has_unverified_claims = False

        for sent in sentences:
            all_cites = re.findall(r"\[cite:([^\]]+)\]", sent)
            if not all_cites:
                # Substantive statement without citation
                words = sent.split()
                is_substantive = len(words) > 4 and not any(
                    w in sent.lower() for w in ["note", "disclaimer", "summary", "please", "consult", "verify", "evidence"]
                )
                if is_substantive:
                    has_unverified_claims = True
                    sent = f"{sent} [unverified]"
                validated_sentences.append(sent)
            else:
                # Validate claim against each cited evidence via explicit canonical UUID token mapping
                is_supported = True
                is_contradicted = False
                supported_evidences: list[tuple[str, ResearchEvidence]] = []

                for c_tok in all_cites:
                    # Enforce canonical full-UUID format: reject numeric, short, or malformed tokens
                    if not CANONICAL_UUID_RE.match(c_tok):
                        is_supported = False
                        break

                    ev = token_to_evidence.get(c_tok.lower())
                    if ev is None:
                        # Unknown or unsupplied citation token: reject support
                        is_supported = False
                        break

                    outcome, reason = verify_claim_support(sent, ev)
                    if outcome == ClaimVerificationOutcome.CONTRADICTED:
                        is_contradicted = True
                        detected_conflicts.append(
                            ResearchConflict(
                                topic=f"Contradiction in [cite:{c_tok}]",
                                claim_a=sent[:300],
                                claim_b=ev.snippet[:300] if ev.snippet else "",
                                resolution_summary=f"Claim contradicted by cited source: {reason}",
                            )
                        )
                        break
                    elif outcome == ClaimVerificationOutcome.SUPPORTED:
                        supported_evidences.append((c_tok, ev))
                    else:  # INSUFFICIENT
                        is_supported = False
                        break

                if is_contradicted or not is_supported or not supported_evidences:
                    has_unverified_claims = True
                    quarantined_claims.append(sent)
                else:
                    # Claim verified as SUPPORTED: allow into verified answer content & create ResearchFact
                    clean_claim = re.sub(r"\[cite:[^\]]+\]", "", sent).strip()
                    for c_tok, ev in supported_evidences:
                        cite_key = f"[cite:{ev.stable_token}]"
                        if cite_key not in citations_map:
                            citations_map[cite_key] = ResearchCitation(
                                citation_id=cite_key,
                                evidence_id=ev.evidence_id,
                                title=ev.title,
                                domain=ev.domain,
                                canonical_url=ev.canonical_url,
                                published_at=ev.published_at,
                                retrieved_at=ev.retrieved_at,
                                locator=ev.locator or f"Passage {c_tok}",
                                cited_claims=[sent[:300]],
                                source_id=ev.source_id,
                            )
                        else:
                            if sent[:300] not in citations_map[cite_key].cited_claims:
                                citations_map[cite_key].cited_claims.append(sent[:300])

                        if clean_claim and not any(f.claim == clean_claim and f.evidence_id == ev.evidence_id for f in facts_list):
                            facts_list.append(
                                ResearchFact(
                                    fact_id=uuid4(),
                                    claim=clean_claim,
                                    evidence_id=ev.evidence_id,
                                    citation_id=cite_key,
                                    source_id=ev.source_id,
                                )
                            )

                    validated_sentences.append(sent)

        final_answer = " ".join(validated_sentences)
        citations_list = list(citations_map.values())
        citations_list.sort(key=lambda c: c.citation_id)

        # Determine evidence completeness status
        if not citations_list and (not final_answer or "not found" in final_answer.lower()):
            status = EvidenceStatus.NOT_FOUND
        elif detected_conflicts:
            status = EvidenceStatus.CONFLICTED
        elif has_unverified_claims or len(citations_list) < len(sentences) / 2:
            status = EvidenceStatus.PARTIAL if citations_list else EvidenceStatus.NOT_FOUND
        else:
            status = EvidenceStatus.COMPLETE

        return final_answer, citations_list, facts_list, status

    async def synthesize_answer(
        self,
        query: str,
        evidence_list: list[ResearchEvidence],
        conflicts: list[ResearchConflict] | None = None,
        call_timeout: float = 20.0,
    ) -> tuple[str, list[ResearchCitation], EvidenceStatus]:
        """Synthesize a citation-grounded answer (backward-compatible 3-tuple return)."""
        answer, citations, _, status = await self.synthesize_answer_with_facts(
            query=query,
            evidence_list=evidence_list,
            conflicts=conflicts,
            call_timeout=call_timeout,
        )
        return answer, citations, status

    async def synthesize(
        self,
        query: Any,
        evidence_list: list[ResearchEvidence],
        conflicts: list[ResearchConflict] | None = None,
        call_timeout: float = 20.0,
    ) -> Any:
        from domain.research import ResearchIntent, ResearchMode, ResearchResult

        query_text = getattr(query, "query_text", str(query))
        query_id = getattr(query, "query_id", uuid4())
        session_id = getattr(query, "session_id", uuid4())
        intent = getattr(query, "intent", ResearchIntent.GLOBAL_FLIGHT_RESEARCH)
        mode = getattr(query, "mode", ResearchMode.AUTO)

        try:
            answer, citations, facts, status = await self.synthesize_answer_with_facts(
                query=query_text,
                evidence_list=evidence_list,
                conflicts=conflicts,
                call_timeout=call_timeout,
            )
        except Exception:
            # Non-hallucinatory failure path without leaking exception text
            answer = "Synthesis unavailable at this time due to upstream service status."
            citations = []
            facts = []
            for ev in evidence_list:
                token = str(ev.evidence_id)
                citations.append(
                    ResearchCitation(
                        citation_id=f"[cite:{token}]",
                        evidence_id=ev.evidence_id,
                        title=ev.title,
                        domain=ev.domain,
                        canonical_url=ev.canonical_url,
                        published_at=ev.published_at,
                        retrieved_at=ev.retrieved_at,
                        source_id=ev.source_id,
                    )
                )
            status = EvidenceStatus.PARTIAL

        return ResearchResult(
            research_session_id=session_id,
            query_id=query_id,
            query_text=query.query_text,
            intent=intent,
            mode=mode,
            answer=answer,
            citations=citations,
            facts=facts,
            evidence_status=status,
            conflicts=conflicts or [],
            stale=False,
            evidence_count=len(evidence_list),
        )


# Backward-compatible alias
GroundedSynthesizer = ResearchSynthesizer
