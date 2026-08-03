"""Fetches and parses the live internship listing table from the
vanshb03/Summer2027-Internships README.
"""
import re
import sys

import requests

README_URL = (
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/README.md"
)


def fetch_readme_text() -> str:
    resp = requests.get(README_URL, timeout=30)
    resp.raise_for_status()
    return resp.text


def _clean_location(raw: str) -> str:
    text = raw.replace("</br>", ", ").replace("<br/>", ", ").replace("<br>", ", ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    return text


def parse_readme_table(md_text: str) -> list[dict]:
    start = md_text.find("TABLE_START")
    end = md_text.find("TABLE_END")
    if start == -1 or end == -1:
        raise ValueError("Could not locate TABLE_START/TABLE_END markers in README")

    table_text = md_text[start:end]
    lines = [line for line in table_text.splitlines() if line.strip().startswith("|")]
    # lines[0] is the header row, lines[1] is the "---" separator row
    rows = lines[2:]

    results = []
    last_company = None
    for line in rows:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue

        company, role, location_raw, link_cell = cells[0], cells[1], cells[2], cells[3]
        date_posted = cells[4] if len(cells) > 4 else ""

        if company == "↳":  # "↳" continuation marker
            company = last_company
        else:
            last_company = company

        href_match = re.search(r'href="([^"]+)"', link_cell)
        url = href_match.group(1) if href_match else None
        closed = url is None and "\U0001F512" in link_cell  # 🔒

        results.append(
            {
                "company": company,
                "role": role,
                "location": _clean_location(location_raw),
                "url": url,
                "date_posted": date_posted,
                "closed": closed,
            }
        )

    return results


def get_listings() -> list[dict]:
    return parse_readme_table(fetch_readme_text())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    listings = get_listings()
    print(f"Parsed {len(listings)} listings ({sum(1 for l in listings if l['closed'])} closed)")
    for row in listings[:5]:
        print(row)
