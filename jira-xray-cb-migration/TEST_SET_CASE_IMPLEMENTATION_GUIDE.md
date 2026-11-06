# Test Set → Test Case: migration execution guide (development branch)

Branch: `test/cb-item-post-minimal-20261106`
Existing local `config.local.json` and existing Jira/Codebeamer credentials are reused. No additional CB field numeric IDs are required for Text/WikiText, OptionChoice, or supported User references; Codebeamer GET resolves them by the user-provided field names and types.

## 0. Coverage and safety

The provided Test Set and Test Case mapping JSON files remain authoritative. **The entire mapping is NOT implemented yet**. The current batch supports Name, Description, Test Case seven custom Text/WikiText fields, optionally target OptionChoice fields and UserReference fields, and existing Test Case WikiText Jira Key. It also captures Xray membership keys and Jira child metadata. It does not yet support every native status/time field, reference-to-work configuration choices, creating missing Labels choices, or the Test Set custom Wiki Link/URL write. Xray Test Run and native Jira History replay are outside the current verified scope. Do not interpret generated Items as a full migration.

The newly added child and relation write programs are separate and **not live-verified**. They assume Codebeamer V3 multipart comment/attachment contracts and a writable Test Cases TableField in the actual Test Set Tracker. Verify with preview and one disposable Item first. Do not bulk migrate a production project while unsupported mapped fields remain.

## 1. Import Test Set Items first (preview, then controlled POST)

Run `RUN_BATCH_CASE_SET.bat` from the `jira-xray-cb-migration` directory, select **2** for Test Set preview and **4** for Test Set POST. Set the test limit to `1`. Select Choice/User only if target options/user names match and the CB field GET exposes confirmed metadata. Select **Y** for relation and child capture. The `PARTIAL` confirmation on POST intentionally limits real creation to one source Issue unless the developer explicitly overrides the guard. Use never-imported test Issues because this batch does not search previous imports from the old one-Issue smoke script.

For direct command-line preview:

```bat
python batch_case_set_import.py --kind set --jira-project STGVSTAR --tracker-id YOUR_SET_TRACKER_ID --limit 1 --capture-relations --capture-children --prompt-auth
```

For a single real POST after preview and destination check:

```bat
python batch_case_set_import.py --kind set --jira-project STGVSTAR --tracker-id YOUR_SET_TRACKER_ID --limit 1 --capture-relations --capture-children --prompt-auth --execute --confirm-project STGVSTAR --acknowledge-partial
```

## 2. Import Test Case Items

Same commands with `--kind case`, and the actual **Test Case Tracker ID**. Test Case mapped Text/WikiText source values are included; CB custom Field IDs are looked up from the existing CB Tracker.

If you want Choice or User mappings to run, append `--include-options` and/or `--include-users` **only after reviewing target types**, and verify each actual value via GET. Choice options names must match the target CB names or you must supply explicit `migration.option_name_mapping` aliases in the local Config. CB usernames must already exist or `migration.user_name_mapping` aliases must be supplied. Do not put numeric internal CB IDs in local config.

## 3. Comment / Attachment processing

The batch `--capture-children` flag reads all Jira comments and attachment metadata into the scoped `children-<hash>.jsonl`. This is source capture, **not** CB upload. The new separate writer uses each verified CB Item ID:

Preview:
```bat
python import_case_set_children.py --kind set --jira-project STGVSTAR --tracker-id YOUR_SET_TRACKER_ID --include-comments --include-attachments --limit 1 --prompt-auth
```

One real child write:
```bat
python import_case_set_children.py --kind set --jira-project STGVSTAR --tracker-id YOUR_SET_TRACKER_ID --include-comments --include-attachments --limit 1 --prompt-auth --execute --confirm-project STGVSTAR
```

Repeat for `--kind case` and target Test Case tracker. Comments use `POST /v3/items/{id}/comments` as multipart `comment` + `commentFormat`; attachments use multipart `attachments` and binary SHA256 readback. Child writes journal source IDs. Original Jira author and timestamp are retained only in captured source JSON, not restored as CB native comment metadata.

## 4. Phase-2 membership (Test Set ↔ Test Case)

Once both Item types have verified Jira Key → CB Item ID mappings and `--capture-relations` was enabled:

```bat
python phase2_resolve_test_sets.py --jira-project STGVSTAR --set-tracker-id YOUR_SET_TRACKER_ID --case-tracker-id YOUR_CASE_TRACKER_ID
```

This creates `.migration/test-case-set-batch/phase2-verified-id-plan.json` with resolvable pairs and unresolved source keys; no relationship is written yet.

Inspect the plan and validate that the **actual target Test Set Tracker** contains a single `Test Cases` TableField with a TrackerItemReference column. If it does, preview one Set:

```bat
python import_test_set_relations.py --set-tracker-id YOUR_SET_TRACKER_ID --limit 1 --prompt-auth
```

After preview, run exactly one Set:

```bat
python import_test_set_relations.py --set-tracker-id YOUR_SET_TRACKER_ID --limit 1 --prompt-auth --execute --confirm-tracker-id YOUR_SET_TRACKER_ID
```

The writer rejects a Test Set whose table already has different members. A failed/uncertain PUT is journaled and needs reconciliation before retrying. The Test Plan relations are reported as unresolved rather than silently discarded, since only Test Set↔Case is currently targeted.

## 5. Local logic tests

```bat
python -m unittest discover -s tests -p test_option_batch.py -v
python -m unittest discover -s tests -p test_user_batch.py -v
python -m unittest discover -s tests -p test_phase1_children.py -v
python -m unittest discover -s tests -p test_import_case_set_children.py -v
python -m unittest discover -s tests -p test_phase2_manifest.py -v
python -m unittest discover -s tests -p test_phase2_resolve.py -v
python -m unittest discover -s tests -p test_import_test_set_relations.py -v
```

These tests have been committed but not executed in the remote environment. The assistant does not have the user's live Jira/CB network credentials or direct access to the servers. Full mapping coverage and live evidence are not yet established.
