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

## Existing member-field comparison follow-up

`mapping_helper._configuration_drift()` now compares the known `choiceOptionSetting.membersTypeDataSourceTypes` enum list independent of ordering, retaining duplicates/unknown values as strict comparisons. The supplied 2.2.1.4 and 3.3.0.0 OpenAPI files describe this property as a unique list of USERS/GROUPS/ROLES. This is not a claim of live 3.0.0.7 compatibility or proof that the customer screenshot was caused only by ordering.

The latest original GET payload remains the POST baseline; comparison normalization never changes it. Post-creation preservation checks apply the same order-only rule. Member additions/removals, permissions and ordinary option order changes still block. Remaining pre-POST drift is saved to a sibling `.drift.json` containing property/shape diagnostics, without raw customer values.

Five new tests cover the reported existing field IDs 1009/1011/1012, member/permission changes, ordinary option-order protection, creation/readback with alternating member order, and real drift blocking all POST requests. Focused regression suite: 65 passed. Real server and Windows GUI execution remain unverified.
