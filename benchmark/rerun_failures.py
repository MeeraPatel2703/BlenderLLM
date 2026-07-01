"""Re-execute previously failed samples' saved scripts (no regeneration)
after an environment/compat fix, then re-render, re-judge, and update
results.json in place. Samples whose scripts still fail keep score 0.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import anthropic

from benchmark.run_benchmark import (
    DIMENSIONS, aggregate, execute_and_export, judge_sample, log,
    render_views,
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--results-dir", required=True)
    p.add_argument("--data", required=True)
    p.add_argument("--blender", default="blender")
    args = p.parse_args()

    results_path = os.path.join(args.results_dir, "results.json")
    with open(results_path) as f:
        payload = json.load(f)
    with open(args.data) as f:
        by_id = {json.loads(line)["id"]: json.loads(line) for line in f}

    client = anthropic.Anthropic()
    judge_model = payload["report"]["judge"]

    for r in payload["samples"]:
        if not r.get("syntax_error"):
            continue
        sid = r["id"]
        sdir = os.path.join(args.results_dir, sid)
        script_path = os.path.join(sdir, "script.py")
        if not os.path.exists(script_path):
            log(f"  [{r['name']}] no saved script, skipping")
            continue
        with open(script_path) as f:
            script = f.read()
        sample = by_id[sid]
        obj_path = os.path.join(sdir, "model.obj")
        ok, out = execute_and_export(args.blender, script, obj_path)
        if not ok:
            with open(os.path.join(sdir, "blender_error.log"), "w") as f:
                f.write(out[-8000:])
            log(f"  [{r['name']}] still fails")
            continue
        views, _ = render_views(args.blender, script, obj_path, sdir,
                                sample.get("color brightness", "Very Dark"))
        scores, verdicts = judge_sample(client, judge_model, sample,
                                        script, views)
        for d in DIMENSIONS:
            scores.setdefault(d, 1.0)
        r.update(syntax_error=False, scores=scores, verdicts=verdicts)
        r.pop("error", None)
        log(f"  [{r['name']}] recovered: "
            + " ".join(f"{d.split()[0]}={scores[d]:.2f}" for d in DIMENSIONS))

    payload["report"]["CADBench-Sim"] = aggregate(payload["samples"], "Simulative")
    payload["report"]["CADBench-Wild"] = aggregate(payload["samples"], "Wild")
    with open(results_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(json.dumps(payload["report"], indent=2))


if __name__ == "__main__":
    main()
