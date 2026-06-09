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
