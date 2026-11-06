# Existing RUN_WINDOWS.bat: Test Set / Test Case mapping in the same UI

**Updated compact Wizard** (`compact_wizard.py`) now includes a Test Set/Test Case mapping preview panel in Step 2. You do not open `RUN_BATCH_CASE_SET.bat`, re-enter CB account credentials, or manually type Codebeamer project/Tracker numeric IDs.

1. Start `RUN_WINDOWS.bat`.
2. Login in the existing connection screen (Jira token, CB username/password). No credentials are copied into config or state.
3. In **1. 프로젝트 연결**, select Jira project and Codebeamer project, then choose Jira **Test Set** and its existing Codebeamer Tracker, click **연결 저장**. To preview Test Case, choose **Test** and its existing CB Test Case Tracker instead.
4. Open **2. 데이터 가져오기**. Under **Test Set / Test Case · 제공한 매핑 파일**, click **자동으로 1건 조회 (GET)** to choose the first available Issue automatically, or enter a known Jira Issue Key and click **매핑 Config 미리보기 (POST 없음)**.
5. In **3. Import 결과 / 로그**, review each source field, target field, presence of source values, and unverified contracts.

The existing **실제 Import 실행…** button now explicitly warns Test Set/Test Case users that it does NOT consume the entire supplied JSON mapping.\n\nThe new preview reads the provided mapping JSON files, Jira Issue and CB Tracker config using the **same authenticated wizard.jira.http / wizard.cb.http clients**. It does not use `config.local.json` credentials again and does not require an independent CLI or subprocess. It explicitly indicates that this is **not** full coverage and does not write any Items.

**Important:** The compact Wizard's existing **실제 Import 실행…** button is still its original separately validated single-Issue engine, **not** the new batch/full-mapping engine. Do not assume that clicking it performs every row of the Test Set/Test Case JSON. The new mapping preview is deliberately GET-only until missing contracts, user/choice/reference mappings and existing-item reconciliation have been verified against the target server.

For local logic tests:

```bat
python -m unittest discover -s tests -p test_connected_case_set.py -v
```

Existing `RUN_WINDOWS.bat` entry point and authentication/project-selection behavior are preserved.
