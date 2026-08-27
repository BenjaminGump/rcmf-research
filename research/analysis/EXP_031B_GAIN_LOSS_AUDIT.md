# EXP-031B Gain/Loss Mechanism Audit

- Run UUID: `rcmf_benefit_preserving_calibration_9b_20260827_001`
- Source EXP-031A: `57d2a3479ff292dd8f89bdd0ea9f9417abc42a48`
- Audited tasks: `14`
- Candidate outcomes inspected: `false`
- Runtime retrieval used: `false`

## Verified Task Mechanisms

| Task | Group | D0/D1/D2 | Critical D1 step | Mechanism |
|---|---:|---:|---:|---|
| `0d01c76_1` | loss | pass/fail/fail | 17 | exact_mapping |
| `0d01c76_2` | loss | pass/fail/pass | 24 | procedural_drift |
| `0d01c76_3` | gain | fail/pass/fail | 15 | exact_mapping |
| `29a7b7e_3` | loss | pass/fail/fail | 11 | argument_construction |
| `325d6ec_1` | loss | pass/fail/pass | 8 | stopping_and_direction |
| `325d6ec_2` | gain | fail/pass/fail | 7 | state_tracking |
| `325d6ec_3` | gain | fail/pass/fail | 9 | state_tracking |
| `634f342_1` | gain | fail/pass/fail | 25 | exact_set_bookkeeping |
| `634f342_2` | gain | fail/pass/fail | 25 | exact_set_bookkeeping |
| `634f342_3` | gain | fail/pass/fail | 19 | exact_set_bookkeeping |
| `8749218_1` | loss | pass/fail/pass | 7 | exact_set_bookkeeping |
| `8749218_2` | retained | pass/pass/pass | 8 | exact_set_bookkeeping |
| `8749218_3` | retained | pass/pass/fail | 10 | exact_set_bookkeeping |
| `d6ac34d_2` | loss | pass/fail/fail | 11 | schema_preservation |

## Hypothesis Audit

- **A_cross_app_mapping: SUPPORTED_WITH_ATTRIBUTION_LIMIT** 0d01c76_3 D1 passed the complete exact note map while D0 did not; credential/data-type separation is consistent with the trace but cannot be isolated from the whole-bank intervention.
- **B_spotify_state_machine: SUPPORTED** Both 325d6ec gains pass only when the live queue cursor, membership predicate, direction action, updated state, and stop predicate remain coherent.
- **C_exact_set_transaction: SUPPORTED** All three 634f342 D1 runs pass evaluator-exact source-absence and destination-set invariants; their D0 controls fail through search drift, duplicate/retry behavior, or incomplete final sets.
- **D_harm_taxonomy: SUPPORTED** The six losses cover procedural drift, direction/stopping failure, argument/path construction, exact-set mismatch, and schema/content preservation failure.
- **E_signed_contribution_ambiguity: SUPPORTED** Mixed dominant contribution signs occur in 14/14 audited D1 trajectories (0d01c76_1, 0d01c76_2, 0d01c76_3, 29a7b7e_3, 325d6ec_1, 325d6ec_2, 325d6ec_3, 634f342_1, 634f342_2, 634f342_3, 8749218_1, 8749218_2, 8749218_3, d6ac34d_2); sign alone is therefore not a harm gate.

## Per-Task Evidence

### `0d01c76_1` (loss)

- **VERIFIED D0:** Created the evaluator-required exact title/content note set.
- **VERIFIED D1:** Completed after a second import/search loop, but the exact title/content set differed.
- First text divergence: step `1`; locked D0/D1 behavioral steps: `11`/`17`; critical D1 step: `17`.
- Evaluator invariant: changed Simple Note records equal the complete title-to-content map and no unrelated model changes occur
- Critical raw pre-RMS field RMS/norm: `29.6512`/`1341.86`; slot reconstruction max error: `2.6226e-06`.
- Reader delta-norm means: L14=1.82391, L21=3.50749, L28=41.8941, L7=4.28617.
- Reader attention-entropy means: L14=1.99523, L21=1.88899, L28=1.10186, L7=1.8683.
- Dominant-sign changes: `10`; negative present: `True`; positive present: `True`.
- Offline top contributions (descriptive only):
  - weight `+0.344547`; key task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc; payload task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc.
  - weight `+0.307753`; key task 287e338_3 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_3 step 4: api_documentation via api_docs.show_api_doc.
  - weight `+0.288992`; key task 287e338_2 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_2 step 4: api_documentation via api_docs.show_api_doc.
- **INFERENCE:** The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.
- **UNVERIFIED:** Individual offline top-contribution records are descriptive and were never runtime inputs.
- Exact Git-safe action/observation text and corresponding raw SHA256 identities are in the machine-readable JSON.

### `0d01c76_2` (loss)

- **VERIFIED D0:** Imported the complete exact title/content note set and completed.
- **VERIFIED D1:** Entered a repeated file-existence probe and exhausted the interaction budget without import completion.
- First text divergence: step `1`; locked D0/D1 behavioral steps: `7`/`24`; critical D1 step: `24`.
- Evaluator invariant: changed Simple Note records equal the complete title-to-content map and no unrelated model changes occur
- Critical raw pre-RMS field RMS/norm: `9.2201`/`417.254`; slot reconstruction max error: `2.81334e-05`.
- Reader delta-norm means: L14=7.13568, L21=6.46484, L28=62.4311, L7=8.00473.
- Reader attention-entropy means: L14=1.99378, L21=1.91105, L28=1.31647, L7=1.96619.
- Dominant-sign changes: `8`; negative present: `True`; positive present: `True`.
- Offline top contributions (descriptive only):
  - weight `-0.286426`; key task cf6abd2_3 step 7: api_read_or_login via simple_note.search_notes; payload task cf6abd2_3 step 7: api_read_or_login via simple_note.search_notes.
  - weight `-0.287051`; key task cf6abd2_1 step 7: api_read_or_login via simple_note.search_notes; payload task cf6abd2_1 step 7: api_read_or_login via simple_note.search_notes.
  - weight `+0.227993`; key task 287e338_1 step 2: api_documentation via api_docs.show_api_doc; payload task 287e338_1 step 2: api_documentation via api_docs.show_api_doc.
- **INFERENCE:** The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.
- **UNVERIFIED:** Individual offline top-contribution records are descriptive and were never runtime inputs.
- Exact Git-safe action/observation text and corresponding raw SHA256 identities are in the machine-readable JSON.

### `0d01c76_3` (gain)

- **VERIFIED D0:** Attempted the import but failed the exact title/content map invariant.
- **VERIFIED D1:** Recovered authentication and preserved filename-to-title and file-content-to-note-content mappings for the complete set.
- First text divergence: step `6`; locked D0/D1 behavioral steps: `11`/`15`; critical D1 step: `15`.
- Evaluator invariant: changed Simple Note records equal the complete title-to-content map and no unrelated model changes occur
- Critical raw pre-RMS field RMS/norm: `24.8936`/`1126.55`; slot reconstruction max error: `2.38419e-06`.
- Reader delta-norm means: L14=1.94965, L21=3.90992, L28=41.8078, L7=4.53971.
- Reader attention-entropy means: L14=1.9877, L21=1.87512, L28=1.13114, L7=1.85647.
- Dominant-sign changes: `8`; negative present: `True`; positive present: `True`.
- Offline top contributions (descriptive only):
  - weight `+0.279417`; key task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc; payload task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc.
  - weight `+0.262242`; key task 287e338_3 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_3 step 4: api_documentation via api_docs.show_api_doc.
  - weight `+0.253089`; key task 287e338_2 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_2 step 4: api_documentation via api_docs.show_api_doc.
- **INFERENCE:** The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.
- **UNVERIFIED:** Individual offline top-contribution records are descriptive and were never runtime inputs.
- Exact Git-safe action/observation text and corresponding raw SHA256 identities are in the machine-readable JSON.

### `29a7b7e_3` (loss)

- **VERIFIED D0:** Moved all source files to the exact destination map while preserving file contents.
- **VERIFIED D1:** Executed a different move transformation and failed the evaluator's exact path/content map.
- First text divergence: step `1`; locked D0/D1 behavioral steps: `11`/`11`; critical D1 step: `11`.
- Evaluator invariant: the complete start-path to end-path mapping is realized with file contents preserved and no unrelated model changes
- Critical raw pre-RMS field RMS/norm: `39.5586`/`1790.22`; slot reconstruction max error: `2.6226e-06`.
- Reader delta-norm means: L14=2.61873, L21=5.53999, L28=61.5271, L7=6.24538.
- Reader attention-entropy means: L14=1.98704, L21=1.85137, L28=1.10723, L7=1.86559.
- Dominant-sign changes: `6`; negative present: `True`; positive present: `True`.
- Offline top contributions (descriptive only):
  - weight `+0.338871`; key task 287e338_1 step 3: api_documentation via api_docs.show_api_doc; payload task 287e338_1 step 3: api_documentation via api_docs.show_api_doc.
  - weight `+0.3367`; key task 287e338_3 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_3 step 4: api_documentation via api_docs.show_api_doc.
  - weight `+0.334755`; key task 287e338_2 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_2 step 4: api_documentation via api_docs.show_api_doc.
- **INFERENCE:** The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.
- **UNVERIFIED:** Individual offline top-contribution records are descriptive and were never runtime inputs.
- Exact Git-safe action/observation text and corresponding raw SHA256 identities are in the machine-readable JSON.

### `325d6ec_1` (loss)

- **VERIFIED D0:** Ended at the exact target cursor position.
- **VERIFIED D1:** Its direction/stopping sequence did not end at the evaluator target cursor.
- First text divergence: step `1`; locked D0/D1 behavioral steps: `7`/`8`; critical D1 step: `8`.
- Evaluator invariant: Spotify cursor_position equals the exact target song cursor and no unrelated model/field changes occur
- Critical raw pre-RMS field RMS/norm: `14.9523`/`676.665`; slot reconstruction max error: `1.09673e-05`.
- Reader delta-norm means: L14=2.04837, L21=3.1972, L28=35.7409, L7=4.24989.
- Reader attention-entropy means: L14=1.98186, L21=1.89356, L28=1.11068, L7=1.87641.
- Dominant-sign changes: `2`; negative present: `True`; positive present: `True`.
- Offline top contributions (descriptive only):
  - weight `+0.262404`; key task 287e338_3 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_3 step 4: api_documentation via api_docs.show_api_doc.
  - weight `+0.253989`; key task 287e338_2 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_2 step 4: api_documentation via api_docs.show_api_doc.
  - weight `+0.257729`; key task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc; payload task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc.
- **INFERENCE:** The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.
- **UNVERIFIED:** Individual offline top-contribution records are descriptive and were never runtime inputs.
- Exact Git-safe action/observation text and corresponding raw SHA256 identities are in the machine-readable JSON.

### `325d6ec_2` (gain)

- **VERIFIED D0:** Drifted into playlist search and play-music behavior rather than advancing the live queue cursor to the downloaded-song target.
- **VERIFIED D1:** Compared the live queue cursor against downloaded-song membership and stopped at the exact target cursor.
- First text divergence: step `1`; locked D0/D1 behavioral steps: `13`/`7`; critical D1 step: `7`.
- Evaluator invariant: Spotify cursor_position equals the exact target song cursor and no unrelated model/field changes occur
- Critical raw pre-RMS field RMS/norm: `37.9528`/`1717.55`; slot reconstruction max error: `2.38419e-06`.
- Reader delta-norm means: L14=2.11847, L21=4.29589, L28=44.0953, L7=4.68384.
- Reader attention-entropy means: L14=1.96489, L21=1.83273, L28=1.11756, L7=1.86922.
- Dominant-sign changes: `2`; negative present: `True`; positive present: `True`.
- Offline top contributions (descriptive only):
  - weight `+0.34282`; key task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc; payload task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc.
  - weight `+0.308684`; key task e7a10f8_2 step 2: api_documentation via api_docs.show_api_doc; payload task e7a10f8_2 step 2: api_documentation via api_docs.show_api_doc.
  - weight `+0.292151`; key task 287e338_3 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_3 step 4: api_documentation via api_docs.show_api_doc.
- **INFERENCE:** The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.
- **UNVERIFIED:** Individual offline top-contribution records are descriptive and were never runtime inputs.
- Exact Git-safe action/observation text and corresponding raw SHA256 identities are in the machine-readable JSON.

### `325d6ec_3` (gain)

- **VERIFIED D0:** Advanced once and stopped without preserving the required updated-state membership loop.
- **VERIFIED D1:** Maintained the queue-position, liked-membership, next-action, updated-state, stop-predicate loop to the exact target cursor.
- First text divergence: step `1`; locked D0/D1 behavioral steps: `9`/`9`; critical D1 step: `9`.
- Evaluator invariant: Spotify cursor_position equals the exact target song cursor and no unrelated model/field changes occur
- Critical raw pre-RMS field RMS/norm: `46.8598`/`2120.63`; slot reconstruction max error: `2.38419e-06`.
- Reader delta-norm means: L14=1.83093, L21=4.04671, L28=37.7453, L7=4.12082.
- Reader attention-entropy means: L14=1.97353, L21=1.82754, L28=1.16805, L7=1.87399.
- Dominant-sign changes: `2`; negative present: `True`; positive present: `True`.
- Offline top contributions (descriptive only):
  - weight `+0.331663`; key task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc; payload task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc.
  - weight `+0.299923`; key task e7a10f8_2 step 2: api_documentation via api_docs.show_api_doc; payload task e7a10f8_2 step 2: api_documentation via api_docs.show_api_doc.
  - weight `+0.286933`; key task 287e338_1 step 2: api_documentation via api_docs.show_api_doc; payload task 287e338_1 step 2: api_documentation via api_docs.show_api_doc.
- **INFERENCE:** The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.
- **UNVERIFIED:** Individual offline top-contribution records are descriptive and were never runtime inputs.
- Exact Git-safe action/observation text and corresponding raw SHA256 identities are in the machine-readable JSON.

### `634f342_1` (gain)

- **VERIFIED D0:** Spent the remaining budget on repeated song search and never completed the exact-set transaction.
- **VERIFIED D1:** Enumerated source playlists, removed every required occurrence, created the destination, populated the exact song set, and verified it.
- First text divergence: step `2`; locked D0/D1 behavioral steps: `16`/`25`; critical D1 step: `25`.
- Evaluator invariant: all listed source occurrences are absent and the new destination playlist contains exactly the required unique song set
- Critical raw pre-RMS field RMS/norm: `14.2112`/`643.126`; slot reconstruction max error: `1.57356e-05`.
- Reader delta-norm means: L14=4.0585, L21=4.69429, L28=54.5721, L7=6.42836.
- Reader attention-entropy means: L14=1.98652, L21=1.90428, L28=1.11156, L7=1.88702.
- Dominant-sign changes: `8`; negative present: `True`; positive present: `True`.
- Offline top contributions (descriptive only):
  - weight `-0.261646`; key task cf6abd2_1 step 7: api_read_or_login via simple_note.search_notes; payload task cf6abd2_1 step 7: api_read_or_login via simple_note.search_notes.
  - weight `-0.254909`; key task cf6abd2_3 step 7: api_read_or_login via simple_note.search_notes; payload task cf6abd2_3 step 7: api_read_or_login via simple_note.search_notes.
  - weight `+0.249347`; key task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc; payload task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc.
- **INFERENCE:** The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.
- **UNVERIFIED:** Individual offline top-contribution records are descriptive and were never runtime inputs.
- Exact Git-safe action/observation text and corresponding raw SHA256 identities are in the machine-readable JSON.

### `634f342_2` (gain)

- **VERIFIED D0:** Drifted through repeated destination creation/search and failed the exact source-absence/destination-set invariant.
- **VERIFIED D1:** Completed the required removals and exact destination population, then verified the resulting playlists.
- First text divergence: step `4`; locked D0/D1 behavioral steps: `17`/`25`; critical D1 step: `25`.
- Evaluator invariant: all listed source occurrences are absent and the new destination playlist contains exactly the required unique song set
- Critical raw pre-RMS field RMS/norm: `63.4563`/`2871.7`; slot reconstruction max error: `2.38419e-06`.
- Reader delta-norm means: L14=2.66967, L21=5.77731, L28=63.2063, L7=6.3809.
- Reader attention-entropy means: L14=1.97869, L21=1.81932, L28=1.02368, L7=1.84691.
- Dominant-sign changes: `10`; negative present: `True`; positive present: `True`.
- Offline top contributions (descriptive only):
  - weight `+0.447312`; key task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc; payload task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc.
  - weight `+0.389673`; key task 287e338_3 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_3 step 4: api_documentation via api_docs.show_api_doc.
  - weight `+0.392501`; key task e7a10f8_2 step 2: api_documentation via api_docs.show_api_doc; payload task e7a10f8_2 step 2: api_documentation via api_docs.show_api_doc.
- **INFERENCE:** The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.
- **UNVERIFIED:** Individual offline top-contribution records are descriptive and were never runtime inputs.
- Exact Git-safe action/observation text and corresponding raw SHA256 identities are in the machine-readable JSON.

### `634f342_3` (gain)

- **VERIFIED D0:** Performed a partial migration but left a final playlist/song-set mismatch.
- **VERIFIED D1:** Completed an evaluator-exact source-removal and destination-set transaction.
- First text divergence: step `4`; locked D0/D1 behavioral steps: `18`/`19`; critical D1 step: `19`.
- Evaluator invariant: all listed source occurrences are absent and the new destination playlist contains exactly the required unique song set
- Critical raw pre-RMS field RMS/norm: `23.3087`/`1054.83`; slot reconstruction max error: `2.6226e-06`.
- Reader delta-norm means: L14=2.91261, L21=5.67939, L28=65.7586, L7=6.81175.
- Reader attention-entropy means: L14=1.98078, L21=1.87746, L28=1.01347, L7=1.83002.
- Dominant-sign changes: `8`; negative present: `True`; positive present: `True`.
- Offline top contributions (descriptive only):
  - weight `+0.29621`; key task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc; payload task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc.
  - weight `+0.27147`; key task 287e338_3 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_3 step 4: api_documentation via api_docs.show_api_doc.
  - weight `+0.262355`; key task 287e338_2 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_2 step 4: api_documentation via api_docs.show_api_doc.
- **INFERENCE:** The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.
- **UNVERIFIED:** Individual offline top-contribution records are descriptive and were never runtime inputs.
- Exact Git-safe action/observation text and corresponding raw SHA256 identities are in the machine-readable JSON.

### `8749218_1` (loss)

- **VERIFIED D0:** Reset the queue to the exact recommendation set, shuffled it, and played it.
- **VERIFIED D1:** Used an extra recommendation retrieval path and produced a queue-set mismatch despite reset/shuffle/play calls.
- First text divergence: step `2`; locked D0/D1 behavioral steps: `6`/`7`; critical D1 step: `7`.
- Evaluator invariant: the queue contains exactly all recommendation IDs, is shuffled relative to canonical order, and playback is active
- Critical raw pre-RMS field RMS/norm: `17.2167`/`779.139`; slot reconstruction max error: `3.45707e-06`.
- Reader delta-norm means: L14=4.63272, L21=7.69342, L28=90.6762, L7=7.39861.
- Reader attention-entropy means: L14=1.95493, L21=1.81258, L28=1.17871, L7=1.89217.
- Dominant-sign changes: `4`; negative present: `True`; positive present: `True`.
- Offline top contributions (descriptive only):
  - weight `+0.258104`; key task 287e338_3 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_3 step 4: api_documentation via api_docs.show_api_doc.
  - weight `+0.256979`; key task 287e338_2 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_2 step 4: api_documentation via api_docs.show_api_doc.
  - weight `-0.267406`; key task cf6abd2_1 step 7: api_read_or_login via simple_note.search_notes; payload task cf6abd2_1 step 7: api_read_or_login via simple_note.search_notes.
- **INFERENCE:** The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.
- **UNVERIFIED:** Individual offline top-contribution records are descriptive and were never runtime inputs.
- Exact Git-safe action/observation text and corresponding raw SHA256 identities are in the machine-readable JSON.

### `8749218_2` (retained)

- **VERIFIED D0:** Passed the exact recommendation-set, shuffled-queue, and playing-state invariant.
- **VERIFIED D1:** Preserved the same exact final-state invariant under the correct field.
- First text divergence: step `2`; locked D0/D1 behavioral steps: `8`/`8`; critical D1 step: `8`.
- Evaluator invariant: the queue contains exactly all recommendation IDs, is shuffled relative to canonical order, and playback is active
- Critical raw pre-RMS field RMS/norm: `44.6727`/`2021.65`; slot reconstruction max error: `2.6226e-06`.
- Reader delta-norm means: L14=3.2379, L21=7.32402, L28=77.2918, L7=7.5744.
- Reader attention-entropy means: L14=1.97025, L21=1.79845, L28=0.986161, L7=1.85754.
- Dominant-sign changes: `4`; negative present: `True`; positive present: `True`.
- Offline top contributions (descriptive only):
  - weight `+0.41088`; key task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc; payload task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc.
  - weight `+0.382974`; key task 287e338_3 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_3 step 4: api_documentation via api_docs.show_api_doc.
  - weight `+0.372678`; key task 287e338_2 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_2 step 4: api_documentation via api_docs.show_api_doc.
- **INFERENCE:** The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.
- **UNVERIFIED:** Individual offline top-contribution records are descriptive and were never runtime inputs.
- Exact Git-safe action/observation text and corresponding raw SHA256 identities are in the machine-readable JSON.

### `8749218_3` (retained)

- **VERIFIED D0:** Passed the exact recommendation-set, shuffled-queue, and playing-state invariant.
- **VERIFIED D1:** Preserved the same exact final-state invariant under the correct field.
- First text divergence: step `2`; locked D0/D1 behavioral steps: `8`/`10`; critical D1 step: `10`.
- Evaluator invariant: the queue contains exactly all recommendation IDs, is shuffled relative to canonical order, and playback is active
- Critical raw pre-RMS field RMS/norm: `43.3577`/`1962.14`; slot reconstruction max error: `2.38419e-06`.
- Reader delta-norm means: L14=3.26955, L21=7.11665, L28=78.0143, L7=7.71769.
- Reader attention-entropy means: L14=1.96834, L21=1.81534, L28=0.927175, L7=1.84596.
- Dominant-sign changes: `4`; negative present: `True`; positive present: `True`.
- Offline top contributions (descriptive only):
  - weight `+0.384952`; key task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc; payload task 07b42fd_3 step 5: api_documentation via api_docs.show_api_doc.
  - weight `+0.345333`; key task 287e338_3 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_3 step 4: api_documentation via api_docs.show_api_doc.
  - weight `+0.334168`; key task 287e338_2 step 4: api_documentation via api_docs.show_api_doc; payload task 287e338_2 step 4: api_documentation via api_docs.show_api_doc.
- **INFERENCE:** The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.
- **UNVERIFIED:** Individual offline top-contribution records are descriptive and were never runtime inputs.
- Exact Git-safe action/observation text and corresponding raw SHA256 identities are in the machine-readable JSON.

### `d6ac34d_2` (loss)

- **VERIFIED D0:** Created the exact habit-log title, header, schema, values, pin state, and tags.
- **VERIFIED D1:** Created a note, but its exact evaluator-normalized habit-log content differed.
- First text divergence: step `3`; locked D0/D1 behavioral steps: `9`/`11`; critical D1 step: `11`.
- Evaluator invariant: one exact normalized habit-log note matches title, header, ten Boolean fields, pin state, and tags
- Critical raw pre-RMS field RMS/norm: `9.57406`/`433.273`; slot reconstruction max error: `2.18153e-05`.
- Reader delta-norm means: L14=7.9399, L21=6.26776, L28=55.6041, L7=7.72185.
- Reader attention-entropy means: L14=2.0017, L21=1.93391, L28=1.25099, L7=1.94437.
- Dominant-sign changes: `3`; negative present: `True`; positive present: `True`.
- Offline top contributions (descriptive only):
  - weight `+0.203732`; key task 07b42fd_1 step 1: api_documentation via api_docs.show_api_doc; payload task 07b42fd_1 step 1: api_documentation via api_docs.show_api_doc.
  - weight `-0.249455`; key task e7a10f8_2 step 3: api_read_or_login via spotify.show_playlist_library; payload task e7a10f8_2 step 3: api_read_or_login via spotify.show_playlist_library.
  - weight `+0.191254`; key task b7a9ee9_1 step 1: api_documentation via api_docs.show_api_doc; payload task b7a9ee9_1 step 1: api_documentation via api_docs.show_api_doc.
- **INFERENCE:** The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.
- **UNVERIFIED:** Individual offline top-contribution records are descriptive and were never runtime inputs.
- Exact Git-safe action/observation text and corresponding raw SHA256 identities are in the machine-readable JSON.


## Evidence Boundaries

**VERIFIED:** Outcomes, emitted redacted code/observations, evaluator hashes and invariants, field magnitudes, reader statistics, and signed contribution sequences come from immutable EXP-031A rows and official AppWorld 0.1.0 task snapshots.

**INFERENCE:** Mechanism labels identify the earliest trajectory decision that best explains the final-state difference; whole-bank interventions prevent attribution to one memory record.

**UNVERIFIED:** No candidate calibration has been evaluated, and no top-contribution row is claimed individually causal.

## Exact Replays

Fourteen replay cases are locked in the machine-readable JSON. Each points to the immutable raw Lambda row, exact renderer/prompt hashes, replay-prefix hashes, task snapshot hash, field query/slot tensor, and fresh-world reconstruction rule. Git-safe prompt components are redacted; exact raw material remains on Lambda.
