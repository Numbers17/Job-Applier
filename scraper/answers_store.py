"""Persistent store of answers you've given to recurring application
questions (e.g. "Which university do you attend?", "Are you a veteran?"),
keyed by normalized question text so the same-worded question on a future
application reuses your answer instead of asking again.
"""
import difflib
import json
import re
from pathlib import Path

ANSWERS_PATH = Path(__file__).resolve().parent.parent / "answers.json"

# Below this SequenceMatcher ratio, a previously-answered question isn't
# offered as a reuse suggestion for a new one -- kept conservative (a
# fuzzy match is only ever a suggestion the caller confirms with you, never
# auto-applied, but a threshold this high still keeps the suggestions
# actually relevant instead of noise).
SIMILARITY_SUGGEST_THRESHOLD = 0.75


def _normalize(question: str) -> str:
    text = question.replace("✱", "").replace("*", "")  # strip required-field markers
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def load_answers() -> dict:
    if not ANSWERS_PATH.exists():
        return {}
    return json.loads(ANSWERS_PATH.read_text(encoding="utf-8"))


def get_answer(question: str):
    return load_answers().get(_normalize(question))


def find_similar(question: str, threshold: float = SIMILARITY_SUGGEST_THRESHOLD):
    """Best-effort fuzzy match against previously-answered questions, for
    when the same question shows up reworded on a different company's form
    (e.g. "Are you legally authorized to work in the country for which you
    are applying?" vs "Are you authorized to work in the United States?").
    Local text similarity only, no API call -- same "don't spend an API
    call deciding something word-overlap can decide" approach as the
    resume-reuse matching in resume_library.py.

    Returns (best_matching_answer_entry_or_None, score). Callers must treat
    this as a suggestion to confirm with the user, never apply silently --
    two questions can be textually close but mean different things (e.g.
    "at least 18" vs "at least 21").
    """
    target = _normalize(question)
    answers = load_answers()
    best_key, best_score = None, 0.0
    for key in answers:
        score = difflib.SequenceMatcher(None, target, key).ratio()
        if score > best_score:
            best_key, best_score = key, score
    if best_key and best_score >= threshold:
        return answers[best_key], best_score
    return None, best_score


def save_answer(question: str, field_type: str, value) -> None:
    answers = load_answers()
    answers[_normalize(question)] = {
        "question": question.strip(),
        "type": field_type,
        "value": value,
    }
    ANSWERS_PATH.write_text(json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8")
