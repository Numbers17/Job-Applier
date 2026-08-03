"""Field-fill logic for jobs.ashbyhq.com application forms.

Verified live against a real posting. Ashby's standard fields use stable
"_systemfield_*" ids for name/email/resume; the phone field's id is a
random per-posting UUID, but its type="tel" is consistent, so it's
targeted that way instead. LinkedIn/GitHub aren't standard Ashby system fields -- they show up (if at
all) as posting-specific custom questions with unpredictable ids, but the
question labels use proper <label for="..."> associations, so they're
targeted via accessible-label lookup instead of a guessed selector.
"""
from ._common import try_fill, try_fill_by_label, try_upload


def fill(page, profile: dict, interactive: bool = True) -> dict:
    """Returns {field_label: {value, selector, status}} for every field
    attempted, so the caller can log exactly what was tried.

    `interactive` is accepted for a consistent call signature across ATS
    handlers but currently unused here -- Ashby's other custom questions
    aren't yet wired into the generic question resolver used by lever.py.
    """
    results: dict = {}

    full_name = f"{profile['first_name']} {profile['last_name']}".strip()
    try_fill(page, "#_systemfield_name", full_name, "name", results)
    try_fill(page, "#_systemfield_email", profile["email"], "email", results)
    try_fill(page, 'input[type="tel"]', profile["phone"], "phone", results)

    try_upload(page, "#_systemfield_resume", profile["resume_path"], "resume", results)

    try_fill_by_label(page, "GitHub", profile["github"], "github", results)
    try_fill_by_label(page, "LinkedIn", profile["linkedin"], "linkedin", results)

    return results
