# EXP-021 Locked Raw-Utility Decomposition

## Contract

The immutable comparator is `u(s,m) = L0(s) - L_transition(s,m)`. Cell A alone
estimates `mu`, state effects `a_s`, and transition effects `b_m`; B/C/D use
those frozen estimates. No prior cache row was rewritten.

## Cell A Components

| Quantity | Value |
|---|---:|
| Global mean `mu` | .0648378143 |
| Total utility variance | .0944172736 |
| State-main variance | .0392744504 |
| Transition-main variance | .0019005376 |
| Additive-main variance | .0410381065 |
| Residual variance | .0533791671 |
| State-only explained fraction | .4145170 |
| Transition-only explained fraction | .0186794 |
| Additive explained fraction | .4346462 |
| Raw effective rank | 35.878806 |
| Residual effective rank | 41.605193 |

## Cells

| Cell | Rows | Mean | Std | Neg/neutral/pos | Raw/residual rank | Mean state scale |
|---|---:|---:|---:|---:|---:|---:|
| A | 8,205 | .064838 | .307274 | 3023/1089/4093 | 35.879/41.605 | .190998 |
| B | 2,051 | .003757 | .180128 | 717/534/800 | 13.335/13.338 | .135882 |
| C | 2,296 | .069050 | .298566 | 795/325/1176 | 17.461/20.616 | .168014 |
| D | 576 | .027876 | .152277 | 159/154/263 | 9.807/10.105 | .090508 |

Mean/std transition popularity is `.065126/.042372` on A,
`.004117/.053317` on B, `.069131/.033176` on C, and `.027876/.033066` on D.
The full quantiles, per-state scales, per-transition counts, singular values,
and matrix masks are stored in `locked_raw_utility_decomposition.json`.

VERIFIED: state main effects dominate transition popularity on A, but more than
half the raw variance remains in the interaction residual. The held-out cells
retain nonzero residual variance and rank; the later failures are not caused by
an identically constant target.

