"""Design agent check: build a mesh with trimesh, break it, and verify the report catches it.
Run:  .venv\\Scripts\\python.exe tests\\test_design.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import trimesh  # noqa: E402

from eli.agents.design_agent import DesignAgent  # noqa: E402

d = DesignAgent()
tmp = Path(tempfile.mkdtemp(prefix="eli-mesh-"))

box = trimesh.creation.box(extents=(40.0, 25.0, 12.0))
good = tmp / "enclosure.stl"
box.export(good)
rep = d.check_mesh_file(str(good))
print(rep)
assert "watertight: yes" in rep and "No structural issues" in rep, "good mesh should pass"

broken = box.copy()
broken.faces = broken.faces[:-2]  # remove two triangles -> open edges
bad = tmp / "broken.stl"
broken.export(bad)
rep2 = d.check_mesh_file(str(bad))
print()
print(rep2)
assert "watertight: NO" in rep2 and "Not watertight" in rep2, "broken mesh should be flagged"

thin = trimesh.creation.box(extents=(30.0, 30.0, 0.6))
thin_p = tmp / "thin.stl"
thin.export(thin_p)
rep3 = d.check_mesh_file(str(thin_p))
assert "below what FDM" in rep3, "thin part should be flagged"
print("\nthin wall flagged:", "below what FDM" in rep3)
print("\nDESIGN TESTS PASS")
