"""Field-fill logic for job-boards.greenhouse.io application forms.

Verified live against a real posting: standard fields use stable ids
(first_name/last_name/email/phone/resume/cover_letter/school--0/degree--0/
discipline--0); LinkedIn/GitHub live under posting-specific "question_..."
ids, matched here by aria-label instead since the id isn't stable across
companies.
"""
from ._common import try_combobox, try_fill, try_fill_by_aria_label, try_upload


def fill(page, profile: dict, interactive: bool = True) -> dict:
    """Returns {field_label: {value, selector, status}} for every field
    attempted, so the caller can log exactly what was tried.

    `interactive` is accepted for a consistent call signature across ATS
    handlers but currently unused here -- Greenhouse's custom questions
    (work auth, EEOC, etc.) use a react-select combobox that isn't yet
    wired into the generic question resolver used by lever.py.
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

    return results
