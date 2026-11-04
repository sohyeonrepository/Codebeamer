# Jira → Codebeamer field-type review matrix

This is **metadata-driven**, not label-driven. Jira's `array/string` alone does not prove Choice.

| Jira schema / metadata | Classification | Suggested Codebeamer destination | Guard |
| --- | --- | --- | --- |
| `type:string`, textarea custom | text/single | Text or WikiText | Keep original line breaks |
| `type:array,items:string`, no options, no select custom | text/multi | CB Text/WikiText via explicit newline transform, or native multi Text if verified | Preserve original JSON; inspect potential separator loss |
| `type:array,items:string`, nonempty `allowedValues` | option/multi | Choice with multi=true | Map each option to verified CB choice ID |
| `type:array,items:string`, Jira select/multiselect/checkbox custom | option/multi | Choice with multi=true | Verify missing `allowedValues` rather than inventing options |
| `type:array,items:option` | option/multi | Choice with multi=true | Map options and re-read |
| `type:array,items:component` | option/multi | Choice with multi=true | Preserve Jira component IDs and names |
| `type:array,items:user` | user/multi | Members or multi User | Map user identities; no automatic user creation |
| `type:array,items:issue` | reference/multi | Reference, deferred until all items exist | Verify target tracker, direction and IDs |
| `type:date`, `type:datetime` | date / datetime | Matching date/datetime field | Never silently discard timezone/time |
| Unknown or conflicting schema | unknown | Manual review | Stop auto-creation and auto-write |

### Source code evidence

The archived [intland/cb-jira-checklist](https://github.com/intland/cb-jira-checklist) demonstrates `@CustomField.MetaData`, `allowedValues`, `setMultipleSelection`, `@CustomField.ImportFieldValue`, `@CustomField.ImportFieldHistoryValue` and `JiraTrackerSyncConfig.getChoiceValueMapping`. The example translates a **Checklist array** to a **single WikiText** field for that add-on. It is not a general rule that Jira arrays should always become Text, nor is it a complete Jira Connector implementation.

### Scope

Both `migration.wizard.semantic_type` and `mapping_helper.suggest_mapping` now call `migration.field_semantics.jira_field_semantics`. Selection is still displayed through the existing checkbox UI. Changes are opt-in by target field mapping; native History and live server compatibility remain unverified.

### Local verification commands

From `jira-xray-cb-migration`:

```powershell
py -3 -m unittest tests.test_jira_field_semantics -v
py -3 -m unittest discover -s tests -v
```

Do not merge before running tests and checking any existing saved mappings in a copied verification state directory; older `array/string` mappings may need explicit review.
