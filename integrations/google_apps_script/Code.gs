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

function installCrmSync() {
  const spreadsheet = SpreadsheetApp.getActive();
  ScriptApp.getProjectTriggers()
    .filter((trigger) => trigger.getHandlerFunction() === "onCrmEdit")
    .forEach((trigger) => ScriptApp.deleteTrigger(trigger));
  ScriptApp.newTrigger("onCrmEdit").forSpreadsheet(spreadsheet).onEdit().create();
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
  const firstColumn = event.range.getColumn();
  const columnCount = event.range.getNumColumns();
  const values = sheet.getRange(
    firstRow,
    firstColumn,
    lastRow - firstRow + 1,
    columnCount
  ).getDisplayValues();

  for (let rowOffset = 0; rowOffset < values.length; rowOffset += 1) {
    const rowNumber = firstRow + rowOffset;
    const suppressionKey = `crm-origin:${sheet.getSheetId()}:${rowNumber}`;
    if (CacheService.getScriptCache().get(suppressionKey)) continue;
    const changedFields = {};
    for (let columnOffset = 0; columnOffset < columnCount; columnOffset += 1) {
      const field = map[String(firstColumn + columnOffset)];
      if (field) changedFields[field] = values[rowOffset][columnOffset] || null;
    }
    if (Object.keys(changedFields).length === 0) continue;

    const rowIdCell = sheet.getRange(rowNumber, rowIdColumn);
    let rowId = String(rowIdCell.getValue() || "").trim();
    if (!rowId) {
      rowId = Utilities.getUuid();
      rowIdCell.setValue(rowId);
    }
    const payload = {
      event_id: Utilities.getUuid(),
      schema_version: CRM_SCHEMA_VERSION,
      row_id: rowId,
      row_number: rowNumber,
      changed_fields: changedFields,
      raw: {
        spreadsheet_id: event.source.getId(),
        tab: sheet.getName(),
        a1: event.range.getA1Notation()
      },
      checksum: sha256Hex_(stableJson_(changedFields)),
      origin: "sheets",
      correlation_id: null
    };
    postSigned_(
      props.getProperty("CRM_WEBHOOK_URL"),
      props.getProperty("CRM_WEBHOOK_SECRET"),
      payload
    );
  }
}

function postSigned_(url, secret, payload) {
  if (!url || !secret) throw new Error("CRM webhook properties are not configured");
  const body = JSON.stringify(payload);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = hmacHex_(secret, `${timestamp}.${body}`);
  UrlFetchApp.fetch(url, {
    method: "post",
    contentType: "application/json",
    payload: body,
    headers: {
      "X-CRM-Timestamp": timestamp,
      "X-CRM-Signature": `sha256=${signature}`
    },
    muteHttpExceptions: false
  });
}

function markCrmOrigin_(sheetId, rowNumber, ttlSeconds) {
  CacheService.getScriptCache().put(
    `crm-origin:${sheetId}:${rowNumber}`,
    "1",
    ttlSeconds || 60
  );
}

function hmacHex_(secret, value) {
  return bytesToHex_(Utilities.computeHmacSha256Signature(value, secret));
}

function sha256Hex_(value) {
  return bytesToHex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, value));
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

function columnNumber_(letters) {
  return String(letters).toUpperCase().split("").reduce(
    (result, character) => result * 26 + character.charCodeAt(0) - 64,
    0
  );
}
