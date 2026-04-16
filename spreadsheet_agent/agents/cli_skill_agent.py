"""
CLI Skill Agent - Bash CLI agent with access to skill documentation.

This agent has the bash tool for command execution, plus knowledge of
skills (local instruction files stored as SKILL.md) that provide detailed
guidance for specific tasks.
"""

import os
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from react_agent import Tool

from .base import BaseSpreadsheetAgent
from ..tools import create_bash_tool
from ..system_prompts import render_full_system_prompt


# Get the absolute path to the skills directory
SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "skills")


@dataclass
class SkillMetadata:
    """Metadata for a skill extracted from SKILL.md frontmatter."""
    name: str
    description: str
    file_path: str


def discover_skills(skills_dir: str) -> list[SkillMetadata]:
    """
    Discover available skills in the skills directory.

    Skills are directories containing a SKILL.md file with YAML frontmatter
    that includes name and description fields.
    """
    skills = []

    if not os.path.exists(skills_dir):
        return skills

    for entry in sorted(os.listdir(skills_dir)):
        skill_dir = os.path.join(skills_dir, entry)
        skill_file = os.path.join(skill_dir, "SKILL.md")

        if os.path.isdir(skill_dir) and os.path.exists(skill_file):
            metadata = extract_skill_metadata(skill_file)
            if metadata:
                skills.append(metadata)

    return skills


def extract_skill_metadata(skill_file: str) -> SkillMetadata | None:
    """
    Extract skill metadata from SKILL.md frontmatter.

    Expected format:
    ---
    name: skill_name
    description: "Skill description"
    ---
    """
    try:
        with open(skill_file, "r") as f:
            content = f.read()

        # Extract YAML frontmatter
        frontmatter_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if not frontmatter_match:
            return None

        frontmatter = frontmatter_match.group(1)

        # Parse name and description
        name_match = re.search(r'^name:\s*["\']?([^"\'\n]+)["\']?\s*$', frontmatter, re.MULTILINE)
        desc_match = re.search(r'^description:\s*["\']?([^"\'\n]+)["\']?\s*$', frontmatter, re.MULTILINE)

        if name_match:
            name = name_match.group(1).strip()
            description = desc_match.group(1).strip() if desc_match else ""
            return SkillMetadata(
                name=name,
                description=description,
                file_path=skill_file,
            )

    except Exception:
        pass

    return None


def render_skills_section(skills: list[SkillMetadata], skills_dir: str) -> str:
    """
    Render the skills section for the system prompt.

    Follows the Codex pattern for skill documentation.
    """
    if not skills:
        return ""

    lines = [
        "## Skills",
        "",
        "Skills are local instructions stored in SKILL.md files that provide detailed guidance for specific tasks.",
        "",
        "### Available Skills",
        "",
    ]

    # List each skill
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description} (file: `{skill.file_path}`)")

    lines.extend([
        "",
        "### Skill Usage Rules",
        "",
        "**Discovery**: The skills listed above are available in this session.",
        "",
        "**Workflow**:",
        "1. First analyze the task from the instruction and spreadsheet_content",
        "2. Evaluate if any available skill is relevant to your task",
        "3. If relevant, read the SKILL.md to find guidance for your operation",
        "4. Execute your solution following the skill's guidance when applicable",
        "",
        "**CRITICAL RULE**: If a skill is relevant to your task and contains useful guidance for the operation you need to perform, you MUST follow the skill's instructions. Only act on your own judgment if:",
        "- No skill is relevant to the task, OR",
        "- The skill does not cover the specific operation you need to perform",
        "",
        "**Skill Authority**: When a skill has guidance for your operation, its instructions take precedence over your general knowledge.",
        "",
        "**Reading Skills**: Read the full SKILL.md file using `cat <skill_file>`. The file is small enough to read in full.",
        "",
        f"**Resources**: Scripts and other resources referenced in a skill are located in the skill's directory under `{skills_dir}`. Use the full path when running them (e.g., `python {skills_dir}/xlsx/recalc.py`).",
        "",
        "**Error Handling**: If a skill file is missing or inaccessible, acknowledge and continue with general knowledge.",
        "",
    ])

    return "\n".join(lines)


# Legacy system prompt kept for backward compatibility
CLI_SKILL_SYSTEM_PROMPT = """You are a spreadsheet expert who can manipulate spreadsheets through Python code.

## Task

You need to solve a spreadsheet manipulation question with the following information:
- working_directory: The absolute path to your working directory where files are located.
- skills_directory: The absolute path to the skills directory containing SKILL.md files.
- instruction: The question about spreadsheet manipulation.
- spreadsheet_path: The absolute path of the spreadsheet file you need to manipulate.
- spreadsheet_content: The first few rows of the content of spreadsheet file.
- instruction_type: Cell-Level Manipulation (specific cells) or Sheet-Level Manipulation (entire worksheet).
- answer_position: The cell(s) to modify or fill.
- output_path: The absolute path where you must save the modified spreadsheet.

## CRITICAL RESTRICTIONS

You can ONLY read and write files within the **working_directory**. The skills_directory is READ-ONLY.

- **Write allowed**: working_directory only
- **Read allowed**: working_directory, skills_directory
- **Read from**: spreadsheet_path (inside working_directory)
- **Write to**: output_path (inside working_directory)

Do NOT create or modify files outside the working_directory. Use the exact absolute paths provided.

Your goal is to produce the modified spreadsheet at output_path.

## Workflow

You have a **bash** action to execute shell commands. Use it to run Python code and read skill files.

{skills_section}

### Recommended Steps

1. Analyze the spreadsheet_content and instruction to understand the task
2. Evaluate if any available skill is relevant to your task
3. If relevant, read the SKILL.md for guidance on your operation
4. Write and execute Python code to perform the manipulation (following skill guidance when applicable)
5. Verify the output file was created successfully
6. Signal completion with ACTION: TASK_COMPLETE

**IMPORTANT**: If a skill is relevant and has useful content for your operation, you MUST follow the skill's guidance. Only use your own approach when the skill does not cover your specific operation.
"""


class CLISkillAgent(BaseSpreadsheetAgent):
    """
    CLI agent with access to skill documentation.

    Skills are local instruction files (SKILL.md) that provide detailed
    guidance for specific tasks like spreadsheet manipulation.

    Features:
    - Discovers skills from skills directory
    - Presents skills with usage guidelines
    - Progressive disclosure for reading skill content

    Actions:
    - bash: Shell command execution

    Skills:
    - Discovered from skills_dir at initialization
    - Each skill has name, description, and file path
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

        if skills_dir is None:
            self.skills_dir = os.path.abspath(SKILLS_DIR)
        else:
            self.skills_dir = os.path.abspath(skills_dir)

        # Discover skills at initialization
        self.skills = discover_skills(self.skills_dir)

    @property
    def name(self) -> str:
        return "cli_skill_agent"

    def get_system_prompt(self) -> str:
        """Legacy method - kept for backward compatibility."""
        skills_section = render_skills_section(self.skills, self.skills_dir)
        return CLI_SKILL_SYSTEM_PROMPT.format(skills_section=skills_section)

    def get_system_template(self) -> str:
        skills_section = render_skills_section(self.skills, self.skills_dir)
        return render_full_system_prompt(
            "cli_skill_full_system.txt",
            skills_section=skills_section,
            skills_dir=self.skills_dir,
        )

    def create_tools(self, working_dir: str) -> list[Tool]:
        return [
            create_bash_tool(working_dir, timeout=self.timeout),
        ]

    def get_no_truncate_patterns(self) -> list[str]:
        """Return paths that should not be truncated - includes skills directory."""
        return [self.skills_dir]

    def build_task_prompt(self, context) -> str:
        """Build task prompt with absolute paths including skills directory."""
        # Convert to absolute paths so agent knows exact locations
        working_dir = os.path.abspath(context.working_dir)
        input_file = os.path.abspath(context.input_file)
        output_file = os.path.abspath(context.output_file)
        
        return f"""Below is the spreadsheet manipulation question you need to solve:

### working_directory
{working_dir}

### skills_directory (READ-ONLY)
{self.skills_dir}

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
**REMINDER**: Write files ONLY in `{working_dir}`. Skills directory is READ-ONLY. Save output to exact path: `{output_file}`
---

Solve the question and save the modified spreadsheet to the exact output_path shown above."""

    def get_available_skills(self) -> list[SkillMetadata]:
        """Get list of discovered skills."""
        return self.skills.copy()
