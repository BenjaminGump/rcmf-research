# RCMF Research Vision

Reversible Compiled Memory Field (RCMF) is intended to turn prior agent
experience into compact behavior-control state that can be added, removed,
replaced, audited, and recompiled without retraining the frozen base model.

The AppWorld/Qwen3-8B line currently tests whether memory derived from official
successful trajectories can improve an agent that already uses the original
full AppWorld prompt and a fixed ReAct execution loop.

Key research constraints:

- The frozen Qwen backbone should remain behaviorally identical to the bare
  baseline when memory scale is zero.
- Training examples must be full per-step trajectory examples: system prompt,
  user query, prior response/observation trace, and current-step response as the
  target.
- Target tokens should include EOS and only target tokens should contribute to
  supervised loss.
- Prepared data must not be truncated. Any context-window conflict must be
  measured and explicitly approved before filtering.
- AppWorld evaluation must keep the baseline prompt, task order, max steps, and
  generation settings fixed when comparing bare Qwen and RCMF.

Long-term target:

- retain baseline successes;
- gain additional successes from memory;
- avoid global prompt perturbations that damage unrelated tasks;
- make per-task changes explainable through trace and memory diagnostics.
