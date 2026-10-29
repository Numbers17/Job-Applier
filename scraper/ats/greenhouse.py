"""Field-fill logic for job-boards.greenhouse.io application forms.

Verified live against a real posting: standard fields use stable ids
(first_name/last_name/email/phone/resume/cover_letter/school--0/degree--0/
discipline--0); LinkedIn/GitHub live under posting-specific "question_..."
ids, matched here by aria-label instead since the id isn't stable across
companies.

Beyond those, Greenhouse postings commonly add custom questions (EEOC
gender/race/veteran/disability, work authorization, referral source,
etc.) as native <select>/radio/checkbox-group fields -- these are
resolved generically via _common.resolve_select/resolve_radio_group/
resolve_checkbox_group, the same mechanism already used for Lever.
school/degree/major are a different widget (a react-select combobox tied
to an education-history autocomplete) and stay on the fixed try_combobox
calls below; a react-select doesn't render a plain <select> element, so
the generic loop can't double-handle them. Not yet verified live against
a posting that actually has custom select/radio/checkbox questions --
check a real run's log before trusting it blindly.
"""
from ._common import (
    clean_label,
    resolve_checkbox_group,
    resolve_radio_group,
    resolve_select,
    try_combobox,
    try_fill,
    try_fill_by_aria_label,
    try_upload,
)

# school/degree/major use a react-select widget at these ids, not a real
# <select> -- excluded defensively from the generic loop below in case a
# posting ever renders them as a native <select> instead.
_KNOWN_COMBOBOX_IDS = {"school--0", "degree--0", "discipline--0"}


def _label_for(page, locator) -> str:
    """Greenhouse gives radio/checkbox question groups a <fieldset><legend>,
    and gives standalone fields (including custom <select> questions) a
    proper <label for="id">.
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
                const field = el.closest('.field');
                if (field) {
                    const lab = field.querySelector('label');
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

    try_fill(page, "#first_name", profile["first_name"], "first_name", results)
    try_fill(page, "#last_name", profile["last_name"], "last_name", results)
    try_fill(page, "#email", profile["email"], "email", results)
    try_fill(page, "#phone", profile["phone"], "phone", results)

    try_upload(page, "#resume", profile["resume_path"], "resume", results)

    try_combobox(page, "#school--0", profile["school"], "school", results)
    try_combobox(page, "#degree--0", profile["degree"], "degree", results)
    try_combobox(page, "#discipline--0", profile["major"], "major", results)

    try_fill_by_aria_label(page, "linkedin", profile["linkedin"], "linkedin", results)
    try_fill_by_aria_label(page, "github", profile["github"], "github", results)

    # Company-specific <select> questions (EEOC, work authorization,
    # referral source, etc.).
    selects = page.locator("select")
    for i in range(selects.count()):
        sel = selects.nth(i)
        sel_id = sel.get_attribute("id") or ""
        if sel_id in _KNOWN_COMBOBOX_IDS:
            continue
        question = _label_for(page, sel)
        if not question:
            continue
        resolve_select(page, sel, question, f"select::{sel_id or i}", results, interactive, profile, listing_type)

    # Radio-button questions (work authorization, EEOC veteran/disability, etc.)
    radio_names = page.eval_on_selector_all("input[type=radio]", "els => [...new Set(els.map(e => e.name))]")
    for name in radio_names:
        group = page.locator(f'input[type=radio][name="{name}"]')
        question = _label_for(page, group.first)
        if not question:
            continue
        resolve_radio_group(page, group, question, f"radio::{name}", results, interactive)

    # Checkbox-group questions (e.g. preferred locations, language skills)
    checkbox_names = page.eval_on_selector_all("input[type=checkbox]", "els => [...new Set(els.map(e => e.name))]")
    for name in checkbox_names:
        group = page.locator(f'input[type=checkbox][name="{name}"]')
        question = _label_for(page, group.first)
        if not question:
            continue
        resolve_checkbox_group(page, group, question, f"checkbox::{name}", results, interactive)

    return results
