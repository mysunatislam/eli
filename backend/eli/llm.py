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
import json
import logging
import os
import re
import secrets
import socket
import time
import urllib.parse
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

    FALLBACK_MODELS = ("gemini-3-flash-preview", "gemini-3.1-flash-lite-preview", "gemini-3.6-flash", "gemini-flash-latest")

    async def complete(self, system: tuple[str, str], turns: list[dict], tools: list[dict], on_text: OnText = None) -> LLMResponse:
        try:
            return await self._complete(system, turns, tools, on_text)
        except Exception as e:
            s = str(e).lower()
            if any(k in s for k in ("not_found", "no longer available", "not found", "429", "quota", "resource_exhausted", "service unavailable", "503")):
                for alt in self.FALLBACK_MODELS:
                    if alt == self.model:
                        continue
                    log.warning("model %s unavailable or quota reached (%s); trying fallback model %s", self.model, str(e)[:120], alt)
                    self.model = alt
                    try:
                        return await self._complete(system, turns, tools, on_text)
                    except Exception as e2:
                        s2 = str(e2).lower()
                        if any(k in s2 for k in ("not_found", "no longer available", "not found", "429", "quota", "resource_exhausted", "service unavailable", "503")):
                            continue
                        raise
            raise

    async def _complete(self, system: tuple[str, str], turns: list[dict], tools: list[dict], on_text: OnText = None) -> LLMResponse:
        T = self.T
        config = T.GenerateContentConfig(
            system_instruction="\n\n".join(s for s in system if s),
            temperature=0.2,
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


# -- OpenAI-Compatible (Ollama, Groq, OpenRouter, LM Studio, LocalAI) ----------------
class OpenAICompatibleProvider:
    """Universal provider for free & local models: Ollama, Groq, OpenRouter, LM Studio, etc."""
    def __init__(self, name: str, base_url: str, model: str, api_key: str = "none"):
        import httpx
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or "none"
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(90.0, connect=10.0)
        )

    def _messages(self, system: tuple[str, str], turns: list[dict]) -> list[dict]:
        stable, dynamic = system
        sys_prompt = "\n\n".join(s for s in (stable, dynamic) if s)
        messages = []
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})
        for t in turns:
            role = t.get("role", "user")
            text_chunks = []
            tool_calls = []
            for p in t.get("parts", []):
                ptype = p.get("type")
                if ptype == "text":
                    text_chunks.append(p.get("text", ""))
                elif ptype == "tool_call":
                    tool_calls.append({
                        "id": p.get("id", f"call_{secrets.token_hex(4)}"),
                        "type": "function",
                        "function": {
                            "name": p.get("name", ""),
                            "arguments": json.dumps(p.get("args", {}))
                        }
                    })
                elif ptype == "tool_result":
                    c = p.get("content", "")
                    if isinstance(c, list):
                        c = "\n".join(x.get("text", "") for x in c if x.get("type") == "text")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": p.get("id", ""),
                        "content": str(c)
                    })
            if role == "assistant":
                msg = {"role": "assistant"}
                if text_chunks:
                    msg["content"] = "\n".join(text_chunks)
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                if "content" in msg or "tool_calls" in msg:
                    messages.append(msg)
            elif role == "user" and text_chunks:
                messages.append({"role": "user", "content": "\n".join(text_chunks)})
        return messages

    def _tools(self, tools: list[dict]) -> list[dict]:
        out = []
        for t in tools:
            out.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}}
                }
            })
        return out

    async def complete(self, system: tuple[str, str], turns: list[dict], tools: list[dict], on_text: OnText = None) -> LLMResponse:
        messages = self._messages(system, turns)
        body: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False
        }
        if tools:
            body["tools"] = self._tools(tools)
            body["tool_choice"] = "auto"

        try:
            resp = await self.client.post("/chat/completions", json=body)
            if resp.status_code == 429:
                raise TransientError("Rate limit / quota exceeded")
            if resp.status_code >= 500:
                raise TransientError(f"Server error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            if _transient(e):
                raise TransientError(str(e)) from e
            raise

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        if on_text and text:
            on_text(text)

        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            call_id = tc.get("id") or f"call_{secrets.token_hex(4)}"
            fname = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except Exception:
                args = {}
            if fname:
                calls.append(ToolCall(call_id, fname, args))

        usage = data.get("usage") or {}
        u = {"input": usage.get("prompt_tokens", 0), "output": usage.get("completion_tokens", 0), "cache_read": 0}
        stop = "tool" if calls else ("max_tokens" if choice.get("finish_reason") == "length" else "end")
        return LLMResponse(text=text.strip(), tool_calls=calls, stop=stop, usage=u, raw=msg)


# -- Offline Local Engine (100% Free, Zero API Keys, Always Ready) -----------------
class OfflineLocalProvider:
    """100% free, offline, local engine that requires no external API keys or network connection."""
    name = "offline"
    model = "eli-offline-engine"

    async def complete(self, system: tuple[str, str], turns: list[dict], tools: list[dict], on_text: OnText = None) -> LLMResponse:
        last_user = ""
        for t in reversed(turns):
            if t.get("role") == "user":
                for p in t.get("parts", []):
                    if p.get("type") == "text":
                        last_user = p.get("text", "")
                        break
                if last_user:
                    break

        low = last_user.lower().strip()
        tools_dict = {t["name"]: t for t in tools}

        # 1. Coding task -> create_code_script tool
        if any(k in low for k in ("write", "create", "make", "generate", "code", "script", "program")) and any(k in low for k in ("python", "code", "matlab", "script", "program", "fibonacci", "prime", "math", "calculator", "game")):
            if "create_code_script" in tools_dict:
                lang = "matlab" if "matlab" in low else "python"
                topic = re.sub(r"^(?:(?:can you |could you |please )*(?:open (?:vs code|vscode|the editor) (?:and |to )?)?)*(?:write|create|make|generate|type|code)(?: (?:a|an|some))?(?: (?:basic|sample|new))?(?: (?:python|matlab|c\+\+|c))?\s*(?:script|code|program|file)?(?: (?:in|into|for) (?:vs code|vscode))?(?: (?:about|for|to|like) )?", "", last_user, flags=re.I).strip()
                fname = f"{re.sub(r'[^a-zA-Z0-9_]', '_', topic.lower()[:25]).strip('_') or 'offline_script'}.py"
                call = ToolCall(f"call_{secrets.token_hex(4)}", "create_code_script", {
                    "filename": fname,
                    "code": "",
                    "language": lang,
                    "run_after": True
                })
                reply_text = f"Generating and verifying {lang.title()} code for '{topic or 'task'}' in offline mode..."
                if on_text:
                    on_text(reply_text)
                return LLMResponse(text=reply_text, tool_calls=[call], stop="tool")

        # 2. Syntax / Error check -> scan_project_errors
        if any(k in low for k in ("syntax", "error", "errors", "check code", "check my code")):
            if "scan_project_errors" in tools_dict:
                target = "matlab" if "matlab" in low else "python" if "python" in low else ""
                call = ToolCall(f"call_{secrets.token_hex(4)}", "scan_project_errors", {"folder_path": target})
                return LLMResponse(text="Scanning project files for syntax errors offline...", tool_calls=[call], stop="tool")

        # 3. Open IDE / App -> open_ide / open_app
        if any(k in low for k in ("open vs code", "open vscode", "launch vs code")):
            if "open_ide" in tools_dict:
                call = ToolCall(f"call_{secrets.token_hex(4)}", "open_ide", {"ide": "vscode"})
                return LLMResponse(text="Opening Visual Studio Code...", tool_calls=[call], stop="tool")
        if any(k in low for k in ("open matlab", "launch matlab")):
            if "open_ide" in tools_dict:
                call = ToolCall(f"call_{secrets.token_hex(4)}", "open_ide", {"ide": "matlab"})
                return LLMResponse(text="Opening MATLAB...", tool_calls=[call], stop="tool")

        # 4. Web & Social Media Navigation -> open_url / web_search
        if any(k in low for k in ("facebook", "messenger", "fb", "youtube", "google", "website", "browse")):
            if "facebook" in low or "messenger" in low:
                target_name = re.sub(r".*?(?:search for|search|find|look for|message|text)\s+", "", last_user, flags=re.I).strip()
                target_name = re.sub(r"(?:on facebook|on messenger|in messenger|in facebook).*$", "", target_name, flags=re.I).strip()
                if target_name and target_name.lower() not in ("facebook", "messenger", "fb"):
                    q = urllib.parse.quote_plus(target_name)
                    url = f"https://www.facebook.com/search/top?q={q}"
                    msg = f"Opening Facebook and searching for '{target_name}'..."
                else:
                    url = "https://www.facebook.com/messages/t/"
                    msg = "Opening Facebook Messenger..."
                if "open_url" in tools_dict:
                    call = ToolCall(f"call_{secrets.token_hex(4)}", "open_url", {"url": url})
                    if on_text: on_text(msg)
                    return LLMResponse(text=msg, tool_calls=[call], stop="tool")

            if "youtube" in low:
                query = re.sub(r"^(?:(?:can you |please )*(?:play|search|find|listen to)?\s*(?:on youtube)?\s*)", "", last_user, flags=re.I).strip()
                if "play_youtube" in tools_dict and query:
                    call = ToolCall(f"call_{secrets.token_hex(4)}", "play_youtube", {"query": query})
                    msg = f"Playing '{query}' on YouTube..."
                    if on_text: on_text(msg)
                    return LLMResponse(text=msg, tool_calls=[call], stop="tool")

            if "google" in low or "search" in low:
                query = re.sub(r"^(?:(?:can you |please )*(?:search|google|look up)(?: for)?\s*)", "", last_user, flags=re.I).strip()
                if "web_search" in tools_dict and query:
                    call = ToolCall(f"call_{secrets.token_hex(4)}", "web_search", {"query": query, "engine": "google"})
                    msg = f"Searching Google for '{query}'..."
                    if on_text: on_text(msg)
                    return LLMResponse(text=msg, tool_calls=[call], stop="tool")

        # 5. Open General Apps -> open_app
        if low.startswith("open ") or low.startswith("launch "):
            app_name = re.sub(r"^(?:open|launch)\s+(?:the\s+)?", "", last_user, flags=re.I).strip()
            if app_name and len(app_name) < 40 and "open_app" in tools_dict:
                call = ToolCall(f"call_{secrets.token_hex(4)}", "open_app", {"name": app_name})
                msg = f"Opening {app_name}..."
                if on_text: on_text(msg)
                return LLMResponse(text=msg, tool_calls=[call], stop="tool")

        # 6. Recall / Memory query -> query_rag
        if any(k in low for k in ("remember", "memory", "solution", "how did i", "past")):
            if "query_rag" in tools_dict:
                call = ToolCall(f"call_{secrets.token_hex(4)}", "query_rag", {"query": last_user})
                return LLMResponse(text="Recalling from local encrypted memory...", tool_calls=[call], stop="tool")

        # 7. Conversational & Informational Reply
        if any(w in low for w in ("hello", "hi", "hey", "who are you", "what can you do", "help")):
            reply = (
                "Hello! I'm Eli, your autonomous desktop AI companion. "
                "I can write and test code in VS Code or MATLAB, check syntax, skip YouTube ads, control media, auto-approve dialogs, and manage your local memory. "
                "For full open-ended chat and reasoning for free, you can connect Groq (free Llama 3.3 70B at console.groq.com) or run Ollama locally!"
            )
        elif any(w in low for w in ("how", "what", "why", "explain", "tell me")):
            reply = (
                f"I heard your question about '{last_user[:60]}'. "
                "I am currently operating in zero-cost local mode. To get deep conversational reasoning and explanations for free just like Gemini, you can drop a free Groq key in .env (console.groq.com) or run Ollama. "
                "In the meantime, I can generate code, open your IDE, and run local tasks for you!"
            )
        else:
            reply = (
                f"Understood: '{last_user}'. I can execute this locally on your desktop. "
                "For unlimited free generative chat and reasoning without paid keys, you can connect Groq (free Llama 3.3 70B) or local Ollama!"
            )
        if on_text:
            on_text(reply)
        return LLMResponse(text=reply, tool_calls=[], stop="end")


def _is_local_service_alive(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.15):
            return True
    except Exception:
        return False


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
        groq_key = os.getenv("GROQ_API_KEY", "")
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "")

        # Check local/free services
        ollama_alive = _is_local_service_alive(11434)
        lmstudio_alive = _is_local_service_alive(1234)

        order = []
        if choice in ("ollama", "groq", "openrouter", "lmstudio", "gemini", "anthropic", "offline"):
            order = [choice]
        else:
            # Auto order prioritizes local and free zero-key options!
            if ollama_alive:
                order.append("ollama")
            if lmstudio_alive:
                order.append("lmstudio")
            if groq_key:
                order.append("groq")
            if openrouter_key:
                order.append("openrouter")
            if gemini_key:
                order.append("gemini")
            if anthropic_creds:
                order.append("anthropic")
            order.append("offline")

        errors = []
        for name in order:
            try:
                if name == "ollama":
                    base = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
                    model = os.getenv("ELI_OLLAMA_MODEL", "llama3.2")
                    self.provider = OpenAICompatibleProvider("ollama", f"{base}/v1", model, "ollama")
                elif name == "lmstudio":
                    base = os.getenv("LOCAL_LLM_URL", "http://127.0.0.1:1234")
                    model = os.getenv("LOCAL_LLM_MODEL", "local-model")
                    self.provider = OpenAICompatibleProvider("lmstudio", f"{base}/v1", model, "not-needed")
                elif name == "groq":
                    if not groq_key:
                        continue
                    model = os.getenv("ELI_GROQ_MODEL", "llama-3.3-70b-versatile")
                    self.provider = OpenAICompatibleProvider("groq", "https://api.groq.com/openai/v1", model, groq_key)
                elif name == "openrouter":
                    if not openrouter_key:
                        continue
                    model = os.getenv("ELI_OPENROUTER_MODEL", "meta-llama/llama-3.2-3b-instruct:free")
                    self.provider = OpenAICompatibleProvider("openrouter", "https://openrouter.ai/api/v1", model, openrouter_key)
                elif name == "gemini":
                    if not gemini_key:
                        continue
                    self.provider = GeminiProvider(os.getenv("ELI_GEMINI_MODEL", "gemini-3.6-flash"), gemini_key)
                elif name == "anthropic":
                    if not anthropic_creds:
                        continue
                    self.provider = AnthropicProvider(os.getenv("ELI_MODEL", "claude-opus-5"), os.getenv("ELI_EFFORT", "medium"),
                                                      int(os.getenv("ELI_MAX_TOKENS", "4096")))
                elif name == "offline":
                    self.provider = OfflineLocalProvider()

                if self.provider:
                    self.available = True
                    log.info("LLM ready: %s / %s", self.provider.name, self.provider.model)
                    break
            except Exception as e:
                errors.append(f"{name}: {e}")

        if not self.provider:
            self.provider = OfflineLocalProvider()
            self.available = True
            log.info("LLM ready: offline / eli-offline-engine")

    @property
    def name(self) -> str:
        return self.provider.name if self.provider else "offline"

    @property
    def model(self) -> str:
        return self.provider.model if self.provider else "eli-offline-engine"

    async def complete(self, system: tuple[str, str], turns: list[dict], tools: Optional[list[dict]] = None,
                       on_text: OnText = None) -> LLMResponse:
        delays = (0.0, 1.5, 3.0)
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
            except Exception as e:
                s = str(e).lower()
                # If network fails, DNS fails (getaddrinfo), quota exceeded, or rate limit:
                # Automatically and seamlessly fall back to the offline local provider!
                if any(k in s for k in ("getaddrinfo", "connection", "unreachable", "429", "quota", "resource_exhausted")):
                    log.warning("Online LLM unreachable (%s); seamlessly falling back to offline local engine.", e)
                    offline_prov = OfflineLocalProvider()
                    return await offline_prov.complete(system, turns, tools or [], on_text)
                if isinstance(e, TransientError) or _transient(e):
                    last = e
                    self.usage["errors"] += 1
                    log.warning("transient LLM error (attempt %d/%d): %s", attempt + 1, len(delays), e)
                else:
                    raise e
        # If all retries exhausted, seamless offline fallback rather than crashing
        log.warning("All online retries exhausted (%s); falling back to offline local engine.", last)
        offline_prov = OfflineLocalProvider()
        return await offline_prov.complete(system, turns, tools or [], on_text)

    def describe_error(self, e: Exception) -> str:
        s = str(e)
        low = s.lower()
        if "api key" in low or "api_key" in low or "401" in low or "authentication" in low or "permission_denied" in low:
            return "my API key was rejected"
        if isinstance(e, TransientError) or "429" in low or "resource_exhausted" in low:
            return "the model is rate-limited right now; switching to offline mode"
        if "connection" in low or "network" in low or "unreachable" in low or "getaddrinfo" in low:
            return "network unreachable; switched to offline local mode"
        return s[:160]

    def status(self) -> dict:
        return {"provider": self.name, "model": self.model, "available": self.available, "reason": self.reason, **self.usage}
