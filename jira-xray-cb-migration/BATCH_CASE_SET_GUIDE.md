# Batch Import — Test Case and Test Set

The batch program is `batch_case_set_import.py` and the Windows launcher is `RUN_BATCH_CASE_SET.bat`.

### Before starting
- Use branch `test/cb-item-post-minimal-20261106` and the existing local `config.local.json`.
- Confirm the Jira project key and the **existing** Codebeamer Tracker ID for each Issue Type. Do not use the same Tracker ID for different kinds unless deliberately configured.
- Use a test CB Tracker or a Jira set of issues **not already migrated** with the older `post_smoke_test.py`. The batch journal tracks its own writes; it does not scan existing CB Items and cannot detect historical duplicates automatically.
- No automated CB field creation, option creation or user creation.

### Windows launch
Double-click `RUN_BATCH_CASE_SET.bat`. Choose 1/2 for read-only preview, 3/4 for real POST, enter project, Tracker and limit.

Start with **preview limit=1**, then **POST limit=1** for one never-migrated Jira Issue, confirm Item values, then preview all with limit=0 and finally POST all with limit=0.

### Direct CLI examples
Preview all Test Cases:

```bat
python batch_case_set_import.py --kind case --jira-project STGVSTAR --tracker-id 4879 --limit 0 --prompt-auth
```

Write all Test Cases (real POST):

```bat
python batch_case_set_import.py --kind case --jira-project STGVSTAR --tracker-id 4879 --limit 0 --prompt-auth --execute --confirm-project STGVSTAR
```

Test Set is `--kind set` with the actual **Test Set** Tracker ID.

### Actual scope
- **Test Case**: name/description, configured 7 additional Text/WikiText source fields, optional WikiText Jira Key (existing local `migration.post_smoke.jira_key_field`).
- **Test Set**: name/description only. User supplied a `Wiki Link / URL` Jira Key field that has not been verified as a WikiText write model, so this is not written in the batch.
- Jira read pagination: ordered `id ASC`, max 50 issues per page by default.
- CB write: one POST per issue; GET verifies Name/Description/sent custom fields.
- Durable `POST_ATTEMPT` and `VERIFIED` events in `.migration/test-case-set-batch/batch-<hash>.jsonl`. On restart, verified Issues are skipped. An uncertain previous POST stops the entire run; inspect server before manually reconciling. Avoid manually deleting journal entries.

### Not yet supported / not a full migration
- Choice/Option values and User mapping
- Jira Comment/Attachment
- Test Set membership, Test Plan relationships, Test Runs
- Status, Resolution, Created/Updated timestamps and revision History
- Existing target item reconciliation from older tools or other computers

Jira may mutate while the batch executes. If the search total changes, the program stops. This version does not guarantee consistent source snapshots across a changing Jira project.

Code has been committed, but **no live Jira or Codebeamer batch POST has been executed by the assistant**. Review the preview and target Tracker mandatory fields before executing a real write.
