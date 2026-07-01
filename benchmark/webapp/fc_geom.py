"""Geometry probe under freecadcmd: extract bbox + hole geometry from a
STEP file (centers, axes, diameters) and export the render mesh.
Env: FC_STEP, FC_OUT. Writes geometry.json + model.obj.
"""
import json
import os

import FreeCAD
import Mesh
import MeshPart
import Part

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

bb = shape.BoundBox
holes = []
seen = {}
for f in shape.Faces:
    surf = f.Surface
    if surf.TypeId != "Part::GeomCylinder":
        continue
    # only concave cylinders (holes), not bosses: normal points at axis
    axis = surf.Axis
    center = surf.Center
    p = f.valueAt(*f.ParameterRange[::2][:1] * 2) if False else None
    u0, u1, v0, v1 = f.ParameterRange
    pt = f.valueAt((u0 + u1) / 2, (v0 + v1) / 2)
    n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    to_axis = center.sub(pt)
    to_axis = to_axis - axis * to_axis.dot(axis)
    if n.dot(to_axis) < 0:  # normal points away from axis -> boss/od
        continue
    # extent of this face along the axis -> hole span
    lo = min(v.Point.dot(axis) for v in f.Vertexes) if f.Vertexes else 0
    hi = max(v.Point.dot(axis) for v in f.Vertexes) if f.Vertexes else 0
    # center on the hole axis, mid-depth
    c_on_axis = center - axis * (center.dot(axis) - (lo + hi) / 2)
    key = (round(surf.Radius, 3),
           round(c_on_axis.x, 2), round(c_on_axis.y, 2),
           round(c_on_axis.z, 2))
    # OCC splits full cylinders into faces; sum angular span per hole so
    # partial arcs (fillets) can be filtered out below
    span = abs(u1 - u0)
    if key in seen:
        seen[key]["span"] += span
        continue
    seen[key] = {
        "diameter": round(2 * surf.Radius, 3),
        "center": [round(c_on_axis.x, 3), round(c_on_axis.y, 3),
                   round(c_on_axis.z, 3)],
        "axis": [round(axis.x, 3), round(axis.y, 3), round(axis.z, 3)],
        "depth": round(hi - lo, 3),
        "span": span,
    }

for h in seen.values():
    if h.pop("span") >= 5.5:  # ~2*pi -> a real hole, not a fillet arc
        holes.append(h)

geom = {
    "bbox_min": [round(bb.XMin, 3), round(bb.YMin, 3), round(bb.ZMin, 3)],
    "bbox_max": [round(bb.XMax, 3), round(bb.YMax, 3), round(bb.ZMax, 3)],
    "holes": holes,
}
with open(os.path.join(OUT, "geometry.json"), "w") as fp:
    json.dump(geom, fp, indent=1)
print("FC_GEOM_OK", len(holes), "holes")
