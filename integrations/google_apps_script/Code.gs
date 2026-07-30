/**
 * CRM bidirectional sync bridge.
 *
 * Script properties required:
 * CRM_WEBHOOK_URL  https://api.example.com/api/v1/google-sheets/webhook/<source UUID>
 * CRM_WEBHOOK_SECRET  returned once by POST /google-sheets/sources or rotate-secret
 * CRM_ROW_ID_COLUMN  protected column letters, e.g. A
 * CRM_HEADER_ROW  e.g. 2
 * CRM_SOURCE_TAB exact source tab name
 * CRM_INBOUND_COLUMN_MAP JSON object keyed by 1-based column number, values are CRM fields
 */

const CRM_SCHEMA_VERSION = 1;
const CRM_RECONCILE_HANDLER = "reconcileCrmRows";
const CRM_RECONCILE_DEFAULT_BATCH_SIZE = 100;
const CRM_REALTIME_EXCLUDED_FIELDS = new Set(["montage.material_status"]);

function installCrmSync() {
  const spreadsheet = SpreadsheetApp.getActive();
  ScriptApp.getProjectTriggers()
    .filter((trigger) => ["onCrmEdit", CRM_RECONCILE_HANDLER]
      .includes(trigger.getHandlerFunction()))
    .forEach((trigger) => ScriptApp.deleteTrigger(trigger));
  ScriptApp.newTrigger("onCrmEdit").forSpreadsheet(spreadsheet).onEdit().create();
  ScriptApp.newTrigger(CRM_RECONCILE_HANDLER).timeBased().everyMinutes(5).create();
}

function onCrmEdit(event) {
  if (!event || !event.range) return;
  const props = PropertiesService.getScriptProperties();
  const headerRow = Number(props.getProperty("CRM_HEADER_ROW") || "1");
  const sourceTab = props.getProperty("CRM_SOURCE_TAB");
  const sheet = event.range.getSheet();
  if (!sourceTab || sheet.getName() !== sourceTab) return;
  if (event.range.getLastRow() <= headerRow) return;

  // Google API/script writes do not fire installable onEdit triggers. This cache marker
  // is an additional guard for explicit CRM-origin script operations.
  const map = JSON.parse(props.getProperty("CRM_INBOUND_COLUMN_MAP") || "{}");
  const rowIdColumn = columnNumber_(props.getProperty("CRM_ROW_ID_COLUMN") || "A");
  const firstRow = Math.max(event.range.getRow(), headerRow + 1);
  const lastRow = event.range.getLastRow();
  const editedColumns = new Set(Array.from(
    {length: event.range.getNumColumns()},
    (_, offset) => String(event.range.getColumn() + offset)
  ));
  const touchesMappedColumn = Object.keys(map).some(
    (column) => editedColumns.has(column) && !CRM_REALTIME_EXCLUDED_FIELDS.has(map[column])
  );
  if (!touchesMappedColumn) return;

  const spreadsheetId = event.source.getId();
  for (let rowNumber = firstRow; rowNumber <= lastRow; rowNumber += 1) {
    const suppressionKey = `crm-origin:${sheet.getSheetId()}:${rowNumber}`;
    if (CacheService.getScriptCache().get(suppressionKey)) continue;
    syncCrmRow_(sheet, rowNumber, map, rowIdColumn, props, spreadsheetId, {
      force: true,
      a1: event.range.getA1Notation()
    });
  }
}

/**
 * Recovery for pasted rows, formulas, imports and temporary webhook failures.
 * It scans a bounded batch every five minutes and only sends changed snapshots.
 */
function reconcileCrmRows() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  try {
    const props = PropertiesService.getScriptProperties();
    const spreadsheet = SpreadsheetApp.getActive();
    const sourceTab = props.getProperty("CRM_SOURCE_TAB");
    const sheet = sourceTab ? spreadsheet.getSheetByName(sourceTab) : null;
    if (!sheet) return;
    const headerRow = Number(props.getProperty("CRM_HEADER_ROW") || "1");
    const lastRow = sheet.getLastRow();
    if (lastRow <= headerRow) return;
    const map = JSON.parse(props.getProperty("CRM_INBOUND_COLUMN_MAP") || "{}");
    const rowIdColumn = columnNumber_(props.getProperty("CRM_ROW_ID_COLUMN") || "A");
    const configuredBatch = Number(
      props.getProperty("CRM_RECONCILE_BATCH_SIZE") || CRM_RECONCILE_DEFAULT_BATCH_SIZE
    );
    const batchSize = Math.max(1, Math.min(500, configuredBatch || 0));
    let cursor = Number(props.getProperty("CRM_RECONCILE_CURSOR") || headerRow + 1);
    if (cursor <= headerRow || cursor > lastRow) cursor = headerRow + 1;
    const endRow = Math.min(lastRow, cursor + batchSize - 1);
    const claimedRowIds = {};

    for (let rowNumber = cursor; rowNumber <= endRow; rowNumber += 1) {
      syncCrmRow_(
        sheet,
        rowNumber,
        map,
        rowIdColumn,
        props,
        spreadsheet.getId(),
        {force: false, a1: `${rowNumber}:${rowNumber}`, claimedRowIds}
      );
    }
    props.setProperty(
      "CRM_RECONCILE_CURSOR",
      String(endRow >= lastRow ? headerRow + 1 : endRow + 1)
    );
  } finally {
    lock.releaseLock();
  }
}

function syncCrmRow_(sheet, rowNumber, map, rowIdColumn, props, spreadsheetId, options) {
  const fields = fullRowFields_(sheet, rowNumber, map);
  const rowIdCell = sheet.getRange(rowNumber, rowIdColumn);
  const rawRowId = rowIdCell.getDisplayValue().trim();
  const hasExistingIdentity = isUuid_(rawRowId);
  if (!hasExistingIdentity && !hasMeaningfulFields_(fields)) return false;

  let rowId = rawRowId;
  const claimed = options.claimedRowIds || null;
  if (!hasExistingIdentity || (claimed && claimed[rowId])) {
    rowId = Utilities.getUuid();
    // The service column may inherit checkbox validation from its neighbour.
    rowIdCell.clearDataValidations();
    rowIdCell.setValue(rowId);
  }
  if (claimed) claimed[rowId] = true;

  const checksum = sha256Hex_(stableJson_(fields));
  const checksumKey = syncChecksumKey_(sheet.getSheetId(), rowId);
  if (!options.force && props.getProperty(checksumKey) === checksum) return false;
  const payload = {
    event_id: Utilities.getUuid(),
    schema_version: CRM_SCHEMA_VERSION,
    row_id: rowId,
    row_number: rowNumber,
    changed_fields: fields,
    raw: {
      spreadsheet_id: spreadsheetId,
      tab: sheet.getName(),
      a1: options.a1
    },
    checksum,
    origin: "sheets",
    correlation_id: null
  };
  const response = postSigned_(
    props.getProperty("CRM_WEBHOOK_URL"),
    props.getProperty("CRM_WEBHOOK_SECRET"),
    payload
  );
  if (response && response.status === "failed") {
    throw new Error(response.error || "CRM rejected the row");
  }
  props.setProperty(checksumKey, checksum);
  return true;
}

function fullRowFields_(sheet, rowNumber, map) {
  const columns = Object.keys(map)
    .map(Number)
    .filter((column) => Number.isFinite(column))
    .sort((left, right) => left - right);
  if (columns.length === 0) return {};
  const firstColumn = columns[0];
  const lastColumn = columns[columns.length - 1];
  const values = sheet.getRange(
    rowNumber,
    firstColumn,
    1,
    lastColumn - firstColumn + 1
  ).getDisplayValues()[0];
  const fields = {};
  columns.forEach((column) => {
    const field = map[String(column)];
    if (!field || CRM_REALTIME_EXCLUDED_FIELDS.has(field)) return;
    const value = values[column - firstColumn];
    fields[field] = value === "" ? null : value;
  });
  return fields;
}

function hasMeaningfulFields_(fields) {
  return Object.values(fields).some((value) => {
    if (value === null || value === undefined) return false;
    const normalized = String(value).trim().toLowerCase();
    return normalized !== "" &&
      normalized !== "false" &&
      normalized !== "—" &&
      normalized !== "-";
  });
}

function syncChecksumKey_(sheetId, rowId) {
  return `CRM_SYNC_${sheetId}_${rowId}`;
}

function postSigned_(url, secret, payload) {
  if (!url || !secret) throw new Error("CRM webhook properties are not configured");
  const body = JSON.stringify(payload);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = hmacHex_(secret, `${timestamp}.${body}`);
  const response = UrlFetchApp.fetch(url, {
    method: "post",
    contentType: "application/json",
    payload: body,
    headers: {
      "X-CRM-Timestamp": timestamp,
      "X-CRM-Signature": `sha256=${signature}`
    },
    muteHttpExceptions: false
  });
  const text = response.getContentText();
  return text ? JSON.parse(text) : null;
}

function markCrmOrigin_(sheetId, rowNumber, ttlSeconds) {
  CacheService.getScriptCache().put(
    `crm-origin:${sheetId}:${rowNumber}`,
    "1",
    ttlSeconds || 60
  );
}

function hmacHex_(secret, value) {
  return bytesToHex_(Utilities.computeHmacSha256Signature(
    utf8Bytes_(value),
    utf8Bytes_(secret)
  ));
}

function sha256Hex_(value) {
  return bytesToHex_(Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    utf8Bytes_(value)
  ));
}

function stableJson_(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableJson_).join(",")}]`;
  return `{${Object.keys(value).sort().map(
    (key) => `${JSON.stringify(key)}:${stableJson_(value[key])}`
  ).join(",")}}`;
}

function bytesToHex_(bytes) {
  return bytes.map((value) => {
    const normalized = value < 0 ? value + 256 : value;
    return (`0${normalized.toString(16)}`).slice(-2);
  }).join("");
}

function utf8Bytes_(value) {
  return Utilities.newBlob(String(value), "text/plain").getBytes();
}

function columnNumber_(letters) {
  return String(letters).toUpperCase().split("").reduce(
    (result, character) => result * 26 + character.charCodeAt(0) - 64,
    0
  );
}

function isUuid_(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
    .test(String(value || ""));
}
