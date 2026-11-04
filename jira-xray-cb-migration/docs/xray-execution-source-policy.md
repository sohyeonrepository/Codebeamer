# Test Execution 원본 전용 기본 정책

기본 대상: 기존 Test Plan 정책, Test Set, Test Case, Test Run.
`full_xray` 엔진에서 Test Execution은 원본 조회·실행 컨텍스트로 보존하고
별도 CB Item/Tracker 생성에서 제외한다. Test Plan을 Release로 매핑하는 등
기존 대상 정책은 변경하지 않는다. 고정 Case→Set→Run→Plan 계층을 만들지 않는다.

- Execution ID/Key, fields(환경 포함), 원본 Plan 연결을 로컬 원본 보존 영역에 저장.
- Run ID + Test Key + Execution Key 일치 확인, 기존 Run ID 연결 변경 차단.
- 모든 Case/Plan/Set Item 생성 후 기존 `records.test_run()`으로 Run 생성.
- Execution Item 인수는 None. `_executionSource`에 ID/Key/fields/관계 보존.
- Test Plan→Test, Test Set→Test는 기존 명시적 원본 관계 어댑터를 사용.
- Execution 관련 원본 관계는 보존하되 임의 CB 관계로 바꾸지 않음.
  네이티브 Plan→Run 관계 쓰기 계약이 없으면 limitations와 complete=False로 표시.

## 필요한 설정과 현재 한계

기존 Wizard 화면/실행기는 유지한다. Source-only Execution을 선택한 일반 issues
모드는 쓰기 전에 차단된다. `migration.mode=full-xray` 및 다음 계약이 필요하다.

- `full_xray.issue_types`: plan/test/set/execution 원본 실제 타입 ID 또는 이름.
- `full_xray.relationships`: 실제 Plan/Test, Set/Test, Plan/Execution 조회 경로.
- `run_adapter.execution_mode=source_metadata`.
- `run_adapter.fields`: `_executionSource` 또는 해당 하위 속성의 검증된 대상 매핑.
- `run_adapter.test_issue_id_path`: 원본 Run의 실제 Test 식별 경로.
- 기존 Run Tracker 설정 및 run_lookup/create/get 등의 검증된 쓰기 계약.

이 설정을 임의 기본 Field ID나 Tracker ID로 채우지 않는다. 실제 CB 3.0.0.7
Run 생성/필드/관계 API 계약은 이번 환경에서 확인하지 못했다.
Wizard에 원본 전용 scope 전용 새 UI는 추가하지 않았다. 기존 매핑/분석 사전검사가
추가 설정을 요구할 수 있으므로 설정 없는 고객사 원클릭 성공을 주장하지 않는다.
원본 컨텍스트 보존은 네이티브 History 저자·시각 복원을 의미하지 않는다.

기존 동작이 명시적으로 필요한 배포에서는 `full_xray.create_execution_items=true`로
이전 별도 Execution Item 정책을 선택할 수 있다. 기본값은 false.
기존 네 종류 대상 E2E 테스트는 이 호환 모드를 명시하여 보존하였다.

## 테스트

전체 실행 결과는 커밋 보고 참조. `tests/test_full_xray.py`에서 다음을 검증한다.
기본 Execution 대상 Item 미생성, 원본 ID/Key 보존, Run 호출 시 Execution Item=None,
실제 Case 연결, 메타데이터 매핑 누락 시 전체 Item 쓰기 전 차단, Plan→Execution
원본 보존과 미확인 관계의 완료 차단, Wizard 자동 Tracker 생성 차단.
실제 서버 쓰기와 PR 병합은 수행하지 않는다.

최종 회귀 결과: 296 실행, 290 통과, GUI 6 건너뜀, 실패 0.
카탈로그 PR #4 수정과 PR #1~#3 선별 통합을 포함한 검수 브랜치에서 실행했다.
