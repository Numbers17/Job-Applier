"""Calls the Claude API to select and lightly reword experience-bank
content for a specific job description, then resolves that selection back
against the bank into the shape scraper/resume/template.py expects.

The model is only ever allowed to choose *which* bank entries/bullets to
use and *reorder/reword* them -- every fact, number, and technology name
must come verbatim from the bank. It references bullets by their existing
IDs rather than retyping them, which both keeps output small and makes
fabrication structurally harder (an invented id just gets dropped in
_resolve() instead of silently entering the resume).

Prompting alone isn't a reliable guarantee against fabrication -- testing
against a real JD showed the model add unstated claims to reworded bullets
(e.g. inserting "backed by a Python data pipeline" into a bullet that
never mentioned one) despite an explicit system-prompt rule against it.
_check_rewording() is a second, deterministic layer on top of that prompt:
any reworded bullet introducing a substantive word not present in the
source bullet is rejected and the original text is used instead.
"""
import json
import os
import re

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-opus-5"

# Words allowed to appear in a reworded bullet even if absent from the
# original -- connectors/articles that don't carry factual content. Any
# other new word is treated as a potential fabrication and rejected.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "to", "of", "in", "on", "for", "with",
    "from", "by", "at", "as", "is", "are", "was", "were", "be", "been",
    "being", "that", "which", "this", "these", "those", "its", "it",
    "using", "via", "into", "across", "over", "under", "up", "down",
    "out", "about", "than", "not", "no", "also", "while", "during",
}


def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+%?", text.lower())}


def check_rewording(original: str, reworded: str) -> tuple[bool, list[str]]:
    """Returns (is_safe, new_words). is_safe is False if the reworded text
    introduces any substantive word not present in the original bullet --
    including any changed/added number or percentage, since those are
    never stopwords.
    """
    added = _tokenize(reworded) - _tokenize(original)
    flagged = sorted(w for w in added if w not in _STOPWORDS and len(w) > 2)
    return (len(flagged) == 0, flagged)

SYSTEM_PROMPT = """You are tailoring a candidate's resume to a specific job posting.

You are given the candidate's full experience bank (JSON) and a job description. \
Decide which experience and project entries to include and in what order, and \
which bullets within each to include, in what order, and whether to lightly \
reword them for keyword alignment with the job description.

STRICT RULES -- these are non-negotiable:
- Never invent, exaggerate, add, or alter any fact, number, percentage, \
technology name, or outcome that is not already present in the source bullet \
text. A reworded bullet must preserve the exact same meaning and every \
quantitative claim exactly as stated in the original.
- If you are not rewording a bullet, set reworded_text to null -- do not retype \
the bullet unchanged.
- Reference bullets and entries only by their existing ids from the experience \
bank. Never invent an id.
- Only omit an experience or project entry if it is clearly irrelevant to the \
role; when in doubt, include it.
- Within an included entry, keep most of its bullets -- only drop one if the \
resume clearly benefits from the cut, not just because it seems less relevant.
- Output only the structured selection. Do not add commentary."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "experience": {
            "type": "array",
            "description": "Experience entries to include, in display order.",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Experience entry id from the bank"},
                    "bullets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "bullet_id": {"type": "string"},
                                "reworded_text": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            },
                            "required": ["bullet_id", "reworded_text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "bullets"],
                "additionalProperties": False,
            },
        },
        "projects": {
            "type": "array",
            "description": "Project entries to include, in display order.",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Project entry id from the bank"},
                    "bullets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "bullet_id": {"type": "string"},
                                "reworded_text": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            },
                            "required": ["bullet_id", "reworded_text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "bullets"],
                "additionalProperties": False,
            },
        },
        "skills_order": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Skill category names from the bank's skills object, in priority order.",
        },
    },
    "required": ["experience", "projects", "skills_order"],
    "additionalProperties": False,
}


def _resolve_bullets(bullet_sels: list[dict], bullets_by_id: dict, rejected: list[dict], entry_id: str) -> list[str]:
    bullets = []
    for bsel in bullet_sels:
        bullet_id = bsel.get("bullet_id")
        original = bullets_by_id.get(bullet_id)
        if original is None:
            continue  # model referenced a bullet id that doesn't exist -- drop it
        reworded = bsel.get("reworded_text")
        if not reworded:
            bullets.append(original)
            continue
        safe, flagged_words = check_rewording(original, reworded)
        if safe:
            bullets.append(reworded)
        else:
            bullets.append(original)  # fall back rather than risk a fabricated claim
            rejected.append(
                {
                    "entry_id": entry_id,
                    "bullet_id": bullet_id,
                    "original": original,
                    "rejected_reworded_text": reworded,
                    "flagged_words": flagged_words,
                }
            )
    return bullets


def _resolve(selection: dict, bank: dict) -> tuple[dict, list[dict]]:
    """Turns the model's id-based selection back into plain-text content,
    pulling every fact directly from the bank -- never from model output.
    Returns (content, rejected_rewordings) so callers can log any bullet
    where the fact-check rejected the model's reworded text.
    """
    exp_by_id = {e["id"]: e for e in bank["experience"]}
    proj_by_id = {p["id"]: p for p in bank["projects"]}
    rejected: list[dict] = []

    resolved_experience = []
    for entry_sel in selection.get("experience", []):
        src = exp_by_id.get(entry_sel.get("id"))
        if not src:
            continue  # model referenced an id that doesn't exist -- drop it, don't guess
        bullets_by_id = {b["id"]: b["text"] for b in src["bullets"]}
        bullets = _resolve_bullets(entry_sel.get("bullets", []), bullets_by_id, rejected, src["id"])
        resolved_experience.append(
            {
                "title": src["title"],
                "company": src["company"],
                "location": src["location"],
                "dates": src["dates"],
                "bullets": bullets,
            }
        )

    resolved_projects = []
    for entry_sel in selection.get("projects", []):
        src = proj_by_id.get(entry_sel.get("id"))
        if not src:
            continue
        bullets_by_id = {b["id"]: b["text"] for b in src["bullets"]}
        bullets = _resolve_bullets(entry_sel.get("bullets", []), bullets_by_id, rejected, src["id"])
        resolved_projects.append({"name": src["name"], "stack": src["stack"], "bullets": bullets})

    skills_order = [k for k in selection.get("skills_order", []) if k in bank["skills"]]
    # keep any categories the model omitted, appended at the end -- never silently drop a skill category
    skills_order += [k for k in bank["skills"] if k not in skills_order]
    skills = {k: bank["skills"][k] for k in skills_order}

    content = {
        "header": bank["header"],
        "education": bank["education"],
        "experience": resolved_experience,
        "projects": resolved_projects,
        "skills": skills,
    }
    return content, rejected


def tailor(jd_text: str, company: str, role: str, bank: dict) -> tuple[dict, list[dict]]:
    """Returns (resolved_content, rejected_rewordings). resolved_content is
    ready for resume.template.render_html(); rejected_rewordings lists any
    bullet where the fact-check pass rejected the model's rewording.
    """
    client = anthropic.Anthropic()

    user_message = (
        f"Company: {company}\nRole: {role}\n\n"
        f"Job description:\n{jd_text[:12000]}\n\n"
        f"Candidate's experience bank (JSON):\n{json.dumps(bank, indent=2)}"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        messages=[{"role": "user", "content": user_message}],
    )

    text = next(b.text for b in response.content if b.type == "text")
    selection = json.loads(text)
    return _resolve(selection, bank)


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    bank = json.loads(open("../experience_bank.json", encoding="utf-8").read())
    content, rejected = tailor(
        jd_text="Software Engineer Intern, Production Infrastructure. Seattle, WA. "
        "We're looking for a strong software engineer with backend and distributed "
        "systems experience, ideally with Python or Go, who has worked on data "
        "pipelines or cloud infrastructure.",
        company="Palantir Technologies",
        role="Software Engineer Intern, Production Infrastructure",
        bank=bank,
    )
    print("=== rejected rewordings ===")
    print(json.dumps(rejected, indent=2))
    print("=== content ===")
    print(json.dumps(content, indent=2))
