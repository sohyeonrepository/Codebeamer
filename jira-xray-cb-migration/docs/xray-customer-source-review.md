# 고객 Xray 자료 기준 재검토 및 검증

검수 브랜치: review/wizard-catalog-integration. 실서버 쓰기/PR 병합 없음.
고객 원본 JSON/설정 파일은 제공되지 않았으며 채팅의 자료 설명을 정책 근거로 사용했다.
Mock 데이터는 실제 서버 조회 증거가 아니다.

## 자료 해석

- TC Text/Textarea 사용자 필드: 원본 schema가 Text이면 절차/예상결과라고 이름만으로
  Native CB Test Step으로 변환하지 않는다. Jira 값과 Xray Test Steps를 별도 수집.
- Test Set Connector Config ID는 CB Tracker/Field ID가 아니다. 코드에 하드코딩하지 않음.
- Reporter는 실제 User 매핑. Security Level 필수 여부는 프로젝트 메타데이터로 확인.
  Jira 보안 수준을 CB 권한으로 자동 번역하지 않음. 미확인 보안 필드는 검토 대상.
- Test Plan의 Components/Squad/Vehicle/SW Version/Date는 실제 schema/값으로 분류.
- 동일 Plan Key/ID의 자료를 Run으로 재사용하지 않음. 중복 source identity는 대상 쓰기 전 오류.
- Execution 환경/원본 ID/Key/Plan 관계는 보존. PASS/FAIL 집계로 Run을 합성하지 않음.
- Run은 별도 숫자 ID + Test Key + Execution Key + 실제 status로 검증.

## 구현 변경

clients/xray_client.py:
  검증된 Execution test 목록에서 개별 Run ID/Test Key를 읽는 opt-in run_discovery.
  원본 응답 구조와 페이지 계약이 없으면 임의 ID를 추론하지 않는다.
  숫자 Run ID 전용 경로 검증. 기존 원본 Run 상세/Step/Evidence 보존 유지.

migration/xray_review.py:
  Test Case/Set/Plan/Execution/Run별 전용 GET 읽기 및 원본 보존 검수 모델.
  Test의 Steps/Set/Plan, Set의 Tests, Plan의 Tests/Executions, Execution의 Tests와
  개별 Run 상세, Run의 Steps/Evidence를 분리. Issue Link를 대체 근거로 쓰지 않음.
  미확인 pagination은 complete=False. 원본 수집 complete와 target_migration_verified를 분리.
  Run의 status/날짜/실행자/Defects 및 Step 결과는 원본 JSON에 유지. 없는 값은 만들지 않음.

import_wizard.py:
  기존 Step 3 필드 테이블에 처리 구분(기본/사용자정의/User/Choice/Date)을 추가.
  선택한 Issue Type에 따른 Test Case/Set/Plan/Execution 제목 표시.
  Jira required=True 원본 필드에 필수 표기.
  별도 'Xray 관계 / 실행 결과 검수' 탭: Issue Key 또는 숫자 Run ID로 GET 조회,
  원본/수집상태/실패 원인 표시. 필드 생성 UI와 분리. Run을 Jira Issue Type으로 만들지 않음.
  이 탭은 원본 검수 화면이며 새 Native Run 결과 쓰기 매핑 편집기를 구현한 것은 아님.
  실제 Run 쓰기는 기존 run_adapter와 검증된 CB 계약을 사용.

migration/full_xray.py:
  개별 Run 미조회 시 limitations 및 complete=False. Run status 미조회 시 쓰기 전 오류.
  Execution 별도 대상 Item 미생성 정책 유지, 실제 원본 관계만 재구성.
  기존 원본 ID/Key→대상 Item ID state 테이블 및 Test Run Case 참조 검증 유지.

mapping_helper.py / clients/tracker_fields.py / migration/wizard.py:
  Datetime 명칭 누락 수정. Date/Datetime가 동일 numeric typeId를 가질 때 관찰된
  타입 명칭까지 일치하는 필드로 생성 계약을 검증. 시간→날짜 자동 축약 없음.
  Configuration 계약과 값 쓰기 계약 분리 유지; 고객 Date POST 성공은 미검증.

## 설정 계약

jira.run_discovery는 명시적으로 확인한 배포에서만 설정:
- verified=True, execution_issue_type: 실제 Execution Issue Type ID/이름
- identity_key: 목록에서 중복을 검사할 원본 식별 멤버
- run_id_key / test_key_key: 실제 응답의 Run ID/Test Key 멤버 이름
- 기존 source_reads와 동일한 pagination 계약(unpaginated 또는 envelope/total/offset)

API 경로는 기존 Server/DC testexec/{key}/test를 사용한다. 목록에 Run ID가 없으면
다른 검증된 조회 방식이 추가로 필요하며 숫자를 만들어내지 않는다.
고객의 실제 응답이 없는 상태에서 위 멤버 이름을 config.local.json에 쓰지 않았다.

## 테스트 및 제한

tests/test_xray_customer_policy.py:
  Plan Issue Key/Issue payload를 Run으로 오인하는 경로 차단, PASS/FAIL 집계만으로
  완료 금지, 실제 Run ID 상세/Steps/Evidence/Defects 수집, 타입별 전용 관계 GET,
  원본 Issue Link 비사용, 필드 분류, Date/Datetime 생성 계약 분리.

tests/test_full_xray.py:
  동일 Plan 원본 중복 시 대상 쓰기 0회, 기존 Case/Execution/Run 식별/관계 검증.
기존 test_system_field_pipeline: Jira API→필드 분석→Tracker 조회→Wizard 계획→
사용자 승인→Mock Item 생성→CB GET 값 비교 흐름. 기존 관계/Run writer 검증도 실행.
이들은 서로 다른 범위의 Mock 테스트이며, 하나의 Windows GUI에서 고객 프로젝트
전체 이관을 성공시킨 검증이 아니다.

실서버 필요: 설치 Xray 버전, execution 목록의 Run ID 응답, Step/Evidence 페이지,
실제 Run Test/Execution 연결, Reporter 사용자와 Security Level 권한, CB Native
Step/Result/Evidence/Defect/Plan-Run 쓰기 계약. 계약이 없으면 기존 차단/제한 표시 유지.
RUN_WINDOWS.bat/config.local.json/.migration 보존. 원본에 없는 관계 생성 없음.

최종 실행 결과: 320개 실행, 314개 통과, GUI 6개 건너뜀, 실패 0.
Windows 실화면 및 고객 Jira/Codebeamer 서버 검증은 미실시.
