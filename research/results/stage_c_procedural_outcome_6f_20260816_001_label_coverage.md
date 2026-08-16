# EXP-022 Procedural Label Coverage

The compiler produced 13,128 unique scoreable labels with tiers 0-4. Cell
counts reproduce EXP-020 exactly: `8,205/2,051/2,296/576`.

Tier-3/4 state coverage is A `60/74`, B `12/18`, C `41/74`, and D `9/18`.
The preregistered gate applies to B and requires at least 70%; observed coverage
is `66.6667%`, so it fails. B has six uncovered states spanning Python-only,
phone login/read, Simple Note read, and Spotify read actions. Full IDs and
maximum tiers are in the main report and `postrun_validation.json`.

Hard same-intent pair counts are A `27,029`, B `6,654`, C `2,457`, and D
`615`. No missing category was fabricated, no state was replaced, and the
fixed 148-transition panel was not expanded.
