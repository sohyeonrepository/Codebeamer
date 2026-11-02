# Codebeamer 3.0.0.7: 실제 서버 1건 생성 확인

**현재 상태:** 실제 CB URL/Swagger/인증/테스트 대상 ID가 제공되지 않아 이 저장소만으로 서버 호환성이나 생성 성공을 주장할 수 없습니다. Swagger의 `info.version`도 제품 빌드 3.0.0.7을 증명하지 않습니다. 서버 관리 화면의 버전과 해당 서버에서 받은 API 정의를 함께 확인하세요.

## 기존 Import 버튼은 실제 쓰기를 하는가?

`import_wizard.launch.import_now()`가 사용자 승인을 받은 뒤 `ImportWizard.execute(approved=True)`를 호출합니다. `execute()`의 사전 확인 통과 후 `cb.write=True`가 되고 `tracker_create`, 기존 Field helper, `Engine.migrate()`가 실행됩니다. `CodebeamerClient.call()`은 Swagger에 등록된 HTTP 메서드/경로/본문을 구성해 `Http.request()`에 전달합니다. `Http.request()`는 `urllib.request.Request(..., method=method)`로 네트워크 요청을 보냅니다. POST/PUT/PATCH는 실제 쓰기입니다. 성공을 가정하는 Mock 분기는 운영 코드에 없습니다. 하지만 사전 조건이 하나라도 충족되지 않으면 이 쓰기 경로에 도달하지 못합니다.

## 전체 Wizard의 차단 조건 및 필요한 정보

다음은 현재 전체 Import 경로의 차단 조건입니다. 생성 이후 검증 실패도 미완료로 보고되며 rollback 삭제는 하지 않습니다.

| 단계 | 차단 조건 | 필요한 설정/확인 |
| --- | --- | --- |
| 시작/인증 | config 파일/JSON 오류, URL 누락, Basic 사용자/암호 누락·사용자명 형식 오류, 미지원 인증, HTTP 정책·TLS·동일 출처/redirect 위반 | 실제 Jira/CB URL, 환경변수 또는 `--prompt-auth`, 신뢰할 CA 및 네트워크 접근 |
| Swagger/API | 정의 조회 실패·잘못된 문서, 미설정/중복 operationId, 실제 정의에 없는 메서드/경로, GET 계약 불일치, 누락 경로 변수·본문·응답 adapter | 해당 서버 Swagger JSON, 실제 operation과 요청/응답 계약 |
| 프로젝트/Tracker 목록 | 전체 배열이 아님, 실제 ID/프로젝트 소속 불일치 | `projects_get/project_get/trackers_get/tracker_get`, 전수 목록 adapter |
| STEP 2 | 프로젝트/유형/Tracker 미선택, 프로젝트 밖 Tracker, 새 이름 누락, 일부 유형 미분석, 실제 Issue 0건 | 프로젝트 전체 Issue Type, 선택 프로젝트의 실제 Tracker 목록과 매핑 |
| Field Configuration | API 미설정, 중복 필드 ID, visibility 미확인, 유형 필터 무시, 전수 조회/페이지 metadata 불완전 | `jira.field_configuration`, 실제 hidden boolean, Jira 검색 권한·전수 페이지 계약 |
| CB 필드 | 전체 필드/설정 배열 또는 Hidden 메타데이터 없음 | `fields`, `tracker_configuration_get` 또는 지원된 기존 경로의 실제 응답 |
| 매핑 | Hidden 원본 값/숨긴 대상 대응 위험, 미지원 타입/배열, 이름 모호·타입/다중 선택 충돌, 대상 중복·payload 키 중복, API key/binding 없음, 선택지·사용자 변환 누락 | `field_mapping`, 실제 타입 메타데이터/FieldValue binding, `user_mapping/status_mapping/value_mapping` |
| 상태/변경 | 다른 URL에 바인딩된 state, 분석 이후 원본/대상 변경 | 같은 환경 전용 state, 재분석 |
| 신규 Tracker | 템플릿 ID가 선택 프로젝트 밖, 이름 충돌, sourceKey 조회 불일치, uncertain 상태, 생성 ID·프로젝트 소속 재조회 실패 | `wizard.tracker_template_id/tracker_template`, 프로젝트 하위 `tracker_create`, `tracker_lookup` |
| 신규 Field | 생성 템플릿/binding 없음, 실제 타입/다중 선택 변경, API 모호·스키마 추가 필수값 불명, 기존 동명 필드, uncertain 상태, 생성 ID·타입·Choice 재조회 불일치 | `wizard.field_types`, 실제 Field 전용 POST 스키마·선택지 형식 |
| 전체 쓰기 preflight | `project_get/tracker_get/trackers_get/item_create/item_get/item_update/lookup` 미확인 | 실제 Item 생성/수정/조회와 원본 ID 완전 일치 조회 |
| 첨부 preflight | upload/download/lookup/메타데이터/URL API 미확인 | `attachment_upload/get/lookup/metadata_get/url_get` (Legacy JSON 보존에도 필수) |
| 댓글/관계 preflight | comment/association create/get/lookup 미확인 | `comment_lookup/create/get`, `association_lookup/create/get`, 변경 시 `*_update` |
| R4J | Revision/Baseline API 미설정·불완전 | `migration.r4j.revisions/baselines` 전수 읽기 계약. 전체 Wizard는 R4J를 무조건 요구하는 현재 제약이 있음 |
| 원본 수집 | Changelog 잘림·total 불일치·중복 ID, 댓글/작업로그 pagination 오류 | 완전한 changelog 또는 `jira.changelog_adapter`, 댓글/작업로그 읽기 권한 |
| 원본 커버리지 | 매핑 없는 실제 값, 예상 외 유형, structured Description, 인벤토리에 없는/중복 이름 이미지 | 실제 필드 매핑·지원 형식·완전한 첨부 목록 |
| Xray | 관계 필드 모호·Run 신원 불일치·이벤트/Activity 불완전·필수 참조/adapter 누락 | 기존 `xray_run_field/run_adapter/run_events/test_mapping` 등 실제 계약 |
| full-Xray | 4종 유형/관계 설정 누락, 관계 대상 미수집, 생성 후 관계 쓰기·검증 실패 | Plan/Test/Set/Execution과 전체 의존성, `full_xray.relationships/relation_adapter` |
| Item 쓰기 | 필드 정의 충돌, 원본 ID lookup 0/1건 계약 위반, state/대상 ID 불일치, uncertain POST, 생성 결과 ID 없음, 현재값 재조회 불일치 | 실제 외부 ID 유일성·원본 ID 저장, `item_create/get/update`, 검증된 필드 정의 |
| Native replay | 역사 필드/인코딩 adapter 누락, revision 증가가 정확히 1개가 아님 | 명시적 `wizard.native_replay`, 기존 scalar history adapter. 원본 작성자/시간 복원은 여전히 미검증 |
| 댓글/첨부/History 보존 | uncertain 작업, 원본/대상 크기·파일명·해시·JSON·건수 불일치, 댓글 본문 재조회 불일치, 링크 변환 실패 | 정상 응답과 실제 바이너리 재다운로드, 변경 시 update API, 상태 대조 |
| Association | Link Type 대응 없음, 대상 Item ID 미준비, 방향/원본 ID 불일치·재조회 실패 | `wizard.association_types`, 전 Item ID 및 실제 CB Association 계약 |
| 완료 판정 | 위 오류·limitations·미검증 Item 존재 | `report.json`의 오류와 limitations를 해결해야 전체 완료 |

이전 전체 Wizard는 단일 Issue 선택 기능이 없었으며 많은 부가 기능을 필수로 요구했습니다. 이번 변경으로 전체 엔진에도 단일 Issue 범위 제한을 추가했습니다. 아래 모드는 **전체 이관을 우회해 성공했다고 보고하는 모드가 아니라**, 별도로 한정된 `single-issue-create-check`입니다. 전체 이관 완료는 항상 `full_migration_complete:false`입니다. 첨부/댓글/관계/History/R4J/Xray의 API 미검증 상태와 이 테스트에서 실행하지 않는 이유를 보고서에 명시합니다.

## UI에서 실제 대상 선택 (ID 하드코딩 없음)

```sh
python import_wizard.py --config config.local.json --live-test --test-state-dir .migration/live-check --prompt-auth
```

Jira/CB URL, 실제 Swagger JSON URL(또는 자동 탐색)을 입력하고 `연결 / 실제 프로젝트 조회 (GET)`를 누릅니다. 기존 테스트 프로젝트와 Tracker를 목록에서 선택하고 Jira Issue 키 1개를 입력합니다. 다음 요청 확인 화면에서 사전 확인 후 `Import…`를 승인합니다. 인증정보는 기존 환경변수 또는 실행 터미널의 비표시 입력을 사용합니다. Swagger UI `{CB_BASE_URL}/v3/swagger/editor.spr`는 JSON 자체가 아니므로 JSON URL란에 HTML을 넣지 마세요.

## 기존 전체 Wizard로 Issue 1건만 이관

`codebeamer.profile: "3.0.0.7"`을 설정하고 기존 Wizard를 실행합니다. STEP 2에서 프로젝트/기존 테스트 Tracker를 연결한 뒤 STEP 3의 Jira Issue 키 칸에 1개를 입력하고 `이 Issue 1건으로 전체 Import 범위 제한`을 누릅니다. 해당 유형만 다시 분석한 뒤 Import를 승인합니다. 기존 `migration/wizard.py`와 `Engine`을 재사용하며 Jira 프로젝트 검색 대신 해당 Issue 직접 GET으로 제한합니다. 다른 Item에 대한 관계, 별도 Tracker의 Xray Run, 기존 source ID 일치 Item, R4J 등 미확인 필수 API가 있으면 전체 이관이 차단됩니다. 이 단일 테스트 경로는 기존 Item을 갱신하지 않으며 생성 Item의 실제 Tracker ID를 확인한 뒤 후속 처리를 허용합니다. full-Xray 모드는 전체 의존성이 필요하므로 단일 Issue 테스트에서 명시적으로 차단합니다. 신규 Tracker가 필요한 별도 생성 검증은 아래 생성 API 확인 모드를 사용합니다.

## 최소 설정

기존 `config.local.json`에 실제 Jira/CB URL·인증 이름을 설정합니다. 비밀번호/토큰은 JSON에 넣지 않습니다. 기본 CB URL은 API 경로를 붙이기 전 서버의 올바른 context root여야 합니다.

```json
{
  "jira": {
    "url": "https://YOUR-JIRA",
    "auth": "bearer",
    "token_env": "JIRA_TOKEN"
  },
  "codebeamer": {
    "url": "https://YOUR-CB/ACTUAL-CONTEXT",
    "profile": "3.0.0.7",
    "auth": "basic",
    "username_env": "CB_USERNAME",
    "password_env": "CB_PASSWORD",
    "swagger_url": "https://YOUR-CB/ACTUAL-SWAGGER-JSON-URL",
    "operations": {}
  },
  "migration": {},
  "single_import": {"item_payload": {}}
}
```

`file:/절대경로/openapi.json`도 지원하지만 파일은 해당 서버에서 받은 정의여야 합니다. `--prompt-auth`는 Jira 토큰과 CB 사용자·암호를 비표시 입력으로 받습니다. 환경변수가 있으면 재사용합니다.

`3.0.0.7` 프로필은 제공된 공식 경로가 **연결 서버 Swagger에 실제로 있을 때만** 등록합니다. `POST /v3/tracker/configuration`, `GET /v3/tracker/{trackerId}/configuration`, Item 생성/조회/수정·History·Comment·Association·첨부 목록이 이에 해당합니다. 모든 JSON 쓰기는 실제 request schema로 검증합니다. 없는 경로는 대체 경로로 성공 처리하지 않습니다. 이 프로필만으로 upload/lookup 등 목록에 없는 API를 검증했다고 표시하지 않습니다.

프로젝트/Tracker 조회 등은 실제 Swagger에 유일하게 존재하면 다음 operationId 후보를 찾아 연결합니다: `getProject`, `getTracker`, `getTrackerFields`(Tracker 경로만), `getTrackerItem`, `createTrackerItem`, 선택적 `createTracker`. 이름이 다르거나 모호하면 차단합니다. 보고서의 `api-capabilities.json`에서 실제 method/path/operationId/생성 스키마를 확인해 `codebeamer.operations`에 지정하세요. 예시는 이름만 설명하며 3.0.0.7에서 확인된 사실로 간주하지 않습니다.

```json
{"item_create": {"operationId": "실제-Swagger에서-확인한-ID"}}
```

이 모드는 임의 body/path override를 거부하고 Swagger-native JSON을 사용합니다. GET의 `response_path/response_fields`로 실제 응답을 정규화할 수 있습니다. 필요한 정규화 형태:

- project: `id`
- tracker: `id`, `project.id`
- fields: 지정 Tracker 필드의 **전체 배열**
- item: `id`, `tracker.id`, `name`, `description`
- create: 실제 `id`

## 실행 순서: 기본은 기존 테스트 Tracker에 Item 1건만

아래 `TEST-123`, `551`, `4862420`은 예시입니다. **반드시 본인이 지정한 테스트 값으로 교체**하세요.

```sh
cd jira-xray-cb-migration
python import_wizard.py --config config.local.json --single-issue TEST-123 --test-project-id 551 --test-tracker-id 4862420 --test-state-dir .migration/check-TEST-123 --preflight-only --prompt-auth
```

이 명령은 GET만 수행합니다. Jira 프로젝트 검색이나 Issue 전체 조회를 하지 않습니다. 지정 Issue를 직접 GET하고 실제 CB 프로젝트/Tracker/필드를 GET합니다. Tracker의 프로젝트 소속과 Item POST 스키마를 확인합니다. 출력 `blockers`가 비어야 다음 단계로 갑니다. 인증·네트워크·API 계약 오류는 성공으로 처리하지 않습니다. 종료코드 2는 차단, 0은 **읽기 준비만** 통과했다는 뜻입니다. POST 권한이 있다는 증거는 아닙니다.

`single_import.item_payload`에는 Swagger가 요구하는 실제 status/required customFields 등 추가 필수값을 넣습니다. `name`은 Jira Summary로 채우며 `description`에는 원본 Description과 이 테스트의 고유 표식을 저장합니다. 필수 필드/enum/다형 스키마를 확인할 수 없으면 요청을 보내지 않고 차단합니다. 기존 Item ID·Tracker/프로젝트 덮어쓰기·상위/하위 관계 payload는 금지합니다.

같은 명령에서 `--preflight-only`만 빼고 실행하세요.

```sh
python import_wizard.py --config config.local.json --single-issue TEST-123 --test-project-id 551 --test-tracker-id 4862420 --test-state-dir .migration/check-TEST-123 --prompt-auth
```

1. `읽기 전용 사전 확인 / 요청 보기`를 누릅니다.
2. `target`, `creation_scope`, `blockers`, 실제 POST `method/path/body`를 확인합니다.
3. `Import…`를 누르고 지정 Issue/Tracker를 확인한 뒤 승인합니다.
4. 실제 Item POST와 Item GET이 실행됩니다. GET의 Item ID·Tracker ID·Name·Description이 일치해야 이 **생성 확인**만 complete가 됩니다.

기존 Item에 PUT/PATCH하지 않으며 삭제도 하지 않습니다. 테스트가 생성한 Item도 자동 삭제하지 않습니다. 기존 데이터가 들어 있는 Tracker라도 지정한 테스트 Tracker 안에 새 Item만 생성합니다. 모든 필드/History를 이관했다고 표시하지 않습니다.

## Tracker/Field 생성 API도 확인할 때

기존 테스트 Tracker 재사용 시에는 Tracker를 새로 생성했다고 표시하지 않습니다. Field도 기본적으로 기존 필드를 조회할 뿐 생성했다고 표시하지 않습니다.

Tracker/Field POST까지 시험하려면 **그 서버 Swagger에서 확인한** JSON을 `single_import.tracker_payload` / `single_import.field_payload`에 명시합니다. 자동으로 typeId나 필수 속성을 추측하지 않습니다. `tracker_create`는 지정 프로젝트 하위 POST 또는 프로젝트 ID가 검증된 공식 Configuration POST, `field_create`는 지정 테스트 Tracker 하위 POST 또는 전체 기존 설정을 보존하는 Configuration POST여야 합니다. 기존 ID를 넣을 수 없고 Field는 새로운 이름과 `mandatory:false`가 필수입니다. 이 선택적 Tracker 생성은 별도의 생성 API probe이며, Jira Issue의 대상은 계속 CLI에서 지정한 기존 테스트 Tracker입니다.

실제 Field 전용 POST가 없고 공식 `POST /v3/tracker/configuration`이 확인되면, 기존 `create_confirmed_field()`를 재사용합니다. 전체 설정을 GET하고 기존 필드·Permission을 유지한 상태에서 새 정의 하나만 추가합니다. 쓰기 직전 설정 동일성을 검사하고 이후 기존 필드의 모든 속성과 생성 Field ID/속성을 재검증합니다. `field_payload`에는 현재 helper가 요구하는 실제 `typeId/mandatory/multipleSelection/options/reference/permission`이 필요합니다. 전체 JSON 스키마 또는 기존 설정을 확인할 수 없으면 차단합니다.

Tracker Configuration 생성은 기존 Wizard에서 실제 템플릿을 GET하고 basicInformation의 trackerId를 제거한 뒤 선택 프로젝트 ID·이름을 적용합니다. 프로젝트 속성과 새 Tracker ID 처리 방식도 실제 Swagger에 정의되어 있어야 합니다. 필수 trackerId가 non-null로만 정의돼 신규 생성 계약을 확인할 수 없으면 차단합니다. 생성 API 확인 모드에서 `tracker_payload`를 지정할 때에도 전체 native configuration과 선택 프로젝트 ID가 필요합니다. 기존 설정 변경 요청을 신규 생성으로 취급하지 않습니다.

## 결과 확인

지정 state 디렉터리에 다음을 남깁니다.

- `single-import-report.json`: 요청·차단 사유·선택 범위·실제 생성 Tracker/Field/Item ID와 GET 응답, 기존 객체를 재사용했는지 여부
- `api-capabilities.json`: 해당 Swagger의 실제 경로/메서드/operationId와 생성 요청 스키마
- `discovery/`: 서버에서 읽은 원본 Swagger
- `requests.jsonl`: 실제 HTTP 상태·시간 등 기존 비밀값 제외 감사 기록
- `state.sqlite3`: 생성 요청 journal. 같은 state로 다시 눌러도 완료된 POST를 반복하지 않습니다.

응답이 유실돼 생성 ID가 불명확하면 자동 POST 재시도를 막고 원인을 표시합니다. 상태 디렉터리를 삭제해 재시도하지 말고 서버에서 먼저 대조하세요. 생성 ID가 있더라도 재조회가 실패하면 complete=false입니다. 사전 GET 성공·Swagger 스키마 통과·오프라인 테스트는 실제 서버 쓰기 성공으로 취급하지 않습니다.

## 연결을 누르면 응답 없음이 발생하는 경우

실제 대상 선택 화면의 Swagger/프로젝트/Tracker 목록 조회는 백그라운드 읽기로 실행합니다. 요청당 10초, 전체 작업 30초 제한을 적용하고 대기 시간과 취소 버튼을 표시합니다. 취소·시간 초과 이후 늦은 결과는 반영하지 않습니다. 인증 입력은 터미널 대기가 아니라 화면의 입력 창에서 받습니다. 이 수정은 연결 화면용이며 Import 실행의 쓰기 결과를 임의로 취소·재시도하지 않습니다. Swagger 조회 실패 시 마지막 HTTP/전송 오류도 표시합니다. 목록이 비어도 연결 성공으로 간주하지 말고 하단 실패/시간 초과 메시지를 확인하세요.

## 필드 매핑 · 첨부파일 1건 테스트

`--live-test`로 연결하고 기존 테스트 프로젝트·Tracker와 Jira Issue 키 1개를 선택한 다음,
생성 확인 화면의 **필드 매핑·첨부 1건 테스트**를 누릅니다.

1. **사전 조회 / 요청 확인**을 누릅니다. Summary → Name과 Description → Description을 기본으로 준비합니다.
2. 왼쪽 CB 필드와 오른쪽 Jira 필드를 선택하고 실제 원본 값 타입을 지정한 뒤 **매핑 저장**을 누릅니다.
   문자열·숫자·Boolean·날짜만 이 제한 테스트에서 지원합니다. 사용자/Choice/참조 및 Xray 테이블을 기본 타입으로 바꾸지 않습니다.
   CB 필드는 실제 Tracker 필드와 생성 요청 스키마 또는 기존 `field_bindings`에서 확인된 것만 표시합니다.
   Jira 후보는 선택한 Issue 응답에 포함된 필드이며 null 값도 유지합니다. Field Configuration adapter가 있으면
   Hidden 필드를 제외하고 남아 있는 값을 위험으로 보고합니다. 없으면 `source_visibility`에 미확인이라고 표시합니다.
3. 사전 조회를 다시 실행합니다. `field_mapping`, `unmapped_source_fields`, `attachment_inventory`,
   `attachment_api`, `requests`, `blockers`를 확인합니다. 첨부 목록에는 실제 다운로드한 원본 SHA-256을 표시합니다.
4. `blockers`가 비어 있으면 **Import…**를 눌러 실제 생성을 승인합니다.
5. `item_id`, `mapped_fields_verified`, `attachments`, `attachment_counts`를 확인합니다.
   첨부파일은 실제 CB 다운로드 바이트의 SHA-256/크기와 목록의 ID/파일명/크기가 모두 일치해야 통과합니다.
   Description 내부 첨부 참조는 다운로드 API URL로 변환하고 Item을 다시 조회합니다. CB 화면의 이미지 렌더링도 직접 확인하세요.
6. CB에서 생성된 Item과 파일을 열어 확인합니다. **결과 / 오류 복사**로 전체 보고서를 복사할 수 있습니다.

이 모드는 이전 Summary/Description 생성 확인 Item을 수정하지 않습니다. 기존 상태 폴더 아래
`mapped-attachments/`에 별도 기록하고 새 테스트 Item 1건을 생성합니다. 같은 폴더를 유지하면 재실행 시
기록된 Item/첨부 ID를 재사용하고 서버에서 다시 검증합니다. 결과가 불명확한 POST는 자동 재전송하지 않으며
`reconciliation` 오류로 차단합니다. 이때 상태 폴더를 지우고 재시도하면 중복이 생길 수 있으므로 지우지 마세요.

바이너리 multipart 필드명은 서버 Swagger에서 확인합니다. 업로드·목록·다운로드 경로가 없거나 추가 필수
multipart 항목이 있으면 Item 생성 전에 차단합니다. 서버별 adapter가 필요한 경우 `blockers`에 capability를 표시합니다.
기본 API 후보는 `/items/{itemId}/attachments` (GET/POST),
`/items/{itemId}/attachments/{attachmentId}/content` (GET)이며 서버 Swagger에 있을 때만 사용합니다.
`/v3` 경로가 함께 있으면 V3를 선택합니다. CB의 업로드 응답은 ID 객체 또는 정확히 1개인 배열,
첨부 목록은 전체 배열이어야 합니다. 다른 응답 envelope는 명시적인 `response_path` 설정이 필요합니다.

성공은 선택한 필드와 첨부파일에만 해당합니다. 미매핑 필드, 신규 Field/Tracker 생성,
Comment/Association/History/R4J/Xray 전체 이관 성공을 의미하지 않습니다. 기존 전체 Wizard는 그대로 유지합니다.
네트워크 처리는 백그라운드에서 실행하며, 실제 쓰기가 진행 중이면 완료 결과를 받은 뒤 창을 닫습니다.
