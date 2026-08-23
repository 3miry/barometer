"""Human-facing model catalogue for the public Barometer interface.

Detection currently operates at family level. Recognised terms make those
families discoverable without pretending that reports have variant-level
attribution when the source post did not provide it.
"""
from __future__ import annotations
from collections import Counter
import re


MODEL_CATALOG = {
    "claude": {
        "label": "Claude",
        "lab": "Anthropic",
        "recognised_terms": ("Claude", "Sonnet", "Opus", "Haiku"),
    },
    "gpt": {
        "label": "GPT / ChatGPT",
        "lab": "OpenAI",
        "recognised_terms": ("ChatGPT", "GPT-5", "GPT-4", "GPT-4o", "o3", "o4"),
    },
    "gemini": {
        "label": "Gemini",
        "lab": "Google",
        "recognised_terms": ("Gemini", "Google AI", "Bard"),
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
    }


def infer_variant(model: str, text: str) -> str | None:
    """Return an exact-enough model key only when the text says one plainly."""
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
    elif model == "gemini":
        match = re.search(
            r"(?i)\bgemini(?:\s+(\d+(?:\.\d+)?))?\s+"
            r"(pro|flash|ultra|nano)\b",
            text,
        )
        if match:
            version = f"-{match.group(1)}" if match.group(1) else ""
            return f"gemini{version}-{match.group(2).lower()}"
        match = re.search(
            r"(?i)\bgemini\s+(pro|flash|ultra|nano)"
            r"(?:\s+(\d+(?:\.\d+)?))?\b",
            text,
        )
        if match:
            version = f"-{match.group(2)}" if match.group(2) else ""
            return f"gemini{version}-{match.group(1).lower()}"
    return None


def variant_label(variant: str, family: str | None = None) -> str:
    """Turn a stable variant key into a compact public label."""
    if variant == "unspecified":
        family_label = model_catalog_entry(family or "model")["label"]
        return f"Unspecified {family_label} model"
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
    if variant.startswith("gemini-"):
        parts = variant.split("-")[1:]
        return "Gemini " + " ".join(
            part.title() if not re.fullmatch(r"\d+(?:\.\d+)?", part) else part
            for part in parts
        )
    return variant.replace("-", " ").title()


def variant_breakdown(model: str, complaints: list) -> list[dict]:
    """Aggregate explicit variants, retaining an honest unspecified bucket."""
    counts = Counter(
        complaint.variant or infer_variant(model, complaint.text) or "unspecified"
        for complaint in complaints
    )
    ordered = sorted(
        counts.items(),
        key=lambda item: (item[0] == "unspecified", -item[1], item[0]),
    )
    return [
        {
            "key": variant,
            "label": variant_label(variant, model),
            "reports": count,
            "explicit": variant != "unspecified",
        }
        for variant, count in ordered
    ]
