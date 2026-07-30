# Google Sheets → CRM import

The adapter is one-way:

`Google Sheets → backend preview/validation → PostgreSQL → CRM`

Google credentials exist only in backend environment variables. The frontend never receives
the private key, access token, spreadsheet ID, raw mapping, or source checksums. Every endpoint
requires an authenticated manager.

## Required input

Before enabling a real source, provide:

1. Spreadsheet ID from the Google Sheets URL.
2. Exact tab name and 1-based header row.
3. Existing active CRM `project_id` for each tab.
4. Optional existing active `scenarist` user ID for each tab.
5. Explicit column overrides for ambiguous or customized headers.
6. A Google Cloud service-account JSON key stored only as a Railway secret.
7. Viewer access to the spreadsheet for the service-account `client_email`.

The adapter does not create clients, projects, or users from a sheet.

## Railway variables

Keep import disabled until preview configuration is verified:

```env
GOOGLE_SHEETS_ENABLED=false
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
GOOGLE_SHEETS_SPREADSHEET_ID=1AbCdEf...
GOOGLE_SHEETS_TAB_CONFIGS=[{"tab":"Сценарист","header_row":2,"project_id":"00000000-0000-0000-0000-000000000000","assigned_scenarist_id":null,"columns":{"scenario_date":"дата","content.script_text":"Сценарий"}}]
GOOGLE_SHEETS_PREVIEW_TTL_MINUTES=30
GOOGLE_SHEETS_MAX_ROWS=1000
```

`GOOGLE_SERVICE_ACCOUNT_JSON` must be the complete one-line JSON secret. Never add it to
`.env.example`, Git, build arguments, Vercel, or browser storage. The service account uses only
the `spreadsheets.readonly` OAuth scope.

`columns` accepts either an exact header string or a 1-based column index. Only fields in the
status endpoint's `safe_import_fields` can be configured. One source column cannot map to more
than one CRM field.

## API

### Status

`GET /api/v1/google-sheets/status`

Returns feature readiness, safe tab summaries, missing variable names, the safe field allowlist,
and the last actual sync run. It never returns credentials or column data.

### Preview

`POST /api/v1/google-sheets/preview`

```json
{"tab":"Сценарист"}
```

The response includes `preview_id`, expiration, immutable snapshot checksum, warnings, and rows:

```json
{
  "summary":{"total_rows":3,"created":1,"updated":1,"skipped":1,"errors":0},
  "rows":[
    {"row_number":3,"action":"created","errors":[]}
  ]
}
```

Preview writes only an audit record, never scenario data. Any invalid row produces
`validation_failed`; that preview cannot be synchronized.

### Confirmed sync

`POST /api/v1/google-sheets/sync`

```json
{"tab":"Сценарист","preview_id":"<uuid>","confirm":true}
```

Sync succeeds only if:

- the preview is successful and unexpired;
- spreadsheet ID, tab, target project, and snapshot are unchanged;
- the corresponding CRM rows still match the preview plan;
- all rows validate;
- target project/client and optional scenarist are active and have the expected role.

The entire upsert and sync log commit in one database transaction. PostgreSQL advisory locking
serializes concurrent syncs for the same spreadsheet/tab. A changed source or CRM state returns
`409`; the manager must run preview again.

## Idempotency and overwrite policy

- A new row is eligible only when `Отправка на согласование` contains
  `Отправить` (case and surrounding whitespace are ignored).
- Rows without that marker and without a valid protected `crm_row_id` are
  skipped before validation and never receive a CRM identity.
- Once a row has a valid CRM identity, scenarist-owned edits synchronize in
  either direction while the corresponding scenarist stage remains editable.
  Selecting `Отправить` submits a draft or revision to the next workflow stage.
- Identity is `(source_sheet_id, source_tab, source_row)` with a database unique constraint.
- A canonical SHA-256 checksum makes unchanged reimports `skipped`.
- New scenarios receive the normal server-generated sequential `external_id`.
- Source metadata and checksum are controlled only by the adapter.
- A changed safe source row can update an imported scenario only before workflow starts.
- The presence of any approval, montage, publication, revision gate, or non-initial status locks
  source updates for that row.
- Sheet row deletion never archives or deletes CRM data.
- Moving existing source rows changes their identity; do not reorder an active imported tab.
- Users/auth, approvals, assignments, workflow decisions/statuses, editor-owned
  montage results, manager-owned controls, publisher results, and server IDs
  are never read from the sheet.

The approved business column references remain in the four
`docs/google-sheets-*-mapping.md` files. Inbound sync accepts the scenarist's
main/research/content fields, source-material fields, and publication-preparation
fields. All other columns are managed inside CRM and are writeback-only toward
Google Sheets.
