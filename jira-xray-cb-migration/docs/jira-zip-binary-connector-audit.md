# Uploaded jira.zip — binary connector API review

Review method: **static inspection only** of user-supplied `jira.zip` (130 archive entries, 100+ `.class` entries), using Java `javap -p` to enumerate declarations. No class was executed, decompiled into replacement source, or published to this repository. Original binary is **not** committed: distribution/license and target Codebeamer runtime compatibility have not been established.

## What the package actually contains

The compiled classes identify themselves by their fully qualified names as `com.intland.codebeamer.controller.jira.*` (the ZIP folder is merely `jira/`). `JiraImportSupport.class` is compiled to **Java class-file major version 65 (Java 21)**. This does **not** prove a Codebeamer release number or compatibility with Codebeamer 3.0.0.7.

| Binary class | Method declarations observed | Mapping/migration implication |
| --- | --- | --- |
| `JiraRestClient` | `decodeDate(String)`, `encodeDate(Date)`, `getMetaDataEndpoint()`, issue/comment API methods | Date handling and REST source client are separate responsibilities; do not copy Java date assumptions into Python blindly |
| `JiraMetaDataEndpoint` and `Jira8MetaDataEndpoint` / `Jira9MetaDataEndpoint` | `getFieldsPerIssueType`, `getIssueTypeFields`, `getStatusOptionsPerIssueTypeId` | **Field metadata is issue-type and Jira-version scoped**, not determined solely from sample issue values |
| `AbstractMetaDataEndpoint` | `getLookupMap`, `getChoiceOption`, `getStatus`, `getPriorities`, `getResolutions`, `getIssueLinkTypes` | Resolve choice/status/link IDs using actual server metadata and preserve original IDs |
| `JiraTrackerSyncConfig` | `buildFieldMapping`, `addFieldMapping`, `newTargetField`, `adjustFieldConfiguration`, `getCustomFieldPlugins` | Field type + mapping + target creation are separated; do not infer Codebeamer numeric type IDs across deployments |
| `CustomFieldPlugin` + `CustomFieldPluginManager` | `setMetaData`, `importFieldValue`, `getHistoryFieldValue`, `getFieldChange`, `exportFieldValue`, `getPlugin` | Add-ons can require their **own field converter**; `allowedValues` alone does not guarantee generic Choice |
| `JiraImportSupport` | `importItemFieldValues`, `importFieldValue`, `importItemRevision`, `importItemHistory`, `importComments`, `importAttachment`, `getAssociation`, `getRemoteItemReferences` | Native Java import implementation has history, comments and association paths, but `javap` only proves methods exist, not externally callable APIs or working write paths |
| `JiraImportController` | `jiraProjectIssueTypeSchema`, `jiraProjectIssueTypes`, `listFields`, `getJiraImportHistory` | Connector collects a richer schema/metadata picture than bare Jira global field list |

## High-confidence integration decisions

1. Retain existing scoped read of Jira field definition and `createmeta` / configured issue-type field metadata.
2. Classify Jira `schema.type`, `schema.items`, `schema.custom` and `allowedValues` jointly. `Text/Multi` is not necessarily Choice/Multi. Do not create target choices from arbitrary user values without checking a real choice schema.
3. Recognize the published Checklist add-on ID `com.okapya.jira.checklist:checklist`: even with `allowedValues`, generic Choice mapping is unsafe. Require explicit `jira_checklist` converter and validate the target WikiText plugin.
4. Preserve Jira ID/name/accountId/option identity while providing collision checks for User and Choice values. Existing saved Choice name-based keys must not be silently replaced.
5. Re-read created CB field schema/choices, use dedicated transaction/run journals, and validate item values after writes.
6. Native history import **cannot** be claimed simply because `importItemHistory` and `importItemRevision` appear in a class file. Full supporting Codebeamer classpath, permission/licensing and version verification are required; Python REST `GET history` is not a `POST history` contract.

## Code applied in review branch

- `migration/field_semantics.py`: add-on Checklist identification now blocks accidental generic Choice inference.
- `tests/test_jira_field_semantics.py`: regression test for Checklist metadata with `array/string` + `allowedValues`.
- This branch is based on `feat/jira-connector-custom-field-reference` and keeps existing Wizard, config, and local state file paths unchanged.

## Required Codex verification

From the `jira-xray-cb-migration` folder run:

```powershell
py -3 -m unittest tests.test_jira_field_semantics -v
py -3 -m unittest discover -s tests -v
```

Report each failure including pre-existing failed tests (Summary suggestion, auto connection, legacy Choice), new User key mapping, and compatibility of existing imports. Confirm no `.class` or `.jar` binary was added to GitHub. Do **not** merge until regression tests pass and customer environment Windows Wizard / Codebeamer 3.0.0.7 validation is separately complete.
