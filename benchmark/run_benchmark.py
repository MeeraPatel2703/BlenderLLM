"""CADBench runner for API-served models (Anthropic).

Phases per sample:
  1. generate  - model writes a bpy script for the instruction
  2. execute   - headless Blender runs the script and exports an .obj
                 (failure here counts toward E_syntax; all criteria score 0)
  3. render    - 8 views using the repo's camera/brightness config
  4. judge     - a vision LLM grades each criterion true/false from the
                 renders + script (paper used GPT-4o; we default to Opus)

Scores: per-dimension mean over samples (failed samples contribute 0),
reported separately for Simulative and Wild splits, like the README table.
"""

import argparse
import base64
import concurrent.futures
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.config import CAMERA_ANGLES, BRIGHTNESS
from scripts.geometry_utils import calculate_bounding_box

import anthropic

SYSTEM_PROMPT = (
    "You are an expert in using bpy script to create 3D models. Based on the "
    "following instruction, your task is to write the corresponding bpy script "
    "that will generate the desired 3D model in Blender. Please pay close "
    "attention to every detail in the script and ensure it fully adheres to "
    "the provided specifications."
)

DIMENSIONS = [
    "Object Attributes",
    "Spatial Understanding and Structure",
    "User Instruction Understanding and Execution",
]

_print_lock = threading.Lock()


def response_text(resp):
    return "\n".join(b.text for b in resp.content if b.type == "text")


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def extract_code(text):
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    if blocks:
        return "\n\n".join(blocks)
    # unterminated fence (response truncated): take everything after it
    m = re.search(r"```(?:python|py)?\s*\n", text)
    if m:
        return text[m.end():]
    return text


def api_call_with_retry(fn, attempts=4):
    import time
    for i in range(attempts):
        try:
            return fn()
        except (anthropic.APIStatusError, anthropic.APIConnectionError,
                json.JSONDecodeError, AttributeError):
            if i == attempts - 1:
                raise
            time.sleep(15 * (i + 1))


def generate_script(client, model, instruction):
    def call():
        resp = client.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": instruction}],
        )
        return extract_code(response_text(resp))
    return api_call_with_retry(call)


def run_blender(blender, body, timeout=240):
    """Run a python body under headless Blender. Returns (ok, output)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(body)
        path = f.name
    try:
        proc = subprocess.run(
            [blender, "--background", "--factory-startup",
             "--python-exit-code", "1", "--python", path],
            capture_output=True, text=True, timeout=timeout,
        )
        out = proc.stdout + proc.stderr
        return proc.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    finally:
        os.unlink(path)


PREAMBLE = (
    "import bpy\nimport os\nimport math\n"
    # compat shim: use_auto_smooth was removed in Blender 4.1, but models
    # trained on older bpy still set it; accept it as a no-op like the
    # paper's eval environment did
    "if not hasattr(bpy.types.Mesh, 'use_auto_smooth'):\n"
    "    bpy.types.Mesh.use_auto_smooth = bpy.props.BoolProperty(default=True)\n"
    "    bpy.types.Mesh.auto_smooth_angle = bpy.props.FloatProperty(default=0.523599)\n"
    "bpy.ops.object.select_all(action='SELECT')\nbpy.ops.object.delete()\n"
)


def execute_and_export(blender, script, obj_path):
    body = (
        PREAMBLE + script +
        f"\nbpy.ops.wm.obj_export(filepath=r'{obj_path}')\n"
    )
    ok, out = run_blender(blender, body)
    if not ok:
        return False, out
    if not os.path.exists(obj_path):
        return False, "no obj exported"
    try:
        coords = calculate_bounding_box(obj_path)
    except Exception as e:
        return False, f"empty/invalid obj: {e}"
    if any(math.isinf(c) for xyz in coords for c in xyz):
        return False, "empty obj (no vertices)"
    return True, out


def render_views(blender, script, obj_path, out_dir, brightness_key, resolution=512):
    coords = calculate_bounding_box(obj_path)
    brightness = BRIGHTNESS.get(brightness_key, BRIGHTNESS["Very Dark"])
    body = PREAMBLE + script + "\n"
    for i, (loc, rot) in enumerate(zip(coords, CAMERA_ANGLES), start=1):
        body += f"""
camera = bpy.data.cameras.new('Camera{i}')
cam_obj = bpy.data.objects.new('Camera{i}', camera)
bpy.context.scene.collection.objects.link(cam_obj)
bpy.context.scene.camera = cam_obj
cam_obj.location = {loc}
cam_obj.rotation_euler = {rot}
light_data = bpy.data.lights.new(name='Key_Light{i}', type='POINT')
light_obj = bpy.data.objects.new(name='Key_Light{i}', object_data=light_data)
bpy.context.collection.objects.link(light_obj)
light_obj.location = ({loc[0] * 1.2}, {loc[1] * 1.2}, {loc[2] * 1.2})
light_data.energy = {brightness[0][i - 1]}
bpy.context.scene.render.resolution_x = {resolution}
bpy.context.scene.render.resolution_y = {resolution}
bpy.context.scene.render.film_transparent = False
bpy.context.scene.render.image_settings.file_format = 'PNG'
bpy.context.scene.render.filepath = os.path.join(r'{out_dir}', 'view{i}.png')
bpy.ops.render.render(write_still=True)
light_obj.hide_render = True
cam_obj.hide_render = True
"""
    ok, out = run_blender(blender, body, timeout=420)
    views = sorted(
        os.path.join(out_dir, f) for f in os.listdir(out_dir)
        if f.startswith("view") and f.endswith(".png")
    )
    return views if ok else views, out


def flatten_criteria(criteria):
    """-> list of (key, dimension, text). key = 'dim|subdim|idx'."""
    flat = []
    for dim in DIMENSIONS:
        for subdim, items in criteria.get(dim, {}).items():
            for idx, text in enumerate(items):
                flat.append((f"{dim}|{subdim}|{idx}", dim, text))
    return flat


JUDGE_SYSTEM = (
    "You are a meticulous CAD quality inspector. You are shown renders of a 3D "
    "model (8 views of the same object from different angles) that was "
    "generated by running a bpy (Blender Python) script, plus the script "
    "itself and the original user instruction. For each numbered criterion, "
    "decide if it is satisfied. Judge shape/spatial criteria from the images; "
    "judge 'set in the script' criteria (color, size values, etc.) from the "
    "script. Be strict but fair: partial or ambiguous satisfaction is false. "
    "Respond with ONLY a JSON object mapping each criterion number (as a "
    "string) to true or false."
)


def judge_sample(client, judge_model, sample, script, views):
    flat = flatten_criteria(sample["criteria"])
    content = []
    for v in views:
        with open(v, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": data},
        })
    numbered = "\n".join(f"{i}. {text}" for i, (_, _, text) in enumerate(flat))
    content.append({
        "type": "text",
        "text": (
            f"User instruction given to the model:\n{sample['instruction']}\n\n"
            f"The bpy script that produced the renders:\n```python\n{script}\n```\n\n"
            f"Criteria:\n{numbered}\n\n"
            'Return JSON only, e.g. {"0": true, "1": false, ...}'
        ),
    })
    def call():
        resp = client.messages.create(
            model=judge_model,
            max_tokens=2048,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
        text = response_text(resp)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(m.group(0))
    verdicts = api_call_with_retry(call)
    per_dim = {}
    for i, (_, dim, _) in enumerate(flat):
        per_dim.setdefault(dim, []).append(bool(verdicts.get(str(i), False)))
    return {dim: sum(v) / len(v) for dim, v in per_dim.items()}, verdicts


def process_sample(client, args, sample, out_root):
    sid = sample["id"]
    sdir = os.path.join(out_root, sid)
    os.makedirs(sdir, exist_ok=True)
    # resume: skip samples already scored in a previous run
    ckpt = os.path.join(sdir, "result.json")
    if os.path.exists(ckpt):
        with open(ckpt) as f:
            return json.load(f)
    # one CADBench sample lacks name/type; default type by split heuristic
    result = {"id": sid, "name": sample.get("name", sid[:8]),
              "type": sample.get("type", "Wild")}
    try:
        script = generate_script(client, args.model, sample["instruction"])
        with open(os.path.join(sdir, "script.py"), "w") as f:
            f.write(script)
        result["script"] = True

        obj_path = os.path.join(sdir, "model.obj")
        ok, out = execute_and_export(args.blender, script, obj_path)
        if not ok:
            with open(os.path.join(sdir, "blender_error.log"), "w") as f:
                f.write(out[-8000:])
            result.update(syntax_error=True,
                          scores={d: 0.0 for d in DIMENSIONS})
            log(f"  [{result['name']}] SYNTAX ERROR")
            with open(ckpt, "w") as f:
                json.dump(result, f)
            return result
        result["syntax_error"] = False

        views, _ = render_views(args.blender, script, obj_path, sdir,
                                sample.get("color brightness", "Very Dark"))
        if not views:
            result.update(render_failed=True,
                          scores={d: 0.0 for d in DIMENSIONS})
            log(f"  [{result['name']}] RENDER FAILED")
            with open(ckpt, "w") as f:
                json.dump(result, f)
            return result

        scores, verdicts = judge_sample(client, args.judge_model, sample,
                                        script, views)
        for d in DIMENSIONS:
            scores.setdefault(d, 1.0)
        result["scores"] = scores
        result["verdicts"] = verdicts
        log(f"  [{result['name']}] "
            + " ".join(f"{d.split()[0]}={scores[d]:.2f}" for d in DIMENSIONS))
    except Exception as e:
        result.update(error=str(e), syntax_error=True,
                      scores={d: 0.0 for d in DIMENSIONS})
        log(f"  [{result['name']}] ERROR: {e}")
    with open(ckpt, "w") as f:
        json.dump(result, f)
    return result


def aggregate(results, split):
    rows = [r for r in results if r["type"] == split]
    if not rows:
        return None
    agg = {d: sum(r["scores"][d] for r in rows) / len(rows) for d in DIMENSIONS}
    agg["Avg"] = sum(agg[d] for d in DIMENSIONS) / len(DIMENSIONS)
    agg["E_syntax"] = sum(1 for r in rows if r.get("syntax_error")) / len(rows)
    agg["n"] = len(rows)
    return agg


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="claude-fable-5")
    p.add_argument("--judge-model", default="claude-opus-4-8")
    p.add_argument("--data", default=os.path.join(
        os.path.dirname(__file__), "data", "CADBench.jsonl"))
    p.add_argument("--out", default=os.path.join(
        os.path.dirname(__file__), "results"))
    p.add_argument("--blender", default="blender")
    p.add_argument("--n-sim", type=int, default=40)
    p.add_argument("--n-wild", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()

    with open(args.data) as f:
        data = [json.loads(line) for line in f]
    rng = random.Random(args.seed)
    sim = rng.sample([d for d in data if d["type"] == "Simulative"], args.n_sim)
    wild = rng.sample([d for d in data if d["type"] == "Wild"], args.n_wild)
    samples = sim + wild

    out_root = os.path.join(args.out, args.model.replace("/", "_"))
    os.makedirs(out_root, exist_ok=True)
    client = anthropic.Anthropic()

    log(f"Benchmarking {args.model} on {len(sim)} sim + {len(wild)} wild "
        f"samples (judge: {args.judge_model})")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(process_sample, client, args, s, out_root)
                   for s in samples]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())
            log(f"progress: {len(results)}/{len(samples)}")

    report = {"model": args.model, "judge": args.judge_model,
              "seed": args.seed,
              "CADBench-Sim": aggregate(results, "Simulative"),
              "CADBench-Wild": aggregate(results, "Wild")}
    with open(os.path.join(out_root, "results.json"), "w") as f:
        json.dump({"report": report, "samples": results}, f, indent=2)

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
