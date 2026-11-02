# 전체 마이그레이션 통합 검증 현황

이 문서는 `feat/import-wizard-scoped-mapping`의 기능 상태를 오프라인 검증과 실제 서버 검증으로 구분한다. Mock 또는 로컬 테스트 통과를 실제 Codebeamer 성공으로 간주하지 않는다.

## 검증 기준

- **구현 완료**: 실행 경로와 실패/재시도/재조회 검증이 코드에 존재한다.
- **Mock 테스트 통과**: 저장소 테스트 더블 또는 로컬 HTTP 서버로 검증했다.
- **실제 서버 테스트 통과**: 실제 서버에서 생성 ID와 GET 재조회 결과를 확인했다.
- **API/환경 제약**: 고객 서버 계약이나 접속이 없어 현재 환경에서 검증할 수 없다.
- **미구현**: 지원 경로가 없다. 이 상태는 완료로 보고하지 않는다.

## 기능별 상태

| 기능 | 구현 | Mock | 실제 서버 | 남은 제약 및 판정 근거 |
| --- | --- | --- | --- | --- |
| Jira/CB URL 및 비표시 인증 입력 | 구현 완료 | 통과 | 현재 변경본 미검증 | UI 값은 메모리에서만 사용한다. 고객망 접속이 필요하다. |
| Jira/CB 프로젝트 조회 | 구현 완료 | 통과 | 이전 빌드에서 사용자 확인 | 성공 기준 커밋 `e1e694b`의 GET 흐름을 `codebeamer_verified_reads.projects/project`로 재사용한다. 현재 변경본은 고객망 재확인이 필요하다. |
| CB Tracker/Field 조회 | 구현 완료 | 통과 | 이전 빌드에서 사용자 확인 | `codebeamer_verified_reads.trackers/tracker/fields`와 `CodebeamerClient.fields/configuration`을 사용하며 Swagger 실패와 분리했다. |
| Issue Type별 Tracker 매핑/신규 Tracker 계획 | 구현 완료 | 통과 | 신규 생성 미검증 | 실제 생성은 승인 후에만 수행하며 전체 Configuration 계약과 생성 ID/프로젝트 소속을 재조회한다. |
| 실제 Jira 필드명, Hidden 분석, 조합별 매핑 | 구현 완료 | 통과 | 미검증 | Field Configuration이 없거나 실패하면 표시 상태 미확인으로 매핑을 표시하고 Import 전 현재 분석 범위 검토를 요구한다. |
| 자동/수동/신규 Field 매핑 | 구현 완료 | 통과 | 신규 생성 미검증 | 신규 Field/Choice는 전체 설정 보존, 실제 schema 검증, ID 재조회가 필요하다. |
| Item 생성/필드 재조회 | 구현 완료 | 통과 | 이전 빌드에서 Item 1건 사용자 확인 | 사용자가 Summary/Description과 Item 생성을 확인했다. 현재 변경본 및 다른 필드 타입은 고객망 재검증이 필요하다. |
| Attachment 및 Description 이미지 | 구현 완료 | 통과 | 미검증 | 파일명/크기/SHA-256/재다운로드와 Description 링크를 검증한다. 실제 multipart 계약이 필요하다. |
| Comment | 구현 완료 | 통과 | 미검증 | 본문은 Native Comment로 생성하고 원본 작성자/시간은 Legacy JSON에도 보존한다. Native 메타데이터 복원은 미확인이다. |
| Worklog | 조건부 구현 완료 | 통과 | 미검증 | 원본 전체를 Legacy JSON에 보존한다. Native/별도 레코드 생성은 `worklog_adapter`가 실제 서버에 설정된 경우만 실행하며, 없으면 limitation이다. |
| Linked Issue/Association | 구현 완료 | 통과 | 미검증 | 모든 Item ID 이후 방향과 Link Type을 보존한다. 실제 Association type 및 create/get/lookup 계약이 필요하다. |
| Jira Changelog/Legacy History | 구현 완료 | 통과 | 미검증 | 변경 전후 값·작성자·시간·필드를 해시 검증된 Item 첨부 JSON으로 보존하고 건수를 비교한다. |
| CB Native History replay | 조건부 구현 완료 | 통과 | 미검증 | 검증된 scalar 필드만 이벤트별 Revision 증가를 확인한다. 원본 작성자/시간을 Native Revision으로 복원했다고 표시하지 않는다. |
| R4J Tree/Revision/Baseline | 구현 완료 | 통과 | 미검증 | 세 전수 읽기 adapter가 모두 필요하며 Legacy JSON과 건수를 검증한다. 서버별 R4J API가 없으면 Import를 차단한다. |
| Xray Test/Set/Plan/Execution | 조건부 구현 완료 | 통과 | 미검증 | 실제 4종 Issue Type과 membership field adapter가 필요하다. 모든 의존 Item 생성 뒤 관계를 생성·재조회한다. |
| Xray Test Run/History/Evidence | 조건부 구현 완료 | 통과 | 미검증 | Run/Activity 전수성, 대상 Run schema, Test/Execution 참조 계약이 필요하다. 불완전하면 쓰기 전에 차단한다. |
| 중복 방지/실패 재시도 | 구현 완료 | 통과 | 일부 Item 경로만 사용자 확인 | 원본 ID lookup과 SQLite journal을 사용한다. 응답 유실로 상태가 불확실하면 자동 재POST하지 않는다. |
| 여러 Issue Type/전체 프로젝트 비교 | 구현 완료 | 통과 | API/환경 제약 | 실제 전수 실행은 승인된 테스트 대상, 서버 접근, 완전한 API adapter가 필요하다. |

## 회귀 기준과 재사용 코드

실제 Item 생성이 사용자에게 확인된 시점의 기준은 `e1e694b`이다. 조회 복구는 새 URL 후보를 추가하지 않고 당시 실행 경로의 다음 함수를 재사용하거나 고정 GET 계약으로 분리했다.

- `single_import.launch_live_setup.read_operation`
- `single_import.launch_live_setup.load_trackers`
- `SingleImport.prepare`
- `CodebeamerClient.fields`
- `CodebeamerClient.configuration`
- `clients.codebeamer_verified_reads.verified_projects/verified_project/verified_trackers/verified_tracker/verified_fields`

연결 버튼은 `/v3/projects`, `/v3/projects/{projectId}/trackers`, `/v3/trackers/{trackerId}/fields`의 실제 GET 응답을 검증한다. 서버가 이 고정 성공 경로와 다른 계약을 사용하면 성공으로 추측하지 않고 정확한 GET 단계에서 실패한다. Swagger/OpenAPI는 Import 직전 POST/PUT request/response schema 검증에만 사용한다.

## 실제 서버에서 남은 검증 순서

1. 연결 후 Jira/CB 프로젝트와 선택 프로젝트의 Tracker 목록을 확인한다.
2. 선택 Tracker의 Field와 Jira Issue Type 실제 필드명을 확인한다.
3. 테스트 Issue 1건으로 매핑을 저장하고 Import 요청 미리보기의 차단 항목을 해소한다.
4. 테스트 대상에서 Import를 승인하고 Tracker/Field/Item 생성 ID와 GET 결과를 확인한다.
5. 첨부·댓글·Association·Legacy History 첨부와 건수/해시를 확인한다.
6. 별도 승인 범위에서 여러 유형, R4J, Xray, 전체 프로젝트 순으로 확대한다.

현재 실행 환경에는 고객 Jira/Codebeamer 네트워크와 인증정보가 없으므로 4~6단계를 수행하지 않았다.
