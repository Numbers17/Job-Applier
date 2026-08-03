"""Visits each candidate posting's actual application page and soft-flags
grad-degree-required or hardware-management language that isn't visible
from the README table alone (only Company/Role/Location/Link/Date are).

These are heuristic text matches, not guaranteed accurate -- treat flags as
a hint to double-check during review, not as a hard filter.
"""
import re
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

REQUEST_DELAY_SECONDS = 1.5

GRAD_REQUIRED_PHRASES = [
    "master's degree required",
    "masters degree required",
    "phd required",
    "ph.d. required",
    "doctorate required",
    "doctoral degree required",
    "graduate degree required",
    "advanced degree required",
    "currently pursuing a master's or phd",
    "currently pursuing a phd",
    "must be pursuing a master's",
    "must be pursuing a phd",
]

HARDWARE_MGMT_PHRASES = [
    "hardware inventory",
    "hardware procurement",
    "hardware lifecycle",
    "manage hardware",
    "hardware asset management",
    "hardware deployment and management",
]


def detect_ats(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if "greenhouse.io" in domain:
        return "greenhouse"
    if "lever.co" in domain:
        return "lever"
    if "ashbyhq.com" in domain:
        return "ashby"
    if "myworkdayjobs.com" in domain or "workday.com" in domain:
        return "workday"
    return "other"


def flag_grad_required(text: str) -> bool:
    text_low = text.lower()
    if any(phrase in text_low for phrase in GRAD_REQUIRED_PHRASES):
        return True
    # generic: "master's"/"phd" tied to required/must/only, with no mention
    # of "bachelor" anywhere (i.e. not offered as an alternative)
    if re.search(r"(master'?s|ph\.?d)[^.]{0,30}(required|only|must)", text_low):
        if "bachelor" not in text_low:
            return True
    return False


def flag_hardware_mgmt(text: str) -> bool:
    text_low = text.lower()
    return any(phrase in text_low for phrase in HARDWARE_MGMT_PHRASES)


def check_listings(listings: list[dict], limit: int = 40) -> list[dict]:
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for i, row in enumerate(listings[:limit]):
            row = dict(row)
            row["ats"] = detect_ats(row["url"])
            try:
                page.goto(row["url"], timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(1000)  # let JS-rendered ATS pages (Ashby) settle
                text = page.inner_text("body")
                row["grad_flag"] = flag_grad_required(text)
                row["hardware_flag"] = flag_hardware_mgmt(text)
            except Exception as exc:  # noqa: BLE001 -- a bad posting shouldn't kill the run
                print(f"  [warn] could not check {row['url']}: {exc}", file=sys.stderr)
                row["grad_flag"] = False
                row["hardware_flag"] = False
            results.append(row)
            time.sleep(REQUEST_DELAY_SECONDS)
        browser.close()

    # remaining rows beyond the limit still get an ats tag, just no description check
    for row in listings[limit:]:
        row = dict(row)
        row["ats"] = detect_ats(row["url"])
        row["grad_flag"] = False
        row["hardware_flag"] = False
        results.append(row)

    return results


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    from fetch_listings import get_listings
    from filter_listings import filter_and_sort

    filtered = filter_and_sort(get_listings())
    checked = check_listings(filtered, limit=10)
    for row in checked[:10]:
        print(row["company"], "|", row["role"], "| grad:", row["grad_flag"], "| hw:", row["hardware_flag"])
