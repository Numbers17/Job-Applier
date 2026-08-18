"""Local, dependency-free similarity matching between job descriptions,
plus manifest management for generated resume variants -- so a
near-duplicate posting reuses an existing tailored resume instead of
paying for another Claude API call. No LLM call is made just to decide
whether to reuse; only word-overlap (Jaccard) on the job description text.
"""
import json
import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
from resume import template
from resume_tailor import tailor

ROOT = Path(__file__).resolve().parent.parent
RESUMES_DIR = ROOT / "resumes"
MANIFEST_PATH = RESUMES_DIR / "manifest.json"
BANK_PATH = ROOT / "experience_bank.json"

# Starting point, not tuned against real data yet -- adjust if reuse feels
# too eager (near-different postings reusing the same resume) or too shy
# (near-identical postings each regenerating).
REUSE_SIMILARITY_THRESHOLD = 0.45
COMPANY_ROLE_MATCH_BOOST = 0.15

_STOPWORDS = {
    "a", "an", "the", "and", "or", "to", "of", "in", "on", "for", "with",
    "from", "by", "at", "as", "is", "are", "was", "were", "be", "been",
    "being", "that", "which", "this", "these", "those", "its", "it", "you",
    "your", "we", "our", "will", "have", "has", "had", "not", "can",
    "who", "what", "all", "any", "or", "if", "so", "than", "then",
}


def extract_keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(entries: list[dict]) -> None:
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def find_reusable(jd_text: str, company: str, role: str, manifest: list[dict]) -> tuple[dict | None, float]:
    """Returns (best-matching manifest entry or None, its similarity score)."""
    keywords = extract_keywords(jd_text)
    best_entry = None
    best_score = 0.0
    for entry in manifest:
        score = jaccard(keywords, set(entry.get("keywords", [])))
        if entry.get("company", "").lower() == company.lower() and entry.get("role", "").lower() == role.lower():
            score += COMPANY_ROLE_MATCH_BOOST
        if score > best_score:
            best_score = score
            best_entry = entry
    if best_entry and best_score >= REUSE_SIMILARITY_THRESHOLD:
        return best_entry, best_score
    return None, best_score


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def _render_pdf(content: dict, out_path: Path, browser=None) -> None:
    """Renders via an existing Playwright `browser` if given (required when
    called from inside autofill.py, which already has a sync_playwright()
    session open -- Playwright's sync API can't be entered twice in the
    same process). Falls back to opening its own session for standalone use
    (e.g. testing this module directly).
    """
    html_out = template.render_html(content)
    if browser is not None:
        page = browser.new_page()
        page.set_content(html_out)
        page.pdf(path=str(out_path), format="Letter", print_background=True)
        page.close()
    else:
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page()
            page.set_content(html_out)
            page.pdf(path=str(out_path), format="Letter", print_background=True)
            b.close()


def select_or_generate(jd_text: str, company: str, role: str, jd_url: str = "", browser=None) -> tuple[Path, dict]:
    """Returns (resume_pdf_path, decision_info). decision_info always has a
    "decision" of "reused" or "generated" plus enough detail to log --
    matched variant + similarity score on reuse, or the fact-check's
    rejected-rewording audit on generation.

    Pass the caller's already-open Playwright `browser` when calling this
    from inside another Playwright session (see _render_pdf).
    """
    manifest = load_manifest()
    match, score = find_reusable(jd_text, company, role, manifest)
    if match:
        path = ROOT / match["path"]
        if path.exists():
            return path, {
                "decision": "reused",
                "similarity": round(score, 3),
                "matched_variant": match["path"],
            }
        # manifest points at a file that's gone missing (moved/deleted) -- fall through and regenerate

    bank = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    content, rejected = tailor(jd_text, company, role, bank)

    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    filename = f"{_slug(company)}-{_slug(role)}-{date_str}.pdf"
    out_path = RESUMES_DIR / filename
    _render_pdf(content, out_path, browser)

    keywords = extract_keywords(jd_text)
    manifest.append(
        {
            "path": f"resumes/{filename}",
            "company": company,
            "role": role,
            "jd_url": jd_url,
            "keywords": sorted(keywords),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    save_manifest(manifest)

    return out_path, {
        "decision": "generated",
        "similarity": round(score, 3),
        "rejected_rewordings": rejected,
    }
