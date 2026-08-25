# EXP-030A Related Work And Novelty Boundary

EXP-030A implements a dedicated memory cross-attention reader from published
method descriptions. The reader, its separate memory softmax, and its
zero-initialized low-rank residual fusion are borrowed mechanisms and are not
claimed as RCMF contributions. No third-party source code is copied.

## Related Mechanisms

- **Prefix-Tuning** keeps the language model frozen and optimizes continuous
  prefixes that later tokens attend to as virtual tokens. It establishes a
  parameter-efficient continuous conditioning interface, but stores a distinct
  learned prefix rather than reversibly compiling a raw-memory ledger.
  [Li and Liang, 2021](https://arxiv.org/abs/2101.00190)
- **LLaMA-Adapter** inserts learned prompts and zero-initialized attention into
  a frozen LLaMA. EXP-030A borrows the conservative zero-effect initialization
  principle, not its learned task-prompt formulation.
  [Zhang et al., 2023](https://arxiv.org/abs/2303.16199)
- **Flamingo / Perceiver Resampler** compresses variable-size encoder features
  into a fixed latent set and connects them to a frozen language model through
  gated cross-attention. This is prior art for a dedicated, fixed-slot external
  channel; EXP-030A does not claim the cross-attention reader or resampling idea.
  [Alayrac et al., 2022](https://arxiv.org/abs/2204.14198),
  [Jaegle et al., 2021](https://arxiv.org/abs/2103.03206)
- **TokenMem** stores free-text passages, retrieves top-1 with FAISS, encodes
  the retrieved raw passage separately through the frozen LLM, and uses its
  layer-wise token representations as cross-attention keys and values. It also
  uses a two-phase utilization/compliance curriculum and zero-initialized
  rank-16 fusion. EXP-030A's Phase A-C reader deliberately follows this
  published interface at smaller AppWorld scale; it is a selected-memory,
  retrieval-based baseline rather than the proposed whole-bank field.
  [Yu et al., 2026](https://arxiv.org/html/2607.22625)
- **KBLaM** maps every knowledge item to continuous key/value pairs and reads
  the entire KB with rectangular attention. Its inference overhead is linear
  in KB size, so read cost still grows with the number of KB entries.
  [Wang et al., 2025](https://arxiv.org/abs/2410.10450)
- **Infini-attention** accumulates streaming segments into a bounded
  associative matrix using additive or delta-style updates and retrieves with
  linear attention. The published contract does not preserve per-record raw
  provenance or provide exact deletion of one arbitrary raw-ledger record.
  [Munkhdalai et al., 2024](https://arxiv.org/abs/2404.07143)

## RCMF Claim Under Test

The RCMF-specific hypothesis is not that cross-attention is novel. It is that
an auditable raw human-readable memory ledger can be compiled by reversible
per-record addition into one fixed-size set of semantic slots for the whole
bank, with:

1. feed-forward addition of a new memory without scanning existing memories;
2. exact subtraction, replacement, and parent removal from stored per-memory
   contributions;
3. whole-bank read cost and output shape independent of memory count; and
4. no retrieval, top-k selection, or raw-memory text in the query prompt.

Phase A-C tests only whether the borrowed selected-single-memory reader works
on AppWorld. Phase D-F is allowed only after that reader gate passes and is the
actual reversible constant-size whole-bank compilation test.
