"""Field-fill logic for jobs.ashbyhq.com application forms.

Verified live against a real posting. Ashby's standard fields use stable
"_systemfield_*" ids for name/email/resume; the phone field's id is a
random per-posting UUID, but its type="tel" is consistent, so it's
targeted that way instead. LinkedIn/GitHub aren't standard Ashby system fields -- they show up (if at
all) as posting-specific custom questions with unpredictable ids, but the
question labels use proper <label for="..."> associations, so they're
targeted via accessible-label lookup instead of a guessed selector.

Beyond those, Ashby postings commonly add custom questions (work
authorization, EEOC, referral source, etc.) as native <select>/radio/
checkbox-group fields -- these are resolved generically via
_common.resolve_select/resolve_radio_group/resolve_checkbox_group, the
same mechanism already used for Lever/Greenhouse. Not yet verified live
against a posting that actually has custom select/radio/checkbox
questions (only LinkedIn/GitHub text questions were confirmed) -- check a
real run's log before trusting it blindly. If Ashby renders a question as
fully custom-styled controls with no underlying native <select>/
input[type=radio|checkbox], this loop won't find it.
"""
from ._common import (
    clean_label,
    resolve_checkbox_group,
    resolve_radio_group,
    resolve_select,
    try_fill,
    try_fill_by_label,
    try_upload,
)


def _label_for(page, locator) -> str:
    """Ashby associates a question's label via <label for="id">, a
    <fieldset><legend>, or an aria-labelledby pointing at the group's
    heading -- tried in that order.
    """
    try:
        text = locator.evaluate(
            """
            el => {
                const fieldset = el.closest('fieldset');
                if (fieldset) {
                    const legend = fieldset.querySelector('legend');
                    if (legend) return legend.textContent;
                }
                if (el.id) {
                    const lab = document.querySelector(`label[for="${el.id}"]`);
                    if (lab) return lab.textContent;
                }
                const group = el.closest('[role="group"]') || el.closest('div');
                if (group) {
                    const labelledBy = group.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        const labEl = document.getElementById(labelledBy.split(' ')[0]);
                        if (labEl) return labEl.textContent;
                    }
                    const lab = group.querySelector('label');
                    if (lab) return lab.textContent;
                }
                return '';
            }
            """
        )
        return clean_label(text)
    except Exception:  # noqa: BLE001
        return ""


def fill(page, profile: dict, interactive: bool = True, listing_type: str = "") -> dict:
    """Returns {field_label: {value, selector, status}} for every field
    attempted, so the caller can log exactly what was tried. `listing_type`
    ("internship" or "new_grad") lets resolve_select answer graduation-year
    questions correctly for whichever context this posting is.
    """
    results: dict = {}

    full_name = f"{profile['first_name']} {profile['last_name']}".strip()
    try_fill(page, "#_systemfield_name", full_name, "name", results)
    try_fill(page, "#_systemfield_email", profile["email"], "email", results)
    try_fill(page, 'input[type="tel"]', profile["phone"], "phone", results)

    try_upload(page, "#_systemfield_resume", profile["resume_path"], "resume", results)

    try_fill_by_label(page, "GitHub", profile["github"], "github", results)
    try_fill_by_label(page, "LinkedIn", profile["linkedin"], "linkedin", results)

    # Company-specific <select> questions (work authorization, referral
    # source, EEOC, etc.)
    selects = page.locator("select")
    for i in range(selects.count()):
        sel = selects.nth(i)
        sel_id = sel.get_attribute("id") or f"select_{i}"
        question = _label_for(page, sel)
        if not question:
            continue
        resolve_select(page, sel, question, f"select::{sel_id}", results, interactive, profile, listing_type)

    # Radio-button questions
    radio_names = page.eval_on_selector_all("input[type=radio]", "els => [...new Set(els.map(e => e.name))]")
    for name in radio_names:
        group = page.locator(f'input[type=radio][name="{name}"]')
        question = _label_for(page, group.first)
        if not question:
            continue
        resolve_radio_group(page, group, question, f"radio::{name}", results, interactive)

    # Checkbox-group questions
    checkbox_names = page.eval_on_selector_all("input[type=checkbox]", "els => [...new Set(els.map(e => e.name))]")
    for name in checkbox_names:
        group = page.locator(f'input[type=checkbox][name="{name}"]')
        question = _label_for(page, group.first)
        if not question:
            continue
        resolve_checkbox_group(page, group, question, f"checkbox::{name}", results, interactive)

    return results
