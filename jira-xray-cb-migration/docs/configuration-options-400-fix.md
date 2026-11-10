# Configuration POST: unknown `options` property

The actual customer POST response identified `Unrecognized field "options"` at
`fields[136].options`, the newly appended field. HTTP status was 400; the follow-up
GET found no new field and no changes to existing Configuration. The earlier
permission diagnosis was incorrect: `permission` occurred in the server's list
of *accepted* properties, not as the rejected property.

The failure journal's existing fields demonstrate the native Choice structure:

```json
{
  "choiceOptionSetting": {
    "type": "CHOICE_OPTIONS",
    "choiceOptions": [{"name": "Example"}]
  }
}
```

The old internal plan used top-level `options` and `reference`. These are now
serialized separately from the Configuration wire model. The adapter applies
only to the new definition, preserving every existing field and permission.

- Use the server POST field schema, not fixed numeric type IDs.
- Convert nonempty internal options only when a same-type CHOICE_OPTIONS
  definition is present on the selected Tracker and the server discriminator
  schema declares choiceOptions. Validate each option against that schema.
- Remove null/empty internal options and null reference when those properties
  are absent from the wire schema. Never discard populated Reference data.
- Block unknown new-field properties, conflicting definitions, and unverified
  conversion contracts before POST.
- Read back native Choice options, including assigned IDs, for value mapping.
- Use the same serializer in Wizard preflight, actual creation, and the single
  POST test tool. Keep old pending receipts; do not automatically repost.
- Classify unknown-property errors before scanning for generic permission text.

Regression results: 518 tests run, 512 passed, 6 existing GUI skips; no failures.
Tests cover native serialization, unchanged existing fields/permissions, preserved
multi-selection and option names, assigned IDs, rejected unsupported contracts,
actual creation journal completion with native readback, and the observed error
classification. The old native-envelope fixture now declares its request schema.

Actual corrected customer-server POST has **not** yet been executed. To run one
approved validation, use the updated `TEST_FIELD_POST_WINDOWS.bat` with the same
original journal and a distinct test name such as `Migration_API_Test_Labels_v2`.
This is a new explicit test after a code correction, not an automatic retry.
Inspect `request.json` before approval: new-field `options`/`reference` must not
appear when absent from the server schema, and all requested names must remain
under `choiceOptionSetting.choiceOptions`. Keep all earlier attempt records.

Send the new `result.json`, `http-status.json`, and reviewed error response if the
server rejects the corrected request. No customer source artifacts are committed.
