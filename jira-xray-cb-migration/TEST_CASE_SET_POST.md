# Test Case / Test Set only: config-driven one-Issue POST check

Branch: `test/cb-item-post-minimal-20261106`

This is **not** a full migration. It uses mappings in:
- `mappings/test_case.mapping.json` for Jira Issue Type **Test**
- `mappings/test_set.mapping.json` for Jira Issue Type **Test Set**

Only phase_1 rows with `mode: direct` are written. Name and Description use the already established POST route. Existing scalar Text/WikiText custom fields are identified by Codebeamer Tracker configuration **label and type**, then converted to actual field IDs by the program. `customField[74]` is a UI registration hint, **not** a required numeric field ID.

## Test Case

Fields attempted: Name, Description, [TC] ID, [TC] RB-Test Environments, [TC] RB-Test Output, [TC] RB-Test Script, 시험 방법, 시험 전 조건, 판정 조건. Null source values are omitted.

If `migration.post_smoke.jira_key_field` is configured with an existing **WikiText** field label, also writes a clickable Jira Key link as in the already-successful smoke test.

Preview:

```bat
python test_case_set_post.py --kind case --config config.local.json --jira-key STGVSTAR-2715 --tracker-id 4879 --prompt-auth
```

One actual POST (use a Jira key that has **not** been imported to the selected Tracker):

```bat
python test_case_set_post.py --kind case --config config.local.json --jira-key STGVSTAR-2715 --tracker-id 4879 --prompt-auth --execute --confirm-key STGVSTAR-2715
```

## Test Set

Fields attempted: Name and Description **only**. All choice/user fields are pending mapping.
The Test Set Jira Key target is `Wiki Link / URL` in the supplied mapping (not the same as the validated Test Case WikiText). It is **not** silently treated as a WikiText field, and no link is written in this initial Test Set test.

Preview:

```bat
python test_case_set_post.py --kind set --config config.local.json --jira-key STGVSTAR-2553 --tracker-id YOUR_TEST_SET_TRACKER_ID --prompt-auth
```

One actual POST (replace the tracker ID with the real, existing Test Set Tracker):

```bat
python test_case_set_post.py --kind set --config config.local.json --jira-key STGVSTAR-2553 --tracker-id YOUR_TEST_SET_TRACKER_ID --prompt-auth --execute --confirm-key STGVSTAR-2553
```

## Excluded for now

Choice, User, system timestamp/status fields, Comments, Attachments, Test Set Tests table relationship, Test Plan membership, Test Run results. These must not be inferred from labels.

## Safety and validation

- Default is **GET-only** preview. POST requires both `--execute` and `--confirm-key`.
- Does not create Codebeamer fields, tracker configurations, or update existing Items.
- Confirms Jira Issue Type exactly before POST.
- Resolves configured fields from the existing Codebeamer Tracker; missing/ambiguous/wrong-type fields fail before POST.
- A source null is omitted, not written as null.
- Journals an attempt *before* POST at `.migration/test-case-set/post-attempts-case.jsonl` or `post-attempts-set.jsonl`; any previous attempt for same key/tracker is blocked. It does **not** scan all existing CB Items, so choose never-imported Jira Issues and a controlled test Tracker to avoid duplicates.
- After POST, GET verifies Name, Description, and sent custom field values. A response that omits customFields fails verification even if the Item was created; inspect it before retrying.
- This branch was committed without live Jira/CB access. The existing simple POST was reported successful by the user; these extended fields have **not yet** been verified on the server.

Local tests (do not create Items):

```bat
python -m unittest discover -s tests -p test_case_set_post_test.py -v
```
