"""
CLI-Only Agent - Bash-only agent for spreadsheet manipulation.

This agent has access to only the bash tool, which is sufficient for:
- File system navigation (ls, cd, find, etc.)
- Executing Python scripts (python script.py or python -c "...")
- Reading Excel files (via Python one-liners or scripts)
- Any other shell operations
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from react_agent import Tool

from .base import BaseSpreadsheetAgent
from ..tools import create_bash_tool
from ..system_prompts import load_full_system_prompt


# Legacy system prompt kept for backward compatibility
CLI_ONLY_SYSTEM_PROMPT = """You are a spreadsheet expert who can manipulate spreadsheets through Python code.

You need to solve the given spreadsheet manipulation question, which contains the following information:
- working_directory: The absolute path to your working directory where files are located.
- instruction: The question about spreadsheet manipulation.
- spreadsheet_path: The absolute path of the spreadsheet file you need to manipulate.
- spreadsheet_content: The first few rows of the content of spreadsheet file.
- instruction_type: There are two values (Cell-Level Manipulation, Sheet-Level Manipulation) used to indicate whether the answer to this question applies only to specific cells or to the entire worksheet.
- answer_position: The position need to be modified or filled. For Cell-Level Manipulation questions, this field is filled with the cell position; for Sheet-Level Manipulation, it is the maximum range of cells you need to modify. You only need to modify or fill in values within the cell range specified by answer_position.
- output_path: The absolute path where you must save the modified spreadsheet.

## CRITICAL RESTRICTIONS

You can ONLY read and write files within the **working_directory**. Any attempt to access files outside this directory will fail.

- **Allowed paths**: working_directory (and its subdirectories)
- **Read from**: spreadsheet_path (inside working_directory)
- **Write to**: output_path (inside working_directory)

Do NOT create files outside the working_directory. Use the exact absolute paths provided.

You have access to a bash tool that can execute any shell command.

## Recommended Workflow

1. Analyze the spreadsheet_content and instruction to understand the task
2. If needed, explore the spreadsheet further to understand its structure
3. Write and execute Python code to perform the manipulation
4. Verify the output file was created successfully

## Action Examples

### Explore spreadsheet structure:

Action:
{
    "name": "bash",
    "arguments": {"command": "python -c \"import openpyxl; wb = openpyxl.load_workbook('/absolute/path/to/input.xlsx', data_only=True); [print(f'{s}: {wb[s].dimensions}, {wb[s].max_row} rows, {wb[s].max_column} cols') for s in wb.sheetnames]\""}
}

### Read specific cells or ranges:

Action:
{
    "name": "bash",
    "arguments": {"command": "python -c \"import openpyxl; wb = openpyxl.load_workbook('/absolute/path/to/input.xlsx'); ws = wb.active; print('A1:', ws['A1'].value); print('Row 1:', [c.value for c in ws[1]])\""}
}

### Write and execute a solution script:

Action:
{
    "name": "bash",
    "arguments": {"command": "cat <<'EOF' > solution.py\nimport openpyxl\n\nwb = openpyxl.load_workbook('/absolute/path/to/input.xlsx')\nws = wb.active\n\n# Perform manipulation based on instruction\nws['A1'] = 'new_value'\n\nwb.save('/absolute/path/to/output.xlsx')\nprint('Saved successfully')\nEOF\npython solution.py"}
}

### Verify output:

Action:
{
    "name": "bash",
    "arguments": {"command": "ls -la /absolute/path/to/output.xlsx && python -c \"import openpyxl; wb = openpyxl.load_workbook('/absolute/path/to/output.xlsx'); print('OK:', wb.active.dimensions)\""}
}

### Signal task completion:

When you have successfully created the output file:

ACTION: TASK_COMPLETE

These are just examples - replace paths with the actual absolute paths from spreadsheet_path and output_path. Your goal is to produce the modified spreadsheet at the exact output_path, then signal completion with ACTION: TASK_COMPLETE.
"""


class CLIOnlyAgent(BaseSpreadsheetAgent):
    """
    CLI-only agent that uses bash for all operations.

    This minimal agent demonstrates that a single bash tool is sufficient
    for file navigation, Python execution, and spreadsheet manipulation.

    Tools:
    - bash: Shell command execution (only tool)
    """

    def __init__(
        self,
        client,
        max_turns: int = 20,  # More turns since bash operations are more granular
        temperature: float = 0.0,
        verbose: bool = True,
        timeout: int = 120,
        log_dir: str | None = None,
        log_format: str = "markdown",
    ):
        super().__init__(client, max_turns, temperature, verbose, log_dir, log_format)
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "cli_only_agent"

    def get_system_prompt(self) -> str:
        """Legacy method - kept for backward compatibility."""
        return CLI_ONLY_SYSTEM_PROMPT

    def get_system_template(self) -> str:
        return load_full_system_prompt("cli_only_full_system_v1.txt")

    def create_tools(self, working_dir: str) -> list[Tool]:
        return [
            create_bash_tool(working_dir, timeout=self.timeout),
        ]

    def build_task_prompt(self, context) -> str:
        """Build task prompt using official SpreadsheetBench format."""
        import os
        # Convert to absolute paths so agent knows exact locations
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
**REMINDER**: You can ONLY access files within `{working_dir}`. Save output to the exact path: `{output_file}`
---

Solve the question and save the modified spreadsheet to the exact output_path shown above."""
