"""Job pipeline: text spec -> fable FreeCAD script -> STEP -> obj + DXF
-> preview PNGs. Each stage updates job['status'] for the frontend poller.
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))
from benchmark.run_benchmark import extract_code, response_text

import anthropic

FREECADCMD = "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"
BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = "claude-fable-5"

SYSTEM = (
    "You are an expert in FreeCAD's Python API (the Part workbench). Write a "
    "complete Python script for FreeCAD 1.1 that runs headless under "
    "freecadcmd. The script must build the requested part as a single solid "
    "using Part primitives and boolean operations (no GUI, no PartDesign "
    "sketch workflow), with all dimensions in millimeters, then export it as "
    "STEP to the exact path given. Respond with only a python code block."
)

RENDER_TEMPLATE = """
import bpy, os, math
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete()
bpy.ops.wm.obj_import(filepath=r'{obj}')
objs = [o for o in bpy.context.scene.objects if o.type == 'MESH']
xs = [c for o in objs for v in o.bound_box
      for c in [(o.matrix_world @ __import__('mathutils').Vector(v))]]
mx = max(max(abs(c.x), abs(c.y), abs(c.z)) for c in xs) or 1
cam = bpy.data.cameras.new('C'); co = bpy.data.objects.new('C', cam)
bpy.context.scene.collection.objects.link(co); bpy.context.scene.camera = co
co.location = (mx*2.4, -mx*2.0, mx*1.8)
co.rotation_euler = (math.radians(63), 0, math.radians(50))
li = bpy.data.lights.new('L', 'SUN'); li.energy = 4
lo = bpy.data.objects.new('L', li)
bpy.context.scene.collection.objects.link(lo)
lo.rotation_euler = (math.radians(45), math.radians(20), 0)
s = bpy.context.scene
s.render.resolution_x = s.render.resolution_y = 700
s.render.image_settings.file_format = 'PNG'
s.render.filepath = r'{png}'
bpy.ops.render.render(write_still=True)
"""


def run_pipeline(job, jobdir, spec):
    client = anthropic.Anthropic()
    step_path = os.path.join(jobdir, "part.step")
    prompt = (f"{spec}\n\nExport the final solid as STEP to exactly this "
              f"path: {step_path}")

    job["status"] = "Fable is writing the FreeCAD script..."
    script = _ask(client, prompt)

    for attempt in (1, 2):
        spath = os.path.join(jobdir, f"script_attempt{attempt}.py")
        with open(spath, "w") as f:
            f.write(script)
        job["status"] = (f"Building the solid in FreeCAD "
                         f"(attempt {attempt})...")
        ok, out = _run([FREECADCMD, spath], jobdir)
        if ok and os.path.exists(step_path):
            break
        if attempt == 2:
            raise RuntimeError(
                "FreeCAD could not build the part:\n" + out[-1200:])
        job["status"] = "Script failed - fable is repairing it..."
        script = _ask(client, (
            f"{prompt}\n\nYour previous script failed with this output - "
            f"fix it and return the complete corrected script:\n```\n"
            f"{out[-3000:]}\n```\n\nPrevious script:\n```python\n{script}\n```"
        ))
    job["script"] = script

    job["status"] = "Generating drawing views and mesh..."
    env = dict(os.environ, FC_STEP=step_path, FC_OUT=jobdir)
    ok, out = _run([FREECADCMD, os.path.join(HERE, "fc_post.py")],
                   jobdir, env=env)
    if not ok or "FC_POST_OK" not in out:
        raise RuntimeError("Drawing generation failed:\n" + out[-1200:])
    m = re.search(r"FC_POST_OK bbox ([\d.]+) ([\d.]+) ([\d.]+)", out)
    if m:
        job["bbox"] = [round(float(x), 2) for x in m.groups()]

    job["status"] = "Rendering 3D preview..."
    rpath = os.path.join(jobdir, "render.py")
    with open(rpath, "w") as f:
        f.write(RENDER_TEMPLATE.format(
            obj=os.path.join(jobdir, "model.obj"),
            png=os.path.join(jobdir, "solid.png")))
    ok, out = _run([BLENDER, "--background", "--factory-startup",
                    "--python-exit-code", "1", "--python", rpath], jobdir)
    if not ok:
        raise RuntimeError("3D render failed:\n" + out[-800:])

    job["status"] = "Rendering the drawing..."
    _render_dxf(os.path.join(jobdir, "drawing.dxf"),
                os.path.join(jobdir, "drawing.png"))
    job["status"] = "done"


def _ask(client, prompt):
    resp = client.messages.create(
        model=MODEL, max_tokens=16000, system=SYSTEM,
        messages=[{"role": "user", "content": prompt}])
    return extract_code(response_text(resp))


def _run(cmd, cwd, env=None, timeout=420):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd, env=env)
        out = p.stdout + p.stderr
        ok = (p.returncode == 0 and "Traceback" not in out
              and "Exception while processing" not in out)
        return ok, out
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"


def _render_dxf(dxf, png):
    import ezdxf
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    doc = ezdxf.readfile(dxf)
    fig = plt.figure(figsize=(14, 10), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(
        doc.modelspace())
    fig.savefig(png, facecolor="white")
    plt.close(fig)
