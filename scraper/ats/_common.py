"""Shared helpers for the per-ATS field-fill modules.

Every helper writes into a shared `results` dict of
{label: {"value": value, "selector": selector, "status": status}}
so the caller can log exactly what was attempted and what happened --
not just a bare "it worked" label.
"""
import re

from answers_store import find_similar, get_answer, save_answer

STATUS_FILLED = "filled"
STATUS_SKIPPED_NO_VALUE = "skipped_no_value_in_profile"
STATUS_FIELD_NOT_FOUND = "field_not_found_on_page"
STATUS_ERROR = "error"
STATUS_UNANSWERED = "unanswered_needs_review"

REFERRAL_QUESTION_RE = re.compile(r"how (did |do )?you hear", re.IGNORECASE)
GRADUATION_YEAR_QUESTION_RE = re.compile(r"graduation year|graduation date", re.IGNORECASE)
HIGH_SCHOOL_RE = re.compile(r"high school", re.IGNORECASE)


def is_college_graduation_question(question: str) -> bool:
    """True for "intended/expected graduation year" style questions, but
    not "Year of High School Graduation" -- that's a separate, unrelated
    question that should go through the normal saved-answer/prompt flow.
    """
    return bool(GRADUATION_YEAR_QUESTION_RE.search(question)) and not HIGH_SCHOOL_RE.search(question)


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


def _get_or_prompt(question: str, field_type: str, interactive: bool, prompt_fn):
    """Cache lookup with a fuzzy-match fallback. An exact-text cache hit is
    used immediately (handled by the caller before this runs). Otherwise,
    if a similarly-worded question has been answered before, you're shown
    it and asked once whether to reuse that answer -- confirmed, never
    auto-applied -- before falling back to a fresh prompt_fn() call.
    Returns None only when not interactive and nothing resolves.
    """
    if not interactive:
        return None
    similar, score = find_similar(question)
    if similar:
        print(f"\n  [similar question already answered] \"{similar['question']}\" -> {similar['value']!r} (similarity {score:.2f})")
        reuse = input(f"  Reuse that answer for: \"{question}\"? [Enter=yes, n=no]: ").strip().lower()
        if reuse not in ("n", "no"):
            save_answer(question, field_type, similar["value"])
            return similar["value"]
    value = prompt_fn()
    save_answer(question, field_type, value)
    return value


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


def resolve_select(
    page, select_locator, question: str, label: str, results: dict, interactive: bool, profile: dict,
    listing_type: str = "",
) -> None:
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

    # Graduation year genuinely differs by whether this is an internship
    # posting (may still be enrolled after) vs. a new-grad posting (expects
    # to have graduated) -- computed fresh from profile.json every time and
    # never cached via save_answer/get_answer, since a single cached value
    # would be wrong for one context or the other.
    if is_college_graduation_question(question):
        field = "graduation_date_internship" if listing_type == "internship" else "graduation_date"
        grad_date = profile.get(field) or profile.get("graduation_date")
        if grad_date:
            year = grad_date[:4]
            match, error = _best_match(year, options)
            target = match or year
            try:
                select_locator.select_option(label=target)
                _record(results, label, year, selector, STATUS_FILLED)
            except Exception as exc:  # noqa: BLE001
                _record(results, label, year, selector, f"{STATUS_ERROR}: {exc}")
            return

    saved = get_answer(question)
    if saved:
        value = saved["value"]
    elif REFERRAL_QUESTION_RE.search(question) and profile.get("default_referral_source"):
        value = profile["default_referral_source"]
        save_answer(question, "select", value)
    else:
        value = _get_or_prompt(question, "select", interactive, lambda: prompt_choice(question, options))
        if value is None:
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
    else:
        value = _get_or_prompt(question, "radio", interactive, lambda: prompt_choice(question, options))
        if value is None:
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
    else:
        values = _get_or_prompt(question, "checkbox_group", interactive, lambda: prompt_multi_choice(question, options))
        if values is None:
            _record(results, label, None, selector, STATUS_UNANSWERED)
            return
        if not isinstance(values, list):
            values = [values]

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


def _capture_label_for(page, locator) -> str:
    """Question-label lookup for capture_answers(), combining every
    strategy the individual ATS modules use (aria-label, fieldset/legend,
    label[for=id], an ancestor <label>, an aria-labelledby'd group, and
    Lever's .application-question/.application-label convention) since
    this runs against whatever site you're currently on, not one known
    ATS's fixed DOM shape.

    fieldset/legend is checked before label[for=id] deliberately: for a
    radio/checkbox passed in as `locator.first`, that input's own id maps
    to its OPTION's label (e.g. "Yes"), not the question -- the fieldset
    wrapping the whole group is the question. For a <select>, its own id
    maps to the correct whole-field label either way, so the order doesn't
    matter there.
    """
    try:
        text = locator.evaluate(
            """
            el => {
                const aria = el.getAttribute('aria-label');
                if (aria) return aria;
                const fieldset = el.closest('fieldset');
                if (fieldset) {
                    const legend = fieldset.querySelector('legend');
                    if (legend) return legend.textContent;
                }
                if (el.id) {
                    const lab = document.querySelector(`label[for="${el.id}"]`);
                    if (lab) return lab.textContent;
                }
                const wrapping = el.closest('label');
                if (wrapping) return wrapping.textContent;
                const group = el.closest('[role="group"]');
                if (group) {
                    const labelledBy = group.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        const labEl = document.getElementById(labelledBy.split(' ')[0]);
                        if (labEl) return labEl.textContent;
                    }
                    const lab = group.querySelector('label');
                    if (lab) return lab.textContent;
                }
                const appQuestion = el.closest('.application-question');
                if (appQuestion) {
                    const lab = appQuestion.querySelector('.application-label');
                    if (lab) return lab.textContent;
                }
                return '';
            }
            """
        )
        return clean_label(text)
    except Exception:  # noqa: BLE001
        return ""


_PLACEHOLDER_OPTION_RE = re.compile(r"^(select|choose|please select|click here)", re.IGNORECASE)

_TEXT_LIKE_SELECTOR = (
    'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])'
    ':not([type="file"]):not([type="submit"]):not([type="button"]), textarea'
)


def capture_answers(page) -> tuple[dict, list[dict]]:
    """Scans the current page for <select>/radio-group/checkbox-group
    questions that already have something chosen, and saves each one into
    answers.json -- meant to be called after you've finished filling out
    an application (by hand, or after an automated fill left something
    for you to complete yourself) so it becomes reusable on future
    postings, the same as an interactively-prompted answer.

    Returns (captured, skipped). `captured` is {question: value} for
    everything saved. `skipped` is a list of {"kind", "question", "reason"}
    for everything this pass looked at but didn't save -- including
    text/textarea fields with something typed in, which this never
    captures (answers.json only stores select/radio/checkbox_group
    answers; free-text custom questions are a fundamentally different,
    less reusable kind of answer and are out of scope here). Surface
    `skipped` to the user so "why didn't X get learned" is self-diagnosable
    instead of a guessing game -- this only sees native HTML controls, so
    a react-select-style combobox (e.g. Greenhouse's school/degree/major)
    or a fully custom-rendered widget with no underlying
    <select>/input[type=radio|checkbox] (common on Workday and other
    heavily-custom sites) won't show up here at all, not even as a skip.

    Graduation-year questions are deliberately skipped -- that answer is
    context-dependent (internship vs. new-grad) and is never cached, by
    design (see resolve_select). A checkbox group with nothing checked is
    also skipped, since there's no way to tell "you intentionally left
    this empty" apart from "you never got to it".
    """
    captured: dict = {}
    skipped: list[dict] = []

    selects = page.locator("select")
    for i in range(selects.count()):
        sel = selects.nth(i)
        question = _capture_label_for(page, sel)
        if not question:
            skipped.append({"kind": "select", "question": "(unlabeled)", "reason": "couldn't find a question label for this field"})
            continue
        if is_college_graduation_question(question):
            skipped.append({"kind": "select", "question": question, "reason": "graduation-year answers are never cached (context-dependent)"})
            continue
        try:
            value = sel.evaluate(
                "el => el.selectedIndex >= 0 ? el.options[el.selectedIndex].textContent : ''"
            )
        except Exception:  # noqa: BLE001
            skipped.append({"kind": "select", "question": question, "reason": "couldn't read the selected option"})
            continue
        value = clean_label(value)
        if not value or _PLACEHOLDER_OPTION_RE.match(value):
            skipped.append({"kind": "select", "question": question, "reason": "still showing the placeholder option (nothing chosen)"})
            continue
        save_answer(question, "select", value)
        captured[question] = value

    radio_names = page.eval_on_selector_all("input[type=radio]", "els => [...new Set(els.map(e => e.name).filter(Boolean))]")
    for name in radio_names:
        group = page.locator(f'input[type=radio][name="{name}"]')
        # :checked must be part of the same compound selector -- chaining
        # it as group.locator(":checked") searches group's DESCENDANTS
        # (radio inputs have none), not the group elements themselves.
        checked = page.locator(f'input[type=radio][name="{name}"]:checked')
        question = _capture_label_for(page, group.first) or "(unlabeled)"
        if not checked.count():
            skipped.append({"kind": "radio", "question": question, "reason": "nothing selected in this group"})
            continue
        if question == "(unlabeled)":
            skipped.append({"kind": "radio", "question": question, "reason": "couldn't find a question label for this field"})
            continue
        rid = checked.first.get_attribute("id")
        value = ""
        if rid:
            lab = page.locator(f'label[for="{rid}"]')
            if lab.count():
                value = clean_label(lab.first.text_content())
        value = value or checked.first.get_attribute("value") or ""
        if not value:
            skipped.append({"kind": "radio", "question": question, "reason": "couldn't determine the selected option's label"})
            continue
        save_answer(question, "radio", value)
        captured[question] = value

    checkbox_names = page.eval_on_selector_all("input[type=checkbox]", "els => [...new Set(els.map(e => e.name).filter(Boolean))]")
    for name in checkbox_names:
        group = page.locator(f'input[type=checkbox][name="{name}"]')
        checked = page.locator(f'input[type=checkbox][name="{name}"]:checked')
        count = checked.count()
        question = _capture_label_for(page, group.first) or "(unlabeled)"
        if not count:
            skipped.append({"kind": "checkbox_group", "question": question, "reason": "nothing checked in this group"})
            continue
        if question == "(unlabeled)":
            skipped.append({"kind": "checkbox_group", "question": question, "reason": "couldn't find a question label for this field"})
            continue
        values = []
        for i in range(count):
            cb = checked.nth(i)
            cid = cb.get_attribute("id")
            v = ""
            if cid:
                lab = page.locator(f'label[for="{cid}"]')
                if lab.count():
                    v = clean_label(lab.first.text_content())
            values.append(v or cb.get_attribute("value") or "")
        values = [v for v in values if v]
        if not values:
            skipped.append({"kind": "checkbox_group", "question": question, "reason": "couldn't determine the checked options' labels"})
            continue
        save_answer(question, "checkbox_group", values)
        captured[question] = values

    text_fields = page.locator(_TEXT_LIKE_SELECTOR)
    for i in range(text_fields.count()):
        el = text_fields.nth(i)
        try:
            value = el.input_value()
        except Exception:  # noqa: BLE001
            continue
        if not value.strip():
            continue
        question = _capture_label_for(page, el) or "(unlabeled)"
        skipped.append({
            "kind": "text",
            "question": question,
            "reason": "free-text fields aren't captured (answers.json only stores select/radio/checkbox answers)",
        })

    return captured, skipped


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
