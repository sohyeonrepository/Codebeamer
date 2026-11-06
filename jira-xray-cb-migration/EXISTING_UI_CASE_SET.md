# RUN_WINDOWS.bat — unified Test Set/Test Case UI

**Use the existing `RUN_WINDOWS.bat` only.** The old experimental `RUN_BATCH_CASE_SET.bat`, `START_TEST_SET.bat`, and standalone Python commands remain in the branch for developer/debug purposes but are **not** the user workflow.

## UI workflow

1. **RUN_WINDOWS.bat** opens the original Jira Token / Codebeamer username/password connection window. The Wizard keeps both authenticated clients in memory.
2. **1. 프로젝트 연결**: choose Jira project + CB project. Choose Jira `Test Set` and its existing CB Test Set Tracker, click **연결 저장**. Repeat for Jira `Test` and its CB Test Case Tracker. No numeric ID entry is needed; the UI dropdown holds the real selected IDs.
3. **2. 데이터 가져오기**: under `Test Set / Test Case · 제공한 매핑 파일`, click **Test Set + Test Case 매핑 한번에 검수 (GET만)**. This automatically selects up to one Jira Issue from each connected kind (Test Set first, Test second), loads `mappings/test_set.mapping.json` and `mappings/test_case.mapping.json`, fetches target tracker configuration, and reports source/target field coverage. No POST, PUT or PATCH.
4. **3. Import 결과 / 로그**: see the per-field mapping status. Unsupported fields are marked for additional validation; this report never means full migration is ready.
5. For an *existing proven limited Item smoke*, choose the desired single type/Tracker from the registered scope dropdown; in step 2 enter a Jira Key or leave blank to use first available issue, then click **선택한 유형 1건 생성 테스트 (부분 범위)**. A confirmation dialog explicitly states that only Name, Description, and a verified single Text Jira Key field are written. The actual POST uses `migration.simple_item_import.run` and existing Wizard-authenticated clients, including full Tracker Jira Key duplicate lookup. If the selected target has a `Wiki Link/URL` (not Text) Jira Key field, this smoke refuses the write instead of inventing a payload.
6. The **전체 이관** button remains disabled until every nonempty mapped field and separate relation/child work has validated write and readback contracts, as well as a safe resume story.

### Behavior when source data is empty

Read-only review shows empty source values separately from supported write contracts. A nullable mapped field is **not** evidence its API payload is supported. Do not report full coverage or consider unsupported field values imported.

### Existing button caveat

The original **실제 Import 실행…** button is unchanged. It uses checked Wizard fields and `simple_item_import`, **not** a full import driven by the supplied mapping files. For `Test Set` and `Test`, it displays a scope warning before any action.

### Developer test

```bat
python -m unittest discover -s tests -p test_connected_case_set.py -v
```

Live Jira/CB tests have not been run in this assistant session. Tests added to the repository are regression definitions, not proof of live success.
