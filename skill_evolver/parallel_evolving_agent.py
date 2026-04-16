"""
Parallel Skill Evolving Agent — map-reduce pipeline for skill evolution.

Instead of mutating the skill sequentially (one error record at a time),
this module:

1. MAP: Each batch of error records independently proposes a concise
   instruction-based patch against a frozen snapshot of the original skill.
2. REDUCE: Patches are merged hierarchically (like the analysis/
   compression pipeline) until one final merged patch remains.
3. APPLY: The final merged patch is converted to full file content
   using the existing full-file JSON schema, then applied and validated.

This mirrors the compress_skill_errors.py -> meta_compress pipeline
but applied to skill editing instead of error compression.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from tqdm import tqdm

from src.react_agent.models import Message, ModelSettings, OpenAIClient
from skill_evolver.prompt_loader import load_prompt_template
from skill_evolver.skill_evolving_agent import (
    MODIFICATION_STRATEGIES_SECTION,
    PROMPT_VARIANTS,
    PROTECTED_FILES,
    SYSTEM_PROMPT_BASE,
    FileEdit,
    FileDiff,
    SkillEvolver,
    compute_unified_diff,
    _strip_think,
)

log = logging.getLogger(__name__)

_REFERENCE_PATH_PATTERN = re.compile(r"references/[\w.\-]+\.md")
_SUPPORTED_PATCH_OPS = {
    "insert_after",
    "insert_before",
    "append_to_section",
    "replace_in_section",
    "add_section",
    "delete_section",
    "create",
    "delete_file",
}
_PATCH_OP_ALIASES = {
    "create_file": "create",
    "createFile": "create",
    "deleteFile": "delete_file",
}

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PatchEdit:
    """A single edit operation within a patch."""

    file: str
    op: str  # insert_after, insert_before, append_to_section, replace_in_section,
    # add_section, delete_section, create, delete_file
    target_section: str = ""
    target_text: str = ""
    content: str = ""
    old_text: str = ""  # for replace_in_section
    after_section: str = ""  # for add_section placement


@dataclass
class Patch:
    """A concise instruction-based patch proposed by the map phase."""

    reasoning: str
    edits: list[PatchEdit]
    changelog_entries: list[str]
    batch_index: int = -1
    raw_json: dict = field(default_factory=dict)


@dataclass
class SemanticPatchItem:
    """A semantic edit proposal used by the markdown patch pipeline."""

    target_file: str
    edit_intent: str
    location_hint: str
    change_instruction: str


@dataclass
class SemanticPatch:
    """A semantic patch block proposed in markdown format."""

    reasoning: str
    items: list[SemanticPatchItem]
    changelog_entries: list[str]
    batch_index: int = -1
    raw_markdown: str = ""


@dataclass
class MergeResult:
    """Result of merging multiple patches."""

    merged_patch: Patch
    source_patches: list[Patch]
    level: int = 0


# ---------------------------------------------------------------------------
# Prompt components
# ---------------------------------------------------------------------------

# Output format section that replaces the sequential evolver's fenced full-file JSON
_MAP_OUTPUT_FORMAT = load_prompt_template("parallel_evolving_agent/map_output_format")

# The merge phase system prompt
MERGE_SYSTEM_PROMPT = load_prompt_template("parallel_evolving_agent/merge_system_prompt")

_MARKDOWN_MAP_OUTPUT_FORMAT = load_prompt_template(
    "parallel_evolving_agent/markdown_map_output_format"
)
_MARKDOWN_MAP_OUTPUT_FORMAT_HEADING = load_prompt_template(
    "parallel_evolving_agent/markdown_map_output_format_heading"
)

MARKDOWN_MERGE_SYSTEM_PROMPT = load_prompt_template(
    "parallel_evolving_agent/markdown_merge_system_prompt"
)
MARKDOWN_MERGE_SYSTEM_PROMPT_HEADING = load_prompt_template(
    "parallel_evolving_agent/markdown_merge_system_prompt_heading"
)

# Apply phase: convert merged patch to full file content
APPLY_SYSTEM_PROMPT_TEMPLATE = load_prompt_template(
    "parallel_evolving_agent/apply_system_prompt_template"
)

_APPLY_CONSTRAINTS = load_prompt_template("parallel_evolving_agent/apply_constraints")

# Verification: fix invalid skill state after programmatic apply
VERIFICATION_SYSTEM_PROMPT = load_prompt_template(
    "parallel_evolving_agent/verification_system_prompt"
)

# Translation: correct inexact text references before programmatic apply
TRANSLATION_SYSTEM_PROMPT = load_prompt_template(
    "parallel_evolving_agent/translation_system_prompt"
)

MARKDOWN_TRANSLATION_SYSTEM_PROMPT = load_prompt_template(
    "parallel_evolving_agent/markdown_translation_system_prompt"
)


# ---------------------------------------------------------------------------
# Batching helper (adapted from compress_skill_errors.py)
# ---------------------------------------------------------------------------


def chunk_list(items: list, batch_size: int) -> list[list]:
    """Split a list into balanced batches.

    If the remainder is smaller than half the batch size, distribute its items
    round-robin into earlier batches.  Otherwise keep it as its own batch.
    """
    total = len(items)
    if total == 0:
        return []
    if total <= batch_size:
        return [list(items)]

    n_full = total // batch_size
    remainder = total % batch_size

    if remainder == 0:
        return [list(items[i : i + batch_size]) for i in range(0, total, batch_size)]

    # Build full batches
    batches = [
        list(items[i : i + batch_size])
        for i in range(0, n_full * batch_size, batch_size)
    ]

    if remainder < batch_size / 2:
        # Small remainder — distribute round-robin into earlier batches
        for i, item in enumerate(items[n_full * batch_size :]):
            batches[i % len(batches)].append(item)
    else:
        # Large enough remainder — keep as its own batch
        batches.append(list(items[n_full * batch_size :]))

    return batches


# ---------------------------------------------------------------------------
# ParallelSkillEvolver
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Programmatic PatchEdit applicator (no LLM calls)
# ---------------------------------------------------------------------------


def _find_section_bounds(
    lines: list[str], section_header: str
) -> tuple[int, int] | None:
    """Find (start_line, end_line) for a markdown section.

    start_line is the index of the header line.
    end_line is exclusive — the index of the first line of the next
    same-or-higher-level header, or len(lines) if the section runs to EOF.
    Returns None if the section header is not found.
    """
    target = section_header.strip()
    level = len(target) - len(target.lstrip("#"))

    for i, line in enumerate(lines):
        if line.strip() == target:
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("#"):
                    j_level = len(lines[j]) - len(lines[j].lstrip("#"))
                    if j_level <= level:
                        end = j
                        break
            return i, end
    return None


def _apply_patch_edit_to_content(content: str, edit: "PatchEdit") -> str:
    """Apply a single PatchEdit to file content and return the updated content.

    All supported ops are handled in pure Python — no LLM call.
    If the target text / section cannot be found the original content is
    returned unchanged (a warning is logged).

    ``create`` and ``delete_file`` ops are not handled here; callers should
    deal with them at the file level before calling this function.
    """
    lines = content.split("\n")

    if edit.op == "append_to_section":
        bounds = _find_section_bounds(lines, edit.target_section)
        if bounds is None:
            log.warning("append_to_section: section not found: %r", edit.target_section)
            return content
        _, end = bounds
        # Insert after the last non-empty line inside the section
        insert_at = end
        while insert_at > bounds[0] + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines = lines[:insert_at] + [""] + edit.content.split("\n") + lines[insert_at:]
        return "\n".join(lines)

    if edit.op == "replace_in_section":
        if edit.target_section:
            bounds = _find_section_bounds(lines, edit.target_section)
            if bounds is None:
                log.warning(
                    "replace_in_section: section not found: %r", edit.target_section
                )
                return content
            start, end = bounds
            section_text = "\n".join(lines[start:end])
            if edit.old_text not in section_text:
                log.warning(
                    "replace_in_section: old_text not found in section %r",
                    edit.target_section,
                )
                return content
            new_section = section_text.replace(edit.old_text, edit.content, 1)
            lines = lines[:start] + new_section.split("\n") + lines[end:]
        else:
            full = "\n".join(lines)
            if edit.old_text not in full:
                log.warning("replace_in_section: old_text not found in file")
                return content
            lines = full.replace(edit.old_text, edit.content, 1).split("\n")
        return "\n".join(lines)

    if edit.op in ("insert_after", "insert_before"):
        if edit.target_section:
            bounds = _find_section_bounds(lines, edit.target_section)
            if bounds is None:
                log.warning("%s: section not found: %r", edit.op, edit.target_section)
                return content
            start, end = bounds
            search_text = "\n".join(lines[start:end])
            offset, section_len = start, end - start
        else:
            search_text = "\n".join(lines)
            offset, section_len = 0, len(lines)

        if edit.target_text not in search_text:
            log.warning("%s: target_text not found: %r", edit.op, edit.target_text[:60])
            return content

        if edit.op == "insert_after":
            new_text = search_text.replace(
                edit.target_text, edit.target_text + "\n" + edit.content, 1
            )
        else:
            new_text = search_text.replace(
                edit.target_text, edit.content + "\n" + edit.target_text, 1
            )
        lines = lines[:offset] + new_text.split("\n") + lines[offset + section_len:]
        return "\n".join(lines)

    if edit.op == "add_section":
        if edit.after_section:
            bounds = _find_section_bounds(lines, edit.after_section)
            if bounds is None:
                log.warning(
                    "add_section: after_section not found: %r, appending at EOF",
                    edit.after_section,
                )
                insert_at = len(lines)
            else:
                insert_at = bounds[1]
        else:
            insert_at = len(lines)
        header = edit.target_section or "## New Section"
        new_section = ["", header, ""] + edit.content.split("\n")
        lines = lines[:insert_at] + new_section + lines[insert_at:]
        return "\n".join(lines)

    if edit.op == "delete_section":
        bounds = _find_section_bounds(lines, edit.target_section)
        if bounds is None:
            log.warning("delete_section: section not found: %r", edit.target_section)
            return content
        start, end = bounds
        # Strip blank lines immediately before the header
        while start > 0 and not lines[start - 1].strip():
            start -= 1
        lines = lines[:start] + lines[end:]
        return "\n".join(lines)

    log.warning("Unknown PatchEdit op: %r — edit not applied", edit.op)
    return content


class ParallelSkillEvolver:
    """Evolves a skill directory using a parallel map-reduce pipeline."""

    def __init__(
        self,
        client: OpenAIClient,
        skill_dir: str | Path,
        batch_size: int = 1,
        merge_batch_size: int = 5,
        max_workers: int = 4,
        max_merge_levels: int = 5,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        verbose: bool = True,
        dry_run: bool = False,
        prompt_variant: str = "skill",
        output_dir: Path | None = None,
        parse_failure_dir: Path | None = None,
        max_skill_lines: int = 500,
        max_references: int = 5,
        max_verification_rounds: int = 3,
        skip_translation: bool = False,
        patch_pipeline: str = "json",
        semantic_item_marker_format: str = "bracket",
        enable_json_format_self_fix: bool = True,
    ):
        self.client = client
        self.skill_dir = Path(skill_dir)
        self.batch_size = batch_size
        self.merge_batch_size = merge_batch_size
        self.max_workers = max_workers
        self.max_merge_levels = max_merge_levels
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.verbose = verbose
        self.dry_run = dry_run
        self.prompt_variant = prompt_variant
        self.output_dir = Path(output_dir) if output_dir else None
        self.parse_failure_dir = Path(parse_failure_dir) if parse_failure_dir else None
        self.max_skill_lines = max_skill_lines
        self.max_references = max_references
        self.max_verification_rounds = max_verification_rounds
        self.skip_translation = skip_translation
        self.patch_pipeline = patch_pipeline
        self.semantic_item_marker_format = semantic_item_marker_format
        self.enable_json_format_self_fix = enable_json_format_self_fix
        if self.patch_pipeline not in {"json", "markdown"}:
            raise ValueError("patch_pipeline must be 'json' or 'markdown'")
        if self.semantic_item_marker_format not in {"bracket", "heading"}:
            raise ValueError(
                "semantic_item_marker_format must be 'bracket' or 'heading'"
            )

        # Build system prompts
        self._map_system_prompt = self._build_map_system_prompt()
        self._merge_system_prompt = (
            MERGE_SYSTEM_PROMPT
            if self.patch_pipeline == "json"
            else self._build_markdown_merge_system_prompt()
        )
        self._apply_system_prompt = APPLY_SYSTEM_PROMPT_TEMPLATE.format(
            constraints_section=_APPLY_CONSTRAINTS
        )
        self._verification_system_prompt = VERIFICATION_SYSTEM_PROMPT
        self._translation_system_prompt = (
            TRANSLATION_SYSTEM_PROMPT
            if self.patch_pipeline == "json"
            else MARKDOWN_TRANSLATION_SYSTEM_PROMPT
        )

        # Reuse a SkillEvolver for its utility methods
        self._evolver = SkillEvolver(
            client=client,
            skill_dir=skill_dir,
            batch_size=batch_size,
            verbose=verbose,
            temperature=temperature,
            max_tokens=max_tokens,
            dry_run=dry_run,
            prompt_variant=prompt_variant,
            max_skill_lines=max_skill_lines,
            max_references=max_references,
        )

    # -- prompt building ----------------------------------------------------

    def _build_map_system_prompt(self) -> str:
        """Build the MAP phase system prompt.

        Reuses SYSTEM_PROMPT_BASE but replaces the Output Format section
        with the concise patch format.
        """
        section = PROMPT_VARIANTS.get(self.prompt_variant)
        if section is None:
            raise ValueError(
                f"Unknown prompt variant {self.prompt_variant!r}. "
                f"Choose from: {', '.join(PROMPT_VARIANTS)}"
            )

        # Build base prompt with error record section
        base = SYSTEM_PROMPT_BASE.format(
            modification_strategies_section=MODIFICATION_STRATEGIES_SECTION,
            error_record_section=section,
        )

        # Replace the Output Format section with patch format
        # The base prompt has "## Output Format\n\n..." through the end
        output_format_marker = "## Output Format"
        idx = base.find(output_format_marker)
        output_format = (
            _MAP_OUTPUT_FORMAT
            if self.patch_pipeline == "json"
            else self._get_markdown_map_output_format()
        )
        if idx == -1:
            # Fallback: append patch format
            return base + "\n\n" + output_format
        return base[:idx] + output_format

    def _get_markdown_map_output_format(self) -> str:
        """Return the markdown output-format prompt for the configured item syntax."""
        if self.semantic_item_marker_format == "heading":
            return _MARKDOWN_MAP_OUTPUT_FORMAT_HEADING
        return _MARKDOWN_MAP_OUTPUT_FORMAT

    def _build_markdown_merge_system_prompt(self) -> str:
        """Return the markdown merge system prompt for the configured item syntax."""
        if self.semantic_item_marker_format == "heading":
            return MARKDOWN_MERGE_SYSTEM_PROMPT_HEADING
        return MARKDOWN_MERGE_SYSTEM_PROMPT

    # -- LLM calling --------------------------------------------------------

    def _call_llm(
        self,
        system_prompt: str,
        user_message: str,
        max_continuations: int = 2,
        tag: str = "editting",
        expect_semantic: bool = False,
    ) -> tuple[str, list[tuple[str, str]]]:
        """Send system + user message to LLM with continuation support.

        Similar to SkillEvolver._call_llm but accepts an arbitrary system
        prompt and uses the specified schema name for completeness checking.
        """
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_message),
        ]
        conversation_trace: list[tuple[str, str]] = [
            ("system", system_prompt),
            ("user", user_message),
        ]
        settings = ModelSettings(
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        full_response = self.client.chat(messages, settings)
        last_assistant_message = full_response
        conversation_trace.append(("assistant", _strip_think(full_response)))
        if self.verbose:
            log.debug("LLM raw output (initial, schema=%s):\n%s", tag, full_response[:500])

        for attempt in range(1, max_continuations + 1):
            if expect_semantic:
                if self._response_has_semantic_block(full_response):
                    break
            else:
                if self._response_has_complete_json_block(full_response, tag):
                    break
                _, has_closed_json_fence = self._extract_outer_fenced_block(full_response, "json")
                if has_closed_json_fence:
                    break

            log.warning(
                "Response missing complete %s output (continuation %d/%d), requesting...",
                "semantic patch block" if expect_semantic else "json block",
                attempt,
                max_continuations,
            )
            messages.append(Message(role="assistant", content=_strip_think(last_assistant_message)))
            if not expect_semantic:
                continue_prompt = load_prompt_template(
                    "parallel_evolving_agent/continue_json_prompt"
                )
            else:
                continue_prompt = load_prompt_template(
                    "parallel_evolving_agent/continue_semantic_prompt"
                )
            messages.append(Message(role="user", content=continue_prompt))
            conversation_trace.append(("user", continue_prompt))
            continuation = self.client.chat(messages, settings)
            last_assistant_message = continuation
            conversation_trace.append(("assistant", _strip_think(continuation)))
            if self.verbose:
                log.debug(
                    "LLM continuation %d/%d:\n%s",
                    attempt,
                    max_continuations,
                    continuation[:500],
                )
            full_response += continuation

        # If the response still cannot be parsed, optionally ask the model to
        # repair only the output formatting while preserving content.
        max_format_fix_rounds = 2
        if not expect_semantic and not self.enable_json_format_self_fix:
            max_format_fix_rounds = 0
        for attempt in range(1, max_format_fix_rounds + 1):
            parse_ok, feedback = self._response_parse_feedback(
                full_response, tag, expect_semantic=expect_semantic
            )
            if parse_ok:
                break

            log.warning(
                "Response parsing failed for %s (format fix %d/%d), requesting reformat...",
                "semantic markdown" if expect_semantic else tag,
                attempt,
                max_format_fix_rounds,
            )
            messages.append(Message(role="assistant", content=_strip_think(last_assistant_message)))
            if not expect_semantic:
                fix_prompt = self._build_json_format_fix_prompt(tag, feedback)
            else:
                fix_prompt = self._build_markdown_format_fix_prompt(
                    feedback,
                    semantic_item_marker_format=self.semantic_item_marker_format,
                )
            messages.append(Message(role="user", content=fix_prompt))
            conversation_trace.append(("user", fix_prompt))
            full_response = self.client.chat(messages, settings)
            last_assistant_message = full_response
            conversation_trace.append(("assistant", _strip_think(full_response)))
            if self.verbose:
                format_label = "semantic markdown" if expect_semantic else "JSON"
                log.debug(
                    "LLM %s format fix %d/%d:\n%s",
                    format_label,
                    attempt,
                    max_format_fix_rounds,
                    full_response[:500],
                )

        return full_response, conversation_trace

    @staticmethod
    def _response_has_semantic_block(response: str) -> bool:
        """Check if response contains at least one complete semantic patch block."""
        return "===== PATCH START =====" in response and "===== PATCH END =====" in response

    @staticmethod
    def _response_has_complete_json_block(response: str, schema: str) -> bool:
        """Check if response contains a complete JSON payload in a supported format."""
        payloads, _ = ParallelSkillEvolver._extract_json_payloads_with_feedback(
            response, schema
        )
        return bool(payloads)

    def _response_parse_feedback(
        self,
        response: str,
        tag: str,
        expect_semantic: bool = False,
    ) -> tuple[bool, str]:
        """Return parser success plus detailed feedback for self-fix retries."""
        if expect_semantic:
            patches, feedback = self._extract_semantic_patch_blocks_with_feedback(
                response,
                semantic_item_marker_format=self.semantic_item_marker_format,
            )
            return bool(patches), feedback
        payloads, feedback = self._extract_json_payloads_with_feedback(response, tag)
        return bool(payloads), feedback

    @staticmethod
    def _build_json_format_fix_prompt(tag: str, feedback: str) -> str:
        """Build a targeted JSON repair prompt from parser feedback."""
        if tag == "editting":
            example = (
                "```json\n"
                '{"reasoning":"Brief summary","changes":[],"changelog_entries":[]}\n'
                "```"
            )
        else:
            example = (
                "```json\n"
                '{"reasoning":"Brief summary","edits":[],"changelog_entries":[]}\n'
                "```"
            )
        return load_prompt_template(
            "parallel_evolving_agent/json_format_fix_prompt"
        ).format(
            feedback=feedback,
            example=example,
        )

    @staticmethod
    def _build_markdown_format_fix_prompt(
        feedback: str,
        semantic_item_marker_format: str = "bracket",
    ) -> str:
        """Build a targeted markdown repair prompt with a one-shot example."""
        if semantic_item_marker_format == "heading":
            example = load_prompt_template(
                "parallel_evolving_agent/markdown_format_fix_example_heading"
            )
            prompt_key = "parallel_evolving_agent/markdown_format_fix_prompt_heading"
        else:
            example = load_prompt_template(
                "parallel_evolving_agent/markdown_format_fix_example"
            )
            prompt_key = "parallel_evolving_agent/markdown_format_fix_prompt"
        return load_prompt_template(
            prompt_key
        ).format(
            feedback=feedback,
            example=example,
        )

    @staticmethod
    def _validate_patch_payload(data: object) -> list[str]:
        """Return structural validation issues for a patch payload."""
        issues: list[str] = []
        if not isinstance(data, dict):
            return ["Top-level JSON must be an object."]

        if "reasoning" not in data:
            issues.append("Missing top-level field: reasoning")
        if "edits" not in data:
            issues.append("Missing top-level field: edits")
        elif not isinstance(data.get("edits"), list):
            issues.append("Top-level field 'edits' must be a list")
        if "changelog_entries" not in data:
            issues.append("Missing top-level field: changelog_entries")
        elif not isinstance(data.get("changelog_entries"), list):
            issues.append("Top-level field 'changelog_entries' must be a list")

        edits = data.get("edits")
        if isinstance(edits, list):
            for idx, edit in enumerate(edits, start=1):
                if not isinstance(edit, dict):
                    issues.append(f"Edit #{idx} must be an object")
                    continue
                if not edit.get("file"):
                    issues.append(f"Edit #{idx} is missing required field: file")
                if not edit.get("op"):
                    issues.append(f"Edit #{idx} is missing required field: op")

        return issues

    @classmethod
    def _find_jsonish_key(cls, text: str, key: str, start: int = 0) -> int | None:
        """Find the end offset of a likely object key, tolerating missing quotes."""
        patterns = [
            rf'"{re.escape(key)}"\s*:',
            rf'{re.escape(key)}"\s*:',
            rf'"{re.escape(key)}\s*:',
            rf'(?<![A-Za-z0-9_]){re.escape(key)}\s*:',
        ]
        for pattern in patterns:
            match = re.search(pattern, text[start:])
            if match:
                return start + match.end()
        return None

    @staticmethod
    def _extract_jsonish_string(text: str, start: int) -> tuple[str | None, int]:
        """Extract a JSON-style quoted string starting at the given index."""
        if start >= len(text) or text[start] != '"':
            return None, start
        i = start + 1
        chars: list[str] = []
        while i < len(text):
            ch = text[i]
            if ch == "\\":
                if i + 1 >= len(text):
                    break
                esc = text[i + 1]
                escape_map = {
                    '"': '"',
                    "\\": "\\",
                    "/": "/",
                    "b": "\b",
                    "f": "\f",
                    "n": "\n",
                    "r": "\r",
                    "t": "\t",
                }
                chars.append(escape_map.get(esc, esc))
                i += 2
                continue
            if ch == '"':
                return "".join(chars), i + 1
            chars.append(ch)
            i += 1
        return None, start

    @classmethod
    def _extract_jsonish_container(cls, text: str, start: int, opener: str) -> tuple[str | None, int]:
        """Extract a balanced JSON-like container string starting at the given index."""
        closer = "]" if opener == "[" else "}"
        if start >= len(text) or text[start] != opener:
            return None, start
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1], i + 1
        return None, start

    @classmethod
    def _extract_jsonish_string_field(cls, text: str, key: str) -> str | None:
        """Extract a likely string field from malformed patch JSON."""
        value_start = cls._find_jsonish_key(text, key)
        if value_start is None:
            return None
        while value_start < len(text) and text[value_start].isspace():
            value_start += 1
        value, _ = cls._extract_jsonish_string(text, value_start)
        return value

    @classmethod
    def _extract_jsonish_array_field(cls, text: str, key: str) -> str | None:
        """Extract a likely array field from malformed patch JSON."""
        value_start = cls._find_jsonish_key(text, key)
        if value_start is None:
            return None
        while value_start < len(text) and text[value_start].isspace():
            value_start += 1
        value, _ = cls._extract_jsonish_container(text, value_start, "[")
        return value

    @classmethod
    def _extract_jsonish_object_chunks(cls, array_text: str) -> list[str]:
        """Extract top-level object chunks from a malformed JSON array."""
        inner = array_text.strip()
        if inner.startswith("[") and inner.endswith("]"):
            inner = inner[1:-1]
        chunks: list[str] = []
        depth = 0
        start_idx: int | None = None
        for i, ch in enumerate(inner):
            if ch == "{":
                if depth == 0:
                    start_idx = i
                depth += 1
            elif ch == "}":
                if depth == 0:
                    continue
                depth -= 1
                if depth == 0 and start_idx is not None:
                    chunks.append(inner[start_idx : i + 1])
                    start_idx = None
        return chunks

    @classmethod
    def _extract_jsonish_string_list(cls, array_text: str) -> list[str]:
        """Extract quoted strings from a malformed JSON array."""
        values: list[str] = []
        inner = array_text.strip()
        if inner.startswith("[") and inner.endswith("]"):
            inner = inner[1:-1]
        i = 0
        while i < len(inner):
            if inner[i] == '"':
                value, next_i = cls._extract_jsonish_string(inner, i)
                if value is None:
                    break
                values.append(value)
                i = next_i
            else:
                i += 1
        return values

    @classmethod
    def _heuristic_parse_patch_payload(cls, raw: str) -> dict | None:
        """Best-effort extraction for malformed patch JSON payloads."""
        reasoning = cls._extract_jsonish_string_field(raw, "reasoning")
        edits_match = re.search(
            r'"?edits"?\s*:\s*(\[.*?\])\s*,\s*"?changelog_entries"?\s*:',
            raw,
            re.DOTALL,
        )
        changelog_match = re.search(
            r'"?changelog_entries"?\s*:\s*(\[[\s\S]*\])',
            raw,
        )
        edits_array = edits_match.group(1) if edits_match else None
        changelog_array = changelog_match.group(1) if changelog_match else None
        if reasoning is None or edits_array is None or changelog_array is None:
            return None

        edits: list[dict[str, str]] = []
        for chunk in cls._extract_jsonish_object_chunks(edits_array):
            edit: dict[str, str] = {}
            for key in (
                "file",
                "op",
                "target_section",
                "target_text",
                "content",
                "old_text",
                "after_section",
            ):
                value = cls._extract_jsonish_string_field(chunk, key)
                if value is not None:
                    edit[key] = value
            if edit:
                edits.append(edit)

        payload = {
            "reasoning": reasoning,
            "edits": edits,
            "changelog_entries": cls._extract_jsonish_string_list(changelog_array),
        }
        issues = cls._validate_patch_payload(payload)
        if issues:
            return None
        return payload

    @staticmethod
    def _extract_fenced_blocks(response: str, language: str) -> list[str]:
        """Extract complete fenced code blocks for the given language."""
        pattern = rf"```{re.escape(language)}[^\n]*\n(.*?)```"
        return [match.group(1).strip() for match in re.finditer(pattern, response, re.DOTALL)]

    @staticmethod
    def _extract_outer_fenced_block(response: str, language: str) -> tuple[str | None, bool]:
        """Extract from the first opening fence to the last closing fence."""
        start_pattern = rf"```{re.escape(language)}[^\n]*\n"
        start_match = re.search(start_pattern, response)
        if not start_match:
            return None, False

        tail = response[start_match.end():]
        closing_matches = list(re.finditer(r"(?m)^```[ \t]*\r?$", tail))
        if closing_matches:
            return tail[:closing_matches[-1].start()].strip(), True
        return tail.strip(), False

    @staticmethod
    def _has_unclosed_fenced_block(response: str, language: str) -> bool:
        """Return True when an opening fence exists without a closing fence."""
        body, has_closing = ParallelSkillEvolver._extract_outer_fenced_block(response, language)
        return body is not None and not has_closing

    @classmethod
    def _extract_json_payloads_with_feedback(
        cls,
        response: str,
        schema: str,
    ) -> tuple[list[dict], str]:
        """Extract valid JSON payloads from fenced json blocks plus diagnostics."""
        outer_block, has_closing = cls._extract_outer_fenced_block(response, "json")
        if outer_block is not None and has_closing:
            blocks = [outer_block]
            parsed_payloads: list[dict] = []
            diagnostics: list[str] = []
            for raw in blocks:
                block_idx = len(diagnostics) + len(parsed_payloads) + 1
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as exc:
                    heuristic = cls._heuristic_parse_patch_payload(raw)
                    if heuristic is not None and schema != "editting":
                        log.warning(
                            "Recovered malformed fenced json block %d using heuristic field extraction after JSON decode failure: %s at char %d",
                            block_idx,
                            exc.msg,
                            exc.pos,
                        )
                        parsed_payloads.append(heuristic)
                        continue
                    diagnostics.append(
                        f"Block {block_idx}: JSON decode error: {exc.msg} "
                        f"(line {exc.lineno}, column {exc.colno}, char {exc.pos})"
                    )
                    continue
                issues = (
                    cls._validate_patch_payload(parsed)
                    if schema != "editting"
                    else cls._validate_apply_payload(parsed)
                )
                if issues:
                    diagnostics.append(
                        f"Block {block_idx}: " + "; ".join(issues)
                    )
                    continue
                parsed_payloads.append(parsed)
            if parsed_payloads:
                return parsed_payloads, ""
            log.warning(
                "JSON parse failed for all %d fenced json block(s)",
                len(blocks),
            )
            return [], " | ".join(diagnostics) if diagnostics else "No valid fenced json blocks found."

        if outer_block is not None and not has_closing:
            return [], "Found an opening ```json fence but the closing ``` fence is missing."

        # Try bare JSON
        stripped = response.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                heuristic = cls._heuristic_parse_patch_payload(stripped)
                if heuristic is not None and schema != "editting":
                    log.warning(
                        "Recovered malformed bare JSON payload using heuristic field extraction after JSON decode failure: %s at char %d",
                        exc.msg,
                        exc.pos,
                    )
                    return [heuristic], ""
                return [], (
                    f"Bare JSON decode error: {exc.msg} "
                    f"(line {exc.lineno}, column {exc.colno}, char {exc.pos})"
                )
            issues = (
                cls._validate_patch_payload(parsed)
                if schema != "editting"
                else cls._validate_apply_payload(parsed)
            )
            if issues:
                return [], "; ".join(issues)
            return [parsed], ""
        return [], "No fenced json block found."

    @classmethod
    def _extract_json_payloads(cls, response: str, schema: str) -> list[dict]:
        """Extract all valid JSON payloads from fenced json blocks."""
        payloads, _ = cls._extract_json_payloads_with_feedback(response, schema)
        return payloads

    @staticmethod
    def _validate_apply_payload(data: object) -> list[str]:
        """Return structural validation issues for a full-file apply payload."""
        issues: list[str] = []
        if not isinstance(data, dict):
            return ["Top-level JSON must be an object."]
        if "reasoning" not in data:
            issues.append("Missing top-level field: reasoning")
        if "changes" not in data:
            issues.append("Missing top-level field: changes")
        elif not isinstance(data.get("changes"), list):
            issues.append("Top-level field 'changes' must be a list")
        if "changelog_entries" not in data:
            issues.append("Missing top-level field: changelog_entries")
        elif not isinstance(data.get("changelog_entries"), list):
            issues.append("Top-level field 'changelog_entries' must be a list")

        changes = data.get("changes")
        if isinstance(changes, list):
            for idx, change in enumerate(changes, start=1):
                if not isinstance(change, dict):
                    issues.append(f"Change #{idx} must be an object")
                    continue
                if not change.get("file"):
                    issues.append(f"Change #{idx} is missing required field: file")
                if not change.get("action"):
                    issues.append(f"Change #{idx} is missing required field: action")
        return issues

    @staticmethod
    def _parse_semantic_patch_block(
        block: str,
        block_idx: int,
        semantic_item_marker_format: str = "bracket",
    ) -> tuple[SemanticPatch | None, str]:
        """Parse one semantic markdown patch block and return diagnostics if invalid."""
        lines = block.strip().splitlines()
        reasoning_lines: list[str] = []
        changelog_entries: list[str] = []
        items: list[SemanticPatchItem] = []
        issues: list[str] = []

        saw_reasoning = False
        saw_changelog = False
        saw_items = False
        mode: str | None = None
        current_item: dict[str, list[str]] | None = None
        current_field: str | None = None
        current_item_number = 0
        current_open_item_id: int | None = None
        field_labels = {
            "Target File": "target_file",
            "Edit Intent": "edit_intent",
            "Location Hint": "location_hint",
            "Change Instruction": "change_instruction",
        }
        item_start_pattern = re.compile(r"^\[ITEM_(\d+)_START\]$")
        item_end_pattern = re.compile(r"^\[ITEM_(\d+)_END\]$")
        heading_item_start_pattern = re.compile(r"^#{1,}\s*Item\s+(\d+)\s*$", re.IGNORECASE)

        def finish_item() -> None:
            nonlocal current_item, current_field, current_open_item_id
            if current_item is None:
                return
            required = {
                "target_file": "Target File",
                "edit_intent": "Edit Intent",
                "location_hint": "Location Hint",
                "change_instruction": "Change Instruction",
            }
            normalized_item = {
                key: "\n".join(current_item.get(key, [])).strip()
                for key in required
            }
            missing = [label for key, label in required.items() if not normalized_item.get(key, "").strip()]
            if missing:
                issues.append(
                    f"Block {block_idx}: item {current_item_number} missing required field(s): {', '.join(missing)}"
                )
            else:
                items.append(
                    SemanticPatchItem(
                        target_file=normalized_item["target_file"],
                        edit_intent=normalized_item["edit_intent"],
                        location_hint=normalized_item["location_hint"],
                        change_instruction=normalized_item["change_instruction"],
                    )
                )
            current_item = None
            current_field = None
            current_open_item_id = None

        for raw_line in lines:
            line = raw_line.rstrip("\n")
            stripped = line.strip()
            if semantic_item_marker_format == "heading":
                start_match = heading_item_start_pattern.match(stripped)
            else:
                start_match = item_start_pattern.match(stripped)
            end_match = item_end_pattern.match(stripped)

            if start_match:
                if not saw_items:
                    saw_items = True
                    mode = "items"
                if current_item is not None and current_open_item_id is not None:
                    finish_item()
                current_item_number = int(start_match.group(1))
                current_open_item_id = current_item_number
                current_item = {}
                current_field = None
                mode = "item"
                continue

            if end_match and semantic_item_marker_format == "bracket":
                closing_item_id = int(end_match.group(1))
                if current_item is None or current_open_item_id is None:
                    issues.append(
                        f"Block {block_idx}: closing marker ITEM_{closing_item_id}_END found without an open item"
                    )
                    continue
                if closing_item_id != current_open_item_id:
                    issues.append(
                        f"Block {block_idx}: closing marker ITEM_{closing_item_id}_END does not match open item {current_open_item_id}"
                    )
                    current_item = None
                    current_field = None
                    current_open_item_id = None
                    mode = "items"
                    continue
                finish_item()
                mode = "items"
                continue

            if current_item is not None:
                field_matched = False
                for label, field_key in field_labels.items():
                    prefix = f"{label}:"
                    if stripped.replace("_", " ").startswith(prefix):
                        value = stripped.split(":", 1)[1].strip()
                        current_item[field_key] = [value] if value else []
                        current_field = field_key
                        mode = "item"
                        field_matched = True
                        break
                if field_matched:
                    continue

                if current_field is not None:
                    current_item.setdefault(current_field, []).append(line)
                    continue

            if stripped.startswith("Reasoning:"):
                finish_item()
                saw_reasoning = True
                mode = "reasoning"
                inline_reasoning = stripped.split(":", 1)[1].strip()
                if inline_reasoning:
                    reasoning_lines.append(inline_reasoning)
                continue
            if stripped == "Changelog:":
                finish_item()
                saw_changelog = True
                mode = "changelog"
                continue
            if stripped == "Items:":
                finish_item()
                saw_items = True
                mode = "items"
                continue
            if mode == "reasoning":
                reasoning_lines.append(line)
                continue
            if mode == "changelog":
                if stripped.startswith("-"):
                    changelog_entries.append(stripped[1:].strip())
                elif stripped:
                    changelog_entries.append(stripped)

        finish_item()

        if not saw_reasoning:
            issues.append(f"Block {block_idx}: missing Reasoning section")
        if not saw_changelog:
            issues.append(f"Block {block_idx}: missing Changelog section")
        if not saw_items:
            issues.append(f"Block {block_idx}: missing Items section")
        reasoning = "\n".join(reasoning_lines).strip()
        if not reasoning:
            issues.append(f"Block {block_idx}: Reasoning section is empty")
        if not items:
            issues.append(f"Block {block_idx}: no valid items found")

        feedback = " | ".join(issues) if issues else ""
        if not items:
            return None, feedback

        return (
            SemanticPatch(
                reasoning=reasoning,
                items=items,
                changelog_entries=changelog_entries,
                raw_markdown=block.strip(),
            ),
            feedback,
        )

    @classmethod
    def _extract_semantic_patch_blocks_with_feedback(
        cls,
        response: str,
        semantic_item_marker_format: str = "bracket",
    ) -> tuple[list[SemanticPatch], str]:
        """Extract semantic markdown patch blocks plus diagnostics."""
        pattern = r"===== PATCH START =====\s*(.*?)\s*===== PATCH END ====="
        matches = list(re.finditer(pattern, response, re.DOTALL))
        if not matches:
            return [], "No ===== PATCH START ===== ... ===== PATCH END ===== block found."
        parsed: list[SemanticPatch] = []
        diagnostics: list[str] = []
        for idx, match in enumerate(matches, start=1):
            patch, feedback = cls._parse_semantic_patch_block(
                match.group(1),
                idx,
                semantic_item_marker_format=semantic_item_marker_format,
            )
            if patch is None:
                if feedback:
                    diagnostics.append(feedback)
                continue
            parsed.append(patch)
            if feedback:
                diagnostics.append(feedback)
        if parsed:
            return parsed, " | ".join(diagnostics) if diagnostics else ""
        return [], " | ".join(diagnostics) if diagnostics else "No valid semantic patch blocks found."

    @classmethod
    def _extract_semantic_patch_blocks(
        cls,
        response: str,
        semantic_item_marker_format: str = "bracket",
    ) -> list[SemanticPatch]:
        """Extract all valid semantic markdown patch blocks."""
        patches, _ = cls._extract_semantic_patch_blocks_with_feedback(
            response,
            semantic_item_marker_format=semantic_item_marker_format,
        )
        return patches

    def _render_semantic_item_marker(self, item_idx: int) -> str:
        """Render an item marker that matches the configured semantic syntax."""
        if self.semantic_item_marker_format == "heading":
            return f"### Item {item_idx}"
        return f"[ITEM_{item_idx}_START]"

    def _render_semantic_merge_user_item_marker(self, item_idx: int) -> str:
        """Render the item heading used inside semantic merge user-message context."""
        if self.semantic_item_marker_format == "heading":
            return f"#### Item {item_idx}"
        return f"[ITEM_{item_idx}_START]"

    # -- reading state (delegate to evolver) --------------------------------

    def read_skill_state(self) -> dict[str, str]:
        return self._evolver.read_skill_state()

    # -- user message building (reuse from evolver) -------------------------

    def _build_map_user_message(
        self,
        skill_state: dict[str, str],
        records: list[dict],
        batch_idx: int,
        total_batches: int,
    ) -> str:
        """Build user message for a MAP phase call.

        Reuses the evolver's message format but works with the patch system prompt.
        """
        return self._evolver.build_user_message(
            skill_state, records, batch_idx, total_batches
        )

    def _build_map_user_message_from_patterns(
        self,
        skill_state: dict[str, str],
        patterns: dict[str, list[dict]],
        batch_idx: int,
        total_batches: int,
    ) -> str:
        """Build user message for MAP phase with pattern input."""
        return self._evolver.build_user_message_from_patterns(
            skill_state, patterns, batch_idx, total_batches
        )

    def _build_merge_user_message(
        self,
        skill_state: dict[str, str],
        patches: list[Patch],
    ) -> str:
        """Build user message for a MERGE phase call."""
        parts: list[str] = []

        # Original skill state for context
        parts.append(load_prompt_template("parallel_evolving_agent/original_skill_folder_header"))
        for path, content in sorted(skill_state.items()):
            n_lines = content.count("\n") + 1
            parts.append(f"### {path} ({n_lines} lines)")
            parts.append(f"```markdown\n{content}\n```\n")

        # Patches to merge
        parts.append(
            load_prompt_template("parallel_evolving_agent/patches_to_merge_header").format(
                patch_count=len(patches)
            )
        )
        for i, patch in enumerate(patches):
            parts.append(f"### Patch {i + 1}")
            parts.append(f"**Reasoning**: {patch.reasoning}")
            parts.append(f"**Edits** ({len(patch.edits)}):")
            for edit in patch.edits:
                edit_dict = {
                    "file": edit.file,
                    "op": edit.op,
                }
                if edit.target_section:
                    edit_dict["target_section"] = edit.target_section
                if edit.target_text:
                    edit_dict["target_text"] = edit.target_text
                if edit.content:
                    edit_dict["content"] = edit.content
                if edit.old_text:
                    edit_dict["old_text"] = edit.old_text
                if edit.after_section:
                    edit_dict["after_section"] = edit.after_section
                parts.append(f"  - {json.dumps(edit_dict)}")
            parts.append(f"**Changelog**: {patch.changelog_entries}")
            parts.append("")

        return "\n".join(parts)

    def _build_merge_user_message_semantic(
        self,
        skill_state: dict[str, str],
        patches: list[SemanticPatch],
    ) -> str:
        """Build user message for a semantic markdown MERGE phase call."""
        parts: list[str] = []
        parts.append(load_prompt_template("parallel_evolving_agent/original_skill_folder_header"))
        for path, content in sorted(skill_state.items()):
            n_lines = content.count("\n") + 1
            parts.append(f"### {path} ({n_lines} lines)")
            parts.append(f"```markdown\n{content}\n```\n")

        parts.append(
            load_prompt_template(
                "parallel_evolving_agent/semantic_patches_to_merge_header"
            ).format(patch_count=len(patches))
        )
        for i, patch in enumerate(patches, start=1):
            parts.append(f"### Patch {i}")
            parts.append(f"Reasoning: {patch.reasoning}")
            parts.append("Changelog:")
            for entry in patch.changelog_entries:
                parts.append(f"- {entry}")
            parts.append("Items:")
            for item_idx, item in enumerate(patch.items, start=1):
                parts.append(self._render_semantic_merge_user_item_marker(item_idx))
                parts.append(f"Target File: {item.target_file}")
                parts.append(f"Edit Intent: {item.edit_intent}")
                parts.append(f"Location Hint: {item.location_hint}")
                parts.append("Change Instruction:")
                parts.append(item.change_instruction)
            parts.append("")
        return "\n".join(parts)

    def _build_apply_user_message(
        self,
        skill_state: dict[str, str],
        final_patch: Patch,
    ) -> str:
        """Build user message for the APPLY phase."""
        parts: list[str] = []

        # Current skill state
        parts.append(load_prompt_template("parallel_evolving_agent/current_skill_folder_header"))
        for path, content in sorted(skill_state.items()):
            n_lines = content.count("\n") + 1
            parts.append(f"### {path} ({n_lines} lines)")
            parts.append(f"```markdown\n{content}\n```\n")

        # Final merged patch
        parts.append(load_prompt_template("parallel_evolving_agent/merged_patch_to_apply_header"))
        parts.append(f"**Reasoning**: {final_patch.reasoning}\n")
        parts.append("**Edits:**")
        for edit in final_patch.edits:
            edit_dict = {
                "file": edit.file,
                "op": edit.op,
            }
            if edit.target_section:
                edit_dict["target_section"] = edit.target_section
            if edit.target_text:
                edit_dict["target_text"] = edit.target_text
            if edit.content:
                edit_dict["content"] = edit.content
            if edit.old_text:
                edit_dict["old_text"] = edit.old_text
            if edit.after_section:
                edit_dict["after_section"] = edit.after_section
            parts.append(f"- {json.dumps(edit_dict)}")

        parts.append(f"\n**Changelog**: {final_patch.changelog_entries}")

        # Size status
        skill_md_content = skill_state.get("SKILL.md", "")
        skill_lines = skill_md_content.count("\n") + 1
        ref_count = sum(1 for p in skill_state if p.startswith("references/"))
        parts.append(load_prompt_template("parallel_evolving_agent/skill_folder_size_status_header"))
        parts.append(
            load_prompt_template("parallel_evolving_agent/skill_md_status_line").format(
                skill_lines=skill_lines,
                max_skill_lines=self.max_skill_lines,
            )
        )
        parts.append(
            load_prompt_template("parallel_evolving_agent/reference_files_status_line").format(
                ref_count=ref_count,
                max_references=self.max_references,
            )
        )

        parts.append(load_prompt_template("parallel_evolving_agent/apply_all_edits_instruction"))

        return "\n".join(parts)

    def _build_verification_user_message(
        self,
        skill_state: dict[str, str],
        validation_error: str,
    ) -> str:
        """Build user message for a verification fix call."""
        parts: list[str] = []

        parts.append(load_prompt_template("parallel_evolving_agent/failed_validation_header"))
        for path, content in sorted(skill_state.items()):
            n_lines = content.count("\n") + 1
            parts.append(f"### {path} ({n_lines} lines)")
            parts.append(f"```markdown\n{content}\n```\n")

        parts.append(load_prompt_template("parallel_evolving_agent/validation_error_header"))
        parts.append(f"```\n{validation_error}\n```\n")
        parts.append(load_prompt_template("parallel_evolving_agent/verification_instruction"))
        return "\n".join(parts)

    # -- prompt-response saving for debugging --------------------------------

    def _save_prompt_response(
        self,
        phase: str,
        label: str,
        system_prompt: str,
        user_message: str,
        response: str,
    ) -> None:
        """Save a prompt-response sample to disk for debugging.

        Only writes when ``self.output_dir`` is set. Files are saved as
        markdown for easy reading::

            {output_dir}/prompt_samples/{phase}/{label}.md
        """
        if self.output_dir is None:
            return
        sample_dir = self.output_dir / "prompt_samples" / phase
        sample_dir.mkdir(parents=True, exist_ok=True)
        path = sample_dir / f"{label}.md"
        parts = [
            "===== PROMPT SAMPLE START =====\n",
            f"PHASE: {phase}\n",
            f"LABEL: {label}\n",
            "\n",
            "===== SYSTEM MESSAGE 1 START =====\n",
            system_prompt,
            "\n",
            "===== SYSTEM MESSAGE 1 END =====\n",
            "\n",
            "===== USER MESSAGE 1 START =====\n",
            user_message,
            "\n",
            "===== USER MESSAGE 1 END =====\n",
            "\n",
            "===== ASSISTANT MESSAGE 1 START =====\n",
            response,
            "\n",
            "===== ASSISTANT MESSAGE 1 END =====\n",
            "\n",
            "===== PROMPT SAMPLE END =====\n",
        ]
        path.write_text("\n".join(parts), encoding="utf-8")

    def _save_parse_failure(
        self,
        phase: str,
        label: str,
        tag: str,
        conversation_trace: list[tuple[str, str]],
        response: str,
    ) -> None:
        """Save parse-failed prompt/response artifacts for debugging.

        Writes to:
            {parse_failure_dir}/{phase}/{timestamp}_{label}_{tag}.md
        """
        if self.parse_failure_dir is None:
            return

        phase_dir = self.parse_failure_dir / phase
        phase_dir.mkdir(parents=True, exist_ok=True)
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "unknown"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = phase_dir / f"{timestamp}_{safe_label}_{tag}_parse_failed.md"
        expected_format = (
            "semantic-patch-block"
            if tag == "markdown"
            else "json-fence"
        )
        parts = [
            f"===== PARSE FAILURE START =====\n",
            f"PHASE: {phase}\n",
            f"LABEL: {label}\n",
            f"EXPECTED FORMAT: {expected_format}\n",
            "\n",
        ]
        role_counts: dict[str, int] = {}
        for role, content in conversation_trace:
            role_counts[role] = role_counts.get(role, 0) + 1
            role_name = f"{role.upper()} MESSAGE {role_counts[role]}"
            parts.extend(
                [
                    f"===== {role_name} START =====\n",
                    content,
                    "\n",
                    f"===== {role_name} END =====\n",
                    "\n",
                ]
            )
        parts.extend(
            [
                "===== FINAL RAW LLM RESPONSE START =====\n",
                response,
                "\n",
                "===== FINAL RAW LLM RESPONSE END =====\n",
                "\n",
                "===== PARSE FAILURE END =====\n",
            ]
        )
        path.write_text("\n".join(parts), encoding="utf-8")
        log.info("Saved parse-failure artifact to %s", path)

    # -- patch parsing ------------------------------------------------------

    @staticmethod
    def _patch_from_data(data: dict) -> Patch:
        """Build a Patch from parsed JSON data."""
        reasoning = data.get("reasoning", "")
        changelog = data.get("changelog_entries", [])
        if not isinstance(changelog, list):
            changelog = [str(changelog)]

        edits: list[PatchEdit] = []
        for edit_data in data.get("edits", []):
            if not isinstance(edit_data, dict):
                continue
            op = str(edit_data.get("op", ""))
            op = _PATCH_OP_ALIASES.get(op, op)
            edits.append(
                PatchEdit(
                    file=edit_data.get("file", ""),
                    op=op,
                    target_section=edit_data.get("target_section", ""),
                    target_text=edit_data.get("target_text", ""),
                    content=edit_data.get("content", ""),
                    old_text=edit_data.get("old_text", ""),
                    after_section=edit_data.get("after_section", ""),
                )
            )

        return Patch(
            reasoning=reasoning,
            edits=edits,
            changelog_entries=changelog,
            raw_json=data,
        )

    @staticmethod
    def _coalesce_semantic_patches(
        patches: list[SemanticPatch],
    ) -> SemanticPatch | None:
        """Combine multiple SemanticPatch objects into one, preserving order."""
        if not patches:
            return None
        if len(patches) == 1:
            return patches[0]
        reasoning_parts = [p.reasoning for p in patches if p.reasoning]
        items: list[SemanticPatchItem] = []
        changelog_entries: list[str] = []
        raw_blocks: list[str] = []
        batch_index = patches[0].batch_index
        for patch in patches:
            items.extend(patch.items)
            changelog_entries.extend(patch.changelog_entries)
            if patch.raw_markdown:
                raw_blocks.append(patch.raw_markdown)
        return SemanticPatch(
            reasoning="\n\n".join(reasoning_parts),
            items=items,
            changelog_entries=changelog_entries,
            batch_index=batch_index,
            raw_markdown="\n\n".join(raw_blocks),
        )

    @staticmethod
    def _coalesce_patches(patches: list[Patch]) -> Patch | None:
        """Combine multiple Patch objects into one, preserving order."""
        if not patches:
            return None
        if len(patches) == 1:
            return patches[0]

        reasoning_parts = [p.reasoning for p in patches if p.reasoning]
        changelog_entries: list[str] = []
        edits: list[PatchEdit] = []
        raw_jsons: list[dict] = []
        batch_index = patches[0].batch_index
        for patch in patches:
            changelog_entries.extend(patch.changelog_entries)
            edits.extend(patch.edits)
            raw_jsons.append(patch.raw_json)

        return Patch(
            reasoning="\n\n".join(reasoning_parts),
            edits=edits,
            changelog_entries=changelog_entries,
            batch_index=batch_index,
            raw_json={"patches": raw_jsons},
        )

    @staticmethod
    def _enforce_create_pairing(
        patches: list[Patch],
        skill_state: dict[str, str],
    ) -> list[Patch]:
        """Drop unpaired create ops and SKILL.md link edits from MAP patches.

        Rules (applied per-patch):
        - A ``create`` edit for ``references/X.md`` is kept only when the same
          patch also contains a non-create edit whose content references
          ``references/X.md`` (i.e. inserts the paired SKILL.md link).
        - A non-create edit to SKILL.md (or any file) is kept only when every
          new ``references/X.md`` it mentions either already exists in
          ``skill_state`` or has a matching ``create`` edit in the same patch.
        - Orphaned edits on either side are logged as WARNINGs and dropped.
        """
        sanitized: list[Patch] = []
        for patch in patches:
            idx = patch.batch_index

            # --- collect create targets in this patch ---
            created_files: set[str] = {
                edit.file for edit in patch.edits if edit.op == "create"
            }

            # --- collect new references mentioned in non-create edit content ---
            linked_files: set[str] = set()
            for edit in patch.edits:
                if edit.op == "create":
                    continue
                for match in _REFERENCE_PATH_PATTERN.findall(edit.content):
                    if match not in skill_state:
                        linked_files.add(match)

            # --- filter edits ---
            kept: list[PatchEdit] = []
            for edit in patch.edits:
                if edit.op == "create":
                    # Keep only if the same patch also links to this file
                    if edit.file in linked_files:
                        kept.append(edit)
                    else:
                        log.warning(
                            "Dropping orphaned create op for '%s' in MAP patch "
                            "batch_index=%s (no matching SKILL.md link in same patch)",
                            edit.file,
                            idx,
                        )
                else:
                    # Check that every new reference this edit mentions has a
                    # corresponding create in the same patch (or already exists)
                    new_refs = [
                        r
                        for r in _REFERENCE_PATH_PATTERN.findall(edit.content)
                        if r not in skill_state
                    ]
                    missing = [r for r in new_refs if r not in created_files]
                    if missing:
                        log.warning(
                            "Dropping edit to '%s' in MAP patch batch_index=%s "
                            "because it links to new reference(s) %s without a "
                            "create op in the same patch",
                            edit.file,
                            idx,
                            missing,
                        )
                    else:
                        kept.append(edit)

            sanitized.append(
                Patch(
                    reasoning=patch.reasoning,
                    edits=kept,
                    changelog_entries=patch.changelog_entries,
                    batch_index=patch.batch_index,
                    raw_json=patch.raw_json,
                )
            )

        return sanitized

    def _parse_patches(
        self,
        response: str,
        tag: str = "patch",
        phase: str | None = None,
        label: str | None = None,
        conversation_trace: list[tuple[str, str]] | None = None,
    ) -> list[Patch]:
        """Parse one or more Patch objects from an LLM response."""
        payloads = self._extract_json_payloads(response, tag)
        if not payloads:
            log.warning("Could not parse fenced json patch payload from response")
            if (
                phase is not None
                and label is not None
                and conversation_trace is not None
            ):
                self._save_parse_failure(
                    phase=phase,
                    label=label,
                    tag="json",
                    conversation_trace=conversation_trace,
                    response=response,
                )
            return []
        return [self._patch_from_data(data) for data in payloads]

    def _parse_semantic_patches(
        self,
        response: str,
        phase: str | None = None,
        label: str | None = None,
        conversation_trace: list[tuple[str, str]] | None = None,
        tag: str = "patch",
    ) -> list[SemanticPatch]:
        """Parse one or more semantic markdown patch blocks."""
        patches, feedback = self._extract_semantic_patch_blocks_with_feedback(
            response,
            semantic_item_marker_format=self.semantic_item_marker_format,
        )
        if feedback:
            log.warning("Semantic patch parse warning: %s", feedback)
        if not patches:
            log.warning("Could not parse semantic patch blocks from response")
        if (not patches or feedback) and (
            phase is not None
            and label is not None
            and conversation_trace is not None
        ):
            self._save_parse_failure(
                phase=phase,
                label=label,
                tag="markdown",
                conversation_trace=conversation_trace,
                response=response,
            )
        if not patches:
            return []
        return patches

    # -- MAP phase ----------------------------------------------------------

    def _run_single_map(
        self,
        skill_state: dict[str, str],
        batch: list[dict],
        batch_idx: int,
        total_batches: int,
    ) -> list[Patch]:
        """Run a single MAP phase LLM call for one batch of records."""
        user_msg = self._build_map_user_message(
            skill_state, batch, batch_idx, total_batches
        )
        response, conversation_trace = self._call_llm(
            self._map_system_prompt, user_msg, tag="patch"
        )
        self._save_prompt_response(
            "map", f"batch_{batch_idx:04d}",
            self._map_system_prompt, user_msg, response,
        )
        patches = self._parse_patches(
            response,
            tag="patch",
            phase="map",
            label=f"batch_{batch_idx:04d}",
            conversation_trace=conversation_trace,
        )
        for patch in patches:
            patch.batch_index = batch_idx
        return patches

    def _run_single_map_patterns(
        self,
        skill_state: dict[str, str],
        pattern_batch: dict[str, list[dict]],
        batch_idx: int,
        total_batches: int,
    ) -> list[Patch]:
        """Run a single MAP phase LLM call for one batch of patterns."""
        user_msg = self._build_map_user_message_from_patterns(
            skill_state, pattern_batch, batch_idx, total_batches
        )
        response, conversation_trace = self._call_llm(
            self._map_system_prompt, user_msg, tag="patch"
        )
        self._save_prompt_response(
            "map", f"batch_{batch_idx:04d}",
            self._map_system_prompt, user_msg, response,
        )
        patches = self._parse_patches(
            response,
            tag="patch",
            phase="map",
            label=f"batch_{batch_idx:04d}",
            conversation_trace=conversation_trace,
        )
        for patch in patches:
            patch.batch_index = batch_idx
        return patches

    def run_map_phase(
        self,
        skill_state: dict[str, str],
        records: list[dict],
    ) -> list[Patch]:
        """Run the MAP phase: parallel patch proposal.

        Each batch of records independently proposes a patch against
        the frozen original skill state.
        """
        batches = chunk_list(records, self.batch_size)
        total_batches = len(batches)

        if not batches:
            return []

        log.info(
            "MAP phase: %d records in %d batches (batch_size=%d, workers=%d)",
            len(records),
            total_batches,
            self.batch_size,
            self.max_workers,
        )

        patches_by_batch: dict[int, list[Patch]] = {}
        success_count = 0
        fail_count = 0
        pbar = tqdm(total=total_batches, desc="MAP", unit="batch")

        # Run all batches in parallel. Keep debug output focused on batch 1.
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    self._run_single_map,
                    skill_state,
                    batch,
                    idx + 1,
                    total_batches,
                ): idx + 1
                for idx, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                batch_idx = futures[future]
                try:
                    batch_patches = future.result()
                    if batch_patches:
                        patches_by_batch[batch_idx] = batch_patches
                        success_count += 1
                        if self.verbose and batch_idx == 1:
                            total_edits = sum(len(p.edits) for p in batch_patches)
                            tqdm.write(
                                f"  MAP batch 1: {len(batch_patches)} patch(es), "
                                f"{total_edits} edits total"
                            )
                    else:
                        fail_count += 1
                        if batch_idx == 1:
                            tqdm.write("  MAP batch 1: failed to parse patch")
                        else:
                            tqdm.write(
                                f"  MAP batch {batch_idx}/{total_batches}: parse failed"
                            )
                except Exception as e:
                    fail_count += 1
                    if batch_idx == 1:
                        tqdm.write(f"  MAP batch 1: error: {e}")
                    else:
                        tqdm.write(
                            f"  MAP batch {batch_idx}/{total_batches}: error: {e}"
                        )
                pbar.set_postfix(ok=success_count, fail=fail_count, refresh=False)
                pbar.update(1)

        pbar.close()
        patches: list[Patch] = []
        for idx in sorted(patches_by_batch):
            patches.extend(patches_by_batch[idx])
        log.info("MAP phase complete: %d/%d patches produced", len(patches), total_batches)
        return patches

    def run_map_phase_patterns(
        self,
        skill_state: dict[str, str],
        patterns_by_type: dict[str, list[dict]],
    ) -> list[Patch]:
        """Run the MAP phase with pattern-based input."""
        # Build batches of patterns (same logic as SkillEvolver.run_evolution_from_patterns)
        max_patterns = max(
            (len(pats) for pats in patterns_by_type.values()), default=0
        )
        batches: list[dict[str, list[dict]]] = []
        for i in range(0, max_patterns, self.batch_size):
            batch: dict[str, list[dict]] = {}
            for item_type, pats in patterns_by_type.items():
                batch[item_type] = pats[i : i + self.batch_size]
            if any(batch.values()):
                batches.append(batch)

        total_batches = len(batches)
        if not batches:
            return []

        total_patterns = sum(len(p) for p in patterns_by_type.values())
        log.info(
            "MAP phase (patterns): %d patterns in %d batches (batch_size=%d, workers=%d)",
            total_patterns,
            total_batches,
            self.batch_size,
            self.max_workers,
        )

        patches_by_batch: dict[int, list[Patch]] = {}
        success_count = 0
        fail_count = 0
        pbar = tqdm(total=total_batches, desc="MAP (patterns)", unit="batch")

        # Run all pattern batches in parallel. Keep debug output focused on batch 1.
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    self._run_single_map_patterns,
                    skill_state,
                    batch,
                    idx + 1,
                    total_batches,
                ): idx + 1
                for idx, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                batch_idx = futures[future]
                try:
                    batch_patches = future.result()
                    if batch_patches:
                        patches_by_batch[batch_idx] = batch_patches
                        success_count += 1
                        if self.verbose and batch_idx == 1:
                            total_edits = sum(len(p.edits) for p in batch_patches)
                            tqdm.write(
                                f"  MAP batch 1: {len(batch_patches)} patch(es), "
                                f"{total_edits} edits total"
                            )
                    else:
                        fail_count += 1
                        if batch_idx == 1:
                            tqdm.write("  MAP batch 1: failed to parse patch")
                        else:
                            tqdm.write(
                                f"  MAP batch {batch_idx}/{total_batches}: parse failed"
                            )
                except Exception as e:
                    fail_count += 1
                    if batch_idx == 1:
                        tqdm.write(f"  MAP batch 1: error: {e}")
                    else:
                        tqdm.write(
                            f"  MAP batch {batch_idx}/{total_batches}: error: {e}"
                        )
                pbar.set_postfix(ok=success_count, fail=fail_count, refresh=False)
                pbar.update(1)

        pbar.close()
        patches: list[Patch] = []
        for idx in sorted(patches_by_batch):
            patches.extend(patches_by_batch[idx])
        log.info(
            "MAP phase complete: %d/%d patches produced", len(patches), total_batches
        )
        return patches

    def _run_single_map_markdown(
        self,
        skill_state: dict[str, str],
        batch: list[dict],
        batch_idx: int,
        total_batches: int,
    ) -> list[SemanticPatch]:
        """Run a single MAP phase call that returns semantic markdown patches."""
        user_msg = self._build_map_user_message(skill_state, batch, batch_idx, total_batches)
        response, conversation_trace = self._call_llm(
            self._map_system_prompt, user_msg, tag="patch", expect_semantic=True
        )
        self._save_prompt_response(
            "map", f"batch_{batch_idx:04d}", self._map_system_prompt, user_msg, response
        )
        patches = self._parse_semantic_patches(
            response,
            phase="map",
            label=f"batch_{batch_idx:04d}",
            conversation_trace=conversation_trace,
            tag="patch",
        )
        for patch in patches:
            patch.batch_index = batch_idx
        return patches

    def _run_single_map_patterns_markdown(
        self,
        skill_state: dict[str, str],
        pattern_batch: dict[str, list[dict]],
        batch_idx: int,
        total_batches: int,
    ) -> list[SemanticPatch]:
        """Run a single pattern MAP phase call that returns semantic markdown patches."""
        user_msg = self._build_map_user_message_from_patterns(
            skill_state, pattern_batch, batch_idx, total_batches
        )
        response, conversation_trace = self._call_llm(
            self._map_system_prompt, user_msg, tag="patch", expect_semantic=True
        )
        self._save_prompt_response(
            "map", f"batch_{batch_idx:04d}", self._map_system_prompt, user_msg, response
        )
        patches = self._parse_semantic_patches(
            response,
            phase="map",
            label=f"batch_{batch_idx:04d}",
            conversation_trace=conversation_trace,
            tag="patch",
        )
        for patch in patches:
            patch.batch_index = batch_idx
        return patches

    def run_map_phase_markdown(
        self,
        skill_state: dict[str, str],
        records: list[dict],
    ) -> list[SemanticPatch]:
        """Run the semantic markdown MAP phase."""
        batches = chunk_list(records, self.batch_size)
        total_batches = len(batches)
        if not batches:
            return []
        log.info(
            "MAP phase (markdown): %d records in %d batches (batch_size=%d, workers=%d)",
            len(records),
            total_batches,
            self.batch_size,
            self.max_workers,
        )
        patches_by_batch: dict[int, list[SemanticPatch]] = {}
        success_count = 0
        fail_count = 0
        pbar = tqdm(total=total_batches, desc="MAP", unit="batch")
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    self._run_single_map_markdown, skill_state, batch, idx + 1, total_batches
                ): idx + 1
                for idx, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                batch_idx = futures[future]
                try:
                    batch_patches = future.result()
                    if batch_patches:
                        patches_by_batch[batch_idx] = batch_patches
                        success_count += 1
                    else:
                        fail_count += 1
                        tqdm.write(f"  MAP batch {batch_idx}/{total_batches}: parse failed")
                except Exception as exc:
                    fail_count += 1
                    tqdm.write(f"  MAP batch {batch_idx}/{total_batches}: error: {exc}")
                pbar.set_postfix(ok=success_count, fail=fail_count, refresh=False)
                pbar.update(1)
        pbar.close()
        patches: list[SemanticPatch] = []
        for idx in sorted(patches_by_batch):
            patches.extend(patches_by_batch[idx])
        log.info("MAP phase complete: %d/%d semantic patches produced", len(patches), total_batches)
        return patches

    def run_map_phase_patterns_markdown(
        self,
        skill_state: dict[str, str],
        patterns_by_type: dict[str, list[dict]],
    ) -> list[SemanticPatch]:
        """Run the semantic markdown MAP phase for pattern inputs."""
        max_patterns = max((len(pats) for pats in patterns_by_type.values()), default=0)
        batches: list[dict[str, list[dict]]] = []
        for i in range(0, max_patterns, self.batch_size):
            batch: dict[str, list[dict]] = {}
            for item_type, pats in patterns_by_type.items():
                batch[item_type] = pats[i : i + self.batch_size]
            if any(batch.values()):
                batches.append(batch)
        total_batches = len(batches)
        if not batches:
            return []
        patches_by_batch: dict[int, list[SemanticPatch]] = {}
        success_count = 0
        fail_count = 0
        pbar = tqdm(total=total_batches, desc="MAP (patterns)", unit="batch")
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    self._run_single_map_patterns_markdown,
                    skill_state,
                    batch,
                    idx + 1,
                    total_batches,
                ): idx + 1
                for idx, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                batch_idx = futures[future]
                try:
                    batch_patches = future.result()
                    if batch_patches:
                        patches_by_batch[batch_idx] = batch_patches
                        success_count += 1
                    else:
                        fail_count += 1
                        tqdm.write(f"  MAP batch {batch_idx}/{total_batches}: parse failed")
                except Exception as exc:
                    fail_count += 1
                    tqdm.write(f"  MAP batch {batch_idx}/{total_batches}: error: {exc}")
                pbar.set_postfix(ok=success_count, fail=fail_count, refresh=False)
                pbar.update(1)
        pbar.close()
        patches: list[SemanticPatch] = []
        for idx in sorted(patches_by_batch):
            patches.extend(patches_by_batch[idx])
        log.info("MAP phase complete: %d/%d semantic patches produced", len(patches), total_batches)
        return patches

    # -- REDUCE phase -------------------------------------------------------

    def _run_single_merge(
        self,
        skill_state: dict[str, str],
        patches: list[Patch],
        level: int,
        merge_idx: int,
    ) -> list[Patch]:
        """Run a single MERGE phase LLM call."""
        user_msg = self._build_merge_user_message(skill_state, patches)
        response, conversation_trace = self._call_llm(
            self._merge_system_prompt,
            user_msg,
            tag="merged_patch",
        )
        self._save_prompt_response(
            f"merge_level_{level}", f"batch_{merge_idx:04d}",
            self._merge_system_prompt, user_msg, response,
        )
        merged_patches = self._parse_patches(
            response,
            tag="merged_patch",
            phase=f"merge_level_{level}",
            label=f"batch_{merge_idx:04d}",
            conversation_trace=conversation_trace,
        )
        if merged_patches and self.verbose:
            total_edits = sum(len(p.edits) for p in merged_patches)
            log.info(
                "MERGE level %d, batch %d: %d input patches -> %d output patch(es), %d edits",
                level,
                merge_idx,
                len(patches),
                len(merged_patches),
                total_edits,
            )
        return merged_patches

    def _run_single_merge_markdown(
        self,
        skill_state: dict[str, str],
        patches: list[SemanticPatch],
        level: int,
        merge_idx: int,
    ) -> list[SemanticPatch]:
        """Run a single semantic markdown MERGE phase call."""
        user_msg = self._build_merge_user_message_semantic(skill_state, patches)
        response, conversation_trace = self._call_llm(
            self._merge_system_prompt,
            user_msg,
            tag="merged_patch",
            expect_semantic=True,
        )
        self._save_prompt_response(
            f"merge_level_{level}", f"batch_{merge_idx:04d}", self._merge_system_prompt, user_msg, response
        )
        merged_patches = self._parse_semantic_patches(
            response,
            phase=f"merge_level_{level}",
            label=f"batch_{merge_idx:04d}",
            conversation_trace=conversation_trace,
            tag="merged_patch",
        )
        for patch in merged_patches:
            patch.batch_index = merge_idx
        return merged_patches

    def run_reduce_phase_markdown(
        self,
        skill_state: dict[str, str],
        patches: list[SemanticPatch],
    ) -> SemanticPatch | None:
        """Run hierarchical semantic markdown REDUCE until one patch remains."""
        if not patches:
            log.warning("REDUCE phase: no patches to merge")
            return None
        if len(patches) == 1:
            log.info("REDUCE phase: only 1 semantic patch, skipping merge")
            return patches[0]
        current = list(patches)
        level = 0
        while len(current) > 1 and level < self.max_merge_levels:
            level += 1
            merge_batches = chunk_list(current, self.merge_batch_size)
            merged_results_by_index: dict[int, list[SemanticPatch]] = {}
            success_count = 0
            fail_count = 0
            pbar = tqdm(
                total=len(merge_batches),
                desc=f"REDUCE L{level} ({len(current)}→{len(merge_batches)})",
                unit="merge",
            )
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(
                        self._run_single_merge_markdown, skill_state, batch, level, idx + 1
                    ): (idx + 1, batch)
                    for idx, batch in enumerate(merge_batches)
                }
                for future in as_completed(futures):
                    merge_idx, batch = futures[future]
                    try:
                        result_patches = future.result()
                        if result_patches:
                            merged_results_by_index[merge_idx] = result_patches
                            success_count += 1
                        else:
                            fail_count += 1
                            tqdm.write(f"  REDUCE level {level}, batch {merge_idx}: merge failed")
                            merged_results_by_index[merge_idx] = list(batch)
                    except Exception as exc:
                        fail_count += 1
                        tqdm.write(f"  REDUCE level {level}, batch {merge_idx}: error: {exc}")
                        merged_results_by_index[merge_idx] = list(batch)
                    pbar.set_postfix(ok=success_count, fail=fail_count, refresh=False)
                    pbar.update(1)
            pbar.close()
            current = []
            for merge_idx in sorted(merged_results_by_index):
                current.extend(merged_results_by_index[merge_idx])
            if self.output_dir:
                level_dir = self.output_dir / f"merge_level_{level}_semantic"
                level_dir.mkdir(parents=True, exist_ok=True)
                for i, patch in enumerate(current, start=1):
                    self._save_semantic_patch(patch, level_dir / f"merged_{i:04d}.md")
        if len(current) > 1:
            merged = self._coalesce_semantic_patches(
                self._run_single_merge_markdown(skill_state, current, level + 1, 1)
            )
            if merged is not None:
                return merged
            return current[0]
        return current[0]

    def run_reduce_phase(
        self,
        skill_state: dict[str, str],
        patches: list[Patch],
    ) -> Patch | None:
        """Run the REDUCE phase: hierarchical merge until 1 patch remains."""
        if not patches:
            log.warning("REDUCE phase: no patches to merge")
            return None

        if len(patches) == 1:
            log.info("REDUCE phase: only 1 patch, skipping merge")
            return patches[0]

        current = list(patches)
        level = 0

        while len(current) > 1 and level < self.max_merge_levels:
            level += 1
            merge_batches = chunk_list(current, self.merge_batch_size)

            n_merge = len(merge_batches)
            pbar = tqdm(
                total=n_merge,
                desc=f"REDUCE L{level} ({len(current)}→{n_merge})",
                unit="merge",
            )
            success_count = 0
            fail_count = 0

            merged_results_by_index: dict[int, list[Patch]] = {}

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(
                        self._run_single_merge,
                        skill_state,
                        batch,
                        level,
                        idx + 1,
                    ): (idx + 1, batch)
                    for idx, batch in enumerate(merge_batches)
                }
                for future in as_completed(futures):
                    merge_idx, batch = futures[future]
                    try:
                        result_patches = future.result()
                        if result_patches:
                            merged_results_by_index[merge_idx] = result_patches
                            success_count += 1
                        else:
                            fail_count += 1
                            tqdm.write(
                                f"  REDUCE level {level}, batch {merge_idx}: "
                                f"merge failed"
                            )
                            merged_results_by_index[merge_idx] = list(batch)
                    except Exception as e:
                        fail_count += 1
                        tqdm.write(
                            f"  REDUCE level {level}, batch {merge_idx}: "
                            f"error: {e}"
                        )
                        merged_results_by_index[merge_idx] = list(batch)
                    pbar.set_postfix(
                        ok=success_count, fail=fail_count, refresh=False
                    )
                    pbar.update(1)

            pbar.close()

            current = []
            for merge_idx in sorted(merged_results_by_index):
                current.extend(merged_results_by_index[merge_idx])
            log.info(
                "REDUCE level %d complete: %d patches remaining", level, len(current)
            )

            # Save intermediate if configured
            if self.output_dir:
                level_dir = self.output_dir / f"merge_level_{level}"
                level_dir.mkdir(parents=True, exist_ok=True)
                for i, p in enumerate(current):
                    self._save_patch(p, level_dir / f"merged_{i + 1:04d}.json")

        if len(current) > 1:
            log.warning(
                "REDUCE phase: %d patches remain after %d levels (max_merge_levels=%d). "
                "Doing one final forced merge.",
                len(current),
                level,
                self.max_merge_levels,
            )
            # Force merge everything remaining
            final = self._coalesce_patches(
                self._run_single_merge(skill_state, current, level + 1, 1)
            )
            if final is not None:
                return final
            # Ultimate fallback: return first patch
            log.warning("REDUCE: forced merge failed, returning first patch")
            return current[0]

        return current[0]

    # -- APPLY phase --------------------------------------------------------

    def run_apply_phase(
        self,
        skill_state: dict[str, str],
        final_patch: Patch,
    ) -> tuple[list[FileEdit], str, list[str]]:
        """Convert the final merged patch to full file content and apply.

        Returns (edits, reasoning, changelog) from the apply LLM call.
        """
        user_msg = self._build_apply_user_message(skill_state, final_patch)
        response, _ = self._call_llm(
            self._apply_system_prompt, user_msg, tag="editting"
        )
        self._save_prompt_response(
            "apply", "apply",
            self._apply_system_prompt, user_msg, response,
        )

        # Reuse SkillEvolver's parse_llm_response for the full-file JSON schema
        edits, reasoning, changelog = self._evolver.parse_llm_response(response)
        return edits, reasoning, changelog

    def _build_translation_user_message(
        self,
        file_path: str,
        file_content: str,
        edits: list[PatchEdit],
    ) -> str:
        """Build user message for a translation call (one file, multiple edits)."""
        parts: list[str] = []

        if file_content:
            n_lines = file_content.count("\n") + 1
            parts.append(
                load_prompt_template("parallel_evolving_agent/current_content_header").format(
                    file_path=file_path,
                    n_lines=n_lines,
                )
            )
            parts.append(f"```markdown\n{file_content}\n```\n")
        else:
            parts.append(
                load_prompt_template("parallel_evolving_agent/missing_file_header").format(
                    file_path=file_path
                )
            )

        edits_data = []
        for edit in edits:
            ed: dict = {"file": edit.file, "op": edit.op}
            if edit.target_section:
                ed["target_section"] = edit.target_section
            if edit.target_text:
                ed["target_text"] = edit.target_text
            if edit.content:
                ed["content"] = edit.content
            if edit.old_text:
                ed["old_text"] = edit.old_text
            if edit.after_section:
                ed["after_section"] = edit.after_section
            edits_data.append(ed)

        parts.append(
            load_prompt_template("parallel_evolving_agent/edits_to_translate_header").format(
                edit_count=len(edits)
            )
        )
        parts.append(f"```json\n{json.dumps(edits_data, indent=2)}\n```")
        parts.append(load_prompt_template("parallel_evolving_agent/translate_edits_instruction"))
        return "\n".join(parts)

    def _build_translation_user_message_from_semantic(
        self,
        file_paths: list[str],
        skill_state: dict[str, str],
        item: SemanticPatchItem,
    ) -> str:
        """Build user message for translating one semantic item into exact edits."""
        parts: list[str] = []
        for file_path in file_paths:
            file_content = skill_state.get(file_path, "")
            if file_content:
                n_lines = file_content.count("\n") + 1
                parts.append(
                    load_prompt_template("parallel_evolving_agent/current_content_header").format(
                        file_path=file_path,
                        n_lines=n_lines,
                    )
                )
                parts.append(f"```markdown\n{file_content}\n```\n")
            else:
                parts.append(
                    load_prompt_template("parallel_evolving_agent/missing_file_header").format(
                        file_path=file_path
                    )
                )
        parts.append(load_prompt_template("parallel_evolving_agent/semantic_edit_instruction_header"))
        parts.append(f"Target File: {item.target_file}")
        parts.append(f"Edit Intent: {item.edit_intent}")
        parts.append(f"Location Hint: {item.location_hint}")
        parts.append("Change Instruction:")
        parts.append(item.change_instruction)
        parts.append(load_prompt_template("parallel_evolving_agent/translate_semantic_instruction"))
        return "\n".join(parts)

    def run_translation_phase(
        self,
        skill_state: dict[str, str],
        final_patch: Patch,
    ) -> Patch:
        """Translate PatchEdit text references to exact matches via parallel LLM calls.

        Each non-file-level edit (anything other than ``create`` / ``delete_file``)
        gets its own LLM call with the target file content so target_section /
        target_text / old_text can be corrected independently. File-level ops
        pass through unchanged.

        Returns a new Patch with corrected edits preserving the original order.
        """
        if not final_patch.edits:
            return final_patch

        # slot -> translated edit (preserves original order)
        translated_slots: dict[int, PatchEdit] = {}
        to_translate: list[tuple[int, PatchEdit]] = []
        for idx, edit in enumerate(final_patch.edits):
            if edit.op in ("create", "delete_file"):
                translated_slots[idx] = edit
            else:
                to_translate.append((idx, edit))

        if not to_translate:
            return final_patch

        log.info(
            "TRANSLATION phase: translating %d edit(s) independently in parallel",
            len(to_translate),
        )

        def _translate_one(index: int, edit: PatchEdit) -> tuple[int, PatchEdit]:
            file_path = edit.file
            file_content = skill_state.get(file_path, "")
            user_msg = self._build_translation_user_message(file_path, file_content, [edit])
            response, conversation_trace = self._call_llm(
                self._translation_system_prompt, user_msg, tag="patch"
            )
            self._save_prompt_response(
                "translation",
                f"{index:04d}_{file_path.replace('/', '_')}",
                self._translation_system_prompt,
                user_msg,
                response,
            )
            translated = self._coalesce_patches(
                self._parse_patches(
                    response,
                    tag="patch",
                    phase="translation",
                    label=f"{index:04d}_{file_path.replace('/', '_')}",
                    conversation_trace=conversation_trace,
                )
            )
            if translated is None:
                log.warning(
                    "Translation failed for edit %d (%s) — using original",
                    index,
                    file_path,
                )
                return index, edit
            # Filter to edits for this file only (guard against hallucinated paths).
            translated_edits = [e for e in translated.edits if e.file == file_path]
            if not translated_edits:
                log.warning(
                    "Translation returned no matching edits for edit %d (%s) — using original",
                    index,
                    file_path,
                )
                return index, edit
            return index, translated_edits[0]

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(_translate_one, idx, edit): (idx, edit.file)
                for idx, edit in to_translate
            }
            for future in as_completed(futures):
                idx, file_path = futures[future]
                try:
                    out_idx, translated_edit = future.result()
                    translated_slots[out_idx] = translated_edit
                    tqdm.write(
                        f"  TRANSLATE edit {out_idx + 1}/{len(final_patch.edits)} "
                        f"({file_path}): corrected"
                    )
                except Exception as exc:
                    log.error("Translation error for edit %d (%s): %s", idx, file_path, exc)
                    translated_slots[idx] = final_patch.edits[idx]

        corrected_edits = [translated_slots[i] for i in range(len(final_patch.edits))]

        return Patch(
            reasoning=final_patch.reasoning,
            edits=corrected_edits,
            changelog_entries=final_patch.changelog_entries,
            batch_index=final_patch.batch_index,
            raw_json=final_patch.raw_json,
        )

    def run_translation_phase_from_semantic(
        self,
        skill_state: dict[str, str],
        final_patch: SemanticPatch,
    ) -> Patch:
        """Translate semantic markdown items into exact PatchEdits."""
        if not final_patch.items:
            return Patch(
                reasoning=final_patch.reasoning,
                edits=[],
                changelog_entries=final_patch.changelog_entries,
                batch_index=final_patch.batch_index,
                raw_json={"semantic_markdown": final_patch.raw_markdown},
            )

        translated_slots: dict[int, list[PatchEdit]] = {}

        def _translate_one(index: int, item: SemanticPatchItem) -> tuple[int, list[PatchEdit]]:
            file_paths = [f.strip() for f in item.target_file.split(",")]
            primary_file = file_paths[0]
            user_msg = self._build_translation_user_message_from_semantic(
                file_paths, skill_state, item
            )
            response, conversation_trace = self._call_llm(
                self._translation_system_prompt, user_msg, tag="patch"
            )
            label = f"{index:04d}_{primary_file.replace('/', '_')}"
            self._save_prompt_response(
                "translation", label, self._translation_system_prompt, user_msg, response
            )
            translated = self._coalesce_patches(
                self._parse_patches(
                    response,
                    tag="patch",
                    phase="translation",
                    label=label,
                    conversation_trace=conversation_trace,
                )
            )
            if translated is None:
                log.warning("Translation failed for semantic item %d (%s)", index, primary_file)
                return index, []
            return index, [edit for edit in translated.edits if edit.file in file_paths]

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(_translate_one, idx, item): (idx, item.target_file)
                for idx, item in enumerate(final_patch.items)
            }
            for future in as_completed(futures):
                idx, file_path = futures[future]
                try:
                    out_idx, edits = future.result()
                    translated_slots[out_idx] = edits
                    tqdm.write(
                        f"  TRANSLATE item {out_idx + 1}/{len(final_patch.items)} ({file_path}): translated"
                    )
                except Exception as exc:
                    log.error("Translation error for semantic item %d (%s): %s", idx, file_path, exc)
                    translated_slots[idx] = []

        translated_edits: list[PatchEdit] = []
        for idx in range(len(final_patch.items)):
            translated_edits.extend(translated_slots.get(idx, []))

        translated_edits = self._drop_shorter_duplicate_exact_edits(translated_edits)
        translated_edits = self._sanitize_translated_edits(skill_state, translated_edits)

        translated_patch = Patch(
            reasoning=final_patch.reasoning,
            edits=translated_edits,
            changelog_entries=final_patch.changelog_entries,
            batch_index=final_patch.batch_index,
            raw_json={"semantic_markdown": final_patch.raw_markdown},
        )
        return translated_patch

    @staticmethod
    def _drop_shorter_duplicate_exact_edits(edits: list[PatchEdit]) -> list[PatchEdit]:
        """Drop shorter duplicate exact edit targets after markdown translation."""
        deduped: list[PatchEdit] = []
        seen: dict[tuple[str, str, str, str, str], int] = {}
        for edit in edits:
            key = (
                edit.file,
                edit.op,
                edit.target_section,
                edit.target_text,
                edit.old_text,
            )
            existing_index = seen.get(key)
            if existing_index is None:
                seen[key] = len(deduped)
                deduped.append(edit)
                continue

            existing = deduped[existing_index]
            existing_size = len(existing.content)
            candidate_size = len(edit.content)
            if candidate_size > existing_size:
                deduped[existing_index] = edit
                log.warning(
                    "Dropping shorter duplicate translated edit for %s (%s in %s); kept later edit",
                    edit.op,
                    edit.target_section or edit.target_text or edit.file,
                    edit.file,
                )
            else:
                log.warning(
                    "Dropping shorter duplicate translated edit for %s (%s in %s); kept earlier edit",
                    edit.op,
                    edit.target_section or edit.target_text or edit.file,
                    edit.file,
                )
        return deduped

    @staticmethod
    def _extract_reference_paths_from_text(text: str) -> list[str]:
        """Return distinct references/*.md paths mentioned in text, preserving order."""
        seen: set[str] = set()
        refs: list[str] = []
        for match in _REFERENCE_PATH_PATTERN.findall(text):
            if match not in seen:
                seen.add(match)
                refs.append(match)
        return refs

    @staticmethod
    def _sanitize_translated_edits(
        skill_state: dict[str, str],
        edits: list[PatchEdit],
    ) -> list[PatchEdit]:
        """Drop translated edits that would create dangling or unsupported references."""
        supported: list[PatchEdit] = []
        for edit in edits:
            edit.op = _PATCH_OP_ALIASES.get(edit.op, edit.op)
            if edit.op not in _SUPPORTED_PATCH_OPS:
                log.warning(
                    "Dropping translated edit for %s in %s due to unsupported op %r",
                    edit.target_section or edit.target_text or edit.file,
                    edit.file,
                    edit.op,
                )
                continue
            supported.append(edit)

        create_targets = {
            edit.file
            for edit in supported
            if edit.op == "create" and edit.file.startswith("references/")
        }

        kept: list[PatchEdit] = []
        skill_links: set[str] = set()
        for edit in supported:
            if edit.file == "SKILL.md" and edit.op != "delete_file":
                refs = ParallelSkillEvolver._extract_reference_paths_from_text(edit.content)
                missing = [
                    ref for ref in refs if ref not in skill_state and ref not in create_targets
                ]
                if missing:
                    log.warning(
                        "Dropping SKILL.md translated edit for %s because it inserts reference path(s) %s without existing files or create ops",
                        edit.target_section or edit.target_text or edit.old_text or "SKILL.md",
                        missing,
                    )
                    continue
                skill_links.update(refs)
            kept.append(edit)

        sanitized: list[PatchEdit] = []
        for edit in kept:
            if (
                edit.op == "create"
                and edit.file.startswith("references/")
                and edit.file not in skill_links
            ):
                log.warning(
                    "Dropping orphaned translated create op for %s because no surviving SKILL.md edit links to it",
                    edit.file,
                )
                continue
            sanitized.append(edit)
        return sanitized

    def run_apply_phase_programmatic(
        self,
        skill_state: dict[str, str],
        final_patch: Patch,
    ) -> tuple[list[FileEdit], str, list[str]]:
        """Apply all PatchEdits directly in code — zero LLM calls.

        Each PatchEdit operation (append_to_section, replace_in_section,
        insert_after, insert_before, add_section, delete_section, create,
        delete_file) is executed programmatically against the frozen
        skill_state. Returns (file_edits, reasoning, changelog) in the
        same format as run_apply_phase, acting as a drop-in replacement.
        """
        if not final_patch.edits:
            return [], "No edits to apply", []

        # Work on a mutable copy of the state
        updated: dict[str, str] = dict(skill_state)
        deleted: set[str] = set()
        changelog: list[str] = []
        applied = skipped = 0

        for edit in final_patch.edits:
            if edit.op == "delete_file":
                if edit.file in updated:
                    del updated[edit.file]
                    deleted.add(edit.file)
                    changelog.append(f"Deleted {edit.file}")
                    applied += 1
                else:
                    log.warning("delete_file: file not found: %s", edit.file)
                    skipped += 1
                continue

            if edit.op == "create":
                updated[edit.file] = edit.content
                changelog.append(f"Created {edit.file}")
                applied += 1
                continue

            current = updated.get(edit.file, "")
            new_content = _apply_patch_edit_to_content(current, edit)
            if new_content == current:
                skipped += 1
            else:
                updated[edit.file] = new_content
                label = edit.target_section or edit.target_text or edit.old_text or ""
                changelog.append(
                    f"{edit.op} in {edit.file}"
                    + (f": {label[:60]}" if label else "")
                )
                applied += 1

        log.info(
            "APPLY (programmatic): %d edit(s) applied, %d skipped",
            applied, skipped,
        )

        # Build FileEdit list for downstream diff/apply
        file_edits: list[FileEdit] = []
        for path, content in updated.items():
            action = "modify" if path in skill_state else "create"
            file_edits.append(FileEdit(relative_path=path, content=content, action=action))
        for path in deleted:
            file_edits.append(FileEdit(relative_path=path, content="", action="delete"))

        reasoning = final_patch.reasoning
        full_changelog = list(final_patch.changelog_entries) + changelog
        return file_edits, reasoning, full_changelog

    def run_verification_phase(
        self,
        max_rounds: int | None = None,
    ) -> list[str]:
        """Validate the skill and iteratively fix errors via patch LLM calls.

        1. Calls validate_skill() -> (valid, msg)
        2. If invalid: sends an LLM call with current skill state + error,
           parses a patch response, applies it programmatically, re-validates,
           repeats up to max_rounds.

        Returns a changelog list describing each fix applied.
        """
        if max_rounds is None:
            max_rounds = self.max_verification_rounds

        fix_changelog: list[str] = []

        for round_num in range(1, max_rounds + 1):
            valid, msg = self._evolver.validate_skill()
            if valid:
                log.info("Verification passed after %d fix round(s).", round_num - 1)
                break

            log.warning(
                "Verification round %d/%d: validation failed — %s",
                round_num, max_rounds, msg,
            )

            current_state = self._evolver.read_skill_state()
            user_msg = self._build_verification_user_message(current_state, msg)
            response, conversation_trace = self._call_llm(
                self._verification_system_prompt, user_msg, tag="patch"
            )
            self._save_prompt_response(
                "verification", f"round_{round_num:02d}",
                self._verification_system_prompt, user_msg, response,
            )

            fix_patch = self._coalesce_patches(
                self._parse_patches(
                    response,
                    tag="patch",
                    phase="verification",
                    label=f"round_{round_num:02d}",
                    conversation_trace=conversation_trace,
                )
            )
            if fix_patch is None or not fix_patch.edits:
                log.warning(
                    "Verification round %d: LLM produced no patch edits, stopping.",
                    round_num,
                )
                fix_changelog.append(
                    f"Verification round {round_num}: no fix produced — {msg}"
                )
                break

            fix_file_edits, fix_reasoning, fix_entries = self.run_apply_phase_programmatic(
                current_state, fix_patch
            )
            changed_fix_edits: list[FileEdit] = []
            for edit in fix_file_edits:
                old_content = current_state.get(edit.relative_path)
                if edit.action == "modify":
                    if old_content != edit.content:
                        changed_fix_edits.append(edit)
                elif edit.action == "create":
                    if old_content is None:
                        changed_fix_edits.append(edit)
                elif edit.action == "delete":
                    if old_content is not None:
                        changed_fix_edits.append(edit)

            if not changed_fix_edits:
                log.warning(
                    "Verification round %d: patch applied no effective changes, stopping.",
                    round_num,
                )
                fix_changelog.append(
                    f"Verification round {round_num}: patch made no changes — {msg}"
                )
                break

            self._evolver.apply_edits(changed_fix_edits)
            fix_changelog.extend(
                fix_entries or [f"Verification fix round {round_num}: {fix_reasoning}"]
            )
            log.info(
                "Verification round %d: applied %d file edit(s) from %d patch op(s).",
                round_num,
                len(changed_fix_edits),
                len(fix_patch.edits),
            )
        else:
            # for-loop exhausted without break (still failing)
            valid, msg = self._evolver.validate_skill()
            if not valid:
                log.warning(
                    "Verification phase exhausted %d rounds, still failing: %s",
                    max_rounds, msg,
                )
                fix_changelog.append(
                    f"Verification FAILED after {max_rounds} rounds: {msg}"
                )

        return fix_changelog

    # -- artifact saving ----------------------------------------------------

    @staticmethod
    def _save_patch(patch: Patch, path: Path) -> None:
        """Save a patch to a JSON file."""
        data = {
            "reasoning": patch.reasoning,
            "edits": [
                {
                    "file": e.file,
                    "op": e.op,
                    "target_section": e.target_section,
                    "target_text": e.target_text,
                    "content": e.content,
                    "old_text": e.old_text,
                    "after_section": e.after_section,
                }
                for e in patch.edits
            ],
            "changelog_entries": patch.changelog_entries,
            "batch_index": patch.batch_index,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _save_translated_patch(self, patch: Patch) -> None:
        """Save the translated patch artifact when intermediate output is enabled."""
        if self.output_dir is None:
            return
        self._save_patch(patch, self.output_dir / "translated_final_patch.json")

    def _save_semantic_patch(self, patch: SemanticPatch, path: Path) -> None:
        """Save a semantic markdown patch to a markdown file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        parts = [
            "===== PATCH START =====",
            "Reasoning:",
            patch.reasoning,
            "",
            "Changelog:",
        ]
        parts.extend(f"- {entry}" for entry in patch.changelog_entries)
        parts.extend(["", "Items:", ""])
        for idx, item in enumerate(patch.items, start=1):
            parts.extend(
                [
                    self._render_semantic_item_marker(idx),
                    f"Target File: {item.target_file}",
                    f"Edit Intent: {item.edit_intent}",
                    f"Location Hint: {item.location_hint}",
                    "Change Instruction:",
                    item.change_instruction,
                    "",
                ]
            )
        parts.append("===== PATCH END =====")
        body = "\n".join(parts)
        path.write_text(body, encoding="utf-8")

    # -- main orchestrator --------------------------------------------------

    def run(
        self,
        records: list[dict],
        input_mode: str = "records",
    ) -> dict:
        """Main entry point: MAP -> REDUCE -> APPLY -> VALIDATE.

        Args:
            records: Either a list of error records (input_mode="records")
                or a dict of patterns (input_mode="patterns").
            input_mode: "records" or "patterns".

        Returns:
            A summary dict with keys: patches, final_patch, edits, diffs,
            reasoning, changelog, cumulative_patch, total_llm_calls.
        """
        # 1. Freeze original skill state
        original_state = self.read_skill_state()
        snapshot = self._evolver._snapshot_skill()
        log.info("Frozen original skill state (%d files)", len(original_state))

        # 2. MAP phase
        if self.patch_pipeline == "markdown":
            if input_mode == "patterns":
                patches = self.run_map_phase_patterns_markdown(original_state, records)
            else:
                patches = self.run_map_phase_markdown(original_state, records)
        else:
            if input_mode == "patterns":
                patches = self.run_map_phase_patterns(original_state, records)
            else:
                patches = self.run_map_phase(original_state, records)

        if not patches:
            log.warning("No patches produced in MAP phase. Nothing to do.")
            return {
                "patches": [],
                "final_patch": None,
                "edits": [],
                "diffs": [],
                "reasoning": "No patches produced",
                "changelog": [],
                "cumulative_patch": "",
                "total_llm_calls": 0,
            }

        # Save MAP artifacts
        if self.output_dir:
            if self.patch_pipeline == "markdown":
                map_dir = self.output_dir / "map_semantic"
                map_dir.mkdir(parents=True, exist_ok=True)
                for i, p in enumerate(patches):
                    self._save_semantic_patch(p, map_dir / f"patch_{i + 1:04d}.md")
            else:
                map_dir = self.output_dir / "map_patches"
                map_dir.mkdir(parents=True, exist_ok=True)
                for i, p in enumerate(patches):
                    self._save_patch(p, map_dir / f"patch_{i + 1:04d}.json")
            log.info("Saved %d map patches to %s", len(patches), map_dir)

        # Sanitize MAP patches: enforce that create ops and their paired
        # SKILL.md link edits are always kept or dropped together.
        # The markdown pipeline uses semantic patches with no explicit create
        # ops, so this check only applies to the JSON patch pipeline.
        if self.patch_pipeline != "markdown":
            patches = self._enforce_create_pairing(patches, original_state)

        # 3. REDUCE phase
        if self.patch_pipeline == "markdown":
            final_semantic_patch = self.run_reduce_phase_markdown(original_state, patches)
            final_patch = None
        else:
            final_semantic_patch = None
            final_patch = self.run_reduce_phase(original_state, patches)
        if final_patch is None and final_semantic_patch is None:
            log.warning("REDUCE phase produced no result.")
            return {
                "patches": patches,
                "final_patch": None,
                "edits": [],
                "diffs": [],
                "reasoning": "Reduce phase failed",
                "changelog": [],
                "cumulative_patch": "",
                "total_llm_calls": len(patches),
            }

        # Save final patch
        if self.output_dir:
            if self.patch_pipeline == "markdown":
                self._save_semantic_patch(
                    final_semantic_patch, self.output_dir / "final_semantic_patch.md"
                )
                log.info(
                    "Saved final merged semantic patch to %s",
                    self.output_dir / "final_semantic_patch.md",
                )
            else:
                self._save_patch(final_patch, self.output_dir / "final_patch.json")
                log.info("Saved final merged patch to %s", self.output_dir / "final_patch.json")

        # 4. TRANSLATION phase — optional correction of inexact text references
        translation_executed = False
        if self.skip_translation and self.patch_pipeline == "markdown":
            log.info(
                "TRANSLATION phase: --skip-translation is ignored for markdown pipeline; "
                "semantic patches still need translation into exact edits"
            )
            translated_patch = self.run_translation_phase_from_semantic(
                original_state, final_semantic_patch
            )
            translation_executed = True
        elif self.skip_translation:
            log.info("TRANSLATION phase: skipped (--skip-translation)")
            translated_patch = (
                self.run_translation_phase_from_semantic(original_state, final_semantic_patch)
                if self.patch_pipeline == "markdown"
                else final_patch
            )
            translation_executed = self.patch_pipeline == "markdown"
        else:
            if self.patch_pipeline == "markdown":
                log.info(
                    "TRANSLATION phase: converting %d semantic item(s) into exact edits...",
                    len(final_semantic_patch.items),
                )
                translated_patch = self.run_translation_phase_from_semantic(
                    original_state, final_semantic_patch
                )
                translation_executed = True
            else:
                log.info(
                    "TRANSLATION phase: correcting text references for %d edit(s)...",
                    len(final_patch.edits),
                )
                translated_patch = self.run_translation_phase(original_state, final_patch)
                translation_executed = True

        translated_patch = Patch(
            reasoning=translated_patch.reasoning,
            edits=self._sanitize_translated_edits(original_state, translated_patch.edits),
            changelog_entries=translated_patch.changelog_entries,
            batch_index=translated_patch.batch_index,
            raw_json=translated_patch.raw_json,
        )
        if translation_executed:
            self._save_translated_patch(translated_patch)

        # 5. APPLY phase (programmatic — no LLM calls)
        log.info(
            "APPLY phase: applying %d edit(s) programmatically...",
            len(translated_patch.edits),
        )
        edits, reasoning, changelog = self.run_apply_phase_programmatic(
            original_state, translated_patch
        )

        if self.verbose:
            log.info("APPLY reasoning: %s", reasoning)
            log.info("APPLY edits: %d, changelog: %d entries", len(edits), len(changelog))

        # Compute diffs against original
        diffs = self._evolver.compute_diffs(original_state, edits)

        # Write edits to disk
        if edits:
            self._evolver.apply_edits(edits)

        # 5. VERIFICATION phase — multi-turn LLM fix loop
        if not self.dry_run and edits:
            log.info("VERIFICATION phase: validating and fixing...")
            verify_changelog = self.run_verification_phase(
                max_rounds=self.max_verification_rounds
            )
            changelog.extend(verify_changelog)

        # Compute cumulative patch text
        all_diff_text: list[str] = []
        for d in diffs:
            if d.unified_diff:
                all_diff_text.append(
                    f"# {d.relative_path} ({d.action})\n{d.unified_diff}"
                )
        cumulative_patch = "\n\n".join(all_diff_text)

        # Save applied diffs
        if self.output_dir and cumulative_patch:
            diff_path = self.output_dir / "applied_diffs.patch"
            diff_path.write_text(cumulative_patch, encoding="utf-8")
            log.info("Saved applied diffs to %s", diff_path)

        # Compute LLM call count
        n_map = len(patches)
        # Rough estimate of merge calls from reduce phase
        n_merge = 0
        n_remaining = len(patches)
        while n_remaining > 1:
            n_batches = (n_remaining + self.merge_batch_size - 1) // self.merge_batch_size
            n_merge += n_batches
            n_remaining = n_batches
        # Translation: one call per non-file-level edit unless skipped
        if self.skip_translation and self.patch_pipeline != "markdown":
            n_translate = 0
        elif self.patch_pipeline == "markdown":
            n_translate = len(final_semantic_patch.items)
        else:
            n_translate = sum(
                1 for e in final_patch.edits if e.op not in ("create", "delete_file")
            )
        # APPLY is programmatic (0 calls); verification is variable
        n_verify = self.max_verification_rounds  # conservative upper bound
        total_llm_calls = n_map + n_merge + n_translate + n_verify

        return {
            "patches": patches,
            "final_patch": translated_patch if self.patch_pipeline == "markdown" else final_patch,
            "edits": edits,
            "diffs": diffs,
            "reasoning": reasoning,
            "changelog": changelog,
            "cumulative_patch": cumulative_patch,
            "total_llm_calls": total_llm_calls,
        }
