"""Fetches and parses live listings from multiple GitHub-hosted
internship/new-grad job repos. Each source uses one of two README table
conventions:

  - "pipe_table": a single TABLE_START/TABLE_END-delimited table with
    HTML <a href> links (vanshb03/Summer2027-Internships,
    vanshb03/New-Grad-2027, speedyapply/2027-SWE-College-Jobs).
  - "detail_sections": several <details>-collapsed tables (one per job
    category), no TABLE_START/END markers, markdown [img](url) links
    instead of HTML <a href> (zapplyjobs/*).
"""
import re
import sys

import requests

SOURCES = [
    {
        "name": "vanshb03/Summer2027-Internships",
        "url": "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/README.md",
        "format": "pipe_table",
        "listing_type": "internship",
    },
    {
        "name": "vanshb03/New-Grad-2027",
        "url": "https://raw.githubusercontent.com/vanshb03/New-Grad-2027/dev/README.md",
        "format": "pipe_table",
        "listing_type": "new_grad",
    },
    {
        "name": "speedyapply/2027-SWE-College-Jobs (NEW_GRAD_USA)",
        "url": "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md",
        "format": "pipe_table",
        "listing_type": "new_grad",
    },
    {
        "name": "zapplyjobs/New-Grad-Jobs-2027",
        "url": "https://raw.githubusercontent.com/zapplyjobs/New-Grad-Jobs-2027/main/README.md",
        "format": "detail_sections",
        "listing_type": "new_grad",
    },
    {
        "name": "zapplyjobs/New-Grad-Data-Science-Jobs-2027",
        "url": "https://raw.githubusercontent.com/zapplyjobs/New-Grad-Data-Science-Jobs-2027/main/README.md",
        "format": "detail_sections",
        "listing_type": "new_grad",
    },
]


def fetch_text(url: str) -> str:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def _clean_text(text: str) -> str:
    """Strips HTML tags and markdown bold markers, normalizes <br> to
    comma separators (for multi-location cells), and collapses whitespace.
    Different source repos mix HTML and markdown for the same purpose
    (bolded company names, <br>-separated locations), so this handles both.
    """
    text = text.replace("</br>", ", ").replace("<br/>", ", ").replace("<br>", ", ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("**", "")
    text = re.sub(r"\s+", " ", text).strip(" ,")
    return text


def _extract_url(cell: str) -> str | None:
    href_match = re.search(r'href="([^"]+)"', cell)  # HTML <a href="...">
    if href_match:
        return href_match.group(1)
    md_match = re.search(r"\]\((https?://[^)\s]+)\)", cell)  # markdown [text](url)
    if md_match:
        return md_match.group(1)
    return None


def parse_pipe_table(md_text: str, listing_type: str, source_name: str) -> list[dict]:
    start = md_text.find("TABLE_START")
    end = md_text.find("TABLE_END")
    if start == -1 or end == -1:
        return []

    table_text = md_text[start:end]
    lines = [line for line in table_text.splitlines() if line.strip().startswith("|")]
    rows = lines[2:]  # skip header + separator row

    results = []
    last_company = None
    for line in rows:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue

        company_raw, role_raw, location_raw, link_cell = cells[0], cells[1], cells[2], cells[3]
        date_posted = cells[4] if len(cells) > 4 else ""

        company = _clean_text(company_raw)
        if company in ("↳", ""):
            company = last_company
        else:
            last_company = company

        url = _extract_url(link_cell)
        closed = url is None and "\U0001F512" in link_cell  # 🔒

        results.append(
            {
                "company": company,
                "role": _clean_text(role_raw),
                "location": _clean_text(location_raw),
                "url": url,
                "date_posted": date_posted,
                "closed": closed,
                "listing_type": listing_type,
                "source": source_name,
            }
        )

    return results


def parse_detail_sections(md_text: str, listing_type: str, source_name: str) -> list[dict]:
    lines = md_text.splitlines()
    results = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("| Company") and "Role" in line and "Location" in line:
            i += 2  # skip header + separator row
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if len(cells) >= 5:
                    company = _clean_text(cells[0])
                    url = _extract_url(cells[-1])  # "Apply" is always the last column
                    if company and url:
                        results.append(
                            {
                                "company": company,
                                "role": _clean_text(cells[1]),
                                "location": _clean_text(cells[2]),
                                "url": url,
                                "date_posted": cells[3],
                                "closed": False,
                                "listing_type": listing_type,
                                "source": source_name,
                            }
                        )
                i += 1
        else:
            i += 1
    return results


PARSERS = {
    "pipe_table": parse_pipe_table,
    "detail_sections": parse_detail_sections,
}


def get_listings() -> list[dict]:
    all_rows = []
    for src in SOURCES:
        text = fetch_text(src["url"])
        rows = PARSERS[src["format"]](text, src["listing_type"], src["name"])
        print(f"  {src['name']}: {len(rows)} rows")
        all_rows.extend(rows)
    return all_rows


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    listings = get_listings()
    print(f"Parsed {len(listings)} listings total ({sum(1 for l in listings if l['closed'])} closed)")
    for row in listings[:5]:
        print(row)
