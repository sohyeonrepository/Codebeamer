# Windows: 필드 생성 POST 1회 테스트

기존 Import 대신 실행하는 별도 API 진단 도구입니다. Item 생성, 필드값 입력,
기존 `.migration` 초기화, 권한 일괄 변경은 하지 않습니다.

## 실행

1. 최신 `test/cb-item-post-minimal-20261106` 소스에서 기존 설정을 유지합니다.
2. `jira-xray-cb-migration/TEST_FIELD_POST_WINDOWS.bat`를 실행합니다.
3. `Failed journal JSON/ZIP full path:`에는 **최초 실패 기록** ZIP 또는 JSON
   전체 경로를 입력합니다. `field-diagnostic-*.zip`은 생성 정의가 없으므로 사용할 수 없습니다.
   예: `C:\자료\field-eb6ab42970c32b45b4983e22933c0f50ed795fb2ab852eb13149da997f50a650 (2).zip`
4. 테스트 Tracker ID를 입력합니다. 기존 사례는 `4874140`입니다. 운영 Tracker라면
   실행하지 마세요. 다른 Tracker의 기록은 템플릿으로 허용하지 않습니다.
5. 새 필드명은 기본 `Migration_API_Test_Labels`를 사용하거나 직접 입력합니다.
   이미 있는 이름은 POST 전에 차단합니다.
   확인된 Choice 경로에서는 신규 referenceId를 자동 선택합니다. 숫자 입력은
   필요 없습니다. 근거와 적용 조건은 [referenceId 안내](configuration-reference-id.md)를 참고하세요.
6. 환경변수에 인증이 없으면 ID/Password를 입력합니다. Password는 표시하지 않습니다.
7. 표시된 서버 주소, Tracker, 필드명을 확인하고 출력된 폴더의 `request.json`과
   `preview.json`을 검토합니다. 승인하려면 콘솔에 표시된 문구를 그대로 입력합니다.

```text
POST 4874140 Migration_API_Test_Labels
```

다르게 입력하면 POST 없이 취소됩니다. 승인 입력 전까지는 GET만 사용합니다.
Swagger가 없으면 POST하지 않습니다. 정확한 Swagger 파일을 지정하려면:

```bat
py -3 test_field_post_once.py --swagger "file:C:\자료\swagger.json"
```

## 요청 구성 및 검증

- 실패 기록에 보존된 신규 필드 정의를 사용하고 이름만 테스트용으로 바꿉니다.
- 기존 실패 요청 전체를 재전송하지 않습니다. 현재 Configuration을 GET하여
  기존 필드/권한/설정을 그대로 보존하고 신규 정의 하나를 추가합니다.
- 실제 서버 OpenAPI 요청 스키마를 검증하고, 승인 후 다시 GET하여 변경 여부를 확인합니다.
- POST는 1회만 실행합니다. HTTP 400, 타임아웃에도 자동 재전송하지 않습니다.
- 실행 결과가 실패여도 GET을 수행하여 실제 필드 ID와 기존 구성 보존 여부를 검사합니다.
- 옵션 ID 등 서버 정규화 때문에 신규 정의 비교가 다르면 성공을 확정하지 않습니다.

## 결과 파일

`diagnostics/post-once-<대상 식별값>/`에 저장됩니다.

| 파일 | 내용 |
| --- | --- |
| `configuration-before.json` | 생성 전 전체 설정 백업 |
| `request.json`, `preview.json` | 실제 POST 후보 및 변경 내역 |
| `attempt.json` | 중복 POST 방지 기록; 지우지 마세요 |
| `http-status.json` | 실제 HTTP 상태 코드 |
| `post-response.bin` | 서버 응답 본문 원문; JSON 오류는 메모장으로 확인 가능 |
| `configuration-after.json` | 생성 후 GET 응답 |
| `result.json` | 오류, 생성 Field ID, 기존 설정/권한 비교 결과 |

응답 원문과 Configuration에는 고객 데이터가 포함될 수 있습니다. 공유 전 확인하세요.
우선 `result.json`, `http-status.json`, 민감정보를 확인한 오류 응답 본문을 전달하면 됩니다.
타임아웃 등 응답을 받지 못한 경우 HTTP 상태/본문 파일은 없고 `result.json`에 기록됩니다.

같은 서버·Tracker·필드명으로 재실행하면 이전 시도 기록 때문에 POST를 차단합니다.
오류가 났다고 새 이름을 바꿔 반복 생성하지 마세요. 서버 로그 또는 실제 응답으로 원인을
확인한 후 다음 작업을 결정합니다. 로컬 파일 잠금은 이 PC/이 작업 폴더 범위이며,
다른 PC에서 동시에 같은 테스트를 실행하지 마세요.

## 테스트 범위

Mock 테스트: 승인 전 쓰기 차단, 생성 전 설정 변경 차단, HTTP 400 원문 저장,
실패 후 GET, 타임아웃 이후 재전송 금지, 재실행 차단, Field ID 확인, 기존 권한
보존 실패 검출. 실제 Codebeamer POST 및 Windows GUI 실행은 별도 검증 대상입니다.

자동 테스트 결과(참고 소스 자동 할당 반영 후): 전체 537개 실행, 531개 통과,
기존 GUI 6개 제외, 실패/오류 0개.

## RUN_WINDOWS.bat에 반영된 경로

`TEST_FIELD_POST_WINDOWS.bat`에서 검증한 POST 방식을 일반 Import 실행 경로에도 연결했습니다.

- `RUN_WINDOWS.bat` → `import_wizard.py` → `compact_wizard.py`의 **실제 Import 실행** 버튼
- 신규 필드 매핑은 저장만 하고 Import 승인 후 해당 Tracker의 설정을 다시 조회
- `configuration_field_contract()`에서 실제 `definition_body()` 결과와 OpenAPI를 검증
- `create_confirmed_field()`는 **POST /v3/tracker/configuration**을 `raw=True`로 1회 실행. 성공 응답이 비어 있어도 JSON 파싱 오류를 만들지 않음
- POST 뒤 Configuration GET으로 생성 Field ID/원래 필드와 권한 보존/정의 일치를 확인한 다음 Item Import
- POST가 실패하거나 결과가 불확실하면 pending을 보존하고 자동 재전송하지 않음

이 경로는 검증용 `test_field_post_once.py`의 실패 journal을 요구하지 않습니다. 다만 현재 Tracker에서 타입/권한/Reference ID 할당 계약을 확인할 수 있어야 하며, 이미 pending인 요청은 서버 대조가 먼저입니다. 별도 테스트 Tracker에서 POST가 성공한 사실만으로 운영 Tracker에 대한 쓰기 권한이나 생성 계약이 검증되지는 않습니다.

실행 절차: 최신 테스트 브랜치 코드를 받은 뒤 `RUN_WINDOWS.bat`를 실행하고, 기존 필드 매핑을 유지한 상태에서 신규 생성 예정 필드를 선택한 후 **실제 Import 실행**을 승인합니다. 실제 고객 서버에서의 전체 Import 성공 여부는 별도 검증이 필요합니다.
