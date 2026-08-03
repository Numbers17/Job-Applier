"""Shared helpers for the per-ATS field-fill modules.

Every helper writes into a shared `results` dict of
{label: {"value": value, "selector": selector, "status": status}}
so the caller can log exactly what was attempted and what happened --
not just a bare "it worked" label.
"""
import re

from answers_store import get_answer, save_answer

STATUS_FILLED = "filled"
STATUS_SKIPPED_NO_VALUE = "skipped_no_value_in_profile"
STATUS_FIELD_NOT_FOUND = "field_not_found_on_page"
STATUS_ERROR = "error"
STATUS_UNANSWERED = "unanswered_needs_review"

REFERRAL_QUESTION_RE = re.compile(r"how (did |do )?you hear", re.IGNORECASE)


def _record(results: dict, label: str, value, selector: str, status: str) -> None:
    results[label] = {"value": value, "selector": selector, "status": status}


def try_fill(page, selector: str, value: str, label: str, results: dict) -> None:
    if not value:
        _record(results, label, value, selector, STATUS_SKIPPED_NO_VALUE)
        return
    try:
        loc = page.locator(selector).first
        if not loc.count():
            _record(results, label, value, selector, STATUS_FIELD_NOT_FOUND)
            return
        loc.fill(value)
        _record(results, label, value, selector, STATUS_FILLED)
    except Exception as exc:  # noqa: BLE001 -- best effort, form structures vary per posting
        _record(results, label, value, selector, f"{STATUS_ERROR}: {exc}")


def try_upload(page, selector: str, path: str, label: str, results: dict) -> None:
    try:
        loc = page.locator(selector).first
        if not loc.count():
            _record(results, label, path, selector, STATUS_FIELD_NOT_FOUND)
            return
        loc.set_input_files(path)
        _record(results, label, path, selector, STATUS_FILLED)
    except Exception as exc:  # noqa: BLE001
        _record(results, label, path, selector, f"{STATUS_ERROR}: {exc}")


def try_combobox(page, selector: str, value: str, label: str, results: dict) -> None:
    """For react-select-style comboboxes (e.g. Greenhouse's school/degree
    fields): click, type to filter, then press Enter to accept the
    top (already-highlighted) matching option. The underlying <input>'s
    .value stays empty even on success -- the selection is tracked in
    component state, not the input -- so this can't read the value back
    to confirm; it records "filled" once Enter is sent without error.
    """
    if not value:
        _record(results, label, value, selector, STATUS_SKIPPED_NO_VALUE)
        return
    try:
        loc = page.locator(selector).first
        if not loc.count():
            _record(results, label, value, selector, STATUS_FIELD_NOT_FOUND)
            return
        loc.click()
        loc.type(value, delay=20)
        page.wait_for_timeout(500)  # let the filtered option list render
        page.keyboard.press("Enter")
        _record(results, label, value, selector, STATUS_FILLED)
    except Exception as exc:  # noqa: BLE001
        _record(results, label, value, selector, f"{STATUS_ERROR}: {exc}")


def try_fill_by_aria_label(page, keyword: str, value: str, label: str, results: dict) -> None:
    selector = f'input[aria-label*="{keyword}" i]'
    if not value:
        _record(results, label, value, selector, STATUS_SKIPPED_NO_VALUE)
        return
    try:
        loc = page.locator(selector).first
        if not loc.count():
            _record(results, label, value, selector, STATUS_FIELD_NOT_FOUND)
            return
        loc.fill(value)
        _record(results, label, value, selector, STATUS_FILLED)
    except Exception as exc:  # noqa: BLE001
        _record(results, label, value, selector, f"{STATUS_ERROR}: {exc}")


def try_fill_by_label(page, label_text: str, value: str, label: str, results: dict) -> None:
    """For forms with proper <label for="..."> associations (e.g. Ashby's
    GitHub/LinkedIn questions), matched via Playwright's accessible-label
    lookup rather than a guessed selector.
    """
    selector = f'label="{label_text}"'  # descriptive only, for the log
    if not value:
        _record(results, label, value, selector, STATUS_SKIPPED_NO_VALUE)
        return
    try:
        loc = page.get_by_label(label_text, exact=False).first
        if not loc.count():
            _record(results, label, value, selector, STATUS_FIELD_NOT_FOUND)
            return
        loc.fill(value)
        _record(results, label, value, selector, STATUS_FILLED)
    except Exception as exc:  # noqa: BLE001
        _record(results, label, value, selector, f"{STATUS_ERROR}: {exc}")


def clean_label(text: str) -> str:
    text = (text or "").replace("✱", "").replace("*", "")
    return re.sub(r"\s+", " ", text).strip()


def prompt_choice(question: str, options: list[str]) -> str:
    print(f"\n  [needs your input] {question}")
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt}")
    while True:
        raw = input("  Enter a number, or type your own answer: ").strip()
        if not raw:
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        return raw


def prompt_multi_choice(question: str, options: list[str]) -> list[str]:
    print(f"\n  [needs your input] {question} (multi-select)")
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt}")
    raw = input("  Enter comma-separated numbers (blank for none): ").strip()
    chosen = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= len(options):
            chosen.append(options[int(part) - 1])
    return chosen


def _best_match(value: str, options: list[str]) -> tuple[str | None, str | None]:
    """Returns (matched_option, error). Never guesses between multiple
    equally-plausible options (e.g. "University of Washington" against a
    list split by campus: "... - Bothell" / "... - Seattle" / "... - Tacoma")
    -- an ambiguous match is reported as an error rather than silently
    picking the alphabetically-first one, since a wrong campus/degree/etc.
    selection is worse than one that's flagged for you to fix.
    """
    exact = [o for o in options if o.lower() == value.lower()]
    if exact:
        return exact[0], None

    value_low = value.lower()
    starts_with = [o for o in options if o.lower().startswith(value_low)]
    candidates = starts_with or [o for o in options if value_low in o.lower() or o.lower() in value_low]

    if len(candidates) == 1:
        return candidates[0], None
    if len(candidates) > 1:
        return None, f"ambiguous match for '{value}': {candidates[:8]} -- save a more specific answer"
    return None, f"no option matched '{value}' (available: {options[:8]})"


def resolve_select(page, select_locator, question: str, label: str, results: dict, interactive: bool, profile: dict) -> None:
    """Handles a native <select> whose meaning isn't known ahead of time
    (e.g. a company-specific Lever question). Reuses a previously-saved
    answer for the same question text if one exists; otherwise prompts
    interactively (headed runs only) and saves the choice for next time.
    """
    question = clean_label(question)
    selector = f'select (question: "{question}")'
    try:
        options = [
            o.strip()
            for o in select_locator.locator("option").all_text_contents()
            if o.strip() and not re.match(r"^(select|choose|please select|click here)", o.strip(), re.I)
        ]
    except Exception as exc:  # noqa: BLE001
        _record(results, label, None, selector, f"{STATUS_ERROR}: {exc}")
        return

    saved = get_answer(question)
    if saved:
        value = saved["value"]
    elif REFERRAL_QUESTION_RE.search(question) and profile.get("default_referral_source"):
        value = profile["default_referral_source"]
        save_answer(question, "select", value)
    elif interactive:
        value = prompt_choice(question, options)
        save_answer(question, "select", value)
    else:
        _record(results, label, None, selector, STATUS_UNANSWERED)
        return

    try:
        select_locator.select_option(label=value)
        _record(results, label, value, selector, STATUS_FILLED)
        return
    except Exception:
        pass

    match, error = _best_match(value, options)
    if error:
        _record(results, label, value, selector, f"{STATUS_ERROR}: {error}")
        return
    try:
        select_locator.select_option(label=match)
        _record(results, label, value, selector, STATUS_FILLED)
    except Exception as exc:  # noqa: BLE001
        _record(results, label, value, selector, f"{STATUS_ERROR}: {exc}")


def resolve_radio_group(page, radios_locator, question: str, label: str, results: dict, interactive: bool) -> None:
    question = clean_label(question)
    selector = f'radio group (question: "{question}")'
    count = radios_locator.count()
    options = []
    for i in range(count):
        r = radios_locator.nth(i)
        rid = r.get_attribute("id")
        opt_label = ""
        if rid:
            lab = page.locator(f'label[for="{rid}"]')
            if lab.count():
                opt_label = clean_label(lab.first.text_content())
        options.append(opt_label or r.get_attribute("value") or f"option {i + 1}")

    saved = get_answer(question)
    if saved:
        value = saved["value"]
    elif interactive:
        value = prompt_choice(question, options)
        save_answer(question, "radio", value)
    else:
        _record(results, label, None, selector, STATUS_UNANSWERED)
        return

    match, error = _best_match(value, options)
    if error:
        _record(results, label, value, selector, f"{STATUS_ERROR}: {error}")
        return
    try:
        radios_locator.nth(options.index(match)).check()
        _record(results, label, value, selector, STATUS_FILLED)
    except Exception as exc:  # noqa: BLE001
        _record(results, label, value, selector, f"{STATUS_ERROR}: {exc}")


def resolve_checkbox_group(page, boxes_locator, question: str, label: str, results: dict, interactive: bool) -> None:
    question = clean_label(question)
    selector = f'checkbox group (question: "{question}")'
    count = boxes_locator.count()
    options = []
    for i in range(count):
        cb = boxes_locator.nth(i)
        cid = cb.get_attribute("id")
        opt_label = ""
        if cid:
            lab = page.locator(f'label[for="{cid}"]')
            if lab.count():
                opt_label = clean_label(lab.first.text_content())
        options.append(opt_label or cb.get_attribute("value") or f"option {i + 1}")

    saved = get_answer(question)
    if saved:
        values = saved["value"] if isinstance(saved["value"], list) else [saved["value"]]
    elif interactive:
        values = prompt_multi_choice(question, options)
        save_answer(question, "checkbox_group", values)
    else:
        _record(results, label, None, selector, STATUS_UNANSWERED)
        return

    try:
        checked = []
        skipped = []
        for v in values:
            match, error = _best_match(v, options)
            if match:
                boxes_locator.nth(options.index(match)).check()
                checked.append(match)
            else:
                skipped.append(f"{v} ({error})")
        if skipped:
            _record(results, label, checked, selector, f"{STATUS_ERROR}: couldn't resolve {skipped}")
            return
        status = STATUS_FILLED if checked else "no_matching_options"
        _record(results, label, checked, selector, status)
    except Exception as exc:  # noqa: BLE001
        _record(results, label, values, selector, f"{STATUS_ERROR}: {exc}")


CLOSED_PHRASES = [
    "no longer open",
    "no longer accepting applications",
    "position has been filled",
    "posting has expired",
    "this job is no longer available",
    "job you are looking for is no longer",
]


def detect_closed_or_expired(page) -> bool:
    try:
        text = page.inner_text("body").lower()
    except Exception:  # noqa: BLE001
        return False
    return any(phrase in text for phrase in CLOSED_PHRASES)


def dismiss_cookie_banner(page) -> None:
    for text in ["Deny", "Decline", "Reject All", "Reject"]:
        try:
            btn = page.get_by_text(text, exact=False).first
            if btn.is_visible(timeout=500):
                btn.click(timeout=1000)
                return
        except Exception:  # noqa: BLE001
            pass
