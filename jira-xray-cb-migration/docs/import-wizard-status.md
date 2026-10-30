# Import Wizard 구현 현황

실행: `python import_wizard.py` (Python 표준 라이브러리, Tkinter 필요).
기존 `main.py`, `mapping_helper.py` 실행 방법과 엔진은 유지합니다.

**이 PR은 전체 Import 완성본이 아닙니다.** 실제 구현이 없는 기능은 설정 플래그로
활성화할 수 없으며, 전체 범위 Import 버튼은 비활성화됩니다. CLI의 기존 명시적
`--apply` 동작은 유지하지만 `--wizard-plan`으로 들어오는 실행은 동일하게 차단합니다.
실제 고객 서버에서 쓰기 테스트를 하지 않았습니다.

| 기능 | 현재 동작 |
|---|---|
| 연결 설정 | Jira/Codebeamer URL, Basic/Bearer 인증 입력, GET 연결 테스트 |
| 연결 정보 재사용 | URL/인증 방식/환경변수 이름만 로컬 저장, 비밀번호·토큰은 메모리만 사용 |
| 프로젝트 | API 전체 목록 확인, 기존 Codebeamer 프로젝트만 선택, 생성 경로 없음 |
| 분석 범위 | 선택 Jira 프로젝트의 전체 이슈·필드값·changelog·댓글·작업로그 조회, 표본 제한 없음 |
| 자동 매핑 | 기존 도우미 재사용, 이름/별칭·서버 타입·전체 관측값·정의 변경·충돌 확인 |
| 기본 화면 | 자동 매핑 숨김, 생성안·확인 필요만 표시, 전체 상세 보기 제공 |
| 예외 수정 | 기존 도우미를 전체 분석 결과로 열기, 기본적으로 예외만 표시, Wizard에서는 서버 생성 차단 |
| 생성안 | 누락 Tracker 이름/프로젝트 제안, 확인된 타입에 한해 새 Field 정의 제안, 없는 Choice 이름 제안 |
| 실제 생성 | 기존 도우미의 명시적 생성 기능 유지. Wizard 자동 생성 실행은 아직 미구현 |
| 실행 경로 | 기존 main/Engine 호출 경로·진행 이벤트 추가. 미지원/미해결이면 실행 전 차단 |
| 상태 격리 | source/target URL·원본 프로젝트·대상 프로젝트 조합별 분석/매핑/엔진 상태 디렉터리 분리 |
| 재시도 | 분석 재실행 지원. 기존 Engine의 lookup/state.once 중복 방지 유지. 불확실한 쓰기는 자동 재시도 금지 |

## 전체 Import를 차단하는 기능

- R4J Tree·Revision·Baseline의 원본 API 추출 및 대상 복원 어댑터가 없습니다.
- History는 기존 로컬 원본 보존/재현 틀이 있지만 Codebeamer 별도 조회 기능이 없습니다.
  Codebeamer 기본 revision 작성자/시각 복원을 지원한다고 표시하지 않습니다.
- Description의 Jira 이미지·내부 링크를 Codebeamer URL로 다시 쓰는 기능이 없습니다.
- Parent-Child 의존성 순서 및 복원 검증이 없습니다.
- 일반 Jira 전체 이슈와 Xray Plan/Test/Set/Execution/Run을 한 작업에서 모두 다루는
  통합 실행기가 없습니다. 기존 `full-xray`는 Xray만 선택하는 별도 모드입니다.
- 댓글/작업로그/관계 및 여러 Codebeamer 쓰기 API는 서버별로 검증한 어댑터가 필요합니다.
- 신규 Tracker의 서버별 필수 타입/템플릿과 새 Field의 payload 바인딩 계약을 자동 확정하지 않습니다.

이 항목을 단순히 “건너뛰기” 처리하거나, 설정값으로 구현 완료로 바꾸지 않습니다.
미해결 매핑, 읽기 실패, 부분 페이지, 불완전한 changelog도 Import 차단 사유입니다.

## 로컬 파일

`.migration/wizard/connection.local.json`: 인증 비밀 제외 연결 선택자.
`.migration/wizard/<프로젝트 조합 해시>/`:

- `analysis-report.json`: 요약, 매핑/생성안, 미지원·미해결 사유, 쓰기 0 표시
- `mapping.local.json`: 기존 도우미와 main이 읽는 호환 매핑 overlay
- `config.local.json`: 자동 생성한 실행 설정. 기존 config 원본을 덮어쓰지 않음
- `import-state/`: 향후 활성화 시 기존 Engine 상태 저장 경로

전체 원본 이슈는 분석 중 메모리에 보유합니다. 대형 프로젝트에서는 시간이 오래 걸리고
메모리 사용량이 커질 수 있습니다. 실행 로그에는 인증 헤더와 응답 본문을 기록하지 않습니다.
서버의 목록 API 응답이 페이지 형태이면 전체 건수의 일치를 검증합니다. 확인되지 않은
페이지 규약을 임의 추정해서 일부 목록만 보여주지 않습니다.

## 검증

실제 고객 서버 대신 로컬 HTTP 모의 서버와 Tk 가상 화면으로 자동 테스트합니다.
전체 이슈 조회(20건 초과), 잘못된 타입, 모호한 Tracker, 누락 선택값, 부분 페이지 거절,
비밀값 저장 제외, 위조된 ready 계획의 실행 차단, 기본 예외 필터를 검증합니다.
