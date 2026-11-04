# Intland Jira Checklist Connector reference — migration reuse

Source: https://github.com/intland/cb-jira-checklist (BSD-3-Clause, archived).
This source is **a custom field extension to Jira Connector**, not the full Codebeamer Jira Connector implementation. It depends on Codebeamer server-side internal JARs, and its API annotations have not been verified for Codebeamer 3.0.0.7.

## Code evidence reviewed
- `ChecklistForJiraField.java`: `@Component` + `@CustomField(type="WikiText", of="Checklist")`, field metadata, import/export field values, changelog value parse, incremental field history conversion and cleanup.
- `ChecklistForJiraMarkup.java`: legacy Checklist Markdown <-> Codebeamer Wiki formatting.
- `ChecklistPlugin.java`: wiki plugin wrapper of JSON, priority/status lookup, rendering logic.
- `build.gradle`: builds against locally installed Codebeamer internal JAR files; not a standalone Maven dependency.
- Unit tests cover field conversion, markup and wiki rendering. The tests do **not** demonstrate native CB 3.0.0.7 history import or Xray Test Run import.

## Reused approach
The optional `migration/jira_connector_custom_fields.py` adapter converts Jira Checklist JSON into WikiText payloads, generates an **unapplied** field metadata proposal, and parses Checklist changelog records. These are explicitly invoked with `type: "jira_checklist"`; existing converters and checkboxes remain unchanged by default.

Source-specific priority IDs stay unresolved until a matching value map is provided, preventing accidental remapping. Dates with a timezone retain that timezone; legacy date-only values remain date-only at midnight without pretending to know the source timezone.

`@CustomField.ImportFieldHistoryValue` / `@CustomField.ImportFieldChange` are plugin extension hooks for a **particular Checklist field**. Their existence does not prove that external Python can create Codebeamer-native revisions or overwrite imported author/time. This adapter does not claim to do so.

## Usage
```json
{
  "customfield_12345": {
    "target": "description",
    "type": "jira_checklist",
    "field_id": 10005,
    "value_mapping": {"3": {"id": 9, "name": "High"}}
  }
}
```
The example `target` / `field_id` are illustrative: **configure the actual target WikiText field, not an arbitrary field or the ordinary Description**. A dedicated WikiText field is normally required.

The UI's type selector still needs an explicit user-facing entry before the new adapter can be selected by GUI; direct field mapping JSON can exercise it.

## Limits
- Checklist-specific conversion only: no automatic Jira field discovery, tracker configuration POST, reference migration, Jira server connectivity, or History POST.
- Wiki plugin rendering requires a compatible Checklist plugin on the Codebeamer server, and has not been verified for 3.0.0.7.
- This implementation is separately authored; original project BSD attribution should be preserved in project documentation.
