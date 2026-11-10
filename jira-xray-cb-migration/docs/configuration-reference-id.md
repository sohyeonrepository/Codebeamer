# 신규 Configuration referenceId 처리

## 최신 변경: 참고 소스 기반 Choice 자동 할당

추가 제공된 `jira_codebeamer_migration_mapping.zip`의
`jira_to_codebeamer/mapper.py: IdAllocator.take()` 및 `codebeamer.py: custom_field(),
choice_options()`를 분석했습니다. 이 소스는 Codebeamer 3.0.0.7 대상으로 명시되어
있으며 Choice 계열 신규 후보를 1010부터 선택합니다(선언된 범위 1000~9999).

현재 Tracker의 **동일 typeId**에서 native `CHOICE_OPTIONS`와 1000번대 커스텀 필드를
확인한 경우, 이제 1010~9999 중 첫 미사용 referenceId를 자동 선택합니다.
모든 타입의 기존 ID를 충돌 검사에 포함하고, 소스에 빠져 있던 상한 초과 차단을 추가했습니다.
기존 설정의 ID 최댓값에 1을 더하지 않습니다. Tracker의 구조가 근거와 다르면 자동 적용하지 않습니다.

따라서 확인된 Labels Choice 경로에서는 숫자를 직접 입력할 필요가 없습니다.
기존 명시적 예약 설정은 우선 적용하며, 다른 타입의 확인된 정책을 설정할 수 있는 기능도 유지합니다.
서버가 명시한 referenceId enum도 자동 후보의 근거로 사용할 수 있습니다.

첨부 요청의 기존 필드 136개를 대상으로 오프라인 계산한 후보는 1010입니다.
실행 시 최신 GET으로 다시 계산하고, 승인 직전 GET에서 변경되면 POST를 차단합니다.
이는 실제 고객 서버에서 생성 성공을 확인했다는 뜻은 아닙니다.

## 이전 변경 및 명시적 설정

실제 고객 서버 응답은 HTTP 500이며 `TrackerFieldModel.referenceId()`가 null이라고
명시했습니다. 해당 요청의 기존 136개 필드에는 값이 있고 신규 필드에만 빠져 있었습니다.
결과 GET에서 신규 필드 0개, 기존 Configuration 변경 0개로 보고되었습니다.

앞선 수정은 서버 자동 할당 가정을 제거하고, 명시적 예약값을 Configuration POST에
넣도록 합니다. ID 할당 범위는 OpenAPI에 명시되지 않았으므로 임의의 번호대, max+1,
Java 버전이 다른 구현의 범위를 적용하지 않습니다. 서버의 실제 신규 필드 ID 정책은
아직 확인이 필요합니다. 미사용 검사만으로 서버가 허용하는 번호라고 보장할 수 없습니다.

## Wizard 설정

`config.local.json`의 `codebeamer` 아래에 `configuration_reference_ids`를 추가합니다.
구조는 **Tracker ID 문자열 → 정확한 신규 필드명 → 정수 예약값**입니다.
예시 구조의 숫자는 설명용이며 실제 서버에 그대로 사용하지 마세요.

```json
{
  "codebeamer": {
    "configuration_reference_ids": {
      "TEST_TRACKER_ID": {
        "EXACT_NEW_FIELD_NAME": 12345
      }
    }
  }
}
```

실제 서버 정책을 확인한 관리자가 예약값을 정해야 합니다. 다른 설정은 보존합니다.
생성 계획은 ID 미지정 상태에서도 검토할 수 있습니다(현재 서버의 선택 속성 스키마).
실제 POST에서는 referenceId가 쓰기 가능한 속성인데 값이 없으면 중단합니다.
기존 생성 템플릿의 ID를 복사하지 않습니다.

## 단일 POST 테스트

`TEST_FIELD_POST_WINDOWS.bat`의 필수 referenceId 입력란은 제거했습니다.
같은 필드에 설정된 예약값이 있으면 우선 사용하고, 위 조건이 맞으면 자동 할당합니다.
CLI의 `--reference-id`로도 명시적으로 지정할 수 있습니다. 자동 정책도 예약값도
확인할 수 없는 타입은 POST 전에 중단됩니다.
실행 전 미리보기와 `request.json`에서 referenceId를 확인합니다.
실패한 이전 시도의 `attempt.json`은 지우지 마세요. 자동 재전송하지 않습니다.

## 검증

- 실제 POST 스키마에 referenceId 선언 및 readOnly 여부 확인
- int32 정수, 서버가 선언한 enum/minimum/maximum 검사
- 선택한 Tracker의 referenceId/id/fieldId와 중복 검사
- 승인 후 Configuration 재조회로 번호 선점 또는 설정 변경 차단
- 기존 필드·권한 보존, 생성 후 실제 referenceId 비교
- 미지정이면 POST나 pending 기록을 만들지 않고 오류 표시

테스트는 Mock 서버 기준입니다. 실제 서버 referenceId 할당 정책과 수정된 POST의
성공 여부는 아직 미검증입니다. 새로운 API나 서버 쓰기는 실행하지 않았습니다.

자동 회귀 결과: 525개 실행, 519개 통과, 기존 GUI 6개 제외, 실패/오류 0개.
