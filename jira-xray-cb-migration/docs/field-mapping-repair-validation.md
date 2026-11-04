# Existing Wizard field mapping repair

Branch: `review/wizard-catalog-integration`

## Implemented corrections

- `field_analysis()` preserves Issue Type metadata (schema, options, required) when overlaying Field Configuration visibility. Configured fields remain eligible even when null. Observed nonempty fields and core system fields are selected; unproven empty fields remain reviewable but default deselected. The existing selected-only filter defaults on; previous explicit selections persist.
- `build_spec(existing=True)` only requires metadata relevant to the existing field. Text/Date/User mapping does not require Choice or Reference metadata. Real IDs, multiplicity and required metadata remain checked.
- `proposals()` and `manual_mapping()` validate actual source values against actual target Choice IDs. Unused source options do not block an existing mapping; creation plans still retain collected options. Saved invalid mappings report an error on reanalysis.
- User matching uses source usernames and target user options, never display-name similarity. The existing mapping dialog preserves saved identifiers and allows explicit CB user IDs when no user options are available. Manually supplied IDs still require deployment validation.
- `creation_profiles()` separates field-definition contracts from value bindings, checks existing Configuration/dedicated creation contracts, and scopes derived templates to their source Tracker. `analyze()` attaches verified Date (and other supported) profiles directly to Import plans when an OpenAPI definition is already loaded; it does not start Swagger discovery for ordinary analysis. Missing contracts remain blockers, not successful writes.
- `_create_field()` uses the actual returned field definition for value mapping. Same-scope newly created Trackers may use their configured template; unrelated Tracker templates are rejected.

## Validation

`python -m unittest tests.test_field_mapping_repairs tests.test_system_field_mapping_contract -q`: 18 passed.

`python -m unittest discover -s tests -q`: 333 run, 327 passed, 6 GUI tests skipped (no display).

Seven new regression cases cover metadata preservation, default selection and persistence, configured null fields, Text metadata requirements, actual Choice IDs, username identity, and Date analysis → creation → Item write/readback without opening the creation popup. Date readback uses a stateful Mock server, not a deployed Codebeamer API.

Existing Xray pipeline regression tests also pass: Test Case/Set/Plan/Run identity and explicit relations, source-only Test Execution, native steps/evidence, and retry behavior. No Xray policy or launcher was replaced in this change.

## Remaining environment checks

No live Jira DC or Codebeamer server was used. Actual 3.0.0.7 Date creation and FieldValue contracts, installed Xray response shapes, user IDs, permissions and Windows GUI behavior require customer-environment validation. A missing verified API contract still blocks the corresponding write. Mock success does not establish server compatibility.

`RUN_WINDOWS.bat`, configuration files, `.migration`, and the existing five-stage UI structure are unchanged. No project data was modified and no PR was merged.
