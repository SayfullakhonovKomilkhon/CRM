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
const CRM_SCENARIST_INBOUND_FIELDS = new Set([
  "external_id",
  "scenarist.name",
  "scenario_date",
  "speaker",
  "deadline",
  "scenario_type",
  "visual_format",
  "score",
  "research.competitor_url",
  "research.competitor_category",
  "research.full_analysis",
  "research.performance_metrics",
  "research.transcription",
  "research.timeline",
  "research.why_viral",
  "research.takeaways",
  "research.improvements",
  "research.replication_template",
  "research.ai_analysis",
  "content.claude_context",
  "content.cover_text",
  "content.script_text",
  "content.montage_brief",
  "content.scenarist_comment",
  "content.hook",
  "content.retention",
  "content.call_to_action",
  "content.visual_notes",
  "content.score_recommendations",
  "content.ai_review",
  "montage.source_material_url",
  "montage.client_brand_style",
  "montage.extra_brief",
  "montage.scenarist_material_comment",
  "montage.scenarist_revision_status",
  "montage.scenarist_revision_comment",
  "publication.publisher_brief",
  "publication.publication_date",
  "publication.description_dzen",
  "publication.description_youtube",
  "publication.description_tiktok",
  "publication.description_instagram",
  "publication.ai_social_descriptions",
  "publication.leia_script"
]);
const CRM_CANONICAL_INBOUND_COLUMN_MAP = {
  "1": "scenario_date",
  "2": "external_id",
  "3": "research.competitor_url",
  "4": "research.competitor_category",
  "5": "scenario_type",
  "6": "visual_format",
  "7": "research.full_analysis",
  "8": "research.performance_metrics",
  "9": "research.transcription",
  "10": "research.timeline",
  "11": "research.why_viral",
  "12": "research.takeaways",
  "13": "research.improvements",
  "14": "research.replication_template",
  "15": "research.ai_analysis",
  "16": "scenarist.name",
  "17": "content.claude_context",
  "18": "speaker",
  "19": "content.cover_text",
  "20": "content.script_text",
  "21": "content.montage_brief",
  "22": "content.scenarist_comment",
  "23": "content.hook",
  "24": "content.retention",
  "25": "content.call_to_action",
  "26": "content.visual_notes",
  "27": "content.score_recommendations",
  "28": "content.ai_review",
  "37": "montage.source_material_url",
  "38": "deadline",
  "39": "montage.client_brand_style",
  "40": "montage.extra_brief",
  "44": "montage.scenarist_material_comment",
  "53": "montage.scenarist_revision_status",
  "54": "montage.scenarist_revision_comment",
  "57": "publication.publication_date",
  "58": "publication.publisher_brief",
  "59": "publication.description_dzen",
  "60": "publication.description_youtube",
  "61": "publication.description_tiktok",
  "62": "publication.description_instagram",
  "80": "publication.ai_social_descriptions",
  "81": "publication.leia_script",
  "96": "score"
};
const CRM_SUBMISSION_HEADER = "Отправка на согласование";
const CRM_SUBMISSION_READY_VALUE = "Отправить";
const CRM_SOURCE_SUBMISSION_HEADER = "Отправить материал";
const CRM_PUBLICATION_SUBMISSION_HEADER = "Статус подготовки публикации";
const CRM_PUBLISHED_HEADER = "Опубликовано";
const CRM_LIVE_SOURCE_FIELDS = new Set([
  "montage.source_material_url",
  "montage.client_brand_style",
  "montage.extra_brief",
  "montage.scenarist_material_comment",
  "montage.scenarist_revision_status",
  "montage.scenarist_revision_comment"
]);
const CRM_SOURCE_CONTENT_FIELDS = new Set([
  "montage.source_material_url",
  "montage.client_brand_style",
  "montage.extra_brief",
  "montage.scenarist_material_comment"
]);
const CRM_LIVE_PUBLICATION_FIELDS = new Set([
  "publication.publication_date",
  "publication.publisher_brief",
  "publication.description_dzen",
  "publication.description_youtube",
  "publication.description_tiktok",
  "publication.description_instagram",
  "publication.ai_social_descriptions",
  "publication.leia_script"
]);
const CRM_PUBLICATION_AUTO_SUBMIT_FIELDS = new Set([
  "publication.publication_date",
  "publication.description_dzen",
  "publication.description_youtube",
  "publication.description_tiktok",
  "publication.description_instagram"
]);
const CRM_LIVE_FIELDS = new Set([
  ...CRM_LIVE_SOURCE_FIELDS,
  ...CRM_LIVE_PUBLICATION_FIELDS
]);

function installCrmSync() {
  const spreadsheet = SpreadsheetApp.getActive();
  const props = PropertiesService.getScriptProperties();
  const sourceTab = props.getProperty("CRM_SOURCE_TAB");
  const headerRow = Number(props.getProperty("CRM_HEADER_ROW") || "1");
  managedCrmSheets_(spreadsheet, props, headerRow).forEach((sheet) =>
    ensurePublishedCheckboxes_(sheet, headerRow, headerRow + 1, sheet.getMaxRows())
  );
  ScriptApp.getProjectTriggers()
    .filter((trigger) => ["onCrmEdit", CRM_RECONCILE_HANDLER]
      .includes(trigger.getHandlerFunction()))
    .forEach((trigger) => ScriptApp.deleteTrigger(trigger));
  ScriptApp.newTrigger("onCrmEdit").forSpreadsheet(spreadsheet).onEdit().create();
  ScriptApp.newTrigger(CRM_RECONCILE_HANDLER).timeBased().everyMinutes(5).create();
  normalizeProjectTabFormatting();
}

/**
 * Remove historical one-off cell colours from project tabs without touching
 * the legacy master tab. The first data row is the canonical visual template.
 */
function normalizeProjectTabFormatting() {
  const spreadsheet = SpreadsheetApp.getActive();
  const props = PropertiesService.getScriptProperties();
  const sourceTab = props.getProperty("CRM_SOURCE_TAB");
  const headerRow = Number(props.getProperty("CRM_HEADER_ROW") || "1");
  managedCrmSheets_(spreadsheet, props, headerRow)
    .filter((sheet) => !sourceTab || sheet.getName() !== sourceTab)
    .forEach((sheet) => normalizeProjectRows_(
      sheet,
      headerRow,
      headerRow + 1,
      sheet.getMaxRows()
    ));
}

function normalizeProjectRows_(sheet, headerRow, firstRow, lastRow) {
  const targetFirstRow = Math.max(firstRow, headerRow + 2);
  if (lastRow < targetFirstRow || sheet.getMaxRows() <= headerRow) return;
  const columnCount = sheet.getLastColumn();
  if (columnCount < 1) return;
  const template = sheet.getRange(headerRow + 1, 1, 1, columnCount);
  const destination = sheet.getRange(
    targetFirstRow,
    1,
    lastRow - targetFirstRow + 1,
    columnCount
  );
  template.copyTo(
    destination,
    SpreadsheetApp.CopyPasteType.PASTE_FORMAT,
    false
  );
  template.copyTo(
    destination,
    SpreadsheetApp.CopyPasteType.PASTE_DATA_VALIDATION,
    false
  );
}

function onCrmEdit(event) {
  if (!event || !event.range) return;
  const props = PropertiesService.getScriptProperties();
  const headerRow = Number(props.getProperty("CRM_HEADER_ROW") || "1");
  const sourceTab = props.getProperty("CRM_SOURCE_TAB");
  const sheet = event.range.getSheet();
  if (!isManagedCrmSheet_(sheet, headerRow, sourceTab)) return;
  if (event.range.getLastRow() <= headerRow) return;

  // Google API/script writes do not fire installable onEdit triggers. This cache marker
  // is an additional guard for explicit CRM-origin script operations.
  const map = withRequiredSourceColumns_(
    sheet,
    headerRow,
    JSON.parse(props.getProperty("CRM_INBOUND_COLUMN_MAP") || "{}")
  );
  const rowIdColumn = columnNumber_(props.getProperty("CRM_ROW_ID_COLUMN") || "A");
  const submissionColumn = submissionColumn_(sheet, headerRow, props);
  const sourceSubmissionColumn = workflowColumn_(
    sheet, headerRow, props, "CRM_SOURCE_SUBMISSION_COLUMN", CRM_SOURCE_SUBMISSION_HEADER
  );
  const publicationSubmissionColumn = workflowColumn_(
    sheet, headerRow, props, "CRM_PUBLICATION_SUBMISSION_COLUMN",
    CRM_PUBLICATION_SUBMISSION_HEADER
  );
  const firstRow = Math.max(event.range.getRow(), headerRow + 1);
  const lastRow = event.range.getLastRow();
  if (!sourceTab || sheet.getName() !== sourceTab) {
    normalizeProjectRows_(sheet, headerRow, firstRow, lastRow);
  }
  const editedColumns = new Set(Array.from(
    {length: event.range.getNumColumns()},
    (_, offset) => String(event.range.getColumn() + offset)
  ));
  const touchesMappedColumn = [
    submissionColumn,
    sourceSubmissionColumn,
    publicationSubmissionColumn
  ].some((column) => editedColumns.has(String(column))) || Object.keys(map).some(
    (column) => editedColumns.has(column) && CRM_SCENARIST_INBOUND_FIELDS.has(map[column])
  );
  if (!touchesMappedColumn) return;

  const spreadsheetId = event.source.getId();
  for (let rowNumber = firstRow; rowNumber <= lastRow; rowNumber += 1) {
    const suppressionKey = `crm-origin:${sheet.getSheetId()}:${rowNumber}`;
    if (CacheService.getScriptCache().get(suppressionKey)) continue;
    syncCrmRow_(sheet, rowNumber, map, rowIdColumn, props, spreadsheetId, {
      force: true,
      a1: event.range.getA1Notation(),
      submissionColumn,
      sourceSubmissionColumn,
      publicationSubmissionColumn,
      editedColumns
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
    const headerRow = Number(props.getProperty("CRM_HEADER_ROW") || "1");
    managedCrmSheets_(spreadsheet, props, headerRow).forEach((sheet) =>
      reconcileCrmSheet_(sheet, spreadsheet, props, headerRow)
    );
  } finally {
    lock.releaseLock();
  }
}

function reconcileCrmSheet_(sheet, spreadsheet, props, headerRow) {
    const lastRow = sheet.getLastRow();
    if (lastRow <= headerRow) return;
    const map = withRequiredSourceColumns_(
      sheet,
      headerRow,
      JSON.parse(props.getProperty("CRM_INBOUND_COLUMN_MAP") || "{}")
    );
    const rowIdColumn = columnNumber_(props.getProperty("CRM_ROW_ID_COLUMN") || "A");
    const submissionColumn = submissionColumn_(sheet, headerRow, props);
    const sourceSubmissionColumn = workflowColumn_(
      sheet, headerRow, props, "CRM_SOURCE_SUBMISSION_COLUMN", CRM_SOURCE_SUBMISSION_HEADER
    );
    const publicationSubmissionColumn = workflowColumn_(
      sheet, headerRow, props, "CRM_PUBLICATION_SUBMISSION_COLUMN",
      CRM_PUBLICATION_SUBMISSION_HEADER
    );
    const configuredBatch = Number(
      props.getProperty("CRM_RECONCILE_BATCH_SIZE") || CRM_RECONCILE_DEFAULT_BATCH_SIZE
    );
    const batchSize = Math.max(1, Math.min(500, configuredBatch || 0));
    const cursorKey = `CRM_RECONCILE_CURSOR_${sheet.getSheetId()}`;
    let cursor = Number(props.getProperty(cursorKey) || headerRow + 1);
    if (cursor <= headerRow || cursor > lastRow) cursor = headerRow + 1;
    const endRow = Math.min(lastRow, cursor + batchSize - 1);
    const claimedRowIds = {};
    ensurePublishedCheckboxes_(sheet, headerRow, cursor, endRow);

    for (let rowNumber = cursor; rowNumber <= endRow; rowNumber += 1) {
      try {
        syncCrmRow_(
          sheet,
          rowNumber,
          map,
          rowIdColumn,
          props,
          spreadsheet.getId(),
          {
            force: false,
            a1: `${rowNumber}:${rowNumber}`,
            claimedRowIds,
            submissionColumn,
            sourceSubmissionColumn,
            publicationSubmissionColumn
          }
        );
      } catch (error) {
        // One malformed or workflow-locked row must not block rows below it.
        console.warn(`CRM row ${rowNumber} was not synchronized: ${error.message}`);
      }
    }
    props.setProperty(cursorKey, String(endRow >= lastRow ? headerRow + 1 : endRow + 1));
}

function isManagedCrmSheet_(sheet, headerRow, sourceTab) {
  if (!sheet) return false;
  if (sourceTab && sheet.getName() === sourceTab) return true;
  const lastColumn = sheet.getLastColumn();
  if (lastColumn < 1 || sheet.getMaxRows() < headerRow) return false;
  const headers = sheet.getRange(headerRow, 1, 1, lastColumn)
    .getDisplayValues()[0]
    .map(normalizeText_);
  return headers.includes(normalizeText_(CRM_SUBMISSION_HEADER)) &&
    headers.includes(normalizeText_("ID"));
}

function managedCrmSheets_(spreadsheet, props, headerRow) {
  const sourceTab = props.getProperty("CRM_SOURCE_TAB");
  return spreadsheet.getSheets().filter((sheet) =>
    isManagedCrmSheet_(sheet, headerRow, sourceTab)
  );
}

function syncCrmRow_(sheet, rowNumber, map, rowIdColumn, props, spreadsheetId, options) {
  const submissionCell = sheet.getRange(rowNumber, options.submissionColumn);
  const submissionStatus = submissionCell.getDisplayValue().trim();
  const submissionRequested = isSubmissionRequested_(submissionStatus);
  const sourceSubmissionCell = sheet.getRange(rowNumber, options.sourceSubmissionColumn);
  const sourceSubmissionStatus = sourceSubmissionCell.getDisplayValue().trim();
  const publicationSubmissionCell = sheet.getRange(
    rowNumber, options.publicationSubmissionColumn
  );
  const publicationSubmissionStatus = publicationSubmissionCell.getDisplayValue().trim();
  const rowIdCell = sheet.getRange(rowNumber, rowIdColumn);
  const rawRowId = rowIdCell.getDisplayValue().trim();
  const hasExistingIdentity = isUuid_(rawRowId);
  const liveColumns = new Set(
    Array.from(options.editedColumns || []).filter(
      (column) => CRM_LIVE_FIELDS.has(map[column])
    )
  );
  const isRecoveryScan = !options.editedColumns;
  const publicationColumns = new Set(
    Object.keys(map).filter((column) => CRM_LIVE_PUBLICATION_FIELDS.has(map[column]))
  );
  const publicationReady = rowHasPublicationReadyContent_(sheet, rowNumber, map);
  const publicationColumnEdited = !isRecoveryScan &&
    Array.from(options.editedColumns).some((column) => publicationColumns.has(column));
  const sourceSubmitRequested = !submissionRequested && hasExistingIdentity &&
    isWorkflowSubmissionRequested_(sourceSubmissionStatus) &&
    (isRecoveryScan || options.editedColumns.has(String(options.sourceSubmissionColumn)));
  const explicitPublicationSubmit = isWorkflowSubmissionRequested_(
    publicationSubmissionStatus
  ) && (isRecoveryScan || options.editedColumns.has(
    String(options.publicationSubmissionColumn)
  ));
  const automaticPublicationSubmit = publicationReady &&
    (isRecoveryScan || publicationColumnEdited);
  const publicationSubmitRequested = !submissionRequested && hasExistingIdentity &&
    (explicitPublicationSubmit || automaticPublicationSubmit);
  const liveUpdate = !submissionRequested && !sourceSubmitRequested &&
    !publicationSubmitRequested && hasExistingIdentity && liveColumns.size > 0;
  if (!submissionRequested && !sourceSubmitRequested &&
      !publicationSubmitRequested && !liveUpdate) return false;
  const syncMode = submissionRequested
    ? "submission"
    : sourceSubmitRequested
    ? "source_material_submit"
    : publicationSubmitRequested
    ? "publication_submit"
    : "scenarist_live_update";
  const selectedColumns = sourceSubmitRequested
    ? new Set(Object.keys(map).filter((column) => CRM_LIVE_SOURCE_FIELDS.has(map[column])))
    : publicationSubmitRequested
    ? new Set(Object.keys(map).filter((column) => CRM_LIVE_PUBLICATION_FIELDS.has(map[column])))
    : liveUpdate
    ? liveColumns
    : null;
  const fields = fullRowFields_(sheet, rowNumber, map, {
    includeEmptySourceFields: hasExistingIdentity,
    onlyColumns: selectedColumns
  });

  let rowId = rawRowId;
  const claimed = options.claimedRowIds || null;
  const duplicateIdentity = hasExistingIdentity && (
    (claimed && claimed[rowId]) ||
    rowIdAppearsEarlier_(sheet, rowNumber, rowIdColumn, rowId, props)
  );
  if (!hasExistingIdentity || duplicateIdentity) {
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
      a1: options.a1,
      submission_status: submissionStatus,
      sync_mode: syncMode
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
    if (String(response.error || "").includes("workflow has started")) {
      // The CRM owns the row after submission. Remember the snapshot so the
      // periodic recovery does not retry it until the Sheet changes again.
      props.setProperty(checksumKey, checksum);
      return false;
    }
    throw new Error(response.error || "CRM rejected the row");
  }
  props.setProperty(checksumKey, checksum);
  if (sourceSubmitRequested) sourceSubmissionCell.setValue("ready_for_review");
  if (publicationSubmitRequested) {
    publicationSubmissionCell.setValue("ready_for_review");
  }
  if (liveUpdate && Array.from(liveColumns).some(
    (column) => CRM_SOURCE_CONTENT_FIELDS.has(map[column])
  )) sourceSubmissionCell.setValue("draft");
  if (liveUpdate && Array.from(liveColumns).some(
    (column) => CRM_LIVE_PUBLICATION_FIELDS.has(map[column])
  )) publicationSubmissionCell.setValue("draft");
  // Only the initial review marker is consumed. Later stages show canonical status.
  if (submissionRequested) submissionCell.clearContent();
  return true;
}

function submissionColumn_(sheet, headerRow, props) {
  const configured = String(
    props.getProperty("CRM_SUBMISSION_COLUMN") || ""
  ).trim();
  if (configured) return columnNumber_(configured);
  const headers = sheet.getRange(1, 1, headerRow, sheet.getLastColumn())
    .getDisplayValues()[headerRow - 1];
  const expected = normalizeText_(CRM_SUBMISSION_HEADER);
  const matches = [];
  headers.forEach((header, index) => {
    if (normalizeText_(header) === expected) matches.push(index + 1);
  });
  if (matches.length !== 1) {
    throw new Error(
      `Expected exactly one "${CRM_SUBMISSION_HEADER}" column in row ${headerRow}`
    );
  }
  return matches[0];
}

function workflowColumn_(sheet, headerRow, props, propertyName, headerName) {
  const configured = String(props.getProperty(propertyName) || "").trim();
  if (configured) return columnNumber_(configured);
  const headers = sheet.getRange(headerRow, 1, 1, sheet.getLastColumn())
    .getDisplayValues()[0];
  const expected = normalizeText_(headerName);
  const matches = [];
  headers.forEach((header, index) => {
    if (normalizeText_(header) === expected) matches.push(index + 1);
  });
  if (matches.length !== 1) {
    throw new Error(
      `Expected exactly one "${headerName}" column in row ${headerRow}`
    );
  }
  return matches[0];
}

function ensurePublishedCheckboxes_(sheet, headerRow, firstRow, lastRow) {
  if (lastRow < firstRow) return;
  const headers = sheet.getRange(headerRow, 1, 1, sheet.getLastColumn())
    .getDisplayValues()[0];
  const expected = normalizeText_(CRM_PUBLISHED_HEADER);
  const matches = [];
  headers.forEach((header, index) => {
    if (normalizeText_(header) === expected) matches.push(index + 1);
  });
  if (matches.length !== 1) return;
  const rule = SpreadsheetApp.newDataValidation().requireCheckbox().build();
  sheet.getRange(firstRow, matches[0], lastRow - firstRow + 1, 1)
    .setDataValidation(rule);
}

function withRequiredSourceColumns_(sheet, headerRow, configuredMap) {
  const headers = sheet.getRange(headerRow, 1, 1, sheet.getLastColumn())
    .getDisplayValues()[0];
  const canonicalLayout = (
    normalizeText_(headers[1]) === "id" &&
    normalizeText_(headers[19]) === "сценарий"
  );
  const map = Object.assign(
    {},
    canonicalLayout ? CRM_CANONICAL_INBOUND_COLUMN_MAP : {},
    configuredMap
  );
  if (Object.values(map).includes("external_id")) return map;
  const idAliases = new Set(["id", "ид", "номер"]);
  const matches = [];
  headers.forEach((header, index) => {
    if (idAliases.has(normalizeText_(header))) matches.push(index + 1);
  });
  if (matches.length === 1) map[String(matches[0])] = "external_id";
  return map;
}

function isSubmissionRequested_(value) {
  return normalizeText_(value) === normalizeText_(CRM_SUBMISSION_READY_VALUE);
}

function isWorkflowSubmissionRequested_(value) {
  const normalized = normalizeText_(value);
  return ["отправить", "готово", "ready for review"].includes(normalized);
}

function normalizeText_(value) {
  return String(value || "").trim().toLocaleLowerCase("ru-RU")
    .replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

function rowIdAppearsEarlier_(sheet, rowNumber, rowIdColumn, rowId, props) {
  const firstDataRow = Number(props.getProperty("CRM_HEADER_ROW") || "1") + 1;
  if (rowNumber <= firstDataRow) return false;
  return sheet.getRange(
    firstDataRow,
    rowIdColumn,
    rowNumber - firstDataRow,
    1
  ).getDisplayValues().some((values) => values[0].trim() === rowId);
}

function fullRowFields_(sheet, rowNumber, map, options) {
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
    if (options.onlyColumns && !options.onlyColumns.has(String(column))) return;
    const field = map[String(column)];
    if (!field || !CRM_SCENARIST_INBOUND_FIELDS.has(field)) return;
    const value = values[column - firstColumn];
    if (value === "" && !options.includeEmptySourceFields) return;
    fields[field] = value === "" ? null : value;
  });
  return fields;
}

function rowHasPublicationReadyContent_(sheet, rowNumber, map) {
  const columns = Object.keys(map)
    .map(Number)
    .filter((column) => CRM_PUBLICATION_AUTO_SUBMIT_FIELDS.has(map[String(column)]));
  return columns.some((column) => (
    sheet.getRange(rowNumber, column).getDisplayValue().trim() !== ""
  ));
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
