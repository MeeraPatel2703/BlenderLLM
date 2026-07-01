"""Drawing executor under freecadcmd: build 3 TechDraw views from a STEP
and place the dimensions specified by a DraftAid plan (plan.json).
Env: FC_STEP, FC_OUT, FC_PLAN. Writes drawing.dxf.
"""
import glob
import json
import os

import FreeCAD
import Part
import TechDraw
from FreeCAD import Vector

STEP = os.environ["FC_STEP"]
OUT = os.environ["FC_OUT"]
PLAN = json.load(open(os.environ["FC_PLAN"]))

VIEWS = {
    "Front": {"dir": Vector(0, -1, 0), "x": Vector(1, 0, 0), "pos": (75, 105)},
    "Top": {"dir": Vector(0, 0, 1), "x": Vector(1, 0, 0), "pos": (75, 190)},
    "Right": {"dir": Vector(1, 0, 0), "x": Vector(0, 1, 0), "pos": (215, 105)},
}

shape = Part.Shape()
shape.read(STEP)
doc = FreeCAD.newDocument()
feat = doc.addObject("Part::Feature", "part")
feat.Shape = shape
doc.recompute()

tpl = glob.glob(os.path.join(FreeCAD.getResourceDir(), "Mod", "TechDraw",
                             "Templates", "**", "A4_Landscape*.svg"),
                recursive=True)[0]
page = doc.addObject("TechDraw::DrawPage", "Page")
t = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
t.Template = tpl
page.Template = t

bb = shape.BoundBox
scale = 110.0 / max(bb.XLength, bb.YLength, bb.ZLength, 1e-6)

views = {}
for name, cfg in VIEWS.items():
    v = doc.addObject("TechDraw::DrawViewPart", name)
    page.addView(v)
    v.Source = [feat]
    v.Direction = cfg["dir"]
    v.XDirection = cfg["x"]
    v.ScaleType = "Custom"
    v.Scale = scale
    v.X, v.Y = cfg["pos"]
    views[name] = v
doc.recompute()


def project(view_name, p3):
    """Part-space point -> unscaled 2D view coords (centered on bbox)."""
    cfg = VIEWS[view_name]
    d, u = cfg["dir"], cfg["x"]
    w = d.cross(u)  # view vertical axis
    p = Vector(*p3)
    c = Vector((bb.XMin + bb.XMax) / 2, (bb.YMin + bb.YMax) / 2,
               (bb.ZMin + bb.ZMax) / 2)
    rel = p - c
    # makeDistanceDim measures in scaled (page) view space, despite the
    # docstring saying unscaled: pass scaled coords so values read true
    return (rel.dot(u) * scale, rel.dot(w) * scale)


# half-spans of each view's projected outline, in page mm
SPANS = {
    "Front": (bb.XLength, bb.ZLength),
    "Top": (bb.XLength, bb.YLength),
    "Right": (bb.YLength, bb.ZLength),
}
_stagger = {name: {"below": 0, "left": 0, "corner": 0} for name in VIEWS}
MARGIN, STEP = 8.0, 7.0


def place(dim, view_name, side, along=0.0):
    """Move a dim label outside the view outline, staggered per side."""
    hu = SPANS[view_name][0] * scale / 2
    hv = SPANS[view_name][1] * scale / 2
    k = _stagger[view_name][side]
    _stagger[view_name][side] += 1
    if side == "below":
        dim.X, dim.Y = along, -(hv + MARGIN + STEP * k)
    elif side == "left":
        dim.X, dim.Y = -(hu + MARGIN + STEP * k), along
    else:  # corner: callout labels above-right
        dim.X, dim.Y = hu * 0.6 + 12, hv + MARGIN + STEP * k


HORIZONTAL, VERTICAL = 0, 1
for e in PLAN.get("extent_dims", []):
    direction = HORIZONTAL if e["type"] == "DistanceX" else VERTICAL
    try:
        dim = TechDraw.makeExtentDim(views[e["view"]], [], direction)
        if dim is not None:
            place(dim, e["view"],
                  "below" if e["type"] == "DistanceX" else "left")
    except Exception as exc:
        print("extent dim skipped:", e, exc)

for pd in PLAN.get("position_dims", []):
    try:
        v = views[pd["view"]]
        p1 = project(pd["view"], pd["from"])
        p2 = project(pd["view"], pd["to"])
        dim = TechDraw.makeDistanceDim(
            v, pd["type"], Vector(p1[0], p1[1], 0), Vector(p2[0], p2[1], 0))
        if pd["type"] == "DistanceX":
            place(dim, pd["view"], "below", along=(p1[0] + p2[0]) / 2)
        else:
            place(dim, pd["view"], "left", along=(p1[1] + p2[1]) / 2)
    except Exception as exc:
        print("position dim skipped:", pd, exc)

doc.recompute()

for co in PLAN.get("diameter_callouts", []):
    v = views[co["view"]]
    placed = False
    for i in range(500):
        try:
            edge = v.getEdgeByIndex(i)
        except Exception:
            break
        if edge is None:
            continue
        try:
            curve = edge.Curve
            if curve.TypeId != "Part::GeomCircle":
                continue
            # view edge geometry is unscaled (real part units)
            if abs(2 * curve.Radius - co["diameter"]) > 0.1:
                continue
            dim = doc.addObject("TechDraw::DrawViewDimension",
                                "Dia_%s" % co["view"])
            dim.Type = "Diameter"
            dim.References2D = [(v, "Edge%d" % i)]
            dim.Arbitrary = True
            # ASCII-safe: %%c is the DXF diameter symbol
            dim.FormatSpec = (co["text"].replace("⌀", "%%c")
                              .replace("Ø", "%%c").replace("×", "X"))
            page.addView(dim)
            place(dim, co["view"], "corner")
            placed = True
            break
        except Exception:
            continue
    if not placed:
        print("callout not placed:", co)

# one annotation per line: multiline Text loses lines in DXF export
notes = PLAN.get("notes", [])
for i, line in enumerate(["NOTES:"] + [f"{j+1}. {n}" for j, n in
                                       enumerate(notes)] if notes else []):
    ann = doc.addObject("TechDraw::DrawViewAnnotation", "Note%d" % i)
    ann.Text = [line.replace("⌀", "DIA ").replace("Ø", "DIA ")]
    ann.TextSize = 4
    page.addView(ann)
    ann.X, ann.Y = 235, 38 - 6 * i

doc.recompute()
TechDraw.writeDXFPage(page, os.path.join(OUT, "drawing.dxf"))
print("FC_DRAW_OK")
