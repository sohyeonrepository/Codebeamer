# 신규 필드 HTTP 400 조회 진단

기존 실행기는 그대로 사용합니다. 이 도구는 실패 원인을 확인하기 위한 **GET 전용** 보조 도구입니다. POST/PUT/PATCH/DELETE는 HTTP 전송 전에 차단합니다. `config.local.json`, `.migration`, 기존 실패 기록을 수정하지 않습니다.

## Windows 실행

1. `test/cb-item-post-minimal-20261106` 브랜치의 최신 소스를 받습니다. 기존 설정과 `.migration`은 보존합니다.
2. `jira-xray-cb-migration/DIAGNOSE_FIELD_WINDOWS.bat`를 실행합니다.
3. `Failed field journal JSON/ZIP path:`에서 아래 경로 또는 업로드했던 ZIP의 경로를 입력합니다.

   `.migration\test-runs3\field-eb6ab42970c32b45b4983e22933c0f50ed795fb2ab852eb13149da997f50a650.json`

4. 설정의 Codebeamer URL과 인증 환경변수를 사용합니다. 인증정보가 없으면 콘솔에서 ID와 비표시 Password를 입력합니다. URL이 비어 있으면 `/cb/api`까지 포함한 실제 API 기본 URL을 입력합니다.
5. `diagnostics/field-diagnostic-*.zip`의 `report.json`을 확인한 뒤 공유합니다.

Swagger 자동 조회가 실패하면 정확한 URL 또는 서버에서 받은 JSON을 직접 지정할 수 있습니다.

```bat
py -3 diagnose_field_creation.py --journal ".migration\test-runs3\field-eb6ab42970c32b45b4983e22933c0f50ed795fb2ab852eb13149da997f50a650.json" --swagger "file:C:\diagnostics\swagger.json"
```

읽기 요청은 전체 약 60초 한도로 제한합니다. API 실패도 가능한 범위에서 보고서에 기록합니다. 원본 Configuration 전체, 필드값, 인증정보, 설정 파일을 ZIP에 넣지 않습니다. 필드명/ID, 스키마 및 API 오류 정보는 포함됩니다.

## 확인 범위와 제한

- 현재 Tracker Configuration과 실제 필드 목록 조회
- Labels 존재 여부 및 기존 요청과 현재 필드 Permission 차이
- 실제 서버 OpenAPI의 요청 스키마 및 Permission 하위 타입과 이전 요청 비교
- 이전 응답 본문이 기록돼 있는지 확인

첨부된 두 실패 ZIP의 JSON은 동일했습니다. 새 로그의 중단은 이전 pending 요청을 재실행하지 않은 결과입니다. 기록에는 permission 관련 요약 오류만 있어 최초 HTTP 400의 상세 본문을 복원할 수 없습니다. 현재 권한/스키마 진단으로도 원인이 확정되지 않을 수 있습니다. 스키마 검사 통과는 실제 POST 성공이나 운영 버전 호환성을 의미하지 않습니다.

상태 파일을 삭제하거나 Permission을 일괄 Unrestricted로 바꾸지 마세요. 이 도구는 필드를 생성하지 않으며 Import 성공을 판정하지 않습니다.

## 자동 검증

`python -m unittest tests.test_field_diagnostic_script -v`

전송 전 쓰기 차단, JSON/ZIP 동일성, 조회 전용 수집 및 원본 데이터 비변경을 검사합니다. 고객 서버와 Windows 실행 검증은 별도입니다.
