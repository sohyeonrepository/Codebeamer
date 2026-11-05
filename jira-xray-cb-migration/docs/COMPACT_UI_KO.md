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
