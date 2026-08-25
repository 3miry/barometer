"""Transparent temporal-priority cues for private review ordering.

Priority is a triage aid, never an eligibility decision. Undated reports remain
in the queue because day zero is the date the report was received.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re


@dataclass(frozen=True)
class TemporalPriority:
    score: int
    band: str
    cues: tuple[str, ...]

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["cues"] = list(self.cues)
        return payload


_CUE_GROUPS = (
    (
        4,
        "day-specific",
        re.compile(
            r"\b(?:today|yesterday|tonight|this morning|this afternoon|"
            r"this evening|right now|just now)\b",
            re.IGNORECASE,
        ),
    ),
    (
        3,
        "recent-period",
        re.compile(
            r"\b(?:recently|lately|this week|in the past few days|"
            r"over the (?:last|past) (?:few )?(?:days|week)|"
            r"since (?:the )?(?:update|release|launch|rollout)|"
            r"after (?:the )?(?:update|release|launch|rollout))\b",
            re.IGNORECASE,
        ),
    ),
    (
        2,
        "change-language",
        re.compile(
            r"\b(?:suddenly|all of a sudden|first time|no longer|anymore|"
            r"used to|has become|have become|became|started (?:to|doing)|"
            r"keeps? (?:getting|becoming)|now (?:feels?|seems?|acts?|sounds?)|"
            r"got (?:better|worse)|regressed|improved)\b",
            re.IGNORECASE,
        ),
    ),
)


def temporal_priority(text: str) -> TemporalPriority:
    """Score explicit recency/change language without rejecting a report."""
    matches: list[tuple[int, int, str]] = []
    score = 0
    for weight, label, pattern in _CUE_GROUPS:
        found = pattern.search(text or "")
        if found is None:
            continue
        score += weight
        matches.append((found.start(), weight, found.group(0).casefold()))
    cues = tuple(item[2] for item in sorted(matches))
    if score >= 7:
        band = "very high"
    elif score >= 4:
        band = "high"
    elif score >= 2:
        band = "medium"
    else:
        band = "undated"
    return TemporalPriority(score=score, band=band, cues=cues)
