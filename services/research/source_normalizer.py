"""Source Normalization, Trust Classification, and Deduplication for Phase 8.

Normalizes URLs, canonicalizes tracking parameters, and assigns descriptive
trust classifications with boundary-safe domain matching.

CRITICAL INVARIANT (Guardrail 3):
Source trust classification is strictly descriptive metadata.
OFFICIAL != automatically safe URL != automatically trusted content.
Trust classification NEVER bypasses SSRF, URL, tenant, or content-security controls.
"""
from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import uuid4

from domain.research import ResearchSource, SourceTrustClass, SourceType


# Strip tracking and noise query parameters
_TRACKING_PARAMS: set[str] = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "msclkid", "ref", "source", "ncid", "igshid",
    "_ga", "_gl", "cmpid", "trk",
}

# Boundary-safe official domain allowlists
_OFFICIAL_TLDS: tuple[str, ...] = (
    ".gov", ".gov.uk", ".gc.ca", ".gov.in", ".gov.au", ".gouv.fr",
    ".gov.sg", ".europa.eu", ".mil", ".state.gov",
)

_OFFICIAL_AUTHORITIES: tuple[str, ...] = (
    "who.int", "iata.org", "icao.int", "cdc.gov", "tsa.gov", "cbp.gov",
    "faa.gov", "caa.co.uk", "dgca.gov.in",
)

_OFFICIAL_AIRLINES: tuple[str, ...] = (
    "airindia.com", "airindia.in", "britishairways.com", "ba.com",
    "emirates.com", "lufthansa.com", "lh.com", "airfrance.com", "airfrance.fr",
    "klm.com", "qatarairways.com", "singaporeair.com", "united.com",
    "delta.com", "aa.com", "goindigo.in", "goindigo.com", "airvistara.com",
    "spicejet.com", "akasaair.com", "etihad.com", "turkishairlines.com",
    "virginatlantic.com", "qantas.com", "cathaypacific.com", "southwest.com",
)

_TRUSTED_SECONDARY_DOMAINS: tuple[str, ...] = (
    "lonelyplanet.com", "tripadvisor.com", "nationalgeographic.com",
    "bbc.com", "reuters.com", "cntraveler.com", "thepointsguy.com",
    "travelandleisure.com", "roughguides.com", "frequentmiler.com",
    "headforpoints.com", "seatguru.com", "flightradar24.com",
)


def matches_domain(hostname_or_url: str, target_domain: str) -> bool:
    """Boundary-safe domain match. Avoids naive substring or suffix vulnerabilities.

    Example:
    matches_domain('news.bbc.com', 'bbc.com') -> True
    matches_domain('mybbc.com', 'bbc.com') -> False
    matches_domain('evil-iata.org', 'iata.org') -> False
    """
    if "://" in hostname_or_url:
        parsed = urlparse(hostname_or_url)
        clean_host = (parsed.hostname or "").lower().rstrip(".")
    else:
        clean_host = hostname_or_url.split("/")[0].split(":")[0].lower().rstrip(".")

    clean_target = target_domain.lower().lstrip(".").rstrip(".")

    if clean_host == clean_target:
        return True
    if clean_host.endswith("." + clean_target):
        return True
    return False


def normalize_canonical_url(url: str) -> str:
    """Produce deterministic canonical URL by removing tracking params, fragments, and normalizing case."""
    if not url or not isinstance(url, str):
        return ""

    try:
        parsed = urlparse(url.strip())
    except Exception:
        return url.strip()

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Filter tracking parameters
    if parsed.query:
        query_pairs = parse_qsl(parsed.query, keep_blank_values=False)
        cleaned_pairs = [
            (k, v) for k, v in query_pairs
            if k.lower() not in _TRACKING_PARAMS
        ]
        # Sort keys deterministically
        cleaned_pairs.sort(key=lambda x: (x[0], x[1]))
        new_query = urlencode(cleaned_pairs)
    else:
        new_query = ""

    # Normalize path (remove double slashes)
    path = parsed.path or "/"
    while "//" in path:
        path = path.replace("//", "/")

    # Standardize root path
    if path == "":
        path = "/"

    # Omit default ports
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    elif netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]

    # Reconstruct without fragment
    return urlunparse((scheme, netloc, path, parsed.params, new_query, ""))


def classify_source_trust(hostname: str) -> SourceTrustClass:
    """Determine trust classification using boundary-safe domain checks.

    NOTE: This classification is purely descriptive and does not grant authorization.
    """
    clean_host = hostname.lower().rstrip(".")

    # 1. Government TLDs
    for tld in _OFFICIAL_TLDS:
        if clean_host.endswith(tld):
            return SourceTrustClass.OFFICIAL

    # 2. International aviation/health authorities
    for auth in _OFFICIAL_AUTHORITIES:
        if matches_domain(clean_host, auth):
            return SourceTrustClass.OFFICIAL

    # 3. Verified carrier domains
    for airline in _OFFICIAL_AIRLINES:
        if matches_domain(clean_host, airline):
            return SourceTrustClass.OFFICIAL

    # 4. Established secondary travel publishers
    for pub in _TRUSTED_SECONDARY_DOMAINS:
        if matches_domain(clean_host, pub):
            return SourceTrustClass.TRUSTED_SECONDARY

    return SourceTrustClass.UNKNOWN


def build_normalized_source(
    raw_url: str,
    title: str | None = None,
    snippet: str = "",
    source_type: SourceType = SourceType.WEB_PAGE,
) -> ResearchSource:
    """Create a canonical ResearchSource with normalized URL and computed content hash."""
    canonical = normalize_canonical_url(raw_url)
    parsed = urlparse(canonical)
    domain = parsed.hostname or ""

    trust = classify_source_trust(domain)
    content_hash = hashlib.sha256(f"{canonical}:{title or ''}:{snippet}".encode("utf-8")).hexdigest()

    return ResearchSource(
        source_id=uuid4(),
        url=raw_url,
        canonical_url=canonical,
        title=title,
        domain=domain,
        source_type=source_type,
        trust_class=trust,
        content_hash=content_hash,
        metadata={"snippet": snippet},
    )


class SourceNormalizer:
    """Convenience class providing source normalization, trust classification, and deduplication."""

    @staticmethod
    def canonicalize_url(url: str) -> str:
        res = normalize_canonical_url(url)
        parsed = urlparse(res)
        if parsed.path.endswith("/") and len(parsed.path) > 1:
            clean_path = parsed.path.rstrip("/")
            return urlunparse((parsed.scheme, parsed.netloc, clean_path, parsed.params, parsed.query, ""))
        return res

    @staticmethod
    def classify_trust(url_or_host: str) -> SourceTrustClass:
        if "://" in url_or_host:
            host = urlparse(url_or_host).hostname or ""
        else:
            host = url_or_host
        return classify_source_trust(host)

    @staticmethod
    def deduplicate_sources(sources: list[ResearchSource]) -> list[ResearchSource]:
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        deduped: list[ResearchSource] = []
        for s in sources:
            if s.canonical_url and s.canonical_url in seen_urls:
                continue
            if s.content_hash and s.content_hash in seen_hashes:
                continue
            if s.canonical_url:
                seen_urls.add(s.canonical_url)
            if s.content_hash:
                seen_hashes.add(s.content_hash)
            deduped.append(s)
        return deduped
