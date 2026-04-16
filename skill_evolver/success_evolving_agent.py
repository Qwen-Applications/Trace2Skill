"""
Success-oriented prompt helpers for skill evolution.
"""

from __future__ import annotations

from skill_evolver.prompt_loader import load_prompt_template
from skill_evolver.skill_evolving_agent import SYSTEM_PROMPT_BASE


SUCCESS_RECORD_SECTION = load_prompt_template(
    "success_evolving_agent/success_record_section"
)
SUCCESS_MODIFICATION_STRATEGIES_SECTION = load_prompt_template(
    "success_evolving_agent/success_modification_strategies_section"
)

SUCCESS_PATTERNS_SECTION = load_prompt_template(
    "success_evolving_agent/success_patterns_section"
)

COMBINED_RECORD_SECTION = load_prompt_template(
    "success_evolving_agent/combined_record_section"
)
COMBINED_MODIFICATION_STRATEGIES_SECTION = load_prompt_template(
    "success_evolving_agent/combined_modification_strategies_section"
)

COMBINED_PATTERNS_SECTION = load_prompt_template(
    "success_evolving_agent/combined_patterns_section"
)

SUCCESS_GOAL_REPLACEMENT = load_prompt_template(
    "success_evolving_agent/success_goal_replacement"
)
SUCCESS_FIRST_CONSTRAINT_REPLACEMENT = load_prompt_template(
    "success_evolving_agent/success_first_constraint_replacement"
)
SUCCESS_TRACEABILITY_CONSTRAINT = load_prompt_template(
    "success_evolving_agent/success_traceability_constraint"
)
SUCCESS_OUTPUT_REASONING_REPLACEMENT = load_prompt_template(
    "success_evolving_agent/success_output_reasoning_replacement"
)

COMBINED_GOAL_REPLACEMENT = load_prompt_template(
    "success_evolving_agent/combined_goal_replacement"
)
COMBINED_FIRST_CONSTRAINT_REPLACEMENT = load_prompt_template(
    "success_evolving_agent/combined_first_constraint_replacement"
)
COMBINED_TRACEABILITY_CONSTRAINT = load_prompt_template(
    "success_evolving_agent/combined_traceability_constraint"
)
COMBINED_OUTPUT_REASONING_REPLACEMENT = load_prompt_template(
    "success_evolving_agent/combined_output_reasoning_replacement"
)


def _replace_core_prompt_text(
    base: str,
    *,
    replacement_intro: str,
    replacement_input: str,
    replacement_goal: str,
    replacement_first_constraint: str,
    replacement_traceability_constraint: str,
    replacement_output_reasoning: str,
) -> str:
    replacements = (
        (
            "You are a skill editor specializing in improving AI agent skills based on\n"
            "observed failure patterns. Your task is to iteratively refine a spreadsheet\n"
            "skill folder so that agents using it make fewer errors in the future.\n",
            replacement_intro,
        ),
        (
            "2. Error analysis records from tasks where agents using this skill failed\n",
            replacement_input,
        ),
        (
            "Your job: propose **skill folder modifications** — changes to SKILL.md,\n"
            "creation/modification/deletion of reference files — that would help prevent\n"
            "these failures, while keeping the skill concise, well-organized, and under\n"
            "size limits.\n",
            replacement_goal,
        ),
        (
            "1. NEVER remove guidance that is currently correct and useful — be additive first",
            replacement_first_constraint,
        ),
        (
            "6. Every change must trace to an observed failure pattern",
            replacement_traceability_constraint,
        ),
        (
            '"reasoning":"2-3 sentences explaining what failure patterns you see and what skill folder changes address them"',
            f'"reasoning":"{replacement_output_reasoning}"',
        ),
    )
    for old_text, new_text in replacements:
        if old_text not in base:
            raise ValueError(f"Expected base system prompt text not found: {old_text!r}")
        base = base.replace(old_text, new_text)
    return base


def build_success_system_prompt() -> str:
    base = _replace_core_prompt_text(
        SYSTEM_PROMPT_BASE,
        replacement_intro=load_prompt_template("success_evolving_agent/success_intro_replacement"),
        replacement_input=load_prompt_template("success_evolving_agent/success_input_replacement"),
        replacement_goal=SUCCESS_GOAL_REPLACEMENT,
        replacement_first_constraint=SUCCESS_FIRST_CONSTRAINT_REPLACEMENT,
        replacement_traceability_constraint=SUCCESS_TRACEABILITY_CONSTRAINT,
        replacement_output_reasoning=SUCCESS_OUTPUT_REASONING_REPLACEMENT,
    )
    return base.format(
        modification_strategies_section=SUCCESS_MODIFICATION_STRATEGIES_SECTION,
        error_record_section=SUCCESS_RECORD_SECTION,
    )


def build_combined_system_prompt() -> str:
    base = _replace_core_prompt_text(
        SYSTEM_PROMPT_BASE,
        replacement_intro=load_prompt_template("success_evolving_agent/combined_intro_replacement"),
        replacement_input=load_prompt_template("success_evolving_agent/combined_input_replacement"),
        replacement_goal=COMBINED_GOAL_REPLACEMENT,
        replacement_first_constraint=COMBINED_FIRST_CONSTRAINT_REPLACEMENT,
        replacement_traceability_constraint=COMBINED_TRACEABILITY_CONSTRAINT,
        replacement_output_reasoning=COMBINED_OUTPUT_REASONING_REPLACEMENT,
    )
    return base.format(
        modification_strategies_section=COMBINED_MODIFICATION_STRATEGIES_SECTION,
        error_record_section=COMBINED_RECORD_SECTION,
    )


def build_success_patterns_system_prompt() -> str:
    base = _replace_core_prompt_text(
        SYSTEM_PROMPT_BASE,
        replacement_intro=load_prompt_template("success_evolving_agent/success_intro_replacement"),
        replacement_input=load_prompt_template("success_evolving_agent/success_patterns_input_replacement"),
        replacement_goal=SUCCESS_GOAL_REPLACEMENT,
        replacement_first_constraint=SUCCESS_FIRST_CONSTRAINT_REPLACEMENT,
        replacement_traceability_constraint=SUCCESS_TRACEABILITY_CONSTRAINT,
        replacement_output_reasoning=SUCCESS_OUTPUT_REASONING_REPLACEMENT,
    )
    return base.format(
        modification_strategies_section=SUCCESS_MODIFICATION_STRATEGIES_SECTION,
        error_record_section=SUCCESS_PATTERNS_SECTION,
    )


def build_combined_patterns_system_prompt() -> str:
    base = _replace_core_prompt_text(
        SYSTEM_PROMPT_BASE,
        replacement_intro=load_prompt_template("success_evolving_agent/combined_intro_replacement"),
        replacement_input=load_prompt_template("success_evolving_agent/combined_patterns_input_replacement"),
        replacement_goal=COMBINED_GOAL_REPLACEMENT,
        replacement_first_constraint=COMBINED_FIRST_CONSTRAINT_REPLACEMENT,
        replacement_traceability_constraint=COMBINED_TRACEABILITY_CONSTRAINT,
        replacement_output_reasoning=COMBINED_OUTPUT_REASONING_REPLACEMENT,
    )
    return base.format(
        modification_strategies_section=COMBINED_MODIFICATION_STRATEGIES_SECTION,
        error_record_section=COMBINED_PATTERNS_SECTION,
    )


def _format_item(item: dict) -> list[str]:
    item_type = item.get("type", "unknown")
    if item_type == "failure_cause":
        type_label = "Failure Cause"
    elif item_type == "failure_memory":
        type_label = "Failure Memory"
    elif item_type == "success_memory":
        type_label = "Success Memory"
    else:
        type_label = item_type.replace("_", " ").title()

    lines = [f"**{type_label}: {item.get('title', 'Untitled')}**"]
    description = item.get("description", "")
    if description:
        lines.append(f"*{description}*")
    content = item.get("content", "")
    if content:
        lines.append(content)
    relation = item.get("relation_to_skill", "")
    if relation:
        lines.append(f"*Relation To Skill (suggestion)*: {relation}")
    reflection = item.get("skill_reflection", "")
    if reflection:
        lines.append(f"*Skill Reflection (suggestion)*: {reflection}")
    lines.append("")
    return lines


def format_success_records_for_prompt(
    records: list[dict],
    batch_idx: int,
    total_batches: int,
) -> str:
    parts = [
        load_prompt_template("success_evolving_agent/success_analysis_records_header").format(
            batch_idx=batch_idx,
            total_batches=total_batches,
        ),
        "",
    ]
    for record in records:
        parts.append(f"### Record: instance {record['instance_id']}")
        source_file = record.get("source_file", "")
        if source_file:
            parts.append(f"Source File: {source_file}")
        for item in record.get("items", []):
            parts.extend(_format_item(item))
    return "\n".join(parts)


def format_mixed_records_for_prompt(
    records: list[dict],
    batch_idx: int,
    total_batches: int,
) -> str:
    parts = [
        load_prompt_template("success_evolving_agent/combined_analysis_records_header").format(
            batch_idx=batch_idx,
            total_batches=total_batches,
        ),
        "",
    ]
    for record in records:
        source = record.get("record_source", "unknown")
        source_label = "Error" if source == "error" else "Success" if source == "success" else source.title()
        parts.append(f"### {source_label} Record: instance {record['instance_id']}")
        source_file = record.get("source_file", "")
        if source_file:
            parts.append(f"Source File: {source_file}")
        for item in record.get("items", []):
            parts.extend(_format_item(item))
    return "\n".join(parts)


def build_success_user_message(
    skill_state: dict[str, str],
    records: list[dict],
    batch_idx: int,
    total_batches: int,
    max_skill_lines: int,
    max_references: int,
) -> str:
    parts: list[str] = []
    parts.append(load_prompt_template("success_evolving_agent/current_skill_folder_header"))
    for path, content in sorted(skill_state.items()):
        n_lines = content.count("\n") + 1
        parts.append(f"### {path} ({n_lines} lines)")
        parts.append(f"```markdown\n{content}\n```\n")

    parts.append(format_success_records_for_prompt(records, batch_idx, total_batches))
    parts.append("")
    _append_size_status(parts, skill_state, max_skill_lines, max_references)
    return "\n".join(parts)


def build_combined_user_message(
    skill_state: dict[str, str],
    records: list[dict],
    batch_idx: int,
    total_batches: int,
    max_skill_lines: int,
    max_references: int,
) -> str:
    parts: list[str] = []
    parts.append(load_prompt_template("success_evolving_agent/current_skill_folder_header"))
    for path, content in sorted(skill_state.items()):
        n_lines = content.count("\n") + 1
        parts.append(f"### {path} ({n_lines} lines)")
        parts.append(f"```markdown\n{content}\n```\n")

    parts.append(format_mixed_records_for_prompt(records, batch_idx, total_batches))
    parts.append("")
    _append_size_status(parts, skill_state, max_skill_lines, max_references)
    return "\n".join(parts)


def build_success_patterns_user_message(
    skill_state: dict[str, str],
    patterns: dict[str, list[dict]],
    batch_idx: int,
    total_batches: int,
    max_skill_lines: int,
    max_references: int,
) -> str:
    parts: list[str] = []
    parts.append(load_prompt_template("success_evolving_agent/current_skill_folder_header"))
    for path, content in sorted(skill_state.items()):
        n_lines = content.count("\n") + 1
        parts.append(f"### {path} ({n_lines} lines)")
        parts.append(f"```markdown\n{content}\n```\n")

    parts.append(
        load_prompt_template("success_evolving_agent/compressed_success_patterns_header").format(
            batch_idx=batch_idx,
            total_batches=total_batches,
        )
    )
    parts.append(load_prompt_template("success_evolving_agent/compressed_success_patterns_intro"))
    _append_pattern_groups(parts, patterns)
    _append_size_status(parts, skill_state, max_skill_lines, max_references)
    return "\n".join(parts)


def build_combined_patterns_user_message(
    skill_state: dict[str, str],
    patterns: dict[str, list[dict]],
    batch_idx: int,
    total_batches: int,
    max_skill_lines: int,
    max_references: int,
) -> str:
    parts: list[str] = []
    parts.append(load_prompt_template("success_evolving_agent/current_skill_folder_header"))
    for path, content in sorted(skill_state.items()):
        n_lines = content.count("\n") + 1
        parts.append(f"### {path} ({n_lines} lines)")
        parts.append(f"```markdown\n{content}\n```\n")

    parts.append(
        load_prompt_template("success_evolving_agent/compressed_mixed_patterns_header").format(
            batch_idx=batch_idx,
            total_batches=total_batches,
        )
    )
    parts.append(load_prompt_template("success_evolving_agent/compressed_mixed_patterns_intro"))
    _append_pattern_groups(parts, patterns)
    _append_size_status(parts, skill_state, max_skill_lines, max_references)
    return "\n".join(parts)


def _append_pattern_groups(parts: list[str], patterns: dict[str, list[dict]]) -> None:
    for item_type, type_patterns in patterns.items():
        if not type_patterns:
            continue
        if item_type == "failure_cause":
            type_label = "Failure Cause Patterns"
        elif item_type == "failure_memory":
            type_label = "Failure Memory Patterns"
        elif item_type == "success_memory":
            type_label = "Success Memory Patterns"
        else:
            type_label = item_type.replace("_", " ").title() + " Patterns"

        parts.append(f"### {type_label}\n")
        for pat in type_patterns:
            parts.append(f"**Pattern {pat.get('index', '?')}: {pat.get('title', 'Untitled')}**")
            desc = pat.get("description", "")
            if desc:
                parts.append(f"*{desc}*")
            improvement = pat.get("skill_improvement", "")
            if improvement:
                parts.append(f"*Skill improvement (suggestion)*: {improvement}")
            details = pat.get("covered_specific_errors", "") or pat.get("covered_specific_successes", "")
            if details:
                parts.append(f"Specific details:\n{details}")
            parts.append("")


def _append_size_status(
    parts: list[str],
    skill_state: dict[str, str],
    max_skill_lines: int,
    max_references: int,
) -> None:
    skill_md_content = skill_state.get("SKILL.md", "")
    skill_lines = skill_md_content.count("\n") + 1
    ref_count = sum(1 for path in skill_state if path.startswith("references/"))
    parts.append(load_prompt_template("success_evolving_agent/skill_folder_size_status_header"))
    parts.append(
        load_prompt_template("success_evolving_agent/skill_md_status_line").format(
            skill_lines=skill_lines,
            max_skill_lines=max_skill_lines,
        )
    )
    parts.append(
        load_prompt_template("success_evolving_agent/reference_files_status_line").format(
            ref_count=ref_count,
            max_references=max_references,
        )
    )
    if skill_lines > max_skill_lines - 50:
        parts.append(load_prompt_template("success_evolving_agent/size_warning"))
