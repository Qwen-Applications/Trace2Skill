"""
Spreadsheet domain-specific prompt content.

This module contains the role, context, and examples specific to spreadsheet
manipulation agents. These are used by the SystemPromptBuilder presets.
"""

# =============================================================================
# Spreadsheet Domain Content
# =============================================================================

SPREADSHEET_ROLE = """You are a spreadsheet expert who can manipulate spreadsheets through Python code."""

SPREADSHEET_TASK_INFO = """You need to solve the given spreadsheet manipulation question, which contains the following information:
- working_directory: The absolute path to your working directory where files are located.
- instruction: The question about spreadsheet manipulation.
- spreadsheet_path: The absolute path of the spreadsheet file you need to manipulate.
- spreadsheet_content: The first few rows of the content of spreadsheet file.
- instruction_type: There are two values (Cell-Level Manipulation, Sheet-Level Manipulation) used to indicate whether the answer to this question applies only to specific cells or to the entire worksheet.
- answer_position: The position need to be modified or filled. For Cell-Level Manipulation questions, this field is filled with the cell position; for Sheet-Level Manipulation, it is the maximum range of cells you need to modify. You only need to modify or fill in values within the cell range specified by answer_position.
- output_path: The absolute path where you must save the modified spreadsheet.

## CRITICAL RESTRICTIONS

You can ONLY read and write files within the allowed directories specified in your task context.

- **Input file**: Read from spreadsheet_path
- **Output file**: Write to output_path (use the EXACT path provided)
- **Temporary files**: Create only in working_directory

Do NOT access files outside the allowed directories. Always use absolute paths as provided."""

# Alias for backward compatibility
SPREADSHEET_DOMAIN_CONTEXT = SPREADSHEET_TASK_INFO

SPREADSHEET_CLI_DOMAIN_CONTEXT = f"""{SPREADSHEET_DOMAIN_CONTEXT}

You have access to a bash tool that can execute any shell command."""

SPREADSHEET_EXAMPLES = """## Recommended Workflow

1. **Analyze**: Read the instruction and spreadsheet_content to understand what needs to be done
2. **Execute**: Write and run Python code to perform the manipulation
3. **Verify**: Confirm the output file was created successfully
4. **Complete**: Signal task completion with ACTION: TASK_COMPLETE

## Action Examples

### Execute Python code to manipulate spreadsheet:

Action:
{
    "name": "python",
    "arguments": {"code": "import openpyxl\\nwb = openpyxl.load_workbook('/path/to/input.xlsx')\\nws = wb.active\\n# Sum column B values\\ntotal = sum(ws.cell(row=r, column=2).value for r in range(2, ws.max_row + 1) if isinstance(ws.cell(row=r, column=2).value, (int, float)))\\nws['B10'] = total\\nwb.save('/path/to/output.xlsx')\\nprint(f'Sum: {total}')"}
}

### Copy cells between locations:

Action:
{
    "name": "python",
    "arguments": {"code": "import openpyxl\\nwb = openpyxl.load_workbook('/path/to/input.xlsx')\\nws = wb.active\\nfor col in range(1, ws.max_column + 1):\\n    ws.cell(row=20, column=col).value = ws.cell(row=1, column=col).value\\nwb.save('/path/to/output.xlsx')\\nprint('Copied')"}
}

### Signal task completion:

When you have successfully created the output file:

ACTION: TASK_COMPLETE

Note: The above examples are just reference actions for inspiration. You should adapt your actions based on context and take any action that you deem appropriate.

Action:
{{
    "name": "bash",
    "arguments": {{"command": "# Any other command you deem appropriate"}}
}}"""

SPREADSHEET_CLI_EXAMPLES = """## Recommended Workflow

1. **Analyze**: Read the instruction and spreadsheet_content to understand what needs to be done
2. **Explore (if needed)**: For complex tasks, explore the spreadsheet structure first
3. **Execute**: Write and run Python code via bash to perform the manipulation
4. **Verify**: Check that the output file was created at the exact output_path
5. **Complete**: Signal task completion with ACTION: TASK_COMPLETE

## Action Examples

### Explore spreadsheet structure:

Action:
{
    "name": "bash",
    "arguments": {"command": "python -c \"import openpyxl; wb = openpyxl.load_workbook('/path/to/input.xlsx', data_only=True); [print(f'{s}: {wb[s].dimensions}, {wb[s].max_row} rows, {wb[s].max_column} cols') for s in wb.sheetnames]\""}
}

### Execute a Python one-liner:

Action:
{
    "name": "bash",
    "arguments": {"command": "python -c \"import openpyxl; wb = openpyxl.load_workbook('/path/to/input.xlsx'); ws = wb.active; total = sum(ws.cell(row=r, column=2).value for r in range(2, ws.max_row + 1) if isinstance(ws.cell(row=r, column=2).value, (int, float))); ws['B10'] = total; wb.save('/path/to/output.xlsx'); print(f'Sum: {total}')\""}
}

### Write and execute a solution script:

Action:
{
    "name": "bash",
    "arguments": {"command": "cat <<'EOF' > solution.py\nimport openpyxl\nwb = openpyxl.load_workbook('/path/to/input.xlsx')\nws = wb.active\nfor col in range(1, ws.max_column + 1):\n    ws.cell(row=20, column=col).value = ws.cell(row=1, column=col).value\nwb.save('/path/to/output.xlsx')\nprint('Done')\nEOF\npython solution.py"}
}

### Verify output file:

Action:
{
    "name": "bash",
    "arguments": {"command": "ls -la /path/to/output.xlsx"}
}

### Signal task completion:

When you have successfully created the output file:

ACTION: TASK_COMPLETE

Note: The above examples are just reference actions for inspiration. You should adapt your actions based on context and take any action that you deem appropriate.

Action:
{{
    "name": "bash",
    "arguments": {{"command": "# Any other command you deem appropriate"}}
}}"""


# =============================================================================
# Script-Making Agent Domain Context
# =============================================================================

SPREADSHEET_SCRIPT_MAKING_CONTEXT = """You have a **bash** action to execute shell commands. Use it to run Python code and scripts.

Additionally, you have a **script library** containing reusable Python scripts at:
  {scripts_dir}

### Available Scripts
{available_scripts}

### Script Library Rules

1. **Check existing scripts first** before writing new code. Never write new code if an existing script is useful
2. **Never modify existing scripts** - create a new version instead
3. **Versioning**: `script_name.py` → `script_name_v2.py` → `script_name_v3.py`
4. **Docstring required**: Purpose, Usage, Arguments
5. **Standalone**: Scripts run via `python script.py [args]`"""

# Alias for backward compatibility
SPREADSHEET_SCRIPT_MAKING_WORKFLOW = SPREADSHEET_SCRIPT_MAKING_CONTEXT


# =============================================================================
# Skill Agent Domain Context
# =============================================================================

SPREADSHEET_SKILL_CONTEXT = """You have a **bash** action to execute shell commands. Use it to run Python code and read skill files.

{skills_section}

### Skill Usage Rules

**Workflow**:
1. **Analyze**: First read the instruction and spreadsheet_content to understand the task
2. **Evaluate Relevance**: Determine if any available skill is relevant to your task
3. **Consult Skill (if relevant)**: If a skill is relevant, read the SKILL.md to find guidance for your task
4. **Execute**: Implement your solution following the skill's guidance when applicable

**CRITICAL RULE**: If a skill is relevant to your task and contains useful guidance for the operation you need to perform, you MUST follow the skill's instructions. Only act on your own judgment if:
- No skill is relevant to the task, OR
- The skill does not cover the specific operation you need to perform

**Skill Authority**: When a skill has guidance for your operation, its instructions take precedence over your general knowledge.

**Reading Skills**: Read the full SKILL.md file to understand all available guidance. The file is small enough to read in full.

**Error Handling**: If a skill file is missing, acknowledge this and continue with general knowledge."""

# Alias for backward compatibility
SPREADSHEET_SKILL_WORKFLOW = SPREADSHEET_SKILL_CONTEXT
SKILL_USAGE_GUIDELINES = ""  # Deprecated - now integrated into SPREADSHEET_SKILL_CONTEXT


# =============================================================================
# Workflow-Integrated Examples
# =============================================================================

SPREADSHEET_CLI_WORKFLOW_EXAMPLES = """## Recommended Workflow

1. **Analyze**: Read the instruction and spreadsheet_content to understand what needs to be done
2. **Explore (if needed)**: For complex tasks, explore the spreadsheet structure first (sheets, dimensions, data types)
3. **Execute**: Write and run Python code to perform the manipulation
4. **Verify**: Check that the output file was created at the exact output_path
5. **Complete**: Signal task completion with ACTION: TASK_COMPLETE

## Action Examples

### Explore spreadsheet structure:

Action:
{
    "name": "bash",
    "arguments": {"command": "python -c \"import openpyxl; wb = openpyxl.load_workbook('/path/to/input.xlsx', data_only=True); [print(f'{s}: {wb[s].dimensions}, {wb[s].max_row} rows, {wb[s].max_column} cols') for s in wb.sheetnames]\""}
}

### Read specific cells or ranges:

Action:
{
    "name": "bash",
    "arguments": {"command": "python -c \"import openpyxl; wb = openpyxl.load_workbook('/path/to/input.xlsx'); ws = wb.active; print('A1:', ws['A1'].value); print('Row 1:', [c.value for c in ws[1]])\""}
}

### Execute a Python one-liner:

Action:
{
    "name": "bash",
    "arguments": {"command": "python -c \"import openpyxl; wb = openpyxl.load_workbook('/path/to/input.xlsx'); ws = wb.active; ws['B10'] = sum(ws.cell(row=r, column=2).value for r in range(2, 6) if isinstance(ws.cell(row=r, column=2).value, (int, float))); wb.save('/path/to/output.xlsx'); print('Done')\""}
}

### Write and execute a solution script (for complex logic):

Action:
{
    "name": "bash",
    "arguments": {"command": "cat <<'EOF' > solution.py\\nimport openpyxl\\nwb = openpyxl.load_workbook('/path/to/input.xlsx')\\nws = wb.active\\n# Your manipulation logic here\\nwb.save('/path/to/output.xlsx')\\nprint('Saved successfully')\\nEOF\\npython solution.py"}
}

### Verify output file was created:

Action:
{
    "name": "bash",
    "arguments": {"command": "ls -la /path/to/output.xlsx && python -c \"import openpyxl; wb = openpyxl.load_workbook('/path/to/output.xlsx'); print('OK:', wb.active.dimensions)\""}
}

### Signal task completion:

When you have successfully created the output file:

ACTION: TASK_COMPLETE

Note: The above examples are just reference actions for inspiration. You should adapt your actions based on context and take any action that you deem appropriate.

Action:
{{
    "name": "bash",
    "arguments": {{"command": "# Any other command you deem appropriate"}}
}}"""


SPREADSHEET_SCRIPT_MAKING_EXAMPLES = """## Recommended Workflow

1. **Check existing scripts**: List and inspect scripts in the script_library to find reusable solutions
2. **Use or create**: If a suitable script exists, run it; otherwise, create a new script. Never create if an existing script is useful
3. **Script versioning**: Never modify existing scripts - create `script_v2.py` instead
4. **Execute**: Run the script with appropriate arguments
5. **Verify**: Check that the output file was created at the exact output_path
6. **Complete**: Signal task completion with ACTION: TASK_COMPLETE

## Action Examples

### List available scripts:

Action:
{{
    "name": "bash",
    "arguments": {{"command": "ls {scripts_dir}/*.py"}}
}}

### View a script's documentation:

Action:
{{
    "name": "bash",
    "arguments": {{"command": "head -20 {scripts_dir}/sum_column.py"}}
}}

### Run an existing script:

Action:
{{
    "name": "bash",
    "arguments": {{"command": "python {scripts_dir}/sum_column.py /path/to/input.xlsx /path/to/output.xlsx B B10"}}
}}

### Create a new script (with required docstring):

Action:
{{
    "name": "bash",
    "arguments": {{"command": "cat << 'EOF' > {scripts_dir}/highlight_negative.py\\n\\\"\\\"\\\"\\nPurpose: Highlight cells with negative values in red\\nUsage: python highlight_negative.py <input_file> <output_file>\\nArguments:\\n    input_file: Path to input spreadsheet\\n    output_file: Path to save output\\n\\\"\\\"\\\"\\nimport sys\\nimport openpyxl\\nfrom openpyxl.styles import PatternFill\\n\\ndef main():\\n    if len(sys.argv) < 3:\\n        print(__doc__)\\n        sys.exit(1)\\n    wb = openpyxl.load_workbook(sys.argv[1])\\n    ws = wb.active\\n    red_fill = PatternFill(start_color='FF0000', fill_type='solid')\\n    for row in ws.iter_rows():\\n        for cell in row:\\n            if isinstance(cell.value, (int, float)) and cell.value < 0:\\n                cell.fill = red_fill\\n    wb.save(sys.argv[2])\\n    print(f'Saved to {{sys.argv[2]}}')\\n\\nif __name__ == '__main__':\\n    main()\\nEOF"}}
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


SPREADSHEET_SKILL_EXAMPLES = """## Recommended Workflow

1. **Analyze**: Read the instruction and spreadsheet_content to understand what needs to be done
2. **Evaluate Skill Relevance**: Determine if any available skill applies to your task
3. **Consult Skill (if relevant)**: If the skill is relevant, read the SKILL.md for guidance
4. **Execute**: Write and run Python code - follow the skill's guidance if it covers your operation
5. **Verify**: Check that the output file was created at the exact output_path
6. **Complete**: Signal task completion with ACTION: TASK_COMPLETE

**IMPORTANT**: If a skill is relevant and has useful content for your operation, you MUST follow the skill's guidance. Only use your own approach when the skill does not cover your specific operation.

## Action Examples

### Analyze task and check if skill is relevant:

First understand the task from the instruction and spreadsheet_content. If your task involves spreadsheet operations (formulas, formatting, data analysis, etc.), the xlsx skill is likely relevant.

### Read skill file (when relevant to your task):

Action:
{{
    "name": "bash",
    "arguments": {{"command": "cat {skills_dir}/xlsx/SKILL.md"}}
}}

### Execute Python code (following skill guidance when applicable):

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
