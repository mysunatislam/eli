"""Bridges into 3D/CAD applications so Eli can debug real geometry, not just pixels.

Blender:
  - blender_check_file(path): runs Blender headless (`blender -b file --python check.py`) and
    reports non-manifold edges, loose geometry, zero-area faces, n-gons, flipped normals,
    unapplied scale and modifier state for every mesh object.
  - blender_run_python(code): executes Python inside the *running* Blender session through the
    Eli Bridge add-on (integrations/blender/eli_bridge.py, localhost:8791). Confirmation-gated.
  - blender_check_live(): the same validation as check_file, but on the open session via the bridge.

SolidWorks (Windows COM, needs SolidWorks running):
  - solidworks_check(): rebuilds the active document and lists features with errors/warnings,
    mass properties, and interference results for assemblies.

Both degrade to a clear message when the application is not installed or not running.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger("eli.cad")

BLENDER_CHECK_SCRIPT = r'''
import json, sys
import bpy, bmesh, mathutils

report = {"file": bpy.data.filepath, "objects": [], "issues": []}
for obj in bpy.data.objects:
    if obj.type != "MESH":
        continue
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table(); bm.edges.ensure_lookup_table(); bm.faces.ensure_lookup_table()
    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    boundary = sum(1 for e in bm.edges if e.is_boundary)
    loose_verts = sum(1 for v in bm.verts if not v.link_edges)
    loose_edges = sum(1 for e in bm.edges if not e.link_faces)
    zero_faces = sum(1 for f in bm.faces if f.calc_area() < 1e-9)
    ngons = sum(1 for f in bm.faces if len(f.verts) > 4)
    center = mathutils.Vector((0.0, 0.0, 0.0))
    if len(bm.verts):
        for v in bm.verts:
            center += v.co
        center /= len(bm.verts)
    flipped = sum(1 for f in bm.faces if (f.calc_center_median() - center).dot(f.normal) < 0)
    bm.free()
    unapplied_scale = any(abs(s - 1.0) > 1e-4 for s in obj.scale)
    mods = []
    for m in obj.modifiers:
        flag = ""
        if not m.show_viewport:
            flag = " (viewport off)"
        if not m.show_render:
            flag += " (render off)"
        mods.append(m.type.lower() + ":" + m.name + flag)
    entry = {"name": obj.name, "verts": len(me.vertices), "faces": len(me.polygons), "non_manifold_edges": non_manifold,
             "boundary_edges": boundary, "loose_verts": loose_verts, "loose_edges": loose_edges, "zero_area_faces": zero_faces,
             "ngons": ngons, "flipped_normal_faces": flipped, "unapplied_scale": unapplied_scale, "modifiers": mods,
             "dimensions": [round(d, 3) for d in obj.dimensions]}
    report["objects"].append(entry)
    n = obj.name
    if non_manifold:
        report["issues"].append(n + ": " + str(non_manifold) + " non-manifold edges (booleans, 3D printing and remesh will misbehave). Edit Mode > Select > Select All by Trait > Non Manifold, then Mesh > Clean Up > Fill Holes / Merge by Distance.")
    if loose_verts or loose_edges:
        report["issues"].append(n + ": " + str(loose_verts) + " loose vertices and " + str(loose_edges) + " loose edges. Mesh > Clean Up > Delete Loose.")
    if zero_faces:
        report["issues"].append(n + ": " + str(zero_faces) + " zero-area faces. Mesh > Clean Up > Degenerate Dissolve.")
    if flipped and len(me.polygons) and flipped / len(me.polygons) > 0.3:
        report["issues"].append(n + ": ~" + str(flipped) + " faces look inward (flipped normals; shading and boolean errors). Mesh > Normals > Recalculate Outside.")
    if unapplied_scale:
        report["issues"].append(n + ": object scale is not 1 (" + ", ".join(str(round(s, 3)) for s in obj.scale) + "). Apply it (Ctrl+A > Scale) before modifiers, booleans or export.")
    if ngons > 50:
        report["issues"].append(n + ": " + str(ngons) + " n-gons; consider triangulating or quad topology for clean deformation/export.")
if not report["objects"]:
    report["issues"].append("No mesh objects found.")
print("ELI_REPORT_JSON " + json.dumps(report))
'''

BLENDER_BRIDGE_PORT = 8791


def find_blender() -> Optional[str]:
    exe = shutil.which("blender")
    if exe:
        return exe
    for pattern in (r"C:\Program Files\Blender Foundation\Blender*\blender.exe",
                    r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe",
                    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Blender Foundation\Blender*\blender.exe")):
        hits = sorted(glob.glob(pattern), reverse=True)
        if hits:
            return hits[0]
    return None


def _format_report(report: dict) -> str:
    lines = [f"Blender check of {report.get('file') or 'the open session'}:"]
    for o in report.get("objects", []):
        lines.append(f"- {o['name']}: {o['verts']} verts, {o['faces']} faces, dims {o['dimensions']}, non-manifold {o['non_manifold_edges']}, "
                     f"boundary {o['boundary_edges']}, loose v/e {o['loose_verts']}/{o['loose_edges']}, zero-area {o['zero_area_faces']}, "
                     f"n-gons {o['ngons']}, scale applied {'no' if o['unapplied_scale'] else 'yes'}"
                     + (f", modifiers: {', '.join(o['modifiers'])}" if o["modifiers"] else ""))
    issues = report.get("issues", [])
    lines.append("Issues:" if issues else "No mesh problems found.")
    lines += [f"  * {i}" for i in issues]
    return "\n".join(lines)


class BlenderBridge:
    def check_file(self, path: str, timeout: int = 180) -> str:
        p = Path(path).expanduser()
        if not p.is_file() or p.suffix.lower() != ".blend":
            return f"Not a .blend file: {path}"
        exe = find_blender()
        if not exe:
            return "Blender is not installed (or not on PATH), so I can't run a headless check. Install Blender or use check_mesh_file on an exported STL."
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(BLENDER_CHECK_SCRIPT)
            script = f.name
        try:
            r = subprocess.run([exe, "-b", str(p), "--python", script], capture_output=True, text=True, timeout=timeout,
                               creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return f"Blender took longer than {timeout}s to analyse {p.name}."
        except Exception as e:
            return f"Couldn't run Blender: {e}"
        finally:
            try:
                os.unlink(script)
            except OSError:
                pass
        for line in (r.stdout or "").splitlines():
            if line.startswith("ELI_REPORT_JSON "):
                try:
                    return _format_report(json.loads(line[len("ELI_REPORT_JSON "):]))
                except Exception:
                    pass
        tail = ((r.stderr or "") + (r.stdout or ""))[-1500:]
        return f"Blender ran but produced no report (exit {r.returncode}). Output:\n{tail}"

    def run_python(self, code: str, timeout: float = 30.0) -> str:
        """Execute code in the live Blender session via the Eli Bridge add-on."""
        try:
            with socket.create_connection(("127.0.0.1", BLENDER_BRIDGE_PORT), timeout=3) as s:
                s.sendall((json.dumps({"code": code}) + "\n").encode("utf-8"))
                s.settimeout(timeout)
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            reply = json.loads(buf.decode("utf-8", "replace") or "{}")
            out = reply.get("output", "")
            return out.strip() or "(no output)"
        except (ConnectionRefusedError, OSError, socket.timeout):
            return ("The Eli Bridge add-on isn't running in Blender. Install backend/integrations/blender/eli_bridge.py "
                    "(Edit > Preferences > Add-ons > Install) and enable it; it listens on 127.0.0.1:8791.")
        except Exception as e:
            return f"Bridge error: {e}"

    def check_live(self) -> str:
        out = self.run_python(BLENDER_CHECK_SCRIPT)
        for line in out.splitlines():
            if line.startswith("ELI_REPORT_JSON "):
                try:
                    return _format_report(json.loads(line[len("ELI_REPORT_JSON "):]))
                except Exception:
                    pass
        return out


class SolidWorksBridge:
    def _app(self):
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
        pythoncom.CoInitialize()
        return win32com.client.GetActiveObject("SldWorks.Application")

    def check_active(self) -> str:
        try:
            sw = self._app()
        except Exception:
            return "SolidWorks isn't running (or pywin32 is missing). Open the part or assembly in SolidWorks first."
        try:
            doc = sw.ActiveDoc
        except Exception as e:
            return f"Couldn't reach the active SolidWorks document: {e}"
        if doc is None:
            return "SolidWorks is running but no document is open."
        lines = []
        try:
            title = doc.GetTitle()
            kind = {1: "part", 2: "assembly", 3: "drawing"}.get(int(doc.GetType()), "document")
            lines.append(f"SolidWorks {kind}: {title}")
        except Exception:
            lines.append("SolidWorks document (title unavailable)")
        # rebuild + feature errors
        try:
            doc.ForceRebuild3(False)
        except Exception as e:
            lines.append(f"(rebuild failed: {e})")
        problems = []
        count = 0
        try:
            feat = doc.FirstFeature()
            while feat is not None and count < 5000:
                count += 1
                try:
                    res = feat.GetErrorCode2()
                    code, warning = (res if isinstance(res, tuple) else (res, False))
                    code = int(code or 0)
                    if code:
                        problems.append(f"{feat.Name} ({feat.GetTypeName2()}): {'warning' if warning else 'ERROR'} code {code}")
                except Exception:
                    pass
                feat = feat.GetNextFeature()
        except Exception as e:
            lines.append(f"(feature walk failed: {e})")
        lines.append(f"Features scanned: {count}. Rebuild problems: {len(problems)}")
        lines += [f"  * {p}" for p in problems[:30]]
        # mass properties
        try:
            mp = doc.Extension.CreateMassProperty()
            lines.append(f"Mass {mp.Mass:.4g} kg, volume {mp.Volume*1e9:.1f} mm^3, surface {mp.SurfaceArea*1e6:.1f} mm^2, "
                         f"centre of mass {[round(c*1000, 2) for c in mp.CenterOfMass]} mm")
        except Exception:
            pass
        # interference (assemblies)
        try:
            if int(doc.GetType()) == 2:
                mgr = doc.InterferenceDetectionManager
                mgr.TreatCoincidenceAsInterference = False
                ints = mgr.GetInterferences()
                n = mgr.GetInterferenceCount()
                lines.append(f"Interferences: {n}")
                for it in (ints or [])[:10]:
                    try:
                        comps = it.GetComponents()
                        names = ", ".join(getattr(c, "Name2", "?") for c in comps) if comps else "?"
                        lines.append(f"  * {names}: volume {it.Volume*1e9:.2f} mm^3")
                    except Exception:
                        pass
                try:
                    mgr.Done()
                except Exception:
                    pass
        except Exception as e:
            lines.append(f"(interference detection unavailable: {e})")
        return "\n".join(lines)
