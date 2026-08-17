# EXP-025B Raw Content Versus Signature-Only Report

On the primary subset, signature-only C2 changes exact API/action signature/
semantic successor versus bare by `-0.0938/+0.0313/-0.0313`. Raw C1 changes
the same metrics by `+0.1250/+0.3438/+0.4063`.

C1-C2 is `+0.2188` exact API with 95% CI `[0.0882,0.3529]`, `+0.3125` action
signature with CI `[0.1333,0.4857]`, and `+0.4375` semantic successor with CI
`[0.2758,0.6000]`. C2 retains only `9.09%` of C1's signature gain over bare.
The raw-content-beyond-metadata gate passes.
