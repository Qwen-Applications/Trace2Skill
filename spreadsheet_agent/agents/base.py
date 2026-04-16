"""
Base class for spreadsheet manipulation agents.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
import json
import os
import sys
from typing import Callable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from react_agent import ReActAgent, AgentConfig, AgentStep, LLMClient, Tool


@dataclass
class AgentContext:
    """Context information passed to the agent for each task."""
    working_dir: str
    input_file: str
    output_file: str
    instruction: str
    spreadsheet_content: str = ""
    instruction_type: str = ""
    answer_position: str = ""
    instance_id: str = ""  # Unique identifier for this instance (e.g., "13-1")


class ChatHistoryLogger:
    """
    Logs raw chat history (system, user, assistant messages) to a file.

    Supports multiple output formats:
    - markdown: Messages with role headers
    - jsonl: One JSON object per message
    """

    def __init__(
        self,
        log_dir: str = "logs",
        format: str = "markdown",
        log_filename: str | None = None,
    ):
        self.log_dir = log_dir
        self.format = format
        self.log_filename = log_filename
        self._current_file: str | None = None
        self._message_count = 0

        os.makedirs(log_dir, exist_ok=True)

    def start_session(self, agent_name: str, task: str, context: AgentContext | None = None):
        """Start a new logging session."""
        self._message_count = 0

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = "md" if self.format == "markdown" else "jsonl"
        
        if self.log_filename:
            filename = self.log_filename
        elif context and context.instance_id:
            # Use instance_id for filename (e.g., "cli_skill_agent_13-1.md")
            filename = f"{agent_name}_{context.instance_id}.{ext}"
        else:
            filename = f"{agent_name}_{timestamp}.{ext}"

        self._current_file = os.path.join(self.log_dir, filename)

        # Write header
        if self.format == "markdown":
            with open(self._current_file, "w") as f:
                f.write(f"# Chat History: {agent_name}\n\n")
                f.write(f"**Timestamp**: {datetime.now().isoformat()}\n\n")
                f.write(f"---\n\n")
        else:
            header = {
                "type": "session_start",
                "agent_name": agent_name,
                "timestamp": datetime.now().isoformat(),
            }
            with open(self._current_file, "w") as f:
                f.write(json.dumps(header) + "\n")

    def log_message(self, role: str, content: str):
        """Log a raw message."""
        if self._current_file is None:
            return

        self._message_count += 1

        if self.format == "markdown":
            with open(self._current_file, "a") as f:
                f.write(f"## [{self._message_count}] {role.upper()}\n\n")
                f.write(f"{content}\n\n")
                f.write(f"---\n\n")
        else:
            record = {
                "type": "message",
                "index": self._message_count,
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat(),
            }
            with open(self._current_file, "a") as f:
                f.write(json.dumps(record) + "\n")

    def log_step(self, step: AgentStep):
        """Log an agent step as raw messages."""
        if self._current_file is None:
            return

        # Log assistant response (thought or final answer)
        if step.thought:
            self.log_message("assistant", step.thought)

        # Log observation as user message (this is how ReAct formats it)
        if step.observation:
            if self.format == "markdown":
                # Wrap in a fenced code block to preserve whitespace alignment.
                # Without this, long lines wrap in viewers and ^^^ error markers
                # from Python tracebacks end up on the wrong visual line.
                fence = "```"
                while fence in step.observation:
                    fence += "`"
                self.log_message("user", f"Observation:\n{fence}\n{step.observation}\n{fence}")
            else:
                self.log_message("user", f"Observation: {step.observation}")

    def log_system_prompt(self, system_prompt: str):
        """Log the system prompt."""
        self.log_message("system", system_prompt)

    def log_user_task(self, task: str):
        """Log the initial user task."""
        self.log_message("user", task)

    def log_result(self, success: bool, answer: str, turns: int, error: str | None = None):
        """Log the final result summary."""
        if self._current_file is None:
            return

        if self.format == "markdown":
            with open(self._current_file, "a") as f:
                f.write(f"## RESULT\n\n")
                f.write(f"- Success: {success}\n")
                f.write(f"- Total Turns: {turns}\n")
                if error:
                    f.write(f"- Error: {error}\n")
        else:
            record = {
                "type": "result",
                "success": success,
                "total_turns": turns,
                "timestamp": datetime.now().isoformat(),
            }
            if error:
                record["error"] = error
            with open(self._current_file, "a") as f:
                f.write(json.dumps(record) + "\n")

    def get_log_file(self) -> str | None:
        """Get the current log file path."""
        return self._current_file


class BaseSpreadsheetAgent(ABC):
    """
    Base class for spreadsheet manipulation agents.

    Subclasses must implement:
    - get_system_prompt(): Returns the system instructions
    - create_tools(): Returns list of tools for the agent
    """

    def __init__(
        self,
        client: LLMClient,
        max_turns: int = 15,
        temperature: float = 0.0,
        verbose: bool = True,
        log_dir: str | None = None,
        log_format: str = "markdown",
    ):
        """
        Initialize the agent.

        Args:
            client: LLM client for generation
            max_turns: Maximum reasoning turns
            temperature: Generation temperature
            verbose: Whether to print debug output
            log_dir: Directory for chat history logs (None to disable logging)
            log_format: Log format ("markdown" or "jsonl")
        """
        self.client = client
        self.max_turns = max_turns
        self.temperature = temperature
        self.verbose = verbose
        self._working_dir: str | None = None
        self._agent: ReActAgent | None = None

        # Setup chat history logger
        if log_dir:
            self._logger = ChatHistoryLogger(
                log_dir=log_dir,
                format=log_format,
            )
        else:
            self._logger = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the agent's name."""
        pass

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this agent (legacy method)."""
        pass

    def get_system_template(self) -> str | None:
        """Return a full system prompt template for this agent."""
        return None

    @abstractmethod
    def create_tools(self, working_dir: str) -> list[Tool]:
        """
        Create and return the tools for this agent.

        Args:
            working_dir: The working directory for tool execution
        """
        pass

    def get_no_truncate_patterns(self) -> list[str]:
        """
        Return paths that should not have their output truncated.
        
        Override this method to specify paths (e.g., skill directories) where
        the AI should always see full file contents without truncation.
        
        Returns:
            List of path patterns that should not be truncated
        """
        return []

    def build_task_prompt(self, context: AgentContext) -> str:
        """
        Build the task prompt from context using official SpreadsheetBench format.

        Can be overridden by subclasses for custom formatting.
        """
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

Execute Python code to solve the question and save the modified spreadsheet to the exact output_path shown above."""

    def _create_step_callback(self) -> Callable[[AgentStep], None] | None:
        """Create a step callback that logs to the history logger."""
        if self._logger is None:
            return None

        def on_step(step: AgentStep):
            self._logger.log_step(step)

        return on_step

    def _ensure_agent(self, working_dir: str) -> ReActAgent:
        """Ensure the agent is created with the correct working directory."""
        if self._agent is None or self._working_dir != working_dir:
            self._working_dir = working_dir
            tools = self.create_tools(working_dir)

            system_template = self.get_system_template()

            # Get patterns that should not be truncated (e.g., skill files)
            no_truncate_patterns = self.get_no_truncate_patterns()

            if system_template:
                config = AgentConfig(
                    max_turns=self.max_turns,
                    verbose=self.verbose,
                    system_template=system_template,
                    no_truncate_patterns=no_truncate_patterns,
                )
            else:
                config = AgentConfig(
                    max_turns=self.max_turns,
                    verbose=self.verbose,
                    system_instructions=self.get_system_prompt(),
                    no_truncate_patterns=no_truncate_patterns,
                )

            self._agent = ReActAgent(
                client=self.client,
                tools=tools,
                config=config,
                on_step=self._create_step_callback(),
            )
        return self._agent

    def run(self, context: AgentContext) -> dict:
        """
        Run the agent on a task.

        Args:
            context: The task context with input/output paths and instructions

        Returns:
            Dictionary with 'success', 'answer', 'turns', and optionally 'error'
        """
        # Set environment variables
        os.environ["INPUT_FILE"] = context.input_file
        os.environ["OUTPUT_FILE"] = context.output_file

        # Build and run
        agent = self._ensure_agent(context.working_dir)
        task_prompt = self.build_task_prompt(context)

        # Start logging session and log initial messages
        if self._logger:
            self._logger.start_session(self.name, task_prompt, context)
            system_template = self.get_system_template()
            if system_template:
                full_system_prompt = agent.converter.build_system_prompt(
                    tools=agent.tool_registry.list_tools(),
                )
            else:
                full_system_prompt = agent.converter.build_system_prompt(
                    tools=agent.tool_registry.list_tools(),
                    extra_instructions=self.get_system_prompt(),
                )
            self._logger.log_system_prompt(full_system_prompt)
            # Log the user task
            self._logger.log_user_task(f"Task: {task_prompt}")

        try:
            result = agent.run(task_prompt)
            
            # Check if output file exists after agent signals completion
            output_exists = os.path.exists(context.output_file)
            
            # If agent succeeded but output doesn't exist, give it another chance
            if result.success and not output_exists:
                remaining_turns = self.max_turns - result.total_turns
                if remaining_turns > 0:
                    if self.verbose:
                        print(f"\n[WARNING] Output file not found at {context.output_file}")
                        print(f"[WARNING] Sending reminder to agent ({remaining_turns} turns remaining)...")
                    
                    if self._logger:
                        self._logger.log_user_task(
                            f"[System Check] The output file was NOT created at: {context.output_file}\n"
                            f"Please return to work again until you create the output file at the exact path specified above, then signal ACTION: TASK_COMPLETE again."
                        )
                    
                    # Continue the conversation with a reminder
                    result = agent.continue_with_message(
                        f"[System Check] The output file was NOT created at: {context.output_file}\n"
                        f"Please return to work again until you create the output file at the exact path specified above, then signal ACTION: TASK_COMPLETE again."
                    )
                    
                    # Re-check output file
                    output_exists = os.path.exists(context.output_file)
            
            # Determine final success and failure reason
            success = result.success and output_exists
            
            # Build detailed error message for failures
            error = result.error
            if not success and not error:
                failure_reasons = []
                if not result.success:
                    failure_reasons.append("Agent did not complete successfully")
                if not output_exists:
                    failure_reasons.append(f"Output file was not created: {context.output_file}")
                error = "; ".join(failure_reasons)
            
            run_result = {
                "success": success,
                "answer": result.final_answer,
                "turns": result.total_turns,
                "error": error,
            }

            # Log failure reason to verbose output
            if not success and self.verbose:
                print(f"[FAILURE] {error}")

            # Log final result
            if self._logger:
                self._logger.log_result(
                    success=run_result["success"],
                    answer=run_result["answer"],
                    turns=run_result["turns"],
                    error=run_result["error"],
                )

            return run_result

        except Exception as e:
            error_msg = f"Exception during agent execution: {str(e)}"
            
            # Log failure to verbose output
            if self.verbose:
                print(f"[FAILURE] {error_msg}")
            
            error_result = {
                "success": False,
                "answer": "",
                "turns": 0,
                "error": error_msg,
            }

            if self._logger:
                self._logger.log_result(
                    success=False,
                    answer="",
                    turns=0,
                    error=error_msg,
                )

            return error_result

    def get_last_log_file(self) -> str | None:
        """Get the path to the last log file."""
        if self._logger:
            return self._logger.get_log_file()
        return None
