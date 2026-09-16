"""Agentic Architecture Core for Ellie:
- Plan: Task breakdown with milestones and verification criteria
- Orchestrate: Specialist tool coordination (Coding, Vision, Automation, Design, Comms, Memory)
- Evaluate / Verifier: Empirical postcondition checks (window existence, file integrity, syntax validity, test results)
- Correct: Autonomous failure diagnosis, RAG-assisted remedy formulation, and retry loop
- Consolidate: Persisting verified solutions into long-term memory
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("eli.agentic")


@dataclass
class VerificationResult:
    passed: bool
    observations: str
    diagnosis: str = ""
    suggested_action: str = ""

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "observations": self.observations,
            "diagnosis": self.diagnosis,
            "suggested_action": self.suggested_action,
        }


@dataclass
class PlanStep:
    id: str
    description: str
    tool: str = ""
    args: dict = field(default_factory=dict)
    verification: str = "none"   # none | window_open | file_syntax | file_exists | test_pass | screen_ocr
    status: str = "pending"       # pending | running | verified | failed | corrected
    result: Optional[Any] = None
    verification_result: Optional[VerificationResult] = None
    retries: int = 0
    max_retries: int = 3

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "tool": self.tool,
            "args": self.args,
            "verification": self.verification,
            "status": self.status,
            "result": str(self.result) if self.result is not None else None,
            "verification_result": self.verification_result.as_dict() if self.verification_result else None,
            "retries": self.retries,
        }


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    current_step_idx: int = 0
    is_complete: bool = False
    rag_context: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "goal": self.goal,
            "steps": [s.as_dict() for s in self.steps],
            "current_step_idx": self.current_step_idx,
            "is_complete": self.is_complete,
            "created_at": self.created_at,
        }

    def current_step(self) -> Optional[PlanStep]:
        if 0 <= self.current_step_idx < len(self.steps):
            return self.steps[self.current_step_idx]
        return None

    def advance(self) -> None:
        self.current_step_idx += 1
        if self.current_step_idx >= len(self.steps):
            self.is_complete = True


class ActionVerifier:
    """Performs empirical verification of tool actions to ensure tasks actually succeed."""

    def __init__(self, vision, auto, coding, memory):
        self.vision = vision
        self.auto = auto
        self.coding = coding
        self.memory = memory

    def verify_action(self, tool_name: str, args: dict, result_content: Any) -> VerificationResult:
        """Determines the appropriate verification strategy for a tool call."""
        res_str = str(result_content or "")

        # 1. App or window launch
        if tool_name == "open_app":
            app_name = str(args.get("name", ""))
            return self.verify_window_open(app_name)

        if tool_name == "open_ide":
            ide = str(args.get("ide", ""))
            label = "Visual Studio Code" if "code" in ide else "MATLAB" if "matlab" in ide else ide
            return self.verify_window_open(label)

        # 2. File write or edit
        if tool_name == "write_file":
            path = str(args.get("path", ""))
            return self.verify_file(path, check_syntax=True)

        # 3. Running tests or commands
        if tool_name in ("run_tests", "run_command"):
            return self.verify_command_result(res_str)

        # 4. Code error checking
        if tool_name in ("check_code_errors", "scan_project_errors"):
            if "error(s)" in res_str or "❌" in res_str or "Found" in res_str:
                return VerificationResult(
                    passed=False,
                    observations=res_str[:300],
                    diagnosis="Code analysis detected syntax or structural errors.",
                    suggested_action="Review and repair the indicated error lines."
                )
            return VerificationResult(
                passed=True,
                observations=res_str[:200]
            )

        # 5. Media playback
        if tool_name == "play_youtube":
            return VerificationResult(
                passed=not res_str.lower().startswith("couldn't"),
                observations=res_str
            )

        # Default: check if tool returned an explicit error indicator
        if any(err_lead in res_str.lower() for err_lead in ("error running", "couldn't open", "i don't know", "failed", "access is denied")):
            return VerificationResult(
                passed=False,
                observations=res_str,
                diagnosis="Tool returned an error message.",
                suggested_action="Check arguments and permissions."
            )

        return VerificationResult(passed=True, observations=res_str[:150])

    def verify_window_open(self, app_or_keyword: str) -> VerificationResult:
        """Verifies that an application window actually exists and is registered in the OS."""
        time.sleep(0.5)  # brief grace period for window creation
        try:
            windows = self.auto.list_windows()
            low_keyword = app_or_keyword.lower().strip()
            # Canonical aliases
            keyword_map = {
                "vs code": "code",
                "vscode": "code",
                "visual studio code": "code",
                "matlab": "matlab",
                "chrome": "chrome",
                "notepad": "notepad",
                "explorer": "explorer",
                "file explorer": "explorer"
            }
            search_term = keyword_map.get(low_keyword, low_keyword)

            for w in windows:
                if search_term in w.lower() or low_keyword in w.lower():
                    return VerificationResult(
                        passed=True,
                        observations=f"Window verified open: '{w}'"
                    )

            # Also check foreground window
            cur = self.vision.user_window()
            if cur and (search_term in cur.describe().lower() or low_keyword in cur.describe().lower()):
                return VerificationResult(
                    passed=True,
                    observations=f"Active window verified: '{cur.describe()}'"
                )

            return VerificationResult(
                passed=False,
                observations=f"No window found matching '{app_or_keyword}'. Available windows: {', '.join(windows[:5])}",
                diagnosis="Application did not open or failed to create a top-level window.",
                suggested_action=f"Check executable path for {app_or_keyword} or launch via shell."
            )
        except Exception as e:
            return VerificationResult(passed=False, observations=str(e), diagnosis=f"Window verification exception: {e}")

    def verify_file(self, path: str, check_syntax: bool = True) -> VerificationResult:
        """Verifies that a file exists, is non-empty, and possesses valid language syntax."""
        p = Path(path).expanduser()
        if not p.is_file():
            return VerificationResult(
                passed=False,
                observations=f"Target file does not exist: {path}",
                diagnosis="File write failed to produce a file on disk.",
                suggested_action="Verify parent directory permissions and target path."
            )
        try:
            sz = p.stat().st_size
            if sz == 0:
                return VerificationResult(
                    passed=False,
                    observations="File exists but is 0 bytes.",
                    diagnosis="File was written empty.",
                    suggested_action="Re-write file with complete content."
                )

            if check_syntax and p.suffix.lower() in (".py", ".m", ".c", ".cpp", ".cc", ".h", ".hpp"):
                syntax = self.coding.check_code_errors(str(p))
                if not syntax.get("valid", True):
                    err_msg = "; ".join(syntax.get("errors", []))
                    return VerificationResult(
                        passed=False,
                        observations=f"File written ({sz} bytes) but contains syntax errors: {err_msg}",
                        diagnosis=f"Syntax error in {syntax.get('language')}: {err_msg}",
                        suggested_action="Fix syntax errors according to error location."
                    )
            return VerificationResult(passed=True, observations=f"File verified intact ({sz} bytes, syntax clean).")
        except Exception as e:
            return VerificationResult(passed=False, observations=str(e), diagnosis=f"File check failed: {e}")

    def verify_command_result(self, output: str) -> VerificationResult:
        """Verifies stdout/stderr of test runs and commands."""
        low = output.lower()
        if any(fail in low for fail in ("fail", "error", "traceback", "syntaxerror", "exception", "abort")):
            lines = [l.strip() for l in output.splitlines() if any(f in l.lower() for f in ("error", "fail", "line "))]
            summary = "; ".join(lines[:3]) if lines else output[:150]
            return VerificationResult(
                passed=False,
                observations=output[:400],
                diagnosis=f"Command/tests reported failures: {summary}",
                suggested_action="Inspect failure traceback and apply appropriate fix."
            )
        return VerificationResult(passed=True, observations=output[:200])


class SelfCorrector:
    """Formulates autonomous corrections when actions fail verification."""

    def __init__(self, memory, coding, auto, verifier: ActionVerifier):
        self.memory = memory
        self.coding = coding
        self.auto = auto
        self.verifier = verifier

    def formulate_correction(self, step: PlanStep, verification: VerificationResult) -> Optional[dict]:
        """Analyzes a failure and constructs modified parameters or alternative actions."""
        tool = step.tool
        args = dict(step.args)
        diag = verification.diagnosis.lower()

        # 1. App Launch Failed
        if tool in ("open_app", "open_ide"):
            app_name = args.get("name") or args.get("ide") or ""
            log.info("Self-correcting launch failure for '%s'...", app_name)
            # Query memory for how this app was launched previously
            mems = self.memory.recall(f"how to launch {app_name}", k=2, kinds=["solution", "fact"])
            for m in mems:
                match = re.search(r"([A-Za-z]:\\[^\s,;]+\.exe)", m.content)
                if match and os.path.exists(match.group(1)):
                    return {"tool": "run_command", "args": {"command": f'start "" "{match.group(1)}"'}}

            # Try discovering executable directly via CodingAgent
            if "vs" in app_name.lower() or "code" in app_name.lower():
                ides = self.coding.discover_ides()
                if ides.get("vscode"):
                    return {"tool": "run_command", "args": {"command": f'start "" "{ides["vscode"]}"'}}
            if "matlab" in app_name.lower():
                ides = self.coding.discover_ides()
                if ides.get("matlab"):
                    return {"tool": "run_command", "args": {"command": f'start "" "{ides["matlab"]}" -desktop'}}

            # Fallback to system shell start
            return {"tool": "run_command", "args": {"command": f"start {app_name}"}}

        # 2. Syntax Error in File Write
        if tool == "write_file":
            path = args.get("path", "")
            log.info("Self-correcting syntax error in file '%s'...", path)
            # Use offline syntax checker to diagnose the exact error
            syntax = self.coding.check_code_errors(path)
            if not syntax.get("valid") and syntax.get("errors"):
                # Recall past solutions for this type of error
                first_err = syntax["errors"][0]
                past_fixes = self.memory.recall(first_err, k=2, kinds=["solution"])
                log.info("Found %d past solutions for error '%s'", len(past_fixes), first_err)
                # Mark step with diagnosis so next prompt/turn incorporates fix
                step.verification_result.diagnosis = f"Syntax error detected: {first_err}"
                return None  # Let model re-generate corrected code with feedback

        # 3. Test or Command Failed
        if tool in ("run_tests", "run_command"):
            log.info("Self-correcting command failure: %s", diag)
            # Check if missing package
            mod_match = re.search(r"no module named ['\"]([\w_]+)['\"]", verification.observations, re.I)
            if mod_match:
                pkg = mod_match.group(1)
                return {"tool": "run_command", "args": {"command": f"pip install {pkg}"}}

        return None


class AgenticOrchestrator:
    """Coordinates the full autonomous Plan -> Orchestrate -> Evaluate -> Correct cycle."""

    def __init__(self, hub, settings, memory, vision, auto, coding, design, comms):
        self.hub = hub
        self.settings = settings
        self.memory = memory
        self.vision = vision
        self.auto = auto
        self.coding = coding
        self.design = design
        self.comms = comms
        self.verifier = ActionVerifier(vision, auto, coding, memory)
        self.corrector = SelfCorrector(memory, coding, auto, self.verifier)
        self.current_plan: Optional[Plan] = None

    def build_plan_offline(self, goal: str) -> Plan:
        """Heuristic plan generation for offline execution."""
        low = goal.lower()
        steps: list[PlanStep] = []

        # Code verification and error fixing goal
        if any(k in low for k in ("check", "scan", "find error", "fix")) and any(k in low for k in ("code", "python", "matlab", "c")):
            target = "matlab" if "matlab" in low else "python" if "python" in low else "c"
            steps.append(PlanStep(
                id="scan",
                description=f"Scan project for {target.upper()} syntax and structural errors",
                tool="scan_project_errors",
                args={"folder": ""},
                verification="file_syntax"
            ))
            steps.append(PlanStep(
                id="evaluate",
                description="Evaluate scan results and report code status",
                tool="none",
                verification="none"
            ))

        # IDE Launching Goal
        elif "open" in low and any(k in low for k in ("vs code", "vscode", "matlab", "ide")):
            ide = "vscode" if "vs" in low or "code" in low else "matlab"
            steps.append(PlanStep(
                id="launch_ide",
                description=f"Launch {ide.upper()} editor",
                tool="open_ide",
                args={"ide": ide, "path": ""},
                verification="window_open"
            ))

        # YouTube Goal
        elif "youtube" in low or ("play" in low and "music" in low):
            query = re.sub(r"^(?:please )?(?:go and |play )*(?:on youtube )?", "", low).strip()
            steps.append(PlanStep(
                id="play_yt",
                description=f"Play '{query}' on YouTube with background ad-skipping",
                tool="play_youtube",
                args={"query": query},
                verification="none"
            ))

        # Fallback single step
        else:
            steps.append(PlanStep(
                id="exec_goal",
                description=f"Execute: {goal}",
                tool="none",
                verification="none"
            ))

        plan = Plan(goal=goal, steps=steps)
        self.current_plan = plan
        return plan

    def emit_plan(self, plan: Plan) -> None:
        """Broadcasts current plan state to the desktop overlay."""
        self.hub.emit({
            "type": "agent_plan",
            "plan": plan.as_dict()
        }, to=("desktop", "mobile"))

    def evaluate_and_correct(self, step: PlanStep, tool_name: str, args: dict, result: Any,
                             executor_callable: Callable[[str, dict], Any]) -> VerificationResult:
        """Runs the verification loop and performs autonomous self-correction if required."""
        step.status = "running"
        step.result = result

        # Step 1: Verification / Evaluation
        v_res = self.verifier.verify_action(tool_name, args, result)
        step.verification_result = v_res

        if v_res.passed:
            step.status = "verified"
            log.info("Step '%s' VERIFIED: %s", step.id, v_res.observations)
            return v_res

        # Step 2: Evaluation Failed -> Trigger Autonomous Correction
        step.status = "failed"
        log.warning("Step '%s' failed evaluation: %s (Diagnosis: %s)", step.id, v_res.observations, v_res.diagnosis)
        self.hub.toast(f"Verifying {step.description}: issue detected, self-correcting...")

        while step.retries < step.max_retries:
            step.retries += 1
            log.info("Self-correction attempt %d/%d for step '%s'...", step.retries, step.max_retries, step.id)
            correction = self.corrector.formulate_correction(step, v_res)

            if not correction:
                break

            corr_tool = correction.get("tool", tool_name)
            corr_args = correction.get("args", args)
            log.info("Executing corrected action: %s with %s", corr_tool, corr_args)

            try:
                retry_res = executor_callable(corr_tool, corr_args)
                retry_v = self.verifier.verify_action(corr_tool, corr_args, retry_res)
                if retry_v.passed:
                    step.status = "corrected"
                    step.result = retry_res
                    step.verification_result = retry_v
                    log.info("Step '%s' successfully CORRECTED on attempt %d!", step.id, step.retries)
                    self.hub.toast(f"Step corrected: {step.description}")
                    return retry_v
                v_res = retry_v
            except Exception as e:
                log.exception("Correction execution error: %s", e)

        log.warning("Step '%s' exhausted %d self-correction attempts.", step.id, step.max_retries)
        return v_res

    def record_successful_resolution(self, goal: str, summary: str, tools: list[str]) -> None:
        """Consolidates verified actions into persistent long-term memory."""
        s = str(summary or "")
        if any(b in s for b in ("/9j/", "data:image", "media_type", "{'type': 'image'")):
            return
        if len(s) > 200:
            s = s[:197] + "..."
        content = f"Solved '{goal}': {s}. Tools: {', '.join(tools)}."
        try:
            self.memory.remember(content, kind="solution", importance=0.8)
            log.info("Consolidated verified solution into memory: %s", content[:80])
        except Exception as e:
            log.warning("Failed to consolidate memory: %s", e)
