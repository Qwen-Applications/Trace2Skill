from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).with_name("prompts")

_STRIP_FINAL_NEWLINE_KEYS = {
    "parallel_evolving_agent/apply_all_edits_instruction",
    "parallel_evolving_agent/apply_constraints",
    "parallel_evolving_agent/apply_system_prompt_template",
    "parallel_evolving_agent/continue_json_prompt",
    "parallel_evolving_agent/continue_semantic_prompt",
    "parallel_evolving_agent/json_format_fix_prompt",
    "parallel_evolving_agent/map_output_format",
    "parallel_evolving_agent/markdown_format_fix_example",
    "parallel_evolving_agent/markdown_format_fix_prompt",
    "parallel_evolving_agent/markdown_format_fix_prompt_heading",
    "parallel_evolving_agent/markdown_format_fix_example_heading",
    "parallel_evolving_agent/markdown_map_output_format",
    "parallel_evolving_agent/markdown_map_output_format_heading",
    "parallel_evolving_agent/markdown_merge_system_prompt",
    "parallel_evolving_agent/markdown_merge_system_prompt_heading",
    "parallel_evolving_agent/markdown_translation_system_prompt",
    "parallel_evolving_agent/merge_system_prompt",
    "parallel_evolving_agent/reference_files_status_line",
    "parallel_evolving_agent/skill_folder_size_status_header",
    "parallel_evolving_agent/skill_md_status_line",
    "parallel_evolving_agent/translate_edits_instruction",
    "parallel_evolving_agent/translate_semantic_instruction",
    "parallel_evolving_agent/translation_system_prompt",
    "parallel_evolving_agent/verification_instruction",
    "parallel_evolving_agent/verification_system_prompt",
    "skill_evolving_agent/changes_made_so_far_header",
    "skill_evolving_agent/error_record_section_generic",
    "skill_evolving_agent/error_record_section_patterns",
    "skill_evolving_agent/error_record_section_patterns_generic",
    "skill_evolving_agent/error_record_section_skill",
    "skill_evolving_agent/json_retry_prompt",
    "skill_evolving_agent/reference_files_status_line",
    "skill_evolving_agent/size_warning",
    "skill_evolving_agent/skill_folder_size_status_header",
    "skill_evolving_agent/skill_md_status_line",
    "success_evolving_agent/combined_analysis_records_header",
    "success_evolving_agent/reference_files_status_line",
    "success_evolving_agent/size_warning",
    "success_evolving_agent/skill_folder_size_status_header",
    "success_evolving_agent/skill_md_status_line",
    "success_evolving_agent/success_analysis_records_header",
}


def prompt_template_path(key: str) -> Path:
    return PROMPTS_DIR / f"{key}.txt"


@lru_cache(maxsize=None)
def load_prompt_template(key: str) -> str:
    path = prompt_template_path(key)
    if not path.is_file():
        raise KeyError(f"Unknown prompt template: {key}")
    text = path.read_text(encoding="utf-8")
    if key in _STRIP_FINAL_NEWLINE_KEYS and text.endswith("\n"):
        text = text[:-1]
    return text
