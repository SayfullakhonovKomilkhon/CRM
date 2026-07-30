# Google Apps Script bridge

Copy `Code.gs` into each source spreadsheet's Apps Script project, set the six
script properties documented at the top of the file, then run `installCrmSync`
once as the spreadsheet owner.

`CRM_SOURCE_TAB` is mandatory. Edits on every other tab are ignored. Paste,
clear, and multi-row/multi-column edits produce one signed event per affected
row containing every mapped column in that row.

The `crm_row_id` column is protected identity, not a workflow field. Do not map
it in either inbound or writeback maps. Google API writes do not invoke
installable `onEdit` triggers; the origin marker is an additional suppression
guard for any future Apps Script write helper.

Rows are sent to CRM only when the header row contains exactly one
`Отправка на согласование` column and that row contains `Отправить`. The marker
is cleared after a successful webhook response, so later draft edits stay in
Google Sheets until the scenarist explicitly submits the row again. When header
lookup is not suitable, `CRM_SUBMISSION_COLUMN` may contain the column letters
(for example, `AC`).
