"""Main Agent (Eli Core Brain): conversation, routing, the tool-use loop, confirmations,
permissions, streaming replies and the state machine the heart animates.

Flow for every user utterance (voice, desktop panel, phone, or a proactive nudge):
  1. deterministic intents (open X, search, remember, forget, private mode...) -> instant, free
  2. otherwise the LLM with tools served by the specialist agents            -> needs a provider
  3. otherwise the offline responder (OCR + error rules)

States emitted for the heart: idle, listening, thinking, executing, talking, success, error.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional

from .. import config, intents, timeparse
from ..fallback import FallbackResponder
from ..llm import ToolCall, estimate_tokens, image_part, text_part, tool_result_part
from ..tools import HIGH_RISK_TOOLS, INPUT_TOOLS, TOOLS, describe_action
from .automation_agent import APP_COMMANDS, URL_SHORTCUTS, normalize_app, ensure_interactive_desktop
from .cad_bridges import BlenderBridge, SolidWorksBridge
from .design_agent import CAD_REVIEW_CHECKLIST, DRAWING_REVIEW_CHECKLIST
from .vision_agent import window_rect_by_title
from .agentic_core import AgenticOrchestrator, ActionVerifier, SelfCorrector, Plan, PlanStep, VerificationResult

# Commands that always need a human even in trust mode.
DANGEROUS_CMD = re.compile(
    r"(\bformat\b|\brm\s+-rf\b|\brmdir\s+/s\b|\bdel\s+/[sq]\b|\bshutdown\b|\breg\s+delete\b|\bdiskpart\b|\bbcdedit\b|"
    r"remove-item\s+.*-recurse|\bnet\s+user\b|\bicacls\b|\btakeown\b|\bcipher\s+/w\b|\bgit\s+push\s+--force\b|\bgit\s+reset\s+--hard\b)",
    re.I,
)

log = logging.getLogger("eli.agent")

PERSONA = """You are Eli, a small heart-shaped AI companion who lives on the user's Windows desktop. You are warm, direct, lightning-fast and exceptionally capable: a brilliant engineer and pair-programmer who bridges gaps and takes action. You are persistent: you remember the user across sessions and you act, not just answer.

You excel at answering WHAT, WHY, and HOW questions:
- WHAT: Give the exact, concise reality, definition, or current state.
- WHY: Explain the technical mechanism, root cause, or design rationale clearly without fluff.
- HOW: Provide direct, actionable, step-by-step procedures, code, or execute the tools yourself.

Your senses and hands (tools):
- SEE: look_at_screen returns a screenshot, the active window, OCR text and error-looking lines. Call it before describing, debugging or clicking anything. review_design does the same with an engineering checklist for CAD tools.
- CONTROL: open_app, open_url, web_search, play_youtube (auto skips ads), auto_allow_antigravity (auto-approve prompts), focus_window, type_text, press_keys, click, click_text, scroll, wait.
- CODE: create_code_script (writes verified Python/MATLAB/C++/JS code directly to disk, validates AST syntax offline, test-runs it, and opens it directly in VS Code in an active editor tab), open_ide (launch VS Code/MATLAB into project), check_code_errors (offline Python/MATLAB/C/C++ check), scan_project_errors (folder scan), find_files, read_file, write_file (confirmation), list_dir, git_info, run_tests (confirmation). To locate something, call find_files with a few words from its name; if it finds nothing, ask the user where it lives. Workflow for bugs: read the error -> read the relevant file -> explain cause -> propose the exact fix -> after the user's OK write_file -> run_tests -> report. Put corrected snippets on the clipboard with set_clipboard when the user wants to paste them.
- DESIGN: review_design for screenshots of CAD or drawing apps (say what you can and cannot verify from pixels), check_mesh_file for real measurements on STL/OBJ, blender_check_file / blender_check_live / blender_run_python for real Blender geometry, solidworks_check for open SolidWorks document. Plan multi-step engineering work as numbered workflows and save_workflow so you can mentor step by step.
- COMMUNICATE: compose_email (drafts only, with attachments), open_chat. You never send messages.
- REMEMBER: remember / recall / forget, remember_project / open_project for the knowledge graph. Store preferences and facts the user states (third person, one sentence). Use what you remember to tailor answers.
- GUIDE: start_guide runs a narrated, on-screen, step-by-step walkthrough drawn over the app: arrows and rings on exactly what to click, spoken steps, and auto-advance when the screen shows the step was done right. It knows merge_holes (workflow_id) and can guide ANY other hands-on skill in an open app: pass the user's goal in `goal` and Eli plans the steps from the live screen. Prefer it over describing steps in text whenever the user wants to learn or do something in an app in front of them. guide_control drives it.
- SCHEDULE: when the user gives you continuous or future work ("keep checking…", "every hour…", "remind me…", "watch … until …", "later"), call schedule_task immediately so it survives idle time and restarts, then confirm in one line. In a scheduled run, be brief and reply NO_CHANGE when there is nothing to report.
- LEARN: screenshots of errors come with "Past fixes that worked" when memory has them; try those first. After a fix is verified (tests pass, error gone), call remember with kind "solution": what the error was, the cause, the exact fix. That is how you get better at this user's problems.

Rules:
- Act immediately on clear commands; bridge the gaps proactively. When asked to play music, play it on YouTube and keep ad-skipping active.
- When asked to handle Antigravity permissions, automatically click Allow and Submit.
- For code error searches in Python, MATLAB, C, or C++, use offline error checking tools first before asking.
- When asked how to learn 3D modeling from scratch, provide the full structured curriculum (Mental Model, Blender vs CAD, 5 Core Operations, Topology, Materials/Lighting, 3 Projects, Export).
- CRITICAL CODE SCRIPT RULE: When asked to write, create, or run a Python script in VS Code or an IDE, NEVER attempt to click "New File" or type code using type_text or GUI clicks! Doing so opens modal dialogs ("Create File", "Text or Jupyter Notebook") and gets stuck. ALWAYS call create_code_script to write the complete, clean, working code directly to a file on disk, verify its syntax with AST, and open it with open_ide. If a modal "Create File" or "Save As" dialog is open on screen, call dismiss_interferences to close it.
- Replies are spoken aloud: keep them to 1-3 sentences unless the user asks for detail or code. No markdown headers. Code goes in a fenced block only when the user needs to copy it.
- Don't narrate tool calls. Do the work, then say what happened in one line.
- CRITICAL STOP RULE: When the user says "stop", "stop the task", "cancel", "abort", or "halt", stop immediately whatever you are working on: halt all background watchers, cancel tasks, stop speech, and return to idle.
"""


@dataclass
class Pending:
    id: str
    call: ToolCall
    description: str
    created: float


@dataclass
class ToolResult:
    content: Any
    is_error: bool = False


class PermissionBroker:
    """User permission before any screen capture. Answers arrive from the desktop panel or the phone."""

    def __init__(self, hub, settings):
        self.hub = hub
        self.settings = settings
        self.pending: dict[str, asyncio.Future] = {}

    def granted(self) -> bool:
        return self.settings.get("screen_permission") == "granted"

    async def ensure_screen(self, reason: str, timeout: float = 90.0) -> bool:
        if self.granted():
            return True
        loop = asyncio.get_running_loop()
        pid = secrets.token_hex(4)
        fut: asyncio.Future = loop.create_future()
        self.pending[pid] = fut
        self.hub.emit({"type": "permission_request", "id": pid, "scope": "screen", "reason": reason})
        self.hub.notify("Eli asks for screen access", reason)
        try:
            ok = bool(await asyncio.wait_for(fut, timeout))
        except asyncio.TimeoutError:
            ok = False
        finally:
            self.pending.pop(pid, None)
        self.settings.set("screen_permission", "granted" if ok else "denied")
        self.hub.status(screen_permission=self.settings.get("screen_permission"))
        return ok

    def resolve(self, pid: str, allow: bool) -> bool:
        fut = self.pending.get(pid)
        if fut is None or fut.done():
            return False
        fut.set_result(bool(allow))
        return True


class ToolExecutor:
    """Dispatches tool calls to the specialist agents, with risk gating and permissions."""

    def __init__(self, vision, auto, memory, coding, design, comms, hub, settings, broker: PermissionBroker):
        self.vision, self.auto, self.memory = vision, auto, memory
        self.coding, self.design, self.comms = coding, design, comms
        self.hub, self.settings, self.broker = hub, settings, broker
        self.pending: dict[str, Pending] = {}
        self.blender = BlenderBridge()
        self.solidworks = SolidWorksBridge()
        self.guide = None  # GuideAgent, attached by the server
        self.scheduler = None  # Scheduler, attached by the server
        # learning: remember what fixed what
        self.last_error = ""
        self.last_write = ""

    # -- trust mode ---------------------------------------------------------------------------------
    def trust_active(self) -> bool:
        if not self.settings.get("trust_mode"):
            return False
        until = float(self.settings.get("trust_until") or 0)
        if until and time.time() > until:
            self.settings.set("trust_mode", False)
            self.hub.status(trust_mode=False)
            self.hub.toast("Trust mode expired; I'll ask before risky actions again.")
            return False
        return True

    def always_confirm(self, name: str, args: dict) -> bool:
        if name in ("run_command", "run_tests") and DANGEROUS_CMD.search(str(args.get("command", ""))):
            return True
        if name == "click_text" and re.match(r"^(pay|buy|purchase|place order|delete account)$", str(args.get("text", "")).strip(), re.I):
            return True
        return False

    async def execute(self, call: ToolCall) -> ToolResult:
        name, args = call.name, dict(call.args or {})
        desc = describe_action(name, args)
        self.hub.tool(name, desc)
        if name in INPUT_TOOLS and not self.settings.get("automation_enabled", True):
            return ToolResult("Computer control is switched off in Eli's Privacy settings. Ask the user to enable it.", True)
        try:
            title = self.vision.user_window().title
        except Exception:
            title = ""
        risky = name in HIGH_RISK_TOOLS or self.auto.risk_of(name, args, title) == "high"
        if risky and self.trust_active() and not self.always_confirm(name, args):
            self.hub.toast("Auto-approved: " + desc)
            self.memory.audit("trust", "auto-approve", name, desc)
            risky = False
        if risky:
            pid = secrets.token_hex(4)
            self.pending[pid] = Pending(pid, call, desc, time.time())
            self.hub.emit({"type": "confirm_request", "id": pid, "description": desc, "tool": name})
            self.hub.notify("Eli needs your OK", desc)
            return ToolResult(
                f"needs_confirmation: '{desc}' is waiting for the user's approval in the Eli panel (or on their phone). "
                "Tell the user in one short line what you're waiting for, then stop.")
        if name in ("schedule_task", "list_tasks", "cancel_task", "finish_task") and self.scheduler is not None:
            return ToolResult(self._schedule_tool(name, args))
        if name == "start_guide" and self.guide is not None:
            return ToolResult(await self.guide.start(request=str(args.get("goal", "")),
                                                     workflow_id=str(args.get("workflow_id", "")), app=str(args.get("app", ""))))
        if name == "guide_control" and self.guide is not None:
            return ToolResult(await self.guide.control(str(args.get("action", ""))) or "ok")
        if name in ("look_at_screen", "review_design"):
            if self.settings.get("private_mode"):
                return ToolResult("Private mode is on: I won't capture the screen. Ask the user to turn it off if they want me to look.", True)
            ok = await self.broker.ensure_screen(args.get("reason") or "Eli wants to look at your screen to help with your request.")
            if not ok:
                return ToolResult("The user declined screen capture. Ask them to enable 'See screen' in the panel if they want you to look.", True)
        try:
            return await asyncio.to_thread(self.run_sync, name, args)
        except Exception as e:
            log.exception("tool %s failed", name)
            return ToolResult(f"Error running {name}: {e}", True)

    # -- sync dispatch (worker thread) ---------------------------------------------------------------
    def run_sync(self, name: str, args: dict) -> ToolResult:
        ensure_interactive_desktop()
        a, v, m, c, d, k = self.auto, self.vision, self.memory, self.coding, self.design, self.comms

        # vision
        if name in ("look_at_screen", "review_design"):
            frame = v.capture_now()
            if frame.blocked:
                return ToolResult(f"{frame.window.app or 'That app'} is on the user's block list; capture refused.", True)
            b64, media = v.model_image(frame)
            err = frame.error_snippet()
            w, h = v.last_model_size
            info = (f"Active window: {frame.window.describe()}\n"
                    f"Screenshot size: {w}x{h} px (use these coordinates for `click`).\n")
            ctx = c.describe_context(frame.window)
            if ctx:
                info += ctx + "\n"
            if err:
                info += f"Lines that look like errors:\n{err}\n\n"
                self.last_error = err
                past = m.recall(err, k=3, kinds=["solution"], min_score=0.2)
                if past:
                    info += "Past fixes that worked for similar errors:\n" + "\n".join(f"- {p.content}" for p in past) + "\n\n"
            info += f"OCR text in the active window:\n{frame.window_text(3500) or '(no text recognised)'}"
            other = frame.other_text(1000)
            if other:
                info += f"\n\nOther text visible on screen:\n{other}"
            if name == "review_design":
                focus = args.get("focus")
                checklist = d.checklist_for(frame.window) or (CAD_REVIEW_CHECKLIST + "\n" + DRAWING_REVIEW_CHECKLIST)
                info += "\n\n" + checklist + (f"\nUser's focus: {focus}" if focus else "")
                if not d.is_cad(frame.window) and not d.is_drawing(frame.window):
                    info += "\nNote: the active window does not look like a CAD or drawing tool; say so if the screen is not a design."
            return ToolResult([image_part(media, b64), text_part(info)])
        if name == "get_active_window":
            w = v.user_window()
            return ToolResult(w.describe() + ((" | " + c.describe_context(w)) if c.describe_context(w) else ""))
        if name == "list_windows":
            return ToolResult("\n".join(a.list_windows()) or "No windows found.")

        # automation
        if name == "focus_window":
            r = a.focus_window(str(args.get("title", "")))
            self._attend(str(args.get("title", "")))
            return ToolResult(r, r.startswith("No window"))
        if name == "open_app":
            r = a.open_app(str(args.get("name", "")))
            self._attend(str(args.get("name", "")))
            return ToolResult(r, r.startswith("I couldn't") or r.startswith("I don't know"))
        if name == "open_url":
            return ToolResult(a.open_url(str(args.get("url", ""))))
        if name == "web_search":
            return ToolResult(a.web_search(str(args.get("query", "")), str(args.get("engine", "google"))))
        if name == "type_text":
            return ToolResult(a.type_text(str(args.get("text", "")), bool(args.get("press_enter", False))))
        if name == "press_keys":
            return ToolResult(a.press_keys(str(args.get("keys", ""))))
        if name == "move_mouse":
            raw_x, raw_y = float(args.get("x", 0)), float(args.get("y", 0))
            x, y = v.to_screen_coords(raw_x, raw_y)
            return ToolResult(a.move_mouse(x, y))
        if name == "click":
            x, y = v.to_screen_coords(float(args.get("x", 0)), float(args.get("y", 0)))
            return ToolResult(a.click(x, y, str(args.get("button", "left")), bool(args.get("double", False))))
        if name == "click_text":
            r = a.click_text(str(args.get("text", "")), bool(args.get("double", False)))
            return ToolResult(r, r.startswith("I couldn't"))
        if name == "scroll":
            return ToolResult(a.scroll(int(args.get("amount", -3)), args.get("x"), args.get("y")))
        if name == "get_clipboard":
            return ToolResult(a.get_clipboard()[:6000] or "(clipboard is empty)")
        if name == "set_clipboard":
            return ToolResult(a.set_clipboard(str(args.get("text", ""))))
        if name == "run_command":
            return ToolResult(a.run_command(str(args.get("command", "")), cwd=args.get("cwd")))
        if name == "wait":
            return ToolResult(a.wait(float(args.get("seconds", 1))))
        if name == "play_youtube":
            return ToolResult(a.play_youtube(str(args.get("query", ""))))
        if name == "auto_allow_antigravity":
            on = bool(args.get("enabled", True))
            self.settings.set("auto_allow_antigravity", on)
            r = a.start_auto_allow() if on else a.stop_auto_allow()
            return ToolResult(r)

        # coding
        if name == "find_files":
            hits = c.find_files(str(args.get("query", "")))
            return ToolResult(c.format_hits(hits), not hits)
        if name == "read_file":
            return ToolResult(c.read_file(str(args.get("path", "")), start_line=args.get("start_line"), end_line=args.get("end_line")))
        if name == "write_file":
            r = c.write_file(str(args.get("path", "")), str(args.get("content", "")))
            if r.startswith("Wrote"):
                self.last_write = str(args.get("path", ""))
            return ToolResult(r, not r.startswith("Wrote"))
        if name == "list_dir":
            return ToolResult(c.list_dir(str(args.get("path", ""))))
        if name == "git_info":
            return ToolResult(c.git_info(str(args.get("path", ""))))
        if name == "run_tests":
            r = a.run_command(str(args.get("command", "")), cwd=args.get("cwd"), timeout=180)
            if r.startswith("exit 0") and self.last_write:
                # a verified fix: learn it
                first = (self.last_error.strip().splitlines() or ["(unknown error)"])[-1][:160]
                m.remember(f"Fix that worked: '{first}' was resolved by editing {os.path.basename(self.last_write)} "
                           f"and re-running `{args.get('command', '')}` (passed).", kind="solution", importance=0.8, source="agent")
                self.hub.toast("Learned this fix for next time.")
                self.last_write = ""
            return ToolResult(r, not r.startswith("exit 0"))
        if name == "open_ide":
            return ToolResult(c.open_ide(str(args.get("ide", "")), str(args.get("path", ""))))
        if name == "create_code_script":
            a.dismiss_interferences()
            res = c.create_code_script(
                filename=str(args.get("filename", "")),
                code=str(args.get("code", "")),
                language=str(args.get("language", "python")),
                folder=str(args.get("folder", "")),
                run_after=bool(args.get("run_after", True))
            )
            return ToolResult(res.get("summary", "Created and opened script in VS Code."), not res.get("ok", True))
        if name == "dismiss_interferences":
            return ToolResult(a.dismiss_interferences())
        if name == "check_code_errors":
            res = c.check_code_errors(str(args.get("path", "")))
            if res["valid"]:
                return ToolResult(f"{res['file']}: {res['summary']}")
            return ToolResult(f"{res['file']} ({res.get('language', '')}):\n" + "\n".join(res["errors"]), True)
        if name == "scan_project_errors":
            return ToolResult(c.scan_project_errors(str(args.get("folder", ""))))

        # design
        if name == "check_mesh_file":
            return ToolResult(d.check_mesh_file(str(args.get("path", ""))))
        if name == "blender_check_file":
            r = self.blender.check_file(str(args.get("path", "")))
            return ToolResult(r, r.startswith(("Not a", "Blender is not", "Couldn't")))
        if name == "blender_check_live":
            r = self.blender.check_live()
            return ToolResult(r, "isn't running" in r)
        if name == "blender_run_python":
            r = self.blender.run_python(str(args.get("code", "")))
            return ToolResult(r, "isn't running" in r or "Traceback" in r)
        if name == "solidworks_check":
            r = self.solidworks.check_active()
            return ToolResult(r, r.startswith("SolidWorks isn't") or r.startswith("Couldn't"))
        if name == "save_workflow":
            steps = [str(s) for s in (args.get("steps") or [])]
            text = f"Workflow '{args.get('name')}' for: {args.get('goal')}\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
            m.remember(text, kind="workflow", importance=0.7, source="agent")
            return ToolResult("Saved.\n" + text)
        if name == "get_workflow":
            items = m.recall(str(args.get("query", "")), k=3, kinds=["workflow"])
            return ToolResult("\n\n".join(i.content for i in items) or "No saved workflow matches.", not items)

        # communication
        if name == "compose_email":
            return ToolResult(k.compose_email(str(args.get("to", "")), str(args.get("subject", "")), str(args.get("body", "")),
                                              [str(p) for p in (args.get("attachments") or [])]))
        if name == "open_chat":
            return ToolResult(k.open_chat(str(args.get("app", "")), str(args.get("contact", "")), str(args.get("text", ""))))

        # memory
        if name == "remember":
            mm = m.remember(str(args.get("content", "")), str(args.get("kind", "fact")), float(args.get("importance", 0.6) or 0.6), source="agent")
            return ToolResult(f"Remembered ({mm.kind}): {mm.content}")
        if name == "recall":
            items = m.recall(str(args.get("query", "")), k=6)
            conv = m.search_conversation(str(args.get("query", "")), 4)
            out = "\n".join(f"- [{i.kind}] {i.content}" for i in items) or "Nothing relevant in memory."
            if conv:
                out += "\nPast conversation:\n" + "\n".join(f"- {x['role']}: {x['text']}" for x in conv)
            return ToolResult(out)
        if name == "forget":
            n = m.forget(query=str(args.get("query", "")))
            return ToolResult(f"Forgot {n} memor{'y' if n == 1 else 'ies'}.")
        if name == "remember_project":
            e = m.remember_project(str(args.get("name", "")), [str(p) for p in (args.get("paths") or [])],
                                   str(args.get("notes", "")), [str(t) for t in (args.get("tools") or [])])
            return ToolResult(f"Registered project '{e.name}' with {len(args.get('paths') or [])} paths.")
        if name == "open_project":
            return self._open_project(str(args.get("name", "")))
        if name == "query_rag":
            rag = m.rag_context(str(args.get("query", "")), str(args.get("project", "")))
            return ToolResult(rag.get("formatted") or "No relevant RAG context found.")
        if name == "verify_action":
            kind = str(args.get("kind", ""))
            target = str(args.get("target", ""))
            if kind == "window":
                vr = a.focus_window(target)
                return ToolResult(f"Window verification: {vr}")
            if kind in ("file_syntax", "file_exists"):
                vr = c.check_code_errors(target)
                return ToolResult(f"File verification ({vr.get('language', 'code')}): {vr.get('summary', 'verified')}")
            return ToolResult(f"Verified action on {target}.")
        return ToolResult(f"Unknown tool: {name}", True)

    def _schedule_tool(self, name: str, args: dict) -> str:
        sch = self.scheduler
        if name == "list_tasks":
            return sch.summary()
        if name == "cancel_task":
            return "Cancelled." if sch.cancel(int(args.get("job_id", 0))) else "No such job."
        if name == "finish_task":
            return "Marked done." if sch.finish(int(args.get("job_id", 0)), str(args.get("summary", ""))) else "No such job."
        kind = str(args.get("kind", "task"))
        text = str(args.get("text", "")).strip()
        when = None
        if args.get("every_minutes"):
            secs = max(60, int(float(args["every_minutes"]) * 60))
            when = {"kind": "every", "seconds": secs, "at": time.time() + secs}
        elif args.get("delay_minutes") is not None:
            when = {"kind": "once", "at": time.time() + max(5, float(args["delay_minutes"]) * 60)}
        elif args.get("at"):
            when = timeparse.parse_when("at " + str(args["at"]))
        if when is None:
            when = timeparse.parse_when(text)
            text = timeparse.strip_when(text) or text
        if when is None:
            if kind == "watch":
                when = {"kind": "every", "seconds": 120, "at": time.time() + 120}
            else:
                return "I need a time: a delay in minutes, a clock time, or an interval."
        job = sch.add(text, when, kind=kind, until_done=(kind == "watch"))
        return f"Scheduled job #{job['id']}: {sch.describe(job)}"

    def _attend(self, keyword: str) -> None:
        """Tell the overlay to float next to the window Eli just opened or focused."""
        try:
            key = normalize_app(keyword)
            from .automation_agent import APP_WINDOW_KEYWORDS
            rect = window_rect_by_title(APP_WINDOW_KEYWORDS.get(key, keyword))
            if rect:
                self.hub.emit({"type": "attention", "rect": list(rect), "ms": 10000}, to=("desktop",))
        except Exception:
            pass

    def _open_project(self, name: str) -> ToolResult:
        b = self.memory.project_bundle(name)
        lines = []
        if b.get("found"):
            lines.append(f"Project: {b['project']}" + (f" — {b['notes']}" if b.get("notes") else ""))
            if b["folders"]:
                lines.append("Folders: " + ", ".join(b["folders"]))
            if b["files"]:
                lines.append("Files: " + ", ".join(b["files"]))
            if b["tools"]:
                lines.append("Tools: " + ", ".join(b["tools"]))
            target = (b["folders"] or b["files"] or [None])[0]
            if target and os.path.exists(target):
                try:
                    os.startfile(target)
                    lines.append(f"Opened {target}.")
                except OSError as e:
                    lines.append(f"Couldn't open {target}: {e}")
        else:
            hits = self.coding.find_files(name, limit=8)
            if hits:
                lines.append("No registered project by that name, but these match in your folders:\n" + self.coding.format_hits(hits))
                lines.append("Use remember_project to register it once you know which folder it is.")
            else:
                lines.append(f"I don't know a project called '{name}' and found no matching files.")
        if b.get("memories"):
            lines.append("Related memories: " + " | ".join(x["content"] for x in b["memories"][:4]))
        if b.get("conversation"):
            lines.append("Past conversation: " + " | ".join(f"{x['role']}: {x['text'][:100]}" for x in b["conversation"][:3]))
        return ToolResult("\n".join(lines), not b.get("found") and not b.get("memories"))


class MainAgent:
    def __init__(self, hub, settings, memory, vision, auto, coding, design, comms, llm, broker: PermissionBroker, proactive=None):
        self.hub, self.settings, self.memory = hub, settings, memory
        self.vision, self.auto, self.coding, self.design, self.comms = vision, auto, coding, design, comms
        self.llm, self.broker, self.proactive = llm, broker, proactive
        self.speech = None
        self.executor = ToolExecutor(vision, auto, memory, coding, design, comms, hub, settings, broker)
        self.fallback = FallbackResponder(vision, memory, auto, llm, broker)
        self.orchestrator = AgenticOrchestrator(hub, settings, memory, vision, auto, coding, design, comms)
        self.history: list[dict] = []
        self.lock = asyncio.Lock()
        self._last_user_text = ""
        self._stream_id = ""
        self._streamed = ""
        self._spoke_stream = False
        self._abort_requested = False
        if self.settings.get("auto_allow_antigravity", False):
            self.auto.start_auto_allow()

    def attach_speech(self, speech) -> None:
        self.speech = speech

    # -- entry point -------------------------------------------------------------------------------
    async def handle(self, text: str, source: str = "desktop") -> str:
        text = (text or "").strip()
        if not text:
            return ""
        async with self.lock:
            self.memory.private = bool(self.settings.get("private_mode"))
            if source == "scheduler":
                self.hub.tool("scheduler", text.split("]", 1)[0].strip("[") + " running")
            else:
                self.memory.log_message("user", text, source)
                self.hub.transcript("user", text, source)
            self.hub.set_state("thinking")
            self._spoke_stream = False
            try:
                reply = await self._route(text, source)
            except Exception as e:
                log.exception("handle failed")
                self.hub.set_state("error")
                reply = f"Something went wrong on my side: {e}"
            reply = (reply or "Done.").strip()
            if source == "scheduler" and reply.upper().startswith("NO_CHANGE"):
                self.hub.tool("scheduler", "checked; nothing to report")
                if self.speech:
                    self.speech.stop_speaking()
                self.hub.set_state("idle")
                return reply
            self.memory.log_message("eli", reply, source)
            self.hub.transcript("eli", reply, source)
            if source in ("mobile", "scheduler"):
                self.hub.notify("Eli", reply)
            self._speak_or_idle(reply, source)
            return reply

    def _speak_or_idle(self, reply: str, source: str) -> None:
        want_voice = self.speech and (self.settings.get("voice_replies") or source == "voice")
        if want_voice and self._spoke_stream:
            self.speech.flush_stream()
        elif want_voice:
            self.speech.say(reply)
        else:
            self.hub.set_state("idle")
        if source == "voice" and self.speech and hasattr(self.speech, "wake") and self.speech.wake:
            self.speech.wake.extend_conversation(15.0)

    # -- routing -----------------------------------------------------------------------------------
    async def _route(self, text: str, source: str) -> str:
        intent = intents.match(text)
        if intent:
            r = await self._run_intent(intent, text)
            if r is not None:
                return r
        if self.llm.available:
            return await self._llm_turn(text)
        offline = await self._execute_offline_instruction(text)
        if offline:
            return offline
        return await self.fallback.respond(intents.strip_wake(text))

    async def _run_intent(self, intent, raw: str) -> Optional[str]:
        kind, groups = intent
        arg = groups[0] if groups else ""
        m, a = self.memory, self.auto
        if kind == "remember":
            k = intents.classify_kind(arg)
            m.remember(intents.third_person(arg), kind=k, importance=0.8 if k == "preference" else 0.6)
            pm = re.search(r"\b(?:my|the|our)\s+([A-Za-z0-9][\w\- ]{1,40}?)\s+project\b", arg, re.I)
            if pm:
                paths = [p for p in re.findall(r"[A-Za-z]:\\[^\s,;]+", arg) if os.path.exists(p)]
                m.remember_project(pm.group(1).strip(), paths, notes=intents.third_person(arg))
            return f"Got it. I'll remember that {intents.second_person(arg)}."
        if kind == "forget_all":
            n = m.forget(everything=True)
            return f"Done. I deleted all {n} memories, the knowledge graph and the conversation log."
        if kind == "forget_today":
            n = m.forget(since=time.time() - time.time() % 86400)
            return f"Done. I deleted {n} memor{'y' if n == 1 else 'ies'} and today's conversation."
        if kind == "forget":
            n = m.forget(query=arg)
            return f"Forgot {n} memor{'y' if n == 1 else 'ies'} about {arg}." if n else f"I didn't have anything stored about {arg}."
        if kind == "what_remember":
            prefs = m.preferences(10)
            recent = [x for x in m.recent(8) if x.kind != "preference"]
            projects = m.entities("project")[:6]
            if not prefs and not recent and not projects:
                return "I don't remember anything about you yet. Tell me something with 'remember that ...'."
            parts = []
            if prefs:
                parts.append("Preferences: " + " ".join(p.content for p in prefs))
            if projects:
                parts.append("Projects: " + ", ".join(p.name for p in projects) + ".")
            if recent:
                parts.append("Other things: " + " ".join(r.content for r in recent[:5]))
            return " ".join(parts)
        if kind == "skip_ad":
            return await asyncio.to_thread(a.skip_ad_now)
        if kind == "stop_media":
            return await asyncio.to_thread(a.stop_or_pause_media)
        if kind == "resume_media":
            return await asyncio.to_thread(a.resume_media)
        if kind == "close_window":
            return await asyncio.to_thread(a.close_app_or_window, arg)
        if kind == "click_allow":
            return await asyncio.to_thread(a.click_dialog_button)
        if kind == "youtube":
            return await asyncio.to_thread(a.play_youtube, arg)
        if kind == "auto_allow_on":
            self.settings.set("auto_allow_antigravity", True)
            start_res = await asyncio.to_thread(a.start_auto_allow)
            btn_res = await asyncio.to_thread(a.click_dialog_button)
            return f"{start_res} {btn_res}"
        if kind == "auto_allow_off":
            self.settings.set("auto_allow_antigravity", False)
            return await asyncio.to_thread(a.stop_auto_allow)
        if kind == "learn_3d":
            from ..fallback import curriculum_3d_modeling
            return curriculum_3d_modeling()
        if kind == "check_errors":
            return await asyncio.to_thread(self.coding.scan_project_errors, arg)
        if kind == "open_ide" and len(groups) == 2:
            ide, path = groups
            return await asyncio.to_thread(self.coding.open_ide, ide, path)
        if kind == "search":
            return await asyncio.to_thread(a.web_search, arg, "google")
        if kind == "open_project":
            bundle = m.project_bundle(arg)
            if not bundle.get("found") and self.llm.available:
                return None  # let the model search folders / memories and ask what to register
            r = await asyncio.to_thread(self.executor.run_sync, "open_project", {"name": arg})
            return str(r.content)
        if kind == "write_code":
            lang = groups[0].lower() if groups and groups[0] else "python"
            topic = groups[1] if len(groups) > 1 and groups[1] else "Hello World"
            return await self._create_and_open_script_intent(lang, topic)
        if kind == "open":
            key = normalize_app(arg)
            known = key in URL_SHORTCUTS or key in APP_COMMANDS or "." in key
            if known or not self.llm.available:
                r = await asyncio.to_thread(self.executor.run_sync, "open_app", {"name": arg})
                return str(r.content)
            return None
        if kind == "type_in" and len(groups) == 2:
            if not self.settings.get("automation_enabled", True):
                return "Computer control is switched off in my Privacy settings."
            text, app = groups
            r1 = await asyncio.to_thread(a.open_app, app)
            await asyncio.sleep(0.8)
            target = {"the editor": "Visual Studio Code", "vs code": "Visual Studio Code", "vscode": "Visual Studio Code"}.get(app.lower(), app)
            focused = await asyncio.to_thread(a.focus_window, target)
            if not focused.startswith("Switched"):
                return f"{r1} But I couldn't bring {app} to the front, so I didn't type anything."
            await asyncio.sleep(0.4)
            r2 = await asyncio.to_thread(a.type_text, text)
            return f"{r1} {r2}"
        if kind == "observe_on":
            if self.settings.get("private_mode"):
                return "Private mode is on. Turn it off first if you want me to watch the screen."
            ok = await self.broker.ensure_screen("Eli wants to observe your screen every few seconds so it knows what you're working on.")
            self.settings.set("observe_enabled", ok)
            self.hub.status(observe_enabled=ok, screen_permission=self.settings.get("screen_permission"))
            return "Okay, I'm watching your screen now. You'll see the red dot while I do." if ok else "Okay, I won't look at your screen."
        if kind == "observe_off":
            self.settings.set("observe_enabled", False)
            self.hub.status(observe_enabled=False)
            return "Screen observation is off. I'm not looking."
        if kind == "private_on":
            self.settings.set("private_mode", True)
            self.memory.private = True
            self.hub.status(private_mode=True)
            return "Private mode on. I'm not capturing, remembering, or sending anything until you turn it off."
        if kind == "private_off":
            self.settings.set("private_mode", False)
            self.memory.private = False
            self.hub.status(private_mode=False)
            return "Private mode off. I'm back."
        if kind in ("stop_all", "stop_talking"):
            self._abort_requested = True
            if self.speech:
                self.speech.stop_speaking()
            self.settings.set("auto_allow_antigravity", False)
            a.stop_auto_allow()
            a.stop_or_pause_media()
            if self.executor.guide:
                try:
                    await self.executor.guide.control("stop")
                except Exception:
                    pass
            if self.executor.scheduler:
                self.executor.scheduler.cancel_all()
            self.executor.pending.clear()
            self.hub.set_state("idle")
            self.hub.toast("Stopped immediately.")
            return "Stopped immediately. I have halted all active tasks, watchers, and speech."
        if kind == "follow_on":
            self.settings.set("follow_cursor", True)
            self.hub.status(follow_cursor=True)
            return "Okay, I'll follow your cursor."
        if kind == "follow_off":
            self.settings.set("follow_cursor", False)
            self.hub.status(follow_cursor=False)
            return "Okay, I'll stay here."
        if kind == "trust_on":
            self.settings.set("trust_mode", True)
            self.settings.set("trust_until", time.time() + 30 * 60)
            self.hub.status(trust_mode=True, trust_until=self.settings.get("trust_until"))
            return "Trust mode on for 30 minutes: I'll run commands, write files and finish tasks without asking. Destructive commands still need your OK."
        if kind == "trust_off":
            self.settings.set("trust_mode", False)
            self.settings.set("trust_until", 0)
            self.hub.status(trust_mode=False)
            return "Trust mode off. I'll ask before risky actions."
        if kind == "autofix_on":
            self.settings.set("proactive_mode", "auto")
            self.hub.status(proactive_mode="auto")
            return "Okay. When I notice a repeated error I'll look at it and bring you the fix instead of asking first."
        if kind == "autofix_off":
            self.settings.set("proactive_mode", "ask")
            self.hub.status(proactive_mode="ask")
            return "Okay, I'll ask before looking into problems."
        if kind in ("autostart_on", "autostart_off"):
            on = kind == "autostart_on"
            out = await asyncio.to_thread(config.set_autostart, on)
            self.hub.status(autostart=config.autostart_enabled())
            if "enabled" in out or "disabled" in out:
                return "Done. I'll start on my own when you sign in to Windows." if on else "Okay, I won't start automatically anymore; use start.bat when you want me."
            return f"I couldn't change the startup entry: {out}"
        sch = self.executor.scheduler
        if sch is not None and kind in ("remind", "job_every", "job_in", "job_watch", "list_jobs", "cancel_job", "cancel_jobs"):
            return self._job_intent(kind, arg, raw)
        guide = self.executor.guide
        if kind == "guide_start":
            if guide is None:
                return None
            from ..guides import find_workflow
            low = raw.lower()
            explicit = any(p in low for p in ("guide me", "walk me through", "show me how", "teach me", "step by step"))
            if find_workflow(arg) or explicit:
                return await guide.start(request=arg)   # built-in workflow, or plan one from the live screen
            return None  # a bare "help me ...": the model decides (it can still call start_guide)
        if kind in ("guide_next", "guide_repeat", "guide_back", "guide_skip", "guide_alt", "guide_stop"):
            if guide is None or not guide.active:
                if kind == "guide_next" and self.executor.pending:
                    return await self._run_intent(("approve", []), raw)
                if kind == "guide_stop":
                    return "No guide is running."
                return None  # not guiding: let the model interpret it
            return await guide.control(kind.split("_", 1)[1]) or ""
        if kind in ("approve", "deny"):
            approve = kind == "approve"
            # a pending screen-permission request?
            for pid in list(self.broker.pending):
                if self.broker.resolve(pid, approve):
                    return "Okay." if approve else "Okay, I won't look."
            # a pending risky action?
            if self.executor.pending:
                pid = sorted(self.executor.pending, key=lambda k: self.executor.pending[k].created)[0]
                asyncio.create_task(self.confirm(pid, approve))
                return "On it." if approve else "Cancelled."
            if guide is not None and guide.active and approve:
                return await guide.control("next") or ""
            return None  # nothing pending: let the model interpret the words
        return None

    async def _create_and_open_script_intent(self, lang: str = "python", topic: str = "") -> str:
        # Dismiss any stuck file dialogs first (e.g. Create File / Save As modals)
        await asyncio.to_thread(self.auto.dismiss_interferences)

        filename = "hello_world.py"
        code = ""
        if self.llm.available:
            prompt = (
                f"Write a complete, high-quality, production-grade {lang} script for: '{topic or 'Hello World and core utility demonstration'}'. "
                "Include clean modular functions, type hints, docstrings, error handling, and an if __name__ == '__main__': block. "
                f"Output ONLY the complete source code inside ```{lang} ... ``` code fences."
            )
            try:
                r = await self.llm.complete(
                    ("You are an elite software engineer. Write clean, elegant, tested, robust code.", ""),
                    [{"role": "user", "parts": [text_part(prompt)]}],
                    []
                )
                txt = r.text.strip()
                m = re.search(r"```(?:\w+)?\s*\n(.*?)```", txt, re.DOTALL)
                if m:
                    code = m.group(1).strip()
                elif txt:
                    code = txt
            except Exception as e:
                log.warning("LLM script generation failed: %s", e)

        if not code:
            code = (
                '"""\n'
                f'Script: {topic or "Hello World Demonstration"}\n'
                'Created by Eli Autonomous AI Companion.\n'
                '"""\n'
                'import sys\n\n'
                'def greet(name: str = "Mysunat") -> str:\n'
                '    """Return a warm greeting with environment verification."""\n'
                '    return f"Hello, {name}! Your Python script is created, verified, and running successfully."\n\n'
                'def main() -> None:\n'
                '    msg = greet()\n'
                '    print("=" * 60)\n'
                '    print(msg)\n'
                '    print(f"Python Version: {sys.version}")\n'
                '    print("Eli successfully validated AST syntax and opened VS Code.")\n'
                '    print("=" * 60)\n\n'
                'if __name__ == "__main__":\n'
                '    main()\n'
            )

        if topic and topic.strip():
            clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', topic.lower().strip())[:30].strip('_')
            if clean_name:
                filename = f"{clean_name}.py"

        res = await asyncio.to_thread(
            self.coding.create_code_script,
            filename=filename,
            code=code,
            language=lang,
            run_after=True
        )
        return (
            f"I have created `{res.get('filename')}` at `{res.get('path')}`, "
            f"verified syntax ({res.get('syntax_note')}), tested execution ({res.get('execution_output', '')[:80]}), "
            "and opened it directly in VS Code!"
        )

    # -- LLM turn ----------------------------------------------------------------------------------
    async def _llm_turn(self, text: str) -> str:
        self._last_user_text = text
        self.history.append({"role": "user", "parts": await self._user_parts(text)})
        reply = await self._loop()
        self._trim()
        return reply

    async def _user_parts(self, text: str) -> list[dict]:
        parts: list[dict] = []
        if intents.wants_screen(text) and not self.settings.get("private_mode"):
            ok = await self.broker.ensure_screen("Eli wants to look at your screen to answer your question.")
            if ok:
                try:
                    frame = await asyncio.to_thread(self.vision.capture_now)
                    if frame.blocked:
                        parts.append(text_part(f"[Screen capture refused: {frame.window.app or 'the active app'} is on the user's block list.]"))
                    else:
                        await asyncio.to_thread(frame.ocr)
                        b64, media = self.vision.model_image(frame)
                        w, h = self.vision.last_model_size
                        err = frame.error_snippet()
                        ctx = self.coding.describe_context(frame.window)
                        note = (f"[Screenshot attached. Active window: {frame.window.describe()}. Image size {w}x{h} px.]\n"
                                + (ctx + "\n" if ctx else "")
                                + (f"Lines that look like errors:\n{err}\n\n" if err else "")
                                + f"OCR text in the active window:\n{frame.window_text(3000) or '(none)'}")
                        checklist = self.design.checklist_for(frame.window)
                        if checklist:
                            note += "\n\n" + checklist
                        if err:
                            self.executor.last_error = err
                            past = self.memory.recall(err, k=3, kinds=["solution"], min_score=0.2)
                            if past:
                                note += "\n\nPast fixes that worked for similar errors:\n" + "\n".join(f"- {p.content}" for p in past)
                        parts.append(image_part(media, b64))
                        parts.append(text_part(note))
                except Exception as e:
                    log.warning("screenshot for prompt failed: %s", e)
            else:
                parts.append(text_part("[The user declined screen capture for this question.]"))
        elif intents.wants_screen(text):
            parts.append(text_part("[Private mode is on: no screenshot was taken.]"))
        parts.append(text_part(text))
        return parts

    def _system(self) -> tuple[str, str]:
        prefs = self.memory.preferences(12)
        related = [m for m in self.memory.recall(self._last_user_text, k=5) if m.kind != "preference"] if self._last_user_text else []
        projects = self.memory.entities("project")[:8]
        mem_lines = [f"- {p.content}" for p in prefs] + [f"- {m.content}" for m in related]
        try:
            win = self.vision.user_window()
            win_desc = win.describe()
            ctx = self.coding.describe_context(win)
        except Exception:
            win_desc, ctx = "unknown", ""
        dynamic = (
            "What you remember about the user:\n" + ("\n".join(mem_lines) if mem_lines else "- (nothing yet)") +
            ("\nKnown projects: " + ", ".join(p.name for p in projects) if projects else "") +
            f"\n\nNow: {time.strftime('%A %Y-%m-%d %H:%M')}\nActive window: {win_desc}" + (f"\n{ctx}" if ctx else "") +
            f"\nContinuous screen observation: {'on' if self.vision.enabled else 'off'}"
            f"\nPrivate mode: {'on' if self.settings.get('private_mode') else 'off'}"
            f"\nComputer control: {'allowed' if self.settings.get('automation_enabled', True) else 'DISABLED by the user'}"
            f"\nTrust mode (risky actions auto-approved): {'ON' if self.executor.trust_active() else 'off'}"
            f"\nVoice replies: {'on' if self.settings.get('voice_replies') else 'off'}"
        )
        if self.executor.scheduler is not None:
            jobs = self.executor.scheduler.context_lines()
            if jobs:
                dynamic += "\n\nScheduled work you own (persisted; runs automatically; use finish_task/cancel_task to change):\n" + jobs
        # RAG Knowledge & Memory Context
        if self._last_user_text:
            rag = self.memory.rag_context(self._last_user_text)
            if rag.get("formatted"):
                dynamic += "\n\n[RAG MEMORY & VERIFIED SOLUTIONS]\n" + rag["formatted"]
        return PERSONA, dynamic

    def _on_text(self, delta: str) -> None:
        self._streamed += delta
        self.hub.emit({"type": "transcript_delta", "id": self._stream_id, "text": delta}, to=("desktop",))
        if self.speech and self.settings.get("voice_replies"):
            self.speech.stream_text(delta)
            self._spoke_stream = True

    async def _loop(self, max_steps: int = 14) -> str:
        last_text = ""
        tools_ran = 0
        tool_errors = 0
        for _ in range(max_steps):
            if self._abort_requested:
                self._abort_requested = False
                self.hub.set_state("idle")
                return "Task was stopped."
            self._stream_id = secrets.token_hex(3)
            self._streamed = ""
            try:
                resp = await self.llm.complete(self._system(), self.history, TOOLS, on_text=self._on_text)
            except Exception as e:
                why = self.llm.describe_error(e)
                log.warning("LLM call failed: %s", e)
                if self.history and self.history[-1]["role"] == "user":
                    self.history.pop()
                self.hub.set_state("error")
                self._spoke_stream = False
                fallback_res = await self._execute_offline_instruction(self._last_user_text)
                if fallback_res:
                    self.hub.set_state("idle")
                    return fallback_res
                return f"Reasoning API is currently unreachable ({why}). In offline mode, I can still play YouTube music, skip ads, pause/stop playback, open VS Code or MATLAB, auto-allow Antigravity dialogs, check code syntax, or teach 3D modeling."
            parts = ([text_part(resp.text)] if resp.text else []) + \
                    [{"type": "tool_call", "id": c.id, "name": c.name, "args": c.args} for c in resp.tool_calls]
            self.history.append({"role": "assistant", "parts": parts or [text_part("")], "raw": resp.raw, "raw_provider": self.llm.name})
            if resp.text:
                last_text = resp.text
            if resp.stop == "refusal":
                return last_text or "I can't help with that one."
            if not resp.tool_calls:
                break
            if resp.text:
                self.memory.log_message("eli", resp.text)
                self.hub.transcript("eli", resp.text)
            self.hub.set_state("executing")
            results = []
            tools_used = []
            for call in resp.tool_calls:
                if self._abort_requested:
                    self._abort_requested = False
                    self.hub.set_state("idle")
                    return "Task was stopped."
                r = await self.executor.execute(call)
                tools_ran += 1
                tools_used.append(call.name)

                # Empirical Verification and Autonomous Correction Loop
                step_obj = PlanStep(
                    id=call.id,
                    description=describe_action(call.name, call.args),
                    tool=call.name,
                    args=call.args
                )
                v_res = self.orchestrator.evaluate_and_correct(
                    step=step_obj,
                    tool_name=call.name,
                    args=call.args,
                    result=r.content,
                    executor_callable=lambda n, a: self.executor.run_sync(n, a).content
                )

                verif_note = f"\n[Verification: {'PASSED' if v_res.passed else 'FAILED'}. Observations: {v_res.observations}]"
                if not v_res.passed and v_res.diagnosis:
                    verif_note += f"\n[Diagnosis: {v_res.diagnosis}. Suggested Action: {v_res.suggested_action}]"

                combined_content = str(r.content) + verif_note
                tool_errors += int(not v_res.passed)
                results.append(tool_result_part(call, combined_content, not v_res.passed))
            self.history.append({"role": "user", "parts": results})
            self.hub.set_state("thinking")
            last_text = ""
            self._spoke_stream = self._spoke_stream and False
        else:
            last_text = last_text or "I stopped after several steps. Tell me if you want me to keep going."
        if tools_ran and not tool_errors:
            self.hub.set_state("success")
            if self._last_user_text and last_text:
                self.orchestrator.record_successful_resolution(self._last_user_text, last_text[:120], tools_used)
        elif tool_errors and not last_text:
            self.hub.set_state("error")
        if last_text and self._streamed.strip() != last_text.strip():
            self._spoke_stream = False  # streamed text differs (e.g. retry); speak the final text instead
        return last_text or "Done."

    async def _execute_offline_instruction(self, text: str) -> Optional[str]:
        """Executes basic user instructions offline when reasoning model is unreachable or offline."""
        if not text:
            return None
        raw = text.strip()
        low = raw.lower()
        a, c, m, v = self.auto, self.coding, self.memory, self.vision

        # 0. Immediate stop / abort command
        if any(k in low for k in ("stop the task", "stop working", "stop it", "stop that", "cancel the task", "abort", "halt")) or low in ("stop", "cancel"):
            self._abort_requested = True
            if self.speech:
                self.speech.stop_speaking()
            self.settings.set("auto_allow_antigravity", False)
            a.stop_auto_allow()
            a.stop_or_pause_media()
            if self.executor.scheduler:
                self.executor.scheduler.cancel_all()
            self.hub.set_state("idle")
            return "Stopped immediately. I have halted all active tasks, watchers, and speech."

        # 1. Deterministic intents (auto-allow, media, 3d, skip ad, ide, etc.)
        intent = intents.match(raw)
        if intent:
            try:
                res = await self._run_intent(intent, raw)
                if res is not None:
                    return res
            except Exception as e:
                log.warning("offline intent run failed: %s", e)

        # 2. Antigravity dialog / prompt auto-allow and click
        if ("allow" in low or "submit" in low or "proceed" in low) and any(k in low for k in ("antigravity", "dialog", "prompt", "away", "everytime", "every time", "always", "auto")):
            self.settings.set("auto_allow_antigravity", True)
            start_msg = await asyncio.to_thread(a.start_auto_allow)
            btn_res = await asyncio.to_thread(a.click_dialog_button)
            return f"{start_msg} {btn_res}"

        if low in ("click allow", "allow", "click submit", "submit", "click allow and submit"):
            return await asyncio.to_thread(a.click_dialog_button)

        # 3. Ad skipping (handles Whisper 'skip and' / 'skip ad')
        if "skip" in low and any(k in low for k in ("ad", "ads", "and", "video", "it")):
            return await asyncio.to_thread(a.skip_ad_now)

        # 4. Stop / pause media playback
        if any(k in low for k in ("stop", "pause", "freeze", "silence", "kill")) and any(k in low for k in ("song", "music", "video", "playback", "youtube", "playing")):
            return await asyncio.to_thread(a.stop_or_pause_media)
        if "why is it" in low and "playing" in low:
            return await asyncio.to_thread(a.stop_or_pause_media)
        if low.strip() in ("stop", "pause", "stop the music", "stop the song"):
            return await asyncio.to_thread(a.stop_or_pause_media)

        # 5. Resume playback
        if any(k in low for k in ("resume", "unpause", "continue")) and any(k in low for k in ("song", "music", "video", "playback", "youtube", "playing")):
            return await asyncio.to_thread(a.resume_media)

        # 6. YouTube playback
        if "youtube" in low or ("play" in low and any(k in low for k in ("music", "song", "track"))):
            query = re.sub(r"^(?:please )?(?:go and |go to )?(?:search |look up |find |play )+(?:on youtube )?(?:the )?(?:music |song )?", "", raw, flags=re.I)
            query = re.sub(r"(?:on youtube|and skip ads?|and skip the ads?).*$", "", query, flags=re.I).strip()
            if query:
                return await asyncio.to_thread(a.play_youtube, query)

        # 7. IDE launching
        if any(k in low for k in ("open vs code", "open vscode", "open code", "launch vs code")):
            return await asyncio.to_thread(c.open_ide, "vscode")
        if any(k in low for k in ("open matlab", "launch matlab")):
            return await asyncio.to_thread(c.open_ide, "matlab")
        if any(k in low for k in ("open my ide", "open the ide", "open ide")):
            return await asyncio.to_thread(c.open_ide, "ide")

        # 8. Offline code error checking
        if any(k in low for k in ("error", "errors", "syntax")) and any(k in low for k in ("code", "matlab", "python", "c++", "c ", "project")):
            target = "matlab" if "matlab" in low else "python" if "python" in low else "c++" if "c++" in low else "c" if "c " in low else ""
            return await asyncio.to_thread(c.scan_project_errors, target)

        # 9. 3D Modeling learning curriculum
        if any(k in low for k in ("3d model", "3d design", "learn 3d", "learn blender", "steps to learn 3d")):
            from ..fallback import curriculum_3d_modeling
            return curriculum_3d_modeling()

        # 10. Close window or app
        if low.startswith("close ") or "close the window" in low or "close window" in low:
            target = re.sub(r"^close (?:the )?", "", low).strip()
            return await asyncio.to_thread(a.close_app_or_window, target)

        # 11. Open general app
        if low.startswith("open ") or low.startswith("launch "):
            app_name = re.sub(r"^(?:open|launch) (?:up )?(?:the )?", "", low).strip()
            if app_name and len(app_name) < 40 and not any(delim in app_name for delim in (",", ";", "\n", " and ")):
                r = await asyncio.to_thread(self.executor.run_sync, "open_app", {"name": app_name})
                return str(r.content)

        # 12. Screen / error diagnosis offline
        if intents.wants_screen(raw) or any(q in low for q in ("what is wrong", "what's wrong", "why did it fail", "explain error")):
            try:
                frame = await asyncio.to_thread(v.capture_now)
                await asyncio.to_thread(frame.ocr)
                if intents.wants_error_help(raw):
                    return self.fallback.explain_error(frame)
                return self.vision.describe_locally(frame)
            except Exception as e:
                log.warning("offline screen check failed: %s", e)

        # 13. What / Why / How general question from memory
        if any(low.startswith(w) for w in ("what ", "why ", "how ", "where ", "who ")):
            recalled = m.recall(raw, k=2)
            if recalled:
                return "From memory: " + "; ".join(r.content for r in recalled)

        return None

    def _trim(self) -> None:
        """Keep the history under the token budget; drop screenshots from all but the newest two turns."""
        def starts():
            return [i for i, t in enumerate(self.history) if t["role"] == "user" and
                    any(p["type"] == "text" for p in t["parts"]) and not any(p["type"] == "tool_result" for p in t["parts"])]
        s = starts()
        while len(s) > 2 and (estimate_tokens(self.history) > config.CONTEXT_BUDGET or len(s) > config.HISTORY_TURNS):
            self.history = self.history[s[1]:]
            s = starts()
        keep_from = s[-2] if len(s) >= 2 else 0
        for i, t in enumerate(self.history):
            if i >= keep_from or t["role"] != "user":
                continue
            new = []
            for p in t["parts"]:
                if p["type"] == "image":
                    new.append(text_part("[earlier screenshot omitted]"))
                elif p["type"] == "tool_result" and isinstance(p.get("content"), list):
                    p = dict(p)
                    p["content"] = [text_part("[earlier screenshot omitted]") if x["type"] == "image" else x for x in p["content"]]
                    new.append(p)
                else:
                    new.append(p)
            t["parts"] = new
            t.pop("raw", None)  # provider-native copies would still carry the image

    # -- confirmations & nudges ------------------------------------------------------------------------
    async def confirm(self, pid: str, approve: bool) -> None:
        pending = self.executor.pending.pop(pid, None)
        if pending is None:
            self.hub.toast("That request has already been handled.")
            return
        async with self.lock:
            self._spoke_stream = False
            if not approve:
                reply = f"Okay, cancelled: {pending.description}."
                self.memory.audit("user", "decline", pending.call.name, pending.description)
            else:
                self.hub.set_state("executing")
                self.memory.audit("user", "approve", pending.call.name, pending.description)
                result = await asyncio.to_thread(self.executor.run_sync, pending.call.name, dict(pending.call.args or {}))
                text = result.content if isinstance(result.content, str) else "done"
                if self.llm.available and self.history:
                    self.hub.set_state("thinking")
                    self.history.append({"role": "user", "parts": [text_part(
                        f"[The user approved: {pending.description}]\nResult:\n{text}\nContinue the task if steps remain; then tell the user the outcome in one line.")]})
                    reply = await self._loop()
                    self._trim()
                else:
                    reply = f"Done. {text}"[:500]
                    self.hub.set_state("success" if not result.is_error else "error")
            self.memory.log_message("eli", reply)
            self.hub.transcript("eli", reply)
            self.hub.notify("Eli", reply)
            self._speak_or_idle(reply, "desktop")

    def _job_intent(self, kind: str, arg: str, raw: str) -> Optional[str]:
        sch = self.executor.scheduler
        if kind == "list_jobs":
            return sch.summary()
        if kind == "cancel_job":
            return "Cancelled." if sch.cancel(int(arg)) else f"I don't have a job #{arg}."
        if kind == "cancel_jobs":
            n = sch.cancel_all()
            return f"Cancelled {n} scheduled job{'s' if n != 1 else ''}."
        if kind == "remind":
            job = sch.add_from_text(arg, kind="reminder")
            if job is None:
                return "When should I remind you? For example: remind me in 20 minutes to stretch, or remind me at 5pm to call Sam."
            return f"Okay, I'll remind you {timeparse.describe_when({'kind': 'every' if job['every'] else 'once', 'seconds': job['every'] or 0, 'at': job['next_run']})}: {job['text']}."
        if kind in ("job_every", "job_in"):
            job = sch.add_from_text(arg, kind="recurring" if kind == "job_every" else "task")
            if job is None:
                return None
            return f"Scheduled: {sch.describe(job)}. I'll keep doing it until you say “cancel job {job['id']}”." if job["every"] else f"Scheduled: {sch.describe(job)}."
        if kind == "job_watch":
            m = re.search(r"^(.*?)(?:,? (?:and )?(?:tell|let|notify|ping|alert) me(?: know)?(?: when| once| if| as soon as)? (.+))?$", arg, re.I)
            what = (m.group(1) if m else arg).strip()
            goal = (m.group(2) if m and m.group(2) else "").strip()
            text = f"Check {what}." + (f" Goal: tell the user as soon as {goal}." if goal else " Report only meaningful changes.")
            job = sch.add_from_text(text, kind="watch", until_done=True)
            if job is None:
                return None
            return f"I'll keep watching: {what}, every {sch._every_text(job)}" + (f", and tell you when {goal}" if goal else "") + f" (job #{job['id']})."
        return None

    async def nudge_action(self, nid: str, action: str) -> None:
        if action.startswith("job:") and self.executor.scheduler is not None:
            r = self.executor.scheduler.action(action)
            if r:
                self.hub.toast(r)
            return
        if action.startswith("guide:") and self.executor.guide is not None:
            await self.executor.guide.control(action.split(":", 1)[1])
            return
        if not self.proactive:
            return
        payload = self.proactive.resolve(nid, action)
        if not payload:
            return
        if payload.get("kind") == "error":
            await self.handle("Look at my screen: what is the error that keeps coming back, and how do I fix it?", source="proactive")
