#!/usr/bin/env python3
"""
Error analysis agent for failed spreadsheet tasks.

Uses a ReAct agent with a bash tool to inspect agent logs, working directories,
produced outputs, and ground-truth spreadsheets to diagnose failures.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from react_agent import ReActAgent, AgentConfig
from react_agent.models import ApiChatClient, OpenAIClient
from react_agent.tools import tool
from spreadsheet_agent.agents.base import ChatHistoryLogger
from spreadsheet_agent.tools.bash import create_bash_tool


SCRIPT_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = SCRIPT_DIR / "error_analysis_system.txt"
USER_PROMPT_PATH = SCRIPT_DIR / "error_analysis_user.txt"
EVALUATE_SCRIPT = SCRIPT_DIR / "evaluate_output.py"


def create_evaluate_tool(
    working_dir: str,
    pass_flag: dict[str, bool] | None = None,
    pass_flag_path: Path | None = None,
):
    """
    Create an evaluate_output tool that runs the evaluation script.

    The tool invokes ``analysis/evaluate_output.py`` as a subprocess so the
    agent cannot modify the script itself.
    """
    script_path = str(EVALUATE_SCRIPT)

    @tool(name="evaluate_output")
    def evaluate_output(output_file: str, ground_truth: str, answer_position: str = "") -> str:
        """
        Compare an output spreadsheet against the ground-truth spreadsheet.
        Returns a detailed evaluation report showing PASS/FAIL per range
        and cell-level mismatches.

        Args:
            output_file: Path to the agent's output .xlsx file
            ground_truth: Path to the ground-truth .xlsx file
            answer_position: Cell range(s) to compare, e.g. "Sheet1!A1:B10" or "A1:C5". Leave empty to compare all cells.
        """
        cmd = [
            sys.executable, script_path,
            "--output_file", output_file,
            "--ground_truth", ground_truth,
        ]
        if answer_position:
            cmd += ["--answer_position", answer_position]
        try:
            result = subprocess.run(
                cmd,
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += f"\n[STDERR]\n{result.stderr}" if output else result.stderr
            output = output.strip() if output.strip() else "[Command completed with no output]"
            if pass_flag is not None:
                passed = bool(re.search(r"^Result:\s+PASS\b", output, re.MULTILINE))
                if passed:
                    pass_flag["passed"] = True
                    if pass_flag_path is not None:
                        pass_flag_path.write_text("PASS\n", encoding="utf-8")
            return output
        except subprocess.TimeoutExpired:
            return "[ERROR] Evaluation timed out after 60 seconds"
        except Exception as e:
            return f"[ERROR] Failed to run evaluation: {e}"

    return evaluate_output


def format_system_prompt(working_directory: str, system_prompt_path: Path = SYSTEM_PROMPT_PATH) -> str:
    """
    Read the system prompt template and resolve placeholders.

    The template uses {working_directory} for the path and {{ / }} for
    literal braces in JSON examples.  A single .format() call handles both.
    """
    template = system_prompt_path.read_text()
    return template.format(working_directory=working_directory)

def sanitize_agent_log(raw_log: str) -> str:
    """
    Remove markdown logger header/trailer sections from the agent log.

    Strips:
    - Initial header block (title/timestamp/separator) if present
    - Trailing "## RESULT" section and everything after it
    - Trailing markdown separators
    """
    text = raw_log.lstrip()

    # Remove common markdown logger header if present.
    # Matches: "# Chat History ...\n\n**Timestamp**: ...\n\n---\n\n"
    header_pattern = re.compile(
        r"^# Chat History[^\n]*\n\n\*\*Timestamp\*\*:[^\n]*\n\n---\n\n",
        re.MULTILINE,
    )
    text = header_pattern.sub("", text)

    # Drop trailing RESULT section (and anything after).
    result_index = text.find("## RESULT")
    if result_index != -1:
        text = text[:result_index].rstrip()

    # Trim trailing separators
    text = re.sub(r"\n---\s*$", "", text).rstrip()
    return text


def format_user_prompt(
    agent_log_text: str,
    working_dir: str,
    evaluate_usage: str,
) -> str:
    """
    Read the user prompt template and substitute placeholders.

    Args:
        agent_log_text: The raw text of the agent execution log.
        working_dir: The analysis working directory path.
        evaluate_usage: Instructions for using the evaluate_output tool,
            with or without --answer_position depending on the instance.

    Uses .replace() instead of .format() because the JSON content may
    contain curly braces that would break str.format().
    """
    template = USER_PROMPT_PATH.read_text()
    result = template.replace("{agent_log}", agent_log_text)
    result = result.replace("{working_dir}", working_dir)
    result = result.replace("{evaluate_usage}", evaluate_usage)
    return result


def build_evaluate_usage(working_dir: str, answer_position: str | None) -> str:
    """Build the evaluate_output usage instructions for the user prompt."""
    agent_work = f"{working_dir}/agent_work"
    output_path = f"{agent_work}/output.xlsx"
    gold_path = f"{agent_work}/gold.xlsx"

    if answer_position:
        return (
            f'To run the evaluation, use the `evaluate_output` tool with the known answer position:\n'
            f'\n'
            f'Action:\n'
            f'{{\n'
            f'    "name": "evaluate_output",\n'
            f'    "arguments": {{\n'
            f'        "output_file": "{output_path}",\n'
            f'        "ground_truth": "{gold_path}",\n'
            f'        "answer_position": "{answer_position}"\n'
            f'    }}\n'
            f'}}\n'
            f'\n'
            f'The answer position `{answer_position}` specifies the exact cell range(s) that are graded.\n'
        )
    else:
        return (
            f'To run the evaluation, use the `evaluate_output` tool:\n'
            f'\n'
            f'Action:\n'
            f'{{\n'
            f'    "name": "evaluate_output",\n'
            f'    "arguments": {{\n'
            f'        "output_file": "{output_path}",\n'
            f'        "ground_truth": "{gold_path}"\n'
            f'    }}\n'
            f'}}\n'
        )


def run_error_analysis(
    analysis_dir: str,
    agent_log_content: str,
    model: str,
    answer_position: str | None = None,
    max_turns: int = 20,
    base_url: str | None = None,
    api_key: str | None = None,
    generation_config: dict | None = None,
    llm_client: str = "openai",
    api_chat_config: str = "config/llm_api.json",
    use_skill_prompt: bool = False,
    verbose: bool = True,
) -> str:
    """
    Run the error analysis agent on a single instance.

    Args:
        analysis_dir: Directory containing the analysis workspace (agent_work/, etc.)
        agent_log_content: The full text of the agent's execution log
        model: Model name (OpenAI-compatible)
        answer_position: Cell range(s) for evaluation (e.g. "K6", "Sheet1!A1:B10").
            If None, the evaluate_output tool compares all cells.
        max_turns: Maximum agent turns
        base_url: OpenAI-compatible base URL
        api_key: API key
        generation_config: Optional generation config for the model client
        use_skill_prompt: If True, use the skill-based system prompt
        verbose: Whether to print agent debug output

    Returns:
        The analysis report text produced by the agent.
    """
    agent_log_text = sanitize_agent_log(agent_log_content)

    evaluate_usage = build_evaluate_usage(analysis_dir, answer_position)

    system_prompt_path = SYSTEM_PROMPT_PATH
    if use_skill_prompt:
        system_prompt_path = SCRIPT_DIR / "error_analysis_system_skill.txt"
    system_prompt = format_system_prompt(analysis_dir, system_prompt_path)
    user_prompt = format_user_prompt(agent_log_text, analysis_dir, evaluate_usage)

    bash_tool = create_bash_tool(working_dir=analysis_dir)
    eval_flag: dict[str, bool] = {"passed": False}
    pass_flag_path = Path(analysis_dir) / "evaluate_passed.flag"
    evaluate_tool = create_evaluate_tool(
        working_dir=analysis_dir,
        pass_flag=eval_flag,
        pass_flag_path=pass_flag_path,
    )

    if llm_client == "api_chat":
        client = ApiChatClient(
            model=model,
            config_path=api_chat_config,
            generation_config=generation_config,
        )
    else:
        client = OpenAIClient(
            model=model,
            api_key=api_key or os.getenv("OPENAI_API_KEY") or "EMPTY",
            base_url=base_url or os.getenv("OPENAI_BASE_URL"),
            generation_config=generation_config,
        )

    # Set up chat history logger
    logger = ChatHistoryLogger(
        log_dir=analysis_dir,
        format="markdown",
        log_filename="error_analysis_chat.md",
    )
    logger.start_session("error_analysis_agent", user_prompt)
    logger.log_system_prompt(system_prompt)
    logger.log_user_task(f"Task: {user_prompt}")

    def on_step(step):
        logger.log_step(step)

    agent = ReActAgent(
        client=client,
        tools=[bash_tool, evaluate_tool],
        config=AgentConfig(
            max_turns=max_turns,
            system_template=system_prompt,
            verbose=verbose,
        ),
        on_step=on_step,
    )

    async def _run_agent():
        result = await agent.run_async(user_prompt)

        if not eval_flag["passed"]:
            remaining_turns = max_turns - result.total_turns
            if remaining_turns > 0:
                # Agent signalled TASK_COMPLETE early but never achieved a PASS
                # evaluation — remind it to keep fixing.
                reminder = (
                    "[System Check] You have not yet produced a PASS evaluation. "
                    "Run evaluate_output on output.xlsx (or output_fixed.xlsx) and "
                    "continue fixing the errors until the report shows PASS, then "
                    "signal ACTION: TASK_COMPLETE."
                )
                if logger:
                    logger.log_user_task(reminder)
                result = await agent.continue_with_message_async(reminder)
            elif result.error == "Max turns exceeded":
                # Turn budget exhausted mid-action: the last logged entry is a
                # USER observation with no following ASSISTANT synthesis.  Grant
                # one extra turn solely for writing the final analysis report.
                # The last message is already USER, so we must NOT inject another
                # USER message — use continue_from_last_user_async instead.
                synthesis_msg = (
                    "[System Check] Turn budget exhausted. Do NOT call any more tools. "
                    "Based on your investigation so far, write your final analysis "
                    "report now (Failure Cause Items and Failure Memory Items) and "
                    "signal ACTION: TASK_COMPLETE."
                )
                if logger:
                    logger.log_user_task(synthesis_msg)
                agent.config.max_turns = max_turns + 1
                result = await agent.continue_from_last_user_async(synthesis_msg)
                agent.config.max_turns = max_turns  # restore

        return result

    result = asyncio.run(_run_agent())

    logger.log_result(
        success=result.success,
        answer=result.final_answer,
        turns=result.total_turns,
        error=result.error,
    )

    return result.final_answer
