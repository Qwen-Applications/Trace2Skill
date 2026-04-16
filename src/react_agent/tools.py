"""
Tool definitions and utilities for the ReAct agent.

Tools are functions that the agent can call to interact with the external world.
Each tool has a name, description, and parameter schema that helps the LLM understand
when and how to use it.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, get_type_hints


@dataclass
class ToolParameter:
    """Describes a single parameter of a tool."""
    name: str
    type: str
    description: str
    required: bool = True
    default: Any = None


@dataclass
class Tool:
    """
    Represents a tool that can be used by the ReAct agent.
    
    Attributes:
        name: Unique identifier for the tool
        description: What the tool does (shown to LLM)
        func: The actual Python function to execute
        parameters: List of parameter definitions
    """
    name: str
    description: str
    func: Callable[..., str]
    parameters: list[ToolParameter] = field(default_factory=list)
    
    def execute(self, **kwargs) -> str:
        """Execute the tool with given arguments."""
        try:
            result = self.func(**kwargs)
            return str(result)
        except Exception as e:
            return f"Error executing tool '{self.name}': {e}"
    
    def get_params_schema(self) -> dict:
        """Get JSON schema for parameters."""
        properties = {}
        required = []
        
        for param in self.parameters:
            properties[param.name] = {
                "type": param.type,
                "description": param.description,
            }
            if param.required:
                required.append(param.name)
        
        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }
    
    def __repr__(self) -> str:
        return f"Tool(name={self.name!r}, params={[p.name for p in self.parameters]})"


def tool(
    name: str | None = None,
    description: str | None = None,
) -> Callable[[Callable[..., str]], Tool]:
    """
    Decorator to convert a function into a Tool.
    
    The function's docstring is used as the description if not provided.
    Parameter types and descriptions are extracted from type hints and docstring.
    
    Usage:
        @tool(name="search", description="Search the web")
        def search(query: str) -> str:
            '''
            Args:
                query: The search query string
            '''
            return "search results..."
    """
    def decorator(func: Callable[..., str]) -> Tool:
        tool_name = name or func.__name__
        tool_description = description or _extract_description(func)
        parameters = _extract_parameters(func)
        
        return Tool(
            name=tool_name,
            description=tool_description,
            func=func,
            parameters=parameters,
        )
    
    return decorator


def _extract_description(func: Callable) -> str:
    """Extract description from function docstring."""
    doc = func.__doc__ or ""
    # Get first paragraph before Args:
    lines = []
    for line in doc.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith(("args:", "arguments:", "parameters:", "returns:")):
            break
        lines.append(stripped)
    return " ".join(lines).strip() or f"Execute {func.__name__}"


def _extract_parameters(func: Callable) -> list[ToolParameter]:
    """Extract parameters from function signature and docstring."""
    sig = inspect.signature(func)
    type_hints = get_type_hints(func) if hasattr(func, "__annotations__") else {}
    param_docs = _parse_param_docs(func.__doc__ or "")
    
    parameters = []
    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        
        # Get type
        py_type = type_hints.get(param_name, str)
        json_type = _python_type_to_json(py_type)
        
        # Get description from docstring
        param_desc = param_docs.get(param_name, f"The {param_name} parameter")
        
        # Check if required
        has_default = param.default != inspect.Parameter.empty
        
        parameters.append(ToolParameter(
            name=param_name,
            type=json_type,
            description=param_desc,
            required=not has_default,
            default=param.default if has_default else None,
        ))
    
    return parameters


def _parse_param_docs(docstring: str) -> dict[str, str]:
    """Parse parameter descriptions from docstring."""
    param_docs = {}
    in_args = False
    current_param = None
    current_desc = []
    
    for line in docstring.split("\n"):
        stripped = line.strip()
        
        if stripped.lower().startswith(("args:", "arguments:", "parameters:")):
            in_args = True
            continue
        
        if stripped.lower().startswith(("returns:", "raises:", "example")):
            in_args = False
            if current_param:
                param_docs[current_param] = " ".join(current_desc).strip()
            break
        
        if in_args:
            # Check for new parameter (name: description format)
            if ":" in stripped and not stripped.startswith(" "):
                if current_param:
                    param_docs[current_param] = " ".join(current_desc).strip()
                parts = stripped.split(":", 1)
                current_param = parts[0].strip()
                current_desc = [parts[1].strip()] if len(parts) > 1 else []
            elif current_param and stripped:
                current_desc.append(stripped)
    
    if current_param:
        param_docs[current_param] = " ".join(current_desc).strip()
    
    return param_docs


def _python_type_to_json(py_type: type) -> str:
    """Convert Python type to JSON schema type."""
    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }
    
    # Handle Optional, Union, etc.
    origin = getattr(py_type, "__origin__", None)
    if origin is not None:
        # For Union types, try to get the first non-None type
        args = getattr(py_type, "__args__", ())
        for arg in args:
            if arg is not type(None):
                return type_map.get(arg, "string")
    
    return type_map.get(py_type, "string")


class ToolRegistry:
    """Registry to manage multiple tools."""
    
    def __init__(self):
        self._tools: dict[str, Tool] = {}
    
    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
    
    def get(self, tool_name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(tool_name)
    
    def list_tools(self) -> list[Tool]:
        """Get all registered tools."""
        return list(self._tools.values())
    
    def execute(self, tool_name: str, **kwargs) -> str:
        """Execute a tool by name."""
        tool = self.get(tool_name)
        if tool is None:
            return f"Error: Unknown tool '{tool_name}'"
        return tool.execute(**kwargs)
