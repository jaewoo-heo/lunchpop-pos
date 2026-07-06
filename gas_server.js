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
}
