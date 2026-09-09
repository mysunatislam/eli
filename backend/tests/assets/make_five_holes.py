"""Build the test part for the guide: a 100 x 60 x 5 mm plate with five 16 mm holes in a row,
separated by 2 mm webs. Run headless:
    "C:\\Program Files\\FreeCAD 1.1\\bin\\freecadcmd.exe" make_five_holes.py
Writes five_holes.FCStd next to this script.
"""
import os
import sys

import FreeCAD as App
import Part
import Sketcher

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "five_holes.FCStd")
HOLE_R = 8.0
CENTERS = [-36.0, -18.0, 0.0, 18.0, 36.0]

doc = App.newDocument("FiveHoles")
body = doc.addObject("PartDesign::Body", "Body")
xy = [f for f in body.Origin.OriginFeatures if f.Role == "XY_Plane"][0]


def attach(sk):
    if hasattr(sk, "AttachmentSupport"):
        sk.AttachmentSupport = (xy, [""])
    else:
        sk.Support = (xy, [""])
    sk.MapMode = "FlatFace"


plate = body.newObject("Sketcher::SketchObject", "PlateSketch")
attach(plate)
pts = [App.Vector(-50, -30, 0), App.Vector(50, -30, 0), App.Vector(50, 30, 0), App.Vector(-50, 30, 0)]
for i in range(4):
    plate.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
for i in range(4):
    plate.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
doc.recompute()
pad = body.newObject("PartDesign::Pad", "Plate")
pad.Profile = plate
pad.Length = 5.0
doc.recompute()

holes = body.newObject("Sketcher::SketchObject", "HolesSketch")
attach(holes)
for x in CENTERS:
    holes.addGeometry(Part.Circle(App.Vector(x, 0, 0), App.Vector(0, 0, 1), HOLE_R), False)
doc.recompute()
pocket = body.newObject("PartDesign::Pocket", "Holes")
pocket.Profile = holes
pocket.Type = 1          # ThroughAll
pocket.Midplane = True   # cut both ways so the sketch on the XY plane goes through the plate
doc.recompute()

holes.Visibility = False
plate.Visibility = False
doc.saveAs(OUT)
print("saved", OUT, "| valid:", pocket.Shape.isValid(), "| volume %.1f mm^3" % pocket.Shape.Volume)
