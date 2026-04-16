"""
Sectioned prompt system for ReAct agents.

This module provides a flexible, section-based approach to building system prompts.
Each section can be customized independently, allowing domain-specific agents to
override only the parts they need while keeping the core ReAct format consistent.

Section Order:
1. role              - Agent identity/role
2. domain_context    - Domain-specific context (task info, no action examples)
3. action_format     - Action/Observation format (core ReAct)
4. task_completion   - ACTION: TASK_COMPLETE signal
5. tool_definitions  - Auto-generated from tools (what tools are available)
6. examples          - Workflow examples showing tool usage (reinforcement)
7. reminders         - Final format reminders
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tools import Tool


@dataclass
class PromptContext:
    """Context passed to sections when rendering."""
    tools: list[Tool] = field(default_factory=list)
    extra_data: dict = field(default_factory=dict)


class PromptSection(ABC):
    """Base class for prompt sections."""

    @abstractmethod
    def render(self, context: PromptContext) -> str:
        """Render this section to a string."""
        pass

    def is_empty(self, context: PromptContext) -> bool:
        """Return True if this section should be skipped."""
        return False


# =============================================================================
# Core Sections
# =============================================================================

class RoleSection(PromptSection):
    """Agent identity/role section."""

    DEFAULT_ROLE = "You are an expert assistant who can solve any task using tool calls. You will be given a task to solve as best you can."

    def __init__(self, role_text: str | None = None):
        self.role_text = role_text or self.DEFAULT_ROLE

    def render(self, context: PromptContext) -> str:
        return self.role_text


class DomainContextSection(PromptSection):
    """Optional domain-specific workflow and context."""

    def __init__(self, context_text: str = ""):
        self.context_text = context_text

    def render(self, context: PromptContext) -> str:
        return self.context_text

    def is_empty(self, context: PromptContext) -> bool:
        return not self.context_text.strip()


class ActionFormatSection(PromptSection):
    """Core ReAct action format section - rarely needs customization."""

    DEFAULT_FORMAT = """To do so, you have been given access to some tools.

The tool call you write is an action: after you output an Action, the tool will be executed and the user will provide the result as an "Observation:" message. You must wait for this observation before continuing - do NOT generate observations yourself.
This Action/Observation can repeat N times, you should take several steps when needed.

You can think step-by-step before taking an action.

## CRITICAL: Action Format Requirements

You MUST use this EXACT format for tool calls:

Action:
{
    "name": "<tool_name>",
    "arguments": {<arguments_as_json>}
}

IMPORTANT:
- The word "Action:" must appear on its own line, followed by a JSON object
- The JSON must have "name" (string) and "arguments" (object) fields
- Do NOT use markdown code blocks (```json or ```bash) around actions
- Do NOT write raw commands or code outside of the Action JSON format
- Any other format will NOT be parsed and the tool will NOT execute"""

    def __init__(self, format_text: str | None = None):
        self.format_text = format_text or self.DEFAULT_FORMAT

    def render(self, context: PromptContext) -> str:
        return self.format_text


class TaskCompletionSection(PromptSection):
    """Task completion signal section."""

    DEFAULT_TEXT = """## Completing the Task

When you have finished the task, signal completion by outputting exactly:

ACTION: TASK_COMPLETE

This tells the system you are done. Do NOT use any other method to end the task."""

    def __init__(self, completion_text: str | None = None):
        self.completion_text = completion_text or self.DEFAULT_TEXT

    def render(self, context: PromptContext) -> str:
        return self.completion_text


class ExamplesSection(PromptSection):
    """Few-shot examples section - commonly customized per domain."""

    DEFAULT_EXAMPLES = """## Examples

Here are a few examples using notional tools:
---
Task: "What is the weather in Paris and what should I wear?"

Thought: I need to first get the weather in Paris, then provide clothing recommendations.

Action:
{
    "name": "get_weather",
    "arguments": {"city": "Paris"}
}

Observation: "Paris: 15C, partly cloudy, 60% humidity"

Thought: The weather is mild at 15C and partly cloudy. I can now give clothing advice.

Since it's 15C and partly cloudy in Paris, I recommend wearing layers - a light jacket or sweater over a t-shirt. Bring an umbrella just in case as it's partly cloudy.

ACTION: TASK_COMPLETE

---
Task: "Calculate 25 * 17 + 300"

Action:
{
    "name": "calculator",
    "arguments": {"expression": "25 * 17 + 300"}
}

Observation: "725"

The result of 25 * 17 + 300 is 725.

ACTION: TASK_COMPLETE

---
Above examples were using notional tools that might not exist for you."""

    def __init__(self, examples_text: str | None = None):
        self.examples_text = examples_text or self.DEFAULT_EXAMPLES

    def render(self, context: PromptContext) -> str:
        return self.examples_text


class ToolDefinitionsSection(PromptSection):
    """Auto-generated tool definitions section."""

    HEADER = "You only have access to these tools:"

    def render(self, context: PromptContext) -> str:
        lines = [self.HEADER]
        for tool in context.tools:
            params_schema = tool.get_params_schema()
            params_str = json.dumps(params_schema, indent=2)
            lines.append(f"- {tool.name}: {tool.description}")
            lines.append(f"    Parameters: {params_str}")
        return "\n".join(lines)

    def is_empty(self, context: PromptContext) -> bool:
        return len(context.tools) == 0


class RemindersSection(PromptSection):
    """Final format reminders section."""

    DEFAULT_REMINDERS = """Remember:
- Use "Action:" followed by a JSON object to call a tool - no other format works
- Wait for "Observation:" to see the result before proceeding
- When you have finished the task, output "ACTION: TASK_COMPLETE"
- Think step-by-step when the problem is complex"""

    def __init__(self, reminders_text: str | None = None):
        self.reminders_text = reminders_text or self.DEFAULT_REMINDERS

    def render(self, context: PromptContext) -> str:
        return self.reminders_text


# =============================================================================
# Prompt Sections Container
# =============================================================================

@dataclass
class PromptSections:
    """Container for all prompt sections in order."""
    role: RoleSection = field(default_factory=RoleSection)
    domain_context: DomainContextSection = field(default_factory=DomainContextSection)
    action_format: ActionFormatSection = field(default_factory=ActionFormatSection)
    task_completion: TaskCompletionSection = field(default_factory=TaskCompletionSection)
    examples: ExamplesSection = field(default_factory=ExamplesSection)
    tool_definitions: ToolDefinitionsSection = field(default_factory=ToolDefinitionsSection)
    reminders: RemindersSection = field(default_factory=RemindersSection)

    def iter_sections(self):
        """Iterate over sections in order."""
        yield self.role
        yield self.domain_context
        yield self.action_format
        yield self.task_completion
        yield self.tool_definitions  # Tools defined before examples use them
        yield self.examples          # Examples reinforce tool usage
        yield self.reminders


# =============================================================================
# System Prompt Builder
# =============================================================================

class SystemPromptBuilder:
    """
    Builder for constructing system prompts from sections.

    Example usage:
        builder = SystemPromptBuilder()
        builder.set_examples("Custom examples...")
        prompt = builder.build(tools=[bash_tool])

    Or use a preset:
        builder = SystemPromptBuilder.from_preset("spreadsheet_cli")
        prompt = builder.build(tools=[bash_tool])
    """

    def __init__(self, sections: PromptSections | None = None):
        self.sections = sections or PromptSections()

    def build(self, tools: list[Tool], extra_data: dict | None = None) -> str:
        """
        Render all sections into a complete system prompt.

        Args:
            tools: List of available tools
            extra_data: Optional extra data for section rendering

        Returns:
            Complete system prompt string
        """
        context = PromptContext(tools=tools, extra_data=extra_data or {})

        parts = []
        for section in self.sections.iter_sections():
            if not section.is_empty(context):
                rendered = section.render(context)
                if rendered.strip():
                    parts.append(rendered)

        return "\n\n".join(parts)

    def set_role(self, text: str) -> SystemPromptBuilder:
        """Override the role section."""
        self.sections.role = RoleSection(text)
        return self

    def set_domain_context(self, text: str) -> SystemPromptBuilder:
        """Set the domain context section."""
        self.sections.domain_context = DomainContextSection(text)
        return self

    def set_action_format(self, text: str) -> SystemPromptBuilder:
        """Override the action format section (rarely needed)."""
        self.sections.action_format = ActionFormatSection(text)
        return self

    def set_task_completion(self, text: str) -> SystemPromptBuilder:
        """Override the task completion section."""
        self.sections.task_completion = TaskCompletionSection(text)
        return self

    def set_examples(self, text: str) -> SystemPromptBuilder:
        """Override the examples section."""
        self.sections.examples = ExamplesSection(text)
        return self

    def set_reminders(self, text: str) -> SystemPromptBuilder:
        """Override the reminders section."""
        self.sections.reminders = RemindersSection(text)
        return self

    @classmethod
    def from_preset(cls, name: str) -> SystemPromptBuilder:
        """
        Create a builder from a preset configuration.

        Available presets:
        - "default": Standard ReAct agent

        For domain-specific presets (e.g., spreadsheet), use the domain's
        own prompt builder or import from the domain module.

        Args:
            name: Preset name

        Returns:
            Configured SystemPromptBuilder
        """
        if name == "default":
            return cls()
        else:
            raise ValueError(f"Unknown preset: {name}. Available: default")
