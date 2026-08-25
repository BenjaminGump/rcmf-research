# EXP-030A Preflight Corrections

Two implementation-only corrections were made before any EXP-030A GPU work.
Neither changes a scientific input, model, sample, selector score, or experiment
parameter.

1. The reconciled decision corpus stores task identity in `metadata.task_id`.
   The first preflight entry point incorrectly expected a top-level `task_id`.
   `prepare_cross_attention_field_8b_v2.py` provides a tested read-only schema
   adapter and does not rewrite the corpus.
2. The milestone prose transcribed the frozen selector SHA with an extra `b`
   (`...d42bb012...`). The immutable selector file and its EXP-025C
   `selector_summary.json` both verify the actual file SHA256 as
   `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`.
   The verified runtime config records that exact existing-file hash. The
   selector file, seed checkpoints, calibration, predictions, scores, and
   rankings remain unchanged.

Both failures occurred before `AttemptLedger` creation and before Qwen was
loaded for EXP-030A. No H100 work had started.
