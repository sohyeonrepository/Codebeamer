# Jira Data Center definition catalog: global versus project usage

Customer screenshot/paste lists many Jira **global definitions** (regular/sub-task Issue Types, Priority Levels, Issue Statuses, Resolutions, Security Levels). These are NOT all field types, and they are not proof that every Issue Type is configured for each project.

## Root cause found

In `mapping_helper.collect_catalog`, Issue Types were inferred only from up to `sample_limit` retrieved issues (default 20). A defined type that did not occur in the sample did not appear at all. The existing five-step `migration/wizard.py` used a different source: `JiraClient.issue_types(project)` → GET `/rest/api/2/project/{project}`, reading `issueTypes`.

This branch fixes the mapper so all configured project Issue Types appear even when none was present in the sample. Those types have `metadata_only=true`, `sample_count=0`, and no guessed field values. Use actual project/Issue Type metadata and real samples for field-level mapping before approving Import.

## Jira REST GET endpoints

| Source | Request | Scope |
| --- | --- | --- |
| Global Issue Types | `/rest/api/2/issuetype` | All visible defined issue types, including sub-task metadata |
| Selected project Issue Types | `/rest/api/2/project/{key}` → `issueTypes` | Types associated with that project's Issue Type Scheme |
| Global Priorities | `/rest/api/2/priority` | Server definitions, not necessarily enabled for every project |
| Global Statuses | `/rest/api/2/status` | Defined workflow statuses, not the valid transitions for each issue |
| Global Resolutions | `/rest/api/2/resolution` | Server definitions, not a field's actual allowedValues |
| Security Levels | Security Level Scheme and permissions are **project scoped** | Must discover using permission-approved Jira DC APIs or per-field metadata; never infer from labels `1`, `2` |

The user-provided lists include Korean/English custom names, Xray Test Plan/Set/Execution Issue Types, and sub-task Issue Types such as `Sub Test Execution`. They are source examples, not a complete machine-readable id/name mapping. Keep actual Jira IDs obtained from GET; do not hardcode copied labels or assume equal IDs across projects.

## Implementation

- `clients/jira_client.py`: `global_catalog()` for GET-only top-level Issue Type/Priority/Status/Resolution definitions, and `project_issue_type_catalog(project)` for project-scoped Issue Types.
- `mapping_helper.collect_catalog()`: pre-populates project Issue Type rows from configured project metadata, then attaches sample issue values; records metadata-only rows, avoids editmeta lookup without a representative issue, preserves Xray Run sample extraction.
- `tests/test_jira_catalog_discovery.py`: read-only catalog, sub-task classification, sample-limit regression, duplicates.
- Existing 5-step UI and Windows launchers are unchanged.

## Important production verification

1. Compare count/IDs of `/rest/api/2/issuetype` and project `/rest/api/2/project/{key}` → `issueTypes`. A type visible globally can be intentionally absent from a project; report that difference rather than invent an issue-type assignment.
2. Check Jira auth and Browse Projects permissions. 403/401 may be configuration or scope issues, not missing types.
3. Test an issue type with zero issue samples; show it but block auto-mapping fields until the chosen context's schema is independently confirmed.
4. For Priority, Status, Resolution and Security Levels, fetch the source's actual allowedValues and workflows for **each selected project and Issue Type** before creating or mapping Codebeamer options.
5. Test `Xray Test Plan`, `Test Set`, `Test Execution` and sub-task types separately; Xray Test Runs are not standard Issue Types.
6. Preserve IDs/names, collisions, pagination where relevant, raw responses, and original config/state. Do not write Jira or Codebeamer until approved.

## Run with Codex

```powershell
py -3 -m unittest tests.test_jira_catalog_discovery -v
py -3 -m unittest discover -s tests -v
```

Integration into `feat/import-wizard-scoped-mapping` is pending; never merge PR #1, #2, #3 or this PR wholesale. Use a clean integration branch, selective backport, and preserve `RUN_WINDOWS.bat` and current five-step GUI.
