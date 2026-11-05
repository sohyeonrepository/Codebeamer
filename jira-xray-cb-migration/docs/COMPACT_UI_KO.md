# 간소화된 Jira → Codebeamer Wizard

현재 작업 브랜치의 기본 UI는 **서버 연결 → 프로젝트 연결 → 데이터 가져오기(한 화면)** 로 구성됩니다.

## 사용 흐름

1. **서버 연결**: Jira 서버/토큰, CB 서버/아이디/비밀번호 입력. 이전 연결 로직을 재사용합니다.
2. **프로젝트 연결**: Jira 프로젝트, 기존 CB 프로젝트 선택 → Jira TYPE, CB Tracker 연결.
3. **데이터 가져오기**: Jira 필드별 체크박스(기본 미선택), Jira 필드명, Codebeamer 대상 필드, Jira 타입을 한 표에 표시합니다.
   - 필터: **현재 사용 중인 필드**(기본) / **전체 필드**.
   - 현재 사용 중: 선택된 Jira Issue Type의 Screen/Field Configuration에 설정되었거나 실제 Issue에서 값이 확인된 필드, 또는 필수 필드. 단순히 Jira 전체 필드 API에 null로 등장하는 필드는 사용으로 취급하지 않습니다.
   - CB 필드 셀 클릭 → 드롭다운으로 기존 필드 매핑 또는 **＋ 신규 필드 생성…** 선택.
   - 신규 필드 선택 → 별도 팝업에서 필드명, 검증된 생성 타입, 다중 선택, Choice 옵션(원본 조회값 자동 채움), Reference 대상 Tracker 설정.
   - 원본 Jira 필드와 대상 필드의 불일치/중복 등은 기존 매핑 검증을 유지합니다.
   - 생성 팝업의 저장은 **생성 계획 저장**입니다. 실제 서버 필드 생성은 사용자 Import 승인 이후에만 진행됩니다.
4. **완료 · 매핑 저장**: 체크된 필드만 이관 대상으로 유지. **실제 Import 실행…**은 별도 승인 필요.

Xray Test Set/Test Plan/Test Execution/Test Run 관계는 별도의 Reference 및 테스트 전용 API 검증이 필요하므로 **Xray / Reference 고급 설정**에서 기존 기능을 사용할 수 있습니다.

## 실행

```bat
python import_wizard.py --config config.local.json --prompt-auth
```

- 기존 Wizard로 되돌리려면 `migration.wizard.legacy_ui: true` 사용.
- API `lookup` 누락에 따른 중복 검사 차단 및 실제 서버 POST 검증은 UI 변경과 별개의 문제이며, 본 커밋으로 해결됐다고 간주하지 않습니다.
- 실제 Jira/Codebeamer 서버 UI 테스트는 아직 수행되지 않았습니다.

## Import 결과 및 고급 설정 (2026-11-05)

- **3. Import 결과 / 로그** 탭에서 승인 범위, 선택 필드의 대상 속성, 실행 결과, 오류 코드/메시지 및 상세 결과 파일 경로를 표시합니다. 전체 JSON은 화면에 출력하지 않습니다.
- Jira **Summary → Codebeamer name**, **Description → description**을 수동 선택할 때에도 기본 속성 키를 유지합니다. 기존 `migration/wizard.py`의 자동 추천 및 이전 Field ID 보정 로직은 그대로 보존합니다.
- **고급 설정**은 예전 전체 Wizard로 전환하지 않고, 현 UI 안의 팝업으로 **Jira Issue Link Type → Codebeamer Association Type**과 방향 반전을 저장합니다.
- Xray Test Run 전용 관계, Reference 값 생성/변경은 이 간소화 팝업에서 아직 지원하지 않으며, 지원하는 것처럼 표시하지 않습니다.
- **중복 확인 `lookup` 누락은 아직 별도 해결 대상**입니다. 로그 화면이 도입되었다고 POST가 성공했다는 뜻은 아닙니다.

## Jira Key 기반 중복 확인 (Java 마이그레이터 로직 적용)

`CbCloudTestRunMigrator.java`의 `allTrackerItemRefs()`, `loadExistingRuns()`, `loadTestCaseMap()` 조회 방식을 Python에 적용했습니다.

- 대상 Tracker의 **Jira Key** 필드가 정확히 1개 존재하고, Swagger로 Text FieldValue 쓰기 구조가 확인되어야 합니다.
- UI에서 Jira Summary/Description만 체크하더라도 Jira Key는 도구 내부의 **이관 식별 필드**로 자동 저장하도록 요청을 구성합니다.
- Item 생성 전 페이지별 Tracker Item 전체 목록을 조회하고 각 Item을 GET하여 Jira Key가 같은지 검사합니다.
- 정확히 한 건이면 기존 Item과 대응시키고, 2건 이상이면 오류로 중단합니다. 읽기 실패·응답 구조 불일치·불완전한 페이지도 '중복 없음'으로 취급하지 않습니다.
- 신규 생성 후 Item GET으로 Jira Key가 실제 반영됐는지 검증합니다.
- Jira Key 필드가 없거나 쓰기 형식이 확인되지 않은 Tracker는 **이관 전에 차단**합니다. 자동 필드 생성이나 임의 이름 기반 추측은 하지 않습니다.
- 이 코드는 서버에서 아직 실행 검증하지 않았고, GET 응답의 `customFields` 구조가 다른 서버에서는 어댑터 추가가 필요합니다.

검증용 오프라인 테스트: `python -m unittest discover -s tests -p 'test_jira_identity_lookup.py'`
