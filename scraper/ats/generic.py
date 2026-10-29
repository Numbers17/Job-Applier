"""Best-effort field-fill for postings on sites that aren't Greenhouse,
Lever, or Ashby -- an in-house company career page, or another ATS
(Workday, iCIMS, SmartRecruiters, etc.) with no fixed selectors we can
hard-code ahead of time.

Approach: enumerate every visible text-like <input>/<textarea>/<select>/
file-upload on the page, read each one's associated label/placeholder/
aria-label text, and fuzzy-match that text against a small set of known
profile fields (name, email, phone, links, school, degree, major, resume).
A confident match gets filled from profile.json; everything else --
custom questions, EEOC, work authorization, anything this heuristic can't
place -- is left untouched and recorded as "unmapped_needs_manual_fill" so
it shows up in the run's log/screenshot for you to fill by hand, same as
any other "needs attention" field elsewhere in this project.

This is inherently less reliable than the verified Greenhouse/Lever/Ashby
handlers: there's no fixed DOM to test against, so it can miss fields
entirely (unlabeled inputs, custom-styled non-native controls) or, more
importantly, could rename/mis-map a field that's superficially labeled
like a known one but means something else on a given company's form --
always check the screenshot before trusting a nonstandard-site fill.
Multi-page wizards (common on Workday), logins, and CAPTCHAs aren't
handled at all -- the run will just fill whatever's on the first page it
lands on.
"""
import re

from ._common import STATUS_ERROR, STATUS_FILLED, clean_label

STATUS_UNMAPPED = "unmapped_needs_manual_fill"

# (field label for the log, regex against the cleaned question/label text,
# profile-value getter). Order matters only in that first_name/last_name
# are checked before the broader "name" pattern below.
_FIELD_PATTERNS = [
    ("first_name", re.compile(r"first\s*name", re.I), lambda p: p.get("first_name", "")),
    ("last_name", re.compile(r"last\s*name|surname|family\s*name", re.I), lambda p: p.get("last_name", "")),
    ("email", re.compile(r"e-?mail", re.I), lambda p: p.get("email", "")),
    ("phone", re.compile(r"phone|mobile\s*number|telephone", re.I), lambda p: p.get("phone", "")),
    ("linkedin", re.compile(r"linkedin", re.I), lambda p: p.get("linkedin", "")),
    ("github", re.compile(r"github", re.I), lambda p: p.get("github", "")),
    ("school", re.compile(r"school|university|college", re.I), lambda p: p.get("school", "")),
    ("major", re.compile(r"major|field\s*of\s*study", re.I), lambda p: p.get("major", "")),
    ("degree", re.compile(r"degree|education\s*level", re.I), lambda p: p.get("degree", "")),
    ("location", re.compile(r"^(location|city|current\s*location)$", re.I), lambda p: p.get("location", "")),
    (
        "full_name",
        re.compile(r"^(full\s*name|your\s*name|applicant\s*name|name)$", re.I),
        lambda p: f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
    ),
]

_RESUME_LABEL_RE = re.compile(r"resum|cv\b", re.I)
_COVER_LETTER_LABEL_RE = re.compile(r"cover\s*letter", re.I)

_TEXT_LIKE_SELECTOR = (
    'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])'
    ':not([type="file"]):not([type="submit"]):not([type="button"]), textarea'
)


def _label_for(page, locator) -> str:
    """Tries, in order: aria-label, an associated <label for="id">, an
    ancestor <label> wrapping the field, then the placeholder text.
    """
    try:
        text = locator.evaluate(
            """
            el => {
                const aria = el.getAttribute('aria-label');
                if (aria) return aria;
                if (el.id) {
                    const lab = document.querySelector(`label[for="${el.id}"]`);
                    if (lab) return lab.textContent;
                }
                const wrapping = el.closest('label');
                if (wrapping) return wrapping.textContent;
                return el.getAttribute('placeholder') || '';
            }
            """
        )
        return clean_label(text)
    except Exception:  # noqa: BLE001
        return ""


def _match_field(label: str, profile: dict) -> tuple[str, str] | tuple[None, None]:
    for field_name, pattern, get_value in _FIELD_PATTERNS:
        if pattern.search(label):
            value = get_value(profile)
            if value:
                return field_name, value
    return None, None


def _record(results: dict, key: str, value, selector: str, status: str) -> None:
    results[key] = {"value": value, "selector": selector, "status": status}


def fill(page, profile: dict, interactive: bool = True, listing_type: str = "") -> dict:
    """Returns {field_label: {value, selector, status}} for every field
    attempted or flagged. `interactive`/`listing_type` are accepted for a
    consistent call signature across ATS handlers but unused here -- this
    handler only fills fields it can confidently map to a known
    profile.json value; it never prompts, since it has no way to know
    what kind of question an unmapped field even is.
    """
    results: dict = {}

    # Resume (and, separately, a cover-letter upload if the profile ever
    # gains one -- not filled today, just not mis-flagged as unmapped).
    file_inputs = page.locator('input[type="file"]')
    for i in range(file_inputs.count()):
        el = file_inputs.nth(i)
        label = _label_for(page, el)
        key = f"file::{label or i}"
        if _COVER_LETTER_LABEL_RE.search(label):
            continue
        if not label or _RESUME_LABEL_RE.search(label):
            try:
                el.set_input_files(profile["resume_path"])
                _record(results, key, profile["resume_path"], "input[type=file] (heuristic)", STATUS_FILLED)
            except Exception as exc:  # noqa: BLE001
                _record(results, key, profile["resume_path"], "input[type=file] (heuristic)", f"{STATUS_ERROR}: {exc}")
        else:
            _record(results, key, None, "input[type=file] (heuristic)", STATUS_UNMAPPED)

    # Text-like inputs and textareas.
    fields = page.locator(_TEXT_LIKE_SELECTOR)
    for i in range(fields.count()):
        el = fields.nth(i)
        label = _label_for(page, el)
        if not label:
            continue
        field_name, value = _match_field(label, profile)
        key = f"text::{label}"
        selector = f'heuristic match: "{label}"'
        if not field_name:
            _record(results, key, None, selector, STATUS_UNMAPPED)
            continue
        try:
            el.fill(value)
            _record(results, f"{field_name} ({key})", value, selector, STATUS_FILLED)
        except Exception as exc:  # noqa: BLE001
            _record(results, f"{field_name} ({key})", value, selector, f"{STATUS_ERROR}: {exc}")

    # Native <select> dropdowns mapped to a known profile field (school,
    # degree, major, etc.) -- anything else (EEOC, work auth, custom
    # questions) is left for the interactive resolver work already done
    # for Greenhouse/Lever/Ashby, not this heuristic pass.
    selects = page.locator("select")
    for i in range(selects.count()):
        sel = selects.nth(i)
        label = _label_for(page, sel)
        if not label:
            continue
        field_name, value = _match_field(label, profile)
        key = f"select::{label}"
        selector = f'heuristic match: "{label}"'
        if not field_name:
            _record(results, key, None, selector, STATUS_UNMAPPED)
            continue
        try:
            sel.select_option(label=value)
            _record(results, f"{field_name} ({key})", value, selector, STATUS_FILLED)
        except Exception as exc:  # noqa: BLE001 -- value doesn't match any option on this page's <select>
            _record(results, f"{field_name} ({key})", value, selector, f"{STATUS_ERROR}: {exc}")

    return results
