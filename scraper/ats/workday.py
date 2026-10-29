"""Navigation + field-fill for jobs on *.myworkdayjobs.com.

Every company on Workday runs the same underlying platform, so the Apply
flow uses stable data-automation-id attributes regardless of company --
unlike a fully custom "other" career page. Verified live against a real
posting (acrisure.wd1.myworkdayjobs.com): the real Apply button
(data-automation-id="adventureButton") needs a force=True click (an
overlay intercepts a plain click), which opens a modal offering "Autofill
with Resume" / "Apply Manually" / "Use My Last Application". Whichever
option is picked, the very next step is "Create Account/Sign In" for that
company's Workday tenant -- there's no guest-checkout path, account
creation is unavoidable.

This module only handles getting you TO that wall reliably. Signing in or
creating the account is left to you -- autofill.py pauses there and waits
for you to confirm before running the same heuristic field-fill used for
other nonstandard sites (ats/generic.py) on whatever page you land on
afterward (typically Workday's "My Information" step, which uses regular
native inputs the heuristic can map).
"""
from . import generic

APPLY_BUTTON = '[data-automation-id="adventureButton"]'
APPLY_MANUALLY_BUTTON = '[data-automation-id="applyManually"]'


def open_apply_flow(page) -> bool:
    """Clicks through to the Create Account/Sign In wall. Returns True if
    both expected buttons were found and clicked, False otherwise (a
    company may have customized their Workday flow) -- caller should let
    you navigate manually either way.
    """
    try:
        page.locator(APPLY_BUTTON).first.click(timeout=5000, force=True)
        page.wait_for_timeout(1500)
        page.locator(APPLY_MANUALLY_BUTTON).first.click(timeout=5000, force=True)
        page.wait_for_timeout(1500)
        return True
    except Exception:  # noqa: BLE001
        return False


def fill(page, profile: dict, interactive: bool = True, listing_type: str = "") -> dict:
    """Same best-effort label-matching heuristic as ats/generic.py --
    Workday's "My Information" fields (name, email, phone, address, etc.)
    are regular native inputs once you're past the sign-in wall."""
    return generic.fill(page, profile, interactive, listing_type)
