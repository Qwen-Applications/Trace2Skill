#!/usr/bin/env python3
"""
Tolerant error analysis agent for failed spreadsheet tasks.

Like error_analysis_agent.py, but the agent's goal is to understand
and report the failure — not to fix it.  There is no PASS-gated
reminder: the agent writes its report and signals TASK_COMPLETE
without needing to produce a corrected output file.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from react_agent import ReActAgent, AgentConfig
from react_agent.models import ApiChatClient, OpenAIClient
from spreadsheet_agent.agents.base import ChatHistoryLogger
from spreadsheet_agent.tools.bash import create_bash_tool

# Re-use helpers that are format/tool-agnostic from the strict agent.
from analysis.error_analysis_agent import (
    create_evaluate_tool,
    sanitize_agent_log,
    build_evaluate_usage,
    format_user_prompt,
)


SCRIPT_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = SCRIPT_DIR / "error_analysis_system_tolerant.txt"
USER_PROMPT_PATH = SCRIPT_DIR / "error_analysis_user_tolerant.txt"


def format_system_prompt(working_directory: str) -> str:
    """Read the tolerant system prompt and resolve the {working_directory} placeholder."""
    template = SYSTEM_PROMPT_PATH.read_text()
    return template.format(working_directory=working_directory)


def format_user_prompt_tolerant(
    agent_log_text: str,
    working_dir: str,
    evaluate_usage: str,
) -> str:
    """Read the tolerant user prompt and substitute placeholders."""
    template = USER_PROMPT_PATH.read_text()
    result = template.replace("{agent_log}", agent_log_text)
    result = result.replace("{working_dir}", working_dir)
    result = result.replace("{evaluate_usage}", evaluate_usage)
    return result


def run_error_analysis_tolerant(
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
    verbose: bool = True,
) -> str:
    """
    Run the tolerant error analysis agent on a single instance.

    The agent diagnoses the failure and writes a report.  It does NOT
    need to produce a corrected spreadsheet or achieve a PASS evaluation.

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
        verbose: Whether to print agent debug output

    Returns:
        The analysis report text produced by the agent.
    """
    agent_log_text = sanitize_agent_log(agent_log_content)
    evaluate_usage = build_evaluate_usage(analysis_dir, answer_position)

    system_prompt = format_system_prompt(analysis_dir)
    user_prompt = format_user_prompt_tolerant(agent_log_text, analysis_dir, evaluate_usage)

    bash_tool = create_bash_tool(working_dir=analysis_dir)
    # evaluate_output is still provided so the agent can inspect discrepancies;
    # we do not track whether PASS is achieved.
    evaluate_tool = create_evaluate_tool(working_dir=analysis_dir)

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

    logger = ChatHistoryLogger(
        log_dir=analysis_dir,
        format="markdown",
        log_filename="error_analysis_chat.md",
    )
    logger.start_session("error_analysis_agent_tolerant", user_prompt)
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

        if result.error == "Max turns exceeded":
            # Turn budget exhausted mid-action: the last logged entry is a USER
            # observation with no following ASSISTANT synthesis.  Grant one extra
            # turn solely for writing the final analysis report.
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
