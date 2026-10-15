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
    SourceTrustClass,
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


CANONICAL_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
UUID_FINDER = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


_SYN_GROUPS = [
    {"allowed", "permitted", "accepted", "free", "authorized", "valid", "allow", "permit"},
    {"prohibited", "banned", "not allowed", "forbidden", "disallowed", "restricted", "prohibit"},
    {"baggage", "luggage", "bags", "suitcase", "carry-on", "cabin bag", "checked bag", "cabin"},
    {"fee", "charge", "cost", "price", "penalty"},
    {"mandatory", "required", "compulsory", "necessary", "obligation"},
    {"exempt", "waived", "free", "not required", "optional"},
    {"curfew", "night ban", "operating restrictions", "closure", "closed at night"},
    {"visa", "entry permit", "travel authorization", "eta", "esta"},
    {"bustling", "busy", "lively", "vibrant", "energetic", "popular", "active"},
    {"quiet", "peaceful", "calm", "calmer", "tranquil", "relaxed", "serene"},
    {"pristine", "unspoiled", "clean", "preserved"},
    {"luxury", "upscale", "high-end", "premium"},
    {"party", "nightlife", "clubs", "bars"},
    {"historical", "heritage", "history", "ancient", "monument", "monuments", "landmark", "landmarks", "fort", "forts", "church", "churches", "cathedral", "cathedrals", "basilica", "architecture", "colonial"},
    {"beach", "beaches", "coast", "coastal", "shore", "sea", "sand", "watersports", "water-sports"},
    {"food", "cuisine", "dining", "seafood", "restaurants", "eateries", "cafes", "dishes", "shacks"},
    {"monsoon", "rain", "rainfall", "rainy", "season", "weather", "climate", "monsoons"},
    {"visit", "visiting", "travel", "traveling", "explore", "exploring", "sightseeing", "attractions", "places"},
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
    clean_sent = re.sub(r"\[cite:\s*[a-zA-Z0-9_\-]+\s*\]", "", sentence).strip()
    # Strip markdown formatting, bullet prefixes, and quotes
    clean_sent = re.sub(r"[*_~`#]", " ", clean_sent)
    clean_sent = (
        clean_sent.replace("\u202f", " ")
        .replace("\xa0", " ")
        .replace("—", " ")
        .replace("–", " ")
        .replace("‑", "-")
        .replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
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
        "both", "such", "as", "which", "more", "other", "part", "state", "each",
    }
    raw_words = [re.sub(r"^[^\w]+|[^\w]+$", "", w) for w in clean_lower.split()]
    words = [w for w in raw_words if w]
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
    if ratio >= 0.45 or matched >= 3:
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

        has_doc = any(
            ev.domain == "local.document"
            or (bool(ev.canonical_url) and ev.canonical_url.startswith("document://"))
            or ev.trust_class == SourceTrustClass.USER_PROVIDED
            for ev in evidence_list
        )
        has_web = any(
            ev.domain != "local.document"
            and not (bool(ev.canonical_url) and ev.canonical_url.startswith("document://"))
            and ev.trust_class != SourceTrustClass.USER_PROVIDED
            for ev in evidence_list
        )
        hybrid_prompt = ""
        if has_doc and has_web:
            hybrid_prompt = (
                "\nNOTE: The evidence contains both private uploaded documents and external web travel guides. "
                "Synthesize findings from both sources, ensuring your answer includes distinct citations "
                "from both uploaded documents and external web sources.\n"
            )

        user_content = (
            f"USER QUERY: {query}\n\n"
            f"RETRIEVED EVIDENCE:\n{envelope}\n"
            f"{conflict_notice}"
            f"{hybrid_prompt}\n"
            f"Provide a clear, fact-based synthesis answering the query. "
            f"Do NOT format the response as a markdown table. Use clear narrative paragraphs or bullet points. "
            f"Every factual sentence MUST have an inline citation like [cite:TOKEN] using each block's EXACT full 36-character cite_token. "
            f"If a bullet point contains multiple sentences, append [cite:TOKEN] to EACH sentence in that bullet point; do NOT wait until the end of the bullet point."
        )

        raw_answer = await self.llm_provider.synthesize(
            system_instruction=SYSTEM_SYNTHESIZER_PROMPT,
            untrusted_evidence_prompt=user_content,
            call_timeout=call_timeout,
        )

        clean_answer = sanitize_for_display(raw_answer)
        # Normalize citation brackets commonly emitted by LLMs (e.g. lenticular brackets 【...】 or Unicode brackets)
        clean_answer = clean_answer.replace("【", "[").replace("】", "]")
        # Normalize non-breaking and narrow spaces
        clean_answer = clean_answer.replace("\u202f", " ").replace("\xa0", " ")
        # Normalize non-breaking hyphens inside UUIDs
        clean_answer = clean_answer.replace("\u2011", "-").replace("‑", "-")
        # Normalize comma-separated or multi-UUID citations like [cite:UUID1, UUID2] or [UUID1, UUID2]
        clean_answer = re.sub(
            r"\[(?:cite:\s*)?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})[;,]\s*(?:cite:\s*)?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\]",
            r"[cite:\1] [cite:\2]",
            clean_answer,
        )
        # Normalize [UUID], [index†UUID], or [cite:UUID] to canonical [cite:UUID]
        clean_answer = re.sub(
            r"\[(?:\d+\s*[\u2020†\*\-:]\s*|\s*cite:\s*)?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\]",
            r"[cite:\1]",
            clean_answer,
        )
        # Build prefix map for unambiguous 8+ character prefixes to canonical full UUID
        prefix_to_full_uuid: dict[str, str] = {}
        for ev in evidence_list:
            full_tok = str(ev.evidence_id).lower()
            pfx = full_tok[:8]
            if pfx not in prefix_to_full_uuid:
                prefix_to_full_uuid[pfx] = full_tok
            elif prefix_to_full_uuid[pfx] != full_tok:
                prefix_to_full_uuid[pfx] = ""

        # Normalize prefix tokens like [cite:9a16f4f4] to canonical full UUID [cite:9a16f4f4-...]
        def _expand_cite_token(m: re.Match) -> str:
            raw_tok = m.group(1).lower().strip()
            if CANONICAL_UUID_RE.match(raw_tok):
                return f"[cite:{raw_tok}]"
            if len(raw_tok) >= 8 and raw_tok[:8] in prefix_to_full_uuid and prefix_to_full_uuid[raw_tok[:8]]:
                return f"[cite:{prefix_to_full_uuid[raw_tok[:8]]}]"
            return f"[cite:{raw_tok}]"

        clean_answer = re.sub(
            r"\[(?:cite:\s*)?([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{0,27}))\s*\]",
            _expand_cite_token,
            clean_answer,
        )

        # Protect common abbreviations and titles from premature sentence splitting
        clean_answer = re.sub(r"\bvs\.\s+", "vs ", clean_answer, flags=re.IGNORECASE)
        clean_answer = re.sub(r"\be\.g\.\s+", "e.g. ", clean_answer, flags=re.IGNORECASE)
        clean_answer = re.sub(r"\bi\.e\.\s+", "i.e. ", clean_answer, flags=re.IGNORECASE)
        clean_answer = re.sub(r"\b(st|dr|mr|mrs|prof)\.\s+(?=[A-Z])", r"\1 ", clean_answer, flags=re.IGNORECASE)
        # Move citations placed after terminal punctuation to before punctuation so they stay attached to the sentence
        clean_answer = re.sub(r"([.!?])\s*((?:\[cite:[^\]]+\]\s*)+)", r" \2\1 ", clean_answer)
        clean_answer = re.sub(r"\s+([.!?])", r"\1", clean_answer)
        # Strip trailing unclosed citation brackets resulting from token truncation
        clean_answer = re.sub(r"\[(?:cite:)?\s*[0-9a-fA-F\-]{1,35}$", "", clean_answer).strip()

        # Build explicit citation token -> ResearchEvidence map (collision-safe full UUID provenance)
        token_to_evidence: dict[str, ResearchEvidence] = {}
        for ev in evidence_list:
            canonical_token = str(ev.evidence_id).lower()
            if canonical_token in token_to_evidence and token_to_evidence[canonical_token].evidence_id != ev.evidence_id:
                # Collision / duplicate identity defense: fail-closed / do not overwrite
                continue
            token_to_evidence[canonical_token] = ev

        # Conservative Claim Verification with Block-Level Context
        citations_map: dict[str, ResearchCitation] = {}
        facts_list: list[ResearchFact] = []
        raw_blocks = [b.strip() for b in re.split(r"(?<=\n)(?=[*\-#]|\d+\.)|\n{2,}", clean_answer) if b.strip()]
        if not raw_blocks:
            raw_blocks = [clean_answer.strip()] if clean_answer.strip() else []

        validated_sentences: list[str] = []
        quarantined_claims: list[str] = []
        detected_conflicts: list[ResearchConflict] = list(conflicts or [])
        has_unverified_claims = False

        for block in raw_blocks:
            block_uuids = [u.lower() for u in UUID_FINDER.findall(block)]
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", block) if s.strip()]

            for sent in sentences:
                extracted_uuids = [u.lower() for u in UUID_FINDER.findall(sent)]
                clean_stripped = sent.strip()
                is_header_or_intro = (
                    (
                        clean_stripped.startswith(("#", "**", "###", "##", "- **", "* **"))
                        and (
                            clean_stripped.endswith((": ", ":", "**", "–", "-"))
                            or (clean_stripped.startswith("**") and clean_stripped.endswith("**"))
                            or len(clean_stripped.split()) <= 8
                        )
                    )
                    or clean_stripped.endswith(":")
                    or clean_stripped.endswith(":-")
                    or any(clean_stripped.lower().startswith(p) for p in [
                        "these highlights", "these points", "in summary", "overall,", "in conclusion", "to conclude"
                    ])
                )

                candidate_uuids = extracted_uuids if extracted_uuids else block_uuids

                if not candidate_uuids:
                    if is_header_or_intro:
                        validated_sentences.append(sent)
                        continue

                    # Substantive statement without citation
                    words = sent.split()
                    is_substantive = len(words) > 4 and not any(
                        w in sent.lower() for w in ["note", "disclaimer", "summary", "please", "consult", "verify", "evidence"]
                    )
                    if is_substantive:
                        has_unverified_claims = True
                        sent = f"{sent} [unverified]"
                    validated_sentences.append(sent)
                    continue

                # Validate claim against candidate evidences
                is_supported = True
                is_contradicted = False
                supported_evidences: list[tuple[str, ResearchEvidence]] = []

                for c_tok in candidate_uuids:
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
                                source_a_title="Generated Claim",
                                source_a_trust=SourceTrustClass.UNKNOWN,
                                claim_b=ev.snippet[:300] if ev.snippet else "",
                                source_b_title=ev.title or "Cited Evidence",
                                source_b_trust=ev.trust_class or SourceTrustClass.UNKNOWN,
                                resolution_summary=f"Claim contradicted by cited source: {reason}",
                            )
                        )
                        break
                    elif outcome == ClaimVerificationOutcome.SUPPORTED:
                        supported_evidences.append((c_tok, ev))

                # If individual evidences fell just short on a multi-clause sentence, check combined text of all cited sources
                if not supported_evidences and not is_contradicted and len(candidate_uuids) > 1:
                    valid_evs = [token_to_evidence[u] for u in candidate_uuids if u in token_to_evidence]
                    if len(valid_evs) == len(candidate_uuids):
                        combined_text = " ".join(f"{e.title or ''} {e.snippet or ''} {' '.join(e.claims)}" for e in valid_evs)
                        mock_combined_ev = ResearchEvidence(
                            evidence_id=valid_evs[0].evidence_id,
                            query_id=valid_evs[0].query_id,
                            user_id=valid_evs[0].user_id,
                            source_id=valid_evs[0].source_id,
                            title="Combined Evidence",
                            snippet=combined_text[:3000],
                            canonical_url="combined://sources",
                            domain="combined",
                            trust_class=valid_evs[0].trust_class,
                            freshness_state=valid_evs[0].freshness_state,
                            retrieved_at=valid_evs[0].retrieved_at,
                            immutable_hash="combined",
                            citation_token=str(valid_evs[0].evidence_id),
                        )
                        outcome, _ = verify_claim_support(sent, mock_combined_ev)
                        if outcome == ClaimVerificationOutcome.SUPPORTED:
                            for u, e in zip(candidate_uuids, valid_evs):
                                supported_evidences.append((u, e))

                if is_contradicted or not is_supported or not supported_evidences:
                    if extracted_uuids:
                        # Explicitly cited claim failed verification: quarantine unsupported claim
                        has_unverified_claims = True
                        quarantined_claims.append(sent)
                    else:
                        # Block-level candidate did not support this claim
                        if not is_header_or_intro:
                            words = sent.split()
                            is_substantive = len(words) > 4 and not any(
                                w in sent.lower() for w in ["note", "disclaimer", "summary", "please", "consult", "verify", "evidence"]
                            )
                            if is_substantive:
                                has_unverified_claims = True
                                sent = f"{sent} [unverified]"
                        validated_sentences.append(sent)
                else:
                    # Claim verified as SUPPORTED: allow into verified answer content & create ResearchFact
                    clean_claim = re.sub(r"\[cite:\s*[^\]]+?\s*\]", "", sent).strip()
                    for c_tok, ev in supported_evidences:
                        cite_key = f"[cite:{ev.stable_token}]"
                        is_doc = (
                            ev.domain == "local.document"
                            or (bool(ev.canonical_url) and ev.canonical_url.startswith("document://"))
                            or ev.trust_class == SourceTrustClass.USER_PROVIDED
                        )
                        s_type = "document" if is_doc else "web"
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
                                source_type=s_type,
                                snippet=ev.snippet,
                                url=ev.canonical_url,
                                publisher=ev.domain,
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

                    if not extracted_uuids:
                        sent = f"{sent} " + " ".join(f"[cite:{c}]" for c, _ in supported_evidences)
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

        query_text = getattr(query, "query_text", None)
        if query_text is None:
            query_text = str(query) if query is not None else ""
        query_text = query_text.strip()
        if not query_text:
            raise ValueError("Query text cannot be empty.")

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
            query_text=query_text,
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
