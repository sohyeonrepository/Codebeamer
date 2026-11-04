# Connector 참고 정책 / 기본 필드 매핑 검증

기준: review/wizard-catalog-integration. PR #5의 UI 옵션 표시/진단/회귀 테스트를
선별 반영했다. 원본 Connector 설정 파일·바이너리를 저장하거나 복사하지 않았다.

## 내부 ID와 REST 계약 구분

제공된 field.* 숫자는 Connector 내부 대상 의미의 단서다. 내부 string/date/
option/user/component 타입 숫자 역시 REST typeId나 쓰기 모델로 사용하지 않는다.
특히 내부 Date/Datetime이 동일 타입이라는 사실만으로 시간대를 버리거나 날짜로
변환하지 않는다. 사용자 및 component의 복수 숫자 규칙으로 단일 REST 모델을
추정하지 않는다. 실제 Tracker 필드 이름/명명된 타입/다중 여부/값 구조와 API
binding을 기준으로 판단한다.

제공된 default.fields, single.user.fields, implicit.fields, dedicated.fields,
designated.fields, foreign.*는 분리 책임의 참고 자료다. 각 설정의 실제 전체 값이
제공되지 않았으므로 소속 필드 집합이나 동작을 전부 재현했다고 주장하지 않는다.
원본 식별자는 매핑 및 보존 데이터에 유지하며 임의 Foreign 필드를 만들지 않는다.

## 적용한 정책

- 기본 필드: Summary/Description/Assignee/Reporter/Priority/Status의 대상이 없으면
  동일 이름의 일반 사용자 필드 자동 생성 대신 실제 기본 필드 확인을 요구.
- 실제 named payload binding의 Field ID + 호환 타입을 우선 후보로 확인.
  저장된 사용자 선택은 재실행 시 유지하며 불일치가 있으면 검증 오류로 남긴다.
  사용자가 저장한 선택을 기본 규칙으로 덮어쓰지 않는다.
- 이후 이름/별칭 + 타입 + 다중 여부로 후보를 좁히고 실제 값 변환으로 검증.
  후보가 여럿이면 임의 연결하지 않는다. 중복 대상 검사는 유지.
- Due Date → End Date 별칭 지원. Date 신규 계획에 Choice 값을 생성하지 않음.
  실제 신규 생성에는 기존 creation_profiles/Configuration 계약 검증이 필요.
- Assignee: 명시적인 user_mapping 사용, 원본 사용자 name/accountId/id와 대상 사용자
  ID 매핑. 원본 사용자를 대상 사용자로 자동 생성하지 않음.
- 선택 옵션 입력은 option/status 의미 타입에만 표시(우선순위/해결 포함).
  Text/User/Date/Reference에서는 Choice 입력을 숨김.
- Attachment/Comment/Changelog 등은 전용 처리로 유지.
  Xray Test Set/Plan/Execution/Run 관계 필드를 일반 자동 생성에서 제외.

## Components / Versions 참조

Jira component/version은 무조건 CB Reference가 아니다. 기존 Choice 지원 유지.
참조하려면 migration.catalog_reference_mapping에 필드별 명시적인 계약을 설정한다.
아래는 구조 예시일 뿐 실제 고객 Field/Tracker/Item ID가 아니다:

```json
{"components":{"field_id":733,"tracker_id":600,"target":"componentsRef",
 "value_mapping":{"9":{"id":901}}}}
```

이 설정은 기존 실제 Reference 필드의 reference.trackerId 구조와 다중 여부를
확인한 배포만 지원한다. 다른 서버 응답 구조에는 어댑터가 필요하다.
원본 Component/Version ID로만 값을 찾고, Import 전 대상 Item GET에서 ID와 Tracker를
확인한다. 대상이 없거나 Tracker가 다르면 실패. source_identity=id, 실제 field_id,
원본 source_field_id를 보존한다. Component 이름을 Jira Issue Key로 취급하지 않는다.
이 참조 계약은 설정 기반이며 새 Reference 대상/Item을 자동으로 생성하지 않는다.
기존 User/Reference API 계약이 확인되지 않으면 검증 완료로 표시하지 않는다.

## 검증 범위

- 기본 필드 단위 계약 테스트 11개.
- 추가 통합 테스트 1개: Mock Jira API → field_analysis → proposals → Wizard 계획 →
  실제 Python Engine → Mock CB Item 생성/재조회. Summary/Description 기존 처리와
  User ID, Date 문자열, Choice ID, Component ID→Reference Item ID 검증.
- Date 생성은 계획 검증이며 실서버 Date 필드 생성 성공을 의미하지 않는다.
- Windows GUI와 실제 Jira/CB 3.0.0.7은 미검증. UI 표시 조건은 소스/단위 검증이며
  headless 환경의 실제 Tk 테스트는 건너뜀.
- 기존 실행기/config.local.json/.migration 유지. PR 병합 및 실서버 쓰기 없음.

최종 전체 회귀: 312 실행, 306 통과, GUI 6 건너뜀, 실패 0.
