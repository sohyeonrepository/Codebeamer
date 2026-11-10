# Supplied source analysis and integration

## Actual Java implementation

Analyzed the newly supplied Test Run JAR with a Java decompiler; did not execute
the migration application. SHA-256:
`b15814c0ea5c101b22b0598e55bed92f4b1bde7a234eb6aa71637f3dc109db0b`.

`Application.nextCustomFieldId()` collects existing referenceIds and selects the
first unused integer from 10000 inclusive to 20000 exclusive. Its caller,
`newTextConfigurationPlan()`, is reached after validating TextFieldValue on the
template. It is **not** a general Choice allocation algorithm.

The plan deep-copies Configuration/template, assigns referenceId and position,
sets new-field presentation flags, removes stale formulas/dependencies/choice
settings, and appends the new field. Post-write checks compare the actual ID and
old field signatures. The Python implementation retains its stricter complete
Configuration comparison; it does not replace old permissions with Unrestricted.

## Additional Jira mapping reference

The later `jira_codebeamer_migration_mapping.zip` provides Python source targeting
Jira DC and Codebeamer 3.0.0.7. Relevant implementations:

- `mapper.IdAllocator.take()`: Choice/user/group/reference family domain 1000..9999,
  starting new candidates at 1010; other family domain 10000..99999, starting 10010.
- `codebeamer.custom_field()`: explicit referenceId, native choiceOptionSetting,
  copied permission, position, hidden/listable and multiplicity.
- `codebeamer.choice_options()`: new field-local option IDs beginning at 1, distinct
  from Jira option IDs. Choice options go under CHOICE_OPTIONS/choiceOptions.

Integration is limited to the current Choice creation defect. Native same-type
Choice metadata and existing custom IDs on the selected Tracker must corroborate
the source policy. No fixed typeId is assumed. New option IDs are supplied only
when the server schema declares a writable integer ID. Scalar/User/Reference
generation is not newly declared server-verified by this change.

The reference allocator did not enforce the upper bound during its increment
loop. The new implementation stops on exhaustion. It also rejects unknown
policies and conflicting field families, checks all ID aliases and rechecks the
Configuration before POST. It preserves the existing no-retry journal.

We did not import the reference's whole-program configuration rewriting, hiding
of fields, permission replacement, credentials, hardcoded project/Tracker IDs,
write retries, or deletion methods. Neither ZIP, original code, decompiled Java,
nor customer configuration is committed.

## Verification limits

Offline application of the rule to the supplied failed request selects 1010
without changing the 136 existing fields. Actual server writes were not executed.
Tests exercise automatic allocation through POST body construction, journal and
readback with native option IDs; collisions, exhausted ranges, missing evidence,
Tracker/type scoping, existing permissions and multiplicity preservation.

Full regression result: 537 tests run, 531 passed, 6 existing GUI skips, no
failures/errors. Customer-server POST and Windows execution remain unverified.
