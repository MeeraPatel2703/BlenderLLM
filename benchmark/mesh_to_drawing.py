"""Mesh (.obj) -> solid -> TechDraw 3-view dimensioned drawing (DXF).
Run: FC_OBJ=<obj> FC_NAME=<name> FC_OUT=<dir> freecadcmd mesh_to_drawing.py
"""
import os

import FreeCAD
import Mesh
import Part
import TechDraw

OBJ = os.environ["FC_OBJ"]
NAME = os.environ["FC_NAME"]
OUT = os.environ["FC_OUT"]

doc = FreeCAD.newDocument()

# mesh -> shape; removeSplitter merges coplanar triangles into clean faces
mesh = Mesh.Mesh(OBJ)
shape = Part.Shape()
shape.makeShapeFromMesh(mesh.Topology, 0.05)
try:
    solid = Part.makeSolid(shape)
except Exception:
    solid = shape
solid = solid.removeSplitter()
feat = doc.addObject("Part::Feature", "part")
feat.Shape = solid
doc.recompute()

import glob
tpl_candidates = glob.glob(os.path.join(
    FreeCAD.getResourceDir(), "Mod", "TechDraw", "Templates",
    "**", "A4_Landscape*.svg"), recursive=True)
tpl_path = tpl_candidates[0]
page = doc.addObject("TechDraw::DrawPage", "Page")
tmpl = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
tmpl.Template = tpl_path
page.Template = tmpl

bb = solid.BoundBox
scale = 120.0 / max(bb.XLength, bb.YLength, bb.ZLength, 1e-6)

views = [
    ("Front", (0, -1, 0), (1, 0, 0), (70, 105)),
    ("Top", (0, 0, 1), (1, 0, 0), (70, 185)),
    ("Right", (1, 0, 0), (0, 1, 0), (210, 105)),
]
for name, direction, xdir, (px, py) in views:
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
        print("dim failed:", e)
doc.recompute()

out_path = os.path.join(OUT, f"{NAME}_drawing.dxf")
TechDraw.writeDXFPage(page, out_path)
print("faces:", len(solid.Faces), "| wrote", out_path)
