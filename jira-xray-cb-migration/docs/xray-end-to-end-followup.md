# Xray 고객 정책 — 후속 통합 검증

기준: review/wizard-catalog-integration / 8722445 이후.
이 문서는 앞선 xray-customer-source-review.md의 '조회/검수만 연결' 상태에서
추가 구현한 범위를 설명한다. 고객 실서버 자료를 새로 조회했다는 뜻은 아니다.

## 추가 발견과 수정

1. 별도 Run Steps/Evidence 수집 결과가 records.test_run으로 전달되지 않았다.
   xray_run_collections.prepare로 전체 수집·Run ID·필드 변환·API 메서드를 검증하고,
   기존 mapped_record/attachment.transfer에 연결했다. 실행 전 계약이 없으면 차단.
2. Execution 원본 전용 정책에도 임시 대상 Tracker 선택이 필요했다.
   Wizard map_execution_context를 추가했다. 2단계에서 원본 맥락을 선택하고,
   Tracker/일반 필드 생성 없이 분석·원본 보존·Run 처리로 이어진다.
3. Run 결과 화면은 조회만 가능했다. 검증된 POST request schema의 writable 속성만
   선택하는 Run/Run Step/Test Case Step 매핑 양식을 연결했다. 값 변환 검증,
   중복 대상 차단, 기존 매핑 변경, 저장·재시작 복원을 지원한다.
4. 별도 Test Steps 조회 결과도 test_step_adapter를 통해 기존 검증 writer로 연결.
5. 명시적 plan_run_adapter가 있을 때에만 실제 Plan↔Execution membership과
   Execution↔Run identity를 사용해 Plan→Run 관계를 구성한다. 원본 없는 Set→Run,
   Set→Plan 관계는 만들지 않는다. 미지원 관계 계약은 계속 limitations로 표시한다.
6. 결과에 원본 Issue ID/Key→실제 대상 Item ID 목록, 원본/검증 Run 건수,
   Step ID와 Evidence 검증 건수를 추가했다. 집계 PASS/FAIL로 Run을 만들지 않는다.

## 주요 변경 파일

- import_wizard.py: 원본 Execution 선택과 타입별 Run/Step 매핑 양식.
- migration/wizard.py: source_only scope, 결과 매핑 복원, writes 전 Run collection 검사.
- migration/xray_mapping.py: 실제 요청 스키마 기반 매핑 후보/값 검증/계획 저장.
- migration/xray_run_collections.py: Steps/Evidence preflight, 기존 검증 writer 연결.
- migration/records.py: 실제 Run 생성 후 Step 생성/GET 확인과 Evidence 전송/검증.
- migration/full_xray.py: Test Steps, 실제 Plan/Run 관계, 원본↔대상 결과표/완료 검사.
- tests/test_xray_mapping_form.py / tests/test_xray_wizard_pipeline.py.

## 재현 가능한 Mock 흐름

실제 Python JiraClient, XrayClient, Wizard, Engine, SQLite state,
mapped_record, attachment.transfer를 사용하고 HTTP/CB 경계만 상태 있는 Mock으로 둔다.
Engine.issue/records.test_run을 성공값으로 대체하지 않았다.

서버/프로젝트 조회 → 전체 타입 표시 모델 → 대상 Tracker 선택 → 실제 필드 분석 →
체크박스 검수 → 원본 Execution 선택 → 명시적 Import 승인 → Plan/Case/Set Item 생성 →
Test Case Step → Run/Run Step → 증적 바이너리 → 관계 생성 → CB GET 대조 → 재실행.

확인 결과:
- 대상 Item 3개(Plan, Case, Set), 별도 Execution Item 없음.
- 개별 Run 1개와 실제 Run Step/Case Step ID 확인.
- 원본 TC Textarea 내용, Test Set Reporter의 사용자 ID 매핑값 일치.
- Run의 Case 참조, Execution 원본 ID/Key·SILS 환경·Legacy History 보존 확인.
- Set→Case, Plan→Case, 실제 Execution을 경유한 Plan→Run 관계 3개 GET 검증.
- 증적 파일명·바이너리 및 기존 attachment.transfer 검증 경로 실행.
- 재시작/재실행 후 Item/Run/Step/관계/첨부 중복 생성 없음.
- Step adapter 미설정, 미확인 Security Level은 대상 쓰기 전 오류.
- 매핑 UI 모델은 readOnly 속성/중복 매핑/enum 불일치 차단, 저장·복원 확인.

Synthetic endpoint/schema/ID이며 고객 STGVSTAR의 실측 데이터나 CB 3.0.0.7 계약이 아니다.
activity_inventory_verified 같은 검증 플래그는 Mock 시나리오의 가정이다.
실제 고객에게 동일 플래그/숫자를 그대로 적용하면 안 된다.

## 실제 배포에 필요한 계약

- jira.source_reads 및 run_discovery: 설치 Xray DC 응답의 실제 pagination/identity.
- migration.full_xray: 실제 Issue Type ID 및 전용 membership source paths.
- run_adapter/test_step_adapter/run_step_adapter: 실제 생성 요청 모델과 값 변환.
- run_step_lookup/create/get/update 및 test_step_*: 서버에서 확인된 API 작업.
- plan_run_adapter: 검증된 관계 이름/타입 값과 targetId/relationKind 쓰기 계약.
- 실제 Run Tracker와 기존 Case/Plan/Set Tracker. Connector Config ID는 사용하지 않음.
- Reporter 사용자 식별과 Security Level 정책. Security를 CB Permission으로 추측하지 않음.

현재 UI에서 저장하는 것은 fields 매핑이다. API 작업/Tracker/Source pagination 계약은
서버별 설정이 먼저 필요하다. 미설정 상태를 성공으로 표시하거나 임의 생성하지 않는다.
Native History 작성자/시간 복원과 임의 실행 관계는 이번 구현 범위에 포함하지 않는다.

## 검증 상태

- 구현: 위 코드/UI 연결 완료.
- 자동/Mock: 326개 실행, 320 통과, GUI 6개 건너뜀, 실패 0.
- Windows 실제 Tk 화면/고객 Jira DC/Xray/CB 3.0.0.7: 미검증.
- 기존 5단계 구조, RUN_WINDOWS.bat, config.local.json, .migration 경로 유지.
- 고객 서버 쓰기, PR 자동 병합 없음. 기존 v0.4 별도 실행본을 추가하지 않음.
