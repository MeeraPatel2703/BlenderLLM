"""One-part probe of the text -> parametric solid -> dimensioned 2D drawing
chain, with fable writing both stages itself.

Stage 1: fable writes a FreeCAD Python script that models the part and
         exports STEP. We run it under freecadcmd.
Stage 2: fable writes a TechDraw script (given only the STEP path) that
         makes front/top/side views with dimensions and exports DXF.
On script failure, the model gets the traceback and one repair round per
stage — matching how it would run inside a product loop.
"""

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from benchmark.run_benchmark import extract_code, response_text

import anthropic

FREECADCMD = "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"
OUT = os.path.join(os.path.dirname(__file__), "results", "3d-to-2d")
MODEL = "claude-fable-5"

PART_SPEC = """\
Model this mechanical part: an L-shaped mounting bracket.
- Base plate: 80 mm x 50 mm x 8 mm thick.
- Vertical wall: rises from one 50 mm edge of the base, 60 mm tall,
  50 mm wide, 8 mm thick.
- Four M6 clearance holes (diameter 6.6 mm) through the base plate,
  positioned 10 mm from each edge at the four corners of the usable
  base area (the area not covered by the wall).
- One 20 mm diameter through-bore in the vertical wall, centered
  30 mm above the top face of the base plate.
- 5 mm fillet along the inside corner where the wall meets the base.
All dimensions in millimeters. Use exact dimensions as specified."""

STAGE1_SYSTEM = (
    "You are an expert in FreeCAD's Python API (the Part workbench). Write a "
    "complete Python script for FreeCAD 1.1 that runs headless under "
    "freecadcmd. The script must build the requested part as a single solid "
    "using Part primitives and boolean operations (no GUI, no PartDesign "
    "sketch workflow), then export it as STEP to the exact path given. "
    "Respond with only a python code block."
)

STAGE2_SYSTEM = (
    "You are an expert in FreeCAD's TechDraw Python API. Write a complete "
    "Python script for FreeCAD 1.1 that runs headless under freecadcmd "
    "(import FreeCAD, Import/Part, TechDraw — TechDrawGui is NOT available). "
    "The script must: load the given STEP file; create a TechDraw page using "
    "the built-in A4 landscape SVG template "
    "(os.path.join(FreeCAD.getResourceDir(), 'Mod', 'TechDraw', 'Templates', "
    "'A4_LandscapeTD.svg')); add front, top, and right orthographic views at "
    "a sensible scale; add dimensions for the principal overall sizes and "
    "hole diameters using TechDraw's dimension functions (e.g. "
    "TechDraw.makeExtentDim for extents); recompute; and export the page "
    "with TechDraw.writeDXFPage to the exact output path given. "
    "Respond with only a python code block."
)


def ask(client, system, user):
    resp = client.messages.create(
        model=MODEL, max_tokens=16000, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return extract_code(response_text(resp))


def run_freecad(script_path):
    proc = subprocess.run(
        [FREECADCMD, script_path],
        capture_output=True, text=True, timeout=300,
        cwd=os.path.dirname(script_path),
    )
    out = proc.stdout + proc.stderr
    ok = proc.returncode == 0 and "Traceback" not in out and "Exception" not in out
    return ok, out


def stage(client, name, system, user_prompt, expect_file):
    """Generate, run, and (once) repair a script. Returns (ok, output)."""
    script = ask(client, system, user_prompt)
    for attempt in (1, 2):
        path = os.path.join(OUT, f"{name}_attempt{attempt}.py")
        with open(path, "w") as f:
            f.write(script)
        ok, out = run_freecad(path)
        produced = os.path.exists(expect_file)
        print(f"--- {name} attempt {attempt}: "
              f"{'OK' if ok and produced else 'FAILED'}")
        if ok and produced:
            return True, out
        if attempt == 1:
            print(out[-1500:])
            script = ask(client, system, (
                f"{user_prompt}\n\nYour previous script failed with this "
                f"output — fix it and return the complete corrected "
                f"script:\n```\n{out[-3000:]}\n```\n\nPrevious script:\n"
                f"```python\n{script}\n```"
            ))
    return False, out


def main():
    os.makedirs(OUT, exist_ok=True)
    client = anthropic.Anthropic()
    step_path = os.path.join(OUT, "bracket.step")
    dxf_path = os.path.join(OUT, "bracket_drawing.dxf")

    ok1, _ = stage(
        client, "stage1_solid", STAGE1_SYSTEM,
        f"{PART_SPEC}\n\nExport the final solid as STEP to exactly this "
        f"path: {step_path}",
        step_path,
    )
    if not ok1:
        print("STAGE 1 FAILED — stopping")
        return

    ok2, _ = stage(
        client, "stage2_drawing", STAGE2_SYSTEM,
        f"The STEP file at {step_path} contains an L-shaped mounting "
        f"bracket (80x50 mm base, 60 mm tall wall, four 6.6 mm holes in "
        f"the base, one 20 mm bore in the wall). Create the dimensioned "
        f"drawing and export DXF to exactly this path: {dxf_path}",
        dxf_path,
    )
    print(f"\nRESULT: solid={'OK' if ok1 else 'FAIL'} "
          f"drawing={'OK' if ok2 else 'FAIL'}")
    print(f"artifacts in {OUT}")


if __name__ == "__main__":
    main()
