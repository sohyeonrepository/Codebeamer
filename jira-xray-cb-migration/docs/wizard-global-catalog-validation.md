# Wizard 전체 Issue Type 연결 검증

브랜치: review/wizard-catalog-integration. 기존 단계 구조와 실행기를 유지.

## 변경

- clients/jira_client.py: global_catalog(categories=...)로 조회 종류 선택 가능.
  Issue Type 조회에 Priority 권한 실패가 영향을 주지 않음. 기본 전체 조회는 유지.
- migration/wizard.py: 프로젝트 선택 시 프로젝트 API + 전체 Issue Type API 조회.
  types는 실제 프로젝트 목록으로 유지하고 display_types에 전체 조회용 목록을 저장.
  전체 조회 실패는 catalog_warnings에 보존, 프로젝트 매핑은 계속 가능.
  map_tracker에서 프로젝트 밖 ID를 명시적으로 거부.
- import_wizard.py: 기존 Step 2 Treeview에 ID, Regular/Sub-task/미확인,
  프로젝트 사용 가능/미적용 구분 표시. 미적용 행은 회색, 저장 버튼 비활성화.
  백엔드에서도 매핑 차단. Jira 원본 이름을 그대로 표시.
  Priority/Status/Resolution 조회 버튼은 원본 JSON ID/이름을 읽기 전용 창에 표시.
  각각 실패를 독립 표시. 글로벌 정의는 프로젝트별 사용 가능 목록과 다름을 명시.
- 테스트 fixture: 실제 추가 호출인 /rest/api/2/issuetype에 응답 추가.
- 신규 tests/test_wizard_global_catalog.py: 100 전체/20 적용, 80 매핑 차단,
  원본 ID 및 고객 사용자 정의 이름, 타입 구분, 403 시 프로젝트 목록 유지,
  글로벌 Priority 권한과 Issue Type 조회 독립성을 검증.

## 결과

전체 300 실행: 294 통과, 6 GUI 건너뜀, 실패 0.
신규 4개 통과. 실제 Tk 디스플레이/Windows 동작은 미검증.
100/20 검증은 실제 Wizard 모델과 UI에서 호출하는 표시 함수에 대한 Mock 테스트다.
고객사 Jira/CB 연결 및 실제 서버 쓰기는 수행하지 않았다.

RUN_WINDOWS.bat, config.local.json, .migration을 변경하지 않았다.
기존 조회 결과만 표시하며 Jira Scheme 변경이나 CB Choice 생성은 하지 않는다.
프로젝트 API 응답을 해당 계정 기준의 적용 타입으로 취급하며 관리자 전용 보안
Scheme이나 모든 사용자의 권한을 검증한 것으로 간주하지 않는다.
PR 자동 병합 없음.
