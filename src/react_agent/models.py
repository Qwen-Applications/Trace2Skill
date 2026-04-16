"""
LLM client abstractions for the ReAct agent.

This module provides a simple interface to interact with LLMs,
with a default implementation for OpenAI-compatible APIs.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


class RequestContextLengthExceeded(RuntimeError):
    """Raised when a request exceeds the model context window."""


def _extract_openai_error_message(exc: Exception) -> str:
    """Best-effort extraction of the provider error message."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message
    return str(exc)


def _is_context_length_bad_request(exc: Exception) -> bool:
    """Return True when the provider rejected the request for context length."""
    body = getattr(exc, "body", None)
    param = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            param = error.get("param")

    message = _extract_openai_error_message(exc).lower()
    return (
        param == "input_tokens"
        and "context length" in message
        and "maximum input length" in message
    )


@dataclass
class Message:
    """A single message in a conversation."""
    role: str  # "system", "user", "assistant"
    content: str


@dataclass
class ModelSettings:
    """Settings for LLM generation."""
    temperature: float = 0.7
    max_tokens: int | None = None
    stop: list[str] = field(default_factory=list)
    extra_body: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        result = {"temperature": self.temperature}
        if self.max_tokens:
            result["max_tokens"] = self.max_tokens
        if self.stop:
            result["stop"] = self.stop
        if self.extra_body:
            result["extra_body"] = self.extra_body
        return result


def _create_disk_cache(cache_path: str):
    """Create a disk cache with standard settings."""
    try:
        import diskcache as dc
    except ImportError:
        return None
    
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    
    cache_settings = dc.DEFAULT_SETTINGS.copy()
    cache_settings["eviction_policy"] = "none"
    cache_settings["size_limit"] = int(1e12)
    cache_settings["cull_limit"] = 0
    return dc.Cache(cache_path, **cache_settings)


def _make_cache_key(model: str, messages: list[dict]) -> tuple:
    """Create a cache key from model and messages."""
    messages_str = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return (model, messages_str)


TRANSIENT_REPLY_PATTERNS = (
    r"\b429\b",
    r"mpe-429",
    r"resource exhausted",
    r"resource_exhausted",
    r"rate limit",
    r"too many requests",
    r"请求服务异常",
    r"模型提供方限流",
)


def _is_transient_error_reply(reply: str) -> bool:
    """Return True when the reply looks like a retriable provider error."""
    import re

    if not reply:
        return False
    text = reply.strip().lower()
    return any(re.search(pattern, text) for pattern in TRANSIENT_REPLY_PATTERNS)


class LLMClient(ABC):
    """Abstract base class for LLM clients."""
    
    @abstractmethod
    def chat(self, messages: list[Message], settings: ModelSettings | None = None) -> str:
        """Send messages to the LLM and get a response."""
        pass
    
    @abstractmethod
    async def chat_async(self, messages: list[Message], settings: ModelSettings | None = None) -> str:
        """Async version of chat."""
        pass


# Generation config presets for various models (useful for vLLM/local serving)
GENERATION_CONFIG_PRESETS: dict[str, dict[str, Any]] = {
    "openai/gpt-oss-120b": {
        "extra_body": {"reasoning_effort": "medium"},
    },
    "Qwen/Qwen3-8B": {
        "temperature": 0.6,
        "top_p": 0.95,
        "extra_body": {"enable_thinking": True, "top_k": 20},
    },
    "microsoft/Phi-4-reasoning-plus": {
        "temperature": 0.8,
        "top_p": 0.95,
        "extra_body": {"enable_thinking": True, "top_k": 50},
    },
    "deepseek-reasoner": {
        "temperature": 0.6,
    },
}


class OpenAIClient(LLMClient):
    """
    OpenAI-compatible LLM client with disk caching and retry logic.
    
    Works with OpenAI API and compatible services (Azure, vLLM, LiteLLM, etc.)
    Supports reasoning models with thinking content parsing.
    """
    
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        cache_path: str | None = None,
        use_cache: bool = True,
        generation_config: dict | None = None,
        retry_times: tuple[int, ...] = (5, 10, 30),
        timeout: float | None = 600.0,
    ):
        """
        Initialize OpenAI-compatible client.

        Args:
            model: Model name
            api_key: API key (defaults to OPENAI_API_KEY env var, use "EMPTY" for local vLLM)
            base_url: API endpoint (defaults to OPENAI_BASE_URL env var)
            cache_path: Path for disk cache (auto-generated if None)
            use_cache: Whether to use disk caching
            generation_config: Custom generation config (uses preset from GENERATION_CONFIG_PRESETS if None and model matches)
            retry_times: Tuple of wait times (seconds) between retries
            timeout: Request timeout in seconds (default 600s). Pass None for no timeout.
        """
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self.retry_times = retry_times
        
        if not self.api_key:
            raise ValueError(
                "API key required. Set OPENAI_API_KEY environment variable, "
                "pass api_key parameter, or use api_key='EMPTY' for local vLLM."
            )
        
        # Get generation config from presets or use custom
        if generation_config is not None:
            self.generation_config = generation_config
        else:
            self.generation_config = GENERATION_CONFIG_PRESETS.get(model, {})
        
        # Lazy import to avoid requiring openai if not used
        from openai import OpenAI, AsyncOpenAI
        
        client_kwargs = {"api_key": self.api_key, "timeout": timeout}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        
        self._client = OpenAI(**client_kwargs)
        # Lazily create async client only if chat_async is used
        self._async_client = None
        self._async_client_kwargs = client_kwargs
        
        # Setup disk cache
        self._cache = None
        self._cache_lock = threading.Lock()
        if use_cache:
            if cache_path is None:
                cache_path = os.path.join(
                    os.path.expanduser("~"), ".cache", "openai_client_cache.diskcache"
                )
            self._cache = _create_disk_cache(cache_path)
    
    def _get_from_cache(self, cache_key: tuple) -> tuple[str, str] | None:
        """Get response from cache if available. Returns (reply, reasoning_content)."""
        if self._cache is None:
            return None
        return self._cache.get(cache_key, None)
    
    def _save_to_cache(self, cache_key: tuple, response: tuple[str, str]):
        """Save response to cache. Response is (reply, reasoning_content)."""
        if self._cache is None:
            return
        with self._cache_lock:
            self._cache[cache_key] = response
    
    def _parse_response(self, response) -> tuple[str, str]:
        """
        Parse response, extracting reasoning content if present.
        
        Returns:
            Tuple of (reply, reasoning_content)
        """
        message = response.choices[0].message
        reply = message.content or ""
        reasoning_content = getattr(message, "reasoning_content", "") or ""
        
        # Handle models that embed thinking in the response with </think> tags
        if "</think>" in reply:
            parts = reply.split("</think>")
            reasoning_content = parts[0].replace("<think>", "").strip()
            reply = parts[1].strip() if len(parts) > 1 else ""
        
        return reply, reasoning_content
    
    def _send_request_with_retry(self, messages: list[dict], config: dict):
        """Send request with retry logic."""
        for i, wait_time in enumerate(self.retry_times):
            try:
                return self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    **config,
                )
            except Exception as e:
                if _is_context_length_bad_request(e):
                    raise RequestContextLengthExceeded(_extract_openai_error_message(e)) from e
                log.warning(
                    f"Request failed: {e}. Retry #{i+1}/{len(self.retry_times)} "
                    f"after {wait_time} seconds."
                )
                time.sleep(wait_time)
        
        # Final attempt without catch
        return self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            **config,
        )
    
    def _get_async_client(self):
        if self._async_client is None:
            from openai import AsyncOpenAI
            self._async_client = AsyncOpenAI(**self._async_client_kwargs)
        return self._async_client

    async def _send_request_with_retry_async(self, messages: list[dict], config: dict):
        """Async version of send request with retry logic."""
        import asyncio
        
        for i, wait_time in enumerate(self.retry_times):
            try:
                return await self._get_async_client().chat.completions.create(
                    model=self.model,
                    messages=messages,
                    **config,
                )
            except Exception as e:
                if _is_context_length_bad_request(e):
                    raise RequestContextLengthExceeded(_extract_openai_error_message(e)) from e
                log.warning(
                    f"Request failed: {e}. Retry #{i+1}/{len(self.retry_times)} "
                    f"after {wait_time} seconds."
                )
                await asyncio.sleep(wait_time)
        
        # Final attempt without catch
        return await self._get_async_client().chat.completions.create(
            model=self.model,
            messages=messages,
            **config,
        )

    async def aclose(self) -> None:
        """Close async client resources if initialized.

        Since ``chat_async`` delegates to the sync client, no async
        resources are created during normal usage, making this a no-op.
        Kept for API compatibility.
        """
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None
    
    def chat(
        self,
        messages: list[Message],
        settings: ModelSettings | None = None,
        return_reasoning: bool = False,
    ) -> str | tuple[str, str]:
        """
        Send messages and get a response.
        
        Args:
            messages: List of messages
            settings: Optional model settings (merged with generation_config)
            return_reasoning: If True, returns (reply, reasoning_content) tuple
            
        Returns:
            Response string, or tuple of (reply, reasoning_content) if return_reasoning=True
        """
        openai_messages = [{"role": m.role, "content": m.content} for m in messages]
        
        # Merge generation config with settings
        config = self.generation_config.copy()
        if settings:
            settings_dict = settings.to_dict()
            config.update(settings_dict)
        
        # Check cache
        cache_key = _make_cache_key(self.model, openai_messages)
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            log.debug("Loaded response from cache")
            reply, reasoning_content = cached
            return (reply, reasoning_content) if return_reasoning else reply
        
        # Send request
        response = self._send_request_with_retry(openai_messages, config)
        reply, reasoning_content = self._parse_response(response)
        
        # Cache the response
        self._save_to_cache(cache_key, (reply, reasoning_content))
        
        return (reply, reasoning_content) if return_reasoning else reply
    
    async def chat_async(
        self,
        messages: list[Message],
        settings: ModelSettings | None = None,
        return_reasoning: bool = False,
    ) -> str | tuple[str, str]:
        """Async version of chat.

        Delegates to the synchronous ``chat()`` method to avoid creating an
        ``AsyncOpenAI`` / ``httpx.AsyncClient`` whose cleanup can raise
        ``RuntimeError('Event loop is closed')`` after ``asyncio.run()``
        tears down the loop.  The ReAct loop is sequential, so true async
        I/O provides no concurrency benefit here.
        """
        return self.chat(messages, settings, return_reasoning)
    
    def __del__(self):
        """Cleanup: close the cache when instance is destroyed."""
        if hasattr(self, '_cache') and self._cache is not None:
            self._cache.close()


class ApiChatClient(LLMClient):
    """
    LLM client for a local POST-based API using the ApiChat payload format.

    The endpoint expects:
    {model, prompt, params, app, quota_id, user_id, access_key, tag}
    """

    def __init__(
        self,
        model: str | None = None,
        config_path: str = "config/llm_api.json",
        cache_path: str | None = None,
        use_cache: bool = True,
        generation_config: dict | None = None,
        retry_times: tuple[int, ...] = (1, 2, 5, 10, 20),
        timeout: float | None = 120.0,
        debug: bool = False,
        debug_dir: str | None = None,
    ):
        with open(config_path, "r", encoding="utf-8") as handle:
            config = json.load(handle)

        self.url = config["url"]
        self.model = model or config["model"]
        self.app = config.get("app", "")
        self.quota_id = config.get("quota_id", "")
        self.user_id = config.get("user_id", "")
        self.access_key = config.get("access_key", "")
        self.tag = config.get("tag", "")
        self.retry_times = retry_times
        self.timeout = timeout
        self.debug = debug
        self._debug_first_call = True
        self._debug_counter = 0
        self.generation_config = generation_config or config.get("default_params", {})

        requests_module = __import__("requests")
        self._session = requests_module.Session()

        self._cache = None
        self._cache_lock = threading.Lock()
        if use_cache:
            cache_root = cache_path or os.path.join(os.path.expanduser("~"), ".cache")
            cache_file = os.path.join(cache_root, "api_chat_client_cache.diskcache")
            self._cache = _create_disk_cache(cache_file)

        self._debug_dir = None
        if self.debug:
            debug_root = cache_path or os.path.join(os.path.expanduser("~"), ".cache")
            self._debug_dir = debug_dir or os.path.join(debug_root, "api_chat_debug")
            os.makedirs(self._debug_dir, exist_ok=True)

    def _get_from_cache(self, cache_key: tuple) -> tuple[str, str] | None:
        if self._cache is None:
            return None
        return self._cache.get(cache_key, None)

    def _save_to_cache(self, cache_key: tuple, response: tuple[str, str]):
        if self._cache is None:
            return
        if self._cache_lock is None:
            self._cache[cache_key] = response
            return
        with self._cache_lock:
            self._cache[cache_key] = response

    def _delete_from_cache(self, cache_key: tuple):
        if self._cache is None or cache_key not in self._cache:
            return
        if self._cache_lock is None:
            del self._cache[cache_key]
            return
        with self._cache_lock:
            if cache_key in self._cache:
                del self._cache[cache_key]

    @staticmethod
    def _extract_reply(data: dict) -> str:
        """Extract the reply text from the API response."""
        if "choices" in data:
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                pass
        for nest_key in ("completion", "data"):
            if nest_key in data and isinstance(data[nest_key], dict):
                result = ApiChatClient._extract_reply(data[nest_key])
                if result:
                    return result
        for key in ("response", "content", "text", "reply", "output", "message"):
            if key in data and isinstance(data[key], str):
                return data[key]
        return ""

    @staticmethod
    def _extract_reasoning(data: dict) -> str:
        """Extract reasoning/thinking content when present."""
        for key in ("reasoning_content", "thinking", "reasoning"):
            if key in data and isinstance(data[key], str):
                return data[key]
        if "choices" in data:
            try:
                message = data["choices"][0]["message"]
                for key in ("reasoning_content", "thinking"):
                    if key in message and isinstance(message[key], str):
                        return message[key]
            except (KeyError, IndexError, TypeError):
                pass
        for nest_key in ("completion", "data"):
            if nest_key in data and isinstance(data[nest_key], dict):
                result = ApiChatClient._extract_reasoning(data[nest_key])
                if result:
                    return result
        return ""

    @staticmethod
    def _normalize_prompt(messages: list[Message]) -> list[dict]:
        prompt = []
        for msg in messages:
            content = msg.content
            prompt.append({"role": msg.role, "content": content})
        return prompt

    def _build_params(self, settings: ModelSettings | None) -> dict:
        params = self.generation_config.copy()
        if not settings:
            return params
        settings_dict = settings.to_dict()
        extra_body = settings_dict.pop("extra_body", {})
        params.update(settings_dict)
        if extra_body:
            params.update(extra_body)
        return params

    def _post_payload(self, payload: dict):
        response = self._session.post(
            self.url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response

    def _send_request(self, prompt: list[dict], params: dict) -> dict | None:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "params": params,
            "app": self.app,
            "quota_id": self.quota_id,
            "user_id": self.user_id,
            "access_key": self.access_key,
            "tag": self.tag,
        }

        if self._debug_first_call:
            self._debug_first_call = False
            payload_debug = dict(payload)
            payload_debug["prompt"] = f"[{len(prompt)} messages, first role={prompt[0]['role']}]"
            log.info("ApiChatClient first request: %s", json.dumps(payload_debug, ensure_ascii=False))

        try:
            response = self._post_payload(payload)
            result = response.json()
            if self.debug and self._debug_dir is not None:
                self._write_debug_artifacts(response, result)
            return result
        except Exception as exc:
            log.warning("ApiChat request failed: %s", exc)
            return None

    def _write_debug_artifacts(self, response, result: dict):
        if self._debug_dir is None:
            return
        with self._cache_lock:
            seq = self._debug_counter
            self._debug_counter += 1
        raw_path = os.path.join(self._debug_dir, f"response_{seq:04d}_raw.bin")
        with open(raw_path, "wb") as handle:
            handle.write(response.content)
        txt_path = os.path.join(self._debug_dir, f"response_{seq:04d}.txt")
        with open(txt_path, "w", encoding="utf-8") as handle:
            handle.write(f"HTTP {response.status_code}\n")
            handle.write(f"Content-Type: {response.headers.get('Content-Type', 'N/A')}\n")
            handle.write(f"Encoding: {response.encoding}\n")
            handle.write(f"Content length: {len(response.content)} bytes\n---\n")
            handle.write(response.text)
        json_path = os.path.join(self._debug_dir, f"response_{seq:04d}_parsed.json")
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)

    def chat(
        self,
        messages: list[Message],
        settings: ModelSettings | None = None,
        return_reasoning: bool = False,
    ) -> str | tuple[str, str]:
        prompt = self._normalize_prompt(messages)
        params = self._build_params(settings)
        cache_key = _make_cache_key(
            self.model,
            [{"messages": prompt, "params": params}],
        )
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            reply, reasoning_content = cached
            if _is_transient_error_reply(reply):
                self._delete_from_cache(cache_key)
            else:
                return (reply, reasoning_content) if return_reasoning else reply

        reply = ""
        reasoning_content = ""
        for i, wait_time in enumerate(self.retry_times):
            response_json = self._send_request(prompt, params)
            if response_json is None:
                reply = ""
                reasoning_content = ""
            else:
                reply = self._extract_reply(response_json)
                reasoning_content = self._extract_reasoning(response_json)
                if "</think>" in reply:
                    parts = reply.split("</think>", 1)
                    reasoning_content = parts[0].replace("<think>", "").strip()
                    reply = parts[1].strip() if len(parts) > 1 else ""

            if reply and not _is_transient_error_reply(reply):
                break
            if i < len(self.retry_times) - 1:
                time.sleep(wait_time)

        if reply and not _is_transient_error_reply(reply):
            self._save_to_cache(cache_key, (reply, reasoning_content))

        low = reply.lower()
        if (
            "please provide" in low
            or "to assist you" in low
            or "as an ai language model" in low
        ):
            return ("", "") if return_reasoning else ""

        return (reply, reasoning_content) if return_reasoning else reply

    async def chat_async(
        self,
        messages: list[Message],
        settings: ModelSettings | None = None,
        return_reasoning: bool = False,
    ) -> str | tuple[str, str]:
        return self.chat(messages, settings, return_reasoning)

    def __del__(self):
        if hasattr(self, "_cache") and self._cache is not None and hasattr(self._cache, "close"):
            self._cache.close()
        if hasattr(self, "_session") and self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass


class MockLLMClient(LLMClient):
    """
    Mock LLM client for testing.
    
    Returns predefined responses or echoes input.
    """
    
    def __init__(self, responses: list[str] | None = None):
        self.responses = responses or []
        self._call_count = 0
        self.call_history: list[list[Message]] = []
    
    def chat(self, messages: list[Message], settings: ModelSettings | None = None) -> str:
        self.call_history.append(messages)
        
        if self._call_count < len(self.responses):
            response = self.responses[self._call_count]
            self._call_count += 1
            return response
        
        # Default: echo last user message
        for msg in reversed(messages):
            if msg.role == "user":
                return f"Echo: {msg.content}"
        return "No response configured"
    
    async def chat_async(self, messages: list[Message], settings: ModelSettings | None = None) -> str:
        return self.chat(messages, settings)
