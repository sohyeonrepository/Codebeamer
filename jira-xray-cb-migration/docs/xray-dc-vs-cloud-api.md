# Xray Cloud docs vs actual Jira Data Center Xray import

Customer source: **Jira Data Center + Xray Server/DC**, not Jira Cloud.
User reference: https://docs.getxray.app/space/XRAYCLOUD/44574479/API
This Xray **Cloud** API reference is useful for the data model but **NOT a compatible endpoint/authentication contract** for the customer's deployment.

## Official Server/Data Center REST evidence

- Tests: https://getxraydocs.atlassian.net/wiki/spaces/XRAY700/pages/49677822/Tests+-+REST
- Test Sets: https://getxraydocs.atlassian.net/wiki/spaces/XRAY700/pages/49677825/Test+Sets+-+REST
- Test Executions: https://getxraydocs.atlassian.net/wiki/spaces/XRAY740/pages/48432407/Test+Executions+-+REST
- Test Plans: https://getxraydocs.atlassian.net/wiki/spaces/XRAY760/pages/48760686
- Test Runs: https://getxraydocs.atlassian.net/wiki/spaces/XRAY700/pages/49677829/Test+Runs+-+REST

Do not assume documentation version equals the version installed at the customer's site. Check the actual Xray plugin version, available paths, user permissions and response schemas.

| Data | Server/DC GET example | Important |
| --- | --- | --- |
| Test standard Jira fields | `/rest/api/2/issue/{key}` | Test is a Jira issue, Xray test steps and execution metadata are separate |
| Test steps | `/rest/raven/1.0/api/test/{key}/step` | Steps, expected results, attachments need dedicated read/verification |
| Test Set memberships | `/rest/raven/1.0/api/testset/{key}/test` | Test Set is grouping; keep parent-child links |
| Test Execution members | `/rest/raven/1.0/api/testexec/{key}/test` | Can be paginated by version |
| Test Plan memberships | `/rest/raven/1.0/api/testplan/{key}/test` | Keep Plan → Tests |
| Test Plan executions | `/rest/raven/1.0/api/testplan/{key}/testexecution` | Keep Plan → Executions |
| Test Run details | `/rest/raven/1.0/api/testrun/{id}` | Run is not a Jira issue; preserve ID, testKey, testExecKey, status, dates |
| Test Run steps | `/rest/raven/1.0/api/testrun/{id}/step` | Might be absent for iteration-based runs |
| Test Run evidence/comment | `/rest/raven/1.0/api/testrun/{id}/attachment`, `/comment` | Separate from Jira issue attachment/comment |
| Reverse lookup | `/rest/raven/1.0/api/test/{key}/testsets`, `/testexecutions`, `/testruns` | Cross-check membership counts |

Cloud uses separate authentication and API/GraphQL; its `getTests` / `getTestRuns` GraphQL operations and `/api/v2` routes must **not** be sent to Jira DC by default. 2026 Cloud added a new Test Run activity GraphQL query; **do not assume this works on Server/DC**.

## Code added

`clients/xray_dc_contract.py` is opt-in read-only GET inspection for documented Server/DC operations. It archives original JSON and marks completeness as `false`, because a single response cannot establish all pages. It never performs a write. Unit tests mock HTTP and check exact routes and fail-closed handling. This does **not** change the existing 5-step Wizard or source-run exporter.

## Next implementation stage (after customer API evidence)

1. Read installed Xray DC version and permission scope.
2. Collect one verified sample for Test, Test Set, Test Plan, Test Execution and a Test Run (including steps, status, evidence, defects, dates and user).
3. Identify pagination response envelope for each endpoint, implement bounded pagination with total/unique ID validation, archive full source data.
4. Cross-check Test Execution membership with each Run's `testExecKey` and `testKey`.
5. Map Run to CB Test Run, Run steps to CB supported step/result model; preserve original activity/history in Legacy evidence when server write API is unavailable.
6. Test using **one source key** and a disposable CB project/Tracker only after approving authenticated writes; verify GET readback and source/target count/hash evidence.
7. Add R4J analysis separately; never confuse R4J revisions with Xray Test Run activity.

## Codex verification commands

```powershell
py -3 -m unittest tests.test_xray_dc_contract -v
py -3 -m unittest discover -s tests -v
```

All tests must pass before selectively porting these source-read improvements to the stable Wizard branch. GUI tests may skip in headless environments. No existing state or config should be overwritten.
