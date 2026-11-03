# v0.4 코드 수정 및 검증 보고서

2026-11-03. 첨부 패킷을 별도 작업 폴더에서 수정했습니다. 운영 서버 접근/POST,
GitHub 업로드, JAR 실행은 하지 않았습니다. 이 결과는 운영 이관 완성본이 아닙니다.

## 수정과 재현

|등급|위치|재현 조건 및 조치|상태|
|---|---|---|---|
|P0|full_migration.py:206 execute_java_history_bridge|외부 프로세스가 native_history_verified=true만 반환해도 성공 인정 가능. 큐 보존 후 실제 Writer 실행 차단.|네이티브 History 미구현, BLOCKED|
|P0|full_migration.py:153 apply_test_runs|검증 플래그 없이 POST 및 결과 전송, GET 검증 없음. 적용 모드 차단하고 이유 저장.|수명주기 쓰기 미구현, BLOCKED|
|P0|field_config.py:64|타입 상수·옵션 순번·User 필터를 실계약처럼 사용. 미리보기 유지, 실제 쓰기 차단.|생성 계약 미검증|
|P1|writer.py:111|POST 직후 장애로 ID 저장 전 재실행. 전송 전 pending 저널, 응답 ID 기록 후 GET payload/Tracker 검증 추가.|중복 재전송 차단, 자동 원격 reconcile 미구현|
|P1|writer.py:171|Reference 여러 대상 PUT이 기존 연결 대체. 완전한 조회 어댑터 요구, 기존 ID 병합, 단일/다중 검사, GET 대사.|Mock 검증 완료|
|P1|full_migration.py:231|같은 두 Issue의 다른 링크를 합치며 방향 손실. Jira Link ID별 정규화·원본 타입 보존 및 충돌 오류.|Mock 검증 완료|
|P1|full_migration.py:304|Association POST 후 읽기 검증 없이 checkpoint. 실제 적용 차단, 원본과 차단 사유 보존.|원격 lookup/readback 미구현|
|P1|full_migration.py:28|알 수 없는 verdict를 None 처리. 비어 있지 않은 미지원 상태는 오류.|수정|
|P1|app.py:128,140|같은 Frame에 pack/grid 혼용하여 UI 구성 실패 가능. 해당 Frame을 grid로 통일.|정적 수정, 화면 미검증|
|P1|app.py:167,186|worker에서 Tk 변수 읽기/after 호출. 메인 스레드 큐로 UI 작업 전달, 병렬 작업 중복 실행 차단.|정적 수정, 화면 미검증|
|P1|writer.py:96|체크한 mapped_fields가 본문 template에 없으면 값이 누락. 본문에 포함되지 않으면 차단.|수정|
|P2|migrator.py:68,85|total 이전 빈 페이지를 완료 처리. 불완전 페이지/누락 total 오류 처리.|Mock 검증 완료|
|P2|full_migration.py:48|Xray 미지원 shape를 빈 결과로 처리. 결과 배열 및 전체 페이지 여부 검사.|Mock 검증 완료|

## 케피코 비교

이전 첨부 Java 분석에서 확인한 Test Run newTextConfigurationPlan/nextCustomFieldId/
assertExistingFieldsUnchanged는 Text 전용 템플릿 복사, ID 지정, 기존 필드 서명 보존 방식입니다.
이 패킷의 FIELD_TYPE_IDS, Choice ID i+1, User referenceFilters를 그것으로 정당화할 수 없습니다.
실행 결과의 최신 한 건 선택은 케피코 세션 관점과 유사하지만 전체 Xray 반복 이력 보존을
충족하지 않습니다. 원본 runs는 저장하되 실제 Run 이관 완료는 선언하지 않습니다.
RV&S 플러그인의 내부 DTO/상위 클래스 의존 코드를 독립 Native History Writer로 간주하지 않습니다.

## 테스트

python -m unittest discover -v: 31개 통과, 실패 0. test-results.txt 포함.
python -m compileall -q .: 통과.
Mock 기반 4종 Plan/Case/Set/Execution 계획, Item POST/GET/재실행,
Reference 다중 병합/이미 존재하는 관계 재실행, Link 방향/타입 보존,
알 수 없는 결과, 부분 페이지, 응답 유실 및 미지원 쓰기 차단을 검증했습니다.
GUI 테스트는 import smoke입니다. 실제 Tk 버튼 조작이나 고객 서버 E2E가 아닙니다.
Test Run/History/Association 생성의 성공 E2E는 없으며 차단 동작만 검증했습니다.

## 남은 제약

- Native History Writer 바이너리/의존성과 원본 revision 독립 재조회 계약 부재.
- CB 3.0.0.7 Run 부모/자식 생성, 반복 이력, 실행자/시간/step/첨부 계약 부재.
- Association 실제 타입 사용자 매핑·lookup·POST·방향 readback 어댑터 필요.
- Reference read_reference는 정규화된 {fieldId,multiple,values:[정수 ID]} 전체 응답 필요.
  실제 서버 shape 변환 및 소유/대상 Tracker 검증은 추가 구현 대상입니다.
- Item 불확실 POST는 자동 복구하지 않고 중단합니다. 원격 ID 확인 후 수동 상태 복구 필요.
  동시 프로세스 간 잠금/서버별 상태 namespace 바인딩은 미구현입니다.
- verified_against_3_0_0_7=true는 실제 API 검증 증거를 대신하지 않습니다.
- 최신 Run 정렬은 아직 문자열 기준이고 전체 이력 복원은 지원하지 않습니다.
- 첨부/Comment의 실제 전체 이관과 Legacy 원본을 CB Item에 연결하는 기능은 미완성입니다.
- 필드 미리보기는 검증되지 않은 제안입니다. 실제 쓰기로 우회하지 마세요.
- 2.2.1.4, 3.3.0.0 스키마를 고객 3.0.0.7 계약으로 전용하지 않았습니다.

## 읽기 전용/오프라인 사용

기존 export가 있으면:

    python run_pipeline.py items --config config.json --output output
    python run_pipeline.py relations --config config.json --output output
    python full_migration.py test-runs --config config.json --output output
    python full_migration.py history-java --config config.json --output output

apply 옵션을 생략하면 대상 서버 쓰기를 하지 않습니다. 원본 추출은 Jira GET이 필요합니다.
설정의 Tracker ID를 실제 승인된 범위로 채워야 합니다. 결과 JSON/백업은 고객 정보이므로
비공개로 보관하세요. 이번 ZIP에는 첨부 JAR/고객 인증정보/테스트 서버 Configuration을 넣지 않았습니다.
