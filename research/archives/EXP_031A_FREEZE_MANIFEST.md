# EXP-031A Freeze Manifest

## Result

EXP-031A is restorable byte-for-byte from commit
`57d2a3479ff292dd8f89bdd0ea9f9417abc42a48` and its independent Lambda
archive. The freeze gate passed before any EXP-031B candidate work began.

## Git Archive

- Archive branch: `archive/exp031a-rcmf-joint-full-bank-57d2a347`
- Annotated tag: `exp031a-rcmf-joint-full-bank-verified-57d2a347`
- Tag object: `aa5a2c276257fd6d0a920a932963e76972875fe3`
- Peeled tag and branch commit: `57d2a3479ff292dd8f89bdd0ea9f9417abc42a48`
- Repository bundle SHA256: `5518db06ae689c907edbea2663b21f968d2df9fad8fb9bea7a37aa32b7e264ff`

Both refs were created without force, pushed to GitHub, and verified with
`git ls-remote`. The archival tag is an experiment tag, not a V5 release tag.

## Artifact Snapshot

The complete EXP-031A run root was copied with `rsync -a --checksum`; the
frozen EXP-025C selector state/calibration needed by the query encoder was
copied as an external dependency. No hard links or `--link-dest` were used.

- Archive: `/lambda/nfs/rcmf-persist/project/archives/exp031a_rcmf_joint_full_bank_57d2a347`
- Regular files: `11,497`
- Regular-file bytes: `3,163,808,663`
- Copied-root directories: `308`
- Symlinks: `0`
- Same-inode source/destination files: `0`
- Artifact manifest SHA256: `7fc45583b4beee9cce10f95bc6a824f8b50bae8ace90232e71b43095f5d1bf6a`
- Snapshot metadata SHA256: `e3c812e38dadd7ef26e3b012115115fddeb2819ecc0c35581df643fcc64eac60`
- Archive control manifest SHA256: `6e0ce928f9fc84d07b02ec0881387a695fc93a57333159b01e6c2ed17da79829`
- Archive identity SHA256: `ff3828a556d01589f7e925d78bce85791a81cfaaa628192bf6067e29e914559d`

Every file passed the final quiet `sha256sum -c` verification. The completed
archive has no writable entries.

## Required Identities

| Artifact | SHA256 |
|---|---|
| Epoch-2 checkpoint | `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1` |
| 401-memory heldout field | `63929027dfddea722419024949e492d9477a5fd61a45fe4dbf40a07a3936fa79` |
| 499-memory deployment field | `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e` |
| Source representation cache | `d9b4a1c6c8e428b12d5681625e2bbefd0c2def9b9836d407a901d0788f8eaedb` |
| Selector ensemble | `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f` |
| Attempt ledger | `cc1cd9ec3a88d4856c2675c8d7a1c44d21197f8cafb92e6c77f5a2fc97ad8856` |
| Git-safe audit index | `6075662dcd3897f3147d26d7067e30f4a05d1ce7b478f9a5fc600af08b0d1109` |

## Restoration Smoke

A fresh checkout was cloned from the bundle and detached at the archival tag.
The smoke loaded the archived checkpoint, 499-memory field, and three selector
checkpoints. It verified field shapes `A=[960,8,256]`, `B=[8,256]`, writer
parameters `8,949,760`, and reader parameters `17,860,608`.

Using the archived `0d01c76_1` smoke prompt:

- D0 reproduced all `112` generated token IDs exactly;
- D1 reproduced all `106` generated token IDs exactly;
- Qwen remained frozen and gradient-free.

The smoke result SHA256 is
`f7aa0eab85a727f87cd0584a44d4ad00d7eadf22d7bbf1eceb22b53c1a8748de`.
The exact verifier is archived at
`research/archives/exp031a_freeze_tools/verify_exp031a_archive_restore.py`.

## Provenance Gate

The accompanying scratch audit classified all 63 scripts and verified all 176
Git bundles. All 68 formal EXP-031A attempt rows call committed source, and no
formal artifact references a scratch basename. The decision is:

`exp031a_formal_execution_provenance_sufficient`

A clean rerun of EXP-031A is not required. The original branch, artifacts,
result label, checkpoint, deployment field, ledger, and audit index remain
unchanged.

## Implementation Notes

Three preservation-only preflight issues were recorded without changing
science: a missing nested selector-copy destination, a verifier exclusion bug,
and one extra character in a temporary selector-hash assertion. Each stopped
before accepting a result and was corrected before the complete verification.
