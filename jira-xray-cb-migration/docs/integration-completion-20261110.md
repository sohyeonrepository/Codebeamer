# Offline integration validation — 2026-11-10

Branch: `test/cb-item-post-minimal-20261106`

## Result

`python -m unittest discover -s tests -q`: **493 run, 487 passed, 6 skipped, 0 failures, 0 errors**.

The six skipped tests require a GUI display. Direct Tk initialization returned `TclError: no display name and no $DISPLAY environment variable`; Xvfb, sudo and the scoped system-package installer are absent in this environment. No Windows GUI test or actual Codebeamer/Jira/Xray server test is asserted.

## Runtime corrections

- Native Resolution mapping accepts both the observed singular field label and plural REST property, while continuing to require writable POST/PUT schemas and ChoiceOptionReference compatibility. Field IDs are not guessed.
- Unmapped relations display blank, rather than falsely implying that a user selected exclusion.
- Restored the reusable `load_mapping(kind)` function and made existing Case/Set preview use it, retaining supported-kind and Issue Type checks.
- Earlier lifecycle changes retain Case → Set → Run → Plan creation order, explicit Release/backlog/reference contract preflight, source-only Execution, and the existing preservation/relationship engine.

## Test repairs and preserved checks

Existing tests were updated to exercise the actual current interfaces rather than older default auto-selection semantics:

- Choose checkboxes and mappings explicitly; configured null fields remain visible but are not automatically selected.
- Date creation follows a saved creation intent and resolves its profile before execution; it does not require a popup or speculative type ID.
- Item mocks supply project/Issue Type metadata, field list responses and exact Jira Key GET/readback values.
- Restart tests reuse the current configuration; all explicit identity bindings remain available.
- Reference catalog tests keep the normal Item GET routes working instead of returning an Association list for every URL.
- Components tests assert source ID mappings and the supported display-name aliases.
- Standard Summary proposals remain available even when another unchecked source field could target Name. Existing selection-duplicate tests still reject conflicting active mappings.
- R4J missing-adapter tests explicitly opt in to preservation; omission alone no longer means R4J was requested.
- No failing test was deleted or marked skipped to obtain this result. Existing GUI skips were retained.

The suite includes full Wizard/Engine Mock flow, comments, binary attachments/evidence, Legacy History preservation, Xray steps and relationships, readback, interrupted-write handling and duplicate prevention. It also asserts that an empty source selection cannot be reported as successful migration.

## Deployment limits

The offline code/test work is complete for this pass. Live 3.0.0.7 field permission contracts, Reference write contracts, Release/backlog representation and the customer's installed Xray/DC response shapes remain deployment inputs. Missing verified contracts still block writes; a passing Mock is not proof of API compatibility.

Legacy History is preserved by the current REST path. Native historical author/time restoration via internal Java APIs is not implemented or claimed. It needs the actual persistence service/DAO and supported server-extension deployment access.

Existing RUN_WINDOWS.bat, config.local.json, .migration data and customer attachments were not changed or committed. No server writes or PR merge were performed.
