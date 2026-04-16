"""
Helpers and subclasses for success-only and combined parallel skill evolution.
"""

from __future__ import annotations

import json

from skill_evolver.parallel_evolving_agent import (
    MARKDOWN_MERGE_SYSTEM_PROMPT,
    MARKDOWN_MERGE_SYSTEM_PROMPT_HEADING,
    MERGE_SYSTEM_PROMPT,
    _MAP_OUTPUT_FORMAT,
    _MARKDOWN_MAP_OUTPUT_FORMAT,
    _MARKDOWN_MAP_OUTPUT_FORMAT_HEADING,
    Patch,
    ParallelSkillEvolver,
    SemanticPatch,
)
from skill_evolver.prompt_loader import load_prompt_template
from skill_evolver.success_evolving_agent import (
    build_combined_patterns_system_prompt,
    build_combined_patterns_user_message,
    build_combined_system_prompt,
    build_combined_user_message,
    build_success_patterns_system_prompt,
    build_success_patterns_user_message,
    build_success_system_prompt,
    build_success_user_message,
)

SUCCESS_MERGE_SYSTEM_PROMPT = load_prompt_template(
    "success_evolving_agent/success_merge_system_prompt"
)
SUCCESS_MARKDOWN_MERGE_SYSTEM_PROMPT = load_prompt_template(
    "success_evolving_agent/success_markdown_merge_system_prompt"
)
COMBINED_MERGE_SYSTEM_PROMPT = load_prompt_template(
    "success_evolving_agent/combined_merge_system_prompt"
)
COMBINED_MARKDOWN_MERGE_SYSTEM_PROMPT = load_prompt_template(
    "success_evolving_agent/combined_markdown_merge_system_prompt"
)


def normalize_mixed_records(
    error_records: list[dict], success_records: list[dict]
) -> list[dict]:
    mixed = []
    for record in error_records:
        mixed.append(
            {
                "record_source": "error",
                "instance_id": record["instance_id"],
                "source_file": record.get("source_file", ""),
                "items": record.get("items", []),
            }
        )
    for record in success_records:
        mixed.append(
            {
                "record_source": "success",
                "instance_id": record["instance_id"],
                "source_file": record.get("source_file", ""),
                "items": record.get("items", []),
            }
        )
    return mixed


def normalize_mixed_patterns(
    error_patterns: dict[str, list[dict]], success_patterns: dict[str, list[dict]]
) -> dict[str, list[dict]]:
    mixed: dict[str, list[dict]] = {}
    for key, values in error_patterns.items():
        mixed[key] = list(values)
    for key, values in success_patterns.items():
        mixed[key] = list(values)
    return mixed


def _build_success_merge_user_message(
    skill_state: dict[str, str],
    patches: list[Patch],
) -> str:
    parts: list[str] = []
    parts.append(load_prompt_template("parallel_evolving_agent/original_skill_folder_header"))
    for path, content in sorted(skill_state.items()):
        n_lines = content.count("\n") + 1
        parts.append(f"### {path} ({n_lines} lines)")
        parts.append(f"```markdown\n{content}\n```\n")

    parts.append(
        load_prompt_template("success_evolving_agent/success_patches_to_merge_header").format(
            patch_count=len(patches)
        )
    )
    parts.append(load_prompt_template("success_evolving_agent/success_patches_to_merge_intro"))
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


def _build_success_merge_user_message_semantic(
    skill_state: dict[str, str],
    patches: list[SemanticPatch],
    marker_format: str = "bracket",
) -> str:
    parts: list[str] = []
    parts.append(load_prompt_template("parallel_evolving_agent/original_skill_folder_header"))
    for path, content in sorted(skill_state.items()):
        n_lines = content.count("\n") + 1
        parts.append(f"### {path} ({n_lines} lines)")
        parts.append(f"```markdown\n{content}\n```\n")

    parts.append(
        load_prompt_template(
            "success_evolving_agent/success_semantic_patches_to_merge_header"
        ).format(patch_count=len(patches))
    )
    parts.append(
        load_prompt_template(
            "success_evolving_agent/success_semantic_patches_to_merge_intro"
        )
    )
    for i, patch in enumerate(patches, start=1):
        parts.append(f"### Patch {i}")
        parts.append(f"Reasoning: {patch.reasoning}")
        parts.append("Changelog:")
        for entry in patch.changelog_entries:
            parts.append(f"- {entry}")
        parts.append("Items:")
        for item_idx, item in enumerate(patch.items, start=1):
            if marker_format == "heading":
                parts.append(f"#### Item {item_idx}")
            else:
                parts.append(f"[ITEM_{item_idx}_START]")
            parts.append(f"Target File: {item.target_file}")
            parts.append(f"Edit Intent: {item.edit_intent}")
            parts.append(f"Location Hint: {item.location_hint}")
            parts.append("Change Instruction:")
            parts.append(item.change_instruction)
        parts.append("")
    return "\n".join(parts)


def _build_combined_merge_user_message(
    skill_state: dict[str, str],
    patches: list[Patch],
) -> str:
    parts: list[str] = []
    parts.append(load_prompt_template("parallel_evolving_agent/original_skill_folder_header"))
    for path, content in sorted(skill_state.items()):
        n_lines = content.count("\n") + 1
        parts.append(f"### {path} ({n_lines} lines)")
        parts.append(f"```markdown\n{content}\n```\n")

    parts.append(
        load_prompt_template("success_evolving_agent/combined_patches_to_merge_header").format(
            patch_count=len(patches)
        )
    )
    parts.append(load_prompt_template("success_evolving_agent/combined_patches_to_merge_intro"))
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


def _build_combined_merge_user_message_semantic(
    skill_state: dict[str, str],
    patches: list[SemanticPatch],
    marker_format: str = "bracket",
) -> str:
    parts: list[str] = []
    parts.append(load_prompt_template("parallel_evolving_agent/original_skill_folder_header"))
    for path, content in sorted(skill_state.items()):
        n_lines = content.count("\n") + 1
        parts.append(f"### {path} ({n_lines} lines)")
        parts.append(f"```markdown\n{content}\n```\n")

    parts.append(
        load_prompt_template(
            "success_evolving_agent/combined_semantic_patches_to_merge_header"
        ).format(patch_count=len(patches))
    )
    parts.append(
        load_prompt_template(
            "success_evolving_agent/combined_semantic_patches_to_merge_intro"
        )
    )
    for i, patch in enumerate(patches, start=1):
        parts.append(f"### Patch {i}")
        parts.append(f"Reasoning: {patch.reasoning}")
        parts.append("Changelog:")
        for entry in patch.changelog_entries:
            parts.append(f"- {entry}")
        parts.append("Items:")
        for item_idx, item in enumerate(patch.items, start=1):
            if marker_format == "heading":
                parts.append(f"#### Item {item_idx}")
            else:
                parts.append(f"[ITEM_{item_idx}_START]")
            parts.append(f"Target File: {item.target_file}")
            parts.append(f"Edit Intent: {item.edit_intent}")
            parts.append(f"Location Hint: {item.location_hint}")
            parts.append("Change Instruction:")
            parts.append(item.change_instruction)
        parts.append("")
    return "\n".join(parts)


class SuccessParallelSkillEvolver(ParallelSkillEvolver):
    """Parallel evolver configured for success-only records."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._map_patterns_system_prompt = self._build_map_system_prompt_from_patterns()
        self._merge_system_prompt = (
            SUCCESS_MERGE_SYSTEM_PROMPT
            if self.patch_pipeline == "json"
            else self._build_success_markdown_merge_system_prompt()
        )

    def _build_map_system_prompt(self) -> str:
        output_format = (
            _MAP_OUTPUT_FORMAT
            if self.patch_pipeline == "json"
            else self._get_markdown_map_output_format()
        )
        base = build_success_system_prompt()
        marker = "## Output Format"
        idx = base.find(marker)
        if idx == -1:
            return base + "\n\n" + output_format
        return base[:idx] + output_format

    def _build_map_system_prompt_from_patterns(self) -> str:
        output_format = (
            _MAP_OUTPUT_FORMAT
            if self.patch_pipeline == "json"
            else self._get_markdown_map_output_format()
        )
        base = build_success_patterns_system_prompt()
        marker = "## Output Format"
        idx = base.find(marker)
        if idx == -1:
            return base + "\n\n" + output_format
        return base[:idx] + output_format

    def _build_map_user_message(
        self,
        skill_state: dict[str, str],
        records: list[dict],
        batch_idx: int,
        total_batches: int,
    ) -> str:
        return build_success_user_message(
            skill_state,
            records,
            batch_idx,
            total_batches,
            self.max_skill_lines,
            self.max_references,
        )

    def _build_map_user_message_from_patterns(
        self,
        skill_state: dict[str, str],
        patterns: dict[str, list[dict]],
        batch_idx: int,
        total_batches: int,
    ) -> str:
        return build_success_patterns_user_message(
            skill_state,
            patterns,
            batch_idx,
            total_batches,
            self.max_skill_lines,
            self.max_references,
        )

    def _run_single_map_patterns(self, skill_state, pattern_batch, batch_idx, total_batches):
        user_msg = self._build_map_user_message_from_patterns(
            skill_state, pattern_batch, batch_idx, total_batches
        )
        response, conversation_trace = self._call_llm(
            self._map_patterns_system_prompt, user_msg, tag="patch"
        )
        self._save_prompt_response(
            "map", f"batch_{batch_idx:04d}", self._map_patterns_system_prompt, user_msg, response
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

    def _run_single_map_patterns_markdown(self, skill_state, pattern_batch, batch_idx, total_batches):
        user_msg = self._build_map_user_message_from_patterns(
            skill_state, pattern_batch, batch_idx, total_batches
        )
        response, conversation_trace = self._call_llm(
            self._map_patterns_system_prompt, user_msg, tag="patch", expect_semantic=True
        )
        self._save_prompt_response(
            "map", f"batch_{batch_idx:04d}", self._map_patterns_system_prompt, user_msg, response
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

    def _build_merge_user_message(
        self,
        skill_state: dict[str, str],
        patches: list[Patch],
    ) -> str:
        return _build_success_merge_user_message(skill_state, patches)

    def _build_merge_user_message_semantic(
        self,
        skill_state: dict[str, str],
        patches: list[SemanticPatch],
    ) -> str:
        return _build_success_merge_user_message_semantic(
            skill_state,
            patches,
            marker_format=self.semantic_item_marker_format,
        )

    def _build_success_markdown_merge_system_prompt(self) -> str:
        if self.semantic_item_marker_format == "heading":
            return SUCCESS_MARKDOWN_MERGE_SYSTEM_PROMPT.replace(
                "[ITEM_1_START]",
                "### Item 1",
            ).replace("\n\nRules:\n- Each item must start with [ITEM_X_START]", "")
        return SUCCESS_MARKDOWN_MERGE_SYSTEM_PROMPT


class CombinedParallelSkillEvolver(ParallelSkillEvolver):
    """Parallel evolver configured for mixed error and success records."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._map_patterns_system_prompt = self._build_map_system_prompt_from_patterns()
        self._merge_system_prompt = (
            COMBINED_MERGE_SYSTEM_PROMPT
            if self.patch_pipeline == "json"
            else self._build_combined_markdown_merge_system_prompt()
        )

    def _build_map_system_prompt(self) -> str:
        output_format = (
            _MAP_OUTPUT_FORMAT
            if self.patch_pipeline == "json"
            else self._get_markdown_map_output_format()
        )
        base = build_combined_system_prompt()
        marker = "## Output Format"
        idx = base.find(marker)
        if idx == -1:
            return base + "\n\n" + output_format
        return base[:idx] + output_format

    def _build_map_system_prompt_from_patterns(self) -> str:
        output_format = (
            _MAP_OUTPUT_FORMAT
            if self.patch_pipeline == "json"
            else self._get_markdown_map_output_format()
        )
        base = build_combined_patterns_system_prompt()
        marker = "## Output Format"
        idx = base.find(marker)
        if idx == -1:
            return base + "\n\n" + output_format
        return base[:idx] + output_format

    def _build_map_user_message(
        self,
        skill_state: dict[str, str],
        records: list[dict],
        batch_idx: int,
        total_batches: int,
    ) -> str:
        return build_combined_user_message(
            skill_state,
            records,
            batch_idx,
            total_batches,
            self.max_skill_lines,
            self.max_references,
        )

    def _build_map_user_message_from_patterns(
        self,
        skill_state: dict[str, str],
        patterns: dict[str, list[dict]],
        batch_idx: int,
        total_batches: int,
    ) -> str:
        return build_combined_patterns_user_message(
            skill_state,
            patterns,
            batch_idx,
            total_batches,
            self.max_skill_lines,
            self.max_references,
        )

    def _run_single_map_patterns(self, skill_state, pattern_batch, batch_idx, total_batches):
        user_msg = self._build_map_user_message_from_patterns(
            skill_state, pattern_batch, batch_idx, total_batches
        )
        response, conversation_trace = self._call_llm(
            self._map_patterns_system_prompt, user_msg, tag="patch"
        )
        self._save_prompt_response(
            "map", f"batch_{batch_idx:04d}", self._map_patterns_system_prompt, user_msg, response
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

    def _run_single_map_patterns_markdown(self, skill_state, pattern_batch, batch_idx, total_batches):
        user_msg = self._build_map_user_message_from_patterns(
            skill_state, pattern_batch, batch_idx, total_batches
        )
        response, conversation_trace = self._call_llm(
            self._map_patterns_system_prompt, user_msg, tag="patch", expect_semantic=True
        )
        self._save_prompt_response(
            "map", f"batch_{batch_idx:04d}", self._map_patterns_system_prompt, user_msg, response
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

    def _build_merge_user_message(
        self,
        skill_state: dict[str, str],
        patches: list[Patch],
    ) -> str:
        return _build_combined_merge_user_message(skill_state, patches)

    def _build_merge_user_message_semantic(
        self,
        skill_state: dict[str, str],
        patches: list[SemanticPatch],
    ) -> str:
        return _build_combined_merge_user_message_semantic(
            skill_state,
            patches,
            marker_format=self.semantic_item_marker_format,
        )

    def _build_combined_markdown_merge_system_prompt(self) -> str:
        if self.semantic_item_marker_format == "heading":
            return COMBINED_MARKDOWN_MERGE_SYSTEM_PROMPT.replace(
                "[ITEM_1_START]",
                "### Item 1",
            ).replace("\n\nRules:\n- Each item must start with [ITEM_X_START]", "")
        return COMBINED_MARKDOWN_MERGE_SYSTEM_PROMPT
