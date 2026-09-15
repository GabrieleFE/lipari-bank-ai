"""Caricamento dei prompt versionati da src/prompts/. I prompt vivono come file, come il codice."""

from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


def load_prompt(name: str) -> str:
    path = PROMPT_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")
