"""
Skill Evolving Agent — iteratively improves a skill from error analysis data.

Reads structured error-analysis JSON (from parse_error_analysis_outputs.py),
calls an LLM with the current skill content + error items, parses structured
edits from the response, and applies them to disk.

After each step, unified diffs are computed so changes are clearly visible.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path

from src.react_agent.models import Message, ModelSettings, OpenAIClient
from skill_evolver.prompt_loader import load_prompt_template


def _strip_think(text: str) -> str:
    """Strip <think>...</think> prefix from an LLM response for conversation history."""
    if "</think>" in text:
        return text.rsplit("</think>", 1)[-1].lstrip("\n")
    return text

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

PROTECTED_FILES = {"LICENSE.txt", "recalc.py"}
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

QUICK_VALIDATE_SCRIPT = Path("skills/skill-creator/scripts/quick_validate.py")


@dataclass
class FileEdit:
    relative_path: str  # e.g. "SKILL.md" or "references/formulas.md"
    content: str  # Full file content
    action: str  # "modify" | "create" | "delete"


@dataclass
class PatchEdit:
    file: str
    op: str
    target_section: str = ""
    target_text: str = ""
    content: str = ""
    old_text: str = ""
    after_section: str = ""


@dataclass
class FileDiff:
    relative_path: str
    action: str  # "modify" | "create" | "delete"
    unified_diff: str  # unified diff text (empty for no-change)


@dataclass
class EvolutionStep:
    batch_index: int
    instance_ids: list[str]
    edits: list[FileEdit]
    diffs: list[FileDiff]
    reasoning: str
    changelog_entries: list[str]


@dataclass
class EvolutionResult:
    steps: list[EvolutionStep]
    total_records_processed: int
    total_llm_calls: int
    final_skill_md_lines: int
    files_created: list[str]
    files_modified: list[str]
    cumulative_patch: str  # all diffs concatenated


# ---------------------------------------------------------------------------
# Diff helpers
# ---------------------------------------------------------------------------


def compute_unified_diff(
    old_content: str | None,
    new_content: str | None,
    path: str,
    action: str,
) -> str:
    """Compute a unified diff between old and new file content."""
    old_lines = (old_content or "").splitlines(keepends=True)
    new_lines = (new_content or "").splitlines(keepends=True)

    if action == "create":
        from_label = "/dev/null"
    else:
        from_label = f"a/{path}"

    if action == "delete":
        to_label = "/dev/null"
    else:
        to_label = f"b/{path}"

    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=from_label,
        tofile=to_label,
        lineterm="",
    ))
    # unified_diff may produce lines without trailing newlines; join with \n
    return "\n".join(line.rstrip("\n") for line in diff_lines)


def _find_section_bounds(
    lines: list[str], section_header: str
) -> tuple[int, int] | None:
    """Find the inclusive markdown section start and exclusive end."""
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


def _apply_patch_edit_to_content(content: str, edit: PatchEdit) -> str:
    """Apply one patch edit to a file body."""
    lines = content.split("\n")

    if edit.op == "append_to_section":
        bounds = _find_section_bounds(lines, edit.target_section)
        if bounds is None:
            log.warning("append_to_section: section not found: %r", edit.target_section)
            return content
        _, end = bounds
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
        while start > 0 and not lines[start - 1].strip():
            start -= 1
        lines = lines[:start] + lines[end:]
        return "\n".join(lines)

    log.warning("Unknown PatchEdit op: %r — edit not applied", edit.op)
    return content


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_BASE = load_prompt_template("skill_evolving_agent/system_prompt_base")
MODIFICATION_STRATEGIES_SECTION = load_prompt_template(
    "skill_evolving_agent/modification_strategies_section"
)
MAP_OUTPUT_FORMAT = load_prompt_template("parallel_evolving_agent/map_output_format")
SEQUENTIAL_MAP_OUTPUT_FORMAT = MAP_OUTPUT_FORMAT.replace(
    "what failures you see and what changes address them",
    "what failure patterns you see and what skill folder changes address them",
)
JSON_FORMAT_FIX_PROMPT = load_prompt_template(
    "parallel_evolving_agent/json_format_fix_prompt"
)

# ---------------------------------------------------------------------------
# Error record sections — injected into the base system prompt
# ---------------------------------------------------------------------------

ERROR_RECORD_SECTION_SKILL = load_prompt_template(
    "skill_evolving_agent/error_record_section_skill"
)

ERROR_RECORD_SECTION_GENERIC = load_prompt_template(
    "skill_evolving_agent/error_record_section_generic"
)

_PATTERNS_BASE_HEADER = """\
## Understanding Failure Patterns

You will receive **failure patterns**. Each pattern groups many similar
failures observed across multiple tasks into a single summary.

There are two types of patterns:

### Failure Cause Patterns

Each pattern groups similar **root causes** — what went wrong across many
tasks. Fields:

| Field | Description |
|-------|-------------|
| **Title** | Short name of the failure cluster (e.g. "Incorrect Formula Logic") |
| **Description** | One-sentence explanation of the pattern |"""

_PATTERNS_SKILL_IMPROVEMENT_ROW = """\
| **Skill improvement (suggestion)** | A suggested skill change — treat as a starting point, not a directive |"""

_PATTERNS_BASE_FOOTER = """\
| **Specific errors** | Bulleted list of concrete, distinct mistakes agents made that fall into this pattern |

The **Specific errors** list is the most valuable field — it tells you
exactly what agents got wrong. Use these to craft precise, targeted skill
folder additions (warnings, code examples, checklists).

### Failure Memory Patterns

Each pattern groups similar **lessons learned** — what agents should have
known or done differently. Fields are the same as Failure Cause Patterns.

The **Specific errors** list here describes what agents failed to do or
know. Distill these into actionable instructions or checklists in the skill
folder.

### How to Use Patterns

1. **Read the specific errors**: These are concrete mistakes — each one
   suggests a specific warning, example, or checklist item to add
2. **Cross-reference cause and memory**: When a Failure Cause Pattern and
   a Failure Memory Pattern describe the same gap (e.g. "Wrong Row Indexing"
   cause + "Verify Row Offsets" memory), that's strong evidence for a
   targeted skill change"""

_PATTERNS_SKILL_SUGGESTION_USAGE = """\
3. **Treat suggestions critically**: The "Skill improvement" field, when
   present, is a suggestion for your reference. You decide the best
   strategy — the suggestion may be too vague, too specific, or point to
   the wrong location in the skill folder
4. **Don't copy verbatim**: Distill patterns into concise skill instructions.
   A pattern covering 15 specific errors should become 2-3 lines of guidance,
   not 15 lines"""

_PATTERNS_GENERIC_DISTILL = """\
3. **Don't copy verbatim**: Distill patterns into concise skill instructions.
   A pattern covering 15 specific errors should become 2-3 lines of guidance,
   not 15 lines"""

_PATTERNS_IGNORE_BASE = """\

### What to Ignore

- Patterns about general reasoning ability (not fixable by skill changes)
- Patterns about file paths, environment issues, or infrastructure problems"""

_PATTERNS_IGNORE_SKILL_SUGGESTION = """\
- Skill improvement suggestions that you disagree with after reading the
  specific errors"""

ERROR_RECORD_SECTION_PATTERNS = load_prompt_template(
    "skill_evolving_agent/error_record_section_patterns"
)

ERROR_RECORD_SECTION_PATTERNS_GENERIC = load_prompt_template(
    "skill_evolving_agent/error_record_section_patterns_generic"
)

# Map variant names to their error record sections
PROMPT_VARIANTS = {
    "skill": ERROR_RECORD_SECTION_SKILL,
    "generic": ERROR_RECORD_SECTION_GENERIC,
    "patterns": ERROR_RECORD_SECTION_PATTERNS,
    "patterns_generic": ERROR_RECORD_SECTION_PATTERNS_GENERIC,
}


def build_system_prompt(variant: str = "skill") -> str:
    """Build the full system prompt for the given variant."""
    section = PROMPT_VARIANTS.get(variant)
    if section is None:
        raise ValueError(
            f"Unknown prompt variant {variant!r}. "
            f"Choose from: {', '.join(PROMPT_VARIANTS)}"
        )
    base = SYSTEM_PROMPT_BASE.format(
        modification_strategies_section=MODIFICATION_STRATEGIES_SECTION,
        error_record_section=section,
    )
    output_format_marker = "## Output Format"
    idx = base.find(output_format_marker)
    if idx == -1:
        return base + "\n\n" + SEQUENTIAL_MAP_OUTPUT_FORMAT
    return base[:idx] + SEQUENTIAL_MAP_OUTPUT_FORMAT


# ---------------------------------------------------------------------------
# SkillEvolver
# ---------------------------------------------------------------------------


class SkillEvolver:
    """Iteratively evolves a skill directory based on error analysis records."""

    def __init__(
        self,
        client: OpenAIClient,
        skill_dir: str | Path,
        batch_size: int = 1,
        max_skill_lines: int = 500,
        max_references: int = 5,
        verbose: bool = True,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        tokenizer=None,
        parse_failure_dir: str | Path = "parse_failures",
        dry_run: bool = False,
        prompt_variant: str = "skill",
        enable_llm_cleanup: bool = True,
    ):
        self.client = client
        self.skill_dir = Path(skill_dir)
        self.batch_size = batch_size
        self.max_skill_lines = max_skill_lines
        self.max_references = max_references
        self.verbose = verbose
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.tokenizer = tokenizer
        self.dry_run = dry_run
        self.enable_llm_cleanup = enable_llm_cleanup
        self.system_prompt = build_system_prompt(prompt_variant)

        self._files_created: set[str] = set()
        self._files_modified: set[str] = set()
        self._progress = None
        self._parse_failure_dir = Path(parse_failure_dir)
        self._parse_failure_dir.mkdir(parents=True, exist_ok=True)

    def _save_parse_failure(self, response: str, reason: str) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self._parse_failure_dir / f"llm_parse_failure_{ts}.txt"
        header = f"Reason: {reason}\n"
        path.write_text(header + response, encoding="utf-8")
        log.warning("Saved raw LLM response to %s", path)

    # -- reading state ------------------------------------------------------

    def read_skill_state(self) -> dict[str, str]:
        """Read SKILL.md + any references/ files into {path: content} dict."""
        state: dict[str, str] = {}

        skill_md = self.skill_dir / "SKILL.md"
        if skill_md.exists():
            state["SKILL.md"] = skill_md.read_text(encoding="utf-8")

        refs_dir = self.skill_dir / "references"
        if refs_dir.is_dir():
            for ref_file in sorted(refs_dir.iterdir()):
                if ref_file.is_file():
                    rel = f"references/{ref_file.name}"
                    state[rel] = ref_file.read_text(encoding="utf-8")

        return state

    # -- message building ---------------------------------------------------

    def build_user_message(
        self,
        skill_state: dict[str, str],
        error_records: list[dict],
        batch_idx: int,
        total_batches: int,
    ) -> str:
        """Format current skill folder contents + error batch into user message."""
        parts: list[str] = []

        # Current skill folder contents
        parts.append(load_prompt_template("skill_evolving_agent/current_skill_folder_header"))
        for path, content in sorted(skill_state.items()):
            n_lines = content.count("\n") + 1
            parts.append(f"### {path} ({n_lines} lines)")
            parts.append(f"```markdown\n{content}\n```\n")

        # Error records
        parts.append(
            load_prompt_template("skill_evolving_agent/error_analysis_records_header").format(
                batch_idx=batch_idx,
                total_batches=total_batches,
            )
        )
        for record in error_records:
            parts.append(f"### Record: instance {record['instance_id']}")
            for item in record.get("items", []):
                item_type = item.get("type", "unknown")
                if item_type == "failure_cause":
                    type_label = "Failure Cause"
                elif item_type == "failure_memory":
                    type_label = "Failure Memory"
                else:
                    type_label = item_type.replace("_", " ").title()
                title = item.get("title", "Untitled")
                content = item.get("content", "")
                description = item.get("description", "")
                parts.append(f"**{type_label}: {title}**")
                if description:
                    parts.append(f"*{description}*")
                if content:
                    parts.append(content)
                # Include optional skill-specific fields only when present
                rel = item.get("relation_to_skill", "")
                if rel:
                    parts.append(f"*Relation To Skill (suggestion)*: {rel}")
                refl = item.get("skill_reflection", "")
                if refl:
                    parts.append(f"*Skill Reflection (suggestion)*: {refl}")
                parts.append("")  # blank line between items

        # Skill folder size status
        skill_md_content = skill_state.get("SKILL.md", "")
        skill_lines = skill_md_content.count("\n") + 1
        ref_count = sum(1 for p in skill_state if p.startswith("references/"))
        parts.append(load_prompt_template("skill_evolving_agent/skill_folder_size_status_header"))
        parts.append(
            load_prompt_template("skill_evolving_agent/skill_md_status_line").format(
                skill_lines=skill_lines,
                max_skill_lines=self.max_skill_lines,
            )
        )
        parts.append(
            load_prompt_template("skill_evolving_agent/reference_files_status_line").format(
                ref_count=ref_count,
                max_references=self.max_references,
            )
        )

        if skill_lines > self.max_skill_lines - 50:
            parts.append(load_prompt_template("skill_evolving_agent/size_warning"))

        return "\n".join(parts)

    # -- pattern-based message building -------------------------------------

    def build_user_message_from_patterns(
        self,
        skill_state: dict[str, str],
        patterns: dict[str, list[dict]],
        batch_idx: int,
        total_batches: int,
    ) -> str:
        """Format current skill folder + compressed failure patterns into user message.

        ``patterns`` is a dict with keys like ``"failure_cause"`` and
        ``"failure_memory"``, each mapping to a list of pattern dicts
        produced by ``compress_skill_errors.py --parsed_output``.
        """
        parts: list[str] = []

        # Current skill folder contents
        parts.append(load_prompt_template("skill_evolving_agent/current_skill_folder_header"))
        for path, content in sorted(skill_state.items()):
            n_lines = content.count("\n") + 1
            parts.append(f"### {path} ({n_lines} lines)")
            parts.append(f"```markdown\n{content}\n```\n")

        # Compressed patterns
        parts.append(
            load_prompt_template("skill_evolving_agent/compressed_failure_patterns_header").format(
                batch_idx=batch_idx,
                total_batches=total_batches,
            )
        )
        parts.append(load_prompt_template("skill_evolving_agent/compressed_failure_patterns_intro"))

        for item_type, type_patterns in patterns.items():
            if not type_patterns:
                continue
            if item_type == "failure_cause":
                type_label = "Failure Cause Patterns"
            elif item_type == "failure_memory":
                type_label = "Failure Memory Patterns"
            else:
                type_label = item_type.replace("_", " ").title() + " Patterns"

            parts.append(f"### {type_label}\n")
            for pat in type_patterns:
                parts.append(f"**Pattern {pat.get('index', '?')}: {pat.get('title', 'Untitled')}**")
                desc = pat.get("description", "")
                if desc:
                    parts.append(f"*{desc}*")
                # Skill improvement suggestion (from skill-focused compression)
                improvement = pat.get("skill_improvement", "")
                if improvement:
                    parts.append(f"*Skill improvement (suggestion)*: {improvement}")

                # Covered specific errors — the core detail
                errors = pat.get("covered_specific_errors", "")
                if errors:
                    parts.append(f"Specific errors:\n{errors}")

                parts.append("")  # blank line between patterns

        # Skill folder size status
        skill_md_content = skill_state.get("SKILL.md", "")
        skill_lines = skill_md_content.count("\n") + 1
        ref_count = sum(1 for p in skill_state if p.startswith("references/"))
        parts.append(load_prompt_template("skill_evolving_agent/skill_folder_size_status_header"))
        parts.append(
            load_prompt_template("skill_evolving_agent/skill_md_status_line").format(
                skill_lines=skill_lines,
                max_skill_lines=self.max_skill_lines,
            )
        )
        parts.append(
            load_prompt_template("skill_evolving_agent/reference_files_status_line").format(
                ref_count=ref_count,
                max_references=self.max_references,
            )
        )

        if skill_lines > self.max_skill_lines - 50:
            parts.append(load_prompt_template("skill_evolving_agent/size_warning"))

        return "\n".join(parts)

    def build_consolidation_message(
        self,
        skill_state: dict[str, str],
        cumulative_changelog: list[str],
    ) -> str:
        """Build user message for the final consolidation pass."""
        parts: list[str] = []

        parts.append(load_prompt_template("skill_evolving_agent/final_consolidation_header"))
        parts.append(
            load_prompt_template("skill_evolving_agent/final_consolidation_checklist").format(
                max_skill_lines=self.max_skill_lines,
            )
        )

        if cumulative_changelog:
            parts.append(load_prompt_template("skill_evolving_agent/changes_made_so_far_header"))
            for entry in cumulative_changelog:
                parts.append(f"- {entry}")
            parts.append("")

        parts.append(load_prompt_template("skill_evolving_agent/current_skill_folder_header"))
        for path, content in sorted(skill_state.items()):
            n_lines = content.count("\n") + 1
            parts.append(f"### {path} ({n_lines} lines)")
            parts.append(f"```markdown\n{content}\n```\n")

        skill_md_content = skill_state.get("SKILL.md", "")
        skill_lines = skill_md_content.count("\n") + 1
        ref_count = sum(1 for p in skill_state if p.startswith("references/"))
        parts.append(load_prompt_template("skill_evolving_agent/skill_folder_size_status_header"))
        parts.append(
            load_prompt_template("skill_evolving_agent/skill_md_status_line").format(
                skill_lines=skill_lines,
                max_skill_lines=self.max_skill_lines,
            )
        )
        parts.append(
            load_prompt_template("skill_evolving_agent/reference_files_status_line").format(
                ref_count=ref_count,
                max_references=self.max_references,
            )
        )

        return "\n".join(parts)

    # -- LLM response parsing -----------------------------------------------

    @staticmethod
    def _extract_fenced_block(response: str, language: str) -> str | None:
        """Extract from the first opening fence to the last closing fence."""
        start_pattern = rf"```{re.escape(language)}[^\n]*\n"
        start_match = re.search(start_pattern, response)
        if not start_match:
            return None

        tail = response[start_match.end():]
        closing_matches = list(re.finditer(r"(?m)^```[ \t]*\r?$", tail))
        if closing_matches:
            return tail[:closing_matches[-1].start()].strip()
        return tail.strip()

    @staticmethod
    def _has_closed_fenced_block(response: str, language: str) -> bool:
        """Return True when a complete fenced code block is present."""
        start_pattern = rf"```{re.escape(language)}[^\n]*\n"
        start_match = re.search(start_pattern, response)
        if not start_match:
            return False
        tail = response[start_match.end():]
        return bool(re.search(r"(?m)^```[ \t]*\r?$", tail))

    @staticmethod
    def _json_format_example() -> str:
        """Return a minimal one-shot example for fenced JSON responses."""
        return (
            "```json\n"
            '{"reasoning":"Brief summary","edits":[],"changelog_entries":[]}\n'
            "```"
        )

    @classmethod
    def _build_json_retry_prompt(
        cls,
        *,
        feedback: str,
        issue: str,
        require_complete: bool = False,
    ) -> str:
        """Build a JSON formatting retry prompt with a one-shot example."""
        del issue, require_complete
        return JSON_FORMAT_FIX_PROMPT.format(
            feedback=feedback,
            example=cls._json_format_example(),
        )

    @classmethod
    def _extract_json_text(cls, response: str) -> str | None:
        """Extract raw JSON string from a fenced json block or bare JSON."""
        fenced = cls._extract_fenced_block(response, "json")
        if fenced is not None:
            return fenced

        lines = response.splitlines()
        # Trim leading/trailing empty lines
        start = 0
        end = len(lines) - 1
        while start <= end and not lines[start].strip():
            start += 1
        while end >= start and not lines[end].strip():
            end -= 1
        if start > end:
            return None

        first = lines[start].strip()
        last = lines[end].strip()
        if first.startswith("```json"):
            if last == "```" and end > start:
                return "\n".join(lines[start + 1:end]).strip()
            # Unclosed fence (truncated response)
            return "\n".join(lines[start + 1:]).strip()

        # Try the whole response as raw JSON
        stripped = response.strip()
        if stripped.startswith("{"):
            return stripped
        return None

    @staticmethod
    def _validate_patch_payload(data: object) -> list[str]:
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
    def _extract_json_payloads_with_feedback(
        cls, response: str
    ) -> tuple[list[dict], str]:
        outer_block = cls._extract_json_text(response)
        if outer_block is None:
            return [], "No fenced json block found."
        try:
            parsed = json.loads(outer_block)
        except json.JSONDecodeError as exc:
            return [], (
                f"JSON decode error: {exc.msg} "
                f"(line {exc.lineno}, column {exc.colno}, char {exc.pos})"
            )
        issues = cls._validate_patch_payload(parsed)
        if issues:
            return [], "; ".join(issues)
        return [parsed], ""

    @classmethod
    def _extract_json_payloads(cls, response: str) -> list[dict]:
        payloads, _ = cls._extract_json_payloads_with_feedback(response)
        return payloads

    @staticmethod
    def _patch_from_data(data: dict) -> tuple[list[PatchEdit], str, list[str]]:
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
        return edits, data.get("reasoning", ""), changelog

    def parse_llm_response(
        self, response: str
    ) -> tuple[list[PatchEdit], str, list[str]]:
        """Parse patch-style JSON and sanitize unsupported or dangling edits."""
        payloads, feedback = self._extract_json_payloads_with_feedback(response)
        if not payloads:
            log.warning("Could not parse JSON from LLM response")
            self._save_parse_failure(response, f"json_parse_failed: {feedback}")
            return [], "No parseable JSON in response", []
        edits, reasoning, changelog = self._patch_from_data(payloads[0])
        edits = self._sanitize_patch_edits(self.read_skill_state(), edits)
        return edits, reasoning, changelog

    # -- diff computation ---------------------------------------------------

    def compute_diffs(
        self,
        pre_state: dict[str, str],
        edits: list[FileEdit],
    ) -> list[FileDiff]:
        """Compute unified diffs for a list of edits against pre-edit state."""
        diffs: list[FileDiff] = []
        for edit in edits:
            old = pre_state.get(edit.relative_path)
            new = None if edit.action == "delete" else edit.content
            diff_text = compute_unified_diff(old, new, edit.relative_path, edit.action)
            diffs.append(FileDiff(
                relative_path=edit.relative_path,
                action=edit.action,
                unified_diff=diff_text,
            ))
        return diffs

    # -- applying edits ------------------------------------------------------

    def validate_skill(self) -> tuple[bool, str]:
        """Run quick_validate.py on the skill directory."""
        script = QUICK_VALIDATE_SCRIPT
        if not script.exists():
            log.warning("quick_validate.py not found at %s, skipping validation", script)
            return True, "Validation skipped (script not found)"

        try:
            result = subprocess.run(
                [sys.executable, str(script), str(self.skill_dir)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            msg = result.stdout.strip() or result.stderr.strip()
            return result.returncode == 0, msg
        except subprocess.TimeoutExpired:
            return False, "Validation timed out"
        except Exception as e:
            return False, f"Validation error: {e}"

    def apply_edits(self, edits: list[FileEdit]) -> None:
        """Write edits to disk. Validate paths, reject protected files."""
        if self.dry_run:
            for edit in edits:
                log.info("[DRY RUN] %s %s", edit.action, edit.relative_path)
            return

        for edit in edits:
            target = self.skill_dir / edit.relative_path

            if edit.action == "delete":
                if target.exists():
                    target.unlink()
                    log.info("Deleted %s", edit.relative_path)
                continue

            # create or modify
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edit.content, encoding="utf-8")

            if edit.action == "create":
                self._files_created.add(edit.relative_path)
            else:
                self._files_modified.add(edit.relative_path)

            log.info("Wrote %s (%s)", edit.relative_path, edit.action)

    def _snapshot_skill(self) -> dict[str, str]:
        """Capture current skill state for potential rollback."""
        return self.read_skill_state()

    def _restore_snapshot(self, snapshot: dict[str, str]) -> None:
        """Restore skill directory to a previous snapshot."""
        # Remove references/ files that weren't in the snapshot
        refs_dir = self.skill_dir / "references"
        if refs_dir.is_dir():
            for ref_file in refs_dir.iterdir():
                rel = f"references/{ref_file.name}"
                if rel not in snapshot:
                    ref_file.unlink()

        # Write snapshot files
        for rel_path, content in snapshot.items():
            target = self.skill_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    # -- main loop -----------------------------------------------------------

    def _response_has_complete_json(self, response: str) -> bool:
        """Check whether the response contains a fully parseable JSON block."""
        return bool(self._extract_json_payloads(response))

    @staticmethod
    def _is_complete_payload(data: dict) -> bool:
        """Validate required top-level fields and basic patch shape."""
        return not SkillEvolver._validate_patch_payload(data)

    @staticmethod
    def _extract_reference_paths_from_text(text: str) -> list[str]:
        seen: set[str] = set()
        refs: list[str] = []
        for match in _REFERENCE_PATH_PATTERN.findall(text):
            if match not in seen:
                seen.add(match)
                refs.append(match)
        return refs

    def _sanitize_patch_edits(
        self,
        skill_state: dict[str, str],
        edits: list[PatchEdit],
    ) -> list[PatchEdit]:
        supported: list[PatchEdit] = []
        for edit in edits:
            edit.op = _PATCH_OP_ALIASES.get(edit.op, edit.op)
            if not edit.file:
                log.warning("Dropping patch edit with empty file path")
                continue
            if edit.file in PROTECTED_FILES:
                log.warning("Dropping patch edit for protected file: %s", edit.file)
                continue
            if ".." in edit.file or os.path.isabs(edit.file):
                log.warning("Dropping patch edit with unsafe path: %s", edit.file)
                continue
            if edit.file != "SKILL.md" and not edit.file.startswith("references/"):
                log.warning("Dropping patch edit outside allowed directories: %s", edit.file)
                continue
            if edit.op not in _SUPPORTED_PATCH_OPS:
                log.warning(
                    "Dropping patch edit for %s due to unsupported op %r",
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
                refs = self._extract_reference_paths_from_text(edit.content)
                missing = [
                    ref for ref in refs if ref not in skill_state and ref not in create_targets
                ]
                if missing:
                    log.warning(
                        "Dropping SKILL.md patch edit because it inserts missing reference path(s) %s",
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
                    "Dropping orphaned reference create op for %s because no surviving SKILL.md edit links to it",
                    edit.file,
                )
                continue
            sanitized.append(edit)
        return sanitized

    def apply_patch_edits(
        self,
        skill_state: dict[str, str],
        patch_edits: list[PatchEdit],
        reasoning: str,
        changelog: list[str],
    ) -> tuple[list[FileEdit], str, list[str]]:
        if not patch_edits:
            return [], reasoning, list(changelog)

        updated: dict[str, str] = dict(skill_state)
        deleted: set[str] = set()
        apply_entries: list[str] = []

        for edit in patch_edits:
            if edit.op == "delete_file":
                if edit.file in updated:
                    del updated[edit.file]
                    deleted.add(edit.file)
                    apply_entries.append(f"Deleted {edit.file}")
                else:
                    log.warning("delete_file: file not found: %s", edit.file)
                continue

            if edit.op == "create":
                updated[edit.file] = edit.content
                apply_entries.append(f"Created {edit.file}")
                continue

            current = updated.get(edit.file, "")
            new_content = _apply_patch_edit_to_content(current, edit)
            if new_content == current:
                continue
            updated[edit.file] = new_content
            label = edit.target_section or edit.target_text or edit.old_text or ""
            apply_entries.append(
                f"{edit.op} in {edit.file}" + (f": {label[:60]}" if label else "")
            )

        file_edits: list[FileEdit] = []
        for path, content in updated.items():
            action = "modify" if path in skill_state else "create"
            file_edits.append(FileEdit(relative_path=path, content=content, action=action))
        for path in deleted:
            file_edits.append(FileEdit(relative_path=path, content="", action="delete"))
        return file_edits, reasoning, list(changelog) + apply_entries

    def _call_llm(
        self, user_message: str, max_continuations: int = 2
    ) -> str:
        """Send system + user message to LLM, with automatic continuation.

        If the response contains truncated JSON (e.g. the LLM hit its output
        token limit), this method sends follow-up continuation requests and
        concatenates the fragments until the JSON is parseable or retries
        are exhausted.
        """
        messages = [
            Message(role="system", content=self.system_prompt),
            Message(role="user", content=user_message),
        ]
        if self.tokenizer is not None and self.verbose:
            try:
                serialized = "\n\n".join(
                    f"{m.role}:\n{m.content}" for m in messages
                )
                input_tokens = len(self.tokenizer.encode(serialized))
                log.info("LLM input tokens (approx): %d", input_tokens)
            except Exception as e:
                log.warning("Tokenizer input count failed: %s", e)
        settings = ModelSettings(
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        full_response = self.client.chat(messages, settings)
        if self.verbose:
            log.info("LLM raw output (initial):\n%s", full_response)

        for attempt in range(1, max_continuations + 1):
            payloads, feedback = self._extract_json_payloads_with_feedback(full_response)
            if payloads:
                break

            raw_json = self._extract_json_text(full_response)
            has_open_json_fence = "```json" in full_response
            has_closed_json_fence = self._has_closed_fenced_block(full_response, "json")

            if has_open_json_fence and not has_closed_json_fence and raw_json:
                log.warning(
                    "Response appears truncated (continuation %d/%d), "
                    "requesting LLM to continue...",
                    attempt,
                    max_continuations,
                )
                messages.append(Message(role="assistant", content=_strip_think(full_response)))
                messages.append(Message(role="user", content=(
                    "Your response was cut off mid-JSON. Continue from EXACTLY "
                    "where you stopped. Output ONLY the remaining text — do not "
                    "repeat anything already written. Do not add explanation."
                )))
                continuation = self.client.chat(messages, settings)
                if self.verbose:
                    log.info(
                        "LLM raw output (continuation %d/%d):\n%s",
                        attempt,
                        max_continuations,
                        continuation,
                    )
                full_response += continuation
                continue

            log.warning(
                "Response JSON formatting is invalid (retry %d/%d): %s",
                attempt,
                max_continuations,
                feedback,
            )
            messages.append(Message(role="assistant", content=_strip_think(full_response)))
            messages.append(Message(role="user", content=self._build_json_retry_prompt(
                feedback=feedback,
                issue="the fenced json block is malformed or missing",
            )))
            full_response = self.client.chat(messages, settings)
            if self.verbose:
                log.info(
                    "LLM raw output (format retry %d/%d):\n%s",
                    attempt,
                    max_continuations,
                    full_response,
                )

        if self.verbose:
            if self.tokenizer is not None:
                try:
                    output_tokens = len(self.tokenizer.encode(full_response))
                    log.info("LLM output tokens (approx): %d", output_tokens)
                except Exception as e:
                    log.warning("Tokenizer output count failed: %s", e)
            log.info("LLM output:\n%s", full_response)

        return full_response

    def _save_checkpoint(
        self,
        checkpoint_dir: Path,
        label: str,
        cumulative_changelog: list[str],
    ) -> Path:
        """Copy the current skill folder to a checkpoint directory.

        Returns the path of the created checkpoint.
        """
        dest = checkpoint_dir / label
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(self.skill_dir, dest)

        # Write a small metadata file inside the checkpoint
        meta = {
            "label": label,
            "timestamp": datetime.now().isoformat(),
            "changelog": cumulative_changelog,
        }
        (dest / "_checkpoint_meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        log.info("Saved checkpoint: %s", dest)
        return dest

    @staticmethod
    def _is_context_length_bad_request(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "badrequesterror" in text
            and "input_tokens" in text
            and "context length" in text
        )

    def run_evolution(
        self,
        error_records: list[dict],
        run_consolidation: bool = True,
        save_step: int | None = None,
        checkpoint_dir: Path | None = None,
    ) -> EvolutionResult:
        """Main loop: batch records, call LLM per batch, apply edits.

        Args:
            error_records: List of error analysis records to process.
            run_consolidation: Run a final consolidation pass after all batches.
            save_step: If set, run consolidation and save a checkpoint every
                ``save_step`` batches (like training checkpoints).
            checkpoint_dir: Where to save checkpoints. Defaults to
                ``{skill_dir}_checkpoints/``.
        """
        steps: list[EvolutionStep] = []
        cumulative_changelog: list[str] = []
        all_diffs: list[str] = []
        total_llm_calls = 0

        # Resolve checkpoint dir
        if save_step and checkpoint_dir is None:
            checkpoint_dir = self.skill_dir.parent / f"{self.skill_dir.name}_checkpoints"
        if checkpoint_dir is not None:
            checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # Batch the records
        batches: list[list[dict]] = []
        for i in range(0, len(error_records), self.batch_size):
            batches.append(error_records[i : i + self.batch_size])

        total_batches = len(batches)
        if self.verbose:
            try:
                from tqdm import tqdm  # type: ignore

                self._progress = tqdm(
                    total=total_batches,
                    desc="Batches",
                    unit="batch",
                    leave=True,
                )
            except Exception:
                self._progress = None
        if self.verbose:
            log.info(
                "Starting evolution: %d records in %d batches (batch_size=%d)",
                len(error_records),
                total_batches,
                self.batch_size,
            )
            if save_step:
                log.info(
                    "Checkpoints every %d step(s) -> %s",
                    save_step,
                    checkpoint_dir,
                )

        for batch_idx, batch in enumerate(batches, start=1):
            instance_ids = [r.get("instance_id", "?") for r in batch]
            if self.verbose:
                log.info(
                    "Batch %d/%d — instances: %s",
                    batch_idx,
                    total_batches,
                    ", ".join(instance_ids),
                )

            # Snapshot for rollback and diff computation
            pre_step_snapshot = self._snapshot_skill()

            # Build message and call LLM
            skill_state = self.read_skill_state()
            user_msg = self.build_user_message(
                skill_state, batch, batch_idx, total_batches
            )
            try:
                response = self._call_llm(user_msg)
            except Exception as exc:
                if not self._is_context_length_bad_request(exc):
                    raise
                message = f"SKIPPED batch {batch_idx}: context-length bad request — {exc}"
                log.warning(message)
                step = EvolutionStep(
                    batch_index=batch_idx,
                    instance_ids=instance_ids,
                    edits=[],
                    diffs=[],
                    reasoning="Skipped batch because prompt exceeded model context length.",
                    changelog_entries=[message],
                )
                steps.append(step)
                if self._progress is not None:
                    self._progress.update(1)
                continue
            total_llm_calls += 1

            # Parse response
            patch_edits, reasoning, changelog = self.parse_llm_response(response)
            edits, reasoning, changelog = self.apply_patch_edits(
                skill_state,
                patch_edits,
                reasoning,
                changelog,
            )
            if self.verbose:
                log.info("Reasoning: %s", reasoning)
                log.info(
                    "Patch edits: %d, file edits: %d, Changelog: %d entries",
                    len(patch_edits),
                    len(edits),
                    len(changelog),
                )

            # Compute diffs before applying
            diffs = self.compute_diffs(pre_step_snapshot, edits)

            # Log diffs
            if self.verbose and diffs:
                for d in diffs:
                    if d.unified_diff:
                        log.info(
                            "Diff for %s (%s):\n%s",
                            d.relative_path, d.action, d.unified_diff,
                        )

            # Apply edits
            if edits:
                self.apply_edits(edits)

                # Validate
                if not self.dry_run:
                    valid, msg = self.validate_skill()
                    if not valid:
                        log.warning(
                            "Validation failed after batch %d: %s — rolling back",
                            batch_idx,
                            msg,
                        )
                        self._restore_snapshot(pre_step_snapshot)
                        reasoning += f" [ROLLED BACK: validation failed — {msg}]"
                        changelog.append(f"ROLLED BACK batch {batch_idx}: {msg}")
                        diffs = []  # edits were reverted

            # Collect diff text
            for d in diffs:
                if d.unified_diff:
                    all_diffs.append(
                        f"# Batch {batch_idx}: {d.relative_path} ({d.action})\n"
                        f"{d.unified_diff}"
                    )

            step = EvolutionStep(
                batch_index=batch_idx,
                instance_ids=instance_ids,
                edits=edits,
                diffs=diffs,
                reasoning=reasoning,
                changelog_entries=changelog,
            )
            steps.append(step)
            cumulative_changelog.extend(changelog)
            if self._progress is not None:
                self._progress.update(1)

            # --- Periodic checkpoint ---
            if (
                save_step
                and batch_idx % save_step == 0
                and not self.dry_run
                and batch_idx < total_batches  # skip if this is the last batch
                and self.enable_llm_cleanup
            ):
                if self.verbose:
                    log.info(
                        "Checkpoint at step %d: running consolidation...",
                        batch_idx,
                    )
                consolidation_step = self.run_consolidation_pass(
                    cumulative_changelog
                )
                steps.append(consolidation_step)
                total_llm_calls += 1
                for d in consolidation_step.diffs:
                    if d.unified_diff:
                        all_diffs.append(
                            f"# Consolidation (step {batch_idx}): "
                            f"{d.relative_path} ({d.action})\n"
                            f"{d.unified_diff}"
                        )
                self._save_checkpoint(
                    checkpoint_dir, f"step_{batch_idx}", cumulative_changelog
                )

        # Final consolidation pass
        if run_consolidation and not self.dry_run and self.enable_llm_cleanup:
            if self.verbose:
                log.info("Running final consolidation pass...")
            consolidation_step = self.run_consolidation_pass(cumulative_changelog)
            steps.append(consolidation_step)
            total_llm_calls += 1
            for d in consolidation_step.diffs:
                if d.unified_diff:
                    all_diffs.append(
                        f"# Consolidation (final): {d.relative_path} ({d.action})\n"
                        f"{d.unified_diff}"
                    )

        # Save final checkpoint
        if save_step and checkpoint_dir and not self.dry_run:
            self._save_checkpoint(
                checkpoint_dir, "final", cumulative_changelog
            )

        # Compute final stats
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        skill_state = self.read_skill_state()
        skill_md_content = skill_state.get("SKILL.md", "")
        final_lines = skill_md_content.count("\n") + 1

        return EvolutionResult(
            steps=steps,
            total_records_processed=len(error_records),
            total_llm_calls=total_llm_calls,
            final_skill_md_lines=final_lines,
            files_created=sorted(self._files_created),
            files_modified=sorted(self._files_modified),
            cumulative_patch="\n\n".join(all_diffs),
        )

    def run_consolidation_pass(
        self,
        cumulative_changelog: list[str] | None = None,
    ) -> EvolutionStep:
        """Final pass: deduplicate, enforce line limits, verify links."""
        pre_step_snapshot = self._snapshot_skill()
        skill_state = self.read_skill_state()

        user_msg = self.build_consolidation_message(
            skill_state, cumulative_changelog or []
        )
        response = self._call_llm(user_msg)
        patch_edits, reasoning, changelog = self.parse_llm_response(response)
        edits, reasoning, changelog = self.apply_patch_edits(
            skill_state,
            patch_edits,
            reasoning,
            changelog,
        )

        diffs = self.compute_diffs(pre_step_snapshot, edits)

        if self.verbose and diffs:
            for d in diffs:
                if d.unified_diff:
                    log.info(
                        "Consolidation diff for %s:\n%s",
                        d.relative_path, d.unified_diff,
                    )

        if edits:
            self.apply_edits(edits)

            valid, msg = self.validate_skill()
            if not valid:
                log.warning(
                    "Validation failed after consolidation: %s — rolling back", msg
                )
                self._restore_snapshot(pre_step_snapshot)
                reasoning += f" [ROLLED BACK: validation failed — {msg}]"
                changelog.append(f"ROLLED BACK consolidation: {msg}")
                diffs = []

        return EvolutionStep(
            batch_index=-1,
            instance_ids=["consolidation"],
            edits=edits,
            diffs=diffs,
            reasoning=reasoning,
            changelog_entries=changelog,
        )

    # -- pattern-based evolution ---------------------------------------------

    def run_evolution_from_patterns(
        self,
        patterns_by_type: dict[str, list[dict]],
        run_consolidation: bool = True,
        save_step: int | None = None,
        checkpoint_dir: Path | None = None,
    ) -> EvolutionResult:
        """Evolve the skill using compressed failure patterns.

        Instead of processing raw error records one-by-one, this method
        takes the pre-compressed patterns (output of ``compress_skill_errors.py
        --parsed_output``) and feeds them to the LLM in batches.

        Each batch contains one or more patterns across all item types
        (failure_cause, failure_memory). Patterns are interleaved by index
        so each LLM call gets both the cause and memory view for the same
        failure cluster.

        Args:
            patterns_by_type: Dict like ``{"failure_cause": [...], "failure_memory": [...]}``.
            run_consolidation: Run a final consolidation pass.
            save_step: Checkpoint every N batches.
            checkpoint_dir: Where to save checkpoints.
        """
        steps: list[EvolutionStep] = []
        cumulative_changelog: list[str] = []
        all_diffs: list[str] = []
        total_llm_calls = 0

        # Resolve checkpoint dir
        if save_step and checkpoint_dir is None:
            checkpoint_dir = self.skill_dir.parent / f"{self.skill_dir.name}_checkpoints"
        if checkpoint_dir is not None:
            checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # Build batches of patterns. Each batch is a dict with the same
        # keys as patterns_by_type, containing a slice of patterns.
        # We batch by pattern index across types.
        max_patterns = max(
            (len(pats) for pats in patterns_by_type.values()), default=0
        )
        batches: list[dict[str, list[dict]]] = []
        for i in range(0, max_patterns, self.batch_size):
            batch: dict[str, list[dict]] = {}
            for item_type, pats in patterns_by_type.items():
                batch[item_type] = pats[i : i + self.batch_size]
            # Only include if the batch has at least one pattern
            if any(batch.values()):
                batches.append(batch)

        total_batches = len(batches)
        if self.verbose:
            try:
                from tqdm import tqdm  # type: ignore

                self._progress = tqdm(
                    total=total_batches,
                    desc="Pattern batches",
                    unit="batch",
                    leave=True,
                )
            except Exception:
                self._progress = None
            total_patterns = sum(len(p) for p in patterns_by_type.values())
            log.info(
                "Starting pattern-based evolution: %d patterns in %d batches "
                "(batch_size=%d)",
                total_patterns,
                total_batches,
                self.batch_size,
            )
            if save_step:
                log.info(
                    "Checkpoints every %d step(s) -> %s",
                    save_step,
                    checkpoint_dir,
                )

        for batch_idx, batch_patterns in enumerate(batches, start=1):
            # Collect pattern titles for logging
            pattern_titles: list[str] = []
            for pats in batch_patterns.values():
                for p in pats:
                    pattern_titles.append(p.get("title", "?"))
            if self.verbose:
                log.info(
                    "Batch %d/%d — patterns: %s",
                    batch_idx,
                    total_batches,
                    ", ".join(pattern_titles),
                )

            # Snapshot for rollback and diff
            pre_step_snapshot = self._snapshot_skill()

            # Build message and call LLM
            skill_state = self.read_skill_state()
            user_msg = self.build_user_message_from_patterns(
                skill_state, batch_patterns, batch_idx, total_batches
            )
            try:
                response = self._call_llm(user_msg)
            except Exception as exc:
                if not self._is_context_length_bad_request(exc):
                    raise
                message = f"SKIPPED batch {batch_idx}: context-length bad request — {exc}"
                log.warning(message)
                step = EvolutionStep(
                    batch_index=batch_idx,
                    instance_ids=pattern_titles,
                    edits=[],
                    diffs=[],
                    reasoning="Skipped batch because prompt exceeded model context length.",
                    changelog_entries=[message],
                )
                steps.append(step)
                if self._progress is not None:
                    self._progress.update(1)
                continue
            total_llm_calls += 1

            # Parse response
            patch_edits, reasoning, changelog = self.parse_llm_response(response)
            edits, reasoning, changelog = self.apply_patch_edits(
                skill_state,
                patch_edits,
                reasoning,
                changelog,
            )
            if self.verbose:
                log.info("Reasoning: %s", reasoning)
                log.info(
                    "Patch edits: %d, file edits: %d, Changelog: %d entries",
                    len(patch_edits),
                    len(edits),
                    len(changelog),
                )

            # Compute diffs before applying
            diffs = self.compute_diffs(pre_step_snapshot, edits)

            if self.verbose and diffs:
                for d in diffs:
                    if d.unified_diff:
                        log.info(
                            "Diff for %s (%s):\n%s",
                            d.relative_path, d.action, d.unified_diff,
                        )

            # Apply edits
            if edits:
                self.apply_edits(edits)

                if not self.dry_run:
                    valid, msg = self.validate_skill()
                    if not valid:
                        log.warning(
                            "Validation failed after batch %d: %s — rolling back",
                            batch_idx,
                            msg,
                        )
                        self._restore_snapshot(pre_step_snapshot)
                        reasoning += f" [ROLLED BACK: validation failed — {msg}]"
                        changelog.append(f"ROLLED BACK batch {batch_idx}: {msg}")
                        diffs = []

            for d in diffs:
                if d.unified_diff:
                    all_diffs.append(
                        f"# Batch {batch_idx}: {d.relative_path} ({d.action})\n"
                        f"{d.unified_diff}"
                    )

            step = EvolutionStep(
                batch_index=batch_idx,
                instance_ids=pattern_titles,
                edits=edits,
                diffs=diffs,
                reasoning=reasoning,
                changelog_entries=changelog,
            )
            steps.append(step)
            cumulative_changelog.extend(changelog)
            if self._progress is not None:
                self._progress.update(1)

            # Periodic checkpoint
            if (
                save_step
                and batch_idx % save_step == 0
                and not self.dry_run
                and batch_idx < total_batches
                and self.enable_llm_cleanup
            ):
                if self.verbose:
                    log.info(
                        "Checkpoint at step %d: running consolidation...",
                        batch_idx,
                    )
                consolidation_step = self.run_consolidation_pass(
                    cumulative_changelog
                )
                steps.append(consolidation_step)
                total_llm_calls += 1
                for d in consolidation_step.diffs:
                    if d.unified_diff:
                        all_diffs.append(
                            f"# Consolidation (step {batch_idx}): "
                            f"{d.relative_path} ({d.action})\n"
                            f"{d.unified_diff}"
                        )
                self._save_checkpoint(
                    checkpoint_dir, f"step_{batch_idx}", cumulative_changelog
                )

        # Final consolidation
        if run_consolidation and not self.dry_run and self.enable_llm_cleanup:
            if self.verbose:
                log.info("Running final consolidation pass...")
            consolidation_step = self.run_consolidation_pass(cumulative_changelog)
            steps.append(consolidation_step)
            total_llm_calls += 1
            for d in consolidation_step.diffs:
                if d.unified_diff:
                    all_diffs.append(
                        f"# Consolidation (final): {d.relative_path} ({d.action})\n"
                        f"{d.unified_diff}"
                    )

        # Save final checkpoint
        if save_step and checkpoint_dir and not self.dry_run:
            self._save_checkpoint(
                checkpoint_dir, "final", cumulative_changelog
            )

        # Final stats
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        skill_state = self.read_skill_state()
        skill_md_content = skill_state.get("SKILL.md", "")
        final_lines = skill_md_content.count("\n") + 1

        total_patterns = sum(len(p) for p in patterns_by_type.values())
        return EvolutionResult(
            steps=steps,
            total_records_processed=total_patterns,
            total_llm_calls=total_llm_calls,
            final_skill_md_lines=final_lines,
            files_created=sorted(self._files_created),
            files_modified=sorted(self._files_modified),
            cumulative_patch="\n\n".join(all_diffs),
        )
