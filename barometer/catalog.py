"""Human-facing model catalogue for the public Barometer interface.

Detection currently operates at family level. Recognised terms make those
families discoverable without pretending that reports have variant-level
attribution when the source post did not provide it.
"""
from __future__ import annotations
from collections import Counter
import re


PREVIEW_DATA_NOTE = (
    "This local preview includes synthetic or legacy calibration samples. "
    "Its report volumes are for interface evaluation, not a normal-user baseline."
)


MODEL_CATALOG = {
    "claude": {
        "label": "Claude",
        "lab": "Anthropic",
        "recognised_terms": (
            "Claude", "Fable 5", "Opus", "Opus 5", "Sonnet",
            "Sonnet 5", "Opus 4.8",
        ),
        "tracked_variants": (
            {"key": "claude-fable-5", "label": "Fable 5", "aliases": ("Fable 5",)},
            {"key": "claude-opus-5", "label": "Opus 5", "aliases": ("Opus 5", "Claude Opus 5")},
            {"key": "claude-sonnet-5", "label": "Sonnet 5", "aliases": ("Sonnet 5", "Claude Sonnet 5")},
            {"key": "claude-opus-4.8", "label": "Opus 4.8", "aliases": ("Opus 4.8", "Claude Opus 4.8")},
        ),
    },
    "gpt": {
        "label": "GPT / ChatGPT",
        "lab": "OpenAI",
        "recognised_terms": (
            "ChatGPT", "GPT", "GPT-5.5", "GPT-5.6", "Sol", "Luna", "Terra",
        ),
        "tracked_variants": (
            {"key": "gpt-5.5", "label": "GPT-5.5", "aliases": ("GPT-5.5", "GPT 5.5")},
            {"key": "gpt-5.6", "label": "GPT-5.6", "aliases": ("GPT-5.6", "GPT 5.6", "Sol", "Luna", "Terra")},
        ),
    },
    "grok": {
        "label": "Grok",
        "lab": "xAI",
        "recognised_terms": ("Grok", "Grok 4.5", "Grok 4.6", "xAI"),
        "tracked_variants": (
            {"key": "grok-4.5", "label": "Grok 4.5", "aliases": ("Grok 4.5", "Grok-4.5")},
            {"key": "grok-4.6", "label": "Grok 4.6", "aliases": ("Grok 4.6", "Grok-4.6")},
        ),
    },
    "gemini": {
        "label": "Gemini",
        "lab": "Google",
        "recognised_terms": (
            "Gemini", "Gemini 3.1 Pro", "Gemini Flash 3.5",
            "Gemini Flash-Lite 3.7", "Google AI",
        ),
        "tracked_variants": (
            {"key": "gemini-3.1-pro", "label": "Gemini 3.1 Pro", "aliases": ("Gemini 3.1 Pro", "Gemini Pro 3.1")},
            {"key": "gemini-3.5-flash", "label": "Gemini Flash 3.5", "aliases": ("Gemini Flash 3.5", "Gemini 3.5 Flash")},
            {"key": "gemini-3.7-flash-lite", "label": "Gemini Flash-Lite 3.7", "aliases": ("Gemini Flash-Lite 3.7", "Gemini Flash Lite 3.7", "Gemini 3.7 Flash-Lite", "Gemini 3.7 Flash Lite")},
        ),
    },
}


def model_catalog_entry(model: str) -> dict:
    """Return public metadata, including a safe fallback for future families."""
    entry = MODEL_CATALOG.get(model)
    if entry is not None:
        return entry
    label = model.replace("_", " ").replace("-", " ").title()
    return {
        "label": label,
        "lab": "Other",
        "recognised_terms": (label,),
        "tracked_variants": (),
    }


def _alias_pattern(alias: str) -> str:
    pieces = [re.escape(piece) for piece in re.split(r"[\s-]+", alias)]
    return r"(?<![a-z0-9])" + r"[\s-]+".join(pieces) + r"(?![a-z0-9])"


def infer_variant(model: str, text: str) -> str | None:
    """Return an exact-enough model key only when the text says one plainly."""
    for variant in model_catalog_entry(model).get("tracked_variants", ()):
        aliases = sorted(variant["aliases"], key=len, reverse=True)
        if any(re.search(_alias_pattern(alias), text, re.IGNORECASE)
               for alias in aliases):
            return variant["key"]
    if model == "claude":
        match = re.search(
            r"(?i)\bclaude\s+(\d+(?:\.\d+)?)\s+(opus|sonnet|haiku)\b",
            text,
        )
        if match:
            return f"claude-{match.group(2).lower()}-{match.group(1)}"
        match = re.search(
            r"(?i)\b(?:claude\s+)?(opus|sonnet|haiku)"
            r"(?:[-\s]+(\d+(?:\.\d+)?))?\b",
            text,
        )
        if match:
            suffix = f"-{match.group(2)}" if match.group(2) else ""
            return f"claude-{match.group(1).lower()}{suffix}"
    elif model == "gpt":
        match = re.search(r"(?i)\bgpt[-\s]?([345](?:\.\d+)?o?)\b", text)
        if match:
            return f"gpt-{match.group(1).lower()}"
        match = re.search(r"(?i)\b(o[134](?:-mini)?)\b", text)
        if match:
            return f"openai-{match.group(1).lower()}"
    elif model == "grok":
        match = re.search(r"(?i)\bgrok[-\s]?(\d+(?:\.\d+)?)\b", text)
        if match:
            return f"grok-{match.group(1)}"
    elif model == "gemini":
        match = re.search(
            r"(?i)\bgemini(?:\s+(\d+(?:\.\d+)?))?\s+"
            r"(pro|flash(?:[-\s]+lite)?|ultra|nano)\b",
            text,
        )
        if match:
            version = f"-{match.group(1)}" if match.group(1) else ""
            kind = re.sub(r"[\s]+", "-", match.group(2).lower())
            return f"gemini{version}-{kind}"
        match = re.search(
            r"(?i)\bgemini\s+(pro|flash(?:[-\s]+lite)?|ultra|nano)"
            r"(?:\s+(\d+(?:\.\d+)?))?\b",
            text,
        )
        if match:
            version = f"-{match.group(2)}" if match.group(2) else ""
            kind = re.sub(r"[\s]+", "-", match.group(1).lower())
            return f"gemini{version}-{kind}"
    return None


def variant_label(variant: str, family: str | None = None) -> str:
    """Turn a stable variant key into a compact public label."""
    if variant == "unspecified":
        family_label = model_catalog_entry(family or "model")["label"]
        return f"Unspecified {family_label} model"
    for entry in MODEL_CATALOG.values():
        for tracked in entry.get("tracked_variants", ()):
            if tracked["key"] == variant:
                return tracked["label"]
    if variant.startswith("claude-"):
        parts = variant.split("-")[1:]
        return "Claude " + " ".join(
            part.title() if index == 0 else part
            for index, part in enumerate(parts)
        )
    if variant.startswith("gpt-"):
        return "GPT-" + variant.removeprefix("gpt-")
    if variant.startswith("openai-"):
        return variant.removeprefix("openai-")
    if variant.startswith("grok-"):
        return "Grok " + variant.removeprefix("grok-")
    if variant.startswith("gemini-"):
        parts = variant.split("-")[1:]
        return "Gemini " + " ".join(
            part.title() if not re.fullmatch(r"\d+(?:\.\d+)?", part) else part
            for part in parts
        )
    return variant.replace("-", " ").title()


def variant_breakdown(model: str, complaints: list) -> list[dict]:
    """Show monitored models even at zero, plus honest older/unknown buckets."""
    counts = Counter(
        complaint.variant or infer_variant(model, complaint.text) or "unspecified"
        for complaint in complaints
    )
    rows = []
    tracked = model_catalog_entry(model).get("tracked_variants", ())
    for variant in tracked:
        rows.append({
            "key": variant["key"],
            "label": variant["label"],
            "reports": counts.pop(variant["key"], 0),
            "explicit": True,
            "monitored": True,
        })
    unspecified = counts.pop("unspecified", 0)
    other_explicit = sum(counts.values())
    if other_explicit:
        rows.append({
            "key": "other-explicit",
            "label": "Older/other named models",
            "reports": other_explicit,
            "explicit": True,
            "monitored": False,
        })
    rows.append({
        "key": "unspecified",
        "label": variant_label("unspecified", model),
        "reports": unspecified,
        "explicit": False,
        "monitored": False,
    })
    return rows
