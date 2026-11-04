# PR #1–#4 선별 통합 검증

- 기준: `feat/import-wizard-scoped-mapping` / `1ce9c5e`
- 검수: `review/wizard-catalog-integration`
- PR #4 검증 대상: `d257fc7`
- PR 전체 병합 없음. 기존 검수 커밋 `8f0ad33`의 선별 변경을 새 기준 브랜치에 재적용.

## 최초 PR #4 결과

신규 5개: 4 통과, 1 오류. 전체 282개: 275 통과, 1 오류, GUI 6 건너뜀.
`test_mapping_catalog_does_not_discard_unseen_issue_types`가 Tracker 목록을 빈 배열로
전달하여 기존 대상 필수 검사에서 실패했다. 테스트에 Mock Tracker를 추가했으며
실제 대상 검사를 제거하지 않았다.

## 수정 및 확인

- `JiraClient.global_catalog`: Issue Type / Priority / Status / Resolution GET,
  ID·이름 보존, 중복 검증, archive 연결 시 원본 응답 보존.
- `JiraClient.issue_types`: 실제 Wizard가 이미 사용하는 프로젝트 API.
  잘못된 응답, 중복 ID, 비Boolean subtask 값을 차단하도록 보강.
- `project_issue_type_catalog`: 프로젝트 정의를 반환. 전체 정의와 합집합하지 않음.
- `mapping_helper.collect_catalog`: 정의 먼저 조회, 표본은 값 분석용으로만 사용.
  표본 없는 타입도 유지. 표본 조회 실패 시 정의는 유지하고 경고.
- `subtask`: True / False / None(미확인). 누락 값을 Regular로 단정하지 않음.
- `project_availability`: configured / observed_only 구분. 과거 Issue에서 발견됐다는
  이유만으로 현재 프로젝트 설정상 사용 가능하다고 확정하지 않음.
- `issue_types_complete`: 메타데이터 조회 실패/중복이면 False. 부분 정의를 완료로 표시하지 않음.
- `security_levels_verified`: False. 실제 프로젝트 보안 Scheme·권한 검증이 필요.

프로젝트 정의 조회 성공은 해당 API가 사용자에게 반환한 범위를 확인한 것이다.
고객사 전체 권한/관리자 Issue Type Scheme과의 일치까지 증명하지 않는다.
글로벌 정의 조회는 읽기용 메서드이며 Priority/Status/Resolution Choice 자동 생성과
연결하지 않았다. 보안 등급 API를 추측하거나 권한을 변경하지 않았다.

## 최종 테스트

`python -m unittest tests.test_jira_catalog_discovery -v`: 11 통과.
`python -m unittest discover -s tests -q`: 291 실행, 285 통과, 6 GUI 건너뜀, 실패 0.
필드 매핑, Choice/User, Xray 및 Mock 단계별 테스트 포함.
테스트 구성은 로컬 Mock이며 실제 고객사 API/Windows GUI 검증이 아니다.

기준 브랜치 대비 `import_wizard.py`, `RUN_WINDOWS.bat`, `config.local.json` 변경 없음.
`.migration` 기존 데이터 수정 없음. v0.4, 대체 실행기, 고객 바이너리 추가 없음.
기존 API 오류 진단 및 Xray 보완 유지. 실제 서버 쓰기 없음.

## 통합 판단 및 고객사 확인

자동 테스트 기준 선별 통합 검수 가능. PR 자동 병합하지 않음.
현재 실행 파일은 기존 `jira-xray-cb-migration/RUN_WINDOWS.bat` 한 개를 유지.
고객사에서는 같은 인증 사용자로 프로젝트 API 원본 ID·이름·subtask와 목록을 대조하고,
Issue 없는 Test Plan/Test Set/Test Execution, 사용자 정의 타입을 확인해야 한다.
글로벌에만 등록된 타입이 프로젝트 사용 가능 타입으로 추가되지 않아야 한다.
보안 수준, 계정 권한, 실서버 쓰기 및 Windows 화면은 별도 검증 필요.
