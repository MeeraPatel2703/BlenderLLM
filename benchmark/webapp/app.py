"""Local frontend for the text -> 3D solid -> 2D drawing pipeline.

Run:  set -a; source ~/draftaid-mvp/.env; set +a
      .venv/bin/python benchmark/webapp/app.py
Then open http://localhost:8437
"""
import os
import threading
import traceback
import uuid

from flask import Flask, jsonify, request, send_from_directory

from pipeline import run_pipeline, run_pipeline_blenderllm

HERE = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(HERE, "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

app = Flask(__name__, static_folder=os.path.join(HERE, "static"))
jobs = {}


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.post("/api/generate")
def generate():
    spec = (request.json or {}).get("spec", "").strip()
    model = (request.json or {}).get("model", "fable")
    if not spec:
        return jsonify({"error": "empty spec"}), 400
    job_id = uuid.uuid4().hex[:12]
    jobdir = os.path.join(JOBS_DIR, job_id)
    os.makedirs(jobdir)
    job = {"id": job_id, "status": "queued", "spec": spec, "model": model}
    jobs[job_id] = job

    def work():
        try:
            if model == "blenderllm":
                run_pipeline_blenderllm(job, jobdir, spec)
            else:
                run_pipeline(job, jobdir, spec)
        except Exception as e:
            traceback.print_exc()
            job["error"] = str(e)
            job["status"] = "error"

    threading.Thread(target=work, daemon=True).start()
    return jsonify({"id": job_id})


@app.get("/api/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


@app.get("/jobs/<job_id>/<path:name>")
def artifact(job_id, name):
    return send_from_directory(os.path.join(JOBS_DIR, job_id), name)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8437, debug=False)
