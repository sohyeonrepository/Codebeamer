# Field creation diagnostic findings — 2026-11-10

The customer supplied the output of the GET-only diagnostic tool. No customer
artifact, configuration, original request, or credentials is committed here.

## Observed on the customer server

- Configuration GET, field-list GET and Swagger retrieval succeeded.
- The requested Labels field was absent.
- Existing field permissions matched those in the failed request.
- The local schema validator reported no errors for the old request, including
  the PER_STATUS permission subtype.
- The original failure receipt is pending and contains no POST response body.

These observations do **not** identify the cause of HTTP 400. Swagger validation
does not establish that a user can update the Tracker, that status/role references
are valid, or that server-specific business rules accept this new field. The
reported Swagger info.version `3.0` is not proof of the product patch version.
The diagnostic did not write anything or verify field creation.

## Confirmed application defects fixed

1. HTTP errors discarded all detailed validation signals. ApiError now retains
   bounded metadata: HTTP status, response digest, truncation indicator, and
   allowlisted property/constraint signals. Arbitrary server text, credentials,
   rejected values and echoed requests are not exported or written to audit logs.
2. Stage wrappers discarded structured evidence. They now preserve it.
3. Both Configuration and dedicated field creation record available evidence in
   the existing failure journal. Dedicated creation previously omitted the error.
4. Pending-field guidance incorrectly classified any mention of permission as a
   validation error. It now distinguishes an unconfirmed cause.
5. Diagnostic reports distinguish unknown field presence (failed GET) from
   confirmed absence, and do not equate schema acceptance with server success.

## Existing pending request

The fix cannot reconstruct an already discarded response. It does not clear the
pending receipt, change existing permissions, or automatically retry the POST.
Do not rerun the same GET diagnostic expecting it to recover the lost response.
To determine this particular HTTP 400, the server-side error for the original
request is needed; another write would require separate approval and prior
reconciliation. This change does not authorize or perform that write.

## Verification

Regression tests cover nested validation errors, omission of secrets, truncation,
HTTP → stage wrapper → journal propagation, Configuration and dedicated creation,
pending error wording, one POST only, and restoration of write guards.
These are automated offline tests, not real Codebeamer creation tests.

Result: `python -m unittest discover -s tests -q` ran 503 tests: 497 passed,
6 existing GUI tests skipped, no failures/errors. The final allowlisted signal
display change also passed all 7 targeted HTTP evidence tests. Windows GUI and
customer-server POST were not executed.
