# Eli — Strategy, Competitive Position, and the Teach-me Specification

*Repository review and build notes, 16 September 2026.*

This document has two halves. **Part A** is a business-level assessment of what Eli is, where it sits in the
September-2026 landscape, and where the defensible scope lies. **Part B** is the specification of the
Teach-me feature built in this pass (record once → runnable guide), how it is wired into the codebase, and
what comes next on that track.

---

## Part A — Business assessment

### A1. What Eli is today (from the code)

| | |
|---|---|
| Shape | Windows-only Electron overlay (animated heart) + Python/FastAPI backend |
| Size | ~14k LOC, single contributor, first commit 10 Sept 2026, v0.4.4 → 0.5 with this pass |
| Brain | Provider-neutral LLM loop: Gemini / Claude / Groq / OpenRouter / Ollama / rule-based offline; streaming, retries, model fallback, token budget |
| Senses | Screen capture → change detection → Windows OCR; adaptive 3–15 s cadence; block list; private mode |
| Hands | 66 tools (after this pass); pyautogui + Win32; risk gating; trust mode; destructive-command guard; audit log |
| Memory | Fernet-encrypted SQLite + sqlite-vec RAG + knowledge graph + "solution" memories learned from verified fixes |
| Speech | faster-whisper + VAD, wake word, barge-in, Edge neural / SAPI TTS |
| Scheduler | Persistent reminders / recurring jobs / watches that survive restarts |
| Agentic core | Plan → execute → empirical verification (window, file syntax, tests, OCR) → self-correct → consolidate |
| **Guide layer** | Full-screen click-through overlay: step cards, glowing targets from OCR/vision boxes, key badges, narration, **vision-verified auto-advance**, stuck handling; hand-authored `merge_holes` workflow for 5 CAD apps; e2e test with a simulated user in FreeCAD |
| **Teach-me (new)** | Record the expert once → segment → validated guide in the library → runnable by name; export/import |
| CAD bridges | trimesh mesh checks; Blender add-on over localhost; SolidWorks COM rebuild/interference |
| Phone | PWA + Android WebView; camera memory, SOS, approvals, live view over LAN |

**Engineering-hygiene gaps that matter commercially:** no `LICENSE`; no signed installer; no auto-updater;
no crash reporting or opt-in telemetry; two 1,500-line agent modules with hard-coded window-title
heuristics; README/package version drift. (This pass adds the first real test suite and a CI workflow.)

**Three features to remove before any commercial distribution or investor diligence:**

1. `auto_allow_antigravity` — an agent that auto-clicks *Allow*/*Submit* on another agent's permission prompts. Regardless of its guard rails, the concept is the anti-pattern the industry moved away from in 2026 (Windows Agent Workspace's isolation model exists as a reaction to exactly this).
2. `play_youtube` with automatic ad skipping — YouTube ToS violation; blocks Store listing and most partnerships.
3. The "complaint search shield" — an assistant that intercepts what a user searches for is a red-flag surface.

### A2. The landscape (September 2026)

The category hardened into four lanes. Eli currently straddles all of them, which is the strategic problem.

| | Eli | Copilot Studio CUA / Copilot Tasks | Claude Cowork | Lapu AI / goose | Screenpipe / Limitless / Recall | Autodesk Assistant / Onshape Advisor / SolidWorks AURA | Grok Ani / Replika |
|---|---|---|---|---|---|---|---|
| Runs on *your* machine, your files | ✅ | ❌ server-side, account-bound | ❌ server-side (since Mar 2026) | ✅ | ✅ | ✅ inside one app | ❌ |
| Controls mouse/keyboard | ✅ OCR + pyautogui | ✅ trained grounding model | ✅ trained grounding | ✅ | partial | ✅ via app API | ❌ |
| Ambient screen memory | ✅ local, encrypted | Recall (Copilot+ PCs) | ❌ | partial | ✅ core product | ❌ | ❌ |
| **Teaches the human with on-screen, verified steps** | ✅ | ❌ | ❌ | ❌ | ❌ | text/chat only, one app | ❌ |
| **Learns a procedure by watching once** | ✅ (this pass) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Works with any app | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | — |
| Offline / local model | ✅ Ollama | ❌ | ❌ | goose ✅ | ✅ | ❌ | ❌ |
| Platform | Windows | Windows/web | macOS | mac/Win | mac/Win/Linux | vendor app | mobile |
| Distribution | none | every Windows PC | every Claude Pro/Max seat | OSS | OSS | every CAD seat | X app |

**Read:**

- *Acting for you* (open apps, fix code, draft email): Eli is a weaker version of what Microsoft ships on every Windows PC and Anthropic ships on every Pro seat. OCR-and-click loses to grounding models trained on millions of UI screenshots. Closed lane for a solo developer.
- *Remembering for you*: Screenpipe is open-source, cross-platform, MCP-native, two years ahead. Closed.
- *Companion persona*: xAI, Replika, Character.ai own it with infinite marketing. The heart is charming; it is not a business.
- *Teaching you, inside the software in front of you, app-agnostically, with visual verification* — **nobody is here.** Autodesk Assistant returns a chat bullet list inside Fusion; Onshape Advisor answers questions inside Onshape. Neither draws on your screen, neither watches whether you did the step, neither works in FreeCAD, KiCad, Blender, Inkscape, ImageJ, MATLAB or 3D Slicer.

### A3. Positioning

**Eli is not a companion. Eli is a GPS for software** — an app-agnostic overlay that turns any procedure into
narrated, on-screen, step-verified guidance, and that learns new procedures by watching an expert do them once.

| | "AI companion on your desktop" | "Interactive guide layer for any desktop software" |
|---|---|---|
| Buyer | consumer, free, fickle | training departments, universities, vocational programs, software vendors, research labs |
| Willingness to pay | ≈ $0 (competes with free Copilot) | site licenses, per-seat, vendor SDK — existing budgets |
| Incumbent risk | Microsoft, Anthropic, xAI | none app-agnostic; vendors are locked to their own app |
| Data moat | none | **expert screen + action traces** — every recorded guide is proprietary cross-app procedural data no vendor can collect |
| Windows-only | fatal | fine — engineering education and manufacturing are overwhelmingly Windows |
| Solo dev, South Asia | cannot out-distribute | beachhead is engineering colleges and SMB manufacturers you can walk into — price-sensitive, tolerant of rough edges, unserved |

A second, sharper wedge with zero competition, aligned with the author's domain: **guided workflows for
research software** — 3D Slicer, ImageJ/Fiji, MATLAB toolboxes, GraphPad, Materialise Mimics, SPM, Blender for
anatomical models. Labs onboard new students every semester and no vendor AI covers any of these tools.

### A4. Roadmap (leverage-ordered)

1. **Teach-me mode** — built in this pass (Part B). This is the content flywheel and the data moat.
2. **Accessibility tree + grounding model** — replace OCR-click with UI Automation (`pywinauto`/UIA) for exact control names/bounds/states; vision grounding only for canvases. Fixes "works on the dev's PC" → "works on a student's 1366×768 laptop". Retires the hard-coded window-title heuristics.
3. **Native bridges, first five apps** — FreeCAD (Python API), Blender (exists), VS Code (extension over the socket), Fusion (MCP is now official), 3D Slicer (Python console). Ground-truth completion checks ("hole is now 6.0 mm") instead of vision guesses.
4. **Remove the three red-flag features; add LICENSE (AGPL core + commercial authoring is the standard play), signed installer, auto-updater, opt-in telemetry** on the metrics that matter: guide completion rate, time-to-step, retries, "another way" requests.
5. **Fully-offline edition** — bundle Ollama + a small VLM. Regulated labs, defence-adjacent manufacturers, universities without cloud budget. Cheap: the provider abstraction exists.
6. **Two skins, one engine** — *Eli* (heart, consumer, free, community guides) and *Eli for Teams* (no persona, admin console, SSO, audit log, policy-based trust, private guide libraries).
7. **Monetisation** — free consumer → Pro ($8–12/mo) → Education site license (the beachhead) → Vendor SDK (mid-tier software vendors embed the guide layer; medical-device software needing auditable training records) → Guide marketplace with creator rev-share.
8. **Cross-platform** — after paying Windows users exist, not before.

---

## Part B — Teach-me specification (implemented)

### B1. Goal

"Eli, let me teach you how to *X*" → the user performs *X* once → Eli produces a guide in the **existing step
schema** (`eli/guides/__init__.py`) so the unchanged guide engine can run it with on-screen indicators,
narration and vision-verified auto-advance, for this user or anyone the guide is exported to.

### B2. Pipeline

```
 user's own input ─► Recorder (Win32 LL hooks) ─► TraceEvent[] ─► Recording (trace.json + thumbs)
                                                                            │
                     ┌────────────────────────────── segmenter.segment() ◄──┘
                     │   vision model available?  ──yes──► LLM: trace text + ≤6 marked-up screenshots
                     │                                       └► JSON steps ─► _clean_step (planner) ─► _backfill()
                     │                            ──no───► heuristic(): 1 step / action, typing folded in
                     ▼
              assemble() ─► workflow dict {recorded:true, events per step} ─► GuideLibrary.add()
                                                                                  │ JSON on disk + register()
                                                                                  ▼
                                               "guide me through <name>" ─► find_workflow ─► GuideAgent.start()
```

### B3. Files

| File | Role |
|---|---|
| `backend/eli/guides/recorder.py` | `TraceEvent`, `Recording` (trace model, save/load), enrichment helpers (`label_under`, `region_of`, `ocr_delta`, key naming), `Recorder` (hooks, worker, snapshots) |
| `backend/eli/guides/segmenter.py` | `heuristic()`, `segment()` (model path), `_backfill()`, `assemble()`, `keywords_for()` |
| `backend/eli/guides/library.py` | `GuideLibrary`: load/add/delete/rename/find/list/export/import; registers with `WORKFLOWS` |
| `backend/eli/guides/__init__.py` | `register()`, `unregister()`, `BUILTIN_IDS`; `find_workflow()` now also resolves learned guides by name/keywords |
| `backend/eli/agents/teach_agent.py` | `TeachAgent`: permissions, lifecycle, hub/speech, review & edit-by-voice, export/import |
| `backend/eli/tools.py` | `TEACH_TOOLS` (9 tools) + `describe_action` entries |
| `backend/eli/agents/main_agent.py` | persona TEACH line; async dispatch; intent handlers; `stop_all` cancels a recording |
| `backend/eli/intents.py` | `extract_teach_request()` + regexes; hooked into `match()` before generic patterns |
| `backend/eli/main.py` | constructs `TeachAgent`, attaches it with the loop and speech |
| `desktop/renderer/*` | purple **learning · N actions** pill; toast per recognised action |
| `backend/tests/test_teach.py` | 29 tests, pure Python, any OS |
| `backend/pytest.ini`, `.github/workflows/ci.yml` | test config and CI (3.10 / 3.12) |

### B4. Recorder details

- **Hooks.** `WH_MOUSE_LL` / `WH_KEYBOARD_LL` via ctypes in a dedicated thread with its own message pump. Callbacks only enqueue (must return in < 300 ms or Windows silently unhooks). Stopped with `WM_QUIT` to the hook thread. Events flagged `LLMHF_INJECTED` / `LLKHF_INJECTED` are ignored, so Eli's own automation and macros are never recorded.
- **Events kept.** `click`, `dblclick` (two clicks < 350 ms, < 6 px apart), `rclick`, `mclick`, `shortcut` (modifier chord, e.g. `Ctrl+Shift+E`), `key` (navigation/function keys by name), `type` (aggregated printable runs, closed by a 1.5 s gap or any non-printable key), `scroll` (wheel bursts collapsed to one). Mouse movement is never stored.
- **Enrichment per event.** A worker thread keeps a fresh frame every 1 s (via `vision.capture_now()`, honouring block list and private mode). On an action: *before* frame → OCR label under the cursor (containing box, else nearest within 40 px, restricted to the active window), region (`top/left/right/bottom/center`), fractional position; wait 0.8 s → *after* frame → window title change, OCR lines that appeared/disappeared inside the app window (fragments < 3 chars and pure numbers ignored). Optional marked-up thumbnails (pink ring on the click) saved as JPEG ≤ 1000 px.
- **Privacy defaults.** Typed text is stored as `chars: N` unless `settings.teach_capture_text` is true. Recording refuses to start in private mode or on a block-listed window. Traces live in `DATA_DIR/recordings/<id>/` and are never uploaded; only the segmenter's *trace text* and up to six thumbnails go to the model, and only when a model is configured.
- **Limits.** 400 events per recording; primary monitor only (mirrors the existing vision capture); multi-monitor offsets are a known gap shared with the rest of the vision code.

### B5. Segmenter details

**Heuristic (offline, deterministic).** One step per click/double-click/right-click/shortcut/nav key. A `type` event that follows a click folds into that click's step ("Click *Label* and type"), and an `Enter` right after typing folds in too. Scrolls are dropped. Targets: `key` for chords, `ui_text` + region for labelled clicks, a ±6 %/±5 % `window_region` around unlabelled clicks. Completion: longest *appeared* line that isn't the label → `ocr_contains`; window title change → `title_contains`; else a *disappeared* line → `ocr_absent`; else `manual`.

**Model path.** Prompt = system + trace text (numbered events with labels, appeared/disappeared, title changes) + up to six evenly spaced marked-up *before* screenshots + the same JSON schema `planner.py` uses, plus an `events: [i, …]` field per step (required) so provenance is kept. Rules push the model to **merge** trace events into learner-sized logical steps and to prefer texts that literally appear in the trace. Output is validated with `planner._clean_step` (unknown kinds dropped, timeouts clamped), then `_backfill()` grounds it: a `ui_text` the recorder never saw is replaced by the label that was actually under the cursor; a `window_region` on a labelled click becomes that label; empty/`manual`-only expects are rebuilt from the recorded OCR deltas. If the model output does not survive, the heuristic result is used.

**Workflow shape.** Same as dynamic plans (`apps.recorded.{label, match, steps}`, `default_app: "recorded"`), plus `recorded: true`, `recording_id`, `goal`, `keywords` (one any-of group: full name + distinctive words), `created`, and `events` on each step. Exported files strip `recording_id` and `events`.

### B6. Voice / tool surface

| Intent (deterministic) | Tool (LLM) | Effect |
|---|---|---|
| "let me teach you how to X", "watch me X", "record this" | `start_recording {name, goal}` | permission → recorder on → "do it once" |
| "stop recording", "I'm done with the demo" | `stop_recording {run_now}` | segment → save → register → summary (optionally start the guide) |
| "cancel the recording" | — | discard |
| "what guides do you have?" | `list_guides` | library listing |
| "review that guide" | `review_guide {guide_id|name}` | steps with targets and completion checks |
| — | `edit_guide_step {index, title|instruction|say|delete}` | small fixes |
| — | `rename_guide`, `delete_guide` | |
| "export that guide" | `export_guide {guide_id|name, path}` | `.eliguide.json` |
| — | `import_guide {path}` | register a shared guide |

`stop_all` ("stop") cancels an in-progress recording along with everything else.

### B7. Tests

`backend/tests/test_teach.py` — 29 tests covering: OCR label selection (containing / nearest / window-restricted), regions, OCR delta filtering, key naming and chords, trace round-trip, heuristic segmentation on a realistic 7-event demo (folding, provenance, expect derivation, typed-text privacy), region fallback, empty recording, model-output validation and back-fill with a fake LLM, library add/find/export/import/delete/reload/garbage, built-in protection, and intent parsing including negatives ("stop", "show me the screen" must not match). Run with `cd backend && python -m pytest tests/test_teach.py -q`. CI runs it on Python 3.10 and 3.12.

### B8. Next on this track

1. **Visual review UI** in the overlay: step list with the marked-up thumbnail per step, click-to-retarget (draw a box → `ui_text`/`window_region`), edit expect, reorder, publish. The data (`events`, thumbnails) is already stored for this.
2. **Segmentation quality loop.** Log heuristic-vs-model diffs and author corrections; those corrections are the training set for a small local segmenter.
3. **Accessibility-tree capture.** Record the UIA control (name, automation id, control type) under the cursor alongside the OCR label; the guide engine gets a fourth target kind `ui_control` that survives themes, DPI and resolutions.
4. **Guide versions per app version.** Store `app_version` in `match`; warn when a guide was recorded on a different major version.
5. **Guide packs.** A folder of `.eliguide.json` + manifest, installable in one step — the unit of distribution for courses and vendors.
6. **Marketplace and licensing hooks** once packs exist.
