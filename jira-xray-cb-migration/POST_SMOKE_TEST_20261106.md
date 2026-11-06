# Jira → Codebeamer: one Issue POST smoke test (2026-11-06)

This is an **isolated test**. Existing Wizard and import engine remain unchanged.
The script reads **one Jira Issue** and sends **at most one Codebeamer POST**.
No tracker creation, field creation, full issue scan, Xray, R4J, or history.

## Setup

- Prepare an existing Codebeamer Tracker and confirm it permits creating an Item with only Name/Description. Mandatory status/priority/custom fields may require additional payload and are deliberately **not guessed**.
- Run in `jira-xray-cb-migration` with the existing `config.local.json` (not committed).
- Credentials must be provided by the existing config's environment variables, or use `--prompt-auth`.
- If the Jira Key field is **WikiText**, set the field name/type/UI registration under `migration.post_smoke.jira_key_field` in `config.local.json`. The script looks up its internal numeric ID automatically using Tracker configuration GET. The registration string such as `customfield[6]` is not assumed to equal the internal ID.
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

## 3. Jira Key as a clickable WikiText link — Config only

Add the following inside the existing `migration` object in your local `config.local.json` (do not create another root-level migration object):

```json
{
  "post_smoke": {
    "jira_key_field": {
      "label": "Jira Key",
      "type": "WikiText",
      "registration": "customfield[6]"
    }
  }
}
```

The above snippet shows **only the portion inside `migration`**. `registration` is an optional human-facing registration hint from the Codebeamer UI, not a numeric API ID. The field is selected by its unique label, and its type and internal ID are read from the actual Codebeamer Tracker configuration. If the label is absent/duplicated, the type cannot be confirmed, or no internal ID is returned, POST is blocked.

With this config, the **same command from step 2** includes the Jira link automatically. If you want to test Summary/Description alone first, omit `post_smoke.jira_key_field` in the config. Do not execute both variants with the same Jira Key and Tracker; duplicate writes are blocked by the attempt journal.

## Test and limitations

```bat
python -m unittest discover -s tests -p test_post_smoke_test.py -v
```

These are local logic tests, not evidence of live API success. First live server POST and WikiText field verification are still pending. Status `POST_ATTEMPT` blocks automatic retry, including after network timeouts, to avoid accidentally creating duplicate Items. The journal has to be inspected and reconciled manually before any retry.

Useful API docs:
- https://support.ptc.com/help/codebeamer/r2.2/en/codebeamer/developers_guide/dg_creating_tracker_item.html
- https://support.ptc.com/help/codebeamer/r3.2/ko/codebeamer/developers_guide/swagger/12709331.html
- https://support.ptc.com/help/codebeamer/r3.1/ko/codebeamer/user_guide/ug_wiki_basic_formatting.html
