# EXP-024R2 AppWorld Authentication Source Audit

- AppWorld 0.1.0 login creates `sub=<app>+<username>` and delegates token
  creation to fastapi-login.
- fastapi-login 1.9.3 uses HS256, `datetime.now(timezone.utc)`, and adds only
  `exp`; AppWorld supplies a random expiration in `[600, 1800)` seconds.
- Validation uses `jwt.decode` with the configured secret and algorithm.
- Source SHA256 values are
  `fad5c75442cef395fdd4e6af85ddfd179a66f5bec6f959ad966c3ca8d18a3c04`
  for AppWorld and
  `9e8fd11183fcce53824ce4e2029559c252e454ee6266496533efddaf5e702726`
  for fastapi-login.
- No `iat`, `nbf`, or `jti` generation was found.
- The fixed secret is redacted; only provenance hash
  `0917b13a9091915d54b6336f45909539cce452b3661b21f386418a257883b30a`
  is retained.
