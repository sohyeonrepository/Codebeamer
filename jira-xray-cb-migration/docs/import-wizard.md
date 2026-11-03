# STEP 3 표시 기준 (최신)

표는 `Jira 필드`와 `Codebeamer 대상 필드` 두 컬럼만 사용합니다. 저장된 매핑은 대상 이름과 ID를
표시하고, 저장된 매핑이 없으면 빈칸입니다. 분석 시 새로운 대상을 자동 선택하거나 생성 대상으로 결정하지 않습니다.
빈칸과 기존 대상 모두 클릭하여 실제 Tracker 필드 목록 또는 `＋ 신규 필드 생성`을 선택할 수 있습니다.
생성 설정 저장 후에는 `입력한 이름 (신규 생성 예정)`, 실제 생성·재조회 후에는 이름과 Field ID를 표시합니다.

기존 매핑→신규 생성 계획, 신규 생성 계획→기존 필드, 신규 생성 계획 수정이 가능합니다.
상태별 필터와 상태/작업 컬럼은 제거했습니다. 내부 생성 단계/타입/중복/쓰기 계약 검증은 유지합니다.
불가능한 선택을 목록에서 숨기지 않고 이유를 표시하며, 아직 결과가 불확실한 POST를 새 설정으로
재실행하는 경우에는 중복 방지를 위해 먼저 서버 대조가 필요합니다. 이 제한은 매핑 UI의 자동 판단이
아니라 이미 전송된 생성 요청의 중복 방지입니다.

이전 문서의 자동 매핑·상태 필터 설명보다 이 표시 기준이 우선합니다.

---

# STEP 3 Field API 연결 업데이트

STEP 3은 Jira 필드명 / Codebeamer 대상 두 컬럼입니다. CB 대상 셀을 클릭하면 실제 Field ID와
이름·타입이 있는 호환 후보 드롭다운이 열립니다. 일반 필드는 선택 즉시 저장하고 Choice는 값 대응을
확인한 뒤 저장합니다. 매핑 상태·작업 컬럼은 표시하지 않습니다. 매핑과 신규 생성 설정은 상태 폴더에
저장하며 같은 환경/상태 폴더로 재실행하면 복원합니다.

선택한 Tracker만 `GET /v3/trackers/{trackerId}/fields`, 각 ID의 `.../fields/{fieldId}`,
`GET /v3/tracker/{trackerId}/configuration`으로 조회합니다. `name`, `type`, `hidden`, `valueModel`,
`multipleValues`, `options`, `referenceType` 원본을 보관하고 기존 엔진 형식과 병합합니다.
예를 들어 `OptionChoiceField`는 Choice, `multipleValues`는 다중 선택 여부로 해석합니다.
상세 조회 실패 시 필드명은 유지하고 HTTP 오류를 표시합니다. Hidden이 불명확한 CB 필드로 실제 Import는 차단합니다.

Jira CreateMeta는 선택 프로젝트와 Issue Type으로 제한합니다. 화면 필드와 실제 값이 있는 추가 필드를
포함합니다. CreateMeta가 불가능하면 기존 Issue 필드 조회로 대체합니다. 어느 경우에도 CreateMeta를
Hidden 판정 근거로 사용하지 않습니다.

신규 생성 팝업은 서버의 Configuration과 Field Detail에서 타입 후보를 얻고 기존 생성 계약 검증 함수를
재사용합니다. `wizard.field_types`가 비어 있어도 확인 가능한 템플릿을 제공합니다. 다만 numeric typeId,
권한 또는 생성 schema가 확인되지 않은 타입은 활성화하지 않고 원인을 표시합니다. FieldValue 미확인은 별도 값 쓰기 제약으로 표시하며 생성 타입을 제외하지 않습니다.
`valueModel` 문자열만으로 쓰기 형식을 단정하지 않습니다. 서버 schema의 discriminator 및 value/values
속성이 확인되어야 자동 binding을 사용합니다. 설정 저장은 로컬에서만 수행하고 실제 POST는 Import 승인 후 실행합니다.

이번 테스트는 사용자가 제공한 문서 응답 형식을 fixture로 사용했으며 실제 고객 서버 응답을 수집한 것은 아닙니다.
실제 Field 생성·재조회 및 Item 값 적용은 승인된 고객 테스트 서버에서 추가 확인이 필요합니다.

---

# Jira → Codebeamer Import Wizard

```sh
cd jira-xray-cb-migration
python3 import_wizard.py --config config.local.json --prompt-auth
```

Python 3.10+와 Tkinter를 사용합니다. 기존 `mapping_helper.py`, CLI, Run-only, full-Xray 기능은 유지됩니다. Wizard는 Run-only 기본 설정을 **선택한 프로젝트의 Issue 이관**으로 전환하며, 명시적인 `full-xray` 설정은 유지합니다. 인증은 기존 환경변수/비표시 입력을 재사용합니다. 실제 서버 쓰기 테스트는 자동으로 수행하지 않습니다.

## 화면과 승인 경계

1. **STEP 2**: 프로젝트 조회 → Jira와 기존 CB 프로젝트 선택 → Issue Type / Tracker 조회 → 유형마다 기존 Tracker 또는 신규 Tracker 이름 연결. Issue가 없는 유형도 전체 목록에 포함됩니다. 여러 Jira 프로젝트도 차례로 설정할 수 있습니다.
2. **STEP 3**: 저장한 Tracker ↔ Issue Type 조합 선택 → 분석. 왼쪽 Jira / 오른쪽 CB 대상의 두 컬럼이며, 선택한 행의 사유는 하단에 표시됩니다. CB 셀을 클릭하면 대상 드롭다운을 열고 Choice 값은 추가로 대응시킵니다. 신규 필드는 생성안으로만 표시됩니다.
3. **Import…**: 명시적 승인 후에만 쓰기를 켭니다. 분석 이후 변경이 있으면 재분석을 요구합니다. API와 원본 데이터를 사전 확인한 뒤 Tracker/Field를 준비하고 기존 Engine으로 전달합니다. 결과 탭에서 실제 조회·검증·오류를 확인합니다. 종료 시 쓰기 권한은 다시 꺼집니다.

조합 매핑은 `state_dir/wizard/`에 저장됩니다. 실제 Field ID를 반영한 `resolved-mapping`은 재시작 시 자동으로 읽습니다. 프로젝트 변경은 그 프로젝트의 이전 조합을 무효화합니다. 다른 소스/대상 URL에는 같은 상태 디렉터리를 사용할 수 없습니다. 기존 데이터 삭제와 DELETE 요청은 허용하지 않습니다.

STEP 3 화면은 선택한 `Jira Issue Type → Codebeamer Tracker` 조합 하나만 분석하고 표시합니다. 왼쪽은
Jira API의 실제 필드명, 오른쪽은 Codebeamer 대상 필드이며 custom field는 실제 이름을 먼저 표시하고 ID를 함께
표시합니다. 기존 필드는 Jira/CB 메타데이터로 타입 호환성을 검사하므로 사용자가 Field Type을 선택하지 않습니다.
자동 매핑·수동 매핑·신규 생성·제외 상태와 검색/필터를 제공합니다. Jira Hidden 필드는 `제외`에 표시하고,
실제 값이 남아 있으면 별도 누락 위험으로 표시하여 Import를 차단합니다.

기존 CB 필드가 없는 행에만 `＋ 신규 필드 생성` 작업이 나타납니다. 팝업의 이름·검증된 타입·다중 선택·Choice
옵션은 생성 계획으로만 저장되며 이 단계에서는 서버 쓰기를 하지 않습니다. 실제 Tracker/Field 생성은 사용자가
마지막 `다음 (Import 승인)`에서 승인하고 쓰기 직전 분석 결과가 동일한지 다시 확인한 뒤 실행됩니다.
Attachment·Comment·Association·History/R4J/Xray 처리는 기존 Migration Engine 경로를 그대로 사용합니다.

기본 전체 Wizard 실행 시 첫 화면에서 Jira 서버 URL·API 토큰, Codebeamer 서버 URL·아이디·비밀번호를
입력합니다. 인증정보는 메모리에만 유지하고 config/state/report/log에 저장하지 않습니다.
`연결 / 실제 프로젝트 조회 (GET)`은 30초 제한과 취소 기능이 있는 읽기 전용 연결이며, 성공한 프로젝트 결과를
STEP 2에 그대로 전달합니다. 프로젝트·Tracker·Field 조회는 Swagger 탐색과 분리되어 있습니다. 연결 화면에서
입력한 URL과 인증은 이후 분석 및 승인된 Import에도 같은 세션에서 사용합니다.

### 실제 성공 조회 코드 복구 기준

고객 서버에서 Summary/Description Item 1건 생성까지 확인된 기준은 커밋 `e1e694b`입니다. 이때 사용한
`launch_live_setup.read_operation`, `launch_live_setup.load_trackers`, `SingleImport.prepare`,
`CodebeamerClient.fields`, `CodebeamerClient.configuration`의 조회 계약을
`clients/codebeamer_verified_reads.py`로 분리해 전체 Wizard에서 재사용합니다.

연결 및 STEP 2/3 조회는 Swagger 자동 탐색을 실행하지 않습니다. Codebeamer Basic 인증 세션으로 다음 GET만
사용하며 응답 계약이 다르면 성공 처리하지 않습니다.

| 조회 | Method와 API path | 필요한 응답 |
| --- | --- | --- |
| 프로젝트 목록 | `GET /v3/projects` (`GET /projects` 서버 alias 지원) | 정수 `id`가 있는 전체 배열 |
| 프로젝트 | `GET /v3/projects/{projectId}` | 선택한 동일 `id` 객체 |
| Tracker 목록 | `GET /v3/projects/{projectId}/trackers` (`/projects/...` alias 지원) | 정수 `id`가 있는 전체 배열 |
| Tracker | `GET /v3/trackers/{trackerId}` | 동일 Tracker ID와 `project.id` |
| 필드 | `GET /v3/trackers/{trackerId}/fields` | 실제 Field ID가 있는 전체 배열 |
| 설정/Hidden | `GET /v3/tracker/{trackerId}/configuration` | `fields` 전체 배열 |

성공한 path는 현재 연결 세션에 기록하여 이후 같은 조회에서 먼저 재사용합니다. Jira 프로젝트·Issue Type·필드
조회는 기존 `JiraClient`의 Bearer 인증과 REST API를 그대로 사용합니다. 이전 성공 실행이 저장한
`discovery/single-openapi`는 SQLite의 `single-import-binding` URL이 현재 Jira/CB URL과 정확히 같은 경우에만
쓰기 계약 증거로 재사용합니다.

Swagger/OpenAPI는 `다음 (Import 승인)`에서 승인 창을 띄우기 전에 쓰기 계약을 검증할 때만 사용합니다.
최소 Item 1건에는 Tracker 범위의 Item POST request schema, 성공 response, Item GET이 필요합니다. 신규 필드가
있으면 전체 Tracker Configuration POST schema도 필요합니다. 계약이 없으면 조회 결과는 유지하면서 필요한
Item/Field/Attachment/Comment/Association/History 계약을 구체적으로 표시하고 POST/PUT을 보내지 않습니다.

## 사용 필드 판정

`editmeta`와 `/rest/api/2/field`는 Field Configuration의 Hidden 여부를 증명하지 않습니다.
`jira.field_configuration`이 설정되어 있으면 선택한 프로젝트·Issue Type으로 기존 전수 조회 adapter를 실행합니다.
명시적인 `hidden`/`isHidden`/`visible` boolean이 있는 필드만 Hidden/Visible로 판정합니다.

설정이 없거나 API 호출이 실패해도 STEP 3은 표시됩니다. 기존 `/rest/api/2/field`에서 이름·ID·타입을,
선택한 Issue Type의 Issue API에서 실제 값을 조회합니다. 설정이 없을 때는 Issue 응답에 나타난 null 필드도
유지합니다. 전역 필드 목록 전체를 그대로 출력하지는 않습니다. Issue에 나타나지 않는 필드까지 발견했다고
주장하지 않습니다. 조회 설정 밖에서 실제 값이 발견된 필드도 `표시 상태 미확인`으로 표시합니다.

Hidden이 확인된 필드만 기본 목록에서 제외하며, 값이 있으면 누락 위험으로 Import를 차단합니다.
미확인 필드는 매핑 검토가 가능하고 Import 전에 현재 분석 범위·누락 가능성에 대한 별도 확인을 요구합니다.
이 확인은 해당 분석 결과에 한정되며 Import 직전 재조회가 달라지면 재분석을 요구합니다.

Jira Data Center 버전/플러그인별 Field Configuration REST 지원은 배포 문서로 확인해야 합니다.
이번 작업에서 공식 9.12 REST 문서(`https://docs.atlassian.com/software/jira/docs/api/REST/9.12.0/`) 접근은
환경 프록시의 HTTP 403으로 차단되어 지원 endpoint를 추가 확정하지 못했습니다.
Cloud의 `/rest/api/3/fieldconfiguration`을 DC 기본 API로 가정하거나 비공개 endpoint를 탐색하지 않습니다.
검증된 DC/플러그인 GET 계약이 있는 경우 아래 adapter를 설정하면 자동 조회하고, 없으면 수동 검토를 사용합니다.

이름/표준 별칭(Summary → Name, Assignee → Assigned to 등), 확인된 타입 이름, 다중 선택 여부와 값 변환이 모두 맞을 때만 자동 연결합니다. 숫자 typeId만으로 타입을 추정하지 않습니다. 같은 CB 필드를 두 Jira 필드에 연결하지 않습니다. 알 수 없는 타입·사용자 ID·API binding은 수동 확인/설정 오류로 남깁니다. Table/미지원 배열을 Text 등으로 자동 변환하지 않습니다.

## 배포별 읽기 API 설정

임의의 플러그인 경로나 쓰기 경로를 기본값으로 가정하지 않습니다. 실제 서버 문서/Swagger에서 확인한 계약을 설정하세요. 아래 경로는 **형식 설명용 자리표시자이며 실제 API가 아닙니다.**

```json
{
  "jira": {
    "field_configuration": {
      "path": "/VERIFIED_FIELD_CONFIG/{projectKey}/{issueTypeId}",
      "pagination": "complete",
      "response_path": "fields",
      "total_path": "total",
      "record_fields": {"id": "fieldId", "hidden": "isHidden"}
    },
    "changelog_adapter": {
      "path": "/VERIFIED_CHANGELOG/{issueKey}",
      "pagination": "jira",
      "records_key": "histories"
    }
  },
  "migration": {
    "r4j": {
      "trees": {"path": "/r4j/trees/{issueKey}", "pagination": "complete"},
      "revisions": {
        "path": "/VERIFIED_R4J_REVISIONS/{issueKey}",
        "pagination": "jira",
        "records_key": "values"
      },
      "baselines": {
        "path": "/VERIFIED_R4J_BASELINES/{projectKey}",
        "pagination": "complete",
        "response_path": "values",
        "total_path": "total"
      }
    }
  }
}
```

`jira` pagination은 기존 `startAt/maxResults/total` 페이지 처리를 사용합니다. `complete`는 계약상 전수 배열이 반환될 때만 사용합니다. 직접 배열을 반환하면 `response_path`를 생략합니다. `total_path`가 있으면 건수를 대조합니다. `query`는 `${projectKey}`, `${issueTypeId}` 등의 기존 템플릿을 지원합니다. `record_fields`는 원본을 유지하며 정규화 키를 추가합니다.

확장 changelog가 잘린 경우 `changelog_adapter`로 전수 수집하고 원본 total 및 ID 중복을 검증합니다. 완전한 응답이 없으면 쓰기 전에 실패합니다. R4J Tree/Revision/Baseline은 독립된 원본 이력으로 전수 수집하는 계약이 필요합니다. 실제 API가 빈 배열을 반환한 경우에만 0건으로 처리합니다.

## Codebeamer operation 계약

기존 `codebeamer.operations` 및 Swagger 검증을 재사용합니다. 페이지형 API는 검증된 전수 응답 adapter가 필요합니다. 첫 페이지를 전체 배열로 설정하지 마세요. 다음은 코드 내 정규화 operation 이름입니다.

| Operation | 정규화 결과 / 계약 |
| --- | --- |
| `projects_get` | 기존 프로젝트 전체 `[{id,name}]` |
| `project_get`, `tracker_get` | 지정한 실제 `id` |
| `trackers_get` | 선택 프로젝트 내 전체 `[{id,name}]` |
| `tracker_create` | `{projectId}`를 포함하는 실제 프로젝트 하위 쓰기 경로, 결과 `{id}` |
| `tracker_lookup` | 프로젝트 내 `sourceKey` 완전 일치, 0/1건 배열 |
| `lookup`, `item_create/get/update` | 기존 Engine의 원본 ID Upsert 및 필드 재조회 계약 |
| `attachment_lookup` | `itemId/sourceId` 완전 일치 0/1건 배열, `sha256`도 제공 |
| `attachment_upload/get` | 기존 multipart 업로드와 바이너리 다운로드; 업로드에 `sourceId/sha256`도 제공 |
| `attachment_metadata_get` | `{filename,size}` |
| `attachment_url_get` | `{url}`: CB Item에서 참조할 수 있는 업로드 파일 HTTP(S) URL |
| `comment_lookup/create/get/update` | `sourceId` 완전 일치 조회 및 본문 재조회 |
| `association_lookup/create/get/update` | `sourceId` 완전 일치 조회 및 방향이 있는 관계 재조회 |

`response_path`, `response_fields`, `body`, `query`로 실제 API 형식을 정규화합니다. 읽기 operation은 GET이어야 합니다. `fields` 및 `tracker_configuration_get`을 지정하면 기존 고정 읽기 경로를 재정의할 수 있습니다. 전체 필드의 `id/label/typeName/typeId/mandatory/multipleSelection/options/reference`와 Hidden 메타데이터를 반환하세요.

`item_create` 템플릿에는 `sourceId/sourceKey/projectId/trackerId/payload`를 사용할 수 있습니다. 원본 ID 전용 유일 필드를 저장하고 `lookup`과 일치시켜야 합니다. 인증정보를 설정에 직접 넣지 마세요.

### 신규 Tracker / Field

`migration.wizard.tracker_template_id`에 선택 프로젝트 내부의 템플릿 Tracker ID를 지정합니다. STEP 3은 그 필드로 분석합니다. `tracker_template`에는 실제 생성 API가 요구하는 템플릿 지정 payload를 설정합니다. 생성 시 선택 프로젝트·이름·sourceKey를 전달하고 프로젝트 소속을 재조회합니다. 새 Tracker의 Field ID는 템플릿 ID를 재사용하지 않고 실제 필드에서 다시 결정합니다. 구조가 다르면 중단합니다.

`migration.wizard.field_types`는 의미 타입별(`text/number/boolean/date/datetime/option/status/user`) 검증된 생성 템플릿입니다. 다음 ID와 discriminator는 예시이므로 실제 값으로 바꿔야 합니다.

```json
{
  "wizard": {
    "tracker_template_id": 123,
    "tracker_template": {},
    "association_types": {"JIRA_LINK_TYPE_ID": 456},
    "field_types": {
      "text": {
        "type_name": "Text",
        "definition": {
          "typeId": 0,
          "mandatory": false,
          "multipleSelection": false,
          "options": null,
          "reference": null
        },
        "binding": {"type": "TextFieldValue", "value_property": "value"}
      }
    }
  }
}
```

Swagger의 유일한 Field 전용 생성 API를 기존 helper로 검증합니다. 이름·다중 선택·Choice 후보는 Jira에서 준비합니다. 생성 후 Field ID·타입·선택지를 재조회해 매핑에 반영합니다. 사용자 대응은 `user_mapping`으로 명시합니다. 기존 CB 필드의 실제 FieldValue binding도 기존 설정이 필요합니다.

## 일반 필드 UI에서 제외되는 자동 이관 데이터

- **Attachment**: 파일명·크기와 재다운로드 SHA-256을 대조합니다. Jira Wiki의 `!filename!`, 원본 첨부 URL 및 `/secure/attachment/{id}/...`를 CB URL로 변환해 Description을 재조회합니다. 같은 이름의 모호한 첨부, 인벤토리에 없는 이미지, 미지원 structured rich text는 오류입니다. 외부 출처의 이미지를 임의로 다운로드하지 않습니다.
- **Comment**: 전체 본문을 CB Comment에 생성합니다. 정규화 payload는 `{body}`입니다. 원본 작성자·작성일·수정일을 포함한 전체 원본은 Item에 연결된 JSON archive에도 보존합니다. Native 작성자/시각을 복원했다고 표시하지 않습니다.
- **Linked Issue**: 모든 Item ID가 준비된 뒤 Association을 생성합니다. `wizard.association_types`로 Jira Link Type ID → 실제 CB Association Type ID를 명시합니다. 정규화 payload는 `fromItemId/toItemId/typeId`입니다. inward 관계는 양 끝을 반전하며 같은 Jira link ID는 한 번 처리합니다. 원본 타입의 ID/이름/inward/outward와 표현할 수 없는 정보는 JSON에 보존합니다. 선택하지 않은 타입은 제외 건수로 보고합니다. 선택한 관계의 범위 밖 대상은 명시적 Item 매핑이 없으면 오류로 보고합니다.
- **History / R4J**: 각 CB Item에 `jira-legacy-<sourceId>-<hash>.json`을 첨부합니다. CB Item의 첨부에서 별도로 조회할 수 있는 **Legacy History**이며, CB가 생성한 Native History와 구분합니다. Jira History의 변경 전후 값·작성자·시각·필드, 댓글, 관계, 원본 필드 및 R4J Revision/Baseline을 저장합니다. 바이너리 및 JSON 전체 일치와 History/변경/R4J 건수를 검증합니다. `report.json`의 `legacy_history`에 비교 건수를 기록합니다. Native 작성자·시각 복원 플래그는 false입니다.

Wizard는 Legacy 보존을 기본으로 합니다. 기존 `history_replay.py`는 `wizard.native_replay: true`일 때 재사용하며 기존 scalar adapter가 필요합니다. 이때도 Native 작성자·시간 복원은 미검증으로 보고합니다. Legacy History는 별도의 웹 뷰어나 CB 플러그인이 아니라 Item 첨부 JSON으로 제공됩니다.

## 재시도 및 제한

동일한 `state_dir`을 사용하는 단일 프로세스로 실행하세요. 대상 원본 ID 완전 일치 조회와 SQLite journal로 완료된 Item/Field/Comment/Association/Attachment를 재사용합니다. 응답 유실 후 완전 일치로 확인되는 생성은 다시 생성하지 않고 대조합니다. 존재 여부가 불명확한 pending 작업은 자동 재생성하지 않으며 서버 대조를 요구합니다. 상태 폴더를 삭제해 재시도하지 마세요. 실패 전 생성된 데이터를 rollback으로 삭제하지 않습니다.

기존 Xray Run/History/Evidence와 Plan-first full-Xray 처리를 유지합니다. 실제 Xray 관계 및 Run adapter는 기존과 같이 필요합니다. full-Xray는 설정한 Plan/Test/Set/Execution의 전체 의존성을 포함하는 범위에 사용하세요. API 장애로 일부 데이터만 생성되면 `complete: false`와 대상별 오류를 보고합니다. 원인 수정 후 동일한 상태에서 재분석/재시도합니다.

GUI 네트워크 처리는 현재 UI 스레드에서 순차 실행합니다. 큰 프로젝트의 전수 분석 중에는 화면 응답이 멈출 수 있습니다. 소스·대상을 동시에 편집하면서 이관하는 작업은 지원하지 않습니다.

## 테스트

```sh
python3 -m unittest discover -s tests -v
# 가상 디스플레이가 있는 환경:
xvfb-run -a python3 -m unittest discover -s tests -v
```

신규 테스트는 읽기 전용 분석, Hidden/null, 중복/타입 충돌, 승인 취소/승인, 실제 Tk 위젯 조작, Tracker/Field 생성·재조회, 다중 Choice, 실제 Engine의 바이너리/댓글/History/R4J/Association 보존, 재시작 매핑 복원, 응답 유실 후 재시도를 검증합니다. DISPLAY가 없으면 GUI 테스트는 명시적으로 skipped입니다. 합성 데이터와 모의 대상을 사용하며 실제 서버에 쓰지 않습니다.

## 수동 매핑 수정 진단

CB 후보 목록은 실제 Tracker의 모든 조회 필드를 이름·ID·타입과 함께 보여줍니다.
Hidden, 타입/다중 선택 미확인, 실제 타입 불일치, 다른 Jira 필드와 중복인 후보는
연결 불가 사유를 표시하며 선택해도 저장되지 않습니다. 정상 후보로 변경하면 해당 행의
Field ID와 Choice 값 대응을 저장하고 즉시 갱신합니다. 기존 선택 및 Choice 대응은 팝업에서 복원합니다.
Tk 드롭다운이 포커스를 이동하는 순간 제거되던 이벤트 처리를 없애 선택 이벤트가 저장까지 도달하도록 수정했습니다.
실제 서버 조회 확인은 고객 환경의 URL/인증으로 분석을 실행한 결과가 필요합니다.
이번 환경에는 해당 연결 설정이 없으므로 실서버 GET 성공을 주장하지 않습니다.

## Field Definition과 FieldValue 계약 분리

KEFICO 디렉터리 및 해당 Git 이력에는 README만 있어 동일 생성 코드/JSON을 확인하지 못했습니다.
현재 프로젝트의 `creation_templates`, `configuration_field_contract`, `create_confirmed_field`를 재사용합니다.
새 생성 로직을 추가하지 않습니다. 신규 후보는 Configuration 생성 계약으로 검증하며 FieldValue 모델이
없어도 생성 설정을 저장할 수 있습니다. 후보는 기존 Tracker typeId/Permission을 근거로 만들고 기존
Field ID·Choice ID·참조 설정을 복사하지 않습니다. 기존 필드 권한을 일괄 변경하지 않습니다.

사용자가 Import를 승인한 뒤 기존 전체 Configuration을 다시 읽어 변경 여부를 확인하고 새 정의를 추가해
POST합니다. 재조회에서 신규 ID·타입·Choice 및 기존 필드/권한/필드 외 설정의 보존을 검증합니다.
생성된 Choice의 서버 할당 ID는 허용하되 요청한 이름/속성과 개수를 대조합니다.

필드 생성 후 FieldValue 계약이 미확인이라면 실제 Field ID와 매핑을 저장하고 Item 이관을 중단하며
`complete:false`로 보고합니다. 필드 생성 성공과 Jira 값 이관 성공은 별도입니다.
상태 폴더를 유지하면 다음 분석에서 생성된 필드를 재사용할 수 있습니다. 확인된 Item/FieldValue 계약이
추가된 뒤에만 데이터 이관을 다시 승인하세요. 실제 고객 서버에서 Text/Choice/Integer 생성과 Item 값
재조회는 이번 환경의 접속 정보가 없어 수행하지 않았습니다. 저장소 테스트 결과는 Mock 결과입니다.

## Components 한 필드 검증

Jira `/rest/api/2/field` 및 선택 Issue의 원본 응답에 있는
`schema: {type: array, items: component}`를 다중 Choice로 해석합니다. 이름만으로 타입을 추측하지 않습니다.
원본 Component ID/Name이 없거나 동일 ID의 이름이 달라지거나 서로 다른 ID의 이름이 같으면
잘못 합치지 않도록 분석을 중단합니다. 선택 옵션은 실제 값과 CreateMeta allowedValues에서 구성합니다.

STEP 3에서 Components 생성 설정의 다중 선택과 옵션을 확인하고 테스트 Issue 1건으로 범위를 제한한 뒤
Import를 승인합니다. Configuration 생성 계약이 확인된 프로필은 기존 `create_confirmed_field()`를 호출합니다.
생성 ID/옵션을 재조회하고 원본 Component ID를 CB 옵션 ID에 연결합니다. 문자열로 변환하지 않습니다.
FieldValue 계약이 없으면 정의 생성 결과를 저장하고 값 이관은 미완료로 보고합니다.

상태 폴더의 `component-mapping/`에 원본 ID→Name 및 원본 ID→CB Option ID와 Field ID를 기록합니다.
`field-creation/`에는 생성 정의/재조회 결과/실패 단계를 보존합니다. Configuration 생성 journal에는 실제
POST 경로·본문·반환값·최종 GET 결과가 포함됩니다. 인증 헤더/토큰/비밀번호는 포함하지 않습니다.
원본 업무 데이터가 있으므로 이 파일들을 공개 저장소에 올리지 마세요. HTTP 상태는 기존 requests 감사 로그로 대조합니다.

현재 자동 테스트는 Components 생성안→다중 Choice 생성→Item 값 변환→GET 비교와 재실행 중복 방지를
모의 서버로 검증합니다. 실제 고객 CB 서버 접근 설정은 이 환경에 없으므로 실제 생성 성공은 미확인입니다.

## STEP 3-2 관계 매핑

STEP 3에는 `필드 매핑`과 `관계 매핑` 탭이 있습니다. 관계 탭의 `관계 유형 조회 (GET)`은
Jira `/rest/api/2/issueLinkType`의 issueLinkTypes 전체 배열과 CB `/v3/associations/types` 전체 배열을
읽습니다. ID/name과 Jira inward/outward가 누락되거나 부분 응답이면 성공 처리하지 않습니다.
이름이 같아도 자동 선택하지 않습니다. 매핑하지 않은 대상은 빈칸으로 유지합니다.
사용자는 대상 유형과 방향 반전을 변경하고 저장할 수 있습니다. 새로운 CB Association Type을 생성하지 않습니다.

Jira `A blocks B`의 inward 표기는 원래 outward 방향으로 정규화합니다. 반전 체크는 그 후 한 번 적용합니다.
모든 Item ID가 확보된 뒤 기존 `preservation.associations` → `record` 경로로 생성/재조회합니다.
`association_create`는 실제 서버의 POST `/v3/associations` schema 및 body adapter와 연결해야 합니다.
`association_lookup/get`에는 원본 Link ID 정확 일치와 관계 양쪽 ID/typeId를 검증하는 정규화가 필요합니다.
POST 경로만 안다고 요청 JSON/lookup 계약을 검증했다고 주장하지 않습니다. 설정이 없으면 명확히 실패합니다.

관계 설정은 `wizard/relation-mapping`에, 조회 근거는 `wizard/relation-catalog`에 저장합니다.
관계 처리 시 실제 Association 유형과 원본 링크의 저장된 방향 메타데이터를 검사합니다.
원본 Link Type/방향/대상은 Legacy JSON과 `source-relations`에 보존하며, 대상 Item 미생성·미매핑·재조회
실패를 완료로 처리하지 않습니다. 기존 정수형 association_types 설정도 호환됩니다.

Reference 방식은 소유 Tracker ID, Reference Field ID, 대상 Tracker ID를 지정합니다.
서버별로 검증된 `reference_field_get`, `reference_values_get`, `reference_values_update` 작업이 필요합니다.
이름은 내부 adapter 이름이며 새로 추측한 API 경로가 아닙니다. 실제 Swagger GET/PUT 또는 PATCH에 연결하세요.
필드 메타데이터는 `{id,hidden:false,multiple:boolean,targetTrackerIds:[...]}`로,
현재 값은 `{fieldId,values:[{id:ItemID},...]}`로 정규화해야 합니다.
필드 쓰기 본문은 실제 FieldValue 계약으로 변환하도록 adapter를 설정해야 합니다.
소유/대상 Item의 실제 Tracker와 방향을 확인하고 기존 참조를 유지하며, 단일 참조가 이미 차 있으면 덮어쓰지 않습니다.
참조를 추가한 뒤 전체 값을 재조회하고 동일 관계를 재실행해도 추가하지 않습니다.

공식 참조: https://support.ptc.com/help/codebeamer/r3.0/en/codebeamer/user_guide/ug_relation_type_mapping.html
이번 환경에서 이 문서 조회는 프록시 HTTP 403으로 차단되어 본문을 독립 확인하지 못했습니다.
고객 서버 접속 정보도 없어 Association/Reference 실서버 생성은 수행하지 않았습니다.
자동 테스트는 방향 반전·inward/outward 중복 방지·재실행/저장 복원·참조 보존·Tracker 불일치 차단을 검증합니다.

## 생성 실행 상태와 복구

`configure_creation()`은 PLANNED만 저장합니다. 전체 Import의 다른 사전 검사가 실패하면 POST가
실행되지 않습니다. PLANNED를 서버 생성 성공으로 읽어서는 안 됩니다.
`create_field()`는 기존 생성 helper의 읽기/스키마 검증을 거친 뒤 VALIDATED를 기록하고,
helper의 실제 POST 직전 callback에서만 CREATE_PENDING과 기존 생성 journal을 연결합니다.

재조회된 Field ID는 Choice 매핑 전에 CREATED로 독립 저장합니다. 이후 VALUE_MAPPING_PENDING에서
원본 값/옵션 ID를 연결하고 실제 Engine의 검증된 Import 결과가 완료일 때만 COMPLETED로 올립니다.
POST 전 실패는 FAILED, POST가 시작된 뒤 응답/결과가 불확실하면 RECONCILIATION_REQUIRED입니다.
`field-status/` JSON과 SQLite `field-phase:*` 기록에 단계 이력·Field ID·원인이 있으며 기존 화면의
선택 행 설명에도 상태가 표시됩니다. 기존 State.once()의 pending 자동 재실행 금지는 유지합니다.

응답 유실 후에는 생성 journal의 POST 전 정의/기존 설정을 실제 서버 응답과 대조합니다.
유일한 필드 이름만으로 성공을 선언하지 않습니다. ID·타입·다중 선택·Permission·Choice 및 기존
필드/Tracker 설정 보존이 확인되어야 이어갑니다. 근거 없는 과거 pending은 자동 POST하지 않고
RECONCILIATION_REQUIRED로 남깁니다. CREATED 이후 옵션 매핑 실패는 생성 성공을 취소하지 않으며,
재분석·재시작 후에도 기존 ID를 재사용합니다. state/journal 파일을 삭제하지 마세요.

### 선택 Tracker에 없는 타입

검증된 `codebeamer.operations.field_type_catalog` GET이 설정된 경우 서버 타입 정의 목록을 읽습니다.
이는 내부 adapter 이름이며 고정된 신규 API 경로를 추측한 것이 아닙니다. 정규화된 전체 배열에는
실제 typeId, typeName, Permission, options/reference 등 생성 정의가 필요합니다. 해당 정의도 실제
Tracker Configuration POST schema로 검증합니다. 카탈로그가 없는 환경에서는 기존 Tracker 정의와
명시적 프로필만 사용하며, 서버가 지원하는 모든 타입을 발견했다고 표시하지 않습니다.

| 타입 | 계획 및 생성 경로 | 이번 변경의 실제 서버 생성/값 재조회 |
| --- | --- | --- |
| Text, Wikitext | 서버 정의·Permission·POST 계약 확인 시 계획 가능 | 미검증 |
| Choice / Components | 서버 옵션 ID와 원본 ID 분리, 다중 선택 보존 | 미검증 |
| Integer, Date, Boolean | 서버 타입 이름·typeId·POST 계약 확인 시 계획 가능 | 미검증 |
| User, Members | 검증된 정의 및 사용자/참조 설정 필요; 참조형 템플릿은 자동 복사하지 않음 | 미검증 |

어느 타입도 이번 작업에서 실서버 생성 완료로 판정하지 않았습니다. FieldValue 쓰기 가능 여부는 각
생성 프로필의 value_contract 및 실제 Item GET 검증을 별도로 확인해야 합니다.
실서버 완료 증거는 POST 경로·HTTP 상태, 실제 Field ID/Configuration, Option ID 대응표, Item ID/GET 값,
기존 설정/권한 보존, 동일 작업 재실행 결과입니다. 이 환경에는 고객 서버 접속 정보가 없어 이 증거는 없습니다.

## 기존 필드 선택과 Reference 처리

`manual_mapping()`은 실제 선택한 Field ID로 Field Detail/Configuration을 다시 병합합니다.
`build_spec(existing=True)`는 받은 속성만 보존하며 없는 options/reference/mandatory를 null·false·빈 배열로
채우지 않습니다. 일부 정의가 부족해도 선택은 저장합니다. 부족한 속성과 Field ID를 표시하고 실제 Import
전에 차단합니다. 재분석·재실행 시 ID를 유지하며 상세 응답이 보완되면 검증 상태를 갱신합니다.
명확한 타입 불일치·Hidden·중복은 구체적인 오류로 표시합니다.

Jira schema의 `issue`/`issuelink` 또는 해당 배열은 참조형으로 해석합니다. 배포별 Issue Picker가 다른
schema를 반환하면 타입을 추측하지 않고 선택 저장 후 타입 확인을 요구합니다. CB `ReferenceField`/
`TrackerItemChoiceField`는 Reference로 정규화하며 원래 reference 정의를 그대로 보관합니다.
사용자가 Reference 필드를 선택한 경우 일반 Item payload에서 제외하고, 전 Item 생성 후 원본 Issue Key/ID를
CB Item ID로 해석해 Reference API로 기록합니다. 선택 범위 밖 참조는 임의 연결하지 않고 오류로 남깁니다.
Jira issuelinks는 기존 Association 탭 또는 명시적 Reference 관계 설정 경로를 사용합니다.

신규 Reference 생성은 검증된 `wizard.field_types.reference` 프로필에 `reference_template`이 필요합니다.
서버 schema에서 확인한 참조 정의의 대상 ID 위치에 `${targetTrackerId}`를 지정합니다. 팝업에서 대상 Tracker를
선택하면 해당 위치에만 실제 ID를 넣습니다. 참조 설정을 다른 필드에서 무조건 복사하지 않습니다.
단일/다중 여부는 원본 schema를 유지합니다. 생성 설정 저장과 실제 POST 검증은 별개이며 생성 전에
Configuration schema 및 기존 설정 보존 계약을 재확인합니다. 확인된 프로필이 없으면 생성 설정에 이유를 표시합니다.

실제 Reference 값 쓰기에는 앞 절의 reference_field_get/values_get/values_update adapter가 필요합니다.
참조 대상 Tracker·방향·단일/다중을 확인하며 기존 참조를 보존합니다. 이번 환경에서는 고객 서버에 접근하지
못해 실제 Reference 생성/값 이관과 Association 생성은 미검증입니다. 자동 테스트 결과만으로 실서버 성공을 주장하지 않습니다.


## 관계 범위 처리 검증 (2026-11-03)

사용자가 선택한 Link Type ID만 처리합니다. 양쪽 Issue에서 수집한 같은 Link ID는 하나로
정규화하며, 방향이 모순되면 오류를 기록합니다. 한 관계가 실패해도 다른 관계 처리는 계속합니다.
`report.json`의 `relations`에는 source/selected/verified/excluded/failed 및 개별 ID·방향·오류가
기록됩니다. `source = verified + excluded + failed`를 확인합니다. 원본은 source-relations와
Item Legacy JSON에도 보존합니다. 제외는 성공 생성 건수에 포함하지 않습니다.

범위 밖 Issue는 `migration.wizard.external_item_mapping`에 실제 Jira ID 또는 Key를 키로,
CB Item 정수 ID를 값으로 명시한 경우에만 사용합니다. 대상 Item GET으로 ID를 검증합니다.
빈 Item을 임의로 생성하지 않습니다. 외부 Item을 Reference 소유자로 선택하면 그 Item의
참조값을 수정하므로 승인한 테스트 범위의 Item만 지정하세요.

Reference 후보는 선택한 프로젝트의 실제 Tracker 필드에서 조회합니다. 소유 Tracker와 대상
Tracker를 실제 Item GET 결과와 대조합니다. 단일 필드에 여러 대상이 선택되면 값 쓰기 전에
해당 관계 모두 실패 처리합니다. 기존 다중 참조는 보존하며 추가 후 재조회합니다.
신규 Reference는 기존 Configuration 생성 helper를 사용하며 Import 승인 후에만 생성합니다.
생성 후 실제 Field ID를 저장하고, 응답 유실 시 저장한 원본 Configuration과 서버 결과를
비교하여 중복 POST를 방지합니다. 정의 생성 계약과 Reference 값 쓰기 계약은 별도입니다.

전체 자동 회귀 테스트: 229개 실행, 223개 통과, 6개 건너뜀. 모의 HTTP/API 검증이며
실제 Tk 화면 상호작용은 DISPLAY 부재로 실행하지 못했습니다. 고객 Jira/Codebeamer 접속
설정이 없어 실제 Association POST, Reference 생성/값 쓰기와 서버 재조회는 미검증입니다.
서버별 association_lookup/create/get 및 reference_* 계약이 없으면 해당 관계를 실패로
보고합니다. 이번 변경은 관계 범위에 한정하며 Choice·필드 부분 Import 개선은 별도 작업입니다.

## 자동 생성 계획 및 검수 (2026-11-03)

STEP 3은 사용 필드를 기본 선택하고 기존 호환 대상 또는 신규 생성 계획을 표시합니다.
전체 선택/해제, 선택 항목만 보기, 옵션 상세와 기존 수동 매핑/생성 설정 수정을 제공합니다.
옵션은 Jira allowedValues 및 실제 수집 값에서 구성합니다. 조회되지 않은 옵션까지 수집했다고
보장하지 않습니다. Reference 대상 Tracker와 미확인 생성 프로필은 추가 설정이 필요합니다.
체크 해제한 필드는 생성·일반 값 payload·누락 오류에서 제외합니다. Summary 해제 시 필수 안내로
차단합니다. 원본 Legacy 보존 및 첨부/댓글/History/R4J/Xray/관계 범위는 별도로 유지합니다.
선택과 수정 계획은 재분석/재실행 후에도 유지됩니다. Import 전 별도 생성 예정 내역 창에서
이름·타입·다중 여부·옵션 수·기존 Field ID를 확인하고 실제 Import 승인을 해야 쓰기합니다.
선택한 필드의 검증 오류는 해결하거나 해당 필드를 해제해야 합니다.
자동 테스트 230개 중 224개 통과, 6개 건너뜀. 실제 GUI 및 고객 서버 쓰기는 미검증입니다.
