# Windows 실제 서버 테스트

대상 폴더: jira-xray-cb-migration. Python 3.12 권장(최소 3.10), Tcl/Tk 포함.
python.org Windows 설치 시 Python Launcher와 Tcl/Tk를 설치하세요.
이 폴더는 표준 라이브러리를 사용하며 pip 설치가 필요하지 않습니다.

1. ZIP을 새 폴더에 풉니다. 기존 config.local.json 및 .migration 폴더를 삭제하지 마세요.
2. RUN_WINDOWS_MOCK_TESTS.bat 실행: 가짜 서버 기반 회귀 확인. 실제 서버 호환성 검증이 아닙니다.
3. RUN_WINDOWS_LIVE_TEST.bat 실행: config.local.json이 없을 때만 config.json을 복사하여
   서버 URL을 비웁니다. 기존 설정은 덮어쓰지 않습니다.
4. 서버 URL/인증 입력, 연결 / 실제 프로젝트 조회(GET), 기존 테스트 프로젝트와 Tracker 선택.
5. 테스트 Jira Issue 키 하나를 지정하고 요청 확인 화면을 엽니다.
6. 읽기 전용 사전 확인 / 요청 보기를 실행합니다. blockers가 있으면 실제 요청을 보내지 않습니다.
7. 기본 테스트는 Item 1건 생성 확인입니다. 필드/첨부 검증은 '필드 매핑·첨부 1건 테스트'를 이용하세요.
   이 제한 테스트를 전체 History/Xray/R4J 이관 성공으로 해석하지 마세요.
8. 표시된 요청과 대상 확인 후 Import… 및 승인 확인창에서 승인하면 실제 서버 쓰기가 발생합니다.
9. 실제 생성 ID와 재조회 결과, .migration/windows-live-test 아래 보고서를 확인합니다.
   실패 시 상태 폴더를 삭제하거나 자동 재실행하지 말고 오류/요청 기록을 확인하세요.

수동 실행:

    py -3 windows_live_test.py

기존 전체 Wizard(별도 검증된 설정 필요):

    py -3 import_wizard.py --config config.local.json

HTTP 사용 여부, 실제 OpenAPI 및 생성/조회 어댑터가 확인되지 않으면 기존 프로그램이 차단합니다.
3.3.0.0 또는 2.2.1.4 OpenAPI를 고객 3.0.0.7 계약으로 대신하지 마세요.
사용자 PC의 localhost는 그 PC 서버만 가리킵니다.

이번 실행 파일은 클라우드에서 설정 보존 테스트와 회귀 테스트를 했습니다.
Windows BAT/Tk 실제 실행 및 실제 Jira/Codebeamer 쓰기는 이 환경에서 미검증입니다.
로그와 보고서에는 원본 데이터가 포함될 수 있으므로 비공개로 보관하세요.
