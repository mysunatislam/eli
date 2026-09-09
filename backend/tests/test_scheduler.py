"""Scheduler tests: time parsing, persisted jobs, reminder delivery, agent-run jobs with NO_CHANGE
suppression and rescheduling, watch jobs finishing, voice intents.
    .venv\\Scripts\\python.exe tests\\test_scheduler.py
"""
import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["ELI_DATA_DIR"] = tempfile.mkdtemp(prefix="eli-sched-")

from eli import intents, timeparse  # noqa: E402
from eli.agents.memory_agent import MemoryAgent  # noqa: E402
from eli.agents.scheduler import Scheduler  # noqa: E402

failures = 0


def check(cond, msg):
    global failures
    print(("  ok   " if cond else "  FAIL ") + msg)
    failures += (not cond)


class Hub:
    def __init__(self):
        self.events = []
    def emit(self, e, to=None):
        self.events.append(e)
    def status(self, **kw):
        self.events.append({"type": "status", **kw})
    def transcript(self, role, text, source="desktop"):
        self.events.append({"type": "transcript", "role": role, "text": text})
    def notify(self, title, body=""):
        self.events.append({"type": "notify", "title": title, "body": body})
    def toast(self, t):
        self.events.append({"type": "toast", "text": t})
    def tool(self, n, d=""):
        self.events.append({"type": "tool", "name": n, "detail": d})
    def set_state(self, s):
        pass


class Settings:
    d = {"voice_replies": False, "private_mode": False}
    def get(self, k, default=None):
        return self.d.get(k, default)


class StubAgent:
    def __init__(self):
        self.calls = []
        self.reply = "NO_CHANGE"
    async def handle(self, text, source="desktop"):
        self.calls.append((text, source))
        return self.reply


async def main():
    now = time.time()
    base = datetime.fromtimestamp(now)
    print("[timeparse]")
    w = timeparse.parse_when("in 20 minutes to stretch", now)
    check(w and w["kind"] == "once" and abs(w["at"] - now - 1200) < 2, "in 20 minutes")
    w = timeparse.parse_when("every 30 minutes check the build", now)
    check(w and w["kind"] == "every" and w["seconds"] == 1800, "every 30 minutes")
    w = timeparse.parse_when("every hour", now)
    check(w and w["seconds"] == 3600, "every hour")
    w = timeparse.parse_when("at 5pm call Sam", now)
    dt = datetime.fromtimestamp(w["at"]) if w else None
    check(w and dt.hour == 17 and dt.minute == 0 and w["at"] > now, f"at 5pm -> {dt}")
    w = timeparse.parse_when("tomorrow at 9 send the report", now)
    dt = datetime.fromtimestamp(w["at"]) if w else None
    check(w and dt.hour == 9 and dt.date() > base.date(), f"tomorrow at 9 -> {dt}")
    w = timeparse.parse_when("every day at 8am", now)
    dt = datetime.fromtimestamp(w["at"]) if w else None
    check(w and w["seconds"] == 86400 and dt.hour == 8, f"every day at 8am -> {dt}")
    check(timeparse.parse_when("open notepad") is None, "no time -> None")
    check(timeparse.strip_when("in 20 minutes to stretch my legs") == "stretch my legs", "strip 'in 20 minutes to'")
    check(timeparse.strip_when("every hour check whether the build passed") == "check whether the build passed", "strip 'every hour'")

    print("[intents]")
    for text, kind in [("remind me in 20 minutes to stretch", "remind"), ("every hour check whether the build passed", "job_every"),
                       ("in 2 hours open my report", "job_in"), ("keep watching the download and tell me when it finishes", "job_watch"),
                       ("what are you working on?", "list_jobs"), ("cancel job 3", "cancel_job"), ("cancel all reminders", "cancel_jobs"),
                       ("open notepad", "open")]:
        m = intents.match(text)
        check(m and m[0] == kind, f"{text!r} -> {m[0] if m else None}")

    print("[jobs + scheduler]")
    from eli import config
    mem = MemoryAgent(config.DATA_DIR)
    hub, agent = Hub(), StubAgent()
    sch = Scheduler(hub, Settings(), mem, agent)
    j = sch.add_from_text("in 5 seconds to drink water", kind="reminder")
    check(j and j["kind"] == "reminder" and j["text"] == "drink water" and 0 < j["next_run"] - now < 8, f"reminder parsed: {j and j['text']}")
    r = sch.add_from_text("every hour check whether the build passed", kind="recurring")
    check(r and r["every"] == 3600 and r["text"].startswith("check whether"), "recurring job stored")
    wj = sch.add_from_text("Check the download. Goal: tell the user as soon as it finishes.", kind="watch", until_done=True)
    check(wj and wj["every"] == 120 and wj["until_done"], "watch job defaults to every 2 min until done")
    check(len(mem.jobs()) == 3 and mem.stats()["jobs"] == 3, "three active jobs persisted")
    # reload from disk: persistence across restarts
    mem2 = MemoryAgent(config.DATA_DIR)
    check(len(mem2.jobs()) == 3, "jobs survive a fresh MemoryAgent (restart)")

    # reminder due -> delivered + done
    mem.job_update(j["id"], next_run=now - 1)
    await sch.tick()
    delivered = [e for e in hub.events if e.get("type") == "transcript" and "Reminder: drink water" in e["text"]]
    check(delivered and mem.job_get(j["id"])["status"] == "done", "reminder delivered and marked done")
    check(any(e.get("type") == "nudge" and e["actions"][1]["id"] == f"job:done:{j['id']}" for e in hub.events), "reminder bubble has Done/Snooze")
    # recurring due -> agent ran with scheduler source, NO_CHANGE stays quiet, rescheduled
    mem.job_update(r["id"], next_run=now - 1)
    await sch.tick()
    check(agent.calls and agent.calls[-1][1] == "scheduler" and "#%d" % r["id"] in agent.calls[-1][0], "recurring job ran through the agent")
    fresh = mem.job_get(r["id"])
    check(fresh["status"] == "active" and fresh["runs"] == 1 and fresh["next_run"] > now + 3500, "recurring rescheduled an hour later")
    # watch job: model finishes it
    agent.reply = "The download finished."
    mem.job_update(wj["id"], next_run=now - 1)
    sch.finish(wj["id"], "download finished")
    await sch.tick()
    check(mem.job_get(wj["id"])["status"] == "done", "watch job finished via finish_task")
    # snooze / cancel
    sch.snooze(j["id"], 600)
    check(mem.job_get(j["id"])["status"] == "active" and mem.job_get(j["id"])["next_run"] > now + 500, "snooze re-activates 10 min later")
    check(sch.cancel(j["id"]) and mem.job_get(j["id"])["status"] == "cancelled", "cancel")
    check("Scheduled work" in sch.summary() and "#%d" % r["id"] in sch.summary(), "summary lists active jobs")
    check(sch.context_lines(), "context lines for the system prompt")

    print("\nSCHEDULER TESTS", "PASS" if not failures else f"FAIL ({failures})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
