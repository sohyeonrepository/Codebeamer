# Jira → Codebeamer Import Wizard

```sh
cd jira-xray-cb-migration
python3 import_wizard.py --config config.local.json --prompt-auth
```

Python 3.10+와 Tkinter를 사용합니다. 기존 `mapping_helper.py`, CLI, Run-only, full-Xray 기능은 유지됩니다. Wizard는 Run-only 기본 설정을 **선택한 프로젝트의 Issue 이관**으로 전환하며, 명시적인 `full-xray` 설정은 유지합니다. 인증은 기존 환경변수/비표시 입력을 재사용합니다. 실제 서버 쓰기 테스트는 자동으로 수행하지 않습니다.

## 화면과 승인 경계

1. **STEP 2**: 프로젝트 조회 → Jira와 기존 CB 프로젝트 선택 → Issue Type / Tracker 조회 → 유형마다 기존 Tracker 또는 신규 Tracker 이름 연결. Issue가 없는 유형도 전체 목록에 포함됩니다. 여러 Jira 프로젝트도 차례로 설정할 수 있습니다.
2. **STEP 3**: 저장한 Tracker ↔ Issue Type 조합 선택 → 분석. 왼쪽 CB / 오른쪽 Jira의 두 컬럼이며, 선택한 행의 상태·사유는 하단에 표시됩니다. 두 번 클릭하면 대상 및 Choice 값을 수동 연결합니다. 신규 필드는 생성안으로만 표시됩니다.
3. **Import…**: 명시적 승인 후에만 쓰기를 켭니다. 분석 이후 변경이 있으면 재분석을 요구합니다. API와 원본 데이터를 사전 확인한 뒤 Tracker/Field를 준비하고 기존 Engine으로 전달합니다. 결과 탭에서 실제 조회·검증·오류를 확인합니다. 종료 시 쓰기 권한은 다시 꺼집니다.

조합 매핑은 `state_dir/wizard/`에 저장됩니다. 실제 Field ID를 반영한 `resolved-mapping`은 재시작 시 자동으로 읽습니다. 프로젝트 변경은 그 프로젝트의 이전 조합을 무효화합니다. 다른 소스/대상 URL에는 같은 상태 디렉터리를 사용할 수 없습니다. 기존 데이터 삭제와 DELETE 요청은 허용하지 않습니다.

STEP 3 화면은 선택한 `Jira Issue Type → Codebeamer Tracker` 조합 하나만 분석하고 표시합니다. 왼쪽은
Codebeamer 필드, 오른쪽은 Jira API의 실제 필드명이며 custom field는 실제 이름을 먼저 표시하고 ID를 함께
표시합니다. 기존 필드는 Jira/CB 메타데이터로 타입 호환성을 검사하므로 사용자가 Field Type을 선택하지 않습니다.
자동 매핑·수동 매핑·신규 생성·제외 상태와 검색/필터를 제공합니다. Jira Hidden 필드는 `제외`에 표시하고,
실제 값이 남아 있으면 별도 누락 위험으로 표시하여 Import를 차단합니다.

기존 CB 필드가 없는 행에만 `＋ 신규 필드 생성` 작업이 나타납니다. 팝업의 이름·검증된 타입·다중 선택·Choice
옵션은 생성 계획으로만 저장되며 이 단계에서는 서버 쓰기를 하지 않습니다. 실제 Tracker/Field 생성은 사용자가
마지막 `다음 (Import 승인)`에서 승인하고 쓰기 직전 분석 결과가 동일한지 다시 확인한 뒤 실행됩니다.
Attachment·Comment·Association·History/R4J/Xray 처리는 기존 Migration Engine 경로를 그대로 사용합니다.

기본 전체 Wizard 실행 시 첫 화면에서 Jira 서버 URL·API 토큰, Codebeamer 서버 URL·아이디·비밀번호,
Swagger JSON URL을 입력합니다. 인증정보는 메모리에만 유지하고 config/state/report/log에 저장하지 않습니다.
`연결 / 실제 프로젝트 조회 (GET)`은 30초 제한과 취소 기능이 있는 읽기 전용 연결이며, 성공한 프로젝트 결과를
STEP 2에 그대로 전달합니다. STEP 2에서 프로젝트를 선택하면 실제 Swagger의 프로젝트 Tracker 목록 경로를
자동 연결하여 조회합니다. 연결 화면에서 입력한 URL과 인증은 이후 분석 및 승인된 Import에도 같은 세션에서 사용합니다.

## 사용 필드 판정

`editmeta`는 Field Configuration과 다릅니다. Wizard는 **실제 배포 환경에서 확인된 Field Configuration 읽기 API**를 요구하며, 없으면 분석 오류를 표시합니다. 각 필드의 `id`와 `hidden`/`isHidden`/`visible` 중 하나의 명시적 boolean이 필요합니다. 이름·스키마는 글로벌 필드 정의와 병합합니다.

Issue를 유형별로 전수 조회합니다. Field Configuration에 포함된 필드는 모두 null이어도 유지합니다. 설정 범위 밖의 전역 null 필드는 표시하지 않고, 실제 값이 있으면 매핑 대상으로 추가합니다. 표본을 전수 수집으로 간주하지 않습니다. Hidden 원본에 값이 있거나 숨긴 CB 후보와 원본 값이 대응하면 별도 누락 위험으로 보고하고 Import를 중단합니다. 숨긴 CB 필드는 기본 후보에서 제외합니다.

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

확장 changelog가 잘린 경우 `changelog_adapter`로 전수 수집하고 원본 total 및 ID 중복을 검증합니다. 완전한 응답이 없으면 쓰기 전에 실패합니다. R4J Revision/Baseline은 독립된 원본 이력으로 전수 수집하는 계약이 필요합니다. 실제 API가 빈 배열을 반환한 경우에만 0건으로 처리합니다.

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
- **Linked Issue**: 모든 Item ID가 준비된 뒤 Association을 생성합니다. `wizard.association_types`로 Jira Link Type ID → 실제 CB Association Type ID를 명시합니다. 정규화 payload는 `fromItemId/toItemId/typeId`입니다. inward 관계는 양 끝을 반전하며 같은 Jira link ID는 한 번 처리합니다. 원본 타입의 ID/이름/inward/outward와 표현할 수 없는 정보는 JSON에 보존합니다. 범위 밖 대상이나 타입 미설정은 미완료/오류로 보고합니다.
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
