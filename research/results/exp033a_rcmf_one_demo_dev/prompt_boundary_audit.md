# EXP-033A One-Demo Prompt Boundary Audit

## Frozen Prompt

- Source: `prompt.AGENT_SYSTEM_PROMPT_TEMPLATE_AW`
- Original raw prompt SHA256: `dd74c379c97031a062ba79b2b82d3992ec3b38870792f53d86821544f994c4c3`
- Original structured messages: `74`
- Original structured-message SHA256: `f9a6937120b7da883c60e9b5e9187290bf71d3d68b0182640487b705f4cb3734`
- Separator byte offsets: `8953`, `15852`, `27991`
- Retained demo #1 section SHA256: `32348a5889682499b1cc17b7dced74dd706db12b6e248c1e6c7dfba5e50ed713`
- Removed demo #2 SHA256: `0a34647714de22cffebd072a933bf3341511cd4145cc8432582715d7d743f52e`
- Removed demo #3 SHA256: `6c4afd304257d0cc57d135180ba9a0050ae46043397c15837b46efcd764d82d6`
- Final one-demo raw prompt SHA256: `a0a8d3b2e10f167dba5dcab5ad62fa8f6737629b813d2d0e27af4872bef9e27b`
- Final one-demo structured messages: `20`
- Final structured-message SHA256: `90c375658628663fbe5b5110e8efc619b2edab229a6d9a64d4e253d2e559ddbe`

The original prompt stores the start of demos #2 and #3 inside the preceding
`user` message after a full separator. The profile therefore operates on the
three raw complete-demo separators, not an arbitrary message count. It retains
the exact raw prefix through demo #1 plus the exact trailing key instructions.
The existing `full_demo` profile remains byte-for-byte unchanged.

## Complete Boundaries

- Demo #1: structured indices `0..18`; index 18 is clipped at separator 1.
- Demo #2: structured indices `18..40`; boundary messages are clipped at separators 1 and 2.
- Demo #3: structured indices `40..72`; boundary messages are clipped at separators 2 and 3.
- Trailing key instructions: structured index `73` in the original prompt and index `19` in the retained prompt.

## Retained Message Hashes

| Index | Role | Bytes | Content SHA256 |
|---:|---|---:|---|
| 0 | user | 2937 | `b827fcf783d32d29a1a63dd8e1016ec838d03a6a5a0e859df979815609270fd6` |
| 1 | assistant | 328 | `2ced9ff94b023ba2173b6f8e174851f3c16f300fed28a82d3b9e544dc2857606` |
| 2 | user | 308 | `f4cc24f87ebba30c956340ef99f2831aa3f9dcabd502aeb5490999e4e7a21136` |
| 3 | assistant | 243 | `9616f12b5402076c92922997efa2bdcdaec37c6500120d447fa2aa1ac5778a40` |
| 4 | user | 407 | `3fe1d4fa53e679919e8651e18c014e09e19cf415a815b4351b998e8c9753f040` |
| 5 | assistant | 213 | `cf9b2145793c45f95762f6bc00781b8f37c610641754bf2719d3b44d4bfdcf6d` |
| 6 | user | 1381 | `797994b26bd4c7941469821c933b6ffd9d8aef8eafa54d5ef49a7a433cae15b0` |
| 7 | assistant | 158 | `5b86e56f03c4b608d9b84b796a1b95139149eae989c5ca5620992637774ca0ef` |
| 8 | user | 132 | `216d390e50118744fe58a6088a69aca0144d57badaffab3ea972961f94475ac0` |
| 9 | assistant | 287 | `a307393e4957898d97742ff5185f18d18fac304a7027915280004b0001faafee` |
| 10 | user | 141 | `be0a6bf3fb833754b8fa38b31d8f214314534c679201a4d7302be20961028617` |
| 11 | assistant | 357 | `4d55ff2ca5ef6ef4293ca1ac0b16a3d30befe48023112e1b31a9edeaf6d485f6` |
| 12 | user | 56 | `418735e5dd4ab0da789bf8f90b0493b932674120ca0e80f13fd94e323c1e032e` |
| 13 | assistant | 305 | `f3abf7edb2c599340370a322f38961a6607adcb003db5da3b10191b3eaf1ace7` |
| 14 | user | 493 | `074b1b408cacbe54939c5235ea9155fbad5c6f17f0f53307cddfb20c76ffbe68` |
| 15 | assistant | 564 | `b5976b56728ae1eafc3ad31d5c85763ee8604e5f49ed38c1299a914b607819ac` |
| 16 | user | 35 | `dbe8209762022cf2d75e4fa7dced815bd3e52772952a735545e81877b555b6d8` |
| 17 | assistant | 353 | `c0b7c3be7207dc50745968561391c8d7906e959f5358ccf9aee9757dcb8b951d` |
| 18 | user | 88 | `9d3253d0233414e36166a475ce1d44c4b80eef5750a3b0a1a82359851e8e7934` |
| 19 | user | 1155 | `b60055dd5a9459d3f857d845fb9945a9441b72181fc69884e887b7bf36754ae3` |

## Dev Leakage Audit

- Official dev source: actual AppWorld 0.1.0 `load_task_ids(dataset_name="dev")`.
- Official dev task count: `57`.
- Ordered dev list SHA256: `c6aad8dca959d9c54537555dd6c3a4ececdd55390029511ab7971550d796e463`.
- Scanned legacy task specifications: `732`.
- Retained demo instruction SHA256: `7f5c6f0873d64024495c56ddccb66b1bfa825ab982b76f6021213a696380025e`.
- Exact retained-demo instruction matches in all task specs: `0`.
- Demo identity: prompt-native demonstration whose exact task instruction is absent from dev and every scanned legacy task spec.
- 499-memory parent tasks: `37`; overlap with dev: `0`.
- Dev ground-truth values entering model-visible input: `0`.

The complete machine-readable evidence is in `prompt_manifest.json`,
`dev_manifest.json`, and `dev_leakage_audit.json` beside this file.
