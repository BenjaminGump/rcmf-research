# RCMF Reproducible Pipeline

EXP-037A wraps the existing selector and writer/field/reader mathematics in a
content-addressed, event-driven pipeline. The generic core lives under
`rcmf/pipeline/`; it owns contracts, manifests, atomic stage state, attempt
ledgers, validation, scheduling, audit redaction, and resume. It does not
import AppWorld or interpret benchmark actions.

The AppWorld adapter supplies trajectory loading, transition extraction,
lineage keys, prompt rendering, selector supervision, causal conditions,
action execution, evaluation, and secret-safe audit redaction. The scientific
3D and conditional 1D arms call the same stage implementation. Their allowed
difference is the task-conditioned prompt profile; final development
evaluation uses the common one-demo deployment prompt.

Every stage writes an atomic `output_manifest.json`. A completion is reusable
only when its source commit and every declared output hash validate. The
append-only attempt ledger closes interrupted parent attempts on restart and
resumes at the first incomplete stage. Explicitly classified transient
infrastructure failures may retry without changing scientific identity;
identity, leakage, configuration, and numerical failures stop the DAG.

The parent orchestrator runs in persistent tmux and waits directly on each
child process. The 20-minute monitor reads heartbeat, process, mount, disk, and
GPU state but cannot acquire the scheduler lock or launch a stage.

## Commands

Prepare the content-addressed contract:

```bash
python scripts/prepare_rcmf_reproducible_pipeline_14b.py \
  --config configs/pipeline/rcmf_appworld_repro_14b.yaml \
  --output-root "$RUN_ROOT/preflight" \
  --source-commit "$SOURCE_COMMIT" \
  --smoke-results "$RUN_ROOT/preflight/smoke_results.json"
```

After the frozen preflight authorizes launch:

```bash
bash scripts/launch_rcmf_reproducible_pipeline_14b.sh
```

The raw artifact root is declared in the pipeline config. Git-safe summaries
and audit indexes are exported only after the corresponding raw stage outputs
validate.
