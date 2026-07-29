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
