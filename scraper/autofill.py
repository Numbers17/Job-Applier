"""Opens approved postings from output/candidates.csv in a browser, fills
in the standard fields from profile.json, and stops -- it never clicks
submit. Every run writes a JSON log (exact field values + fill status) and
a full-page screenshot per posting to output/logs/ so you can verify what
happened without re-watching the browser.

Usage:
    python autofill.py                  # headed + interactive: process unreviewed CSV rows
    python autofill.py --limit 3        # cap how many postings to open this run
    python autofill.py --url <job_url>  # spot-check a single posting, ignores the CSV
    python autofill.py --headless       # no visible window, no prompts -- just log+screenshot each row
"""
import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

import resume_library
from ats import ashby, greenhouse, lever
from ats._common import detect_closed_or_expired, dismiss_cookie_banner
from describe_check import detect_ats

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_PATH = ROOT / "output" / "candidates.csv"
PROFILE_PATH = ROOT / "profile.json"
LOGS_DIR = ROOT / "output" / "logs"

HANDLERS = {
    "greenhouse": greenhouse.fill,
    "lever": lever.fill,
    "ashby": ashby.fill,
}

# ATS platforms where the description page's "Apply" button must be clicked
# to reveal the actual application form fields.
NEEDS_APPLY_CLICK = {"lever", "ashby"}


def load_profile() -> dict:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    resume_path = (ROOT / profile["resume_path"]).resolve()
    if not resume_path.exists():
        raise FileNotFoundError(f"Resume not found at {resume_path}")
    profile["resume_path"] = str(resume_path)
    return profile


def load_candidate_rows() -> list[dict]:
    with CANDIDATES_PATH.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_candidate_rows(rows: list[dict]) -> None:
    with CANDIDATES_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def update_status(url: str, status: str) -> None:
    rows = load_candidate_rows()
    for row in rows:
        if row["url"] == url:
            row["status"] = status
            row["status_updated"] = datetime.now().isoformat(timespec="seconds")
    write_candidate_rows(rows)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def process_posting(
    page, browser, url: str, ats: str, company: str, role: str, profile: dict, run_dir: Path, index: int, interactive: bool
) -> dict:
    print(f"\n--- Opening [{ats}] {url}")
    log = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "url": url,
        "ats": ats,
        "apply_click": "not_needed",
        "page_title": None,
        "closed_or_expired": False,
        "resume": None,
        "fields": {},
        "screenshot": None,
        "error": None,
    }

    try:
        page.goto(url, timeout=30000, wait_until="load")
        page.wait_for_timeout(1500)
        dismiss_cookie_banner(page)
        log["page_title"] = page.title()

        if detect_closed_or_expired(page):
            log["closed_or_expired"] = True
            print("  This posting looks closed/expired (page says so) -- skipping field fill.")
            screenshot_path = run_dir / f"{index:02d}_{_slug(ats)}.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            log["screenshot"] = str(screenshot_path.relative_to(ROOT))
            log_path = run_dir / f"{index:02d}_{_slug(ats)}.json"
            log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
            print(f"  Log: {log_path.relative_to(ROOT)}")
            return log

        # Grab the JD text before any Apply click navigates Lever/Ashby away
        # from the description page.
        posting_profile = profile
        try:
            jd_text = page.inner_text("body")
            resume_path, resume_decision = resume_library.select_or_generate(jd_text, company, role, jd_url=url, browser=browser)
            log["resume"] = resume_decision
            posting_profile = dict(profile)
            posting_profile["resume_path"] = str(resume_path)
            print(f"  Resume: {resume_decision['decision']} (similarity={resume_decision['similarity']}) -> {resume_path.name}")
        except Exception as exc:  # noqa: BLE001 -- fall back to the default resume rather than abort the posting
            log["resume"] = {"decision": "error", "error": str(exc)}
            print(f"  [warn] resume tailoring failed, falling back to default resume: {exc}")

        if ats in NEEDS_APPLY_CLICK:
            try:
                page.get_by_text("Apply", exact=False).first.click(timeout=5000)
                page.wait_for_timeout(2000)
                log["apply_click"] = "clicked"
            except Exception as exc:  # noqa: BLE001
                log["apply_click"] = f"failed: {exc}"
                print(f"  [warn] couldn't find/click an Apply button automatically: {exc}")
                print("  Navigate to the application form yourself, then come back to this terminal.")

        handler = HANDLERS[ats]
        log["fields"] = handler(page, posting_profile, interactive)

        filled = [k for k, v in log["fields"].items() if v["status"] == "filled"]
        problems = {k: v["status"] for k, v in log["fields"].items() if v["status"] != "filled" and v["status"] != "skipped_no_value_in_profile"}
        print(f"  Filled: {', '.join(filled) if filled else '(none)'}")
        if problems:
            print(f"  Needs attention: {problems}")
        print("  Custom questions/EEOC fields are left for you. This script never clicks submit.")
    except Exception as exc:  # noqa: BLE001 -- a bad posting shouldn't kill the run
        log["error"] = str(exc)
        print(f"  [error] {exc}")

    screenshot_path = run_dir / f"{index:02d}_{_slug(ats)}.png"
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
        log["screenshot"] = str(screenshot_path.relative_to(ROOT))
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] screenshot failed: {exc}")

    log_path = run_dir / f"{index:02d}_{_slug(ats)}.json"
    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"  Log: {log_path.relative_to(ROOT)}")

    return log


def run(limit: int, single_url: str | None, headless: bool, company: str = "", role: str = "") -> None:
    profile = load_profile()

    if single_url:
        ats = detect_ats(single_url)
        if ats not in HANDLERS:
            print(f"'{ats}' isn't an autofill-supported ATS (only greenhouse/lever/ashby).")
            return
        targets = [{"url": single_url, "ats": ats, "company": company, "role": role}]
    else:
        if not CANDIDATES_PATH.exists():
            print(f"No {CANDIDATES_PATH} found -- run run_pipeline.py first.")
            return
        rows = load_candidate_rows()
        targets = [
            row
            for row in rows
            if row["ats"] in HANDLERS and row["status"] not in ("applied", "skip")
        ][:limit]
        if not targets:
            print("Nothing left to review -- every greenhouse/lever/ashby row is already applied/skip.")
            return

    run_dir = LOGS_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Logging this run to {run_dir.relative_to(ROOT)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        interactive = not headless
        for i, row in enumerate(targets, start=1):
            process_posting(
                page, browser, row["url"], row["ats"], row.get("company", ""), row.get("role", ""),
                profile, run_dir, i, interactive,
            )
            if single_url:
                if not headless:
                    input("  Press Enter to close...")
            elif headless:
                pass  # no live window to review; log + screenshot stand in for it
            else:
                status = input(
                    "  Status for this row [Enter=reviewed, 'applied', 'skip', or custom]: "
                ).strip() or "reviewed"
                update_status(row["url"], status)
        browser.close()

    print(f"\nDone. Review screenshots + JSON logs in {run_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5, help="Max postings to open this run (default: 5)")
    parser.add_argument("--url", type=str, default=None, help="Spot-check a single posting URL instead of reading the CSV")
    parser.add_argument("--headless", action="store_true", help="No visible browser/prompts -- just write logs+screenshots for review")
    parser.add_argument("--company", type=str, default="", help="Company name, only used with --url (helps resume tailoring/reuse matching)")
    parser.add_argument("--role", type=str, default="", help="Role title, only used with --url (helps resume tailoring/reuse matching)")
    args = parser.parse_args()
    run(args.limit, args.url, args.headless, args.company, args.role)
