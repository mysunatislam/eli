# Eli — a persistent AI companion that lives on your desktop

Eli is a small animated heart that floats above every window on your Windows PC. It sees the screen
when you allow it, listens ("Hey Eli"), talks, remembers you across sessions, controls the computer
with your approval, and pairs with your phone. Version 0.2 turns the MVP into a multi-agent
operating layer: one core brain routing to vision, memory, automation, coding, design and
communication agents, with proactive nudges and a private mode.

```
                          ┌──────────────────────── Eli Core Brain (main_agent.py) ────────────────────────┐
  heart overlay ◄─ws─►    │ intents → LLM tool loop (Gemini / Claude, streaming, retries) → offline rules │
  phone (PWA)   ◄─ws─►    └──┬──────────┬──────────┬───────────┬───────────┬──────────────┬───────────────┘
                             ▼          ▼          ▼           ▼           ▼              ▼
                          Vision     Memory    Automation    Coding      Design     Communication
                          capture    encrypted mouse/keys    files/git   CAD review  email/chat drafts
                          OCR        graph     apps/windows  tests       mesh checks (never sends)
                          adaptive   photos    risk gating   VS Code ctx workflows
                             │          │          │           │           │              │
                             └──────────┴──────────┴─────┬─────┴───────────┴──────────────┘
                                                         ▼
                                     Desktop control layer (Win32, pyautogui, Outlook COM, shell)
                                                         ▼
                                            Applications / OS / Browser
```

## What changed in 0.2 (and why the heart was invisible)

**Diagnosis.** Three independent problems could make the heart invisible or frozen:

1. `heart.js` started in the `sleeping` state and only changed on a `state` event, which the backend never sent on connect. The heart looked dim and asleep with closed eyes. Fixed: it starts `idle`, the socket-open handler sets `idle`, and the `status` payload carries the current state.
2. `main.js` waited for Electron's `ready-to-show` before showing the window. That event is not reliably emitted for transparent windows. Fixed: the window is also shown on a 2.5 s timer, with `showInactive()` so it never steals focus.
3. Click-through was driven by forwarded mouse-move events (`setIgnoreMouseEvents(true, {forward: true})`), which some Windows setups never deliver, so hovers and clicks passed straight through the heart. Fixed: the main process polls the cursor and toggles click-through from explicit hit regions the renderer reports.

Hardening added on top: window bounds are logged at startup, the window is placed on the display under the cursor and clamped on screen, `alwaysOnTop('screen-saver')` is re-asserted on blur and every 20 s, a first-run Windows notification tells you where the heart is, `Ctrl+Shift+H` brings it to your cursor, and two escape hatches exist for GPU problems: `ELI_NO_GPU=1` and `ELI_OPAQUE=1`.

**Movement.** The heart now moves on its own: it glides next to a window Eli just opened or focused, drifts a little while idle, comes back home, and is still draggable (dragging sets a new home).

**Character states.** `IDLE · LISTENING · THINKING · SPEAKING · EXECUTING · ERROR · SUCCESS` (+ `SLEEPING` when the backend is off). Thinking pulses slowly with orbiting dots; executing leans forward with a spinning work ring; success sparkles and nods; error tilts, looks confused and shows a "?"; plus breathing, blinking, double blinks, eyes that follow the pointer and glance around.

**Brain.** `eli/llm.py` is a provider-neutral layer: Google Gemini (default when `GEMINI_API_KEY` is set) or Anthropic Claude, with token streaming to the panel and to speech (Eli starts talking at the first sentence), retries with backoff on rate limits and 5xx, automatic model fallback when a Gemini model is retired, per-call token accounting shown in the Privacy drawer, and a token-budget history trimmer that drops old screenshots first.

**Agents.** Coding (VS Code context from the window title, project roots incl. VS Code's recent workspaces, find/read/write files with backups, git status, run tests), Design (CAD review checklist for screenshots, real mesh measurements for STL/OBJ/3MF with trimesh, saved workflows for step-by-step mentoring), Communication (Outlook drafts with attachments via COM, Gmail fallback, chat apps; nothing is ever sent), Memory (knowledge graph of projects/files/tools; "open my CPAP project" pulls files, notes and past conversations), Proactive (repeated-error and long-session nudges with Yes / Not now), Vision (adaptive capture: 3 s while the screen changes, backing off to 15 s when static; change detection skips OCR; app block list; private mode).

**Phone.** Camera memory ("Remember this" → captioned by the vision model, stored encrypted, recalled with "where did I keep my charger?"), SOS button (alert is spoken and shown on the PC), private-mode toggle, approvals for actions and nudges, live screen view, commands.

## Living on the desktop (0.2.1)

- **Follows your cursor.** The heart trails the pointer like a companion: it stays put while you work nearby and glides over once you've moved far away, settling below-right of the cursor, never under it. Toggle with `Ctrl+Shift+F`, the tray, the Follow switch, or by saying *"follow me"* / *"stay here"*. Dragging it sets a new resting spot.
- **Minimal by default.** Clicking the heart opens a one-line quick bar, not a chat window. Replies and questions appear as small bubbles beside the heart; confirmations and permission requests are bubbles with two buttons. The full transcript panel is opt-in (the expand icon on the quick bar, the tray, or *"open the panel"*).
- **Trust mode.** Say *"trust mode on"*, *"just do it"* or *"stop asking me"* (30 minutes), or flip the Trust switch (until you turn it off): shell commands, test runs, file writes and Blender scripts run without a confirmation. Destructive commands (format, rm -rf, rmdir /s, del /s, shutdown, reg delete, git push --force, …) and payment/delete-account buttons always ask. Every auto-approval is logged in the audit table and shown as a toast.
- **Approve by voice.** When Eli is waiting, *"approve"*, *"go ahead"*, *"yes"* or *"cancel"* answers the pending request without touching the mouse.
- **Learns from fixes.** When a `write_file` is followed by a passing `run_tests`, Eli stores the error, the file and the command as a *solution* memory. The next time a similar error shows up on screen, those past fixes are handed to the model first.
- **Proactive fixing.** *"fix errors automatically"* switches proactive mode to auto: when the same error keeps appearing Eli looks immediately and proposes the fix instead of asking whether to look.
- **Blender and SolidWorks bridges.** `blender_check_file` validates every mesh in a .blend headlessly; `blender_check_live` / `blender_run_python` work inside the running session through the Eli Bridge add-on in `backend/integrations/blender/eli_bridge.py` (install it via Edit > Preferences > Add-ons, listens on 127.0.0.1:8791); `solidworks_check` rebuilds the active document over COM and reports feature errors, mass properties and assembly interferences. Drawing apps (Krita, Photoshop, Illustrator, Inkscape, GIMP, Figma…) get their own review checklist for screenshots.

## Requirements

Windows 10/11, Python 3.10+, Node.js 18+, a microphone for voice, and an API key for reasoning:
`GEMINI_API_KEY` (Google AI Studio) or `ANTHROPIC_API_KEY`. Without a key Eli runs in offline mode
(rule-based commands, OCR, error rules, memory, voice).

## Install and run

```powershell
git clone https://github.com/mysunatislam/ellie_desktop.git C:\ellie      # keep path short (Windows 260-char path limit)
cd C:\ellie
install.bat                                                               # creates venv + pip + npm install; copies .env.example
notepad backend\.env                                                      # paste your GEMINI_API_KEY (or ANTHROPIC_API_KEY)
start.bat                                                                 # launches backend + animated desktop overlay
```

The heart appears at the bottom-right of the display your cursor is on. Click it for the panel,
drag it anywhere, `Ctrl+Shift+E` toggles the panel, `Ctrl+Shift+Space` push-to-talk,
`Ctrl+Shift+H` brings Eli to your cursor. Quit from the panel or the tray icon.

## Testing procedure

```powershell
cd backend
.venv\Scripts\python.exe tests\smoke_agents.py    # offline: intents, encrypted memory, capture+OCR, risk gating, error rules
.venv\Scripts\python.exe tests\test_design.py     # mesh analysis on generated STL files
.venv\Scripts\python.exe tests\test_llm.py        # live model: chat, tool round-trip, screenshot understanding
.venv\Scripts\python.exe run.py                   # then in a second terminal:
.venv\Scripts\python.exe tests\ws_client.py --llm # full protocol: memory, Notepad, private mode, screen Q&A, files, project, code
.venv\Scripts\python.exe tests\test_trust.py      # confirmations: voice approval, trust-mode auto-approve, destructive-command guard
cd ..\desktop; $env:ELI_DEBUG=1; npm start        # overlay with logs (bounds, hit toggles, renderer console)
cd ..\backend; .venv\Scripts\python.exe tests\ui_click_test.py   # clicks the real heart once the PC is idle 45 s
```

Manual scenarios:
1. **Heart visible and alive** — start Eli; you get a Windows notification and a pink heart bottom-right that breathes, blinks and follows your pointer with its eyes. Drag it; it stays where you drop it and drifts slightly when idle.
2. **Error explanation** — with a failing script in VS Code, say *"Eli, what is wrong?"*: it asks for screen permission once, reads the traceback, names file/line, explains, proposes the fix, and offers to write it (approval card) and run the tests (approval card).
3. **Prepare an email** — *"Eli, prepare my project report email"*: it finds the newest report file, opens an Outlook (or Gmail) draft with the attachment and waits for you to send.
4. **Design review** — with Fusion/SolidWorks/Blender on screen, *"check my enclosure design"*; or *"check C:\...\part.stl"* for measured watertightness, open edges, thin walls and overhangs.
5. **Memory graph** — *"remember that my CPAP project lives in Documents\cpap"* then, tomorrow, *"open my CPAP project"*.
6. **Proactive** — turn on **See screen**, hit the same error three times in 15 minutes; Eli asks whether to look.
7. **Private mode** — say *"private mode"* (or tap Private on the phone): no capture, no memory writes, no screenshots leave the PC; the pill turns gold.
8. **Phone** — Phone button → open the URL on your phone; tap **Remember this**, photograph your desk; later ask *"where did I put my glasses?"*.

## Debug flags

| Flag | Effect |
|---|---|
| `ELI_DEBUG=1` | Logs window bounds, display scale, click-through toggles and renderer console output to the terminal. |
| `ELI_DEVTOOLS=1` | Opens DevTools for the overlay. |
| `ELI_NO_GPU=1` | Disables GPU compositing (fixes invisible transparent windows on some drivers). |
| `ELI_OPAQUE=1` | Solid dark window instead of transparency (last resort). |

If the heart is still not visible: read the backend/overlay consoles for `overlay shown at x,y` (it prints the display and scale) and for `WARNING: the renderer has not painted`, try `ELI_NO_GPU=1`, then `ELI_OPAQUE=1`, and check that no other program is in exclusive fullscreen.

## Privacy architecture

- **Local first.** Capture, OCR, speech-to-text, text-to-speech, memory and automation run on the PC. Only reasoning calls the model API, and a screenshot is sent only when you ask about the screen (or approve a nudge).
- **Permission before capture.** Nothing is captured until you click **Allow**; the red dot on the heart and the "observing" pill show continuous observation. Revoke any time in Privacy.
- **App block list.** Password managers, banking and wallet windows are never captured (edit the list in Privacy).
- **Private mode.** One toggle (panel, phone, or "private mode" by voice): no capture, no memory writes, no screenshots to the cloud.
- **Computer-control kill switch.** Mouse/keyboard tools are refused when disabled in Privacy.
- **Encrypted memory.** Memories, transcripts, entity names and photo captions are Fernet-encrypted; the key lives in Windows Credential Manager. Deleting is a hard delete ("forget everything", "forget today", "forget about X", or the buttons in Privacy).
- **Confirmation gate.** Shell commands, test runs, file writes, and anything that would send/submit/pay/delete wait for your approval on the PC or phone.
- **Abort switch.** Slam the mouse into the top-left corner to abort automation (pyautogui fail-safe).

## Performance

Idle: the overlay animates at 60 FPS while awake and 15 FPS while asleep or hidden; the backend sleeps between adaptive captures (3 → 15 s) and skips OCR when the screen hasn't changed. Check `http://127.0.0.1:8790/metrics` for backend CPU/RSS, capture interval, OCR count and token usage. On the development PC the backend idles at ~0% CPU and ~180 MB RSS (Whisper adds ~150 MB when loaded), and Gemini replies stream in 1–4 s.

## Folder structure

```
eli/
├─ install.bat / start.bat / scripts/      setup + launch
├─ backend/                                Python 3.10+ (FastAPI)
│  ├─ run.py, requirements.txt, .env.example
│  ├─ eli/
│  │  ├─ config.py       env + settings.json (permissions, private mode, block list, pairing token)
│  │  ├─ main.py         HTTP + WebSocket server: /status /metrics /api/frame.jpg /api/camera /api/sos /mobile
│  │  ├─ bus.py          thread-safe event hub → all connected clients
│  │  ├─ llm.py          provider-neutral turns; Gemini + Anthropic providers; streaming, retries, usage
│  │  ├─ tools.py        tool schemas grouped by agent + confirmation text
│  │  ├─ intents.py      deterministic commands (open X, remember, forget, private mode, open my X project)
│  │  ├─ fallback.py     offline answers: OCR description, error rules
│  │  ├─ agents/
│  │  │  ├─ main_agent.py          Eli Core Brain: routing, tool loop, permissions, confirmations, states
│  │  │  ├─ vision_agent.py        adaptive capture, change detection, OCR, block list, model images
│  │  │  ├─ memory_agent.py        encrypted SQLite + vectors + knowledge graph + photo memories
│  │  │  ├─ automation_agent.py    apps, windows, keyboard, mouse, clipboard, risk_of()
│  │  │  ├─ coding_agent.py        VS Code context, project roots, find/read/write, git
│  │  │  ├─ design_agent.py        CAD checklist + trimesh measurements
│  │  │  ├─ communication_agent.py Outlook/Gmail drafts with attachments, chat apps
│  │  │  └─ proactive.py           repeated-error / long-session nudges
│  │  ├─ speech/         stt.py (whisper + VAD), tts.py, wake.py, controller.py (sentence streaming)
│  │  └─ mobile/         phone PWA (camera memory, SOS, private mode, live view)
│  └─ tests/             smoke_agents, test_llm, test_design, ws_client, ui_click_test
├─ desktop/              Electron overlay: main.js (window, movement engine, click-through), renderer/ (heart.js, app.js)
└─ android/              Kotlin WebView wrapper for the phone page
```

## Protocol (desktop/phone ⇄ backend)

Client → backend: `user_text|command {text}`, `ptt`, `set_setting {key, value}` (observe_enabled, wake_enabled, voice_replies,
private_mode, automation_enabled, nudges_enabled, blocked_apps, screen_permission), `confirm {id, approve}`,
`permission {id, allow}`, `nudge_action {id, action}`, `stop_speaking`, `forget_all`, `forget_today`, `get_status`.
Backend → clients: `status`, `state`, `transcript`, `transcript_delta {id, text}`, `context`, `attention {rect}`, `tool`,
`confirm_request`, `permission_request`, `nudge {id, text, actions}`, `toast`, `notify`, `notification`, `history`.

## Configuration (`backend/.env`)

| Variable | Default | Meaning |
|---|---|---|
| `ELI_LLM_PROVIDER` | `auto` | `gemini`, `anthropic`, or auto (Gemini if its key is set). |
| `GEMINI_API_KEY` / `ELI_GEMINI_MODEL` | — / `gemini-3.6-flash` | Google model; falls back to 3.5-flash / flash-latest if retired. |
| `ANTHROPIC_API_KEY` / `ELI_MODEL` / `ELI_EFFORT` | — / `claude-opus-5` / `medium` | Anthropic alternative. |
| `ELI_CONTEXT_BUDGET` | `30000` | Approximate tokens of history kept. |
| `ELI_CAPTURE_INTERVAL` / `ELI_CAPTURE_INTERVAL_MAX` | `3` / `15` | Adaptive observation bounds (seconds). |
| `ELI_PROJECT_DIRS` | — | Extra folders for the coding agent (`;` separated). |
| `ELI_WHISPER_MODEL` / `ELI_TTS_VOICE` / `ELI_TTS_RATE` | `base.en` / — / `185` | Speech. |
| `ELI_PORT` | `8790` | Backend port. |

## Next upgrade steps

1. Native IDE bridge: a VS Code extension that streams diagnostics, the open file and terminal output over the socket (no OCR needed) and applies edits as reviewable diffs.
2. CAD APIs: Fusion 360 add-in and SolidWorks COM adapter for measured interference, wall-thickness and hole-alignment checks (the "misaligned by 1.2 mm" class of findings that screenshots cannot give).
3. Local models: an on-device VLM/LLM option (Ollama / llama.cpp) so screen understanding never leaves the PC.
4. Continuous camera memory on the phone with on-device captioning and encrypted sync, plus place/time indexing ("what did I do yesterday?").
5. Multi-device presence and a WebRTC channel for low-latency screen streaming and two-way voice on the phone.
6. Learned workflows: record how the user performs a task once, replay it with confirmation ("teach me" mode).

## Guided workflows (0.3): precise, narrated, on-screen steps

Ask *"Eli, guide me through merging the holes"* (or *"walk me through …"*, *"show me how to …"*). Eli:

1. **Recognises the geometry.** The vision model returns bounding boxes for the holes; each one gets a glowing box on screen and Eli says how many it found.
2. **Shows one step at a time on a full-screen, click-through layer.** A step card (Step 2 of 6, title, exact instruction) plus an animated indicator at the precise place to act: a glowing box and pulsing ring on the button found by OCR (e.g. “FINISH SKETCH”), boxes from the vision model on geometry (the arcs to trim), a key-cap badge for shortcuts (T, X, E), a dashed animated arrow from the card to the target. If a label can't be found, a dashed region marks where to look instead of a vague "somewhere in the toolbar".
3. **Narrates in a female voice** (Edge neural *Aria* online, Microsoft *Zira* offline) in sync with the indicator's entrance. `ELI_TTS_ENGINE=neural|sapi|auto`, `ELI_TTS_NEURAL_VOICE=en-US-JennyNeural` to change.
4. **Watches the viewport and advances by itself.** While a guide runs, capture speeds up to 1.5 s. Each changed frame is checked against the step's completion conditions: OCR text appearing/disappearing (the TRIM dialog, FINISH SKETCH), the window title, or a yes/no question to the vision model every 6 s ("Do the five holes now form one outline?"). When satisfied, a check-mark bursts on the target and the next step appears.
5. **Handles trouble.** Leaves the app → pause and wait; step takes longer than its timeout → "Repeat / Another way / I did it / Skip" bubble and spoken offer; optional step already done → skips ahead. Voice at any time: *next*, *repeat*, *back*, *skip*, *another way*, *stop the guide*.

**Workflow: merge five holes, keep the separating curves** — `backend/eli/guides/merge_holes.py`, variants for Fusion 360, Onshape, SolidWorks and Blender (the app is detected from the active window). Sketch method: edit the sketch → turn the separating arcs into construction geometry (X / Q / "For construction") so they survive → Trim (T / Trim / Power trim) the arcs that fall inside neighbouring holes until one outline remains → finish the sketch → re-cut the merged profile (E / Extrude ▸ Cut/Remove ▸ Through all). Blender: Edit Mode → mark the hole rims sharp → select the webs → delete faces.

**Extending to other single-shape operations**: add a workflow dict to `backend/eli/guides/` with `keywords`, `recognize`, and per-app `steps` (`target` = `ui_text` / `geometry` / `key` / `window_region`; `expect` = `ocr_contains` / `ocr_absent` / `title_contains` / `vision` / `manual`) and register it in `WORKFLOWS`. Nothing else changes.

Tests: `tests\test_guide.py` (schema, target resolution, auto-advance, stuck, pause/resume) and the overlay can be exercised without a CAD app: say "guide me through merging the holes"; Eli reports it can't see Fusion 360, shows step 1 with a region marker, and pauses until a matching app is in front.

### FreeCAD variant and the end-to-end test

FreeCAD 1.x is supported directly (detected from the window): edit `HolesSketch` → draw a bridging rectangle across the hole centres (G, R; Eli marks click points ① and ② on the leftmost and rightmost holes it recognised) → Trim edge (G, T) every segment inside the merged shape, leaving the outer arcs of each hole → Close the sketch, and the Holes pocket recomputes into one scalloped opening.

`backend/tests/assets/make_five_holes.py` builds the test plate headlessly (`freecadcmd.exe make_five_holes.py`), `five_holes_demo.py` is a simulated user that performs the steps inside FreeCAD on a timer, and `tests/e2e_freecad_guide.py` launches FreeCAD with it, starts the guide over the socket, and checks that Eli recognises the holes, points at the right places and auto-advances through the steps purely from what it sees.

**Which window the guide watches.** The guide follows the *active* window of the chosen app. If you name the app ("… in FreeCAD") and it is not in front, Eli brings the first matching window forward. With your own FreeCAD open, that is your session, not a test file; the end-to-end test therefore only runs cleanly on a machine where the test instance is the only FreeCAD window, or with your session closed. To watch the auto-advance yourself, open `backend/tests/assets/five_holes.FCStd`, say *"guide me through merging the holes in FreeCAD"*, and either follow the steps or run `five_holes_demo.py` from Macro ▸ Macros… to have FreeCAD perform them on a timer.

## Scheduler: continuous work that survives idle time (0.3.1)

Eli keeps assignments in a persistent job list (encrypted in the memory database) and a background loop runs them, so "keep checking…" work is not forgotten when the conversation goes quiet or Eli restarts.

| Say | What Eli does |
|---|---|
| *"remind me in 20 minutes to stretch"*, *"remind me at 5pm to call Sam"*, *"remind me tomorrow at 9 to send the report"* | A **reminder**: spoken, shown as a bubble with **Done** / **Snooze 10 min**, and a Windows notification. |
| *"in 2 hours open my report"* | A **task** run once at that time through the normal agent (tools, apps, screen). |
| *"every hour check whether the build passed"*, *"every day at 8am summarise my inbox"* | A **recurring** job; runs quietly and only speaks up when there is something to report (a run that finds nothing replies `NO_CHANGE` and stays silent). |
| *"keep watching the download and tell me when it finishes"* | A **watch**: every 2 minutes until the goal is met; the model calls `finish_task` when it is. |
| *"what are you working on?"*, *"cancel job 3"*, *"cancel all reminders"* | List / cancel. The panel's **Tasks** drawer shows every job with next run, run count, last result and a cancel button; the badge shows how many are active. |

The model can also create jobs itself (`schedule_task`, `list_tasks`, `cancel_task`, `finish_task`) whenever you ask for something later, repeatedly, or "until". Active jobs are injected into every prompt, so Eli remembers them mid-conversation. Jobs that came due while Eli was off run at the next start. Minimum interval is 1 minute; recurring jobs stop after 500 runs unless renewed. Tests: `tests\test_scheduler.py`.

**Guide card.** The step card is now a compact 330 px card docked in a screen corner (it moves to the opposite corner from the target), one step at a time, with the instruction clipped to three lines (the full text is spoken and in the transcript). Recognition and hints are a slim toast along the top edge.

## Building an installable app for another laptop

`install.bat` / `start.bat` are for *this* machine, where you already have Python and Node. To hand
Eli to a laptop that has neither, build a real Windows installer once, here:

```powershell
powershell -ExecutionPolicy Bypass -File installer\build-installer.ps1
```

This downloads a portable Python runtime, installs every backend dependency into it, and packages
it together with the Electron overlay into a single NSIS installer:
`desktop\dist\Eli-Setup-<version>.exe` (roughly 350–450 MB — it carries its own Python, so the other
laptop needs nothing pre-installed). Building takes a few minutes and needs internet access on *this*
machine only. Re-run it after any code change you want to ship; it always rebuilds the installer from
the current source.

**On the other laptop:** copy `Eli-Setup-<version>.exe` over (USB drive, cloud, whatever) and double-click
it — no admin rights needed, it installs to `%LocalAppData%\Programs\Eli`. First launch shows a short
setup screen: pick Gemini or Claude and paste an API key (or skip for offline mode), choose whether Eli
should start at sign-in and speak replies, then it starts itself — no separate `start.bat`, no console
windows. The heart appears the same way it does here. The API key and all data are written to
`%APPDATA%\Eli` (never inside the install folder, so re-installing or updating never touches them).
Uninstall from Windows Settings ▸ Apps, same as any other app.

## Starting with Windows

Eli is two processes (the Python backend and the Electron overlay). To have them come back on their own after a reboot, enable the startup entry once:

```powershell
autostart.bat              # or: powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Enable
```

This puts an "Eli" shortcut in your Startup folder (visible in Task Manager ▸ Startup) that runs `scripts\start.ps1 -Hidden` at sign-in: the backend starts without a console (logs in `backend\data\server.log`) and the heart appears a few seconds later. The same switch is in the panel's **Privacy** drawer ("Start Eli when I sign in to Windows") and by voice: *"start with Windows"* / *"don't start with Windows"*. `autostart.bat off` removes it. `stop.bat` stops both processes; `start.bat` starts them by hand (it skips a backend that is already running, and a second overlay launch only brings the existing heart forward).

Scheduled jobs, memory, trust and privacy settings are on disk, so everything is exactly as you left it after a restart; jobs that came due while the PC was off run at the next start.
