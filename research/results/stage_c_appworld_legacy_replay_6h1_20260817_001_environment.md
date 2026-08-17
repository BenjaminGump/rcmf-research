# EXP-024R Environment Provenance

- Runtime: Python 3.11.15 at
  `/home/ubuntu/venvs/appworld-0.1.0-replay-py311-click817/bin/python`.
- AppWorld package/code/data/evaluation: `0.1.0/0.1.0/0.1.0`.
- Wheel: `appworld-0.1.0-4-py3-none-any.whl`, SHA256
  `128bdb088bd1c76b8ac763e831334f0843507e9e5a5e2e88ec4e2949e2e5d476`.
- APPWORLD_ROOT:
  `/lambda/nfs/rcmf-persist/appworld_legacy/0.1.0/root`.
- Root manifest: 17,995 files, 176,204,608 bytes, SHA256
  `b998bc041922cae81059321e5019dd40836fefc310921a9b797f71e47e574122`.
- Dependency wheels: 97 files, 45,955,460 bytes, SHA256
  `6c1b52d1d833c4c88986ea8334e64049a20e3d7222c081f565ea0b8651c9c664`.
- `pip freeze` SHA256:
  `9d2fcf8a8b2009eb142d84aba677839e8d7caa1dfc646ed5aaa41a2f05c74644`.
- Constraint lock: Click 8.1.7, SHA256
  `1d8786c8aeca6c9d67ed775b50687179049d72a3f6b826c6c4df1b696172afba`.

The existing AppWorld 0.2.0.dev0 environment was not modified. The package
wheel permits Python 3.10 in metadata but imports `typing.Self`; Python 3.11 is
therefore required in practice. Click 8.1.7 is a documented release-era CLI
compatibility pin, not a scientific change.
