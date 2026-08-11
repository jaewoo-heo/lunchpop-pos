/**
 * 런치팝 GAS 백엔드 v2.3
 * 변경: fetchV2 날짜 필터, markDone rowIndex O(1) 처리, sha256 업데이트 검증, 로그 자동 정리
 */
function doGet(e) {
  var action = e.parameter.action;
  var storeName = e.parameter.storeName || "시스템";
  var logMsg = e.parameter.logMsg;

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("data");
  var logSheet = ss.getSheetByName("log");

  // ── [액션 1] 업데이트 확인 ──
  // 버전관리 시트 구조:
  //   A2 = 버전번호 (예: 3.3)
  //   B2 = 다운로드 URL (GitHub Release asset URL)
  //   C2 = sha256 해시값 (선택)
  //   D2 = GitHub Token (Private 저장소일 때만 입력, Public이면 빈칸)
  if (action === "checkUpdate") {
    var versionSheet = ss.getSheetByName("버전관리");
    var vData = { version: "1.0", url: "", sha256: "", gh_token: "" };
    if (versionSheet) {
      vData.version  = String(versionSheet.getRange("A2").getValue());
      vData.url      = String(versionSheet.getRange("B2").getValue());
      vData.sha256   = String(versionSheet.getRange("C2").getValue());
      vData.gh_token = String(versionSheet.getRange("D2").getValue());
    }
    return createJSON(vData);
  }

  // ── [액션 2] 매장 리스트 가져오기 ──
  if (action === "getStores") {
    var storeSheet = ss.getSheetByName("매장목록");
    var storeList = [];
    if (storeSheet) {
      var sData = storeSheet.getRange("A2:A").getValues();
      for (var i = 0; i < sData.length; i++) {
        if (sData[i][0] !== "") storeList.push(sData[i][0]);
      }
    }
    return createJSON(storeList.length === 0 ? ["매장목록 없음"] : storeList);
  }

  // ── [액션 3] 로그 기록 ──
  if (action === "log" && logMsg) {
    if (logSheet) {
      // 로그 시트 2000행 초과 시 오래된 500행 삭제
      var logLastRow = logSheet.getLastRow();
      if (logLastRow > 2000) {
        logSheet.deleteRows(2, 500);
      }
      var formattedTime = Utilities.formatDate(new Date(), "GMT+9", "yyyy/MM/dd HH:mm:ss");
      logSheet.appendRow([formattedTime, storeName, "'" + logMsg]);
    }
    return createJSON({ status: "success" });
  }

  // ── [액션 4] 인쇄 완료 처리 ──
  // rowIndex가 있으면 O(1) 직접 접근, 없으면 orderNo로 선형 탐색 (하위 호환)
  if (action === "markDone") {
    if (!sheet) return createJSON({ status: "error", msg: "data 시트 없음" });

    var rowIndex = parseInt(e.parameter.rowIndex || "0");
    var orderNo  = e.parameter.orderNo || "";

    if (rowIndex >= 2) {
      // 신규: rowIndex로 O(1) 처리
      sheet.getRange(rowIndex, 12).setValue("TRUE");
      return createJSON({ status: "success" });
    } else if (orderNo) {
      // 구버전 클라이언트 호환: orderNo 선형 탐색
      var lastRow = sheet.getLastRow();
      if (lastRow < 2) return createJSON({ status: "not_found" });
      var allData = sheet.getRange(2, 8, lastRow - 1, 1).getValues();
      for (var i = 0; i < allData.length; i++) {
        if (String(allData[i][0]) === String(orderNo)) {
          sheet.getRange(i + 2, 12).setValue("TRUE");
          return createJSON({ status: "success" });
        }
      }
      return createJSON({ status: "not_found" });
    }

    return createJSON({ status: "error", msg: "rowIndex 또는 orderNo 필요" });
  }

  // ── [액션 5] 전체 주문 현황 가져오기 (신규 정식 버전) ──
  if (action === "fetchV2") {
    if (!sheet) return createJSON([]);
    var lastRow = sheet.getLastRow();
    if (lastRow < 2) return createJSON([]);

    // P2셀(16열)의 날짜를 기준으로 필터 — 운영자가 수동 입력하는 서비스 날짜
    // P2가 비어있으면 날짜 필터 없이 전체 반환 (안전장치)
    var refDate = sheet.getRange(2, 16).getDisplayValue().trim();

    // 날짜 정규화: 숫자만 추출하여 비교 (구분자, 요일, 괄호 등 모두 무시)
    // 예) "2026.06.09.(화)" = "2026/06/09" = "2026-06-09" 모두 "20260609"으로 동일 처리
    var normDate = function(d) {
      return d.replace(/\D/g, '');
    };
    var refNorm = normDate(refDate);

    var values = sheet.getRange(2, 1, lastRow - 1, 12).getDisplayValues();
    var result = [];

    for (var j = 0; j < values.length; j++) {
      var row = values[j];
      // 정규화된 날짜 비교 — 구분자 형식 달라도 매칭
      var dateMatch = (refDate === "") || (normDate(row[6]) === refNorm);
      if (row[4].indexOf(storeName) !== -1 && dateMatch) {
        result.push({
          rowIndex:     j + 2,          // markDone O(1)용 행 번호
          customerName: row[1],
          menuName:     row[2],
          quantity:     row[3],
          storeName:    row[4],
          address:      row[9],
          resDate:      row[6],
          orderNo:      row[7],
          deliveryTime: row[8],
          isQueued:     row[10] === "TRUE",
          isPrinted:    row[11] === "TRUE"
        });
      }
    }
    return createJSON(result);
  }

  // ── [액션 6] 레거시 fetch (하위 호환 유지) ──
  if (action === "fetch" || (!action && storeName)) {
    if (!sheet) return createJSON([]);
    var lastRow = sheet.getLastRow();
    if (lastRow < 2) return createJSON([]);

    var values = sheet.getRange(2, 1, lastRow - 1, 12).getDisplayValues();
    var result = [];

    for (var j = 0; j < values.length; j++) {
      var row = values[j];
      // 기존 방식: K열 TRUE이고 L열 비어있는 것만 전달
      if (row[4].indexOf(storeName) !== -1 && row[10] === "TRUE" && row[11] !== "TRUE") {
        result.push({
          customerName: row[1], menuName: row[2], quantity: row[3],
          storeName:    row[4], address: row[9], resDate: row[6],
          orderNo:      row[7], deliveryTime: row[8]
        });
      }
    }
    return createJSON(result);
  }

  return createJSON({ status: "unknown_action", action: action });
}



function createJSON(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}


// ── [이벤트] P2 날짜 변경 시 K/L열 초기화 ──
function onEdit(e) {
  var sheet = e.source.getActiveSheet();
  if (sheet.getName() !== "data") return;
  if (e.range.getRow() === 2 && e.range.getColumn() === 16) {
    var lastRow = sheet.getLastRow();
    if (lastRow >= 2) {
      sheet.getRange(2, 11, lastRow - 1, 1).clearContent();
      sheet.getRange(2, 12, lastRow - 1, 1).clearContent();
    }
  }
}


// ==========================================================
// [백업 감지] 매장 PC 장애 감지 → Slack 알림
// - checkHealthAndAlert()를 10분 주기 시간 트리거로 등록해서 사용
//   (Apps Script 편집기에서 setupHealthCheckTrigger()를 한 번만 수동 실행하면 등록됨)
// - 버전관리 시트 G2 = Slack 웹훅 URL (비어있으면 조용히 아무것도 안 함)
// - 평일(월~금, KST)에만 동작. 매장당 알림은 1시간에 한 번으로 제한(캐시)
// ==========================================================
var HEALTH_SILENT_MINUTES = 90;   // 마지막 로그로부터 이 이상 조용하면 "폴링 끊김"
var HEALTH_URGENT_MINUTES = 15;   // 배달예정시간까지 이 이하로 남았는데 미인쇄면 "미인쇄 지연"
var HEALTH_ALERT_TTL_SEC  = 3600; // 매장당 알림 재발송 최소 간격(초)

function _isWeekdayKST(now) {
  // 스크립트 프로젝트의 타임존 설정에 의존하지 않기 위해, now를 +9시간 이동한 뒤
  // UTC getter로 읽어 "KST 벽시계 기준 요일"을 구함
  var kst = new Date(now.getTime() + 9 * 60 * 60 * 1000);
  var dow = kst.getUTCDay(); // 0=일 ... 6=토 (KST 기준)
  return dow >= 1 && dow <= 5;
}

function _isDeliveryUrgent(deliveryTimeStr, now) {
  if (!deliveryTimeStr) return false;
  var clean = String(deliveryTimeStr).replace(/시 ?/g, ':').replace('분', '');
  var m = clean.match(/(\d{1,2}):\s?(\d{1,2})/);
  if (!m) return false;
  var h = parseInt(m[1], 10), min = parseInt(m[2], 10);
  if (deliveryTimeStr.indexOf("오후") !== -1 && h < 12) h += 12;
  else if (deliveryTimeStr.indexOf("오전") !== -1 && h === 12) h = 0;

  var kst = new Date(now.getTime() + 9 * 60 * 60 * 1000);
  // KST 기준 "오늘 h시 min분"을 다시 실제 UTC epoch로 환산(위와 반대 방향으로 9시간 보정)
  var deliveryEpoch = Date.UTC(kst.getUTCFullYear(), kst.getUTCMonth(), kst.getUTCDate(), h, min, 0) - 9 * 60 * 60 * 1000;
  var diffMin = (deliveryEpoch - now.getTime()) / 60000;
  return diffMin <= HEALTH_URGENT_MINUTES; // 이미 지났거나(음수) 곧 임박
}

function _lastLogTimeByStore(logSheet, storeNames) {
  var result = {};
  if (!logSheet) return result;
  var lastRow = logSheet.getLastRow();
  if (lastRow < 2) return result;

  var scanRows = Math.min(1000, lastRow - 1);
  var startRow = lastRow - scanRows + 1;
  var data = logSheet.getRange(startRow, 1, scanRows, 2).getValues(); // A=시각, B=매장명

  var remaining = {};
  storeNames.forEach(function (s) { remaining[s] = true; });

  for (var i = data.length - 1; i >= 0; i--) {
    var store = String(data[i][1]).trim();
    if (remaining[store]) {
      var t = data[i][0];
      var d = (t instanceof Date) ? t : new Date(String(t).replace(/\//g, '-'));
      if (!isNaN(d.getTime())) result[store] = d;
      delete remaining[store];
      if (Object.keys(remaining).length === 0) break;
    }
  }
  return result;
}

function checkHealthAndAlert() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var logSheet = ss.getSheetByName("log");

  try {
    var now = new Date();
    if (!_isWeekdayKST(now)) return; // 주말은 감지하지 않음

    var versionSheet = ss.getSheetByName("버전관리");
    var webhookUrl = versionSheet ? String(versionSheet.getRange("G2").getValue()).trim() : "";
    if (!webhookUrl) return; // 웹훅 URL 미설정 시 조용히 종료

    var storeSheet = ss.getSheetByName("매장목록");
    var dataSheet = ss.getSheetByName("data");
    if (!storeSheet || !dataSheet) return;

    var storeNames = storeSheet.getRange("A2:A").getValues()
      .map(function (r) { return String(r[0]).trim(); })
      .filter(function (s) { return s !== ""; });
    if (storeNames.length === 0) return;

    var lastLogMap = _lastLogTimeByStore(logSheet, storeNames);

    // fetchV2와 동일한 기준으로 오늘 서비스 날짜(P2) 필터링
    var refDate = dataSheet.getRange(2, 16).getDisplayValue().trim();
    var normDate = function (d) { return String(d).replace(/\D/g, ''); };
    var refNorm = normDate(refDate);

    var pendingByStore = {};
    var lastRowData = dataSheet.getLastRow();
    if (lastRowData >= 2) {
      var values = dataSheet.getRange(2, 1, lastRowData - 1, 12).getDisplayValues();
      for (var i = 0; i < values.length; i++) {
        var row = values[i];
        var store = String(row[4]).trim();
        var dateMatch = (refDate === "") || (normDate(row[6]) === refNorm);
        var isQueued = row[10] === "TRUE";
        var isPrinted = row[11] === "TRUE";
        if (dateMatch && isQueued && !isPrinted) {
          if (!pendingByStore[store]) pendingByStore[store] = [];
          pendingByStore[store].push(row[8]); // 배달예정시간
        }
      }
    }

    var cache = CacheService.getScriptCache();

    storeNames.forEach(function (store) {
      var issueType = "", detail = "";

      var lastLog = lastLogMap[store];
      var silentMin = lastLog ? Math.floor((now - lastLog) / 60000) : null;
      if (silentMin === null || silentMin >= HEALTH_SILENT_MINUTES) {
        issueType = "폴링 끊김";
        detail = (silentMin === null)
          ? "해당 매장의 로그 기록이 없음 (한 번도 연결되지 않았거나 오래 중단됨)"
          : ("마지막 응답: " + silentMin + "분 전");
      } else {
        var pendingTimes = pendingByStore[store] || [];
        var urgent = pendingTimes.filter(function (dt) { return _isDeliveryUrgent(dt, now); });
        if (urgent.length > 0) {
          issueType = "미인쇄 지연";
          detail = "미인쇄 " + urgent.length + "건, 배달예정 임박/초과 (" + urgent.join(", ") + ")";
        }
      }

      if (!issueType) return;

      var cacheKey = "healthalert_" + store;
      if (cache.get(cacheKey)) return; // 매장당 1시간 내 중복 발송 방지
      cache.put(cacheKey, "1", HEALTH_ALERT_TTL_SEC);

      try {
        UrlFetchApp.fetch(webhookUrl, {
          method: "post",
          contentType: "application/json",
          payload: JSON.stringify({
            store_name: store,
            issue_type: issueType,
            detail: detail,
            detected_at: Utilities.formatDate(now, "GMT+9", "yyyy-MM-dd HH:mm")
          }),
          muteHttpExceptions: true
        });
      } catch (sendErr) {
        if (logSheet) {
          logSheet.appendRow([Utilities.formatDate(now, "GMT+9", "yyyy/MM/dd HH:mm:ss"),
                               "시스템", "[ERR] Slack 알림 전송 실패(" + store + "): " + String(sendErr)]);
        }
      }
    });
  } catch (err) {
    try {
      if (logSheet) {
        logSheet.appendRow([Utilities.formatDate(new Date(), "GMT+9", "yyyy/MM/dd HH:mm:ss"),
                             "시스템", "[ERR] checkHealthAndAlert 실패: " + String(err)]);
      }
    } catch (e2) { /* 로그 기록조차 실패하면 포기 */ }
  }
}

function setupHealthCheckTrigger() {
  // Apps Script 편집기에서 이 함수를 한 번만 수동으로 실행하면 10분마다
  // checkHealthAndAlert가 자동 실행되도록 트리거가 등록됨. 재실행해도 중복
  // 등록되지 않도록 기존 동일 트리거를 먼저 정리함.
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === "checkHealthAndAlert") {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }
  ScriptApp.newTrigger("checkHealthAndAlert").timeBased().everyMinutes(10).create();
}
