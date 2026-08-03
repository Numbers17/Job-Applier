"""Filters the raw listing table down to relevant, open roles and sorts
Seattle/WA-area and California postings to the top.
"""
import re
import sys

KEYWORD_PATTERNS = [
    r"software engineer",
    r"\bswe\b",
    r"backend",
    r"full[- ]?stack",
    r"\bpm\b",
    r"product manager",
    r"business",
    r"marketing",
    r"data analy",  # analyst / analysis
    r"data science",
]

PRIORITY_RE = re.compile(r", ?WA\b|seattle|, ?CA\b|california", re.IGNORECASE)


def is_relevant_role(role: str) -> bool:
    text = role.lower()
    if "hardware" in text:
        return False
    return any(re.search(pattern, text) for pattern in KEYWORD_PATTERNS)


def is_priority_location(location: str) -> bool:
    return bool(PRIORITY_RE.search(location))


def filter_and_sort(listings: list[dict]) -> list[dict]:
    seen_urls = set()
    kept = []
    for row in listings:
        if row["closed"]:
            continue
        if not row["url"]:
            continue
        if row["url"] in seen_urls:
            continue
        if not is_relevant_role(row["role"]):
            continue
        seen_urls.add(row["url"])
        row = dict(row)
        row["priority"] = is_priority_location(row["location"])
        kept.append(row)

    # stable sort: priority rows first, otherwise keep original (date-desc) order
    kept.sort(key=lambda r: 0 if r["priority"] else 1)
    return kept


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    from fetch_listings import get_listings

    listings = get_listings()
    filtered = filter_and_sort(listings)
    print(f"{len(listings)} total -> {len(filtered)} after keyword/location/closed filtering")
    for row in filtered[:10]:
        print(row)
