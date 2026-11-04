# 기존 Wizard 실행 경로 복구 — 2026-11-04

작업 브랜치: feat/import-wizard-scoped-mapping
Windows 실행 파일: jira-xray-cb-migration/RUN_WINDOWS.bat
실제 프로그램: 기존 import_wizard.py. 새 UI/별도 이관 엔진을 만들지 않았습니다.

## 원인

test/mock-five-stage-e2e의 windows_live_test.py가 --live-test를 전달했습니다.
이 옵션은 launch_connected/launch 대신 single_import.launch_live_setup을 열고,
그 화면은 프로젝트 조회 전에 cb.discover()를 호출합니다. 기존 메인 Wizard의
Jira PAT/CB Basic 입력 화면과 Swagger-independent 프로젝트 조회 경로를 우회한 것이
실행 화면·동작이 바뀐 직접 원인입니다. 고객사 HTTP 오류 원인까지 확정한 것은 아닙니다.

## 비교와 최소 통합

- 기준 a327a72의 기존 연결, 프로젝트/Tracker, 체크박스 검수, Import, 관계 기능 유지.
- test 브랜치: Jira 날짜 +0900 정규화, 페이지 total/중복 검사,
  Run 동기화의 무관한 필드 권한 보존과 해당 Mock 테스트만 통합.
- review/jira-xray-v04: 별도 엔진/설정 형식/Basic Jira 인증 및 미검증 단계 차단을
  기존 Wizard에 복사하지 않았습니다. 기존 원격 조회/저널/관계 재검증 경로를 유지합니다.
- field-definition-contract의 신규 ID 정책도 이번 연결 복구 범위에서 제외했습니다.
- 프로젝트 실패에 서비스명, 실제 요청 경로와 HTTP 상태를 표시합니다.
  응답은 JSON 인증/권한 오류, HTML, Seraph 인증 거부 등 실제 신호를 분류합니다.
  임의 오류 본문/토큰은 출력하지 않고 분류 불가는 명시합니다.

## 데이터 보존

RUN_WINDOWS.bat는 config.local.json이 없을 때만 config.json을 복사합니다.
기존 설정을 덮어쓰거나 migration.state_dir를 변경하지 않습니다.
.migration을 이동/초기화/삭제하지 않습니다. 기존 설정과 .migration은 원래 위치에서 유지하세요.
고객 PC 자료는 이 환경에 없으며 직접 수정한 적이 없습니다.
이전에 별도 실행본에 만든 상태는 자동 병합하지 않습니다.

## 검증

변경 전: 230개 실행, 224개 통과, 6개 skip.
변경 후: 244개 실행, 238개 통과, 6개 skip, 실패 0.
실제 Windows Tk 클릭 및 고객 프로젝트 조회는 아직 미검증입니다.
실제 HTTP 403의 서버측 원인은 수정본의 서비스/경로/상태/응답 분류로 확인해야 합니다.
기존 단일 Issue 제한을 확인하고 승인된 테스트 대상만 Import하세요.
