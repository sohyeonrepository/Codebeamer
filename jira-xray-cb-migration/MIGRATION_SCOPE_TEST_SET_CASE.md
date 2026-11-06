# Test Set → Test Case migration scope (user-supplied mapping baseline)

**Scope authority:** `mappings/test_set.mapping.json` and `mappings/test_case.mapping.json`. Do not silently reduce the requested migration to Name/Description or Text/WikiText. Every mapping row needs a final outcome: migrated & GET-verified, deliberately excluded by user, or blocked with reason. Source null values are skipped without rewriting non-null values as null.

## Processing order

1. Test Set issue Items: create or reconcile Item, field mapping, comments, attachments; persist Jira Key ↔ CB Item ID.
2. Test Case issue Items: same; persist Jira Key ↔ CB Item ID.
3. Resolve and apply Xray Test Set membership (Test Set → Test Case); do not attempt to link to missing Items. Keep failed relation queue and retry.
4. Test Run/execution relationship, when the execution mapping and API contract are supplied and confirmed, is independent of Test Set membership.
5. Jira original History: preserve separately and mark native CB Revision replay unsupported/unconfirmed until checked.

The order Test Set then Case is recommended, not a hard dependency for Item creation. Both target IDs must exist before phase 2 relationships.

## Test Set mapping — full requested scope

- `summary` → Name (`name`, Text)
- `description` → Description (`description`, WikiText)
- `reporter.name` → Reporter (`customField[6]`, Choice/User)
- `assignee` → Assigned to (`assignedTo`, Members)
- `customfield_17552` → Squad (`choiceList[1]`, Choice/Option)
- `components` → Components (`choiceList[2]`, Choice/Work/config)
- `security` → Security Level (`choiceList[3]`, Choice/Option)
- `resolution` → Resolution (`resolutions`, Choice)
- `labels` → Labels (to be created, Choice; do not invent missing option IDs)
- `priority.name` → Jira Priority (`choiceList[5]`, Choice)
- `key` → Jira Key (`customField[4]`, Wiki Link/URL; requires FieldValue contract)
- `comment` / `attachment` → Comment/Attachment (separate APIs)
- `created` → submittedAt (Date; user list labels it Submitted by; flag mismatched display label)
- `updated` → Modified at (`modifiedAt`)
- `creator` → Submitted by (`submitter`, User)
- `status` → Status (`status`, Choice)
- `customfield_17775` → Tests association with a Test Set (`table[0]-tableColumn[0,0]`): phase 2, not a scalar field.

## Test Case mapping — full requested scope

- `summary` → Name, `description` → Description
- `assignee` → Assigned to (`assignedTo`, Members)
- `comment` / `attachment` → Comment/Attachment (separate APIs)
- `priority.name` → Jira Priority (`choiceList[17]`, Choice)
- `components` → Components (`choiceList[14]`, Choice/Work/config)
- `labels` → Labels (`choiceList[18]`, Choice)
- `customfield_17552` → Squad (`choiceList[13]`, Choice/Option)
- `customfield_19102` → [TC] ID (`customField[74]`, Text)
- `customfield_19807` → [TC] RB-Test Environments (`customField[75]`, Text)
- `customfield_19809` → [TC] RB-Test Output (`customField[77]`, Text)
- `customfield_19808` → [TC] RB-Test Script (`customField[76]`, Text)
- `customfield_19805` → 시험 방법 (`customField[14]`, WikiText)
- `customfield_19804` → 시험 전 조건 (`customField[13]`, WikiText)
- `customfield_19806` → 판정 조건 (`customField[15]`, WikiText)
- `security` → Security Level (`choiceList[15]`, Choice)
- `reporter.name` → Reporter (`choiceList[16]`, Choice/User)
- `created` → Submitted at (`submittedAt`, Date)
- `updated` → Modified at (`modifiedAt`, Date)
- `key` → Jira Key (existing WikiText field, source is issue.key)
- `customfield_17770` → Test Sets (`choiceList[19]`, Xray membership; phase 2)
- `customfield_17772` → Test Plans (`choiceList[20]`, Xray membership; phase 2)
- Xray Test Runs → CB Test Run/results, requires execution API design.

## Implementation status as of this commit

- The existing `post_smoke_test.py` basic POST has been reported successful on a real server by the user.
- `batch_case_set_import.py` currently writes **only direct scalar fields** (Case: Name/Description and seven Text/WikiText fields; Set: Name/Description). It does **not** fulfill this full mapping scope yet.
- Choice/Option, User/Members, Jira Key URL for Test Set, comments, attachments, system timestamps, Status, Test Run and relational writes are **pending**. No full migration completion claim until these are implemented, tested and verified.
- Existing code must not be presented as a full migration. Do not run `--limit 0 --execute` expecting to complete all mapped data.
