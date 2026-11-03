# Field Definition contract, first implementation

Branch: feat/field-definition-contract, based on a327a72.
No customer server writes performed. Uploaded JARs are not included.

`clients.field_definition.plan_definition()` resolves the supplied server's
Configuration POST request schema. It never copies an existing ID. If
referenceId is optional it leaves the ID absent. If required, it requires a
writable integer schema with an explicit enum or bounded positive range.
It selects an unused value against the complete current configuration.
No 10000..19999 constant or RV&S-specific allocation policy is used.
A server schema without allocation bounds is insufficient and blocks this path.
Deployment owners must confirm these constraints represent the creation contract,
not merely a generic integer range shared by built-in and custom fields.

Request envelope (symbolic, not executable JSON):

    POST /v3/tracker/configuration
    {
      ...unchanged current Configuration properties...,
      "fields": [
        ...unchanged existing field definitions...,
        {
          ...verified type-specific definition and permission...,
          "label": "user-reviewed name",
          "referenceId": "only present when required and allocated by server schema"
        }
      ]
    }

`configuration_field_contract()` uses the same planner for read-only validation.
`create_confirmed_field()` repeats planning against unchanged current configuration,
then journals the actual POST body before writing. It rereads the actual field,
checks the allocated referenceId, and checks all prior field and Tracker settings.
Missing readback attributes are no longer filled from the proposed definition.
Existing name or uncertain journal blocks duplicate creation; the existing Wizard
reconciliation path handles retry evidence. No automatic deletion/rollback occurs.

FieldValue bindings remain independent: planning does not inspect FieldValue
models. Existing Wizard CREATED / VALUE_MAPPING_PENDING / COMPLETED phase storage
is preserved. Definition success is not Item value success. Existing Text Item
payload/readback tests remain in the regression suite; new tests cover required-ID
allocation, optional ID, unsupported schema, exhaustion, permissions, approval,
POST/readback and duplicate blocking. Mock results are not live server evidence.

Remaining work: verify the real 3.0.0.7 creation schema; explicit allocation
policies for deployments whose schema does not describe usable custom-field IDs;
Choice option IDs and cardinality; User identity resolution; timezone-aware Date
conversion; Reference contracts. Existing checkbox review, relation mapping,
Xray and R4J paths are retained. Real Text Item round-trip requires separate user
approval and an accessible approved test Tracker/Issue.
