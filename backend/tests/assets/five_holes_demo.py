"""Simulated user for the guide test. Run inside the FreeCAD GUI:
    "C:\\Program Files\\FreeCAD 1.1\\bin\\freecad.exe" five_holes_demo.py
Opens five_holes.FCStd, then on a timer performs exactly what the guide asks for:
  t=25 s  open HolesSketch for editing
  t=55 s  draw the bridging rectangle across the hole centres
  t=80 s  trim every segment inside the merged region (keeping the outer arcs)
  t=110 s close the sketch (the pocket recomputes into one opening)
Eli must notice each of these from the screen and advance on its own.
"""
import math
import os

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Sketcher
from PySide import QtCore

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "five_holes.FCStd")
CENTERS = [-36.0, -18.0, 0.0, 18.0, 36.0]
R = 8.0
HALF_H = 5.0   # bridging rectangle half height (< R)

doc = App.openDocument(PATH)
Gui.ActiveDocument = Gui.getDocument(doc.Name)
Gui.SendMsgToActiveView("ViewFit")
Gui.activeDocument().activeView().viewIsometric()
sk = doc.getObject("HolesSketch")
_timers = []


def later(ms, fn):
    t = QtCore.QTimer()
    t.setSingleShot(True)
    t.timeout.connect(fn)
    t.start(ms)
    _timers.append(t)


def log(msg):
    App.Console.PrintMessage("[demo] " + msg + "\n")


def raise_window():
    """Keep this FreeCAD window in front so Eli keeps watching it (a real user would be working in it)."""
    try:
        mw = Gui.getMainWindow()
        mw.showNormal() if mw.isMinimized() else None
        mw.raise_()
        mw.activateWindow()
    except Exception as e:
        log("raise failed: %s" % e)


def geo_at(point):
    """GeoId of the (non-construction) circle/arc/line passing within 0.6 mm of point."""
    best, best_d = None, 0.6
    for i, g in enumerate(sk.Geometry):
        if sk.getConstruction(i) if hasattr(sk, "getConstruction") else False:
            continue
        try:
            d = g.toShape().distToShape(Part.Vertex(point))[0]
        except Exception:
            continue
        if d < best_d:
            best, best_d = i, d
    return best


def step_edit():
    raise_window()
    Gui.getDocument(doc.Name).setEdit(sk)
    Gui.SendMsgToActiveView("ViewFit")
    log("sketch opened for editing")


def step_rectangle():
    raise_window()
    x0, x1 = CENTERS[0], CENTERS[-1]
    p = [App.Vector(x0, -HALF_H, 0), App.Vector(x1, -HALF_H, 0), App.Vector(x1, HALF_H, 0), App.Vector(x0, HALF_H, 0)]
    base = len(sk.Geometry)
    for i in range(4):
        sk.addGeometry(Part.LineSegment(p[i], p[(i + 1) % 4]), False)
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", base + i, 2, base + (i + 1) % 4, 1))
    doc.recompute()
    log("bridging rectangle drawn")


def step_trim():
    raise_window()
    dx = math.sqrt(R * R - HALF_H * HALF_H)
    targets = []
    # arcs of each circle that lie inside the rectangle (inner sides)
    for k, x in enumerate(CENTERS):
        if k > 0:
            targets.append(App.Vector(x - R, 0, 0))
        if k < len(CENTERS) - 1:
            targets.append(App.Vector(x + R, 0, 0))
    # rectangle edges inside the circles
    for x in CENTERS:
        targets.append(App.Vector(x, HALF_H, 0))
        targets.append(App.Vector(x, -HALF_H, 0))
    targets.append(App.Vector(CENTERS[0], 0, 0))    # left vertical edge (entirely inside the first circle)
    targets.append(App.Vector(CENTERS[-1], 0, 0))   # right vertical edge
    n = 0
    for pt in targets:
        gid = geo_at(pt)
        if gid is None:
            log("no edge at %s" % pt)
            continue
        try:
            sk.trim(gid, pt)
            n += 1
        except Exception as e:
            log("trim failed at %s: %s" % (pt, e))
        doc.recompute()
    log("trimmed %d segments" % n)


def step_close():
    raise_window()
    Gui.getDocument(doc.Name).resetEdit()
    doc.recompute()
    Gui.SendMsgToActiveView("ViewFit")
    pk = doc.getObject("Holes")
    log("sketch closed; pocket valid=%s faces=%d" % (pk.Shape.isValid(), len(pk.Shape.Faces)))


later(12000, raise_window)
later(25000, step_edit)
later(55000, step_rectangle)
later(80000, step_trim)
later(110000, step_close)
log("demo scheduled")
