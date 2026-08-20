# ICLR 2027 Critical Path

This schedule is deadline-driven from 2026-08-19 through the ICLR 2027 paper
deadline on 2026-09-25 AOE. The abstract deadline is 2026-09-18 AOE.

| Dates (2026) | Deliverable | Go/no-go decision |
| --- | --- | --- |
| Aug 19-20 | EXP-025D-Fast implementation, CPU contracts, bounded manifest, and runtime preflight | Aug 20: launch only if expected required H100 time is at most 12 hours |
| Aug 20-21 | Clean decoder repair, canonical pair latents, and tensor-space program training | Aug 21: stop if pair effects are nonidentifiable or PairMLP fails |
| Aug 21-22 | B/C/D/E teacher-forced validation and conditional one-step audit | Aug 22: require the fast pilot gate before full-bank integration work |
| Aug 23-27 | Separately reviewed full-bank integration, add/remove validation, and fixed-cost read | Aug 27: require a compiled-program end-to-end smoke |
| Aug 28-Sep 03 | AppWorld end-to-end evaluation and essential controls | Sep 03: require a behaviorally positive result or narrow the paper claim |
| Sep 04-10 | Replication, ablations required for the central claim, figures, and tables | Sep 10: freeze the experimental scope |
| Sep 11-17 | Paper writing, related work, limitations, artifact audit, and internal review | Sep 17: freeze abstract and headline numbers |
| Sep 18 AOE | Abstract submission deadline | Submit the abstract; no new headline claim afterward |
| Sep 19-24 | Final paper revision, reproducibility appendix, and release checks | Sep 24: final go/no-go and upload rehearsal |
| Sep 25 AOE | Paper submission deadline | Submit final paper and supplement |

No-go rules: do not launch the old 201.72-H100-hour design; do not begin
full-bank integration if EXP-025D-Fast fails; and do not let nonessential
architecture sweeps displace the Sep 10 scope freeze.
