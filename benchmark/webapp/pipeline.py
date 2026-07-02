"""Job pipeline: text spec -> fable FreeCAD script -> STEP -> obj + DXF
-> preview PNGs. Each stage updates job['status'] for the frontend poller.
"""
import json
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
PALMETTO = os.path.expanduser("~/Palmetto/core/.build/bin/palmetto_engine")
HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = "claude-fable-5"

with open(os.path.join(HERE, "draftaid_prompt.md")) as _f:
    DRAFTAID_SYSTEM = _f.read()

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


OLLAMA_MODEL = "hf.co/mradermacher/BlenderLLM-GGUF:Q3_K_M"
BPY_SYSTEM = (
    "You are an expert in using bpy script to create 3D models. Based on the "
    "following instruction, your task is to write the corresponding bpy "
    "script that will generate the desired 3D model in Blender. Please pay "
    "close attention to every detail in the script and ensure it fully "
    "adheres to the provided specifications."
)


def run_pipeline_blenderllm(job, jobdir, spec):
    """BlenderLLM (local 7B via ollama): bpy script -> Blender -> render.
    No DraftAid stage: it outputs meshes, so the drawing gets extent dims
    from the mesh-derived solid only.
    """
    import urllib.request

    job["status"] = "BlenderLLM (local 7B) is writing the bpy script..."
    body = json.dumps({
        "model": OLLAMA_MODEL, "stream": False,
        "options": {"num_predict": 1024},
        "messages": [{"role": "system", "content": BPY_SYSTEM},
                     {"role": "user", "content": spec}],
    }).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/chat", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        script = extract_code(
            json.loads(r.read())["message"]["content"])
    job["script"] = script

    job["status"] = "Executing bpy script in Blender..."
    obj_path = os.path.join(jobdir, "model.obj")
    preamble = (
        "import bpy\nimport os\nimport math\n"
        "if not hasattr(bpy.types.Mesh, 'use_auto_smooth'):\n"
        "    bpy.types.Mesh.use_auto_smooth = bpy.props.BoolProperty(default=True)\n"
        "    bpy.types.Mesh.auto_smooth_angle = bpy.props.FloatProperty(default=0.523599)\n"
        "bpy.ops.object.select_all(action='SELECT')\nbpy.ops.object.delete()\n"
    )
    bpath = os.path.join(jobdir, "bpy_script.py")
    with open(bpath, "w") as f:
        f.write(preamble + script +
                f"\nbpy.ops.wm.obj_export(filepath=r'{obj_path}')\n")
    ok, out = _run([BLENDER, "--background", "--factory-startup",
                    "--python-exit-code", "1", "--python", bpath], jobdir)
    if not ok or not os.path.exists(obj_path):
        raise RuntimeError("bpy script failed (this counts as a syntax "
                           "error in CADBench):\n" + out[-1200:])

    job["status"] = "Rendering 3D preview..."
    _render_obj(jobdir, obj_path)

    job["status"] = "Drawing (extent dims only - mesh input)..."
    try:
        _mesh_drawing(jobdir, obj_path)
        _render_dxf(os.path.join(jobdir, "drawing.dxf"),
                    os.path.join(jobdir, "drawing.png"))
    except Exception as e:
        job["warnings"] = [f"drawing failed on mesh input: {e}"]
    job["status"] = "done"


def _render_obj(jobdir, obj_path):
    rpath = os.path.join(jobdir, "render.py")
    with open(rpath, "w") as f:
        f.write(RENDER_TEMPLATE.format(
            obj=obj_path, png=os.path.join(jobdir, "solid.png")))
    ok, out = _run([BLENDER, "--background", "--factory-startup",
                    "--python-exit-code", "1", "--python", rpath], jobdir)
    if not ok:
        raise RuntimeError("3D render failed:\n" + out[-800:])


def _mesh_drawing(jobdir, obj_path):
    """Mesh -> solid -> extent-dim drawing via the benchmark converter."""
    conv = os.path.join(HERE, "..", "mesh_to_drawing.py")
    env = dict(os.environ, FC_OBJ=obj_path,
               FC_NAME="drawing_tmp", FC_OUT=jobdir)
    ok, out = _run([FREECADCMD, conv], jobdir, env=env)
    src = os.path.join(jobdir, "drawing_tmp_drawing.dxf")
    if not ok or not os.path.exists(src):
        raise RuntimeError(out[-400:])
    os.rename(src, os.path.join(jobdir, "drawing.dxf"))


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

    job["status"] = "Probing geometry (FreeCAD)..."
    env = dict(os.environ, FC_STEP=step_path, FC_OUT=jobdir)
    ok, out = _run([FREECADCMD, os.path.join(HERE, "fc_geom.py")],
                   jobdir, env=env)
    if not ok or "FC_GEOM_OK" not in out:
        raise RuntimeError("Geometry probe failed:\n" + out[-1200:])
    with open(os.path.join(jobdir, "geometry.json")) as f:
        geom = json.load(f)
    job["bbox"] = [round(geom["bbox_max"][i] - geom["bbox_min"][i], 2)
                   for i in range(3)]

    job["status"] = "Recognizing features (Palmetto)..."
    features = _palmetto_features(step_path, jobdir)
    geom["recognized_features"] = features
    job["features"] = _feature_summary(geom)

    job["status"] = "Fable is planning the dimension scheme..."
    plan = _plan_dimensions(client, spec, geom)
    plan_path = os.path.join(jobdir, "plan.json")
    with open(plan_path, "w") as f:
        json.dump(plan, f, indent=1)
    job["plan"] = plan

    job["status"] = "Placing dimensions (TechDraw)..."
    env = dict(os.environ, FC_STEP=step_path, FC_OUT=jobdir,
               FC_PLAN=plan_path)
    ok, out = _run([FREECADCMD, os.path.join(HERE, "fc_draw.py")],
                   jobdir, env=env)
    if not ok or "FC_DRAW_OK" not in out:
        raise RuntimeError("Drawing generation failed:\n" + out[-1200:])
    skipped = [ln for ln in out.splitlines()
               if "skipped" in ln or "not placed" in ln]
    if skipped:
        job["warnings"] = skipped

    job["status"] = "Rendering 3D preview..."
    _render_obj(jobdir, os.path.join(jobdir, "model.obj"))

    job["status"] = "Rendering the drawing..."
    _render_dxf(os.path.join(jobdir, "drawing.dxf"),
                os.path.join(jobdir, "drawing.png"))
    job["status"] = "done"


def _palmetto_features(step_path, jobdir):
    """Run Palmetto feature recognition; empty list if unavailable."""
    if not os.path.exists(PALMETTO):
        return []
    pdir = os.path.join(jobdir, "palmetto")
    os.makedirs(pdir, exist_ok=True)
    ok, _ = _run([PALMETTO, "--input", step_path, "--outdir", pdir,
                  "--modules", "all"], jobdir, timeout=180)
    fpath = os.path.join(pdir, "features.json")
    if not os.path.exists(fpath):
        return []
    with open(fpath) as f:
        data = json.load(f)
    feats = data if isinstance(data, list) else data.get("features", [])
    return [
        {"id": ft["id"], "type": ft["type"], "subtype": ft.get("subtype"),
         "params": ft.get("params", {}),
         "confidence": ft.get("confidence")}
        for ft in feats
    ]


def _feature_summary(geom):
    from collections import Counter
    parts = []
    holes = Counter((h["diameter"], tuple(h["axis"]))
                    for h in geom["holes"])
    for (dia, _axis), n in sorted(holes.items()):
        parts.append(f"{n}× ⌀{dia}" if n > 1 else f"⌀{dia}")
    for ft in geom.get("recognized_features", []):
        if ft["type"] == "fillet":
            parts.append(f"fillet R{ft['params'].get('radius_mm')}")
    return ", ".join(parts) if parts else "no features recognized"


def _plan_dimensions(client, spec, geom):
    user = (
        f"Design intent (user's request): {spec}\n\n"
        f"Part data:\n{json.dumps(geom, indent=1)}"
    )
    resp = client.messages.create(
        model=MODEL, max_tokens=8000, system=DRAFTAID_SYSTEM,
        messages=[{"role": "user", "content": user}])
    text = response_text(resp)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise RuntimeError("planner returned no JSON")
    return json.loads(m.group(0))


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
