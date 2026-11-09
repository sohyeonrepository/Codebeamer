# Test Case / Set / Run / Release migration

This extends the existing compact Wizard and Engine. It does not install a second launcher. Use the existing RUN_WINDOWS.bat.

## Implemented path

The compact Wizard can register Test Execution as source context without a target Tracker. The new full-test-import action calls the existing ImportWizard.execute, instead of the limited single-Item writer. It requires explicit approval and the existing visibility review. Existing field creation, preservation, state, deduplication, Reference and Association implementations are reused.

Full Xray ordering is Case (including steps), Set, individual Run, then Plan. Relations are written after IDs are available. Execution stays source-only; aggregate PASS/FAIL counts cannot create Runs. Existing engine children processing preserves attachments, inline images, comments, Legacy History and configured R4J data. They are not replaced by plain Item POSTs.

## Required server-specific configuration

The new button deliberately blocks until the existing full-Xray adapters are configured. It does not turn unverified routes into success:

- `migration.mode = full-xray`; clear any saved `jira.single_issue_key` restriction for full scope.
- `migration.full_xray.issue_types`: actual Jira IDs or names for test/set/plan/execution.
- Map and analyze all four source kinds; Execution must be source-only.
- Existing `source_reads`, `run_adapter`, test/run-step and evidence adapters must describe the installed Xray/DC and CB contracts.
- `full_xray.release_type_contract.path` and `.value`: the actual Configuration JSON path/value proving that the selected Plan Tracker is Release. No numeric Tracker Type ID is invented.
- `full_xray.relationships`: explicit Set→Test and Plan→Test source membership paths. Jira Issue Links are not a substitute for Xray membership APIs.
- `full_xray.backlog_relation_name`: the name of the Plan→Test adapter wired to the verified CB backlog relation. The generic relation adapter must use a real writable/readback contract; semantic compatibility still needs live validation.
- `full_xray.typed_link_fields.clone` and `.tests`: actual Jira Link Type IDs. The existing `wizard.association_types` entries for those IDs must be `mode=reference`, with actual source Tracker / target Tracker / Field IDs and direction. No arbitrary type creation or conversion to generic Association.
- Reference field metadata must prove cardinality, visibility and allowed target Tracker. Existing reference writer appends without discarding previous values and verifies GET results.

For example, a `release_type_contract` path could be `basicInformation.type.name` only if the actual server returns that property. This is not a recommended universal path. The code validates the configured value against the selected Tracker.

## History / internal Java API

Current executable preservation remains Legacy History, separate from Native History. User-supplied RV&S source notes identify TrackerItemRevisionDto / TrackerItemRevisionConfiguration and related history processing. Those construction calls do not prove persistence. A server-side extension requires tracing the final manager/DAO persistence call, transaction boundaries, authorization, revision ordering, original author/time handling and readback on 3.0.0.7. No guessed REST History-create endpoint or server JAR deployment was added. Native-history restoration remains unimplemented/unverified pending source and deployment evidence.

## Validation and limits

38 focused Mock tests passed: full-Xray dependency/creation ordering, source-only Execution, read-only Release/reference preflight, relation scope and unfinished-field Item POST guards. Existing full-Xray fixtures verify Case→Set→Run→Plan sequencing with mocked native Run creation. These are not Windows GUI or actual server tests.

The pre-existing Wizard→Engine integration fixture still has identity-handoff/default-selection failures on this branch. Full end-to-end customer operation is NOT declared complete. No live server data was changed, no PR merged, and no customer attachments/configuration were committed.
