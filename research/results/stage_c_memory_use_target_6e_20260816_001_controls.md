# EXP-021 Shuffle, Bootstrap, And Per-Task Controls

## Selected T4 Field

| Cell | Correct NDCG@4 | State shuffle | Transition shuffle | Both shuffle |
|---|---:|---:|---:|---:|
| B | .346490 | .141950 | .216460 | .217780 |
| C | .584393 | .358947 | .342960 | .360351 |
| D | .433983 | .361654 | .339809 | .371433 |

Paired task-grouped 95% CIs for correct-minus-control NDCG@4:

| Cell | State shuffle | Transition shuffle | Both shuffle |
|---|---:|---:|---:|
| B | .212071 `[.016520,.439924]` | .134922 `[-.024588,.307877]` | .135341 `[.037059,.296842]` |
| C | .224464 `[.148177,.298564]` | .240077 `[.188709,.289691]` | .224524 `[.159845,.288052]` |
| D | .076482 `[-.048236,.248761]` | .096801 `[-.076267,.269645]` | .066856 `[-.088465,.237850]` |

Point bootstrap CIs for the selected correct model:

- B NDCG@4 `.354824 [.174124,.560938]`; state/residual Spearman
  `.176101 [.044364,.312677]` / `.140398 [-.032056,.294312]`.
- C NDCG@4 `.582534 [.507813,.652267]`; state/residual Spearman
  `.410462 [.343936,.479356]` / `.331170 [.244498,.418554]`.
- D NDCG@4 `.441549 [.272614,.640251]`; state/residual Spearman
  `.116094 [.019204,.218210]` / `-.055564 [-.293084,.156303]`.

## D Per-Task Locked Comparison

| Task | T4 field | Locked transition-only | Difference |
|---|---:|---:|---:|
| 229360a_1 | .535746 | .858769 | -.323023 |
| 2a163ab_1 | .358990 | .670726 | -.311736 |
| 771d8fc_3 | .726045 | .312804 | +.413241 |
| 7d7fbf6_2 | .617051 | .437692 | +.179359 |
| 82e2fac_3 | .266738 | .454902 | -.188164 |
| aa8502b_3 | .649257 | .477422 | +.171835 |
| b0a8eae_1 | .099715 | .089730 | +.009985 |
| b0a8eae_2 | .186476 | .459784 | -.273308 |
| e85d92a_1 | .999576 | .999890 | -.000315 |

Only 4/9 tasks improve. The T4 cross-encoder improves on 3/9. The formal
transition-shuffle CI includes zero, so point-estimate shuffle sensitivity is
not sufficient evidence of robust matching.

