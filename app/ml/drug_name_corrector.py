"""
Post-process STT transcripts — hardcoded phonetic fixes + rapidfuzz drug matching.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)

DRUGS_JSON = Path(__file__).resolve().parent / "drugs.json"
FUZZ_SCORE_CUTOFF = 82

# First-pass phonetic fixes seen in Telugu-English clinic dictation
HARDCODED_CORRECTIONS: dict[str, str] = {
    "metro foreman": "Metformin",
    "amla deep in": "Amlodipine",
    "para set a mol": "Paracetamol",
    "panto pro zone": "Pantoprazole",
    "atos ta teen": "Atorvastatin",
}


class CorrectionLog(TypedDict):
    original: str
    corrected: str
    score: float
    method: str


@dataclass
class DrugCorrectionResult:
    transcript: str
    corrections: list[CorrectionLog]

    @property
    def corrections_made(self) -> int:
        return len(self.corrections)


_drug_names: list[str] = []
_alias_to_canonical: dict[str, str] = {}
_loaded = False


def _load_drug_index() -> None:
    global _drug_names, _alias_to_canonical, _loaded
    if _loaded:
        return

    canonicals: set[str] = set(HARDCODED_CORRECTIONS.values())
    alias_map: dict[str, str] = {k.lower(): v for k, v in HARDCODED_CORRECTIONS.items()}

    if DRUGS_JSON.exists():
        payload = json.loads(DRUGS_JSON.read_text(encoding="utf-8"))
        for row in payload.get("entries", []):
            alias = str(row.get("alias", "")).strip()
            canonical = str(row.get("canonical", "")).strip()
            if alias and canonical:
                alias_map[alias.lower()] = canonical
                canonicals.add(canonical)

    _alias_to_canonical = alias_map
    _drug_names = sorted(canonicals, key=str.lower)
    _loaded = True
    logger.info("Drug name index loaded: %d canonical names", len(_drug_names))


def invalidate_drug_index() -> None:
    global _loaded
    _loaded = False
    _drug_names.clear()
    _alias_to_canonical.clear()


def _apply_hardcoded_phrases(text: str) -> tuple[str, list[CorrectionLog]]:
    corrections: list[CorrectionLog] = []
    updated = text
    for wrong, right in sorted(HARDCODED_CORRECTIONS.items(), key=lambda x: -len(x[0])):
        pattern = re.compile(re.escape(wrong), re.IGNORECASE)
        if not pattern.search(updated):
            continue

        def _replace(match: re.Match[str]) -> str:
            original = match.group(0)
            if original.lower() != right.lower():
                corrections.append(
                    CorrectionLog(
                        original=original,
                        corrected=right,
                        score=100.0,
                        method="hardcoded",
                    )
                )
            return right

        updated = pattern.sub(_replace, updated)
    return updated, corrections


def _fuzzy_correct_tokens(text: str) -> tuple[str, list[CorrectionLog]]:
    if not _drug_names:
        return text, []

    corrections: list[CorrectionLog] = []
    tokens = text.split()
    output: list[str] = []

    for token in tokens:
        bare = token.strip(".,;:!?()[]\"'")
        prefix = token[: len(token) - len(token.lstrip(".,;:!?()[]\"'"))] if token else ""
        suffix = token[len(bare) + len(prefix) :] if bare else token

        lower = bare.lower()
        if bare.isdigit() or re.match(r"^\d+\.?\d*(mg|ml|mcg|g|iu|units?|%)?$", bare, re.IGNORECASE):
            output.append(token)
            continue

        if lower in _alias_to_canonical:
            canonical = _alias_to_canonical[lower]
            if bare != canonical:
                corrections.append(
                    CorrectionLog(
                        original=bare,
                        corrected=canonical,
                        score=100.0,
                        method="alias",
                    )
                )
            output.append(prefix + canonical + suffix)
            continue

        match = process.extractOne(
            bare,
            _drug_names,
            scorer=fuzz.WRatio,
            score_cutoff=FUZZ_SCORE_CUTOFF,
        )
        if match is None:
            output.append(token)
            continue

        canonical, score, _ = match
        if bare.lower() != canonical.lower():
            corrections.append(
                CorrectionLog(
                    original=bare,
                    corrected=canonical,
                    score=round(float(score), 1),
                    method="fuzzy",
                )
            )
            logger.debug("Drug fuzzy correction: %r → %r (%.1f)", bare, canonical, score)
        output.append(prefix + canonical + suffix)

    return " ".join(output), corrections


def correct_drug_names(transcript: str) -> DrugCorrectionResult:
    """Run hardcoded + fuzzy drug correction on a transcript."""
    _load_drug_index()
    text = transcript.strip()
    if not text:
        return DrugCorrectionResult(transcript="", corrections=[])

    text, hardcoded = _apply_hardcoded_phrases(text)
    text, fuzzy = _fuzzy_correct_tokens(text)
    return DrugCorrectionResult(
        transcript=text,
        corrections=hardcoded + fuzzy,
    )
