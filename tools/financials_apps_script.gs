/**
 * FAM Financials: refresh the Google Sheet's Daily Spend tab from production.
 *
 * Paste into the sheet (Extensions > Apps Script), then:
 *   1. Project Settings > Script properties: add FAM_ADMIN_TOKEN with
 *      production's admin token. It stays in the script, never in a cell.
 *   2. Run installDailyTrigger once and allow access. From then on
 *      refreshFamSpend runs every morning; run it by hand for a refresh now.
 *
 * It replaces the Daily Spend rows with /api/admin/financials/daily.json
 * (app.py) and sets Costs!B3 to the day the figures are as of. Costs and
 * Projections are formulas over those rows, so they follow by themselves.
 */
const FAM_URL = 'https://fam.onrender.com/api/admin/financials/daily.json';
const MONEY = '$#,##0.00;($#,##0.00);"-"';

function refreshFamSpend() {
  const token = PropertiesService.getScriptProperties().getProperty('FAM_ADMIN_TOKEN');
  if (!token) {
    throw new Error('Add FAM_ADMIN_TOKEN under Project Settings > Script properties.');
  }
  const res = UrlFetchApp.fetch(FAM_URL, {
    headers: {'X-Admin-Token': token},
    muteHttpExceptions: true,
  });
  if (res.getResponseCode() !== 200) {
    throw new Error('FAM answered ' + res.getResponseCode() +
        ' (404: wrong token, or the financials endpoint is not deployed yet).');
  }
  const data = JSON.parse(res.getContentText());
  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName('Daily Spend');
  const costs = ss.getSheetByName('Costs');
  if (!sheet || !costs) {
    throw new Error('This sheet needs its "Daily Spend" and "Costs" tabs, named exactly that.');
  }
  const last = sheet.getLastRow();
  if (last > 1) sheet.getRange(2, 1, last - 1, 6).clearContent();
  // Real dates, not "2026-10-08" text: the Costs and Projections formulas
  // compare dates, and a text cell would quietly drop out of every sum.
  const rows = data.rows.map(function (r) { return [asDate(r[0])].concat(r.slice(1)); });
  if (rows.length) {
    sheet.getRange(2, 1, rows.length, 6).setValues(rows);
    sheet.getRange(2, 1, rows.length, 1).setNumberFormat('yyyy-mm-dd');
    sheet.getRange(2, 2, rows.length, 1).setNumberFormat('#,##0;(#,##0);"-"');
    sheet.getRange(2, 3, rows.length, 4).setNumberFormat(MONEY);
  }
  costs.getRange('B3').setValue(asDate(data.as_of));
}

/** "2026-10-08" as a date at noon UTC, so no time zone moves it a day. */
function asDate(iso) {
  return new Date(iso + 'T12:00:00Z');
}

function installDailyTrigger() {
  ScriptApp.getProjectTriggers()
      .filter(function (t) { return t.getHandlerFunction() === 'refreshFamSpend'; })
      .forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('refreshFamSpend').timeBased().everyDays(1).atHour(7).create();
  refreshFamSpend();
}
