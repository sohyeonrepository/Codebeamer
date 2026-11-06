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

## Additional development (2026-11-06)

The importer now supports optional existing **ChoiceOptionReference** and **UserReference** fields, resolved from target Tracker field details and CB usernames. It does not require you to manually parse numeric IDs.

- `--include-options`: recognized Choice / Choice-option rows only. Fetches each target OptionChoiceField's real `options` through GET; requires exact option name matches. `Components` (Work/config references) and Xray Test membership are intentionally **not** treated as ordinary options.
- `--include-users`: recognizes `UserChoiceField` and native `Assigned to`; resolves Jira username through GET `/v3/users/findByName?name=...`. Existing users must already exist on CB.
- `--capture-relations`: captures Jira Xray membership keys into the local `relations-<hash>.jsonl` next to the batch journal. This is **capture**, not CB relation writing.
- `--capture-children`: captures complete Jira comment history and attachment metadata into `children-<hash>.jsonl` after successful Item creation, marked `SOURCE_CAPTURED_NOT_IMPORTED`. It does not upload comments or binaries.
- `phase2_resolve_test_sets.py` reads both verified Item journals and membership manifests, resolves Test Set/Test Case Jira keys to CB Item IDs, deduplicates inverse membership, and saves `phase2-verified-id-plan.json`. This is a plan, **not** a CB relation write.

Run **preview** first:

```bat
python batch_case_set_import.py --kind set --jira-project STGVSTAR --tracker-id YOUR_SET_TRACKER_ID --limit 1 --include-options --include-users --capture-relations --capture-children --prompt-auth
```

The BAT menu can also select these capabilities. For a real POST, the program explicitly requires `--execute --confirm-project STGVSTAR --acknowledge-partial` because parts of the **full user-provided mapping remain unimplemented**. Do NOT treat this command as a full migration.

If target option display names differ from Jira values, optional configuration in the existing local `migration` object:

```json
{
  "option_name_mapping": {
    "case": {
      "priority.name": { "Highest": "High" }
    }
  },
  "user_name_mapping": {
    "case": {
      "assignee": { "jira_account_name": "cb_account_name" }
    }
  }
}
```

This is a fragment *inside* `migration`, not a complete `config.local.json`. IDs are looked up automatically.

### Remaining user-mapped data before any full-completion claim

1. `Components` as Work/config references and similar lookup fields, Test Set Labels target field creation
2. Test Set Jira Key as `Wiki Link / URL` (different from existing WikiText contract)
3. Native Status/Resolution/Created/Updated/Creator properties, subject to CB write permissions and exact field contracts
4. Real Comment/Attachment POST/upload and reconciliation, not just capture
5. Test Set ↔ Test Case table/reference write and GET verification
6. Test Plans and Xray Test Run/results only after additional target model setup
7. Original Jira History preservation/native Revision replay remains under review

No assistant-side live batch POST or local unit-test execution has been performed in this environment. The basic one-Issue POST success was reported by the user. Do not rerun already-created Items from the older one-Issue smoke test without reconciling IDs: the new batch journal does not know those prior writes.
