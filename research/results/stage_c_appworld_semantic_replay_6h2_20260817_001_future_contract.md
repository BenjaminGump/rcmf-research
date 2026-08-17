# EXP-024R2 Future Replay-Prompt Contract

Generation remains blocked. After a separate identity-provenance repair and
review, the fixed sentinel must run twice in fresh AppWorld 0.1.0 worlds and
pass 13/13 under semantic v2 before full 45-state replay.

Any future Qwen generation must use actual current replay observations from
the same 0.1.0 world that executes the generated action. Historical JWTs must
not be inserted into a world containing newly issued tokens. Semantic v2
permits only audited `exp` timing differences and cannot excuse identity,
permission, response, database, or non-token differences.
