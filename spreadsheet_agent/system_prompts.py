"""Utilities for loading full system prompt templates."""

from __future__ import annotations

import os


SYSTEM_PROMPT_DIR = os.path.join(os.path.dirname(__file__), "system_prompt")


def load_full_system_prompt(filename: str) -> str:
    """Load a full system prompt template from the system_prompt directory."""
    path = os.path.join(SYSTEM_PROMPT_DIR, filename)
    with open(path, "r") as f:
        return f.read()


def render_full_system_prompt(filename: str, **kwargs: str) -> str:
    """Render a full system prompt template with optional placeholder values.

    The {tool_definitions} placeholder is preserved unless explicitly provided.
    """
    template = load_full_system_prompt(filename)
    if "tool_definitions" not in kwargs:
        kwargs["tool_definitions"] = "{tool_definitions}"
    for key, value in kwargs.items():
        template = template.replace("{" + key + "}", value)
    return template
