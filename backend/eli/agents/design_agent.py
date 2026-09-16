"""Design Agent: engineering-design review.

Two sources of truth:
1. Screenshots of a CAD tool (SolidWorks, Fusion 360, Blender, FreeCAD, Onshape...): the vision model
   reviews them against an engineering checklist. Screenshots can reveal visible problems (open
   edges, overlapping bodies, missing fillets, obviously thin walls) but cannot measure sub-millimetre
   misalignment; the agent says so instead of inventing numbers.
2. Mesh files (STL/OBJ/3MF/PLY) analysed with trimesh: watertightness, non-manifold edges, degenerate
   faces, bodies, bounding box, volume, overhang fraction. These are real measurements.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("eli.design")

CAD_APPS = ("solidworks", "fusion 360", "fusion360", "blender", "autocad", "freecad", "onshape", "inventor",
            "catia", "rhino", "sketchup", "tinkercad", "openscad", "kicad", "cura", "prusaslicer", "bambu")
DRAWING_APPS = ("krita", "photoshop", "illustrator", "inkscape", "gimp", "paint.net", "clip studio", "affinity",
                "figma", "corel", "sketchbook", "procreate", "paint")

DRAWING_REVIEW_CHECKLIST = """Drawing/illustration review checklist (say what is visible vs. what you cannot verify):
1. Layers: hidden or locked layers, drawing on the wrong layer, empty layers, layer blend modes/opacity set unexpectedly.
2. Selections and masks: an active selection or mask limiting where paint lands; quick-mask left on.
3. Tools: brush size/opacity/flow at 0 or 100, wrong blend mode, eraser vs brush, pressure sensitivity off, symmetry/snapping toggled.
4. Colour: colour mode (CMYK vs RGB), bit depth, out-of-gamut warnings, foreground/background swapped.
5. Document: canvas size vs export size, DPI for print (300) vs screen (72/96), transparent vs white background, artboard bounds.
6. Vector: open paths that should be closed, stroke without fill, boolean/pathfinder results, text not outlined for export.
7. Export: format (PNG for transparency, JPG for photos, SVG/PDF for vectors), missing fonts, embedded vs linked images.
Report as: what you see -> why it matters -> the menu/shortcut to fix it in this app."""

CAD_REVIEW_CHECKLIST = """Engineering review checklist (apply to what is visible; say clearly what you cannot verify from a screenshot):
1. Topology: open/disconnected edges, gaps between bodies, sketches that are not closed, self-intersections, non-manifold hints (zero-thickness walls, faces sharing a single edge).
2. Overlaps and interference: bodies that visibly overlap, bosses passing through walls, holes that do not go through the intended face.
3. Dimensions: conflicting or missing dimensions, hole diameters vs. fastener sizes, features that reference nothing, symmetry that is not enforced.
4. Manufacturability: minimum wall thickness (3D printing ~1.2 mm FDM, 0.8 mm SLA), overhangs > 45 deg without supports, tiny features, sharp internal corners, draft angles for moulding, tolerance for mating parts (0.2-0.3 mm clearance for FDM).
5. Assembly: mounting holes alignment across parts, screw bosses, lid/base fit, cable routing, snap fits.
6. Medical/enclosure specifics if relevant: smooth cleanable surfaces, no sharp edges, vent hole sizes, ingress.
Report findings as: what you see -> why it matters -> concrete fix (with numbers only when they are visible on screen or standard practice). If you need a measurement you cannot see, ask the user to check that dimension or export an STL so I can measure it."""


class DesignAgent:
    def is_cad(self, window) -> bool:
        s = f"{getattr(window, 'app', '')} {getattr(window, 'process', '')} {getattr(window, 'title', '')}".lower()
        return any(a in s for a in CAD_APPS)

    def is_drawing(self, window) -> bool:
        s = f"{getattr(window, 'app', '')} {getattr(window, 'process', '')} {getattr(window, 'title', '')}".lower()
        return any(a in s for a in DRAWING_APPS)

    def checklist_for(self, window) -> str:
        if self.is_cad(window):
            return CAD_REVIEW_CHECKLIST
        if self.is_drawing(window):
            return DRAWING_REVIEW_CHECKLIST
        return ""

    def check_mesh_file(self, path: str) -> str:
        p = Path(path).expanduser()
        if not p.is_file():
            return f"File not found: {path}"
        if p.suffix.lower() not in (".stl", ".obj", ".ply", ".3mf", ".off", ".glb", ".gltf"):
            return f"I can analyse STL, OBJ, PLY, 3MF, OFF and glTF meshes, not {p.suffix}."
        try:
            import numpy as np  # type: ignore
            import trimesh  # type: ignore
        except Exception as e:
            return f"Mesh analysis needs trimesh (pip install trimesh): {e}"
        try:
            loaded = trimesh.load(str(p), force="mesh")
        except Exception as e:
            return f"Couldn't load {p.name}: {e}"
        mesh = loaded
        if not isinstance(mesh, trimesh.Trimesh) or mesh.faces is None or len(mesh.faces) == 0:
            return f"{p.name} contains no triangle faces."
        try:
            bodies = mesh.split(only_watertight=False)
        except Exception:
            bodies = [mesh]
        ext = mesh.extents
        watertight = bool(mesh.is_watertight)
        winding = bool(mesh.is_winding_consistent)
        # non-manifold edges: edges shared by != 2 faces
        edges = mesh.edges_sorted
        _, counts = np.unique(edges, axis=0, return_counts=True)
        open_edges = int((counts == 1).sum())
        over_shared = int((counts > 2).sum())
        # degenerate faces
        areas = mesh.area_faces
        degenerate = int((areas < 1e-9).sum())
        # overhangs (assuming Z up): faces whose normal points down more than 45 deg
        normals = mesh.face_normals
        centroids = mesh.triangles_center
        z_min = float(mesh.bounds[0][2])
        on_bed = centroids[:, 2] <= z_min + max(0.05, 0.01 * float(ext[2]))  # faces resting on the build plate
        down = (normals[:, 2] < -0.7071) & ~on_bed
        overhang_frac = float(areas[down].sum() / max(areas.sum(), 1e-9))
        volume = float(mesh.volume) if watertight else None
        thin_axis = float(ext.min())
        lines = [
            f"Mesh report for {p.name}",
            f"- triangles: {len(mesh.faces):,}, vertices: {len(mesh.vertices):,}, bodies: {len(bodies)}",
            f"- bounding box (units as modelled, usually mm): {ext[0]:.2f} x {ext[1]:.2f} x {ext[2]:.2f}",
            f"- watertight: {'yes' if watertight else 'NO'}; consistent winding: {'yes' if winding else 'NO'}",
            f"- open (boundary) edges: {open_edges}; non-manifold edges (shared by >2 faces): {over_shared}; degenerate faces: {degenerate}",
            f"- volume: {volume:.1f} mm^3 ({volume/1000:.1f} cm^3)" if volume is not None else "- volume: n/a (mesh is not closed)",
            f"- surface area: {float(mesh.area):.1f} mm^2",
            f"- faces overhanging > 45 deg (Z up): {overhang_frac*100:.0f}% of surface area",
        ]
        issues = []
        if not watertight:
            issues.append(f"Not watertight ({open_edges} open edges): slicers will produce gaps or fail. Run a mesh repair (Netfabb/Meshmixer/PrusaSlicer 'fix') or close the sketch/loft in CAD.")
        if over_shared:
            issues.append(f"{over_shared} non-manifold edges: geometry folds onto itself. Check for overlapping bodies that were not merged.")
        if degenerate:
            issues.append(f"{degenerate} zero-area triangles: harmless for viewing, but can break boolean ops and slicing; remove them.")
        if len(bodies) > 1:
            issues.append(f"{len(bodies)} separate bodies: make sure they are meant to be separate parts; loose fragments often mean a feature failed to merge.")
        if thin_axis < 1.0:
            issues.append(f"Thinnest overall dimension is {thin_axis:.2f} mm, below what FDM printers resolve (~1.2 mm walls).")
        if overhang_frac > 0.25:
            issues.append(f"{overhang_frac*100:.0f}% of the surface overhangs more than 45 deg: plan supports or re-orient the part.")
        lines.append("Issues:" if issues else "No structural issues found in the mesh itself (dimensional intent still needs CAD checks).")
        lines += [f"  * {i}" for i in issues]
        return "\n".join(lines)
