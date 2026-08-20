# EXP-025D-Fast Incremental Field Validation

Production operations `add_fast`, `remove_fast`, `replace_fast`, and
`remove_parent_fast` update stored deltas through direct addition/subtraction.
They do not call `audit_rebuild()` or enumerate unrelated records.

CPU validation passed for:

- fast state versus an explicit weighted sum;
- fast state versus full sorted `audit_rebuild()`;
- add/remove/replace restoration;
- parent removal and restoration;
- absence of unrelated-record iteration;
- parent-normalized `rho=1/T_i` and standalone `rho=1` defaults;
- fixed read tensor shapes independent of memory count.

The deterministic test shape was `V0=[5,7]`, `T=[5,3,7]`. The toy adapter
smoke confirms these operations do not import AppWorld. This validates the
incremental algebra contract only; no full bank was constructed or trained.

