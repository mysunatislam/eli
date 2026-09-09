"""LLM layer.

Provider-neutral conversation turns + two providers (Google Gemini, Anthropic Claude) with
streaming, retries and token accounting. The main agent never touches provider SDK objects.

Turn format (kept in MainAgent.history):
    {"role": "user"|"assistant", "parts": [part, ...], "raw": <provider-native>, "raw_provider": "gemini"|"anthropic"}
Parts:
    {"type": "text", "text": str}
    {"type": "image", "media_type": str, "data": <base64 str>}
    {"type": "tool_call", "id": str, "name": str, "args": dict}                      (assistant only)
    {"type": "tool_result", "id": str, "name": str, "content": str | [parts], "is_error": bool}   (user only)
`raw` is the provider's own assistant content; it is echoed back verbatim so thinking blocks /
thought signatures survive multi-step tool use. It is only used while the same provider is active.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import config as _config  # noqa: F401  (loads backend/.env before we read the environment)

log = logging.getLogger("eli.llm")
OnText = Optional[Callable[[str], None]]


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    stop: str = "end"  # end | tool | refusal | max_tokens
    usage: dict = field(default_factory=dict)
    raw: Any = None


class TransientError(Exception):
    """Rate limits, 5xx, network: worth retrying."""


# -- part helpers --------------------------------------------------------------------------------
def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def image_part(media_type: str, data_b64: str) -> dict:
    return {"type": "image", "media_type": media_type, "data": data_b64}


def tool_result_part(call: ToolCall, content, is_error: bool = False) -> dict:
    return {"type": "tool_result", "id": call.id, "name": call.name, "content": content, "is_error": bool(is_error)}


def estimate_tokens(turns: list[dict]) -> int:
    n = 0
    for t in turns:
        for p in t.get("parts", []):
            if p["type"] == "text":
                n += len(p["text"]) // 4 + 2
            elif p["type"] == "image":
                n += 1600
            elif p["type"] == "tool_call":
                n += len(str(p["args"])) // 4 + 10
            elif p["type"] == "tool_result":
                c = p["content"]
                if isinstance(c, list):
                    n += sum((len(x.get("text", "")) // 4 + 2) if x["type"] == "text" else 1600 for x in c)
                else:
                    n += len(str(c)) // 4 + 5
    return n


def _transient(e: Exception) -> bool:
    s = str(e).lower()
    return any(k in s for k in ("429", "resource_exhausted", "rate limit", "503", "502", "504", "unavailable",
                                "overloaded", "deadline", "timeout", "timed out", "connection", "temporarily"))


# -- Anthropic ---------------------------------------------------------------------------------------
class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, effort: str, max_tokens: int):
        import anthropic  # type: ignore
        self.sdk = anthropic
        self.client = anthropic.AsyncAnthropic()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    def _messages(self, turns: list[dict]) -> list[dict]:
        out = []
        for t in turns:
            if t["role"] == "assistant" and t.get("raw") is not None and t.get("raw_provider") == self.name:
                out.append({"role": "assistant", "content": t["raw"]})
                continue
            blocks = []
            for p in t["parts"]:
                k = p["type"]
                if k == "text":
                    blocks.append({"type": "text", "text": p["text"]})
                elif k == "image":
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": p["media_type"], "data": p["data"]}})
                elif k == "tool_call":
                    blocks.append({"type": "tool_use", "id": p["id"], "name": p["name"], "input": p["args"]})
                elif k == "tool_result":
                    c = p["content"]
                    if isinstance(c, list):
                        c = [{"type": "image", "source": {"type": "base64", "media_type": x["media_type"], "data": x["data"]}}
                             if x["type"] == "image" else {"type": "text", "text": x["text"]} for x in c]
                    b = {"type": "tool_result", "tool_use_id": p["id"], "content": c}
                    if p.get("is_error"):
                        b["is_error"] = True
                    blocks.append(b)
            if blocks:
                out.append({"role": t["role"], "content": blocks})
        return out

    async def complete(self, system: tuple[str, str], turns: list[dict], tools: list[dict], on_text: OnText = None) -> LLMResponse:
        stable, dynamic = system
        kwargs: dict = dict(
            model=self.model, max_tokens=self.max_tokens,
            system=[{"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}}, {"type": "text", "text": dynamic}],
            messages=self._messages(turns),
        )
        if tools:
            kwargs["tools"] = tools
        if "haiku" not in self.model and "4-5" not in self.model:
            kwargs["output_config"] = {"effort": self.effort}
        try:
            async with self.client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    if on_text and text:
                        on_text(text)
                msg = await stream.get_final_message()
        except self.sdk.RateLimitError as e:
            raise TransientError(str(e)) from e
        except self.sdk.APIConnectionError as e:
            raise TransientError(str(e)) from e
        except self.sdk.APIStatusError as e:
            if e.status_code >= 500:
                raise TransientError(str(e)) from e
            raise
        text = "\n".join(b.text for b in msg.content if b.type == "text").strip()
        calls = [ToolCall(b.id, b.name, dict(b.input or {})) for b in msg.content if b.type == "tool_use"]
        stop = {"tool_use": "tool", "refusal": "refusal", "max_tokens": "max_tokens"}.get(msg.stop_reason or "", "end")
        u = msg.usage
        usage = {"input": getattr(u, "input_tokens", 0) or 0, "output": getattr(u, "output_tokens", 0) or 0,
                 "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0}
        return LLMResponse(text=text, tool_calls=calls, stop=stop, usage=usage, raw=msg.content)


# -- Gemini ------------------------------------------------------------------------------------------
class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str, api_key: str):
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
        self.genai, self.T = genai, types
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def _schema(self, s: dict):
        s = dict(s or {})
        s.pop("additionalProperties", None)
        if not s.get("properties"):
            return None
        if not s.get("required"):
            s.pop("required", None)
        return s

    def _tools(self, tools: list[dict]):
        T = self.T
        decls = []
        for t in tools:
            kw = dict(name=t["name"], description=t["description"])
            params = self._schema(t.get("input_schema"))
            if params:
                kw["parameters"] = params
            decls.append(T.FunctionDeclaration(**kw))
        return [T.Tool(function_declarations=decls)]

    def _contents(self, turns: list[dict]):
        T = self.T
        out = []
        for t in turns:
            if t["role"] == "assistant" and t.get("raw") is not None and t.get("raw_provider") == self.name:
                out.append(t["raw"])
                continue
            parts = []
            for p in t["parts"]:
                k = p["type"]
                if k == "text":
                    parts.append(T.Part.from_text(text=p["text"]))
                elif k == "image":
                    parts.append(T.Part.from_bytes(data=base64.b64decode(p["data"]), mime_type=p["media_type"]))
                elif k == "tool_call":
                    parts.append(T.Part.from_function_call(name=p["name"], args=p["args"]))
                elif k == "tool_result":
                    c = p["content"]
                    images = []
                    if isinstance(c, list):
                        text = "\n".join(x["text"] for x in c if x["type"] == "text")
                        images = [x for x in c if x["type"] == "image"]
                    else:
                        text = str(c)
                    payload = {"error": text} if p.get("is_error") else {"result": text}
                    parts.append(T.Part.from_function_response(name=p["name"], response=payload))
                    for x in images:
                        parts.append(T.Part.from_bytes(data=base64.b64decode(x["data"]), mime_type=x["media_type"]))
            if parts:
                out.append(T.Content(role="user" if t["role"] == "user" else "model", parts=parts))
        return out

    FALLBACK_MODELS = ("gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest", "gemini-2.5-flash")

    async def complete(self, system: tuple[str, str], turns: list[dict], tools: list[dict], on_text: OnText = None) -> LLMResponse:
        try:
            return await self._complete(system, turns, tools, on_text)
        except Exception as e:
            s = str(e).lower()
            if "not_found" in s or "no longer available" in s or "not found" in s:
                for alt in self.FALLBACK_MODELS:
                    if alt == self.model:
                        continue
                    log.warning("model %s unavailable (%s); trying %s", self.model, str(e)[:120], alt)
                    self.model = alt
                    try:
                        return await self._complete(system, turns, tools, on_text)
                    except Exception as e2:
                        s2 = str(e2).lower()
                        if "not_found" in s2 or "not found" in s2 or "no longer available" in s2:
                            continue
                        raise
            raise

    async def _complete(self, system: tuple[str, str], turns: list[dict], tools: list[dict], on_text: OnText = None) -> LLMResponse:
        T = self.T
        config = T.GenerateContentConfig(
            system_instruction="\n\n".join(s for s in system if s),
            temperature=0.4,
            tools=self._tools(tools) if tools else None,
            automatic_function_calling=T.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = self._contents(turns)
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        raw_parts = []
        finish = ""
        usage_meta = None
        try:
            stream = await self.client.aio.models.generate_content_stream(model=self.model, contents=contents, config=config)
            async for chunk in stream:
                cand = (chunk.candidates or [None])[0]
                if cand is not None and cand.content and cand.content.parts:
                    for part in cand.content.parts:
                        raw_parts.append(part)
                        fc = getattr(part, "function_call", None)
                        if fc is not None and fc.name:
                            calls.append(ToolCall(getattr(fc, "id", None) or f"call_{secrets.token_hex(4)}", fc.name, dict(fc.args or {})))
                        elif getattr(part, "text", None) and not getattr(part, "thought", False):
                            text_parts.append(part.text)
                            if on_text:
                                on_text(part.text)
                    if cand.finish_reason:
                        finish = str(cand.finish_reason)
                if getattr(chunk, "usage_metadata", None):
                    usage_meta = chunk.usage_metadata
        except Exception as e:
            if _transient(e):
                raise TransientError(str(e)) from e
            raise
        raw = T.Content(role="model", parts=raw_parts) if raw_parts else None
        if calls:
            stop = "tool"
        elif "SAFETY" in finish or "PROHIBITED" in finish:
            stop = "refusal"
        elif "MAX_TOKENS" in finish:
            stop = "max_tokens"
        else:
            stop = "end"
        usage = {}
        if usage_meta is not None:
            usage = {"input": getattr(usage_meta, "prompt_token_count", 0) or 0,
                     "output": getattr(usage_meta, "candidates_token_count", 0) or 0,
                     "cache_read": getattr(usage_meta, "cached_content_token_count", 0) or 0}
        return LLMResponse(text="".join(text_parts).strip(), tool_calls=calls, stop=stop, usage=usage, raw=raw)


# -- facade ------------------------------------------------------------------------------------------
class LLM:
    def __init__(self):
        self.provider = None
        self.available = False
        self.reason = ""
        self.usage = {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "errors": 0, "retries": 0, "last_ms": 0}
        choice = os.getenv("ELI_LLM_PROVIDER", "auto").strip().lower()
        gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
        anthropic_creds = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")) or \
            (Path.home() / ".config" / "anthropic").exists()
        if choice in ("gemini", "anthropic"):
            order = [choice]
        else:
            order = (["gemini", "anthropic"] if gemini_key else ["anthropic", "gemini"])
        errors = []
        for name in order:
            try:
                if name == "gemini":
                    if not gemini_key:
                        errors.append("GEMINI_API_KEY not set")
                        continue
                    self.provider = GeminiProvider(os.getenv("ELI_GEMINI_MODEL", "gemini-3.6-flash"), gemini_key)
                else:
                    if not anthropic_creds:
                        errors.append("ANTHROPIC_API_KEY not set")
                        continue
                    self.provider = AnthropicProvider(os.getenv("ELI_MODEL", "claude-opus-5"), os.getenv("ELI_EFFORT", "medium"),
                                                      int(os.getenv("ELI_MAX_TOKENS", "4096")))
                self.available = True
                log.info("LLM ready: %s / %s", self.provider.name, self.provider.model)
                break
            except Exception as e:
                errors.append(f"{name}: {e}")
        if not self.available:
            self.reason = "No LLM configured (" + "; ".join(errors) + "). Running in offline mode."
            log.warning(self.reason)

    @property
    def name(self) -> str:
        return self.provider.name if self.provider else "none"

    @property
    def model(self) -> str:
        return self.provider.model if self.provider else ""

    async def complete(self, system: tuple[str, str], turns: list[dict], tools: Optional[list[dict]] = None,
                       on_text: OnText = None) -> LLMResponse:
        if not self.available:
            raise RuntimeError(self.reason)
        delays = (0.0, 1.5, 4.0)
        last: Exception = RuntimeError("LLM failed")
        for attempt, delay in enumerate(delays):
            if delay:
                await asyncio.sleep(delay)
                self.usage["retries"] += 1
            t0 = time.time()
            try:
                r = await self.provider.complete(system, turns, tools or [], on_text)
                self.usage["calls"] += 1
                self.usage["input"] += r.usage.get("input", 0)
                self.usage["output"] += r.usage.get("output", 0)
                self.usage["cache_read"] += r.usage.get("cache_read", 0)
                self.usage["last_ms"] = int((time.time() - t0) * 1000)
                return r
            except TransientError as e:
                last = e
                self.usage["errors"] += 1
                log.warning("transient LLM error (attempt %d/%d): %s", attempt + 1, len(delays), e)
        raise last

    def describe_error(self, e: Exception) -> str:
        s = str(e)
        low = s.lower()
        if "api key" in low or "api_key" in low or "401" in low or "authentication" in low or "permission_denied" in low:
            self.available = False
            self.reason = "The API key was rejected. Check backend/.env."
            return "my API key was rejected"
        if isinstance(e, TransientError) or "429" in low or "resource_exhausted" in low:
            return "the model is rate-limited right now; try again in a moment"
        if "connection" in low or "network" in low or "unreachable" in low:
            return "I can't reach the model API (no network?)"
        return s[:160]

    def status(self) -> dict:
        return {"provider": self.name, "model": self.model, "available": self.available, "reason": self.reason, **self.usage}
