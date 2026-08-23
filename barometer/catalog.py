"""Human-facing model catalogue for the public Barometer interface.

Detection currently operates at family level. Recognised terms make those
families discoverable without pretending that reports have variant-level
attribution when the source post did not provide it.
"""
from __future__ import annotations


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
