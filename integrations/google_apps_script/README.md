# Google Apps Script bridge

Copy `Code.gs` into each source spreadsheet's Apps Script project, set the six
script properties documented at the top of the file, then run `installCrmSync`
once as the spreadsheet owner.

`CRM_SOURCE_TAB` is mandatory and remains the legacy/main tab. Project tabs
created by CRM are also accepted when their header row contains both `ID` and
`Отправка на согласование`; unrelated instruction and service tabs remain
ignored. One Apps Script installation and webhook secret therefore serve the
main tab plus all registered project tabs in the same spreadsheet. Paste,
clear, and multi-row/multi-column edits produce one signed event per affected
row containing every mapped column in that row.

The `crm_row_id` column is protected identity, not a workflow field. Do not map
it in either inbound or writeback maps. Google API writes do not invoke
installable `onEdit` triggers; the origin marker is an additional suppression
guard for any future Apps Script write helper.

Rows are sent to CRM only when the header row contains exactly one
`Отправка на согласование` column and that row contains `Отправить`. The marker
is cleared after a successful webhook response. This rule applies to both new
and already linked rows: Google edits remain drafts in the Sheet until the
scenarist explicitly selects `Отправить` again. When header lookup is not
suitable, `CRM_SUBMISSION_COLUMN` may contain the column letters (for example,
`AC`).

The script sends only the fields in `CRM_SCENARIST_INBOUND_FIELDS`. Approval,
assignment, editor, client, manager, and publisher fields are CRM-owned:
changes to those Sheet cells never travel to CRM. CRM may still write all
configured role fields to Google through `writeback_column_map`.

The visible Google column `ID` is detected automatically and sent as
`external_id`. It is separate from the protected UUID column. A submitted new
row must have a visible ID; CRM stores exactly that value and advances its
numeric ID sequence so later CRM-created rows do not collide.
