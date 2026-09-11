"""Tool definitions the main agent exposes to the model, grouped by the agent that serves them,
plus human-readable descriptions used in confirmation prompts."""
from __future__ import annotations


def _t(name: str, description: str, props: dict, required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required or []}}


# --- Vision agent --------------------------------------------------------------------------------------
VISION_TOOLS = [
    _t("look_at_screen",
       "Take a fresh screenshot of the user's primary monitor and return it with the active window, OCR text and "
       "error-looking lines. Call this before describing, debugging or clicking anything on screen. Pixel coordinates "
       "you read from the image are valid input for `click`.",
       {"reason": {"type": "string", "description": "One short line on why you need to look (shown to the user)."}}),
    _t("get_active_window", "Return the title and app of the foreground window (cheap; no screenshot).", {}),
    _t("list_windows", "List the titles of open windows.", {}),
]

# --- Automation agent ---------------------------------------------------------------------------------
AUTOMATION_TOOLS = [
    _t("focus_window", "Bring a window to the front by a substring of its title.", {"title": {"type": "string"}}, ["title"]),
    _t("open_app", "Launch an application or a well-known site by name (chrome, vs code, notepad, file explorer, terminal, "
       "calculator, youtube, gmail, github ...). Also accepts a domain like example.com.", {"name": {"type": "string"}}, ["name"]),
    _t("open_url", "Open a URL in the default browser.", {"url": {"type": "string"}}, ["url"]),
    _t("web_search", "Search the web and show results in the browser.",
       {"query": {"type": "string"}, "engine": {"type": "string", "enum": ["google", "youtube", "bing", "duckduckgo"]}}, ["query"]),
    _t("type_text", "Type text into the focused window with the keyboard. Focus the right window first. Set press_enter only when "
       "the user asked to submit/send.", {"text": {"type": "string"}, "press_enter": {"type": "boolean"}}, ["text"]),
    _t("press_keys", "Press a key or shortcut, e.g. 'enter', 'ctrl+s', 'alt+tab', 'ctrl+shift+p'. Chain with ' then '.",
       {"keys": {"type": "string"}}, ["keys"]),
    _t("click", "Click at pixel coordinates from the most recent look_at_screen image.",
       {"x": {"type": "number"}, "y": {"type": "number"}, "button": {"type": "string", "enum": ["left", "right", "middle"]},
        "double": {"type": "boolean"}}, ["x", "y"]),
    _t("click_text", "Find visible text on screen with OCR (a button label, menu item, link) and click it. More reliable than coordinates.",
       {"text": {"type": "string"}, "double": {"type": "boolean"}}, ["text"]),
    _t("scroll", "Scroll the mouse wheel. Positive = up, negative = down (in notches).",
       {"amount": {"type": "integer"}, "x": {"type": "integer"}, "y": {"type": "integer"}}, ["amount"]),
    _t("get_clipboard", "Read the clipboard text (useful when the user copied code).", {}),
    _t("set_clipboard", "Put text on the clipboard, e.g. corrected code for the user to paste.", {"text": {"type": "string"}}, ["text"]),
    _t("run_command", "Run a shell command on the user's PC and return its output. ALWAYS requires the user's confirmation; "
       "prefer other tools.", {"command": {"type": "string"}, "cwd": {"type": "string"}}, ["command"]),
    _t("move_mouse", "Move the mouse cursor smoothly across the screen to (x, y) coordinates.",
       {"x": {"type": "number"}, "y": {"type": "number"}}, ["x", "y"]),
    _t("wait", "Pause for up to 5 seconds (e.g. after opening an app).", {"seconds": {"type": "number"}}, ["seconds"]),
    _t("play_youtube", "Search and play music or a video on YouTube with background automated skipping of ads.",
       {"query": {"type": "string"}}, ["query"]),
    _t("auto_allow_antigravity", "Start or stop watching for Antigravity permission prompts to automatically click Allow/Submit.",
       {"enabled": {"type": "boolean"}}, ["enabled"]),
    _t("dismiss_interferences", "Dismiss any stuck modal file dialogs (Create File, Save As) or popups on screen by sending Escape or closing the dialog window.", {}),
]

# --- Coding agent --------------------------------------------------------------------------------------
CODING_TOOLS = [
    _t("create_code_script", "Create a complete, verified code script (Python, MATLAB, C++, JavaScript), save it directly to disk in the user's workspace/projects folder, verify syntax offline with AST, test-run it, and open it directly in VS Code so it is immediately active in an editor tab.",
       {"filename": {"type": "string", "description": "e.g. 'hello_world.py' or 'data_pipeline.py'"},
        "code": {"type": "string", "description": "The complete source code"},
        "language": {"type": "string", "enum": ["python", "matlab", "javascript", "cpp", "c"], "description": "Script language, defaults to python"},
        "folder": {"type": "string", "description": "Optional destination folder path"},
        "run_after": {"type": "boolean", "description": "Whether to test-run the script with python and verify execution output"}},
       ["filename", "code"]),
    _t("find_files", "Search the user's project folders (Documents, Desktop, Downloads, recent VS Code workspaces) for files or "
       "folders whose name contains all the given words. Newest first.", {"query": {"type": "string"}}, ["query"]),
    _t("read_file", "Read a text file (with line numbers). Optional line range.",
       {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, ["path"]),
    _t("write_file", "Write the complete new content of a file (a .eli.bak backup is kept). Requires the user's confirmation. "
       "Use for applying a fix after you explained it.", {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _t("list_dir", "List a folder.", {"path": {"type": "string"}}, ["path"]),
    _t("git_info", "git status, diff --stat and recent commits for the repository containing a path.", {"path": {"type": "string"}}, ["path"]),
    _t("run_tests", "Run the project's test or build command (e.g. 'pytest -q', 'npm test', 'python main.py') in a folder and return "
       "the output. Requires the user's confirmation. Use it to verify a fix.", {"command": {"type": "string"}, "cwd": {"type": "string"}}, ["command"]),
    _t("open_ide", "Launch an IDE (VS Code or MATLAB) optionally opening a workspace folder or project path.",
       {"ide": {"type": "string", "enum": ["vscode", "matlab"]}, "path": {"type": "string"}}, ["ide"]),
    _t("check_code_errors", "Run offline static syntax and error analysis on a Python (.py), MATLAB (.m), C, or C++ file.",
       {"path": {"type": "string"}}, ["path"]),
    _t("scan_project_errors", "Scan an entire project folder offline for syntax and structural errors in Python, MATLAB, C, and C++ files.",
       {"folder": {"type": "string"}}, []),
]

# --- Design agent ---------------------------------------------------------------------------------------
DESIGN_TOOLS = [
    _t("review_design", "Take a screenshot of the CAD tool on screen and return it with an engineering review checklist "
       "(topology, overlaps, dimensions, manufacturability, assembly). Use for 'check my design' requests.",
       {"focus": {"type": "string", "description": "What the user cares about, e.g. '3D printing', 'screw holes', 'assembly fit'."}}),
    _t("check_mesh_file", "Measure an STL/OBJ/PLY/3MF mesh file: watertightness, open and non-manifold edges, degenerate faces, "
       "bodies, bounding box, volume, overhang fraction. Real numbers, unlike a screenshot.", {"path": {"type": "string"}}, ["path"]),
    _t("save_workflow", "Save a step-by-step workflow you planned for a goal so it can be followed and reused "
       "(e.g. 'design a 3D-printable enclosure').",
       {"name": {"type": "string"}, "goal": {"type": "string"}, "steps": {"type": "array", "items": {"type": "string"}}}, ["name", "goal", "steps"]),
    _t("get_workflow", "Retrieve a saved workflow by name or topic.", {"query": {"type": "string"}}, ["query"]),
    _t("blender_check_file", "Validate every mesh in a .blend file with Blender itself (headless): non-manifold and boundary edges, loose "
       "geometry, zero-area faces, n-gons, flipped normals, unapplied scale, modifiers. Real numbers with Blender menu fixes.",
       {"path": {"type": "string"}}, ["path"]),
    _t("blender_check_live", "Run the same mesh validation inside the currently open Blender session (needs the Eli Bridge add-on).", {}),
    _t("blender_run_python", "Execute Python (bpy) inside the running Blender session via the Eli Bridge add-on, e.g. to fix normals, apply "
       "scale, or remove doubles. Requires the user's confirmation. Print what you changed.", {"code": {"type": "string"}}, ["code"]),
    _t("schedule_task", "Schedule continuous or future work so it survives idle time and restarts: a reminder, a task to run later, a "
       "recurring check, or a watch that repeats until finish_task is called. Use this whenever the user asks for something later, "
       "repeatedly, or 'until' a condition.",
       {"text": {"type": "string", "description": "What to do or remind, as an instruction to yourself."},
        "kind": {"type": "string", "enum": ["reminder", "task", "recurring", "watch"]},
        "delay_minutes": {"type": "number"}, "at": {"type": "string", "description": "Clock time like 17:30 or 5pm (today/tomorrow)."},
        "every_minutes": {"type": "number"}}, ["text", "kind"]),
    _t("list_tasks", "List scheduled jobs (reminders, recurring checks, watches).", {}),
    _t("cancel_task", "Cancel a scheduled job by id.", {"job_id": {"type": "integer"}}, ["job_id"]),
    _t("finish_task", "Mark a watch/recurring job as achieved so it stops running.", {"job_id": {"type": "integer"}, "summary": {"type": "string"}}, ["job_id"]),
    _t("start_guide", "Start an on-screen, narrated, step-by-step guide: Eli highlights exactly where to click, speaks each step, "
       "watches the screen and advances only when the step was done right. For the built-in merge_holes workflow pass workflow_id. "
       "For ANY other skill the user wants to learn hands-on in an app that's open (CAD, Blender, editors, anything), pass their "
       "goal in `goal` - Eli looks at the screen and plans the steps itself. Prefer this over describing steps in text whenever "
       "the user wants to DO something in an app in front of them.",
       {"goal": {"type": "string", "description": "What the user wants to do or learn, in their words."},
        "workflow_id": {"type": "string", "enum": ["merge_holes"]},
        "app": {"type": "string", "description": "App name if the user said one (e.g. freecad, blender)."}}),
    _t("guide_control", "Control the running guide: next, repeat, back, skip, alt (alternative), stop.",
       {"action": {"type": "string", "enum": ["next", "repeat", "back", "skip", "alt", "stop"]}}, ["action"]),
    _t("solidworks_check", "Rebuild the active SolidWorks document and report feature errors/warnings, mass properties, and interferences "
       "(assemblies). Needs SolidWorks running with a document open.", {}),
]

# --- Communication agent ---------------------------------------------------------------------------------
COMMUNICATION_TOOLS = [
    _t("compose_email", "Open an email draft with fields pre-filled and files attached (Outlook if installed, otherwise Gmail). "
       "This NEVER sends; the user reviews and sends. Use find_files first to locate attachments.",
       {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
        "attachments": {"type": "array", "items": {"type": "string"}, "description": "Absolute file paths"}}),
    _t("open_chat", "Open a chat app or web client (whatsapp, slack, discord, teams, telegram, messenger). With `text`, the message "
       "is put on the clipboard for the user to paste; nothing is sent.",
       {"app": {"type": "string"}, "contact": {"type": "string"}, "text": {"type": "string"}}, ["app"]),
]

# --- Memory agent --------------------------------------------------------------------------------------
MEMORY_TOOLS = [
    _t("remember", "Store something about the user for future sessions (preferences, facts, solutions that worked).",
       {"content": {"type": "string", "description": "One clear sentence, third person: 'The user prefers Python.'"},
        "kind": {"type": "string", "enum": ["preference", "fact", "episode", "task", "solution", "project"]},
        "importance": {"type": "number", "description": "0..1"}}, ["content"]),
    _t("recall", "Search long-term memory (preferences, facts, solutions, phone photos, past conversations).", {"query": {"type": "string"}}, ["query"]),
    _t("forget", "Delete memories matching a query (hard delete). Use when the user asks you to forget something.", {"query": {"type": "string"}}, ["query"]),
    _t("remember_project", "Register a project in the knowledge graph with its files/folders and notes, so 'open my X project' works later.",
       {"name": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}, "notes": {"type": "string"},
        "tools": {"type": "array", "items": {"type": "string"}}}, ["name"]),
    _t("open_project", "Look up a project in the knowledge graph (files, folders, notes, related memories and past conversations) and "
       "open its main folder. If unknown, falls back to searching project folders by name.", {"name": {"type": "string"}}, ["name"]),
    _t("query_rag", "Perform a comprehensive RAG search across personal memory, past verified solutions, user preferences, and the project knowledge graph.",
       {"query": {"type": "string"}, "project": {"type": "string"}}, ["query"]),
    _t("verify_action", "Explicitly verify the empirical result of an action (e.g. check if a window is open, file syntax is clean, or tests pass).",
       {"target": {"type": "string", "description": "What to verify: an app name, window title, file path, or command output"},
        "kind": {"type": "string", "enum": ["window", "file_syntax", "file_exists", "test"]}}, ["target", "kind"]),
]

TOOLS: list[dict] = VISION_TOOLS + AUTOMATION_TOOLS + CODING_TOOLS + DESIGN_TOOLS + COMMUNICATION_TOOLS + MEMORY_TOOLS
INPUT_TOOLS = {"type_text", "press_keys", "click", "click_text", "scroll", "move_mouse"}
HIGH_RISK_TOOLS = {"run_command", "run_tests", "write_file", "blender_run_python"}


def describe_action(name: str, args: dict) -> str:
    a = args or {}
    if name == "query_rag":
        return f"search RAG memory for: {a.get('query', '')}"
    if name == "verify_action":
        return f"verify {a.get('kind', '')} on {a.get('target', '')}"
    if name == "run_command":
        return f"run the command: {a.get('command', '')}"
    if name == "run_tests":
        return f"run in {a.get('cwd') or 'the project folder'}: {a.get('command', '')}"
    if name == "write_file":
        return f"write {len(str(a.get('content', '')))} characters to {a.get('path', '')}"
    if name == "press_keys":
        return f"press {a.get('keys', '')}"
    if name == "type_text":
        t = str(a.get("text", ""))
        t = t if len(t) <= 80 else t[:77] + "..."
        return f"type \"{t}\"" + (" and press Enter" if a.get("press_enter") else "")
    if name == "click_text":
        return f"click \"{a.get('text', '')}\""
    if name == "click":
        return f"click at ({a.get('x')}, {a.get('y')})"
    if name == "move_mouse":
        return f"move mouse to ({a.get('x')}, {a.get('y')})"
    if name == "open_app":
        return f"open {a.get('name', '')}"
    if name == "open_url":
        return f"open {a.get('url', '')}"
    if name == "web_search":
        return f"search {a.get('engine', 'google')} for \"{a.get('query', '')}\""
    if name == "focus_window":
        return f"switch to {a.get('title', '')}"
    if name == "compose_email":
        n = len(a.get("attachments") or [])
        return f"draft an email to {a.get('to', '') or 'someone'}" + (f" with {n} attachment{'s' if n != 1 else ''}" if n else "")
    if name == "open_chat":
        return f"open {a.get('app', '')}"
    if name == "remember":
        return f"remember: {a.get('content', '')}"
    if name == "remember_project":
        return f"register project {a.get('name', '')}"
    if name == "open_project":
        return f"open project {a.get('name', '')}"
    if name == "forget":
        return f"forget memories about: {a.get('query', '')}"
    if name == "look_at_screen":
        return a.get("reason") or "look at the screen"
    if name == "review_design":
        return "review the design on screen"
    if name == "check_mesh_file":
        return f"analyse {a.get('path', '')}"
    if name == "blender_check_file":
        return f"check {a.get('path', '')} in Blender"
    if name == "blender_run_python":
        code = str(a.get("code", "")).strip().splitlines()
        return "run in Blender: " + (code[0][:70] + (" …" if len(code) > 1 else "") if code else "(empty)")
    if name == "solidworks_check":
        return "check the SolidWorks document"
    if name == "start_guide":
        return "start a guide: " + str(a.get("goal") or a.get("workflow_id") or "")
    if name == "schedule_task":
        return f"schedule: {a.get('text', '')}"
    if name == "cancel_task":
        return f"cancel job #{a.get('job_id')}"
    if name == "finish_task":
        return f"finish job #{a.get('job_id')}"
    if name == "guide_control":
        return f"guide: {a.get('action', '')}"
    if name == "find_files":
        return f"search files for \"{a.get('query', '')}\""
    if name == "read_file":
        return f"read {a.get('path', '')}"
    if name == "list_dir":
        return f"list {a.get('path', '')}"
    if name == "git_info":
        return f"git status of {a.get('path', '')}"
    if name == "save_workflow":
        return f"save workflow {a.get('name', '')}"
    if name == "play_youtube":
        return f"play \"{a.get('query', '')}\" on YouTube (auto ad-skip active)"
    if name == "auto_allow_antigravity":
        return "enable Antigravity auto-allow" if a.get("enabled") else "disable Antigravity auto-allow"
    if name == "open_ide":
        return f"open {a.get('ide', 'IDE')} on {a.get('path', 'workspace')}"
    if name == "check_code_errors":
        return f"check errors in {a.get('path', '')}"
    if name == "scan_project_errors":
        return f"scan project folder {a.get('folder', '')} for code errors"
    if name == "create_code_script":
        return f"create and open script '{a.get('filename', '')}' in VS Code"
    if name == "dismiss_interferences":
        return "dismiss modal dialogs and interferences"
    if name == "scroll":
        return f"scroll {a.get('amount')}"
    return name.replace("_", " ")
