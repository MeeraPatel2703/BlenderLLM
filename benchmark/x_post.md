# X post (fact-checked)

---

In Dec 2024, researchers fine-tuned a 7B model for text→CAD (BlenderLLM) and showed it beating every frontier model on their benchmark. Their conclusion: CAD needs domain fine-tuning.

I re-ran their full benchmark (CADBench, all 700 tasks) against an untrained frontier model this week. It won.

CADBench avg (sim / wild):
• Claude Fable 5: 0.787 / 0.750 ← zero CAD training
• BlenderLLM (fine-tuned): 0.748 / 0.664
• o1-Preview: 0.687 / 0.583
• Claude 3.5 Sonnet: 0.593 / 0.489

The stat that needs no judge: script crash rate. Late-2024 frontier models wrote Blender code that failed 15–26% of the time. Fable: 3.0% sim / 5.5% wild. Reliability went from 1-in-6 failing to ~1-in-30 in 18 months.

Biggest sub-score gap vs the fine-tune: instruction-following (0.72 vs 0.64 sim, 0.76 vs 0.58 wild) — the thing 12k training pairs couldn't teach.

Methodology honesty: same 700 prompts, same criteria checklists, same pipeline (generate bpy → execute headless → render 8 views → LLM judges each criterion). One deviation: I judged with Opus 4.8 instead of their GPT-4o, so cross-table gaps are directional. Crash rates are judge-independent.

Takeaway if you're building vertical AI: static fine-tunes depreciate at the speed of the frontier. The durable layer is the eval harness, the domain infrastructure, and the judgment you encode around the model — not the weights.

Fork with harness + full per-sample results: github.com/MeeraPatel2703/BlenderLLM

---

# Fact-check appendix (claim → source)

1. "Dec 2024 ... BlenderLLM" — arXiv 2412.14203, tech report released 12/16/2024 (repo README).
2. "7B fine-tune" — base model Qwen2.5-Coder-7B-Instruct (repo README, model card).
3. "all 700 tasks" — CADBench.jsonl: 500 type=Simulative + 200 type=Wild, verified by count; 700/700 result checkpoints on disk.
4. "0.787 / 0.750" — recomputed from raw per-sample result.json files, not the summary: sim avg 0.787 (attr 0.858, spat 0.779, inst 0.723), wild avg 0.750 (attr 0.769, spat 0.724, inst 0.757).
5. "3.0% / 5.5% crash rate" — recomputed: 15/500 sim, 11/200 wild scripts failed to execute.
6. Comparison rows — paper's published table (repo README): BlenderLLM 0.748/0.664 (E 3.4%/3.5%), o1-Preview 0.687/0.583 (15.6%/17.5%), Claude-3.5-Sonnet 0.593/0.489 (15.6%/26.5%), GPT-4o 0.565/0.444 (21.4%/28.5%).
7. "instruction-following 0.72 vs 0.64 / 0.76 vs 0.58" — our inst 0.723 sim / 0.757 wild vs paper's BlenderLLM inst 0.638 sim / 0.578 wild.
8. "18 months" — Dec 2024 → Jul 2026.
9. Judge deviation disclosed in post. Also noted in repo (not in post, for space): execution env is Blender 5.1 with a compat shim for the removed use_auto_smooth API so pre-4.1 bpy code isn't unfairly penalized — this matches the paper-era environment behavior.
10. Not claimed anywhere: dollar cost (not logged), exact wall-clock (varied due to shared machine).
