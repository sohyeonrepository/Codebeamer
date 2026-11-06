# 1차 Jira → Codebeamer 데이터 Import — RUN_WINDOWS.bat

## Only user entry point

Run **RUN_WINDOWS.bat**. Use existing login and project selection. No separate BAT, mapping JSON preview, or explicit GET verification screen.

1. Select existing Jira and Codebeamer projects.
2. Connect Jira Issue Type to its Codebeamer Tracker with **연결 저장**.
3. Click **완료 · 데이터 가져오기**. Before fetching Jira fields, the UI inspects the selected CB Tracker for **exactly one single Text Jira Key field**. If none exists, an error says **"선택한 CB Tracker에 Jira Key 필드를 사용하세요"**, and field analysis is not started.
4. On **데이터 가져오기**, use **필드 검색** (Jira ID/name or CB target label), assign the correct CB fields and check the fields to import. Search is a display filter, not a scope reset.
5. Enter the exact Jira Issue key in **Jira Issue Key** and press **실제 Import 실행…**. This single action internally fetches the source values and destination FieldValue metadata, checks duplicates, and submits an actual POST **only if all non-null selected values are supported**. It verifies created Name, Description, Jira Key, selected custom Text/WikiText fields by GET.
6. See **3. Import 결과 / 로그** for created item ID or specific reasons for blocked fields.

### Current first-pass import scope

Existing successful Name, Description, Jira Key paths are retained. New selected **Text/WikiText** fields with confirmed Codebeamer Field IDs and exact `TextFieldValue`/`WikiTextFieldValue` type are included in the same POST, and their values are checked afterward.

Selected User, Choice, Status, Reference, system metadata, attachments and Xray relations are **not implemented in this first-pass write engine**. When a selected field has a nonempty unsupported value, the engine **fails before POST**, logging the Jira source field and reason. It does not silently discard values or treat omitted fields as migrated. Null selected fields are skipped without a write. For a preexisting Jira Key, the Item is never duplicated, but when additional custom fields are requested it reports pending update rather than claiming migration success.

**One exact Jira Issue per click**, not bulk; no ability to set or rewrite native history. The broad target of all source data, attachments, and relations remains incomplete.

### Removed from current UI

- "Test Set / Test Case · 제공한 매핑 파일" panel and its GET-only actions.
- "선택 필드 API 검증 (GET)" button.

The importer still uses GET internally to read metadata, check duplicates, and verify real POST success; it does not perform a separate GET-only validation workflow.

### Developer regression checks

```bat
python -m unittest discover -s tests -p test_first_import_fields.py -v
```

**Live server validation not performed in this GitHub change.** Do not assume a successful POST based solely on a successful test or commit.
