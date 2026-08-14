# RCMF Version History

This directory records research architecture families. A version name marks a
reproducible design boundary; it does not imply end-to-end task success.

## RCMF V1 - Destructive Prefix Prototype

Characteristics:

- virtual-prefix or early high-magnitude injection;
- prompt length, position, or token embeddings were strongly disturbed;
- Qwen did not reliably preserve valid Python/tool trajectories;
- no meaningful complete AppWorld score was established.

V1 has no Git tag. Its representative historical commit has not been audited.

## RCMF V2 - Low-Disturbance Semantic-Retrieval Prototype

V2 is a family:

- V2a: low-disturbance injection;
- V2b: low-disturbance injection plus semantic retrieval.

Historical AppWorld results:

- low-disturbance step-100 first-10: `3/10`;
- low-disturbance final first-10: `2/10`;
- semantic-retrieval first-10: `4/10`;
- semantic-retrieval partial first-37: `7/37`;
- locked bare-Qwen first-37: `10/37`.

V2 has no Git tag in this freeze. The exact representative commit has not been
independently audited across its historical artifacts.

## RCMF V3 - Component-Validated Trajectory-Memory Field (Pre-Transition)

V3 uses one complete successful trajectory as one human-readable
`MemoryRecord` and one compiled field write. It established validated teacher,
signed-selector, reversible-field, input-injection, and oracle-decoder
components, but did not validate content-derived static trajectory programs or
an end-to-end trajectory-memory agent.

Authoritative references:

- source-state commit:
  `97ca723ad66597d2afcbbce1eb5466eb34c009f6`;
- annotated tag: `rcmf-v3-component-validated-pre-transition`;
- archive branch: `archive/rcmf-v3-component-validated`;
- manifest:
  `research/versions/RCMF_V3_COMPONENT_VALIDATED_PRE_TRANSITION.md`.

V3 must not be described as final, successful, or working end to end.

## V4 Candidate - Decision-Transition Memory

Development begins on `research/v4-decision-transition-memory`. This is a
candidate research direction, not an official validated V4 release. No V4 tag
exists. The initial pilot changes memory granularity while preserving the
fixed-cost reversible-field principle.
