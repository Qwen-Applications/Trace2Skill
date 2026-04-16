"""
CLI Skill Preloaded Agent - Bash CLI agent with skill content pre-loaded in context.

Unlike CLISkillAgent which discovers skills and instructs the agent to read them
on demand via bash, this agent reads all skill content at initialization and embeds
it directly into the system prompt. The agent does not need to decide whether or
when to read a skill file — the full guidance is already available in context.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from react_agent import Tool

from .base import BaseSpreadsheetAgent
from ..tools import create_bash_tool
from .cli_skill_agent import SKILLS_DIR, SkillMetadata, discover_skills
from ..system_prompts import render_full_system_prompt


def read_skill_content(skill: SkillMetadata) -> str:
    """Read the full content of a skill file, stripping YAML frontmatter."""
    try:
        with open(skill.file_path, "r") as f:
            content = f.read()

        # Strip YAML frontmatter (already parsed into metadata)
        if content.startswith("---"):
            end = content.index("---", 3)
            content = content[end + 3:].lstrip("\n")

        return content
    except Exception:
        return ""


def render_preloaded_skills_section(skills: list[tuple[SkillMetadata, str]], skills_dir: str) -> str:
    """
    Render the skills section with full skill content embedded.

    Args:
        skills: List of (metadata, content) tuples for each loaded skill.
        skills_dir: Absolute path to the skills directory.
    """
    if not skills:
        return ""

    lines = [
        "## Skills",
        "",
        "The following skills have been loaded for this session. Their full guidance is included below.",
        "",
    ]

    for metadata, content in skills:
        lines.extend([
            f"### {metadata.name}",
            "",
            metadata.description,
            "",
            "---",
            "",
            content,
            "",
            "---",
            "",
        ])

    lines.extend([
        "### Skill Usage Rules",
        "",
        "**CRITICAL RULE**: If a skill above is relevant to your task and contains useful guidance",
        "for the operation you need to perform, you MUST follow the skill's instructions. Only act",
        "on your own judgment if:",
        "- No skill is relevant to the task, OR",
        "- The skill does not cover the specific operation you need to perform",
        "",
        "**Skill Authority**: When a skill has guidance for your operation, its instructions take",
        "precedence over your general knowledge.",
        "",
        f"**Resources**: Scripts and other resources referenced in a skill are located in the skill's directory under `{skills_dir}`. Use the full path when running them (e.g., `python {skills_dir}/xlsx/recalc.py`).",
        "",
    ])

    return "\n".join(lines)


SPREADSHEET_SKILL_PRELOADED_CONTEXT = """You have a **bash** action to execute shell commands. Use it to run Python code.

{skills_section}"""


SPREADSHEET_SKILL_PRELOADED_EXAMPLES = """## Recommended Workflow

1. **Analyze**: Read the instruction and spreadsheet_content to understand what needs to be done
2. **Apply Skill Guidance**: Review the skill content loaded above. If any skill covers your operation, follow its guidance
3. **Execute**: Write and run Python code following the skill's guidance when applicable
4. **Verify**: Check that the output file was created at the exact output_path
5. **Complete**: Signal task completion with ACTION: TASK_COMPLETE

**IMPORTANT**: Skill guidance is already loaded in your context above. Follow it when it covers your operation. Only use your own approach when no loaded skill covers your specific task.

## Action Examples

### Execute Python code (following loaded skill guidance when applicable):

Action:
{{
    "name": "bash",
    "arguments": {{"command": "python -c \"import openpyxl; wb = openpyxl.load_workbook('/path/to/input.xlsx'); ws = wb.active; ws['D2'] = '=SUM(B2:C2)'; wb.save('/path/to/output.xlsx'); print('Done')\""}}
}}

### Write and execute a solution script:

Action:
{{
    "name": "bash",
    "arguments": {{"command": "cat <<'EOF' > solution.py\\nimport openpyxl\\nwb = openpyxl.load_workbook('/path/to/input.xlsx')\\nws = wb.active\\n# Your manipulation logic here\\nwb.save('/path/to/output.xlsx')\\nprint('Saved')\\nEOF\\npython solution.py"}}
}}

### Verify output file:

Action:
{{
    "name": "bash",
    "arguments": {{"command": "ls -la /path/to/output.xlsx"}}
}}

### Signal task completion:

When you have successfully created the output file:

ACTION: TASK_COMPLETE

Note: The above examples are just reference actions for inspiration. You should adapt your actions based on context and take any action that you deem appropriate.

Action:
{{
    "name": "bash",
    "arguments": {{"command": "# Any other command you deem appropriate"}}
}}"""


class CLISkillPreloadedAgent(BaseSpreadsheetAgent):
    """
    CLI agent with skill content pre-loaded in the system prompt.

    Unlike CLISkillAgent which lists available skills and instructs the agent to
    read them on demand via bash, this agent reads all skill content at
    initialization and embeds it directly into the context. The agent follows
    skill guidance without needing to decide whether or when to read a skill file.

    Actions:
    - bash: Shell command execution

    Skills:
    - Discovered and fully loaded at initialization
    - Content embedded in system prompt, no runtime file reads needed
    """

    def __init__(
        self,
        client,
        skills_dir: str | None = None,
        max_turns: int = 20,
        temperature: float = 0.0,
        verbose: bool = True,
        timeout: int = 120,
        log_dir: str | None = None,
        log_format: str = "markdown",
    ):
        super().__init__(client, max_turns, temperature, verbose, log_dir, log_format)
        self.timeout = timeout
        self.skills_dir = os.path.abspath(skills_dir if skills_dir is not None else SKILLS_DIR)

        # Discover skills and load their full content at initialization
        metadata_list = discover_skills(self.skills_dir)
        if not metadata_list:
            raise ValueError(
                f"No skills discovered in skills_dir: {self.skills_dir}"
            )
        self._skills: list[tuple[SkillMetadata, str]] = [
            (meta, read_skill_content(meta)) for meta in metadata_list
        ]

    @property
    def name(self) -> str:
        return "cli_skill_preloaded_agent"

    def get_system_prompt(self) -> str:
        """Legacy method - kept for backward compatibility."""
        skills_section = render_preloaded_skills_section(self._skills, self.skills_dir)
        return SPREADSHEET_SKILL_PRELOADED_CONTEXT.format(skills_section=skills_section)

    def get_system_template(self) -> str:
        # Load skill content and directory for the v1 template
        if self._skills:
            metadata, content = self._skills[0]
            skill_content = content
            skill_dir = os.path.dirname(os.path.abspath(metadata.file_path))
        else:
            skill_content = "(No skill loaded)"
            skill_dir = self.skills_dir

        return render_full_system_prompt(
            "cli_skill_preloaded_full_system_v1.txt",
            skill_content=skill_content,
            skill_dir=skill_dir,
        )

    def create_tools(self, working_dir: str) -> list[Tool]:
        return [
            create_bash_tool(working_dir, timeout=self.timeout),
        ]

    def build_task_prompt(self, context) -> str:
        """Build task prompt with absolute paths.

        Unlike CLISkillAgent, this does not include a skills_directory field
        since skill content is already embedded in the system prompt.
        """
        working_dir = os.path.abspath(context.working_dir)
        input_file = os.path.abspath(context.input_file)
        output_file = os.path.abspath(context.output_file)

        return f"""Below is the spreadsheet manipulation question you need to solve:

### working_directory
{working_dir}

### instruction
{context.instruction}

### spreadsheet_path
{input_file}

### spreadsheet_content
{context.spreadsheet_content}

### instruction_type
{context.instruction_type}

### answer_position
{context.answer_position}

### output_path
{output_file}

---
**REMINDER**: Write files ONLY in `{working_dir}`. Save output to exact path: `{output_file}`
---

Solve the question and save the modified spreadsheet to the exact output_path shown above."""

    def get_available_skills(self) -> list[SkillMetadata]:
        """Get list of discovered skills with their loaded content."""
        return [meta for meta, _ in self._skills]
