/**
 * 런치팝 GAS 백엔드 v2.4
 * 변경(v2.4): 공유 API 키 인증(과도기 모드), markDone rowIndex 상한 검증,
 *            매장명 완전일치, 전체 try/catch, onEdit 범위 체크 개선,
 *            address/deliveryCode 컬럼 매핑 수정, 죽은 gh_token 필드 제거
 */
function doGet(e) {
  try {
    var action = e.parameter.action;
    var storeName = String(e.parameter.storeName || "시스템").trim();
    var logMsg = e.parameter.logMsg;
    var apiKey = e.parameter.apiKey || "";

    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName("data");
    var logSheet = ss.getSheetByName("log");
    var versionSheet = ss.getSheetByName("버전관리");

    // ── 인증 설정 ──
    // 버전관리 시트:
    //   E2 = 공유 API 키 (비어있으면 인증 비활성 상태 = 기존 동작과 동일, 과도기 시작 전 기본값)
    //   F2 = 인증모드: "GRACE"(기본, 키 없어도 경고 로그만 남기고 통과) 또는 "STRICT"(키 불일치 시 차단)
    var configuredKey = versionSheet ? String(versionSheet.getRange("E2").getValue()).trim() : "";
    var authMode = versionSheet ? String(versionSheet.getRange("F2").getValue()).trim().toUpperCase() : "GRACE";
    if (authMode !== "STRICT") authMode = "GRACE";

    function checkAuth(actionName) {
      if (!configuredKey) return true; // 관리자가 아직 키를 설정하지 않음 → 인증 비활성(하위 호환)
      if (apiKey === configuredKey) return true;
      if (authMode === "GRACE") {
        // 매장당 6시간에 한 번만 경고 로그 (구버전 클라이언트가 매 폴링마다 로그를 도배하지 않도록)
        var cache = CacheService.getScriptCache();
        var cacheKey = "authwarn_" + storeName;
        if (!cache.get(cacheKey)) {
          cache.put(cacheKey, "1", 21600);
          if (logSheet) {
            var t = Utilities.formatDate(new Date(), "GMT+9", "yyyy/MM/dd HH:mm:ss");
            logSheet.appendRow([t, storeName, "[AUTH-WARN] 키 없음/불일치 - 구버전 클라이언트 추정 (action=" + actionName + ")"]);
          }
        }
        return true;
      }
      return false; // STRICT 모드에서 키 불일치 → 차단
    }

    // ── [액션 1] 업데이트 확인 (인증 불필요 — 최초 부팅 시 필요) ──
    // 버전관리 시트 구조: A2=버전번호, B2=다운로드 URL, C2=sha256 해시값
    if (action === "checkUpdate") {
      var vData = { version: "1.0", url: "", sha256: "" };
      if (versionSheet) {
        vData.version = String(versionSheet.getRange("A2").getValue());
        vData.url     = String(versionSheet.getRange("B2").getValue());
        vData.sha256  = String(versionSheet.getRange("C2").getValue());
      }
      return createJSON(vData);
    }

    // ── [액션 2] 매장 리스트 가져오기 (인증 불필요 — 최초 설정 화면에서 사용) ──
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
      if (!checkAuth("log")) return createJSON({ status: "error", msg: "unauthorized" });
      if (logSheet) {
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
      if (!checkAuth("markDone")) return createJSON({ status: "error", msg: "unauthorized" });
      if (!sheet) return createJSON({ status: "error", msg: "data 시트 없음" });

      var rowIndex = parseInt(e.parameter.rowIndex || "0", 10);
      var orderNo  = e.parameter.orderNo || "";
      var lastRowMD = sheet.getLastRow();
      var rowIndexValid = (!isNaN(rowIndex) && rowIndex >= 2 && rowIndex <= lastRowMD);

      function markByOrderNo() {
        if (lastRowMD < 2) return createJSON({ status: "not_found" });
        var allData = sheet.getRange(2, 8, lastRowMD - 1, 1).getValues();
        for (var i = 0; i < allData.length; i++) {
          if (String(allData[i][0]) === String(orderNo)) {
            sheet.getRange(i + 2, 12).setValue("TRUE");
            return createJSON({ status: "success" });
          }
        }
        return createJSON({ status: "not_found" });
      }

      if (rowIndexValid) {
        sheet.getRange(rowIndex, 12).setValue("TRUE");
        return createJSON({ status: "success" });
      } else if (orderNo) {
        // rowIndex가 없거나(구버전 클라이언트) 행 삭제/이동 등으로 더 이상 유효하지 않으면
        // orderNo 선형 탐색으로 폴백 (요청에 항상 둘 다 실려오므로 여기서 복구 가능)
        return markByOrderNo();
      } else if (rowIndex >= 2) {
        return createJSON({ status: "error", msg: "invalid rowIndex" });
      }

      return createJSON({ status: "error", msg: "rowIndex 또는 orderNo 필요" });
    }

    // ── [액션 5] 전체 주문 현황 가져오기 (신규 정식 버전) ──
    if (action === "fetchV2") {
      if (!checkAuth("fetchV2")) return createJSON({ status: "error", msg: "unauthorized" });
      if (!sheet) return createJSON([]);
      var lastRow2 = sheet.getLastRow();
      if (lastRow2 < 2) return createJSON([]);

      // P2셀(16열)의 날짜를 기준으로 필터 — 운영자가 수동 입력하는 서비스 날짜
      // P2가 비어있으면 날짜 필터 없이 전체 반환 (안전장치)
      var refDate = sheet.getRange(2, 16).getDisplayValue().trim();

      // 날짜 정규화: 숫자만 추출하여 비교 (구분자, 요일, 괄호 등 모두 무시)
      // 예) "2026.06.09.(화)" = "2026/06/09" = "2026-06-09" 모두 "20260609"으로 동일 처리
      var normDate = function(d) {
        return d.replace(/\D/g, '');
      };
      var refNorm = normDate(refDate);

      // 컬럼 매핑(A~L): 0=번호 1=이름 2=메뉴명 3=수량 4=상호명 5=주소(시트 열람용, 클라이언트 미전송)
      //               6=예약일자 7=주문번호 8=배달예정시간 9=배달코드("address"로 전송) 10=출력선택 11=인쇄완료
      var values = sheet.getRange(2, 1, lastRow2 - 1, 12).getDisplayValues();
      var result = [];

      for (var j = 0; j < values.length; j++) {
        var row = values[j];
        var dateMatch = (refDate === "") || (normDate(row[6]) === refNorm);
        // 매장명은 완전일치만 허용 (부분일치 시 "김밥"이 "옆집김밥"에도 매칭되는 문제 방지)
        // 앞뒤 공백 차이로 매장 전체 주문이 누락되지 않도록 양쪽 다 trim 후 비교
        if (String(row[4]).trim() === storeName && dateMatch) {
          result.push({
            rowIndex:     j + 2,          // markDone O(1)용 행 번호
            customerName: row[1],
            menuName:     row[2],
            quantity:     row[3],
            storeName:    row[4],
            address:      row[9],         // J열 배달코드 (의도된 매핑)
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
      if (!checkAuth("fetch")) return createJSON({ status: "error", msg: "unauthorized" });
      if (!sheet) return createJSON([]);
      var lastRow3 = sheet.getLastRow();
      if (lastRow3 < 2) return createJSON([]);

      var values2 = sheet.getRange(2, 1, lastRow3 - 1, 12).getDisplayValues();
      var result2 = [];

      for (var j2 = 0; j2 < values2.length; j2++) {
        var row2 = values2[j2];
        // 기존 방식: K열 TRUE이고 L열 비어있는 것만 전달, 매장명은 완전일치(trim 후 비교)
        if (String(row2[4]).trim() === storeName && row2[10] === "TRUE" && row2[11] !== "TRUE") {
          result2.push({
            customerName: row2[1], menuName: row2[2], quantity: row2[3],
            storeName:    row2[4], address: row2[9], resDate: row2[6],
            orderNo:      row2[7], deliveryTime: row2[8]
          });
        }
      }
      return createJSON(result2);
    }

    return createJSON({ status: "unknown_action", action: action });
  } catch (err) {
    return createJSON({ status: "error", msg: String(err) });
  }
}


function createJSON(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}


// ── [이벤트] P2 날짜 변경 시 K/L열 초기화 ──
// 단일 셀 수정뿐 아니라, P2를 포함하는 행/범위 붙여넣기 시에도 감지되도록 범위 겹침으로 판정
function onEdit(e) {
  try {
    var sheet = e.source.getActiveSheet();
    if (sheet.getName() !== "data") return;

    var r = e.range.getRow(), nr = e.range.getNumRows();
    var c = e.range.getColumn(), nc = e.range.getNumColumns();
    var rowsCoverRow2 = (r <= 2 && r + nr > 2);
    var colsCoverCol16 = (c <= 16 && c + nc > 16);

    if (rowsCoverRow2 && colsCoverCol16) {
      var lastRow = sheet.getLastRow();
      if (lastRow >= 2) {
        sheet.getRange(2, 11, lastRow - 1, 1).clearContent();
        sheet.getRange(2, 12, lastRow - 1, 1).clearContent();
      }
    }
  } catch (err) {
    // 트리거 실패는 사용자에게 보이지 않으므로 최소한 로그 시트에 흔적을 남김
    try {
      var logSheet = e.source.getSheetByName("log");
      if (logSheet) {
        var t = Utilities.formatDate(new Date(), "GMT+9", "yyyy/MM/dd HH:mm:ss");
        logSheet.appendRow([t, "시스템", "[ERR] onEdit 트리거 실패: " + String(err)]);
      }
    } catch (e2) { /* 로그 기록조차 실패하면 포기 */ }
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
