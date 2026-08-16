# EXP-021 Relative And Pairwise Target Results

## Definitions

- T0: locked absolute raw utility.
- T1: utility minus within-state median; mean-centered is diagnostic.
- T2: median-centered utility divided by within-state IQR plus epsilon, with
  train-fixed numerical clipping.
- T3: averaged within-state percentile rank.
- T4: pairwise preference for utility gaps at least `.05`, weighted by
  `min(abs(gap)/.25, 1)`.
- T5: predicted-query-intent and transition-signature compatibility; oracle
  query intent is diagnostic only.
- T6: predicted-intent prior plus a pairwise/percentile relative residual.
- T7: pairwise comparisons among transitions sharing coarse intent.

## A-Only Grouped CV

| Architecture/target | Epoch | NDCG@4 | Gap accuracy | State gap | Transition gap |
|---|---:|---:|---:|---:|---:|
| Field T3 | 120 | .391347 | .532515 | .022236 | .016257 |
| Cross T3 | 120 | .472438 | .569729 | .118961 | .093452 |
| Field T4 | 120 | .423183 | .558759 | .025350 | .009706 |
| Cross T4 | 60 | .460331 | .570002 | .108558 | .105096 |
| Field T6 | 30 | .384355 | .525447 | .000325 | .013668 |
| Cross T6 | 30 | .442133 | .550132 | .073585 | .062880 |
| Field T7 | 30 | .410596 | .516745 | .017358 | .065980 |
| Cross T7 | 30 | .465734 | .561677 | .112399 | .137684 |

The frozen A-only rule selected T4 field epoch 120. All five grouped folds were
positive under the selection score. No B/C/D metric changed this choice.

## B/C/D NDCG@4

| Target/model | B | C | D | D state Spearman | D residual Spearman | D gap accuracy | D raw Huber |
|---|---:|---:|---:|---:|---:|---:|---:|
| T0 transition-only | .389826 | .396519 | .480274 | .054476 | n/a | n/a | .066209 |
| T0 field | .287604 | .612958 | .510827 | .134551 | .439346 | n/a | .085152 |
| T0 cross | .448943 | .657188 | .523176 | .117526 | .063559 | n/a | .098524 |
| T3 field | .249460 | .597460 | .405944 | .082621 | .097507 | .597509 | .364594 |
| T3 cross | .278528 | .669877 | .490757 | .054680 | .146247 | .573687 | .384892 |
| T4 field | .346490 | .584393 | .433983 | .117200 | -.051060 | .583228 | .306444 |
| T4 cross | .294308 | .597460 | .384751 | -.033663 | .203538 | .498381 | .158151 |
| T5 oracle intent | .204336 | .364124 | .337362 | .073997 | n/a | .526623 | .087959 |
| T5 predicted intent | .210625 | .328699 | .344384 | .034798 | n/a | .498028 | .084080 |
| T6 field | .308736 | .470707 | .544606 | .021913 | .281752 | .559839 | .584675 |
| T6 cross | .507111 | .505530 | .373134 | .030425 | .078922 | .515885 | .459092 |
| T7 field | .300274 | .535616 | .501103 | .111559 | .010895 | .630894 | .102188 |
| T7 cross | .442732 | .536350 | .389853 | .001588 | .208042 | .498289 | .453536 |

All additional NDCG@1/8, recall, utility mass, candidate-target metrics,
per-state outputs, and B/C controls are preserved in
`model_audit_summary.json` and the per-row model output directories.

No candidate combines a gain over locked transition-only with the required
state/residual correlation, both shuffle sensitivities, bootstrap
significance, and 6/9 task consistency on D.

