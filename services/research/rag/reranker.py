"""Lexical-Semantic Hybrid Reranker for TrustTrip Phase 8 RAG.

Reranks candidate chunks or passages against the user query using:
- Term overlap and TF-IDF style term frequency weighting
- Exact multi-word phrase matching boost
- Length normalization penalty
"""
from __future__ import annotations

import math
import re
from typing import Any, NamedTuple
from domain.research import SourceTrustClass
from services.research.ports import RerankerPort


class ScoredPassage(NamedTuple):
    passage: Any
    score: float


class LexicalSemanticReranker(RerankerPort):
    """Hybrid reranker combining lexical term frequency, exact phrase boost, and length normalization."""

    def rerank(
        self,
        query: str,
        passages: list[Any],
        top_k: int = 5,
    ) -> list[ScoredPassage]:
        if not passages:
            return []

        clean_query = query.strip().lower()
        query_words = set(re.findall(r"\w+", clean_query))
        query_phrases = [p.strip() for p in clean_query.split() if len(p) > 3]

        scored: list[ScoredPassage] = []
        for p in passages:
            trust_weight = 1.0
            if isinstance(p, (tuple, list)):
                if len(p) >= 2 and isinstance(p[1], SourceTrustClass):
                    item, text = p[0], p[0]
                    tc = p[1]
                    if tc == SourceTrustClass.OFFICIAL:
                        trust_weight = 1.2
                    elif tc == SourceTrustClass.TRUSTED_SECONDARY:
                        trust_weight = 1.1
                    elif tc == SourceTrustClass.USER_PROVIDED:
                        trust_weight = 1.05
                elif len(p) >= 2 and isinstance(p[1], str) and len(p[1]) > len(str(p[0])):
                    item, text = p[0], p[1]
                else:
                    item, text = p[0], str(p[0])
            elif isinstance(p, str):
                item, text = p, p
            else:
                item, text = p, getattr(p, "text_content", str(p))
                tc = getattr(p, "trust_class", None)
                if tc == SourceTrustClass.OFFICIAL:
                    trust_weight = 1.2
                elif tc == SourceTrustClass.TRUSTED_SECONDARY:
                    trust_weight = 1.1

            if not text:
                scored.append(ScoredPassage(passage=item, score=0.0))
                continue

            clean_text = text.lower()
            text_words = re.findall(r"\w+", clean_text)
            text_word_set = set(text_words)

            if not text_words or not query_words:
                scored.append((item, 0.0))
                continue

            # 1. Term overlap (Jaccard + frequency)
            overlap_count = len(query_words.intersection(text_word_set))
            overlap_score = overlap_count / len(query_words)

            # Frequency of query words in text
            freq_score = sum(clean_text.count(w) for w in query_words) / math.sqrt(len(text_words))

            # 2. Exact phrase bonus
            phrase_bonus = 0.0
            if clean_query in clean_text:
                phrase_bonus = 0.3
            else:
                for phrase in query_phrases:
                    if phrase in clean_text:
                        phrase_bonus += 0.05
            phrase_bonus = min(0.3, phrase_bonus)

            # 3. Length normalization
            length_factor = 1.0 / (1.0 + 0.0005 * abs(len(text_words) - 150))

            combined_score = (0.5 * overlap_score + 0.3 * min(1.0, freq_score) + phrase_bonus) * length_factor * trust_weight
            normalized_score = max(0.0, min(1.0, combined_score))
            scored.append(ScoredPassage(passage=item, score=normalized_score))

        # Sort descending by score
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]
