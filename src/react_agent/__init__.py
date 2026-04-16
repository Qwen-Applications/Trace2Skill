"""
React Agent Minimal - A self-contained ReAct (Reasoning + Acting) agent implementation.

This package provides a simple, easy-to-understand implementation of the ReAct pattern
for building LLM-powered agents that can use tools to solve tasks.
"""

from .agent import ReActAgent, AgentConfig, AgentStep, AgentResult
from .tools import Tool, ToolParameter, tool
from .models import ApiChatClient, LLMClient, OpenAIClient
from .converter import ReActConverter, ParseResult, ParseResultType, truncate_observation
from .prompts import (
    SystemPromptBuilder,
    PromptContext,
    PromptSection,
    PromptSections,
    RoleSection,
    DomainContextSection,
    ActionFormatSection,
    TaskCompletionSection,
    ExamplesSection,
    ToolDefinitionsSection,
    RemindersSection,
)

__version__ = "0.1.0"

__all__ = [
    # Agent
    "ReActAgent",
    "AgentConfig",
    "AgentStep",
    "AgentResult",
    # Tools
    "Tool",
    "ToolParameter",
    "tool",
    # Models
    "ApiChatClient",
    "LLMClient",
    "OpenAIClient",
    # Converter
    "ReActConverter",
    "ParseResult",
    "ParseResultType",
    "truncate_observation",
    # Prompts
    "SystemPromptBuilder",
    "PromptContext",
    "PromptSection",
    "PromptSections",
    "RoleSection",
    "DomainContextSection",
    "ActionFormatSection",
    "TaskCompletionSection",
    "ExamplesSection",
    "ToolDefinitionsSection",
    "RemindersSection",
]
