"""Offline responder: what Eli can say without an LLM key. Reads the screen with OCR,
explains common errors with a rules table, and describes what it sees."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger("eli.fallback")

from . import intents

RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"NameError: name '(\w+)' is not defined"),
     "Python says '{0}' isn't defined where it's used. It's either misspelled, assigned later than it's used, or missing an import."),
    (re.compile(r"IndexError: list index out of range"),
     "You're indexing past the end of a list. Check the loop bound; the classic culprit is range(len(x) + 1) or an off-by-one after removing items."),
    (re.compile(r"KeyError: '?([^'\n]+)'?"),
     "The dictionary has no key {0}. Use .get({0!r}) or check `if {0!r} in d` before reading it."),
    (re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'"),
     "The package '{0}' isn't installed in this interpreter. Run: pip install {0} (make sure it's the same Python/venv VS Code is using)."),
    (re.compile(r"ImportError: cannot import name '(\w+)'"),
     "'{0}' doesn't exist in that module, or you have a circular import. Check the name and the module's version."),
    (re.compile(r"AttributeError: '(\w+)' object has no attribute '(\w+)'"),
     "A {0} object has no attribute '{1}'. Usually the variable isn't the type you expect (often None) or the method name is misspelled."),
    (re.compile(r"TypeError: (.+)"),
     "Type error: {0}. Check the argument count/types on that call, and whether a value is None."),
    (re.compile(r"ValueError: (.+)"),
     "Value error: {0}. The value has the right type but a bad content (e.g. int('abc'))."),
    (re.compile(r"ZeroDivisionError"),
     "Something divides by zero. Guard the divisor (`if n:`) before dividing."),
    (re.compile(r"FileNotFoundError.*'([^']+)'"),
     "The file '{0}' can't be found. Check the path and the current working directory (relative paths resolve from where you run the script)."),
    (re.compile(r"SyntaxError"),
     "There's a syntax error at the line shown. Look just before that spot for a missing colon, bracket, quote or comma."),
    (re.compile(r"IndentationError"),
     "Indentation is inconsistent. Make sure the block uses the same indent (4 spaces) and no tabs are mixed in."),
    (re.compile(r"RecursionError"),
     "A function keeps calling itself without reaching a base case."),
    (re.compile(r"Cannot find name '(\w+)'"),
     "TypeScript can't find '{0}'. Import it, declare it, or fix the spelling."),
    (re.compile(r"(\w+) is not defined"),
     "'{0}' is used before it's declared or imported."),
    (re.compile(r"Cannot read propert(?:y|ies) of (undefined|null)(?: \(reading '(\w+)'\))?"),
     "You're reading a property from {0}. The object you expected isn't there yet: check the lookup that produced it (or the async timing)."),
    (re.compile(r"Unexpected token"),
     "A syntax error: an unexpected token. Check for a missing bracket, comma or quote right before it."),
    (re.compile(r"Module not found: Can't resolve '([^']+)'"),
     "The import '{0}' can't be resolved. Install it (npm install {0}) or fix the relative path."),
    (re.compile(r"EADDRINUSE"),
     "That port is already in use. Stop the other process or change the port."),
    (re.compile(r"ENOENT"),
     "A file or directory the command needs doesn't exist. Check the path."),
    (re.compile(r"npm ERR!"),
     "npm failed. Read the first 'npm ERR!' line: it usually names a missing package, a bad script name, or a permissions problem."),
    (re.compile(r"exit code ([1-9]\d*)"),
     "The process exited with code {0}, meaning it failed; the real cause is in the lines above it."),
]
LOCATION_RE = re.compile(r'File "([^"]+)", line (\d+)')
TS_LOCATION_RE = re.compile(r"([\w./\\-]+\.(?:ts|tsx|js|jsx|py))[:(](\d+)")


def diagnose(snippet: str) -> str:
    loc = ""
    m = LOCATION_RE.findall(snippet)
    if m:
        f, line = m[-1]
        loc = f"The failing line is in {f.split(chr(92))[-1].split('/')[-1]} at line {line}. "
    else:
        m2 = TS_LOCATION_RE.search(snippet)
        if m2:
            loc = f"Look at {m2.group(1)} line {m2.group(2)}. "
    for rx, template in RULES:
        h = rx.search(snippet)
        if h:
            return loc + template.format(*[g or "" for g in h.groups()])
    return loc + "I can see an error but don't recognise the pattern. Read the last line of the message: it names the exception, and the line above it is where it happened."


def curriculum_3d_modeling() -> str:
    return (
        "Here is the complete step-by-step roadmap to learn 3D modeling from scratch:\n\n"
        "1. Understand the Foundations (Mental Model):\n"
        "   - Geometry Elements: Vertices (points), Edges (lines), Faces (polygons).\n"
        "   - Coordinate Space: X (width/red), Y (depth/green), Z (height/blue).\n"
        "   - Mesh vs CAD: Polygonal meshes (Blender) are built from surface facets for organic shapes and visuals; Parametric CAD (Fusion 360/FreeCAD) uses exact mathematical curves and sketches for real-world engineering.\n\n"
        "2. Choose Your Primary Tool:\n"
        "   - For CGI, Game Assets, Animation & Organic Sculpting: Download Blender (free, open source).\n"
        "   - For Precision Parts, Engineering, Functional 3D Printing: Use Autodesk Fusion 360 (free personal tier) or FreeCAD.\n\n"
        "3. Master the 5 Core Modeling Tools (The 80/20 Rule):\n"
        "   - Extrude (E): Pulls new faces outward or pushes them inward.\n"
        "   - Inset (I): Creates an interior offset face inside the selection.\n"
        "   - Bevel (Ctrl+B): Rounds or chamfers sharp edges to catch realistic light highlights.\n"
        "   - Loop Cut (Ctrl+R): Adds edge loops to define form and support curves.\n"
        "   - Knife tool / Boolean: Cuts custom geometry or performs unions/differences.\n\n"
        "4. Learn Clean Topology (The Professional Standard):\n"
        "   - Aim for all-quads (4-sided polygons). Avoid N-gons (5+ sides) and minimize triangles on curved surfaces.\n"
        "   - Watch your Normals: Ensure face normals always point outward (Shift+N in Blender).\n"
        "   - Watertightness: For 3D printing, ensure zero non-manifold edges or holes.\n\n"
        "5. Materials, Lighting & Camera:\n"
        "   - PBR Shading: Base Color, Roughness (shine vs matte), and Metallic.\n"
        "   - 3-Point Lighting: Key light (primary), Fill light (softens shadows), Rim/Back light (separates object from background).\n\n"
        "6. Practical Milestone Projects:\n"
        "   - Project 1: Ceramic Coffee Mug (learn cylinders, extruding handles, beveling lips, subdivision surface).\n"
        "   - Project 2: Snap-fit Electronics Enclosure (learn exact dimensions, tolerances of 0.2-0.4mm, screw bosses, draft angles).\n"
        "   - Project 3: Sci-Fi Crate or Weapon Prop (learn hard-surface modeling, booleans, edge beveling, and texture mapping).\n\n"
        "7. Exporting & Manufacturing:\n"
        "   - 3D Printing: Export STL or 3MF -> Slice in Bambu Studio / Cura / PrusaSlicer.\n"
        "   - Games & Web: Export GLTF/GLB or FBX with packed textures."
    )


class FallbackResponder:
    def __init__(self, vision, memory, auto, llm, broker):
        self.vision = vision
        self.memory = memory
        self.auto = auto
        self.llm = llm
        self.broker = broker

    async def respond(self, text: str) -> str:
        if not text or not text.strip():
            return "Hello! I'm Eli. How can I help you today?"
        low = text.lower().strip()
        if low in ("hello", "hey", "hi", "howdy", "good morning", "good afternoon", "good evening", "hello ellie", "hello eli", "hey eli", "hey ellie"):
            return "Hello! I'm Eli. How can I help you today?"
        if any(k in low for k in ("3d model", "3d design", "learn 3d", "learn blender", "steps to learn 3d")):
            return curriculum_3d_modeling()
        if intents.wants_screen(text):
            ok = await self.broker.ensure_screen("Eli needs to capture the screen once to answer your question.")
            if not ok:
                return "I can't look at the screen without your permission. Enable 'See screen' in my panel or allow the request."
            frame = await asyncio.to_thread(self.vision.capture_now)
            await asyncio.to_thread(frame.ocr)
            if intents.wants_error_help(text):
                return self.explain_error(frame)
            return self.describe(frame)
        return self.generic(text)

    def explain_error(self, frame) -> str:
        snippet = frame.error_snippet()
        app = frame.window.describe()
        if not snippet:
            return (f"I can see {app}, but I don't spot an error message on screen right now. "
                    f"Scroll so the error is visible (the terminal or Problems panel) and ask me again.")
        return f"Here's what I read in {app}:\n{snippet[:700]}\n\n{diagnose(snippet)}"

    def describe(self, frame) -> str:
        return self.vision.describe_locally(frame)

    def generic(self, text: str) -> str:
        clean_q = text.strip().strip("?.!\"'")
        if not clean_q:
            return "Hello! I'm Eli. How can I help you today?"

        # 1. Recall from local memory
        related = self.memory.recall(clean_q, k=2)
        mem_hint = ""
        if related:
            mem_hint = "From memory: " + "; ".join(m.content for m in related) + "\n\n"

        # 2. Try instant web search via DuckDuckGo API (100% free, zero-key)
        abstract = ""
        try:
            q_enc = urllib.parse.quote_plus(clean_q)
            api_url = f"https://api.duckduckgo.com/?q={q_enc}&format=json&no_html=1&skip_disambig=1"
            req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Eli/1.0"})
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                abstract = (data.get("AbstractText") or data.get("Answer") or "").strip()
        except Exception as e:
            log.debug("DuckDuckGo instant answer lookup failed: %s", e)

        # 3. Open Google search in the browser so the user gets full interactive results
        try:
            self.auto.web_search(clean_q, "google")
        except Exception as e:
            log.debug("Auto web search launch failed: %s", e)

        if abstract:
            return f"{mem_hint}{abstract}\n\nI have also opened Google search results for '{clean_q}' in your browser."
        elif mem_hint:
            return f"{mem_hint}I have also opened Google search for '{clean_q}' in your browser."
        else:
            return f"I searched Google for '{clean_q}' and opened the top results in your browser."
