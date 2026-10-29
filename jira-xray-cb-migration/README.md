# Jira DC + Xray → Codebeamer Python 마이그레이터

Python 3.10 이상, Windows/VS Code 지원. 외부 패키지는 필요하지 않습니다.
기존 저장소에는 README만 있어 API 클라이언트와 마이그레이션 엔진을 새로 작성했습니다.

**현재 상태:** 실행 가능한 엔진 및 모의 서버 검증 구현입니다. Codebeamer 실제 Swagger와 요청/응답에 맞는 설정을 입력하기 전에는 운영 이관에 사용할 수 없습니다. 실서버 쓰기 검증은 수행하지 않았습니다. 기본 설정으로 정상 완료를 보고하지 않습니다.

## 실행

PowerShell에서 이 디렉터리로 이동한 뒤:

```powershell
Copy-Item config.json config.local.json
python main.py diagnose-connection --config config.local.json --tracker-id 4862420 --prompt-auth
python main.py discover --config config.local.json --prompt-auth
python main.py migrate --config config.local.json
python -m unittest discover -s tests -v
```

Jira는 기본 Bearer 인증, Codebeamer는 기본 Basic(아이디·비밀번호) 인증입니다. `--prompt-auth`는 누락된 인증정보를 실행 중 입력받으며 토큰과 비밀번호를 화면에 표시하거나 파일에 저장하지 않습니다. 자동 실행 시 Jira는 `JIRA_TOKEN`, Codebeamer는 `CB_USERNAME`/`CB_PASSWORD` 환경변수를 사용하세요. Codebeamer 토큰 인증은 `auth: "bearer"`, `token_env: "CB_TOKEN"`으로 변경할 수 있습니다. HTTPS 및 OS/Python CA 검증을 유지합니다. 신뢰하는 사내 HTTP 서버는 해당 서버 설정의 `allow_http: true`로 명시적으로 허용할 수 있습니다. HTTP Basic 인증은 네트워크 구간에서 암호화되지 않으므로 가능하면 HTTPS를 사용하세요. 다른 서버로의 리디렉션 및 인증정보 전달은 거부합니다. 사내 CA는 신뢰 저장소 또는 지원되는 `SSL_CERT_FILE`로 제공하세요. 인증정보를 설정 JSON에 넣지 마세요.

`discover`: Jira 프로젝트·필드, Codebeamer Swagger 및 매핑된 트래커 설정/필드를 저장하는 읽기 전용 명령입니다. `codebeamer.url`과 실제 JSON Swagger URL인 `swagger_url`을 설정해야 합니다. 이미 받은 Swagger 파일은 `file:C:/path/openapi.json`도 지원합니다. OpenAPI에 나열되어 있다는 사실만으로 요청 스키마나 쓰기 권한이 검증되지는 않습니다.

`migrate`: 기본 dry-run. Jira 전체/지정 프로젝트를 조회하고 원본 JSON, 계획, 누락 매핑을 보관합니다. `jira.projects: []`는 Jira DC 전체 프로젝트 목록을 사용합니다. 실제 쓰기는 검증계에서 요청/응답을 확인한 후 아래처럼 **명시적으로** 실행합니다.

```powershell
python main.py migrate --config config.local.json --apply
```

실제 서버 작업은 자동 수행하지 않았습니다. dry-run도 소스 조회와 로컬 파일 저장은 수행합니다. 정상 완료는 실제 쓰기 후 필드·바이너리 검증이 통과하고 오류/미지원 항목이 없을 때만 가능합니다. dry-run/부분 이관/미지원은 종료 코드 2, 검증된 이관 완료는 0입니다.

## 실제 API를 설정하는 방법

`codebeamer.operations`는 실제 Swagger에 있는 **경로 템플릿과 HTTP 메서드**를 등록합니다. 알려지지 않은 수정 경로를 기본값으로 가정하지 않습니다. 다음은 설명용 구조이며 실제 Codebeamer 경로가 아닙니다.

```json
{
  "item_update": {
    "method": "PATCH",
    "path": "/ACTUAL_PATH/{itemId}",
    "body": {"name": "${payload.name}", "customFields": "${payload.customFields}"}
  },
  "lookup": {
    "method": "GET",
    "path": "/ACTUAL_SEARCH_PATH",
    "query": {"sourceId": "${sourceId}", "tracker": "${trackerId}"},
    "response_path": "items"
  },
  "item_get": {
    "method": "GET",
    "path": "/ACTUAL_PATH/{itemId}",
    "response_fields": {"name": "name", "customFields": "customFields"}
  }
}
```

- `${...}` 전체 문자열은 타입을 보존하는 치환입니다. 문자열 내부 보간은 하지 않습니다.
- `response_path`: 응답 envelope의 점 경로. `response_fields`: 정규화된 필드명 → 실제 응답 점 경로.
- 원본 ID 전용 필드를 쓰기 payload에 포함하고 `lookup`이 그 ID의 **정확한 일치**를 조회하게 설정하세요. 이름만으로 검색하면 안 됩니다.
- 원본 ID 필드의 서버 유일성을 권장합니다. 하나의 상태 디렉터리를 사용해 단일 프로세스로 실행하세요. 여러 프로세스/다른 상태 파일의 동시 이관은 지원하지 않습니다.
- 설정되지 않은 기능이나 Swagger에 없는 작업은 실패합니다. 필드 생성 실패를 성공으로 처리하지 않습니다.

프로젝트/트래커 재사용은 `migration.project_mapping[프로젝트키] = 대상ID`, `tracker_mapping["프로젝트키:IssueTypeID"] = 대상ID`를 사용합니다. 대상 ID를 GET으로 확인합니다. 신규 생성은 ID 대신 `{"create_payload": {...}}`를 지정하며 `project_lookup/create`, `tracker_lookup/create`가 필요합니다. payload는 해당 프로젝트별 실제 스키마로 작성해야 합니다.

필요 작업:

| 기능 | operations 키 | 주요 경로 파라미터 |
|---|---|---|
| 프로젝트/트래커 | `project_get/lookup/create`, `tracker_get/lookup/create` | projectId, trackerId, sourceKey |
| 아이템 | `lookup`, `item_create/update/get` | sourceId, sourceKey, trackerId, itemId |
| 필드 생성 | `field_create` | trackerId |
| 실제 History 조회 | `history` (GET만 허용) | itemId |
| 첨부파일 | `attachment_upload`, `attachment_get` | itemId, attachmentId |
| 댓글/작업로그/Test Run/관계 | `comment_*`, `worklog_*`, `run_*`, `relation_*` | itemId, recordId, sourceId; run은 trackerId, executionId, testId |

`*_lookup/create/get/update`는 각 레코드의 원본 ID 기반 Upsert와 재조회 검증에 사용합니다. 생성 응답은 `id`를 반환하도록 정규화하세요. lookup은 전체 결과 배열(0개 또는 1개)이어야 합니다. 페이지 제한 검색을 그대로 쓰면 중복 방지를 보장하지 못합니다. 첨부 업로드는 multipart이며 필요하면 `part_name`을 설정합니다. 다운로드는 실제 바이너리를 반환해야 합니다.

## 필드 및 사용자 매핑

`migration.field_mapping[IssueTypeID]`에 각 Jira 필드를 등록합니다.

```json
{
  "summary": {
    "target": "name", "type": "text", "field_id": 3,
    "expected_type_id": 0, "expected_mandatory": true,
    "expected_multiple": false, "expected_options": null,
    "expected_reference": null,
    "history_encoding": "scalar", "history_use_display": true
  }
}
```

위 타입 ID/속성은 설명용입니다. 실제 전체 필드 응답에 맞게 지정하세요. 타입·필수·다중·선택지·참조 설정이 미확인/충돌하면 멈춥니다. 누락 필드는 `create_payload` 및 검증된 `field_create`로 생성 후 다시 조회합니다. 자동 이름/타입 추측으로 필드를 덮어쓰지 않습니다. 배포별 필드 구조가 다르면 `compare_definitions` 어댑터를 조정해야 합니다.

변환: `text`, `number`, `boolean`, `date`, `datetime`, `raw`, `user`, `status`, `option`, `reference`. 사용자/상태는 `user_mapping`/`status_mapping`, 선택지/참조는 `value_mapping`으로 변환합니다. `raw`는 JSON 전체 보존용이며 대상이 같은 구조를 지원할 때 사용하세요. null은 누락시키지 않습니다. 미매핑 필드가 있으면 실패합니다. 별도 수집되는 attachment/comment/worklog/issuelinks는 해당 기능에서 처리합니다.

댓글·Worklog·관계는 `comment_adapter.fields`, `worklog_adapter.fields`, `relation_adapter.fields`에 같은 매핑 구조를 사용합니다. 원본 레코드의 모든 최상위 필드를 매핑해야 합니다. 대상이 네이티브 작성자/날짜나 Worklog를 지원하지 않으면 원본 JSON은 로컬에 보존되지만 미처리로 보고합니다. 필요하면 실제 대상의 별도 보존 필드를 매핑하고 재조회 비교하세요.

## Xray Test Run

Xray 스키마에서 관계 필드를 단일 식별하지 못하면 `jira.xray_run_field`에 실제 필드 ID를 설정합니다. `customfield_17778`을 기본 가정하지 않습니다. 현재 관계 배열 어댑터는 각 항목의 `testRunId`를 사용하며, 다른 응답이면 `XrayClient.runs`를 수정해야 합니다. 각 Test Run 전체 JSON은 보관합니다.

실제 생성/갱신은 `migration.run_adapter`로 설정합니다:

```json
{
  "tracker_id": 123,
  "test_issue_id_path": "testIssueId",
  "fields": {},
  "evidence_lists": [
    {"path": "evidences", "fields": {"id": "id", "filename": "filename", "size": "size", "content": "downloadUrl"}}
  ],
  "evidence_inventory_verified": false,
  "activity_inventory_verified": false
}
```

경로는 설명용입니다. `fields`에 원본 전체 필드와 엔진이 추가하는 `_testItem`, `_executionItem`을 매핑하세요. 상태·실행자·시각·소요시간·버전·steps/results·defects·environments 등은 실제 응답 필드 전체를 포함해야 합니다. Test Case가 먼저 생성된 후 Test Run 참조를 연결합니다. Evidence는 실제 바이너리를 전송하고 대상 재다운로드 SHA-256/크기를 비교합니다. Step별 증거 경로가 중첩 배열이면 배포별 추출 어댑터를 추가해야 합니다.

모든 Activity가 Jira changelog에 있다고 가정하지 않습니다. `*_inventory_verified`는 실제 수집 API와 전체 건수를 확인한 뒤에만 true로 바꿉니다. 알려진 API 실패를 우회해 임의 Activity를 만들어내지 않습니다. 별도 Activity API가 필요한 환경은 소스 수집 어댑터 추가가 필요하며, 기본 설정에서는 완전 이관으로 보고하지 않습니다.

## History Replay 진단

조회 확인:

```powershell
python main.py diagnose-history --config config.local.json --item-id 123
```

**폐기 가능한 검증계 아이템**에서 실제 확인된 수정 API/payload로 Revision 생성 진단:

```powershell
python main.py diagnose-history --config config.local.json --item-id 123 --probe-payload probe.json --apply
```

History POST 및 DB 직접 수정은 하지 않습니다. Revision 차이가 정확히 1개인지 확인합니다. 원본 변경자·시각의 네이티브 복원은 검증되지 않았으므로 성공으로 보고하지 않습니다. 진단 보고서에는 수정 전후 History 원본이 저장됩니다.

`migration.history_replay: true`는 최신 필드에서 변경 전 값을 역산해 초기 아이템을 만들고, 시간순으로 이벤트 하나당 수정 한 번을 수행합니다. `history_encoding: scalar`로 검증한 필드만 지원합니다. Jira의 from/to ID 및 display 문자열과 현재 JSON 구조가 다르므로 사용자/선택지 등의 복잡한 history는 실제 응답 기반 어댑터가 필요합니다. 하나의 이벤트가 두 Revision 또는 0개를 만들면 실패합니다. 이때 자동 재시도하지 않습니다. 원본 History ID → CB Revision 및 원본 작성자/날짜는 SQLite에 남기며, 네이티브 복원 미검증을 보고서 제한 사항으로 남깁니다.

Jira expanded changelog가 전체 total을 포함하지 않으면 이관을 중단합니다. 이 Jira DC 버전에서 확인되지 않은 changelog 페이지 API를 추측하지 않습니다. 전체 조회 API를 검증해 `JiraClient.bundle`을 확장해야 합니다.

## 보관·재실행·검증

`.migration/`에는 원본 JSON, 대상 응답, 바이너리, SQLite 매핑과 `report.json`을 저장합니다. **민감한 원본 데이터이므로 접근을 제한하고 Git에 올리지 마세요.** HTTP 감사 로그는 메서드, 경로 해시, 상태, 크기, 시간을 기록하며 URL 쿼리·헤더·본문·오류 응답은 기록하지 않습니다. 원본/대상 JSON 보관은 별도 목적이며 익명화되지 않습니다.

- 완료된 작업은 상태 DB를 재사용하여 중복 Replay/업로드를 막습니다.
- 쓰기 전 pending을 커밋합니다. 응답 유실 등 불확실한 작업은 재실행을 멈춥니다.
- `pending`은 대상 원본 ID 및 실제 데이터/Revision을 조회해 성공/실패를 확인한 뒤에만 SQLite 상태를 수동 복구하세요. 확인 없이 DB를 삭제하거나 pending을 done으로 바꾸면 안 됩니다.
- 상태 디렉터리는 소스/대상/이관 범위별로 분리하세요. 동일 상태로 다른 서버에 실행하지 마세요.
- 소스는 이관 중 고정하거나 쓰기를 중지하세요. 온라인 변경분 동기화/삭제 및 동시 실행은 지원하지 않습니다.
- issue 건수, 필드값, 각 child readback, History Revision, 파일 크기/해시를 검증합니다. 누락/실패/미지원 항목은 정상 완료가 아닙니다.

기본 설정은 쓰기 경로나 고객사 ID를 하드코딩하지 않으므로 실제 연결 없이 `migrate`하면 사전조건 오류로 종료되는 것이 정상입니다. 먼저 discovery 산출물에서 민감정보를 제거한 Swagger 및 대표 응답 구조로 어댑터를 완성하세요.


## 아이디·비밀번호 인증으로 첫 연결 확인

`config.json`을 `config.local.json`으로 복사한 뒤 `codebeamer`를 다음과 같이 설정합니다. URL에는 Markdown 링크 문법을 넣지 않습니다.

```json
{
  "url": "http://사내서버주소:8081/cb",
  "auth": "basic",
  "username_env": "CB_USERNAME",
  "password_env": "CB_PASSWORD",
  "allow_http": true,
  "swagger_url": "",
  "operations": {}
}
```

Jira의 `projects`는 `["STGVSTAR"]`처럼 문자열 배열로 지정하세요. `token_env`에는 실제 토큰이 아닌 `JIRA_TOKEN`이라는 환경변수 이름을 입력합니다.

```powershell
python main.py diagnose-connection --config config.local.json --tracker-id 4862420 --prompt-auth
```

이 Tracker ID는 지시서의 검증용 예시입니다. 실제 접근 가능한 ID로 바꾸세요. 명령은 Jira 토큰과 Codebeamer 아이디·비밀번호를 입력받고 Jira 필드, 대상 Tracker 설정·필드를 GET으로 조회합니다. **Swagger 주소 없이 실행할 수 있으며 대상 쓰기는 하지 않습니다.** `jira: ok`, `codebeamer: ok`이면 이 조회 범위의 인증/연결 확인에 성공한 것입니다. 전체 이관이나 쓰기 권한 검증을 뜻하지 않습니다.

HTTP 401은 인증 방식·아이디·비밀번호, 403은 권한, 404는 주소/컨텍스트 경로/Tracker ID를 우선 확인하세요. 저장되는 diagnostics 원본에는 업무 데이터가 포함될 수 있으므로 그대로 공유하지 마세요. 실패 시 인증정보를 제외한 콘솔 결과를 제공하면 다음 설정을 진단할 수 있습니다.

## STGVSTAR → 프로젝트 551: Tracker 직접 지정

기본 예제의 대상은 `migration.project_mapping: {"STGVSTAR": 551}`입니다. 다른 프로젝트에서는 변경하세요. 기존 로컬 `config.json`은 최신 ZIP으로 교체하기 전에 백업하세요.

Jira 이슈 유형별 Tracker ID를 `migration.tracker_mapping`에 지정합니다. 키는 `프로젝트키:Jira Issue Type ID` 또는 `프로젝트키:정확한 이슈 유형 이름`입니다. ID 설정이 이름 설정보다 우선합니다. 예를 들어 실제 Jira 이름이 Test라면 `"STGVSTAR:Test": 대상_Tracker_ID` 형식입니다. ID는 따옴표 없는 양의 정수여야 하며, 같은 이름의 유형이 여러 개면 ID를 사용하세요. Test Run Tracker를 모든 이슈의 대상으로 사용하지 마세요.

Jira 유형 ID를 모르면 다음 읽기 전용 명령으로 실제 목록과 설정 템플릿을 만듭니다. Codebeamer 인증이나 Swagger가 필요하지 않습니다.

```powershell
python main.py list-issue-types --config config.json --prompt-auth
```

`.migration/tracker-mapping-template.json`의 `tracker_mapping`에서 null을 대응 Tracker ID로 바꾼 뒤 config의 `migration.tracker_mapping`에 복사합니다. 프로그램이 원본 config를 덮어쓰지 않습니다. 이 명령의 `writes: 0`은 대상 데이터를 쓰지 않는다는 뜻입니다.

`field_mapping`도 이슈 유형 ID 또는 정확한 이름을 키로 사용할 수 있습니다. Tracker 설정만으로 필드 및 API 스키마를 검증한 것이 되지는 않습니다. 실제 이관에는 기존에 설명한 `operations`, 필드/사용자/상태 매핑, 해당 child 어댑터가 여전히 필요합니다. 설정 후 `migrate --config config.json --prompt-auth`의 오류를 해결한 다음 검증계에서 `--apply`를 사용하세요.
