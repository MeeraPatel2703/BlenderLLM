# I re-ran an academic CAD benchmark against a frontier model. The fine-tuned specialist lost.

**TL;DR:** In Dec 2024, researchers published CADBench + BlenderLLM, a Qwen fine-tune for text→CAD that beat every frontier model — their pitch was "you need domain fine-tuning for CAD." I forked their repo yesterday and ran the full 700-sample benchmark against an off-the-shelf frontier model (Claude Fable 5). It beat their specialist on both splits, untrained. If your moat is "we fine-tuned a model for our vertical," this is worth five minutes.

## The setup

CADBench: 700 natural-language requests ("model an L-bracket with four clearance holes..."), each graded against a hidden criteria checklist. The pipeline: model writes Blender Python → script executes headless → 8 renders → vision-LLM judges each criterion. Scripts that crash score zero.

The paper's headline table (Dec 2024): their fine-tune scored 0.748/0.664 (sim/wild splits), while every general model crashed 15–26% of the time and scored far below.

## What I measured (full 700 samples, one afternoon on a laptop)

| | Sim Avg | Wild Avg | crash rate |
|---|---|---|---|
| BlenderLLM (fine-tuned, paper) | 0.748 | 0.664 | 3.4% / 3.5% |
| o1-Preview (paper) | 0.687 | 0.583 | ~16% |
| Claude-3.5-Sonnet (paper) | 0.593 | 0.489 | 16–27% |
| **Claude Fable 5 (my run)** | **0.787** | **0.750** | **3.0% / 5.5%** |

Caveat so you can calibrate: I judged with Opus instead of the paper's GPT-4o (no self-grading; same criteria, same renders), so treat cross-table deltas as directional. The crash-rate numbers are judge-independent and those alone tell the story: code reliability went from 1-in-6 failing to 1-in-33, in ~18 months, with zero domain training.

## Why I care (and maybe you should)

I build in mechanical engineering tooling (drawing review / drafting automation). The uncomfortable takeaway for anyone whose deck says "proprietary fine-tuned model for [vertical]": the frontier eats static fine-tunes on a ~1 year lag. The paper wasn't wrong when published — it was obsoleted by the base rate of model improvement.

What *didn't* get eaten is more interesting. When I pushed past the benchmark into actual drafting work (3D model → dimensioned 2D engineering drawing), the model nailed geometry and views but produced amateur dimensioning — overlapping labels, extent dims instead of datum-referenced feature dims. It only produced a machinist-usable drawing after I wired in a deterministic feature-recognition engine (hole positions, patterns, fillets) and constrained the model to *choosing the dimensioning scheme* while code placed every line. One nice moment: told "holes 10mm from the edge of the usable area," it dimensioned them 18mm from the part edge — it inferred usable area starts after the 8mm wall. Drafter-level reasoning, but only inside a harness.

So the defensible layer in vertical AI right now, at least in CAD: not the model, but (1) the domain benchmark/eval loop that tells you when output is actually correct, (2) deterministic domain infrastructure the model plugs into, and (3) the conventions/judgment encoded in the harness. The model is the intern; you're selling the training program.

Fork with the full harness, results, and the drawing pipeline: github.com/MeeraPatel2703/BlenderLLM

Happy to share the eval harness pattern (generate → execute → render → judge) if you're building evals for your own vertical — it generalizes way beyond CAD.
