/**
 * Google Form -> Supabase bridge. Lives in the Apps Script project of the
 * spreadsheet LINKED to the expense Form (Extensions -> Apps Script).
 *
 * Script Properties required (File -> Project properties -> Script properties):
 *   SUPABASE_URL          e.g. https://xxxx.supabase.co
 *   SUPABASE_SERVICE_KEY  the service_role key
 *
 * Triggers required (clock icon -> Add trigger):
 *   onFormSubmitHandler  - From spreadsheet - On form submit
 *   retryErrors          - Time-driven - Day timer (also the keep-alive ping)
 */
var PROPS = PropertiesService.getScriptProperties();
var SUPABASE_URL = PROPS.getProperty('SUPABASE_URL');
var SERVICE_KEY = PROPS.getProperty('SUPABASE_SERVICE_KEY');

// Column headers in the responses sheet — must match the Form question titles.
var Q_NAME = 'Name';
var Q_CATEGORY = 'Category';
var Q_ARCA = 'ARCA';
var Q_CURRENCY = 'Currency';
var Q_AMOUNT = 'Amount';
var Q_PAYMENT = 'Payment Method';
var ARCA_NEGRO = 'Negro'; // the option meaning fiscal = false

function onFormSubmitHandler(e) {
  var payload = null;
  try {
    // NOTE: never parse e.values[0] with new Date() — on an es-AR sheet it is a
    // dd/MM/yyyy string that V8 misparses. The handler runs within seconds of
    // submission, so "now" IS the submission time.
    payload = buildPayload(e.namedValues, new Date());
    postTransaction(payload);
  } catch (err) {
    logError(payload || { raw: JSON.stringify(e && e.namedValues) }, err);
  }
}

/** Amount parser tolerant of es-AR formats; throws (-> errors tab) on garbage.
 *  PRECONDITION: the Form is es-AR locale with Number validation — a lone comma
 *  is a DECIMAL separator ('1,5' = 1.5) and dot-groups of 3 are thousands
 *  ('15.000' = 15000). If the Form locale ever changes, revisit this parser. */
function parseAmount(raw) {
  raw = String(raw).replace(/\s/g, '');
  var lastComma = raw.lastIndexOf(','), lastDot = raw.lastIndexOf('.');
  if (lastComma > -1 && lastDot > -1) {
    // both present: the later one is the decimal separator ('1.234,56' -> 1234.56)
    var thousands = lastComma > lastDot ? '.' : ',';
    raw = raw.split(thousands).join('').replace(',', '.');
  } else if (lastComma > -1) {
    raw = raw.replace(',', '.'); // decimal comma ('1500,50' -> 1500.50)
  } else if (/^\d{1,3}(\.\d{3})+$/.test(raw)) {
    raw = raw.split('.').join(''); // es-AR thousands dots ('15.000' -> 15000)
  }
  var n = parseFloat(raw);
  if (!isFinite(n) || n <= 0) throw new Error('unparseable amount: ' + raw);
  return n;
}

function buildPayload(v, submittedAt) {
  function first(key) {
    return (v[key] && v[key][0]) ? String(v[key][0]).trim() : '';
  }
  var arca = first(Q_ARCA);
  return {
    p_occurred_on: Utilities.formatDate(
      submittedAt, 'America/Argentina/Buenos_Aires', 'yyyy-MM-dd'),
    p_type: 'outflow', // the Form is outflow-only by design (spec §8)
    p_description: first(Q_NAME),
    p_category: first(Q_CATEGORY),
    p_amount: parseAmount(first(Q_AMOUNT)),
    p_currency: first(Q_CURRENCY) || 'ARS',
    p_fiscal: arca === '' ? true : arca !== ARCA_NEGRO, // blank -> Blanco
    p_payment_method: first(Q_PAYMENT) || null,
    p_source: 'form'
  };
}

function postTransaction(payload) {
  var res = UrlFetchApp.fetch(SUPABASE_URL + '/rest/v1/rpc/add_transaction', {
    method: 'post',
    contentType: 'application/json',
    headers: { apikey: SERVICE_KEY, Authorization: 'Bearer ' + SERVICE_KEY },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  if (res.getResponseCode() >= 300) {
    throw new Error(res.getResponseCode() + ': ' + res.getContentText());
  }
}

function logError(payload, err) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var tab = ss.getSheetByName('errors') || ss.insertSheet('errors');
  tab.appendRow([new Date(), JSON.stringify(payload), String(err)]);
}

/** Daily: retry failed posts, and ping Supabase so the free tier never pauses. */
function retryErrors() {
  try { keepAlive(); } catch (e) { /* ping failure must not abort retries */ }
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var tab = ss.getSheetByName('errors');
  if (!tab || tab.getLastRow() === 0) return;
  var rows = tab.getDataRange().getValues();
  for (var i = rows.length - 1; i >= 0; i--) {
    try {
      var parsed = JSON.parse(rows[i][1]);
      if (!parsed || parsed.p_amount == null) continue; // unfixable {raw:...} row: leave for manual review
      postTransaction(parsed);
      tab.deleteRow(i + 1);
    } catch (err) {
      // stays in the tab for the next daily retry
    }
  }
}

function keepAlive() {
  UrlFetchApp.fetch(SUPABASE_URL + '/rest/v1/categories?select=id&limit=1', {
    headers: { apikey: SERVICE_KEY, Authorization: 'Bearer ' + SERVICE_KEY },
    muteHttpExceptions: true
  });
}
