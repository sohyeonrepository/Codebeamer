# Jira → Codebeamer: one Issue POST smoke test (2026-11-06)

This is an **isolated test**. Existing Wizard and import engine remain unchanged.
The script reads **one Jira Issue** and sends **at most one Codebeamer POST**.
No tracker creation, field creation, full issue scan, Xray, R4J, or history.

## Setup

- Prepare an existing Codebeamer Tracker and confirm it permits creating an Item with only Name/Description. Mandatory status/priority/custom fields may require additional payload and are deliberately **not guessed**.
- Run in `jira-xray-cb-migration` with the existing `config.local.json` (not committed).
- Credentials must be provided by the existing config's environment variables, or use `--prompt-auth`.
- If the Jira Key field is a **WikiText** field, get its *actual Field ID* from the target Tracker configuration. The script checks its `valueModel` via GET.
- The Jira link is a **link to the original Jira issue**, e.g. `[STGVSTAR-2715|https://jira.example.com/browse/STGVSTAR-2715]`. This follows Codebeamer WikiText hyperlink syntax, not an internal CB Item link.

## 1. Read-only preview (no POST)

```bat
python post_smoke_test.py --config config.local.json --jira-key STGVSTAR-2715 --tracker-id 4879 --prompt-auth
```

## 2. Actual POST: Summary + Description only (first recommended test)

```bat
python post_smoke_test.py --config config.local.json --jira-key STGVSTAR-2715 --tracker-id 4879 --prompt-auth --execute --confirm-key STGVSTAR-2715
```

The `--execute` flag and matching `--confirm-key` deliberately authorize one real write. The program retrieves the new Item by ID and checks Name/Description. It writes an attempt journal under `.migration/post-smoke/` **before** POST. Never run the same Jira key/Tracker a second time without checking the created Item and journal first.

## 3. Optional: include Jira Key as a clickable WikiText link

Only after preparing the field, use its **actual** ID:

```bat
python post_smoke_test.py --config config.local.json --jira-key STGVSTAR-2715 --tracker-id 4879 --jira-link-field-id 10006 --prompt-auth --execute --confirm-key STGVSTAR-2715
```

**10006 is only an example**; the existing field may have been changed from Text to WikiText and could have a different ID. Do not run both step 2 and step 3 against the same Jira Issue key/Tracker: that would be a second creation attempt. To compare the two modes, use a different Issue key or a disposable test Tracker, after checking the target first.

The script validates the field definition first; if the field is still Text rather than WikiText, it stops before POST.

## Test and limitations

```bat
python -m unittest discover -s tests -p test_post_smoke_test.py -v
```

These are local logic tests, not evidence of live API success. First live server POST and WikiText field verification are still pending. Status `POST_ATTEMPT` blocks automatic retry, including after network timeouts, to avoid accidentally creating duplicate Items. The journal has to be inspected and reconciled manually before any retry.

Useful API docs:
- https://support.ptc.com/help/codebeamer/r2.2/en/codebeamer/developers_guide/dg_creating_tracker_item.html
- https://support.ptc.com/help/codebeamer/r3.2/ko/codebeamer/developers_guide/swagger/12709331.html
- https://support.ptc.com/help/codebeamer/r3.1/ko/codebeamer/user_guide/ug_wiki_basic_formatting.html
