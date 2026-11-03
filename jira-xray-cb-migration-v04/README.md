# 검토 수정본 안내

현재 검증 결과와 실제 쓰기 차단 범위는 **REVIEW_REPORT.md**를 우선 확인하세요.
아래는 원 패킷 설명이며 실제 서버 성공을 의미하지 않습니다.

# Jira DC + Xray → Codebeamer · 데스크톱 마이그레이터 v0.4

## 실행

Python 3.11 이상을 설치하고 Windows에서 `RUN_WINDOWS.bat`를 실행하거나:

```bash
python -m pip install -r requirements.txt
python app.py
```

Tkinter 기반 GUI입니다. 모든 연결은 사용자가 입력하는 URL과 계정 정보를 사용합니다. **비밀번호는 저장되지 않습니다.**

## 화면의 5단계

1. **Jira/CB URL, ID, 비밀번호** 입력. Jira `GET /rest/api/2/myself`로 인증 확인. CB는 Tracker ID가 있으면 Configuration GET으로 확인.
2. **프로젝트 선택**: Jira 프로젝트 목록, CB 프로젝트 목록 조회(경로는 `cb_projects_path`에서 변경 가능), 또는 프로젝트 ID 직접 입력.
3. **Tracker/Field 매핑**: 기존의 체크박스 방식. Jira 필드를 체크하고 CB 필드 ID를 입력. CB Tracker Configuration을 GET하여 백업. Test Plan → Release Tracker ID는 사용자가 지정.
4. **데이터 이관**: Jira/Xray 원본 추출, Item dry-run/쓰기, Test Run 추출·이관, Java History Queue 및 외부 Writer 호출.
5. **관계 이관**: Reference 필드 ID별 매핑 후 연결, Related Association, 결과 검증.

## 꼭 확인할 사항

- 프로젝트와 필드를 자동으로 **생성하는 서버 변경 기능은 포함하지 않았습니다.** 케피코 Java가 사용하는 내부 Configuration/Import 로직은 독립 Java 서버 모듈이 아니며, Codebeamer 3.0.0.7에서 정상 동작하는지 확인할 수 없습니다. 기존 필드를 직접 선택하거나 필드 구성 JSON을 서버/Swagger로 검증한 다음 어댑터를 활성화하세요.
- `config.json`의 `cb_api.verified_against_3_0_0_7`은 기본 `false`입니다. 실제 API 스키마 검증 없이 `true`로 설정하지 마세요. **모든 쓰기 작업은 사용자의 명시적 허용 및 확인을 필요로 합니다.**
- Xray DC의 `xray_paths`와 실제 Test Run 응답 필드 구조는 환경별로 다를 수 있습니다. 경로를 입력하지 않으면 Xray 링크가 자동으로 채워진다고 간주하지 않습니다.
- History Java Writer는 외부 플러그인 **실행 인터페이스만** 제공합니다. 검증된 3.0.0.7 Writer가 없으면 원본 보존만 수행합니다. 이를 네이티브 History 복원 완료라고 표시하지 않습니다.
- 케피코 Test Run Script 기준으로 실행 결과/세션 매핑 규칙이 다를 수 있습니다. UI의 Xray Test Run 연결은 **전용 매핑 검증** 후 적용하세요.
- 현 버전의 Item 생성과 Test Run/관계 쓰기는 서버 실제 인증 실행을 완료한 상태가 아닙니다. 운영계에 바로 적용하지 말고 격리 테스트 서버와 백업을 이용하세요.

## 구조

- `app.py`: 사용자 요청 5단계 데스크톱 UI
- `migrator.py`: Jira / Xray 추출과 정규화
- `writer.py`: 필드 매핑, Item 작성, Reference 연결, ID 체크포인트
- `full_migration.py`: Xray runs, Codebeamer REST v1 Test Run, Java History Bridge, 점검
- `related.py`: 일반 Linked Issues → Related Association
- `config.example.json`: 설정 예제 (복사해 `config.json`으로 사용)
- `RUN_WINDOWS.bat`: Windows 실행기
- `test_*.py`: 자동화 회귀 테스트

## 설정

`config.example.json`을 `config.json`으로 복사하고, 5단계 UI에서 저장할 수 있습니다. API 작성 어댑터와 Xray 경로는 해당 서버의 실제 OpenAPI 결과를 기반으로 설정합니다. 비밀번호는 `config.json`에 넣지 마세요.

## v0.4 기능 추가 및 케피코 Test Run 스크립트 참고

- `field_config.py`: 현재 Tracker Configuration 전체를 GET하여 기존 필드 보존 검증 및 신규 필드 후보 생성. **기본 미리보기**. 실제 저장 시 재조회로 충돌을 검사하고 POST 뒤 GET으로 확인합니다. 서버별 `referenceId` 배정 규칙·필드 타입 설정은 실서버 검증이 필요합니다.
- `writer.py`: 여러 Test Case가 하나의 Reference 필드에 연결되면 일괄 처리. `cb_api.reference_body_bulk`가 실제 검증된 형태로 제공되지 않으면 **쓰기 차단**합니다. 이때 기존 참조값도 병합해야 하는지 확인해야 하며, 기존 참조값을 대체하지 않는다고 보장할 수 없습니다.
- `full_migration.py`: 케피코 Test Run 이관 규칙을 참고해 같은 Test Execution/Case의 **최신 업데이트**를 선택하고, Skipped/빈 결과는 정상 판정 없이 원본을 기록합니다. **실행 시각/결과별 첨부/원본 Status 전용 필드** 복원은 완성되지 않았으므로 고객 검수 전 사용하지 마세요.
- **Java History**: 케피코 Java는 `RemoteSynchronizationController`·`TrackerItemRevisionDto`를 직접 사용하는 Codebeamer 서버 내부 코드입니다. 독립 실행 Java 프로그램으로 현재 패키지에 포함할 수 있는 호환 바이너리는 없으므로 Java Writer 호출 형식만 제공하며, **네이티브 History 쓰기는 아직 작동하지 않습니다.**

검증 결과: 기본 소스 로딩 및 23개 모킹 기반 단위 테스트 통과. 고객 서버 연결·실제 데이터 생성·추적성 GET 검증은 수행하지 않았습니다.
