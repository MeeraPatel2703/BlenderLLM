"""Post-process a STEP file under freecadcmd: export a render mesh (.obj)
and a 3-view TechDraw drawing with extent dimensions (.dxf).
Env: FC_STEP, FC_OUT
"""
import glob
import os

import FreeCAD
import Mesh
import MeshPart
import Part
import TechDraw

STEP = os.environ["FC_STEP"]
OUT = os.environ["FC_OUT"]

shape = Part.Shape()
shape.read(STEP)
doc = FreeCAD.newDocument()
feat = doc.addObject("Part::Feature", "part")
feat.Shape = shape
doc.recompute()

m = doc.addObject("Mesh::Feature", "m")
m.Mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=0.05)
Mesh.export([m], os.path.join(OUT, "model.obj"))

tpl = glob.glob(os.path.join(FreeCAD.getResourceDir(), "Mod", "TechDraw",
                             "Templates", "**", "A4_Landscape*.svg"),
                recursive=True)[0]
page = doc.addObject("TechDraw::DrawPage", "Page")
t = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
t.Template = tpl
page.Template = t

bb = shape.BoundBox
scale = 120.0 / max(bb.XLength, bb.YLength, bb.ZLength, 1e-6)
for name, direction, xdir, (px, py) in [
    ("Front", (0, -1, 0), (1, 0, 0), (70, 105)),
    ("Top", (0, 0, 1), (1, 0, 0), (70, 185)),
    ("Right", (1, 0, 0), (0, 1, 0), (210, 105)),
]:
    v = doc.addObject("TechDraw::DrawViewPart", name)
    page.addView(v)
    v.Source = [feat]
    v.Direction = direction
    v.XDirection = xdir
    v.ScaleType = "Custom"
    v.Scale = scale
    v.X, v.Y = px, py
doc.recompute()

HORIZONTAL, VERTICAL = 0, 1
for direction in (HORIZONTAL, VERTICAL):
    try:
        TechDraw.makeExtentDim(doc.getObject("Front"), [], direction)
        TechDraw.makeExtentDim(doc.getObject("Top"), [], direction)
    except Exception as e:
        print("dim skipped:", e)
doc.recompute()

TechDraw.writeDXFPage(page, os.path.join(OUT, "drawing.dxf"))
print("FC_POST_OK bbox", bb.XLength, bb.YLength, bb.ZLength)
