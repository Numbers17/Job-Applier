"""Persistent store of answers you've given to recurring application
questions (e.g. "Which university do you attend?", "Are you a veteran?"),
keyed by normalized question text so the same-worded question on a future
application reuses your answer instead of asking again.
"""
import json
import re
from pathlib import Path

ANSWERS_PATH = Path(__file__).resolve().parent.parent / "answers.json"


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


def save_answer(question: str, field_type: str, value) -> None:
    answers = load_answers()
    answers[_normalize(question)] = {
        "question": question.strip(),
        "type": field_type,
        "value": value,
    }
    ANSWERS_PATH.write_text(json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8")
