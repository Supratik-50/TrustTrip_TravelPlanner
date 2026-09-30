"""Deterministic travel-planning utilities.

This module extracts the planning constraints needed to drive live providers.
It intentionally does not call an LLM so that a follow-up such as
"make it cheaper" can trigger a real provider search rather than a textual rewrite.
"""
from __future__ import annotations

from datetime import date, timedelta, datetime
from difflib import get_close_matches
from functools import lru_cache
import re
import json
from typing import Any

CITY_TO_IATA = {
    "kolkata": "CCU", "calcutta": "CCU", "delhi": "DEL", "new delhi": "DEL",
    "mumbai": "BOM", "bombay": "BOM", "bangalore": "BLR", "bengaluru": "BLR",
    "chennai": "MAA", "madras": "MAA", "hyderabad": "HYD", "pune": "PNQ",
    "ahmedabad": "AMD", "jaipur": "JAI", "goa": "GOI", "goa india": "GOI",
    "guwahati": "GAU", "patna": "PAT", "bhubaneswar": "BBI", "ranchi": "IXR",
    "surat": "STV", "amritsar": "ATQ", "kochi": "COK", "cochin": "COK", "lucknow": "LKO",
    "varanasi": "VNS", "nagpur": "NAG", "indore": "IDR", "chandigarh": "IXC", "coimbatore": "CJB",
    "srinagar": "SXR", "goa": "GOI", "mangalore": "IXE", "visakhapatnam": "VTZ", "vizag": "VTZ",
    "rajkot": "RAJ", "vadodara": "BDQ", "bhopal": "BHO", "dehradun": "DED", "agra": "AGR",
    "new york": "JFK", "newark": "EWR", "los angeles": "LAX", "san francisco": "SFO",
    "chicago": "ORD", "toronto": "YYZ", "vancouver": "YVR", "sydney": "SYD", "melbourne": "MEL",
    "frankfurt": "FRA", "amsterdam": "AMS", "doha": "DOH", "riyadh": "RUH", "jeddah": "JED",
    "istanbul": "IST", "hong kong": "HKG", "seoul": "ICN", "beijing": "PEK",
    "dhaka": "DAC", "dubai": "DXB", "bangkok": "BKK", "singapore": "SIN",
    "kathmandu": "KTM", "tokyo": "HND", "london": "LHR", "paris": "CDG",
    "kyoto": "KIX", "udaipur": "UDR",
}

CITY_TO_NAME = {
    "CCU": "Kolkata", "DEL": "Delhi", "BOM": "Mumbai", "BLR": "Bengaluru",
    "MAA": "Chennai", "HYD": "Hyderabad", "PNQ": "Pune", "AMD": "Ahmedabad",
    "JAI": "Jaipur", "GOI": "Goa", "GAU": "Guwahati", "PAT": "Patna",
    "BBI": "Bhubaneswar", "IXR": "Ranchi", "STV": "Surat", "ATQ": "Amritsar", "COK": "Kochi",
    "LKO": "Lucknow", "VNS": "Varanasi", "NAG": "Nagpur", "IDR": "Indore", "IXC": "Chandigarh",
    "CJB": "Coimbatore", "SXR": "Srinagar", "IXE": "Mangalore", "VTZ": "Visakhapatnam", "RAJ": "Rajkot",
    "BDQ": "Vadodara", "BHO": "Bhopal", "DED": "Dehradun", "AGR": "Agra", "UDR": "Udaipur", "JFK": "New York",
    "EWR": "Newark", "LAX": "Los Angeles", "SFO": "San Francisco", "ORD": "Chicago", "YYZ": "Toronto",
    "YVR": "Vancouver", "SYD": "Sydney", "MEL": "Melbourne", "FRA": "Frankfurt", "AMS": "Amsterdam",
    "DOH": "Doha", "RUH": "Riyadh", "JED": "Jeddah", "IST": "Istanbul", "HKG": "Hong Kong",
    "ICN": "Seoul", "PEK": "Beijing", "DAC": "Dhaka", "DXB": "Dubai",
    "BKK": "Bangkok", "SIN": "Singapore", "KTM": "Kathmandu", "HND": "Tokyo", "NRT": "Tokyo",
    "LHR": "London", "CDG": "Paris", "KIX": "Kyoto",
}


def _clean(text: str) -> str:
    return " ".join(str(text).strip().split())


def _find_date(text: str) -> str | None:
    patterns = [
        r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b",
        r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b",
    ]
    for p in patterns:
        m = re.search(p, text)
        if not m:
            continue
        try:
            if p.startswith(r"\b(20"):
                y, mo, d = map(int, m.groups())
            else:
                d, mo, y = map(int, m.groups())
            return date(y, mo, d).isoformat()
        except ValueError:
            pass

    low = text.lower()
    today = date.today()
    if "tomorrow" in low:
        return (today + timedelta(days=1)).isoformat()
    if "day after tomorrow" in low:
        return (today + timedelta(days=2)).isoformat()
    m = re.search(r"(?:next|coming)\s+(\d+)\s+days?", low)
    if m:
        return (today + timedelta(days=int(m.group(1)))).isoformat()
    if "next week" in low:
        return (today + timedelta(days=7)).isoformat()
    return None


def _find_duration(text: str) -> int | None:
    m = re.search(r"\b(\d+)\s*(?:day|days|night|nights)\b", text.lower())
    return int(m.group(1)) if m else None


def _find_budget(text: str) -> float | None:
    text = text.lower().replace(",", "")
    patterns = [
        r"(?:increase|raise|set|change|make)\s+(?:my\s+)?budget(?:\s+(?:to|at))?\s*(?:₹|rs\.?|inr)?\s*([0-9]+(?:\.[0-9]+)?)\s*(lakh|lakhs|k)?",
        r"(?:under|below|within|budget(?:\s+of)?|less than)\s*(?:₹|rs\.?|inr)?\s*([0-9]+(?:\.[0-9]+)?)\s*(lakh|lakhs|k)?",
        r"(?:₹|rs\.?|inr)\s*([0-9]+(?:\.[0-9]+)?)\s*(lakh|lakhs|k)?",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            value = float(m.group(1))
            suffix = (m.group(2) or "").lower()
            if suffix in {"lakh", "lakhs"}:
                value *= 100000
            elif suffix == "k":
                value *= 1000
            return value
    return None


@lru_cache(maxsize=1)
def _airport_catalog():
    """Load the complete IATA airport catalog lazily.

    The planner is intentionally not restricted to the small legacy CITY_TO_IATA
    dictionary. ``airportsdata`` is the primary global resolver; the dictionary
    is only an alias/fallback layer for common names.
    """
    try:
        import airportsdata
        return airportsdata.load("IATA")
    except Exception:
        return {}


def _airport_record(code: str) -> dict[str, Any]:
    return _airport_catalog().get(code.upper(), {})


def _country_name(code_or_name: str | None) -> str:
    """Normalize 2-letter ISO codes or common country text to canonical country name."""
    if not code_or_name:
        return ""
    val = str(code_or_name).strip()
    if len(val) == 2:
        try:
            import pycountry
            c = pycountry.countries.get(alpha_2=val.upper())
            if c:
                return c.name
        except Exception:
            pass
    common = {
        "in": "India", "us": "United States", "gb": "United Kingdom", "uk": "United Kingdom",
        "ae": "United Arab Emirates", "jp": "Japan", "th": "Thailand", "sg": "Singapore",
        "ca": "Canada", "au": "Australia", "fr": "France", "de": "Germany", "nl": "Netherlands",
        "it": "Italy", "es": "Spain", "ch": "Switzerland", "my": "Malaysia", "id": "Indonesia",
        "qa": "Qatar", "sa": "Saudi Arabia", "tr": "Turkey", "kr": "South Korea",
    }
    if val.lower() in common:
        return common[val.lower()]
    return val


def _city_from_airport(code: str, record: dict[str, Any], default: str = "") -> str:
    """Extract clean city name without leaking airport suffix."""
    if default and len(default) >= 3 and not default.isupper():
        return default
    if code in CITY_TO_NAME:
        return CITY_TO_NAME[code]
    city = str(record.get("city") or "").strip()
    if city:
        return city
    name = str(record.get("name") or "").strip()
    for suffix in (" International Airport", " Airport", " Aerodrome", " Air Base", " Station"):
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
            break
    return name or default or code


def _resolve_place(value: str) -> tuple[str | None, str | None, str | None]:
    """Resolve arbitrary city/airport/IATA text to (IATA, city, country).

    Resolution order is deterministic: explicit IATA -> known alias -> exact
    city/name -> fuzzy city. Country-only values intentionally return no airport
    because a country is not a valid flight endpoint; callers can still use the
    country metadata for planning and ask for a departure city when required.
    """
    raw = _clean(value)
    if not raw:
        return None, None, None

    airports = _airport_catalog()

    # 1. Handle explicit IATA code in parentheses, e.g. "Kolkata (CCU)" or "London (LHR)"
    m_paren = re.search(r"\(([A-Za-z]{3})\)", raw)
    if m_paren:
        code_cand = m_paren.group(1).upper()
        if code_cand in airports:
            a = airports[code_cand]
            city_prefix = raw[:m_paren.start()].strip()
            city = city_prefix.title() if city_prefix else _city_from_airport(code_cand, a, code_cand)
            country = _country_name(a.get("country"))
            return code_cand, city, country
        if code_cand in CITY_TO_NAME:
            city_prefix = raw[:m_paren.start()].strip()
            return code_cand, (city_prefix.title() if city_prefix else CITY_TO_NAME[code_cand]), ("India" if code_cand in {
                "CCU","DEL","BOM","BLR","MAA","HYD","PNQ","AMD","JAI","GOI","GAU","PAT","BBI","IXR","STV","ATQ","COK","LKO","VNS","NAG","IDR","IXC","CJB","SXR","IXE","VTZ","RAJ","BDQ","BHO","DED","AGR"
            } else "")

    # 2. Handle comma-separated place and country, e.g. "Goa, India" or "Paris, France"
    if "," in raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) >= 2:
            sub_code, sub_city, sub_country = _resolve_place(parts[0])
            hint_country = _country_name(parts[1]) or parts[1].title()
            if sub_code:
                return sub_code, sub_city, (sub_country or hint_country)
            elif sub_city:
                return None, sub_city, (sub_country or hint_country)

    value = raw.lower()
    STOP_WORDS = {"a", "an", "the", "my", "our", "this", "that", "trip", "tour", "plan", "days", "day", "night", "nights"}
    if not value or value in STOP_WORDS:
        return None, None, None

    # 3. Prioritize city alias before 3-letter airport codes so "goa" resolves to GOI (India) rather than Genoa (Italy)
    if value in CITY_TO_IATA:
        code = CITY_TO_IATA[value]
        a = _airport_record(code)
        city = _city_from_airport(code, a, raw.title())
        fallback_country = {"DXB": "United Arab Emirates", "BKK": "Thailand", "SIN": "Singapore", "HND": "Japan", "NRT": "Japan", "LHR": "United Kingdom", "CDG": "France", "JFK": "United States", "EWR": "United States", "LAX": "United States", "SFO": "United States", "YYZ": "Canada", "YVR": "Canada", "SYD": "Australia", "MEL": "Australia", "FRA": "Germany", "AMS": "Netherlands", "DOH": "Qatar", "RUH": "Saudi Arabia", "JED": "Saudi Arabia", "IST": "Turkey", "HKG": "Hong Kong", "ICN": "South Korea", "PEK": "China", "DAC": "Bangladesh", "KTM": "Nepal", "KIX": "Japan"}
        country = _country_name(a.get("country")) or fallback_country.get(code) or ("India" if code in {
            "CCU","DEL","BOM","BLR","MAA","HYD","PNQ","AMD","JAI","GOI","GAU","PAT","BBI","IXR","STV","ATQ","COK","LKO","VNS","NAG","IDR","IXC","CJB","SXR","IXE","VTZ","RAJ","BDQ","BHO","DED","AGR","UDR"
        } else None)
        return code, city, country

    # 4. Explicit 3-letter IATA code if not a known city collision (like GOA)
    if len(raw) == 3 and raw.upper() in airports and raw.upper() not in {"GOA"}:
        code = raw.upper()
        a = airports[code]
        city = _city_from_airport(code, a, raw.title())
        country = _country_name(a.get("country"))
        return code, city, country

    if len(raw) == 3 and raw.upper() in CITY_TO_NAME:
        code = raw.upper()
        return code, CITY_TO_NAME[code], ("India" if code in {
            "CCU","DEL","BOM","BLR","MAA","HYD","PNQ","AMD","JAI","GOI","GAU","PAT","BBI","IXR","STV","ATQ","COK","LKO","VNS","NAG","IDR","IXC","CJB","SXR","IXE","VTZ","RAJ","BDQ","BHO","DED","AGR","UDR"
        } else "")

    country_aliases = {
        "india":"India", "japan":"Japan", "thailand":"Thailand", "uae":"United Arab Emirates",
        "dubai":"United Arab Emirates", "uk":"United Kingdom", "england":"United Kingdom",
        "usa":"United States", "america":"United States", "canada":"Canada", "france":"France",
        "germany":"Germany", "italy":"Italy", "spain":"Spain", "singapore":"Singapore",
        "australia":"Australia", "malaysia":"Malaysia", "indonesia":"Indonesia",
        "netherlands":"Netherlands", "holland":"Netherlands", "china":"China",
        "bangladesh":"Bangladesh", "nepal":"Nepal", "qatar":"Qatar", "saudi arabia":"Saudi Arabia",
        "turkey":"Turkey", "south korea":"South Korea", "korea":"South Korea",
    }
    if value in country_aliases:
        return None, None, country_aliases[value]

    try:
        import pycountry
        c_obj = pycountry.countries.get(name=raw.title()) or pycountry.countries.get(common_name=raw.title())
        if c_obj:
            return None, None, c_obj.name
    except Exception:
        pass

    if not airports:
        return None, None, None

    exact_city = []
    exact_name = []
    for code, a in airports.items():
        city = str(a.get("city") or "").strip().lower()
        name = str(a.get("name") or "").strip().lower()
        if value == city:
            exact_city.append((code, a))
        elif value == name:
            exact_name.append((code, a))

    # Prefer larger/known airport codes where a city has multiple airports.
    preferred_codes = {"JFK","EWR","LGA","LHR","LGW","HND","NRT","CDG","ORY","FRA","AMS","DXB","DWC","SIN","BKK","DMK","SYD","MEL","SFO","LAX","ORD","DEL","BOM","BLR","MAA","HYD","CCU"}
    candidates = exact_city or exact_name
    if candidates:
        candidates.sort(key=lambda item: (0 if item[0] in preferred_codes else 1, item[0]))
        code, a = candidates[0]
        city = _city_from_airport(code, a, raw.title())
        country = _country_name(a.get("country"))
        return code, city, country

    # Never interpret a country name as one arbitrary airport.
    country_matches = [a for a in airports.values() if str(a.get("country") or "").strip().lower() == value or _country_name(a.get("country")).lower() == value]
    if country_matches:
        return None, None, _country_name(country_matches[0].get("country"))

    # Conservative substring/fuzzy city resolution requires at least 3 characters.
    if len(value) < 3:
        return None, None, None

    candidates = []
    for code, a in airports.items():
        city = str(a.get("city") or "").lower()
        name = str(a.get("name") or "").lower()
        if value and (value == city or value in city or value in name):
            candidates.append((code, a))
    if candidates:
        candidates.sort(key=lambda item: (0 if item[0] in preferred_codes else 1, item[0]))
        code, a = candidates[0]
        city = _city_from_airport(code, a, raw.title())
        country = _country_name(a.get("country"))
        return code, city, country

    names = {str(a.get("city") or "").lower(): (code, a) for code, a in airports.items() if a.get("city")}
    match = get_close_matches(value, list(names), n=1, cutoff=0.82)
    if match:
        code, a = names[match[0]]
        city = _city_from_airport(code, a, raw.title())
        country = _country_name(a.get("country"))
        return code, city, country
    return None, None, None


def _resolve_airport(value: str) -> tuple[str | None, str | None]:
    code, city, _country = _resolve_place(value)
    return code, city


def _find_date_range(text: str) -> tuple[str | None, str | None]:
    patterns = [
        r"(?:from|between)\s+(\d{1,2}[\-/]\d{1,2}[\-/]20\d{2}|20\d{2}[\-/]\d{1,2}[\-/]\d{1,2}|[A-Za-z]{3,9}\s+\d{1,2},?\s+20\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2})\s+(?:to|until|through|and)\s+(\d{1,2}[\-/]\d{1,2}[\-/]20\d{2}|20\d{2}[\-/]\d{1,2}[\-/]\d{1,2}|[A-Za-z]{3,9}\s+\d{1,2},?\s+20\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2})",
        r"(\d{1,2}[\-/]\d{1,2}[\-/]20\d{2}|20\d{2}[\-/]\d{1,2}[\-/]\d{1,2}|[A-Za-z]{3,9}\s+\d{1,2},?\s+20\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2})\s*(?:to|-)\s*(\d{1,2}[\-/]\d{1,2}[\-/]20\d{2}|20\d{2}[\-/]\d{1,2}[\-/]\d{1,2}|[A-Za-z]{3,9}\s+\d{1,2},?\s+20\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2})",
    ]
    def parse(v):
        v=v.replace(",", "")
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y"):
            try: return datetime.strptime(v, fmt).date().isoformat()
            except ValueError: pass
        return None
    for pattern in patterns:
        m=re.search(pattern, text, flags=re.I)
        if m:
            a,b=parse(m.group(1)),parse(m.group(2))
            if a and b: return a,b
    return None,None

def _find_route(text: str) -> tuple[str | None, str | None]:
    low = text.lower()
    m = re.search(r"\b([a-z]{3})\s+to\s+([a-z]{3})\b|\b([a-z]{3})\s*(?:→|->)\s*([a-z]{3})\b", low)
    if m:
        c1 = m.group(1) or m.group(3)
        c2 = m.group(2) or m.group(4)
        a, _, _ = _resolve_place(c1)
        b, _, _ = _resolve_place(c2)
        return a, b

    def resolve_pair(left: str, right: str):
        left = re.sub(r"^(?:please\s+)?plan(?:\s+a)?(?:\s+complete)?(?:\s+\d+\s*(?:day|days|night|nights))?(?:\s+trip)?\s*", "", left.strip(), flags=re.I)
        left = re.sub(r"^(?:a\s+)?trip\s+from\s+", "", left.strip(), flags=re.I)
        right = re.sub(r"\b(?:trip|tour|holiday|vacation)\b", "", right, flags=re.I).strip()
        o, _, _ = _resolve_place(left.strip(" .,;:-"))
        d, _, _ = _resolve_place(right.strip(" .,;:-"))
        return o, d

    route = re.search(
        r"\bfrom\s+(.+?)\s+to\s+(.+?)(?=\s+(?:on|for|under|below|within|budget|with|including|starting|between|from\s+\d|\d{1,2}[\-/]\d{1,2})\b|$)",
        low,
    )
    if route:
        o, d = resolve_pair(route.group(1), route.group(2))
        if o and d:
            return o, d

    m = re.search(r"\b(.+?)\s+to\s+(.+?)(?=\s+(?:on|for|under|below|within|budget|with|including|starting|between)\b|$)", low)
    if m:
        o, d = resolve_pair(m.group(1), m.group(2))
        if o and d:
            return o, d
    return None, None


def _find_trip_origin(text: str) -> tuple[str | None, str | None, str | None]:
    """Resolve an explicit origin in phrases like 'Japan trip from Chennai'."""
    low = text.lower()
    m = re.search(r"\bfrom\s+([A-Za-z][A-Za-z .'-]+?)(?=\s+(?:including|with|under|for|on|and)\b|$)", low)
    if not m:
        return None, None, None
    value = m.group(1).strip(" .,;:-")
    resolved = _resolve_place(value)
    if resolved[0] or resolved[2]:
        return resolved
    country_aliases = {
        "india":"India", "japan":"Japan", "thailand":"Thailand", "uae":"United Arab Emirates",
        "dubai":"United Arab Emirates", "uk":"United Kingdom", "england":"United Kingdom",
        "usa":"United States", "america":"United States", "canada":"Canada",
    }
    if value.lower() in country_aliases:
        return None, value.title(), country_aliases[value.lower()]
    return resolved


def _find_trip_destination(text: str) -> tuple[str | None, str | None, str | None]:
    """Resolve destination from arbitrary preset/natural trip phrases."""
    low = text.lower()
    patterns = [
        r"\b(?:plan\s+(?:a\s+)?(?:complete\s+)?)?(?:\d+\s+(?:day|days|night|nights)\s+)?([A-Za-z][A-Za-z .'-]+?)\s+(?:trip|tour|holiday|vacation)\b",
        r"\b(?:trip|tour|holiday|vacation)\s+(?:to|in)\s+([A-Za-z][A-Za-z .'-]+?)(?=\s+(?:from|for|under|with|including|on|$))",
        r"\b(?:to|in)\s+([A-Za-z][A-Za-z .'-]+?)\s+(?:trip|tour|holiday|vacation)\b",
    ]
    STOP_WORDS = {"a", "an", "the", "my", "our", "this", "that", "trip", "tour", "plan", "complete", "new"}
    for pattern in patterns:
        m = re.search(pattern, low)
        if m:
            value = m.group(1).strip(" .,;:-")
            # Remove leading planner phrases accidentally captured by the first pattern.
            value = re.sub(r"^(?:plan|a|complete|\d+\s*(?:day|days|night|nights))\s+", "", value).strip()
            if not value or value.lower() in STOP_WORDS or len(value) < 3:
                continue
            code, city, country = _resolve_place(value)
            if code or country:
                return code, city, country
            country_aliases = {
                "japan":"Japan", "thailand":"Thailand", "dubai":"United Arab Emirates", "uae":"United Arab Emirates",
                "india":"India", "uk":"United Kingdom", "england":"United Kingdom", "france":"France",
                "italy":"Italy", "singapore":"Singapore", "australia":"Australia", "usa":"United States",
                "america":"United States", "canada":"Canada", "germany":"Germany", "spain":"Spain",
                "switzerland":"Switzerland", "malaysia":"Malaysia", "indonesia":"Indonesia",
                "netherlands":"Netherlands", "holland":"Netherlands", "china":"China",
            }
            if value.lower() in country_aliases:
                return None, None, country_aliases[value.lower()]
    return None, None, None


def _find_route_queries(text: str) -> tuple[str | None, str | None]:
    """Return raw endpoint phrases so country-level routes are still represented."""
    low = text.lower()
    m = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+?)(?=\s+(?:on|for|under|below|within|budget|with|including|starting|between|from\s+\d|\d{1,2}[\-/]\d{1,2})\b|$)", low)
    if not m:
        m = re.search(r"\b(.+?)\s+to\s+(.+?)(?=\s+(?:on|for|under|below|within|budget|with|including|starting|between)\b|$)", low)
    if not m:
        return None, None
    left = re.sub(r"^(?:please\s+)?plan(?:\s+a)?(?:\s+complete)?(?:\s+\d+\s*(?:day|days|night|nights))?(?:\s+trip)?\s*", "", m.group(1).strip(), flags=re.I)
    right = re.sub(r"\b(?:trip|tour|holiday|vacation)\b", "", m.group(2), flags=re.I).strip()
    return left.strip(" .,;:-"), right.strip(" .,;:-")


def _find_passengers(text: str, previous: dict[str, Any] | None = None) -> dict[str, int]:
    """Extract passenger counts without using an LLM or spending provider calls."""
    previous = previous or {}
    low = text.lower()
    adults = previous.get("adults", 1) or 1
    children = previous.get("children", 0) or 0
    infants = previous.get("infants", 0) or 0

    patterns = [
        ("adults", r"(?:([0-9]+)\s*(?:adult|adults)|adult(?:s)?\s*[:=]?\s*([0-9]+))"),
        ("children", r"(?:([0-9]+)\s*(?:child|children|kid|kids)|(?:child(?:ren)?|kids?)\s*[:=]?\s*([0-9]+))"),
        ("infants", r"(?:([0-9]+)\s*(?:infant|infants|baby|babies)|(?:infant(?:s)?|bab(?:y|ies))\s*[:=]?\s*([0-9]+))"),
    ]
    for key, pattern in patterns:
        m = re.search(pattern, low)
        if m:
            value = next((g for g in m.groups() if g is not None), None)
            if value is not None:
                parsed = max(0, int(value))
                if key == "adults": adults = max(1, parsed)
                elif key == "children": children = parsed
                else: infants = parsed

    # Common compact forms: "for 3 people", "for 5 travellers".
    m = re.search(r"\bfor\s+(\d+)\s+(?:people|persons|passengers|travellers|travelers)\b", low)
    if m and not any(re.search(pattern, low) for _, pattern in patterns):
        adults = max(1, int(m.group(1)))

    # Revision forms: "change it to 5 people", "make it 4 travellers".
    m = re.search(r"\b(?:change|make|set)\b.*?\b(?:to|for)\s+(\d+)\s+(?:people|persons|passengers|travellers|travelers)\b", low)
    if m:
        adults = max(1, int(m.group(1)))
        children = 0
        infants = 0

    return {"adults": int(adults), "children": int(children), "infants": int(infants)}


def _is_follow_up_request(text: str) -> bool:
    """Return True only for an explicit continuation/revision request.

    A new trip such as ``Japan trip`` must never inherit the previous route
    (for example Mumbai -> London) merely because the same browser thread is
    being reused. HITL feedback such as ``make it cheaper`` still inherits the
    current trip.
    """
    low = _clean(text).lower()
    if not low:
        return False
    explicit = (
        "same trip", "same route", "same plan", "continue", "change it",
        "change the", "change destination", "change origin", "change budget",
        "change dates", "change duration", "change passengers", "change to",
        "make it", "make this", "keep the", "instead",
        "add a", "add an", "remove the", "remove", "replace the",
        "cheaper", "more affordable", "increase my budget", "increase budget",
        "reduce the cost", "lower the cost", "change my budget", "for the same",
        "revise", "modify the", "modify",
    )
    if any(x in low for x in explicit):
        return True
    # A standalone route/trip request is a new planning session.
    return False


def parse_trip_request(
    query: str,
    previous: dict[str, Any] | Any | None = None,
) -> dict[str, Any]:
    text = _clean(query)
    if hasattr(previous, "model_dump") and hasattr(previous, "origin"):
        from domain.compat import trip_request_to_legacy
        previous_dict = trip_request_to_legacy(previous)
    elif isinstance(previous, dict):
        previous_dict = previous
    else:
        previous_dict = {}

    is_canonical = bool(previous_dict.get("_is_canonical") or previous_dict.get("trip_id"))
    previous = previous_dict if (is_canonical or _is_follow_up_request(text)) else {}
    origin, destination = _find_route(text)
    raw_origin, raw_destination = _find_route_queries(text)
    route_origin_info = _resolve_place(raw_origin) if raw_origin else (None, None, None)
    route_destination_info = _resolve_place(raw_destination) if raw_destination else (None, None, None)
    trip_destination, trip_destination_city, trip_destination_country = _find_trip_destination(text)
    trip_origin, trip_origin_city, trip_origin_country = _find_trip_origin(text)
    if origin is None and trip_origin:
        origin = trip_origin
    if origin is None and route_origin_info[0] is not None:
        origin = route_origin_info[0]
    if destination is None and route_destination_info[0] is not None:
        destination = route_destination_info[0]
    duration = _find_duration(text)
    budget = _find_budget(text)
    range_start, range_end = _find_date_range(text)
    departure_date = range_start or _find_date(text)
    passengers = _find_passengers(text, previous)

    if is_canonical:
        # In an authoritative canonical trip, hard constraints (origin, destination, dates, duration, passengers, budget)
        # come strictly from the authoritative canonical trip unless the user explicitly requested a change.
        if previous.get("origin"):
            origin = previous.get("origin")
        if previous.get("destination"):
            destination = previous.get("destination")
        if previous.get("departure_date"):
            departure_date = previous.get("departure_date")
        if previous.get("return_date"):
            range_end = previous.get("return_date")
        if previous.get("duration_days"):
            duration = previous.get("duration_days")
        if previous.get("budget_amount") is not None:
            budget = previous.get("budget_amount")
        if previous.get("adults"):
            passengers["adults"] = int(previous.get("adults"))

    if destination is None and trip_destination:
        destination = trip_destination
    if origin is None:
        origin = previous.get("origin")
    if destination is None:
        destination = previous.get("destination")
    if duration is None:
        duration = previous.get("duration_days")
    if budget is None:
        budget = previous.get("budget_amount")
    if departure_date is None:
        departure_date = previous.get("departure_date")
    return_date = range_end or previous.get("return_date")
    if departure_date and return_date:
        try:
            duration = max(1, (date.fromisoformat(return_date) - date.fromisoformat(departure_date)).days + 1)
        except ValueError:
            pass

    origin_query = (previous.get("origin_query") if is_canonical else None) or raw_origin or origin
    destination_query = (previous.get("destination_query") if is_canonical else None) or raw_destination or destination
    origin_iata, origin_city, origin_country = _resolve_place(origin) if origin else (None, None, None)
    if is_canonical and previous.get("origin_country"):
        origin_country = previous.get("origin_country")
    elif not origin_country:
        origin_country = route_origin_info[2] or trip_origin_country or previous.get("origin_country") or ""
    origin_country = _country_name(origin_country)
    origin_city = (previous.get("origin_city") if is_canonical else None) or origin_city or route_origin_info[1] or trip_origin_city
    if origin_city and origin_country and origin_city.lower() == origin_country.lower():
        origin_city = None

    destination_iata, destination_city, destination_country = _resolve_place(destination) if destination else (None, None, None)
    origin = origin_iata or origin
    destination = destination_iata or destination
    if is_canonical and previous.get("destination_country"):
        destination_country = previous.get("destination_country")
    elif not destination_country:
        destination_country = route_destination_info[2] or trip_destination_country or previous.get("destination_country") or ""
    destination_country = _country_name(destination_country)
    destination_city = (previous.get("destination_city") if is_canonical else None) or destination_city or route_destination_info[1] or trip_destination_city
    if destination_city and destination_country and destination_city.lower() == destination_country.lower():
        destination_city = None

    # Country-only quick-trip requests need a concrete city for hotel/activity
    # planning. These are transparent gateway defaults for the three built-in
    # quick prompts; explicit city/airport requests always take precedence.
    if destination is None:
        gateway_defaults = {
            "japan": ("HND", "Tokyo"),
            "thailand": ("BKK", "Bangkok"),
            "united arab emirates": ("DXB", "Dubai"),
        }
        key = str(destination_country or "").strip().lower()
        is_explicit_route = bool(raw_origin and raw_destination)
        if key in gateway_defaults and not is_explicit_route and re.search(r"\b(?:trip|tour|holiday|vacation)\b", text, re.I):
            destination, destination_city = gateway_defaults[key]

    lower = text.lower()
    cheaper = any(phrase in lower for phrase in (
        "make it cheaper", "cheaper", "reduce the cost", "lower the cost",
        "save money", "more affordable", "budget option", "cheapest",
    ))
    flight_preference = previous.get("flight_preference") or ""
    if any(x in lower for x in ("morning flight", "early flight", "fly in the morning")):
        flight_preference = "morning"
    elif any(x in lower for x in ("afternoon flight", "fly in the afternoon")):
        flight_preference = "afternoon"
    elif any(x in lower for x in ("evening flight", "fly in the evening")):
        flight_preference = "evening"
    elif any(x in lower for x in ("night flight", "fly at night", "overnight flight")):
        flight_preference = "night"
    elif any(x in lower for x in ("non-stop", "nonstop", "direct flight")):
        flight_preference = "nonstop"
    flexible = departure_date is None
    if origin_country and destination_country:
        journey_scope = "domestic" if origin_country.lower() == destination_country.lower() else "international"
    else:
        journey_scope = previous.get("journey_scope") or "unknown"
    return {
        "origin": origin,
        "destination": destination,
        "origin_query": origin_query or raw_origin or previous.get("origin_query") or "",
        "destination_query": destination_query or raw_destination or previous.get("destination_query") or "",
        "origin_city": origin_city,
        "destination_city": destination_city,
        "origin_country": origin_country,
        "destination_country": destination_country,
        "journey_scope": journey_scope,
        "duration_days": duration or 5,
        "budget_amount": budget,
        "departure_date": departure_date,
        "return_date": return_date,
        "travel_dates": (f"{departure_date} to {return_date}" if departure_date and return_date else (departure_date or "Flexible dates")),
        "cheaper_mode": cheaper,
        "flight_preference": flight_preference,
        "flexible_dates": flexible,
        "query": text,
        "adults": passengers["adults"],
        "children": passengers["children"],
        "infants": passengers["infants"],
        "passenger_count": passengers["adults"] + passengers["children"] + passengers["infants"],
    }


def candidate_dates(constraints: dict[str, Any], limit: int = 3) -> list[tuple[str, str | None]]:
    """Generate deterministic, deduplicated, past-pruned candidate travel date windows.

    Supports exact date, plus_minus_days (+/- 1 to 3), date_range, and near_term_fallback.
    Strictly caps candidate pairs to at most 7 (and <= limit).
    """
    max_cap = min(limit, 7) if limit is not None else 7
    if max_cap < 1:
        return []

    mode = str(constraints.get("flexibility_mode") or "").strip().lower()
    flex_days = int(constraints.get("flexibility_days", 0) or 0)
    start = constraints.get("departure_date")
    duration = int(constraints.get("duration_days") or 5)
    today = date.today()

    candidates: list[tuple[str, str | None]] = []

    # 1. Date Range Mode
    if mode == "date_range" or (constraints.get("earliest_departure") and constraints.get("latest_departure")):
        earliest_raw = constraints.get("earliest_departure")
        latest_raw = constraints.get("latest_departure")
        try:
            earliest = date.fromisoformat(str(earliest_raw)[:10]) if earliest_raw else today
            latest = date.fromisoformat(str(latest_raw)[:10]) if latest_raw else today
        except Exception:
            earliest, latest = today, today

        if earliest > latest:
            earliest, latest = latest, earliest

        curr = earliest
        while curr <= latest and len(candidates) < max_cap:
            if curr >= today:
                ret = (curr + timedelta(days=max(1, duration - 1))).isoformat()
                candidates.append((curr.isoformat(), ret))
            curr += timedelta(days=1)

    # 2. Plus/Minus Days Mode
    elif (mode == "plus_minus_days" or flex_days > 0) and start:
        try:
            base_dep = date.fromisoformat(str(start)[:10])
        except Exception:
            base_dep = today + timedelta(days=2)

        span = max(1, min(3, flex_days if flex_days > 0 else 1))
        explicit_ret_str = constraints.get("return_date")
        explicit_ret: date | None = None
        if explicit_ret_str:
            try:
                explicit_ret = date.fromisoformat(str(explicit_ret_str)[:10])
            except Exception:
                explicit_ret = None

        for offset in range(-span, span + 1):
            dep = base_dep + timedelta(days=offset)
            if dep < today:
                continue
            if explicit_ret:
                ret = explicit_ret + timedelta(days=offset)
                if ret < dep:
                    ret = dep + timedelta(days=max(1, duration - 1))
                ret_str: str | None = ret.isoformat()
            else:
                ret_str = (dep + timedelta(days=max(1, duration - 1))).isoformat()
            candidates.append((dep.isoformat(), ret_str))

    # 3. Exact Date Mode (explicit date without flexibility)
    elif start and mode != "near_term_fallback":
        try:
            d = date.fromisoformat(str(start)[:10])
        except Exception:
            d = None
        if d and d >= today:
            explicit_return = constraints.get("return_date")
            candidates.append((d.isoformat(), explicit_return or (d + timedelta(days=max(1, duration - 1))).isoformat()))
        # Exact date mode must never silently fall into near-term fallback when date is in the past
        return candidates

    # 4. Near-Term Fallback Mode (explicitly requested or no date was supplied at all)
    elif mode == "near_term_fallback" or (not start and not constraints.get("earliest_departure")):
        base_fallback = today + timedelta(days=2)
        for i in range(max_cap):
            dep = base_fallback + timedelta(days=i * 3)
            ret = dep + timedelta(days=duration)
            candidates.append((dep.isoformat(), ret.isoformat()))

    # Deduplicate preserving order and cap to max_cap
    seen = set()
    deduped: list[tuple[str, str | None]] = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
        if len(deduped) >= max_cap:
            break

    return deduped



def _provider_text(value: Any) -> str:
    """Convert MCP/provider output into safe text for parsing and prompts.

    Tavily/MCP adapters may return a list/dict rather than a plain string.
    The planner must never pass those objects directly to ``re``.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            else:
                parts.append(json.dumps(item, ensure_ascii=False, default=str))
        return "\n".join(parts)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def budget_from_live_data(
    constraints: dict[str, Any],
    flight_offers: list[dict[str, Any]],
    hotel_text: Any,
    hotel_offers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Budget uses the deterministic recommended offer, not an invented fare.
    # Duffel's total already includes the requested passengers. Only INR offers
    # are included directly in the INR budget; non-INR provider prices remain
    # visible on the flight card without silently applying a fake exchange rate.
    recommended = next((o for o in flight_offers if o.get("is_recommended")), None) or (flight_offers[0] if flight_offers else None)
    if recommended:
        if recommended.get("price_inr") is not None:
            cheapest_flight = float(recommended["price_inr"])
        elif str(recommended.get("currency", "")).upper() == "INR" and recommended.get("price") is not None:
            cheapest_flight = float(recommended["price"])
        else:
            cheapest_flight = None
    else:
        cheapest_flight = None
    hotel_offers = hotel_offers or []
    hotel_prices = [float(o.get("price_inr")) for o in hotel_offers if o.get("price_inr") is not None and float(o.get("price_inr")) > 0]
    if hotel_prices:
        hotel_estimate = min(hotel_prices)
        hotel_source = "live dual-provider hotel offer"
    else:
        hotel_text = _provider_text(hotel_text)
        snippet_prices = []
        for m in re.finditer(r"(?:₹|INR|Rs\.?)[\s]*([0-9]{2,6})", hotel_text, flags=re.I):
            value = float(m.group(1))
            if 500 <= value <= 100000:
                snippet_prices.append(value)
        nightly = min(snippet_prices) if snippet_prices else 1800.0
        nights = max(1, int(constraints.get("duration_days") or 5) - 1)
        hotel_estimate = nightly * nights
        hotel_source = "planning estimate" if not snippet_prices else "live search snippet"
    local = 1200.0 * max(1, int(constraints.get("duration_days") or 5))
    activities = 900.0 * max(1, int(constraints.get("duration_days") or 5))
    flight = cheapest_flight or 0.0
    passenger_count = max(1, int(constraints.get("passenger_count") or 1))
    # Duffel's offer total already represents the passengers used in the
    # search, so do not multiply the flight amount again. Hotel/local/activity
    # estimates, however, need a modest passenger-aware adjustment.
    hotel_multiplier = max(1.0, 1.0 + 0.35 * max(0, passenger_count - 2))
    hotel_estimate *= hotel_multiplier
    local *= max(1.0, 1.0 + 0.15 * max(0, passenger_count - 1))
    activities *= max(1.0, 1.0 + 0.10 * max(0, passenger_count - 1))
    subtotal = flight + hotel_estimate + local + activities
    contingency = subtotal * 0.10
    total = subtotal + contingency
    target = constraints.get("budget_amount")
    return {
        "currency": "INR",
        "flight_estimate": round(flight, 2),
        "hotel_estimate": round(hotel_estimate, 2),
        "local_transport_estimate": round(local, 2),
        "activities_estimate": round(activities, 2),
        "contingency": round(contingency, 2),
        "estimated_total": round(total, 2),
        "target_budget": target,
        "within_budget": (target is None or total <= float(target)),
        "hotel_price_source": hotel_source,
        "recommended_hotel_id": (next((o for o in hotel_offers if o.get("is_recommended")), hotel_offers[0] if hotel_offers else {}) or {}).get("id"),
        "recommended_hotel_price_inr": (next((o for o in hotel_offers if o.get("is_recommended")), hotel_offers[0] if hotel_offers else {}) or {}).get("price_inr"),
        "passenger_count": passenger_count,
        "flight_data_available": bool(flight_offers),
        "flight_price_source": "recommended Duffel offer converted/displayed in INR" if cheapest_flight is not None else "INR conversion unavailable or no offer",
        "recommended_flight_id": (recommended or {}).get("id"),
        "recommended_flight_currency": (recommended or {}).get("currency"),
        "recommended_flight_price": (recommended or {}).get("price"),
    }
