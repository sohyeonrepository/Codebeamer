# 간소화된 Jira → Codebeamer Wizard

사용자 승인 화면 설계(4페이지)를 반영합니다.

1. **서버 연결**: `import_wizard.py`의 기존 Jira URL/토큰, CB URL/계정/비밀번호 및 Swagger 연결 화면을 그대로 사용합니다. 인증 정보는 화면에 저장하지 않습니다.
2. **프로젝트 연결**: Jira 프로젝트와 기존 Codebeamer 프로젝트를 선택한 뒤 Jira Issue Type과 CB Tracker를 연결하고 저장합니다. 여러 Type을 등록할 수 있습니다.
3. **데이터 가져오기 · 추천**: 선택된 Type에 사용된 Jira 필드 및 CB 필드를 조회하고 기존 `proposals` 로직으로 동일명/호환 타입의 매핑을 제안합니다. 기본으로 선택되지 않으며 사용자가 추천 필드를 선택하거나 개별 제외할 수 있습니다.
4. **데이터 가져오기 · 수동**: 각 Jira 필드의 무시/기존 CB 필드 연결/새 필드 생성 요청을 선택합니다. 신규 필드 타입은 대상 Swagger/Tracker 설정으로 검증된 생성 프로필만 노출합니다. Choice 값은 Jira 관찰 값과 허용 값에서 가져와 제안합니다.

**주의사항**
- '새 필드 생성 요청'은 **계획 저장**입니다. 필드 생성 POST는 별도 '실제 Import 실행' 버튼에서 명시적 사용자 승인을 받은 이후에만 진행됩니다.
- Xray의 Test Set/Test Plan/Test Execution/Test Run 관계는 문자열 필드 매핑과 다릅니다. 필요한 경우 **Xray / Reference 고급 설정**을 열어 기존 Wizard의 관계 매핑 기능을 이용합니다.
- 기존 Wizard는 `migration.wizard.legacy_ui: true` 옵션으로 유지합니다.
- API `lookup` 누락 문제 및 실제 Codebeamer 3.0.0.7에 대한 POST 성공 여부는 UI 변경과 별개의 검증 과제입니다.
- 실제 서버 연결 테스트는 이 GitHub 코드 변경만으로 완료되지 않았습니다.

실행 예:
```bat
python import_wizard.py --config config.local.json --prompt-auth
```
