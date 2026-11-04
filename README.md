# Codebeamer 프로젝트

고객사 프로젝트와 마이그레이션 도구를 구분해 관리합니다.

## 프로젝트 폴더

| 폴더 | 목적 | 관리 원칙 |
| --- | --- | --- |
| [jira-xray-cb-migration/](jira-xray-cb-migration/) | Jira Data Center + Xray + R4J → Codebeamer 마이그레이션 도구 | 실행 코드, 클라이언트, 테스트, 가이드를 한 폴더에서 유지 |
| [KEFICO/](KEFICO/) | 현대케피코 RV&S → Codebeamer 자료 | 기존 `migrator/`, `rvs-id/`, `test-run/`, `docs/` 구분 유지 |

## Jira 마이그레이션 주요 경로

- `jira-xray-cb-migration/import_wizard.py`: 화면 기반 연결, 필드·관계 매핑, Import
- `jira-xray-cb-migration/migration/`: 데이터 변환·이관 로직
- `jira-xray-cb-migration/clients/`: Jira·Codebeamer 연동 API
- `jira-xray-cb-migration/tests/`: 단위·통합 테스트
- `jira-xray-cb-migration/docs/`: 설계·운영·검증 문서 ([문서 목차](jira-xray-cb-migration/docs/README.md))

## 브랜치 사용

- `main`: 기본 브랜치. 현재 Wizard 변경 사항이 아직 모두 반영된 것은 아닙니다.
- `review/wizard-catalog-integration`: Jira 마이그레이션 Wizard의 **현재 작업·테스트 브랜치**.
- 이전 개발·검토 브랜치는 고유 커밋이 있는지 확인한 뒤에만 삭제합니다.

> 기존 고객 자료와 이전 버전 이력은 임의로 삭제·이동하지 않습니다. 고객사 설정 파일, API 토큰 및 로그는 업로드 전에 민감정보를 제거하세요.
