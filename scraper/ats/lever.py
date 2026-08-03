"""Field-fill logic for jobs.lever.co application forms.

Verified live against a real posting. The application form lives at
{job_url}/apply and is reached by clicking the "Apply" button on the job
description page -- autofill.py handles that navigation before calling
fill() here.

Beyond the standard name/email/phone/links/resume fields, Lever forms
commonly include company-specific <select>/radio/checkbox questions
(school, graduation year, EEOC, "how did you hear about us", etc.) with
no fixed selector -- these are resolved generically via
_common.resolve_select/resolve_radio_group/resolve_checkbox_group, which
reuse a previously-given answer for the same question text or (in
interactive/headed runs) ask you once and remember it in answers.json.
"""
from ._common import (
    clean_label,
    resolve_checkbox_group,
    resolve_radio_group,
    resolve_select,
    try_fill,
    try_upload,
)


def _label_for(page, locator) -> str:
    try:
        text = locator.evaluate(
            "el => el.closest('.application-question')?.querySelector('.application-label')?.textContent || ''"
        )
        return clean_label(text)
    except Exception:  # noqa: BLE001
        return ""


def fill(page, profile: dict, interactive: bool = True) -> dict:
    """Returns {field_label: {value, selector, status}} for every field
    attempted, so the caller can log exactly what was tried.
    """
    results: dict = {}

    full_name = f"{profile['first_name']} {profile['last_name']}".strip()
    try_fill(page, 'input[name="name"]', full_name, "name", results)
    try_fill(page, 'input[name="email"]', profile["email"], "email", results)
    try_fill(page, 'input[name="phone"]', profile["phone"], "phone", results)
    try_fill(page, 'input[name="urls[LinkedIn]"]', profile["linkedin"], "linkedin", results)
    try_fill(page, 'input[name="urls[GitHub]"]', profile["github"], "github", results)

    try_upload(page, 'input[name="resume"]', profile["resume_path"], "resume", results)

    # Company-specific <select> questions (school, graduation year, "how did
    # you hear about us", etc.) -- location's own autocomplete is handled by
    # Lever separately and isn't a plain <select>, so it's left untouched.
    selects = page.locator("select")
    for i in range(selects.count()):
        sel = selects.nth(i)
        name = sel.get_attribute("name") or f"select_{i}"
        question = _label_for(page, sel)
        if not question:
            continue
        resolve_select(page, sel, question, f"select::{name}", results, interactive, profile)

    # Radio-button questions (work authorization, EEOC veteran/disability, etc.)
    radio_names = page.eval_on_selector_all("input[type=radio]", "els => [...new Set(els.map(e => e.name))]")
    for name in radio_names:
        group = page.locator(f'input[type=radio][name="{name}"]')
        question = _label_for(page, group.first)
        if not question:
            continue
        resolve_radio_group(page, group, question, f"radio::{name}", results, interactive)

    # Checkbox-group questions (language skills, preferred office locations, etc.)
    checkbox_names = page.eval_on_selector_all("input[type=checkbox]", "els => [...new Set(els.map(e => e.name))]")
    for name in checkbox_names:
        group = page.locator(f'input[type=checkbox][name="{name}"]')
        question = _label_for(page, group.first)
        if not question:
            continue
        resolve_checkbox_group(page, group, question, f"checkbox::{name}", results, interactive)

    return results
