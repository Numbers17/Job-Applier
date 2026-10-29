"""Opens approved postings from output/candidates.csv in a browser, fills
in the standard fields from profile.json, and stops -- it never clicks
submit. Every run writes a JSON log (exact field values + fill status) and
a full-page screenshot per posting to output/logs/ so you can verify what
happened without re-watching the browser.

In interactive (non-headless, non-single-url) runs, each row is previewed
(company/role/URL) before any work happens -- type "s" to skip it (marks
the row skip in candidates.csv and moves on) before spending any time or
API cost on resume tailoring and form-filling. There's also a second,
post-fill "skip" option in the status prompt for postings you only decide
against after seeing the filled form.

At that same post-fill prompt (and after a single --url run), typing
"r"/"refill" re-runs the field-fill against the already-open page --
useful if a field didn't take, or a site cleared/reset something -- without
redoing navigation or resume tailoring. The log/screenshot are refreshed
each time so they always reflect the latest fill attempt.

Typing "l"/"learn" at that same prompt scans the still-open page for any
dropdown/radio/checkbox question you've filled in by hand (left blank by
the automated fill, or on a fully manual/nonstandard site) and saves it to
answers.json, exactly as if you'd been prompted for it interactively --
so it's answered automatically on every future posting that asks the same
thing. Call it as many times as you like before moving on; it only saves
questions that currently have something selected/checked.

--headless only affects browser visibility and the per-row skip/status
prompts described above -- it does NOT make custom-question answering
silent. Every dropdown/radio/checkbox question on a posting is still
checked against answers.json, and any question with no saved answer stops
the run right there to ask you in the terminal (blocking on stdin, same
as a headed run) and saves what you type to answers.json before moving
on, so it's answered automatically on every future posting that asks the
same thing.

Every run uses a persistent Chromium profile (.browser_profile/, gitignored)
instead of a fresh throwaway session -- so if you sign into something (e.g.
MyGreenhouse, to have Greenhouse applications tracked under your account)
during a headed run, that login carries over to future runs, headless
included, the same as it would in a normal browser. This does mean only
one autofill.py can run at a time -- Chromium locks the profile directory
against concurrent use.

Usage:
    python autofill.py                  # headed + interactive: process unreviewed CSV rows
    python autofill.py --limit 3        # cap how many postings to open this run
    python autofill.py --url <job_url>  # spot-check a single posting, ignores the CSV
    python autofill.py --headless       # no visible window/row prompts -- still asks about unseen custom questions
    python autofill.py --listing-type internship  # only process internship rows this run
    python autofill.py --listing-type new_grad     # only process new-grad rows this run
    python autofill.py --standard-only             # only greenhouse/lever/ashby -- skips workday/other
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
from ats import ashby, generic, greenhouse, lever, workday
from ats._common import capture_answers, detect_closed_or_expired, dismiss_cookie_banner
from describe_check import detect_ats

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_PATH = ROOT / "output" / "candidates.csv"
PROFILE_PATH = ROOT / "profile.json"
LOGS_DIR = ROOT / "output" / "logs"

# A persistent Chromium profile (cookies, localStorage, etc.) instead of a
# fresh throwaway session every run -- so signing into something once (e.g.
# MyGreenhouse, my.greenhouse.io) stays signed in on future runs, the same
# as it would in a normal browser. This does mean only one autofill.py can
# run at a time -- Chromium locks the profile directory against concurrent
# use. Holds real login sessions, more sensitive than profile.json -- gitignored.
BROWSER_PROFILE_DIR = ROOT / ".browser_profile"

HANDLERS = {
    "greenhouse": greenhouse.fill,
    "lever": lever.fill,
    "ashby": ashby.fill,
    "workday": workday.fill,
    # Anything describe_check.detect_ats doesn't recognize as one of the
    # above (an in-house career page, iCIMS, SmartRecruiters, etc.) falls
    # back to the best-effort label-matching heuristic -- see
    # ats/generic.py's docstring for what it can and can't do.
    "other": generic.fill,
}

# ATS platforms where the description page's "Apply" button must be clicked
# to reveal the actual application form fields. Workday is handled
# separately (see the ats == "workday" branch in process_posting) since its
# real Apply button needs a force=True click plus a second click to pick
# "Apply Manually", not just "click anything with the text Apply".
NEEDS_APPLY_CLICK = {"lever", "ashby", "other"}

# The three ATSes with verified, hand-written selectors -- no heuristic
# guessing, no account-creation wall. Everything else (workday, other)
# is best-effort. --standard-only restricts a run to just these.
STANDARD_ATS = {"greenhouse", "lever", "ashby"}


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


def _run_handler(page, ats: str, posting_profile: dict, interactive: bool, listing_type: str) -> dict:
    """Calls the ATS handler's fill() and prints a filled/problems summary.
    Safe to call more than once against the same page (e.g. for a refill)
    -- fields just get overwritten with the same values.
    """
    handler = HANDLERS[ats]
    fields = handler(page, posting_profile, interactive, listing_type)
    filled = [k for k, v in fields.items() if v["status"] == "filled"]
    problems = {k: v["status"] for k, v in fields.items() if v["status"] not in ("filled", "skipped_no_value_in_profile")}
    print(f"  Filled: {', '.join(filled) if filled else '(none)'}")
    if problems:
        print(f"  Needs attention: {problems}")
    return fields


def _learn_from_page(page) -> None:
    """Scans the still-open page for any dropdown/radio/checkbox question
    with something already chosen (you filled it in by hand) and saves it
    to answers.json, same as if you'd been prompted for it interactively.
    Also reports everything it looked at but didn't save, and why -- so a
    field that "should" have been learned but wasn't is self-diagnosable
    instead of a guessing game.
    """
    captured, skipped = capture_answers(page)
    if captured:
        print(f"  Learned {len(captured)} answer(s), saved to answers.json:")
        for question, value in captured.items():
            print(f"    \"{question}\" -> {value!r}")
    else:
        print("  Nothing new found to learn.")
    if skipped:
        print(f"  Skipped {len(skipped)} field(s) this pass didn't save:")
        for item in skipped:
            print(f"    [{item['kind']}] \"{item['question']}\" -- {item['reason']}")
        print(
            "  Note: this only sees native <select>/radio/checkbox controls -- a react-select-style "
            "combobox or a fully custom-rendered widget (common on Workday and other heavily-custom "
            "sites) won't show up here at all, not even as skipped."
        )


def _save_log_and_screenshot(page, log: dict, run_dir: Path, index: int, ats: str) -> None:
    screenshot_path = run_dir / f"{index:02d}_{_slug(ats)}.png"
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
        log["screenshot"] = str(screenshot_path.relative_to(ROOT))
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] screenshot failed: {exc}")

    log_path = run_dir / f"{index:02d}_{_slug(ats)}.json"
    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"  Log: {log_path.relative_to(ROOT)}")


def process_posting(
    page, browser, url: str, ats: str, company: str, role: str, listing_type: str,
    profile: dict, run_dir: Path, index: int, prompt_missing: bool = True, headless: bool = False,
) -> tuple[dict, dict]:
    """Returns (log, posting_profile) -- posting_profile (with the tailored
    resume_path already applied) is handed back so a caller can trigger a
    refill against the same page without redoing resume tailoring.

    `browser` is only ever used for its .new_page() (by
    resume_library.select_or_generate, to render a tailored resume PDF on
    its own page) -- a Playwright BrowserContext works here just as well
    as a Browser, which is what run() actually passes now that it launches
    a persistent context instead of a throwaway browser.

    `prompt_missing` controls whether an unrecognized custom question
    (dropdown/radio/checkbox with no saved answer) stops to ask you in the
    terminal and saves the answer to answers.json -- this is independent of
    --headless (no browser window) so a headless run still pauses on stdin
    for anything it hasn't seen before, rather than silently leaving it
    blank.

    `headless` gates the Workday sign-in pause specifically (see below) --
    that step needs a visible browser window for you to actually type into,
    which --headless doesn't have.
    """
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
    posting_profile = profile

    try:
        page.goto(url, timeout=30000, wait_until="load")
        page.wait_for_timeout(1500)
        dismiss_cookie_banner(page)
        log["page_title"] = page.title()

        if detect_closed_or_expired(page):
            log["closed_or_expired"] = True
            print("  This posting looks closed/expired (page says so) -- skipping field fill.")
            _save_log_and_screenshot(page, log, run_dir, index, ats)
            return log, posting_profile

        # Grab the JD text before any Apply click navigates Lever/Ashby away
        # from the description page.
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

        if ats == "workday":
            reached_wall = workday.open_apply_flow(page)
            log["apply_click"] = "workday_wall_reached" if reached_wall else "workday_wall_not_found"
            if not reached_wall:
                print("  [warn] couldn't find Workday's Apply button automatically -- this company may have a customized flow.")
            if headless:
                print("  [warn] Workday requires signing in/creating an account by hand -- rerun this posting without --headless.")
                _save_log_and_screenshot(page, log, run_dir, index, ats)
                return log, posting_profile
            input("  Sign in or create an account on this company's Workday page, then press Enter here to continue...")

        elif ats in NEEDS_APPLY_CLICK:
            try:
                page.get_by_text("Apply", exact=False).first.click(timeout=5000)
                page.wait_for_timeout(2000)
                log["apply_click"] = "clicked"
            except Exception as exc:  # noqa: BLE001
                log["apply_click"] = f"failed: {exc}"
                print(f"  [warn] couldn't find/click an Apply button automatically: {exc}")
                print("  Navigate to the application form yourself, then come back to this terminal.")

        log["fields"] = _run_handler(page, ats, posting_profile, prompt_missing, listing_type)
        print("  Custom questions/EEOC fields are left for you. This script never clicks submit.")
    except Exception as exc:  # noqa: BLE001 -- a bad posting shouldn't kill the run
        log["error"] = str(exc)
        print(f"  [error] {exc}")

    _save_log_and_screenshot(page, log, run_dir, index, ats)
    return log, posting_profile


def run(
    limit: int, single_url: str | None, headless: bool, company: str = "", role: str = "", listing_type: str = "",
    standard_only: bool = False,
) -> None:
    profile = load_profile()

    if single_url:
        ats = detect_ats(single_url)
        if ats not in HANDLERS:
            print(f"'{ats}' isn't an autofill-supported ATS (only greenhouse/lever/ashby).")
            return
        targets = [{"url": single_url, "ats": ats, "company": company, "role": role, "listing_type": listing_type}]
    else:
        if not CANDIDATES_PATH.exists():
            print(f"No {CANDIDATES_PATH} found -- run run_pipeline.py first.")
            return
        rows = load_candidate_rows()
        targets = [
            row
            for row in rows
            if row["ats"] in HANDLERS
            and (not standard_only or row["ats"] in STANDARD_ATS)
            and row["status"] not in ("applied", "skip")
            and (not listing_type or row.get("listing_type") == listing_type)
        ][:limit]
        if not targets:
            scope = f" for listing_type={listing_type}" if listing_type else ""
            scope += " (standard ATS only)" if standard_only else ""
            print(f"Nothing left to review{scope} -- every matching row is already applied/skip.")
            return

    run_dir = LOGS_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Logging this run to {run_dir.relative_to(ROOT)}")

    with sync_playwright() as p:
        BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        context = p.chromium.launch_persistent_context(user_data_dir=str(BROWSER_PROFILE_DIR), headless=headless)
        # launch_persistent_context sometimes already hands back one open tab.
        page = context.pages[0] if context.pages else context.new_page()
        interactive = not headless
        for i, row in enumerate(targets, start=1):
            if interactive and not single_url:
                preview = f"[{i}/{len(targets)}] {row.get('company', '?')} -- {row.get('role', '?')}\n  {row['url']}"
                choice = input(f"{preview}\n  Process this one? [Enter=yes, s=skip]: ").strip().lower()
                if choice in ("s", "skip"):
                    update_status(row["url"], "skip")
                    print("  Skipped -- no time or API cost spent on it.\n")
                    continue

            log, posting_profile = process_posting(
                page, context, row["url"], row["ats"], row.get("company", ""), row.get("role", ""),
                row.get("listing_type", ""), profile, run_dir, i, headless=headless,
            )

            if single_url:
                if not headless:
                    while True:
                        choice = input("  Press Enter to close, 'r' to refill, or 'l' to learn your answers on this page: ").strip().lower()
                        if choice in ("r", "refill"):
                            print("  Refilling the page with the same information...")
                            log["fields"] = _run_handler(page, row["ats"], posting_profile, interactive, row.get("listing_type", ""))
                            _save_log_and_screenshot(page, log, run_dir, i, row["ats"])
                            continue
                        if choice in ("l", "learn"):
                            _learn_from_page(page)
                            continue
                        break
            elif headless:
                pass  # no live window to review; log + screenshot stand in for it
            else:
                while True:
                    status = input(
                        "  Status for this row [Enter=reviewed, 'applied', 'skip', 'refill', 'learn', or custom]: "
                    ).strip()
                    if status.lower() in ("r", "refill"):
                        print("  Refilling the page with the same information...")
                        log["fields"] = _run_handler(page, row["ats"], posting_profile, interactive, row.get("listing_type", ""))
                        _save_log_and_screenshot(page, log, run_dir, i, row["ats"])
                        continue
                    if status.lower() in ("l", "learn"):
                        _learn_from_page(page)
                        continue
                    update_status(row["url"], status or "reviewed")
                    break
        context.close()

    print(f"\nDone. Review screenshots + JSON logs in {run_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5, help="Max postings to open this run (default: 5)")
    parser.add_argument("--url", type=str, default=None, help="Spot-check a single posting URL instead of reading the CSV")
    parser.add_argument(
        "--headless", action="store_true",
        help="No visible browser window and no per-row skip/status prompts -- but still stops to ask "
             "about any unrecognized dropdown/radio/checkbox question and saves your answer to answers.json",
    )
    parser.add_argument("--company", type=str, default="", help="Company name, only used with --url (helps resume tailoring/reuse matching)")
    parser.add_argument("--role", type=str, default="", help="Role title, only used with --url (helps resume tailoring/reuse matching)")
    parser.add_argument(
        "--listing-type", type=str, default="", choices=["", "internship", "new_grad"],
        help=(
            "internship or new_grad. With --url, picks graduation_date vs "
            "graduation_date_internship for that one posting. Without --url, "
            "also filters output/candidates.csv down to only rows of that "
            "type this run (omit to process both)."
        ),
    )
    parser.add_argument(
        "--standard-only", action="store_true",
        help="Only process greenhouse/lever/ashby rows -- skips workday and other "
             "(nonstandard, best-effort/heuristic) rows this run. No effect with --url.",
    )
    args = parser.parse_args()
    run(args.limit, args.url, args.headless, args.company, args.role, args.listing_type, args.standard_only)
