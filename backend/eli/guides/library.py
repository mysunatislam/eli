"""Guide library: every guide Eli has learned, on disk, findable by name, shareable as a file.

DATA_DIR/guides/<id>.json holds one workflow each (the same dict shape the engine runs). On start the
library loads them all and registers them with eli.guides.WORKFLOWS so "guide me through <name>" finds
them exactly like the built-ins. Export writes a single self-contained .eliguide.json; import reads
one (from a colleague, a vendor, a course) and registers it. Guides never contain screenshots or
typed text unless the author chose to keep typed text at recording time.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from . import WORKFLOWS, register, unregister

log = logging.getLogger("eli.guide.library")

EXPORT_SUFFIX = ".eliguide.json"
_WORD = re.compile(r"[a-z0-9]{3,}")


class GuideLibrary:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "guides"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.guides: dict[str, dict] = {}

    # -- persistence -----------------------------------------------------------------------------------

    def load(self) -> int:
        n = 0
        for p in sorted(self.dir.glob("*.json")):
            try:
                wf = json.loads(p.read_text("utf-8"))
                if self._valid(wf):
                    self.guides[wf["id"]] = wf
                    register(wf)
                    n += 1
            except Exception as e:
                log.warning("skipping guide %s: %s", p.name, e)
        if n:
            log.info("loaded %d learned guide(s)", n)
        return n

    def add(self, wf: dict) -> dict:
        if not self._valid(wf):
            raise ValueError("not a valid guide")
        wf.setdefault("created", time.time())
        self.guides[wf["id"]] = wf
        register(wf)
        (self.dir / f"{wf['id']}.json").write_text(json.dumps(wf, indent=1), "utf-8")
        return wf

    def delete(self, guide_id: str) -> bool:
        wf = self.guides.pop(guide_id, None)
        if wf is None:
            return False
        unregister(guide_id)
        p = self.dir / f"{guide_id}.json"
        if p.exists():
            p.unlink()
        return True

    def rename(self, guide_id: str, name: str) -> Optional[dict]:
        wf = self.guides.get(guide_id)
        if not wf:
            return None
        from .segmenter import keywords_for
        wf["name"] = name.strip()[:80]
        wf["keywords"] = keywords_for(wf["name"], wf.get("goal", ""))
        return self.add(wf)

    # -- lookup ------------------------------------------------------------------------------------------

    def get(self, guide_id: str) -> Optional[dict]:
        return self.guides.get(guide_id)

    def find(self, text: str, min_score: float = 0.34) -> Optional[dict]:
        """Best learned guide for a request, by word overlap with name + goal (exact name wins)."""
        low = (text or "").lower()
        if not low.strip():
            return None
        best, best_s = None, 0.0
        for wf in self.guides.values():
            name = wf.get("name", "").lower()
            if name and name in low:
                return wf
            words = set(_WORD.findall(f"{name} {wf.get('goal', '')}".lower()))
            if not words:
                continue
            hit = sum(1 for w in words if w in low)
            s = hit / float(len(words))
            if s > best_s:
                best, best_s = wf, s
        return best if best_s >= min_score else None

    def list(self) -> list[dict]:
        out = []
        for wf in sorted(self.guides.values(), key=lambda w: -float(w.get("created") or 0)):
            app = wf["apps"].get(wf.get("default_app", ""), {}) if wf.get("apps") else {}
            out.append({"id": wf["id"], "name": wf.get("name", ""), "app": app.get("label", ""),
                        "steps": len(app.get("steps", [])), "recorded": bool(wf.get("recorded")),
                        "created": wf.get("created")})
        return out

    def describe(self) -> str:
        items = self.list()
        if not items:
            return "I haven't learned any guides yet. Say “teach you how to …” and show me once."
        return "\n".join(f"- {g['name']} ({g['app'] or 'app?'}, {g['steps']} steps) [id {g['id']}]" for g in items)

    # -- sharing ----------------------------------------------------------------------------------------

    def export(self, guide_id: str, path: str = "") -> Optional[Path]:
        wf = self.guides.get(guide_id)
        if not wf:
            return None
        target = Path(path) if path else self.dir / f"{wf['id']}{EXPORT_SUFFIX}"
        if target.is_dir():
            target = target / f"{wf['id']}{EXPORT_SUFFIX}"
        payload = {"format": "eliguide", "version": 1, "exported": time.time(), "guide": self._strip_private(wf)}
        target.write_text(json.dumps(payload, indent=1), "utf-8")
        return target

    def import_file(self, path: str) -> dict:
        data = json.loads(Path(path).read_text("utf-8"))
        wf = data.get("guide") if isinstance(data, dict) and data.get("format") == "eliguide" else data
        if not self._valid(wf):
            raise ValueError("that file is not an Eli guide")
        wf = dict(wf)
        wf["imported"] = time.time()
        if wf["id"] in self.guides:
            wf["id"] = f"{wf['id']}_{int(time.time()) % 100000}"
        return self.add(wf)

    # -- internals --------------------------------------------------------------------------------------

    @staticmethod
    def _strip_private(wf: dict) -> dict:
        """Exported guides never carry a recording id or absolute paths."""
        out = json.loads(json.dumps(wf))
        out.pop("recording_id", None)
        for app in out.get("apps", {}).values():
            for s in app.get("steps", []):
                s.pop("events", None)
        return out

    @staticmethod
    def _valid(wf) -> bool:
        if not isinstance(wf, dict) or not wf.get("id") or not isinstance(wf.get("apps"), dict) or not wf["apps"]:
            return False
        da = wf.get("default_app") or next(iter(wf["apps"]))
        app = wf["apps"].get(da)
        return isinstance(app, dict) and isinstance(app.get("steps"), list) and len(app["steps"]) >= 1 and \
            all(isinstance(s, dict) and s.get("title") and s.get("target") for s in app["steps"])
