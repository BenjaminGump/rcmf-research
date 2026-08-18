# EXP-025C-R Missing-Row Policy

Version: `selector_behavioral_missing_policy_7cr_v1`

This prospective policy applies only when a frozen selector chooses a frozen
signature class, no member of that class fits the locked context, truncation is
forbidden, and no same-class alternative exists.

The logical condition remains in the manifest with:

```text
condition_status = over_context_missing
valid_for_generation = false
valid_for_pairwise_comparison = false
missing_reason = selected_signature_class_has_no_context_feasible_raw_member
```

It receives no model response, extracted code, execution result, metric,
success/failure label, zero, neutral value, or imputation. Comparisons not
involving the condition use their full preregistered sets. Comparisons involving
it use paired complete cases, retain all other rows from its task, report exact
per-task denominators, and use task-grouped bootstrap resampling over valid
pairs. A separate deterministic one-row best/worst bound may be reported for
bounded metrics, but it is not a scientific imputation.

For EXP-025C-R this policy applies to exactly one F5 logical slot:
`appworld:trace:2a163ab_1:step:13:line:33`, selected class
`procedure:046349e3bb380f803cd6bfd1545a10f26aa9cca1cf98bfa5db70b9b4772bf08f`,
with `41,134` prompt tokens against a `40,960` limit. The class has one member.
The final accounting is `225` logical, `224` executable, and one missing.
