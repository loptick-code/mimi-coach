"""Parseur déterministe du fichier Markdown produit par un LLM externe."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

class ProgramMarkdownError(ValueError):
    pass

@dataclass(frozen=True)
class ParsedProgramMarkdown:
    start_date: date
    normalized_text: str

_WEEK_RE = re.compile(r"^##\s+Semaine\s+(\d+)\s*$", re.IGNORECASE)
_SESSION_RE = re.compile(r"^###\s+Séance\s+([1-6])\s*$", re.IGNORECASE)
_DATE_RE = re.compile(r"(?:date|début)[^:]*:\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_VERSION_MARKER = "MI_COACH_PROGRAM_VERSION: 1"

def _parse_date(value: str) -> date:
    try:
        if "/" in value:
            day, month, year = (int(part) for part in value.split("/"))
            return date(year, month, day)
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ProgramMarkdownError("La date de première semaine est invalide.") from exc

def parse_program_markdown(content: str) -> ParsedProgramMarkdown:
    if _VERSION_MARKER not in content:
        raise ProgramMarkdownError("Ce fichier ne contient pas le marqueur MI_COACH_PROGRAM_VERSION: 1. Télécharge les instructions Mi Coach pour générer le bon format.")
    if len(content) > 200_000:
        raise ProgramMarkdownError("Le fichier dépasse la limite de 200 000 caractères.")
    date_match = _DATE_RE.search(content)
    if not date_match:
        raise ProgramMarkdownError("Ajoute « Date : JJ/MM/AAAA » dans la section Première semaine.")
    start_date = _parse_date(date_match.group(1))
    if start_date.weekday() != 0:
        raise ProgramMarkdownError("La date de première semaine doit être un lundi.")
    lines = content.splitlines()
    weeks = []
    current_week = None
    current_session = None
    def finish_session():
        nonlocal current_session
        if current_session is not None and current_week is not None:
            current_week[1].append(current_session)
        current_session = None
    def finish_week():
        nonlocal current_week
        finish_session()
        if current_week is not None:
            weeks.append(current_week)
        current_week = None
    for raw_line in lines:
        line = raw_line.strip()
        week_match = _WEEK_RE.match(line)
        if week_match:
            finish_week(); current_week = (int(week_match.group(1)), []); continue
        session_match = _SESSION_RE.match(line)
        if session_match:
            if current_week is None:
                raise ProgramMarkdownError("Une séance apparaît avant sa section Semaine.")
            finish_session(); current_session = (int(session_match.group(1)), []); continue
        if current_session is not None and line:
            if re.match(r"^-?\s*type\s*:", line, re.IGNORECASE):
                continue
            if re.match(r"^-?\s*description\s*:", line, re.IGNORECASE):
                line = line.split(":", 1)[1].strip()
            elif line.startswith("-"):
                line = line[1:].strip()
            current_session[1].append(line)
    finish_week()
    if not weeks:
        raise ProgramMarkdownError("Aucune section « Semaine N » n'a été trouvée.")
    normalized = []
    seen_weeks = set()
    for week_number, sessions in weeks:
        if week_number in seen_weeks:
            raise ProgramMarkdownError(f"La semaine {week_number} apparaît plusieurs fois.")
        seen_weeks.add(week_number)
        if not sessions:
            raise ProgramMarkdownError(f"La semaine {week_number} ne contient aucune séance.")
        normalized.append(f"Semaine {week_number}")
        seen_sessions = set()
        for session_number, description_lines in sessions:
            if session_number in seen_sessions:
                raise ProgramMarkdownError(f"La séance {session_number} apparaît plusieurs fois dans la semaine {week_number}.")
            seen_sessions.add(session_number)
            description = " + ".join(part for part in description_lines if part).strip()
            if not description:
                raise ProgramMarkdownError(f"La séance {session_number} de la semaine {week_number} n'a pas de description.")
            normalized.append(f"Séance {session_number} : {description}")
    return ParsedProgramMarkdown(start_date=start_date, normalized_text="\n".join(normalized))
