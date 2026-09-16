"""Scheduler: persistent, continuous work.

Jobs live in the encrypted memory database and survive idle time and restarts:
- reminder   : at a time (or every N) Eli speaks/shows the reminder, with Done / Snooze.
- task       : at a time Eli performs an instruction through the normal agent (tools, screen, apps).
- recurring  : the same, every N seconds/minutes/hours until cancelled (or max runs).
- watch      : recurring until the goal is reached; the model calls finish_task when it is.
A background loop ticks every 10 s. Scheduled runs that find nothing to report reply NO_CHANGE and stay
silent, so a "check my build every 10 minutes" job only speaks up when something happened.
Active jobs are listed in Eli's system context, so it never forgets an assignment mid-conversation.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from .. import timeparse

log = logging.getLogger("eli.scheduler")

MIN_EVERY = 60
DEFAULT_MAX_RUNS = 10000   # ~7 days at one run/min; long watches must not expire silently
WATCH_EVERY = 120


class Scheduler:
    TICK = 10

    def __init__(self, hub, settings, memory, agent, speech=None):
        self.hub, self.settings, self.memory, self.agent, self.speech = hub, settings, memory, agent, speech
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._busy = False

    # -- lifecycle ------------------------------------------------------------------------------------
    def start(self, loop) -> None:
        self.running = True
        self._task = loop.create_task(self._loop())

    def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        await asyncio.sleep(5)
        missed = [j for j in self.memory.jobs() if j["next_run"] <= time.time() - 120]
        if missed:
            self.hub.toast(f"{len(missed)} scheduled job{'s' if len(missed) != 1 else ''} came due while I was off; running now.")
        while self.running:
            try:
                await self.tick()
            except Exception as e:
                log.warning("scheduler tick failed: %s", e)
            await asyncio.sleep(self.TICK)

    async def tick(self) -> None:
        if self._busy:
            return
        now = time.time()
        due = [j for j in self.memory.jobs() if j["status"] == "active" and j["next_run"] <= now]
        for job in sorted(due, key=lambda j: j["next_run"]):
            self._busy = True
            try:
                await self.execute(job)
            finally:
                self._busy = False

    # -- creation --------------------------------------------------------------------------------------
    def add(self, text: str, when: dict, kind: str = "task", until_done: bool = False, source: str = "user") -> dict:
        every = when.get("seconds") if when.get("kind") == "every" else None
        if every:
            every = max(MIN_EVERY, int(every))
        job = self.memory.job_add(kind=kind, text=text.strip(), next_run=float(when["at"]), every=every,
                                  max_runs=DEFAULT_MAX_RUNS if every else 1, until_done=until_done, source=source)
        self.hub.status(jobs=self.list_public())
        log.info("job #%s added: %s (%s)", job["id"], text[:60], timeparse.describe_when(when))
        return job

    def add_from_text(self, text: str, kind: str = "task", until_done: bool = False) -> Optional[dict]:
        when = timeparse.parse_when(text)
        body = timeparse.strip_when(text)
        if not when:
            if kind == "watch" or until_done:
                when = {"kind": "every", "seconds": WATCH_EVERY, "at": time.time() + WATCH_EVERY}
            else:
                return None
        if not body:
            return None
        return self.add(body, when, kind=kind, until_done=until_done)

    # -- execution -------------------------------------------------------------------------------------
    async def execute(self, job: dict) -> None:
        jid = job["id"]
        log.info("running job #%s (%s): %s", jid, job["kind"], job["text"][:80])
        if job["kind"] == "reminder":
            text = f"Reminder: {job['text']}"
            self.hub.transcript("eli", text, "scheduler")
            self.hub.notify("Eli reminder", job["text"])
            self.hub.emit({"type": "nudge", "id": f"job-{jid}", "text": text, "actions": [
                {"id": f"job:snooze:{jid}", "label": "Snooze 10 min"}, {"id": f"job:done:{jid}", "label": "Done", "primary": True}]})
            if self.speech and self.settings.get("voice_replies", True):
                self.speech.say_now(text)
            self._reschedule(job, "reminded")
            return
        if self.settings.get("private_mode") and job.get("needs_screen"):
            self._reschedule(job, "skipped: private mode")
            return
        prompt = (f"[Scheduled job #{jid} ({job['kind']}{', every ' + self._every_text(job) if job.get('every') else ''}), run {job['runs'] + 1}] "
                  f"{job['text']}\n"
                  "This run happens automatically. Do what it asks with your tools, briefly. "
                  "If nothing needs the user's attention right now, reply with exactly NO_CHANGE and nothing else. ")
        if job.get("until_done"):
            prompt += (
                f"This is a standing watch. Call finish_task with job_id={jid} ONLY if the job text names a definite end state "
                "(e.g. 'until the download finishes') AND that end state has clearly been reached now. "
                "If the job is open-ended (words like 'whenever', 'always', 'keep', 'automatically', or no end condition), it runs until "
                "the user cancels it: NEVER call finish_task for it, no matter how many runs found nothing - just act or reply NO_CHANGE."
            )
        try:
            reply = await self.agent.handle(prompt, source="scheduler")
        except Exception as e:
            reply = f"job failed: {e}"
            log.warning("job #%s failed: %s", jid, e)
        self._reschedule(job, reply[:300])

    def _reschedule(self, job: dict, result: str) -> None:
        jid = job["id"]
        fresh = self.memory.job_get(jid)
        if fresh is None or fresh["status"] != "active":
            self.hub.status(jobs=self.list_public())
            return
        runs = job["runs"] + 1
        fields = {"runs": runs, "last_run": time.time(), "last_result": result}
        if job.get("every") and (not job.get("max_runs") or runs < job["max_runs"]):
            fields["next_run"] = time.time() + job["every"]
        else:
            fields["status"] = "done"
        self.memory.job_update(jid, **fields)
        self.hub.status(jobs=self.list_public())

    # -- controls --------------------------------------------------------------------------------------
    def cancel(self, jid: int) -> bool:
        ok = self.memory.job_update(jid, status="cancelled")
        self.hub.status(jobs=self.list_public())
        return ok

    def cancel_all(self) -> int:
        n = 0
        for j in self.memory.jobs():
            n += int(self.memory.job_update(j["id"], status="cancelled"))
        self.hub.status(jobs=self.list_public())
        return n

    def finish(self, jid: int, summary: str = "") -> bool:
        ok = self.memory.job_update(jid, status="done", last_result=summary or "done")
        self.hub.status(jobs=self.list_public())
        return ok

    def snooze(self, jid: int, seconds: int = 600) -> bool:
        ok = self.memory.job_update(jid, status="active", next_run=time.time() + seconds)
        self.hub.status(jobs=self.list_public())
        return ok

    def action(self, action: str) -> str:
        # "job:done:3" / "job:snooze:3" from the bubble buttons
        parts = action.split(":")
        if len(parts) != 3:
            return ""
        _, what, sid = parts
        jid = int(sid)
        if what == "done":
            self.finish(jid, "done by user")
            return "Done."
        if what == "snooze":
            self.snooze(jid, 600)
            return "Snoozed for 10 minutes."
        return ""

    # -- presentation ----------------------------------------------------------------------------------
    @staticmethod
    def _every_text(job: dict) -> str:
        s = int(job.get("every") or 0)
        if not s:
            return ""
        if s % 86400 == 0:
            return f"{s // 86400} day"
        if s % 3600 == 0:
            return f"{s // 3600} hour"
        return f"{max(1, s // 60)} min"

    def describe(self, job: dict) -> str:
        when = ("every " + self._every_text(job)) if job.get("every") else "once"
        nxt = job["next_run"] - time.time()
        due = ("due now" if nxt <= 0 else (f"in {int(nxt // 60)} min" if nxt < 3600 * 12 else time.strftime("%a %H:%M", time.localtime(job["next_run"]))))
        return f"#{job['id']} {job['kind']} {when}, next {due}: {job['text']}"

    def list_public(self) -> list[dict]:
        out = []
        for j in self.memory.jobs():
            out.append({"id": j["id"], "kind": j["kind"], "text": j["text"], "every": j.get("every"), "next_run": j["next_run"],
                        "runs": j["runs"], "last_result": (j.get("last_result") or "")[:120], "desc": self.describe(j)})
        return out

    def summary(self) -> str:
        jobs = self.memory.jobs()
        if not jobs:
            return "Nothing is scheduled. Say things like “remind me in 20 minutes to …”, “every hour check …”, or “keep watching … until …”."
        return "Scheduled work:\n" + "\n".join("- " + self.describe(j) for j in jobs)

    def context_lines(self) -> str:
        jobs = self.memory.jobs()
        if not jobs:
            return ""
        return "\n".join(f"- {self.describe(j)}" + (f" (last: {j['last_result'][:80]})" if j.get("last_result") else "") for j in jobs[:8])
