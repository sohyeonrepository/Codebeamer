# Selective Wizard / Xray DC integration verification

Base: `feat/import-wizard-scoped-mapping` at `1ce9c5e`.
Review branch: `review/wizard-xray-dc-integration`. No PR merged.
PR source snapshots: #1 `01b1710`, #2 `16d45b4`; #3 `4d36bda`.

## Verified scope

PR #3: 5 focused tests passed; full suite 277 run, 271 passed, 6 GUI skips.
Integrated suite: 280 run, 274 passed, 6 GUI skips. These are local unit/Mock
checks, not Xray DC or Codebeamer 3.0.0.7 compatibility certification.

Selected field semantics, Choice/User identity compatibility, Checklist guard,
source inspection code and tests. Kept the existing field-definition creation
function instead of importing the unrelated referenceId contract changes.
No changes to import_wizard.py, RUN_WINDOWS.bat, config.local.json or .migration.
No v0.4 source, binary archive, alternative launcher or Windows live-test launcher
was added. Test Plan target policy remains in existing Tracker mapping.

## Source collection implementation

`clients/xray_dc_contract.py`: existing GET inspector plus bounded collector.
`clients/xray_client.py`: optional issue enrichment and Run step/evidence collection;
Run identity cross-check, duplicate Run rejection and malformed detail rejection.
`migration/full_xray.py`: enabled issue enrichment runs before target writes.
Raw pages are archived before validation, and merged evidence retains original IDs.
Existing membership adapters may read `_xray_dc.<operation>.items`; their normal
missing-target/type validation still applies after all source items are collected.

Configuration is opt-in under `jira.source_reads` (no shipped config rewritten).
Example **synthetic contract**, only enable after inspecting the installed API:

```json
{"jira":{"api_family":"server-dc","source_reads":{"plan":{"test_plan_tests":{
  "verified":true,"items_key":"values","total_key":"total","identity_key":"key",
  "offset_param":"startAt","limit_param":"limit","page_size":100,"max_pages":1000
}},"run":{"test_run_steps":{
  "verified":true,"unpaginated":true,"identity_key":"id"
}}}}}
```

This does NOT assert that Xray returns the example envelope. Bare-array completion
requires an explicitly verified unpaginated contract. Unknown pagination is not
completed implicitly. Empty collection with total=0 is valid; empty intermediate
pages, duplicate identity, changing totals, overflow and page bounds are errors.
Use Jira's configured PAT/HTTP client for DC, not Cloud client credentials or
GraphQL. `verified` is a local operator assertion, not automatic API certification.

## Coverage and remaining contracts

| Source | Current handling | Remaining verification |
|---|---|---|
| Test basic fields / type / Preconditions | Original Jira bundle retained; configured field mappings | Actual custom field IDs and dependency policy; no guessed Preconditions endpoint |
| Test steps | Optional verified `test_steps` collection | Installed response envelope and CB step write model |
| Test Set / Plan / Execution membership | Optional DC collections and existing explicit full-Xray relation adapters | Complete selection and target Tracker references |
| Execution environment / Plan | Original Jira fields and source collection retained | Actual environment fields and relation adapter |
| Run ID, Test/Execution, status, users, dates | Full Run response retained, identity cross-check | Configured target value converters and writable CB models |
| Run steps, actual result, defects, evidence | Original nested data and optional separate collections archived | Evidence binary endpoints and mapping separate collected data into native CB results/attachments/defect relations |
| History / Activity | Existing explicitly configured collectors / Legacy evidence | DC endpoint availability, complete paging; no inferred native CB History write API |

Source collection is not a declaration of complete target migration. Separate
Run collections are archived evidence; merely collecting them does not automatically
write native CB step results or download attachment binaries. Existing verified
writers remain unchanged and require their existing contracts. Full-Xray issue
reads are opt-in; source inspection does not silently enable a new Wizard mode.

## Actual-server acceptance still required

Customer Xray version and GET envelopes/pagination; Jira PAT permissions; Windows
Tk interaction; Codebeamer 3.0.0.7 field/option/item/test-result/reference contracts;
attachment hash checks and source/target values/counts. No real server writes were
performed. No history author/time restoration is claimed.
