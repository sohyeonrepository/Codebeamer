# 서버 없는 Mock E2E 검증

대상: jira-xray-cb-migration (v04 폴더 아님).

실행: Python 3.10 이상, 표준 라이브러리. 저장소 폴더에서:

    python -m unittest tests.test_mock_five_stage tests.test_relation_scope tests.test_full_xray tests.test_run_extensions -v
    python -m unittest discover -s tests -v
    python -m compileall -q clients migration tests import_wizard.py

실제 인증정보나 서버가 필요하지 않습니다. 전체 테스트에는 127.0.0.1 임시 포트로
HTTP 서버를 여는 테스트가 있어 로컬 socket 권한이 필요합니다.

tests/fixtures/five_stage_source.json: 합성 Plan/Case/Set/Execution 4건과 Run 1건.
MockJiraDCXray는 Jira 프로젝트/유형/필드/Issue/History/Comment/R4J 및 Xray detail GET을
제공합니다. 기존 tests.test_import_wizard.Target은 상태를 보존하는 CB operation adapter mock이며
Item/Field/Attachment/Comment/Association 생성과 GET, 실패 주입을 제공합니다.
실제 HTTP/인증/바이너리 테스트는 test_migration의 ThreadingHTTPServer를 사용합니다.
이 둘을 실제 Codebeamer 3.0 서버 에뮬레이터 또는 Swagger 준수 인증으로 해석하면 안 됩니다.

## 검증 범위

- 연결/조회, 프로젝트 선택, 유형별 Tracker와 체크박스 선택, 승인 전 쓰기 없음.
- 실제 Wizard/Engine/SQLite로 4종 Item 생성, 이름 값 GET, 재시작 후 중복 없음.
- 실제 XrayClient로 Run/Execution/Test ID와 step 원본 검증.
- comment 응답 유실 후 readback 재개, 분석 timeout 쓰기 없음.
- 페이지 중복 ID, total 변경, 중간 빈 페이지 오류.
- 기존 relation_scope: 방향 반전, 단일 Reference 충돌, 다중 참조 보존,
  외부 대상 검증, 개별 실패 후 계속 처리, 재시작 중복 방지.
- 기존 full_xray: Plan 우선 순서, 의존성 누락 사전 차단. 이 테스트는 일부 engine 메서드를 mock하므로
  4종 Item+Run+모든 관계의 단일 완전 통합 시험은 아닙니다.
- 기존 run_extensions/verified_xray: Run 스키마/이벤트/참조 분리 검증.

## 실제 코드 수정

clients/jira_client.py pages: total 음수/잘못된 타입/초과/변동 및 중복 ID를 차단.
migration/run_schema.py sync_run_fields: 무관한 기존 필드 권한을 기본 보존.
전체 필드 권한 변경이 명시적으로 필요한 기존 사용자는 field_sync.apply_permission_to_all_fields=true 필요.
README: CLI 기본 Test Run 전용과 Wizard/전체 Xray 경로 구분.

## 결과와 제약

실제 고객 3.0.0.7 API 계약, Native History 작성자/시각 복원, 실서버 신규 필드/옵션/Run 생성은
미검증입니다. UI 테스트 일부는 DISPLAY가 없어 skip됩니다. 서비스 단계 호출 검증과 실제
Tk 창 클릭 검증을 구분합니다. Test Plan→Release 전용 타입과 모든 Xray 관계의 서버 의미 검증도 남아 있습니다.
새 테스트의 네 가지 Issue를 성공적으로 이관한 것을 전체 프로젝트 이관 완성으로 보고하지 않습니다.

최종 실행: unittest 242개, 236개 통과, 6개 skip, 실패 0. compileall 통과.
실제 고객 서버 호출 0. 고객 인증정보 사용 0.
