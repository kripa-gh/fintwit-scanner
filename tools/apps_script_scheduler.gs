/**
 * FinTwit scheduler — Google Apps Script alternative to cron-job.org.
 * Paste into script.google.com, set TOKEN, run setupTrigger() once.
 * Checks every 15 min; dispatches inside IST windows; dedupes per day/mode.
 */
var TOKEN = "PASTE_FINE_GRAINED_TOKEN_HERE"; // Actions:RW on fintwit-scanner ONLY
var REPO  = "kripa-gh/fintwit-scanner";

function setupTrigger() {
  ScriptApp.newTrigger("tick").timeBased().everyMinutes(15).create();
}

function tick() {
  var now = new Date();
  var ist = new Date(now.getTime() + (5.5 * 60 + now.getTimezoneOffset()) * 60000);
  var dow = ist.getDay();                       // 0=Sun..6=Sat
  if (dow === 0 || dow === 6) return;
  var mins = ist.getHours() * 60 + ist.getMinutes();
  var mode = null;
  if (mins >= 500 && mins < 545) mode = "premarket";   // 08:20-09:04 IST
  if (mins >= 955 && mins < 1015) mode = "main";       // 15:55-16:54 IST
  if (!mode) return;
  var key = mode + ":" + ist.toISOString().slice(0, 10);
  var props = PropertiesService.getScriptProperties();
  if (props.getProperty(key)) return;                  // already sent today
  var resp = UrlFetchApp.fetch(
    "https://api.github.com/repos/" + REPO + "/actions/workflows/daily_scan.yml/dispatches",
    {
      method: "post",
      headers: { Authorization: "Bearer " + TOKEN, Accept: "application/vnd.github+json" },
      contentType: "application/json",
      payload: JSON.stringify({ ref: "main", inputs: { mode: mode } }),
      muteHttpExceptions: true,
    }
  );
  if (resp.getResponseCode() === 204) props.setProperty(key, "1");
  else MailApp.sendEmail(Session.getActiveUser().getEmail(),
    "FinTwit scheduler FAILED", "HTTP " + resp.getResponseCode() + ": " + resp.getContentText());
}
