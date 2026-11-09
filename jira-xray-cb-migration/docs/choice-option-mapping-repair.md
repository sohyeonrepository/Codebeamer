# Existing Choice option mapping repair

Base: `test/cb-item-post-minimal-20261106` at `7163589`.

- Compact option editor restores both existing `{id: ...}` mappings and legacy target-name strings against server options. A missing saved ID stays blank for review; it does not turn into an automatic request to create the Jira label.
- Confirmed mappings save actual Option IDs. Compact Import preflight and Item payload conversion both accept these IDs and legacy names, and verify against current server options. Multi Choice retains multiple values; single Choice rejects multiple values.
- Existing options require no creation POST. Missing options retain the separate explicit creation approval. Save callback errors appear through the existing error/log handler.
- Dedicated option POST readback retries GET up to three times, never POST. Unresolved responses leave the pending journal intact and record response shape, ID match and option-match count, without raw source values or credentials. Existing pending-request reconciliation remains in place; no state reset or forced resend was added.

Validation:
- Focused tests: 26 passed, including 7 new regression tests.
- Full suite before repair: 452 run, 15 failures, 25 errors, 6 skips.
- Full suite with the repair: 459 run, the same 15 failures and 25 errors, 6 skips. Failure/error test names match the baseline; these pre-existing failures remain unresolved. The subsequent compact preflight ID-format adjustment was syntax checked and the 26 focused tests rerun successfully.
- Windows GUI and actual Codebeamer server execution were not tested. Readback recovery tests use mocked HTTP responses. The user's exact server response causing the original error is still unknown.

No launcher, customer configuration, migration-state data or server data was changed. No PR merged.
