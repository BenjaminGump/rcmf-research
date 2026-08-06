# Milestone 4B Teacher Utility Decomposition

Date: 2026-08-06.

## VERIFIED

- Source commit:
  `e61981fdd10514ba3250f32176f45ea21c2d0661`.
- Artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_4b_20260806_002`.
- Labels:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/student_labels_20260806_002`.
- The train-derived memory prior was estimated only on train labels:
  `mu_i = mean_s utility(s, i)` over valid train rows.
- Validation labels were not used to estimate `mu_i` or select hyperparameters.

## Main Effects

- Train utility matrix shape: `[499, 36]`.
- Validation utility matrix shape: `[139, 36]`.
- Global train mean utility: `0.087255`.
- Memory prior distribution:
  min `-0.000505`, p25 `0.062010`, median `0.097485`,
  p75 `0.112648`, max `0.177404`, std `0.044904`.

Variance decomposition:

- Train total variance: `0.111835`.
- Train total centered SSE: `1877.262695`.
- Train memory-main-effect SSE: `1843.749268`.
- Variance explained by memory main effect: `0.017852`.
- Train residual variance after subtracting `mu_i`: `0.109839`.
- Train memory-mean variance: `0.002016`.
- Train state-mean variance: `0.057149`.
- Validation state-mean variance: `0.054048`.

## Residuals

Train residual distribution:

- count `16,786`.
- mean approximately `0.0`.
- std `0.331419`.
- min `-1.765584`, p05 `-0.437191`, median `-0.065020`,
  p95 `0.658854`, max `2.245731`.

Validation residual distribution:

- count `4,930`.
- mean `-0.029875`.
- std `0.324027`.
- min `-1.863085`, p05 `-0.461009`, median `-0.068290`,
  p95 `0.557907`, max `1.523608`.

## Spectra

Masked/imputed train utility matrix:

- effective rank `26.145799`.

Masked/imputed train residual matrix:

- effective rank `26.145799`.

Missing entries were imputed with `mu_i` for utility and `0.0` for residual
before SVD, so the residual matrix represents remaining state-conditioned
structure after removing the train-derived memory prior.

## Interpretation

The memory main effect explains only about `1.8%` of train utility variance.
This means the global prior is a strong ranking baseline on validation, but it
does not explain most label variation. The successful state-only and signed
two-tower residual ablations confirm that the residual contains usable
held-out-task signal.

