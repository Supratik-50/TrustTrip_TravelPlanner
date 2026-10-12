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
    "surat": "STV", "amritsar": "ATQ", "kochi": "COK", "cochin": "COK",
    "kerala": "COK", "kerala india": "COK",
    "thiruvananthapuram": "TRV", "trivandrum": "TRV",
    "kozhikode": "CCJ", "calicut": "CCJ",
    "kannur": "CNN", "mattannur": "CNN",
    "lucknow": "LKO", "varanasi": "VNS", "nagpur": "NAG", "indore": "IDR",
    "chandigarh": "IXC", "coimbatore": "CJB", "srinagar": "SXR",
    "mangalore": "IXE", "visakhapatnam": "VTZ", "vizag": "VTZ",
    "rajkot": "RAJ", "vadodara": "BDQ", "bhopal": "BHO", "dehradun": "DED", "agra": "AGR",
    "darjeeling": "IXB", "darjeeling india": "IXB", "bagdogra": "IXB",
    "gangtok": "IXB", "gangtok india": "IXB", "sikkim": "IXB", "sikkim india": "IXB",
    "leh": "IXL", "leh ladakh": "IXL", "ladakh": "IXL", "ladakh india": "IXL",
    "ooty": "CJB", "ooty india": "CJB",
    "manali": "KUU", "manali india": "KUU", "kullu": "KUU",
    "shimla": "SLV", "dharamshala": "DHM",
    "rishikesh": "DED", "haridwar": "DED", "mussoorie": "DED",
    "new york": "JFK", "newark": "EWR", "los angeles": "LAX", "san francisco": "SFO",
    "chicago": "ORD", "toronto": "YYZ", "vancouver": "YVR", "sydney": "SYD", "melbourne": "MEL",
    "frankfurt": "FRA", "amsterdam": "AMS", "doha": "DOH", "riyadh": "RUH", "jeddah": "JED",
    "istanbul": "IST", "hong kong": "HKG", "seoul": "ICN", "beijing": "PEK",
    "dhaka": "DAC", "dubai": "DXB", "bangkok": "BKK", "singapore": "SIN",
    "kathmandu": "KTM", "tokyo": "HND", "london": "LHR", "paris": "CDG",
    "kyoto": "KIX", "udaipur": "UDR", "genoa": "GOA", "genova": "GOA",
}

REGION_TO_IATA = {
    "kerala": "COK",
    "kerala india": "COK",
    "goa": "GOI",
    "goa india": "GOI",
    "rajasthan": "JAI",
    "rajasthan india": "JAI",
    "darjeeling": "IXB",
    "darjeeling india": "IXB",
    "sikkim": "IXB",
    "sikkim india": "IXB",
    "ladakh": "IXL",
    "ladakh india": "IXL",
    "bali": "DPS",
    "bali indonesia": "DPS",
    "hawaii": "HNL",
    "hawaii usa": "HNL",
    "tuscany": "FLR",
    "tuscany italy": "FLR",
    "phuket": "HKT",
    "phuket thailand": "HKT",
}

CITY_TO_NAME = {
    "CCU": "Kolkata", "DEL": "Delhi", "BOM": "Mumbai", "BLR": "Bengaluru",
    "MAA": "Chennai", "HYD": "Hyderabad", "PNQ": "Pune", "AMD": "Ahmedabad",
    "JAI": "Jaipur", "GOI": "Goa", "GAU": "Guwahati", "PAT": "Patna",
    "BBI": "Bhubaneswar", "IXR": "Ranchi", "STV": "Surat", "ATQ": "Amritsar", "COK": "Kochi",
    "TRV": "Thiruvananthapuram", "CCJ": "Kozhikode", "CNN": "Kannur",
    "LKO": "Lucknow", "VNS": "Varanasi", "NAG": "Nagpur", "IDR": "Indore", "IXC": "Chandigarh",
    "CJB": "Coimbatore", "SXR": "Srinagar", "IXE": "Mangalore", "VTZ": "Visakhapatnam", "RAJ": "Rajkot",
    "BDQ": "Vadodara", "BHO": "Bhopal", "DED": "Dehradun", "AGR": "Agra", "UDR": "Udaipur", "JFK": "New York",
    "IXB": "Bagdogra", "IXL": "Leh", "KUU": "Kullu", "SLV": "Shimla", "DHM": "Dharamshala",
    "EWR": "Newark", "LAX": "Los Angeles", "SFO": "San Francisco", "ORD": "Chicago", "YYZ": "Toronto",
    "YVR": "Vancouver", "SYD": "Sydney", "MEL": "Melbourne", "FRA": "Frankfurt", "AMS": "Amsterdam",
    "DOH": "Doha", "RUH": "Riyadh", "JED": "Jeddah", "IST": "Istanbul", "HKG": "Hong Kong",
    "ICN": "Seoul", "PEK": "Beijing", "DAC": "Dhaka", "DXB": "Dubai",
    "BKK": "Bangkok", "SIN": "Singapore", "KTM": "Kathmandu", "HND": "Tokyo", "NRT": "Tokyo",
    "LHR": "London", "CDG": "Paris", "KIX": "Kyoto", "GOA": "Genoa",
}


def _clean(text: str) -> str:
    return " ".join(str(text).strip().split())


MONTH_MAP = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _resolve_year_for_date(month: int, day: int, base_year: int | None = None) -> str:
    """Explicit year policy: use base_year if provided. Otherwise, if date is in the future
    relative to today in the current year, use current year; if passed, roll to next year."""
    today = date.today()
    target_year = base_year or today.year
    try:
        cand = date(target_year, month, day)
        if not base_year and cand < today:
            cand = date(target_year + 1, month, day)
        return cand.isoformat()
    except ValueError:
        return f"{target_year:04d}-{month:02d}-{day:02d}"


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

    # Yearless single date support: "Oct 14", "October 14", "14 Oct", "14th October"
    m_name = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b", text, re.I)
    if m_name and m_name.group(1).lower() in MONTH_MAP:
        return _resolve_year_for_date(MONTH_MAP[m_name.group(1).lower()], int(m_name.group(2)))
    m_day = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\b", text, re.I)
    if m_day and m_day.group(2).lower() in MONTH_MAP:
        return _resolve_year_for_date(MONTH_MAP[m_day.group(2).lower()], int(m_day.group(1)))

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


def _find_currency(text: str, previous: dict[str, Any] | None = None) -> str:
    low = text.lower()
    if "usd" in low or "$" in text:
        return "USD"
    if "eur" in low or "€" in text:
        return "EUR"
    if "gbp" in low or "£" in text:
        return "GBP"
    if "inr" in low or "₹" in text or "rs" in low:
        return "INR"
    if previous and previous.get("currency"):
        return str(previous.get("currency")).upper()
    return "INR"


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


def _city_from_airport(code: str, record: dict[str, Any] | None = None, default: str = "") -> str:
    """Extract clean city name without leaking airport suffix."""
    if code in CITY_TO_NAME:
        return CITY_TO_NAME[code]
    if default and len(default) >= 3 and not default.isupper() and default.upper() != code.upper():
        return default
    if record is None:
        record = _airport_record(code)
    city = str(record.get("city") or "").strip()
    if city:
        return city
    name = str(record.get("name") or "").strip()
    for suffix in (" International Airport", " Airport", " Aerodrome", " Air Base", " Station"):
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
            break
    return name or default or code


def _normalize_country(val: str | None) -> tuple[str, str]:
    """Normalize input country text to (canonical_country_name, iso2_country_code)."""
    if not val:
        return "", ""
    s = val.strip()
    if len(s) == 2 and s.isalpha():
        try:
            import pycountry
            c = pycountry.countries.get(alpha_2=s.upper())
            if c:
                return c.name, c.alpha_2
        except Exception:
            pass
    common_map = {
        "india": ("India", "IN"),
        "united states": ("United States", "US"),
        "usa": ("United States", "US"),
        "us": ("United States", "US"),
        "america": ("United States", "US"),
        "united kingdom": ("United Kingdom", "GB"),
        "uk": ("United Kingdom", "GB"),
        "england": ("United Kingdom", "GB"),
        "japan": ("Japan", "JP"),
        "france": ("France", "FR"),
        "italy": ("Italy", "IT"),
        "germany": ("Germany", "DE"),
        "spain": ("Spain", "ES"),
        "thailand": ("Thailand", "TH"),
        "united arab emirates": ("United Arab Emirates", "AE"),
        "uae": ("United Arab Emirates", "AE"),
        "dubai": ("United Arab Emirates", "AE"),
        "singapore": ("Singapore", "SG"),
        "australia": ("Australia", "AU"),
        "canada": ("Canada", "CA"),
        "netherlands": ("Netherlands", "NL"),
        "holland": ("Netherlands", "NL"),
        "switzerland": ("Switzerland", "CH"),
        "indonesia": ("Indonesia", "ID"),
        "south korea": ("South Korea", "KR"),
        "korea": ("South Korea", "KR"),
        "china": ("China", "CN"),
        "bangladesh": ("Bangladesh", "BD"),
        "nepal": ("Nepal", "NP"),
        "qatar": ("Qatar", "QA"),
        "saudi arabia": ("Saudi Arabia", "SA"),
        "turkey": ("Turkey", "TR"),
    }
    low = s.lower()
    if low in common_map:
        return common_map[low]
    try:
        import pycountry
        c = pycountry.countries.get(name=s.title()) or pycountry.countries.get(common_name=s.title())
        if c:
            return c.name, c.alpha_2
        matches = pycountry.countries.search_fuzzy(s)
        if matches:
            return matches[0].name, matches[0].alpha_2
    except Exception:
        pass
    name = _country_name(s)
    return name, ""


INDIAN_AIRPORTS = {
    "CCU","DEL","BOM","BLR","MAA","HYD","PNQ","AMD","JAI","GOI","GAU","PAT",
    "BBI","IXR","STV","ATQ","COK","LKO","VNS","NAG","IDR","IXC","CJB","SXR",
    "IXE","VTZ","RAJ","BDQ","BHO","DED","AGR","UDR","TRV","CCJ","CNN",
    "IXB","IXL","KUU","SLV","DHM"
}


def _resolve_place(value: str, country_hint: str | None = None) -> tuple[str | None, str | None, str | None]:
    """Resolve arbitrary city/airport/IATA text to (IATA, city, country).

    Resolution priority:
    1. Exact IATA code (e.g. CCU, COK, CDG, or in parentheses like 'Kolkata (CCU)')
    2. Exact known city name / alias (CITY_TO_IATA, respecting country constraint)
    3. Exact state/region mapping (REGION_TO_IATA, e.g. 'Kerala' -> COK, 'Goa' -> GOI)
    4. Exact city/state/name in candidate airports filtered by country
    5. Country-aware substring matching (only within specified country)
    6. Controlled safe fuzzy matching (strictly bound to candidate country when provided)
    7. Fail safely rather than inventing unrelated foreign airports
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
            return code_cand, (city_prefix.title() if city_prefix else CITY_TO_NAME[code_cand]), ("India" if code_cand in INDIAN_AIRPORTS else "")

    # 2. Comma-separated place and country, e.g. "Kerala, India" or "Paris, France"
    effective_country_hint = country_hint
    place_text = raw
    if "," in raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) >= 2:
            c_name, c_iso = _normalize_country(parts[-1])
            if c_name:
                effective_country_hint = c_name
                place_text = ", ".join(parts[:-1]).strip()
            else:
                place_text = parts[0]
                effective_country_hint = parts[-1]

    country_canon, country_iso = _normalize_country(effective_country_hint)
    norm_val = place_text.lower()
    STOP_WORDS = {"a", "an", "the", "my", "our", "this", "that", "trip", "tour", "plan", "days", "day", "night", "nights"}
    if not norm_val or norm_val in STOP_WORDS:
        return None, None, country_canon or None

    # Country-only values return country without picking an arbitrary single airport
    country_matches = [a for a in airports.values() if str(a.get("country") or "").strip().lower() == norm_val or _country_name(a.get("country")).lower() == norm_val]
    if country_matches:
        return None, None, _country_name(country_matches[0].get("country"))

    # Documented Genoa/Goa Resolution Policy:
    # - "goa" / "goa india" -> GOI (Goa, India)
    # - "genoa" / "genova" -> GOA (Genoa, Italy)
    # - Literal ambiguous 3-letter "GOA": By policy in TrustTrip's travel context,
    #   "GOA" without an Italian qualifier resolves to Goa, India (GOI) to preserve
    #   domestic and international route stability. Explicit Genoa Cristoforo Colombo
    #   is resolved via "Genoa", "Genova", or "GOA (Italy)".
    is_italy = bool(effective_country_hint and ("italy" in effective_country_hint.lower() or "italia" in effective_country_hint.lower()))
    if (norm_val in {"genoa", "genova"}) or (norm_val in {"goa", "goa italy"} and is_italy) or (place_text.upper() == "GOA" and is_italy):
        a = _airport_record("GOA")
        return "GOA", "Genoa", _country_name(a.get("country")) or "Italy"
    if norm_val in {"goa", "goa india"} or place_text.upper() == "GOA":
        return "GOI", "Goa", "India"

    # 1. Exact 3-letter IATA code if uppercase or 3-letter match (never override explicit IATA)
    if len(place_text) == 3 and place_text.upper() in airports and place_text.upper() not in {"GOA"}:
        code = place_text.upper()
        a = airports[code]
        a_country = _country_name(a.get("country"))
        a_iso = str(a.get("country") or "").upper()
        if not country_iso or a_iso == country_iso or (country_canon and a_country.lower() == country_canon.lower()):
            city = _city_from_airport(code, a, place_text.title())
            return code, city, a_country

    # 2. Exact known city alias (CITY_TO_IATA)
    if norm_val in CITY_TO_IATA:
        code = CITY_TO_IATA[norm_val]
        a = _airport_record(code)
        a_country = _country_name(a.get("country")) or ("India" if code in INDIAN_AIRPORTS else None)
        a_iso = str(a.get("country") or "").upper()
        if not country_iso or a_iso == country_iso or (country_canon and str(a_country).lower() == country_canon.lower()):
            city = _city_from_airport(code, a, place_text.title())
            return code, city, a_country or country_canon

    # 3. Exact state / region mapping (REGION_TO_IATA)
    if norm_val in REGION_TO_IATA:
        code = REGION_TO_IATA[norm_val]
        a = _airport_record(code)
        a_country = _country_name(a.get("country")) or ("India" if code in INDIAN_AIRPORTS else None)
        a_iso = str(a.get("country") or "").upper()
        if not country_iso or a_iso == country_iso or (country_canon and str(a_country).lower() == country_canon.lower()):
            city = _city_from_airport(code, a, place_text.title())
            return code, city, a_country or country_canon

    # Filter candidate airports by country if country was specified
    candidate_airports = airports
    if country_iso:
        filtered = {c: a for c, a in airports.items() if str(a.get("country") or "").upper() == country_iso}
        if filtered:
            candidate_airports = filtered
    elif country_canon:
        filtered = {c: a for c, a in airports.items() if _country_name(a.get("country")).lower() == country_canon.lower()}
        if filtered:
            candidate_airports = filtered

    # Prefer larger/known airport codes where a city/region has multiple airports
    preferred_codes = {"JFK","EWR","LGA","LHR","LGW","HND","NRT","CDG","ORY","FRA","AMS","DXB","DWC","SIN","BKK","DMK","SYD","MEL","SFO","LAX","ORD","DEL","BOM","BLR","MAA","HYD","CCU","COK","GOI"}

    # 4. Exact city name, airport name, or subdivision (state) in candidate airports
    exact_city = []
    exact_name = []
    exact_subd = []
    for code, a in candidate_airports.items():
        city = str(a.get("city") or "").strip().lower()
        name = str(a.get("name") or "").strip().lower()
        subd = str(a.get("subd") or "").strip().lower()
        if norm_val == city:
            exact_city.append((code, a))
        elif norm_val == name:
            exact_name.append((code, a))
        elif norm_val == subd:
            exact_subd.append((code, a))

    exact_candidates = exact_city or exact_name or exact_subd
    if exact_candidates:
        exact_candidates.sort(key=lambda item: (0 if item[0] in preferred_codes else 1, item[0]))
        code, a = exact_candidates[0]
        city = _city_from_airport(code, a, place_text.title())
        country = _country_name(a.get("country")) or country_canon
        return code, city, country

    # 5. Substring / prefix match within candidate airports (min length >= 4)
    if len(norm_val) >= 4:
        sub_candidates = []
        for code, a in candidate_airports.items():
            city = str(a.get("city") or "").lower()
            name = str(a.get("name") or "").lower()
            if norm_val in city or norm_val in name:
                sub_candidates.append((code, a))
        if sub_candidates:
            sub_candidates.sort(key=lambda item: (0 if item[0] in preferred_codes else 1, item[0]))
            code, a = sub_candidates[0]
            city = _city_from_airport(code, a, place_text.title())
            country = _country_name(a.get("country")) or country_canon
            return code, city, country

    # 6. Controlled safe fuzzy matching
    # When a country context is specified, ONLY search candidate airports within that country.
    # Never cross international borders during fuzzy matching.
    if len(norm_val) >= 4:
        names = {str(a.get("city") or "").lower(): (code, a) for code, a in candidate_airports.items() if a.get("city")}
        match = get_close_matches(norm_val, list(names), n=1, cutoff=0.86)
        if match:
            code, a = names[match[0]]
            city = _city_from_airport(code, a, place_text.title())
            country = _country_name(a.get("country")) or country_canon
            return code, city, country

    # 7. Fail safely rather than guessing or picking an unrelated foreign airport
    return None, place_text.title(), country_canon or None
    return None, None, None


def _resolve_airport(value: str) -> tuple[str | None, str | None]:
    code, city, _country = _resolve_place(value)
    return code, city


def _find_date_range(text: str) -> tuple[str | None, str | None]:
    patterns_with_year = [
        r"(?:from|between)\s+(\d{1,2}[\-/]\d{1,2}[\-/]20\d{2}|20\d{2}[\-/]\d{1,2}[\-/]\d{1,2}|[A-Za-z]{3,9}\s+\d{1,2},?\s+20\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2})\s+(?:to|until|through|and)\s+(\d{1,2}[\-/]\d{1,2}[\-/]20\d{2}|20\d{2}[\-/]\d{1,2}[\-/]\d{1,2}|[A-Za-z]{3,9}\s+\d{1,2},?\s+20\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2})",
        r"(\d{1,2}[\-/]\d{1,2}[\-/]20\d{2}|20\d{2}[\-/]\d{1,2}[\-/]\d{1,2}|[A-Za-z]{3,9}\s+\d{1,2},?\s+20\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2})\s*(?:to|-)\s*(\d{1,2}[\-/]\d{1,2}[\-/]20\d{2}|20\d{2}[\-/]\d{1,2}[\-/]\d{1,2}|[A-Za-z]{3,9}\s+\d{1,2},?\s+20\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2})",
    ]
    def parse_with_year(v):
        v = v.replace(",", "")
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(v, fmt).date().isoformat()
            except ValueError:
                pass
        return None
    for pattern in patterns_with_year:
        m = re.search(pattern, text, flags=re.I)
        if m:
            a, b = parse_with_year(m.group(1)), parse_with_year(m.group(2))
            if a and b:
                return a, b

    # Yearless named month ranges: "Oct 14 to Oct 24", "Oct 14 - Oct 24", "Oct 14 to 24", "October 14 to October 24"
    m_month_first = re.search(
        r"(?:from\s+)?([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\s*(?:to|-|until|through)\s*(?:([A-Za-z]{3,9})\s+)?(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(20\d{2}))?",
        text,
        re.I,
    )
    if m_month_first:
        m1_name, d1_str, m2_name, d2_str, yr_str = m_month_first.groups()
        m1_key = m1_name.lower()
        if m1_key in MONTH_MAP:
            mo1 = MONTH_MAP[m1_key]
            d1 = int(d1_str)
            mo2 = MONTH_MAP[m2_name.lower()] if m2_name and m2_name.lower() in MONTH_MAP else mo1
            d2 = int(d2_str)
            base_yr = int(yr_str) if yr_str else None
            start_iso = _resolve_year_for_date(mo1, d1, base_yr)
            start_date_obj = date.fromisoformat(start_iso)
            ret_yr = start_date_obj.year + 1 if mo2 < mo1 else start_date_obj.year
            end_iso = _resolve_year_for_date(mo2, d2, ret_yr)
            return start_iso, end_iso

    # Day-first named month ranges: "14 Oct to 24 Oct", "14 October - 24 October"
    m_day_first = re.search(
        r"(?:from\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\s*(?:to|-|until|through)\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})(?:,?\s*(20\d{2}))?",
        text,
        re.I,
    )
    if m_day_first:
        d1_str, m1_name, d2_str, m2_name, yr_str = m_day_first.groups()
        if m1_name.lower() in MONTH_MAP and m2_name.lower() in MONTH_MAP:
            mo1 = MONTH_MAP[m1_name.lower()]
            mo2 = MONTH_MAP[m2_name.lower()]
            d1 = int(d1_str)
            d2 = int(d2_str)
            base_yr = int(yr_str) if yr_str else None
            start_iso = _resolve_year_for_date(mo1, d1, base_yr)
            start_date_obj = date.fromisoformat(start_iso)
            ret_yr = start_date_obj.year + 1 if mo2 < mo1 else start_date_obj.year
            end_iso = _resolve_year_for_date(mo2, d2, ret_yr)
            return start_iso, end_iso

    # Day-first with single month at end: "14 to 24 Oct", "14-24 October"
    m_day_single = re.search(
        r"(?:from\s+)?\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:to|-|until|through)\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})(?:,?\s*(20\d{2}))?",
        text,
        re.I,
    )
    if m_day_single:
        d1_str, d2_str, m_name, yr_str = m_day_single.groups()
        if m_name.lower() in MONTH_MAP:
            mo = MONTH_MAP[m_name.lower()]
            d1 = int(d1_str)
            d2 = int(d2_str)
            base_yr = int(yr_str) if yr_str else None
            start_iso = _resolve_year_for_date(mo, d1, base_yr)
            start_date_obj = date.fromisoformat(start_iso)
            ret_yr = start_date_obj.year + 1 if d2 < d1 else start_date_obj.year
            end_iso = _resolve_year_for_date(mo, d2, ret_yr)
            return start_iso, end_iso

    # Numeric day/month or month/day ranges without year: "14/10 to 24/10", "14-10 to 24-10"
    m_num = re.search(
        r"(?:from\s+)?(\d{1,2})[\-/](\d{1,2})\s*(?:to|-|until|through)\s*(\d{1,2})[\-/](\d{1,2})",
        text,
        re.I,
    )
    if m_num:
        n1, n2, n3, n4 = map(int, m_num.groups())
        if n2 <= 12 and n4 <= 12:
            d1, mo1, d2, mo2 = n1, n2, n3, n4
        elif n1 <= 12 and n3 <= 12:
            mo1, d1, mo2, d2 = n1, n2, n3, n4
        else:
            return None, None
        start_iso = _resolve_year_for_date(mo1, d1)
        start_date_obj = date.fromisoformat(start_iso)
        ret_yr = start_date_obj.year + 1 if mo2 < mo1 else start_date_obj.year
        end_iso = _resolve_year_for_date(mo2, d2, ret_yr)
        return start_iso, end_iso

    return None, None


ROUTE_DELIM = r"(?:,\s*(?:[A-Za-z]{3,9}\s+\d|\d{1,2}\s+[A-Za-z]{3,9}|\d{1,2}\s*(?:to|-)\s*\d{1,2}|\d{1,2}[\-/]\d{1,2}|\d+\s*(?:travelers?|travellers?|adults?|people|guests)|budget|₹|rs\.?|inr)|\s+(?:on|for|under|below|within|budget|with|including|starting|between|from\s+\d|\d{1,2}[\-/]\d{1,2})\b|$)"


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
        right = re.split(r",\s*(?:[A-Za-z]{3,9}\s+\d|\d{1,2}\s+[A-Za-z]{3,9}|\d{1,2}\s*(?:to|-)\s*\d{1,2}|\d{1,2}[\-/]\d{1,2}|\d+\s*(?:travelers?|travellers?|adults?|people|guests)|budget|₹|rs\.?|inr)", right, flags=re.I)[0]
        o, _, _ = _resolve_place(left.strip(" .,;:-"))
        d, _, _ = _resolve_place(right.strip(" .,;:-"))
        return o, d

    route = re.search(
        r"\bfrom\s+(.+?)\s+to\s+(.+?)(?=" + ROUTE_DELIM + r")",
        low,
    )
    if route:
        o, d = resolve_pair(route.group(1), route.group(2))
        if o and d:
            return o, d

    m = re.search(r"\b(.+?)\s+to\s+(.+?)(?=" + ROUTE_DELIM + r")", low)
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
    m = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+?)(?=" + ROUTE_DELIM + r")", low)
    if not m:
        m = re.search(r"\b(.+?)\s+to\s+(.+?)(?=" + ROUTE_DELIM + r")", low)
    if not m:
        return None, None
    left = re.sub(r"^(?:please\s+)?plan(?:\s+a)?(?:\s+complete)?(?:\s+\d+\s*(?:day|days|night|nights))?(?:\s+trip)?\s*", "", m.group(1).strip(), flags=re.I)
    right = re.sub(r"\b(?:trip|tour|holiday|vacation)\b", "", m.group(2), flags=re.I).strip()
    right = re.split(r",\s*(?:[A-Za-z]{3,9}\s+\d|\d{1,2}\s+[A-Za-z]{3,9}|\d{1,2}\s*(?:to|-)\s*\d{1,2}|\d{1,2}[\-/]\d{1,2}|\d+\s*(?:travelers?|travellers?|adults?|people|guests)|budget|₹|rs\.?|inr)", right, flags=re.I)[0]
    return left.strip(" .,;:-"), right.strip(" .,;:-")


def _find_passengers(text: str, previous: dict[str, Any] | None = None) -> dict[str, int]:
    """Extract passenger counts without using an LLM or spending provider calls."""
    previous = previous or {}
    low = text.lower()
    adults = previous.get("adults", 1) or 1
    children = previous.get("children", 0) or 0
    infants = previous.get("infants", 0) or 0

    patterns = [
        ("adults", r"(?:([0-9]+)\s*(?:adults?|travellers?|travelers?|people|persons?|guests?|pax)|(?:adults?|travellers?|travelers?|guests?)\s*[:=]?\s*([0-9]+))"),
        ("children", r"(?:([0-9]+)\s*(?:children|child|kids?)|(?:children|child|kids?)\s*[:=]?\s*([0-9]+))"),
        ("infants", r"(?:([0-9]+)\s*(?:infants?|babies|baby)|(?:infants?|bab(?:y|ies))\s*[:=]?\s*([0-9]+))"),
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

    # Common forms: "2 travelers", "for 3 people", "for 5 travellers", "party of 4"
    m = re.search(r"\b(?:for\s+|party\s+of\s+)?(\d+)\s*(?:people|persons?|passengers?|travellers?|travelers?|guests?|adults?|pax)\b", low)
    if m and not any(re.search(pattern, low) for key, pattern in patterns if key in ("children", "infants")):
        adults = max(1, int(m.group(1)))

    # Revision forms: "change it to 5 people", "make it 4 travellers".
    m = re.search(r"\b(?:change|make|set)\b.*?\b(?:to|for)\s+(\d+)\s+(?:people|persons|passengers|travellers|travelers|guests|adults)\b", low)
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
    if origin == "GOA" and (trip_origin_city == "Genoa" or (route_origin_info and route_origin_info[1] == "Genoa") or "genoa" in text.lower()):
        origin_iata, origin_city, origin_country = "GOA", "Genoa", "Italy"
    elif route_origin_info and route_origin_info[0] is not None and origin == route_origin_info[0]:
        origin_iata, origin_city, origin_country = route_origin_info
    else:
        origin_iata, origin_city, origin_country = _resolve_place(origin) if origin else (None, None, None)

    if is_canonical and previous.get("origin_country"):
        origin_country = previous.get("origin_country")
    elif not origin_country:
        origin_country = route_origin_info[2] or trip_origin_country or previous.get("origin_country") or ""
    origin_country = _country_name(origin_country)
    origin_city = (previous.get("origin_city") if is_canonical else None) or origin_city or route_origin_info[1] or trip_origin_city
    if origin_city and origin_country and origin_city.lower() == origin_country.lower():
        origin_city = None

    if destination == "GOA" and (trip_destination_city == "Genoa" or (route_destination_info and route_destination_info[1] == "Genoa") or "genoa" in text.lower()):
        destination_iata, destination_city, destination_country = "GOA", "Genoa", "Italy"
    elif route_destination_info and route_destination_info[0] is not None and destination == route_destination_info[0]:
        destination_iata, destination_city, destination_country = route_destination_info
    else:
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
        "currency": _find_currency(text, previous),
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
    if hotel_offers is None and isinstance(hotel_text, list):
        if hotel_text and any(
            isinstance(x, dict) and any(k in x for k in ("price", "price_inr", "total_amount", "price_total", "pricing_basis"))
            or hasattr(x, "price")
            for x in hotel_text
        ):
            hotel_offers = hotel_text
            hotel_text = ""
        else:
            hotel_offers = []
    hotel_offers = hotel_offers or []
    duration_days = max(1, int(constraints.get("duration_days") or 5))
    trip_nights = max(1, duration_days - 1)

    normalized_hotel_stays: list[float] = []
    primary_pricing_basis = "unknown"

    for o in hotel_offers:
        basis = getattr(o, "pricing_basis", None) or (o.get("pricing_basis") if isinstance(o, dict) else None)
        if hasattr(o, "nights") and hasattr(o, "price"):
            price_val = float(o.price.inr_reference_amount) if getattr(o.price, "inr_reference_amount", None) is not None else float(o.price.total_amount)
            if price_val > 0:
                normalized_hotel_stays.append(price_val)
                primary_pricing_basis = "full_stay"
        elif isinstance(o, dict):
            p_inr = o.get("price_inr") or o.get("price") or o.get("price_total") or o.get("total_amount")
            if p_inr is not None and float(p_inr) > 0:
                val = float(p_inr)
                offer_nights = int(o.get("nights") or 0)
                if basis == "per_night" or "night" in str(o.get("rate_type") or "").lower():
                    stay_nights = offer_nights if offer_nights > 0 else trip_nights
                    normalized_hotel_stays.append(val * stay_nights)
                    primary_pricing_basis = "per_night"
                elif basis == "full_stay" or offer_nights > 1 or o.get("total_price") is not None:
                    normalized_hotel_stays.append(val)
                    primary_pricing_basis = "full_stay"
                else:
                    # basis is missing, "unknown", or any other unrecognized value.
                    # We do NOT invent a pricing interpretation — skip this offer entirely
                    # so the budget falls back to the text-snippet estimate rather than
                    # silently assuming a semantics we cannot verify.
                    import logging as _logging
                    _logging.getLogger("trusttrip.budget").warning(
                        "Hotel offer '%s' has unrecognized pricing_basis=%r; "
                        "skipping offer to avoid silent full_stay assumption.",
                        o.get("id", "<unknown>"),
                        basis,
                    )

    if normalized_hotel_stays:
        hotel_estimate = min(normalized_hotel_stays)
        hotel_source = f"live dual-provider hotel offer ({primary_pricing_basis})"
    else:
        hotel_text = _provider_text(hotel_text)
        snippet_prices = []
        for m in re.finditer(r"(?:₹|INR|Rs\.?)[\s]*([0-9]{2,6})", hotel_text, flags=re.I):
            value = float(m.group(1))
            if 500 <= value <= 100000:
                snippet_prices.append(value)
        nightly = min(snippet_prices) if snippet_prices else 1800.0
        hotel_estimate = nightly * trip_nights
        primary_pricing_basis = "estimated_per_night" if snippet_prices else "unknown"
        hotel_source = "planning estimate (fallback)" if not snippet_prices else "live search snippet"
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
        "flight_estimate_inr": round(flight, 2),
        "hotel_estimate_inr": round(hotel_estimate, 2),
        "local_transport_estimate_inr": round(local, 2),
        "activities_estimate_inr": round(activities, 2),
        "contingency_inr": round(contingency, 2),
        "estimated_total_inr": round(total, 2),
        "target_budget_inr": target,
        "breakdown": {
            "Flights": round(flight, 2),
            "Accommodation": round(hotel_estimate, 2),
            "Local Transit": round(local, 2),
            "Activities": round(activities, 2),
            "Contingency": round(contingency, 2),
        },
        "within_budget": (target is None or total <= float(target)),
        "hotel_price_source": hotel_source,
        "hotel_pricing_basis": primary_pricing_basis,
        "recommended_hotel_id": (next((o for o in hotel_offers if o.get("is_recommended")), hotel_offers[0] if hotel_offers else {}) or {}).get("id"),
        "recommended_hotel_price_inr": (next((o for o in hotel_offers if o.get("is_recommended")), hotel_offers[0] if hotel_offers else {}) or {}).get("price_inr"),
        "passenger_count": passenger_count,
        "flight_data_available": bool(flight_offers),
        "flight_price_source": "recommended Duffel offer converted/displayed in INR" if cheapest_flight is not None else "INR conversion unavailable or no offer",
        "recommended_flight_id": (recommended or {}).get("id"),
        "recommended_flight_currency": (recommended or {}).get("currency"),
        "recommended_flight_price": (recommended or {}).get("price"),
    }
