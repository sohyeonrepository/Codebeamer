# Jira → Codebeamer 마이그레이션 문서 안내

코드와 문서는 기존 경로를 유지합니다. 실행 및 테스트 경로가 깨지지 않도록 파일 이동 대신 문서를 목적별로 분류했습니다.

## 시작·실행
- [Wizard 사용·구조](import-wizard.md)
- [1건 실서버 테스트](live-single-import.md)
- [통합 검증](integration-validation.md)

## Jira 필드·타입 매핑
- [Jira 필드 타입 매트릭스](jira-field-type-matrix.md)
- [Jira DC 전역·프로젝트 필드 카탈로그](jira-dc-global-vs-project-catalog.md)
- [Connector 시스템 필드 정책](connector-system-field-policy.md)
- [필드 매핑 오류 검증](field-mapping-repair-validation.md)
- [기존 Intland Connector 참고](intland-jira-connector-reference.md)

## Xray / Jira 관계
- [Xray DC와 Cloud API 차이](xray-dc-vs-cloud-api.md)
- [Xray 고객 데이터 원본 검수](xray-customer-source-review.md)
- [Test Execution 원본 보존 정책](xray-execution-source-policy.md)
- [Xray 통합 검토](xray-integration-review.md)
- [Xray E2E 후속 작업](xray-end-to-end-followup.md)

## 과거 검토·복구 기록
- [Wizard 복구](canonical-wizard-restoration.md)
- [Jira ZIP Connector 감사](jira-zip-binary-connector-audit.md)
- [PR4 통합 검증](pr4-integration-validation.md)
- [Wizard 글로벌 카탈로그 검증](wizard-global-catalog-validation.md)

## 화면 예시
- [매핑 미사용 값 미리보기](mapping-helper-missing-value-preview.png)
- [매핑 도우미 화면](mapping-helper-preview.png)
- [타입 미리보기](mapping-helper-type-preview.png)

문서는 참고 자료이며, 실제 서버 API 지원 여부와 완료 상태는 별도로 검증해야 합니다.
