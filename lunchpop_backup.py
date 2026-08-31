"""
KitchenPic 백업 출력 프로그램 v1.0
- 매장 PC(Master)에 장애가 생겼을 때, 시설 관리실 PC에서 해당 매장의 미인쇄
  주문을 대신 조회 → 인쇄 → 인쇄완료 처리하기 위한 온디맨드 도구.
- 상시 실행 프로그램이 아님 (자동 폴링/시작프로그램 등록 없음, 문제 발생 시에만 수동 실행).
- 기존 GAS 액션(getStores/fetchV2/markDone)을 그대로 재사용하므로 서버 변경 불필요.
"""
import requests
import time
import re
import os
import sys
import json
import shutil
import threading
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
import win32print
import serial

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

BACKUP_VERSION = "1.0"
WEB_APP_URL = "https://script.google.com/macros/s/AKfycbzG_q6m1svwhZZny0DAz1s29qEGfVUO_gdnUOelX5QmIKPjTM8kvYjYhro_b7b_7w/exec"
# Master와 동일한 공유 인증 키. STRICT 모드에서도 이 도구가 계속 동작하도록 함.
API_KEY = "7d7bcc91e8ba535b5c7d43ed4b81c9865141ad2d15a52e17"

BASE_DIR = (os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
            else os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "lunchpop_backup_config.json")


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"printer": "기본 프린터"}


def save_config(data):
    """원자적 저장 - 임시 파일 쓰기 후 rename (다른 lunchpop_*.py와 동일한 패턴)"""
    tmp_file = CONFIG_FILE + ".tmp"
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        shutil.move(tmp_file, CONFIG_FILE)
    except Exception:
        try:
            os.remove(tmp_file)
        except Exception:
            pass


CONFIG = load_config()


def _sanitize(text):
    """제어문자 제거 — 주문 데이터에 섞인 제어문자가 ESC/POS 명령으로 해석되는 것을 방지."""
    return re.sub(r'[\x00-\x1f\x7f]', '', str(text))


def fetch_with_retry(url, params=None, retries=3, timeout=20):
    for i in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if i < retries - 1:
                time.sleep(2 ** i)
    return None


def send_mark_done(row_index, order_no, retries=3):
    """GAS는 논리적 실패도 HTTP 200으로 반환하므로 status 필드까지 확인해야 함."""
    resp = fetch_with_retry(WEB_APP_URL, params={
        "action": "markDone",
        "rowIndex": row_index,
        "orderNo": order_no,
        "apiKey": API_KEY
    }, retries=retries, timeout=10)
    if not resp:
        return False
    try:
        data = resp.json()
    except Exception:
        return False
    return isinstance(data, dict) and data.get("status") == "success"


def print_raw_text(receipt_bytes, printer):
    """프린터에 RAW 데이터 전송 (lunchpop_master.py의 print_raw_text와 동일한 로직)."""
    last_err = ""
    for attempt in range(3):
        try:
            if printer == "기본 프린터":
                p_name = win32print.GetDefaultPrinter()
                hPrinter = win32print.OpenPrinter(p_name)
                try:
                    win32print.StartDocPrinter(hPrinter, 1, ("KitchenPicBackupOrder", None, "RAW"))
                    win32print.StartPagePrinter(hPrinter)
                    win32print.WritePrinter(hPrinter, receipt_bytes)
                    win32print.EndPagePrinter(hPrinter)
                    win32print.EndDocPrinter(hPrinter)
                finally:
                    win32print.ClosePrinter(hPrinter)
                return True
            elif printer.startswith("COM"):
                with serial.Serial(printer, 9600, timeout=2) as ser:
                    ser.write(b'\x10\x04\x04')
                    status = ser.read(1)
                    if status and (ord(status) & 0x60) == 0x60:
                        return False
                    ser.write(receipt_bytes)
                    time.sleep(0.5)
                return True
            else:
                hPrinter = win32print.OpenPrinter(printer)
                try:
                    win32print.StartDocPrinter(hPrinter, 1, ("KitchenPicBackupOrder", None, "RAW"))
                    win32print.StartPagePrinter(hPrinter)
                    win32print.WritePrinter(hPrinter, receipt_bytes)
                    win32print.EndPagePrinter(hPrinter)
                    win32print.EndDocPrinter(hPrinter)
                finally:
                    win32print.ClosePrinter(hPrinter)
                return True
        except Exception as e:
            last_err = str(e)
            time.sleep(1)
    return False


def _build_test_bytes():
    CMD_INIT = b'\x1B\x40'
    CMD_ALIGN_CENTER = b'\x1B\x61\x01'
    CMD_CUT = b'\x1D\x56\x42\x00'
    return (CMD_INIT + CMD_ALIGN_CENTER +
            "\n[ KitchenPic 백업 출력 테스트 ]\n\n정상적으로 연결되었습니다.\n\n\n\n\n"
            .encode('cp949', errors='replace') + CMD_CUT)


def _build_receipt_bytes(order):
    """주문 하나를 영수증 바이트로 변환. 여러 매장을 한 PC에서 다루므로 매장명을
    전역 설정이 아닌 주문 데이터(order['storeName'])에서 가져오고, 정상 매장 PC가
    아닌 이 도구로 출력됐다는 것을 구분할 수 있도록 상단에 태그를 표시함."""
    CMD_ALIGN_CENTER = b'\x1B\x61\x01'
    CMD_ALIGN_LEFT = b'\x1B\x61\x00'
    CMD_SIZE_LARGE = b'\x1D\x21\x22'
    CMD_SIZE_NORMAL = b'\x1D\x21\x00'
    CMD_CUT = b'\x1D\x56\x42\x00'

    store_name = _sanitize(order.get('storeName', ''))
    customer_name = _sanitize(order.get('customerName', ''))
    menu_name = _sanitize(order.get('menuName', ''))
    quantity = _sanitize(order.get('quantity', ''))
    order_no = _sanitize(order.get('orderNo', ''))
    delivery_time = _sanitize(order.get('deliveryTime', ''))

    cook_time = ""
    if delivery_time:
        clean_str = delivery_time.replace("시 ", ":").replace("시", ":").replace("분", "")
        match = re.search(r'(\d{1,2}):\s?(\d{2})', clean_str)
        if match:
            h, m = int(match.group(1)), int(match.group(2))
            if "오후" in delivery_time and h < 12:
                h += 12
            elif "오전" in delivery_time and h == 12:
                h = 0
            dt = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0) - timedelta(minutes=30)
            cook_time = f"{dt.hour:02d}시 {dt.minute:02d}분"

    body = (
        CMD_ALIGN_CENTER + CMD_SIZE_LARGE +
        "KITCHENPIC\n\n".encode('cp949', errors='replace') +
        CMD_SIZE_NORMAL + CMD_ALIGN_LEFT +
        (f"[ 비상 백업출력 ]\n"
         f"------------------------------------------\n"
         f"[ {store_name} ]\n"
         f"------------------------------------------\n"
         f"주문자: {customer_name}\n"
         f"주문번호: {order_no}\n"
         f"배달예정: {delivery_time}\n"
         f"조리완료: {cook_time} (목표)\n"
         f"------------------------------------------\n").encode('cp949', errors='replace')
    )
    menu_info = f"{menu_name}   x{quantity}\n".encode('cp949', errors='replace')
    footer = (
        "\n------------------------------------------\n"
        f"관리번호: {order_no[-4:]}\n"
        "------------------------------------------\n"
    ).encode('cp949', errors='replace') + \
        CMD_ALIGN_CENTER + \
        "바쁜 일상이 좀 더 편해지도록, KitchenPic\n\n\n\n\n".encode('cp949', errors='replace') + \
        CMD_CUT

    return body + menu_info + footer


class BackupApp:
    def __init__(self):
        self.orders = []  # 현재 조회된, 선택 매장의 미인쇄 주문 목록
        self.root = ctk.CTk()
        self.root.title(f"KitchenPic 백업 출력 (v{BACKUP_VERSION})")
        self.root.geometry("580x540")
        self.root.attributes('-topmost', True)
        self._build_ui()
        self._load_stores()

    def _build_ui(self):
        header = ctk.CTkFrame(self.root, fg_color="#e74c3c", corner_radius=0, height=74)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(header, text="🍱  KitchenPic 백업 출력",
                     font=ctk.CTkFont("맑은 고딕", 18, "bold"),
                     text_color="white").pack(pady=(14, 0))
        ctk.CTkLabel(header, text="매장 PC에 문제가 생겼을 때 대신 인쇄하는 비상용 프로그램",
                     font=ctk.CTkFont("맑은 고딕", 10),
                     text_color="#fadbd8").pack()

        body = ctk.CTkFrame(self.root, fg_color="#f4f6f7", corner_radius=0)
        body.pack(fill="both", expand=True)

        row1 = ctk.CTkFrame(body, fg_color="transparent")
        row1.pack(fill="x", padx=16, pady=(16, 6))
        ctk.CTkLabel(row1, text="매장 선택", font=ctk.CTkFont("맑은 고딕", 11, "bold")).pack(side="left")
        self.store_combo = ttk.Combobox(row1, font=("맑은 고딕", 11), width=20, state="readonly")
        self.store_combo.pack(side="left", padx=(10, 10))
        self.fetch_btn = ctk.CTkButton(row1, text="미인쇄 주문 가져오기", width=160,
                                        fg_color="#e74c3c", hover_color="#c0392b",
                                        command=self.on_fetch)
        self.fetch_btn.pack(side="left")

        list_frame = ctk.CTkFrame(body, fg_color="white", border_width=1, border_color="#e8ecef")
        list_frame.pack(fill="both", expand=True, padx=16, pady=6)
        ctk.CTkLabel(list_frame, text="인쇄할 주문을 선택하세요 (기본: 전체 선택 / Ctrl·Shift+클릭으로 조정)",
                     font=ctk.CTkFont("맑은 고딕", 9),
                     text_color="#95a5a6").pack(anchor="w", padx=10, pady=(8, 2))
        self.listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED,
                                   font=("맑은 고딕", 10), activestyle="none")
        self.listbox.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        row2 = ctk.CTkFrame(body, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=(6, 4))
        ctk.CTkLabel(row2, text="프린터", font=ctk.CTkFont("맑은 고딕", 11, "bold")).pack(side="left")

        printer_list = ["기본 프린터"]
        try:
            installed = win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS, None, 2)
            printer_list += [p[2] for p in installed]
        except Exception:
            pass
        printer_list += [f"COM{i}" for i in range(1, 13)]

        self.printer_combo = ttk.Combobox(row2, font=("맑은 고딕", 11), width=20,
                                           values=printer_list, state="readonly")
        self.printer_combo.pack(side="left", padx=(10, 10))
        self.printer_combo.set(CONFIG.get("printer", "기본 프린터"))
        self.printer_combo.bind("<<ComboboxSelected>>", self.on_printer_change)

        self.test_btn = ctk.CTkButton(row2, text="테스트 인쇄", width=100,
                                       fg_color="#7f8c8d", hover_color="#636e72",
                                       command=self.on_test_print)
        self.test_btn.pack(side="left", padx=(0, 10))

        self.print_btn = ctk.CTkButton(row2, text="선택 항목 인쇄", width=140,
                                        fg_color="#27ae60", hover_color="#1e8449",
                                        command=self.on_print_selected, state="disabled")
        self.print_btn.pack(side="left")

        self.status_var = tk.StringVar(value="매장을 선택하고 [미인쇄 주문 가져오기]를 눌러주세요.")
        ctk.CTkLabel(body, textvariable=self.status_var,
                     font=ctk.CTkFont("맑은 고딕", 10),
                     text_color="#7f8c8d").pack(anchor="w", padx=18, pady=(0, 12))

    def on_printer_change(self, *_):
        CONFIG["printer"] = self.printer_combo.get().strip()
        save_config(CONFIG)

    def on_test_print(self):
        printer = self.printer_combo.get().strip()
        self.test_btn.configure(state="disabled", text="인쇄 중...")

        def worker():
            ok = print_raw_text(_build_test_bytes(), printer)
            def apply():
                self.test_btn.configure(state="normal", text="테스트 인쇄")
                if ok:
                    messagebox.showinfo("성공", "테스트 용지가 출력되었습니다.")
                else:
                    messagebox.showerror("실패", f"프린터 연결을 확인해주세요.\n현재 설정: {printer}")
            self.root.after(0, apply)
        threading.Thread(target=worker, daemon=True).start()

    def _load_stores(self):
        def worker():
            try:
                resp = requests.get(WEB_APP_URL, params={"action": "getStores"}, timeout=10)
                stores = resp.json() if resp.status_code == 200 else []
                if not isinstance(stores, list):
                    stores = []
            except Exception:
                stores = []

            def apply():
                self.store_combo["values"] = stores
                if stores:
                    self.store_combo.set(stores[0])
            self.root.after(0, apply)
        threading.Thread(target=worker, daemon=True).start()

    def on_fetch(self):
        store = self.store_combo.get().strip()
        if not store:
            messagebox.showwarning("경고", "매장을 선택해주세요.")
            return
        self.fetch_btn.configure(state="disabled", text="조회 중...")
        self.print_btn.configure(state="disabled")
        self.status_var.set(f"'{store}' 매장의 미인쇄 주문을 조회하는 중...")

        def worker():
            pending, err = None, ""
            try:
                resp = requests.get(WEB_APP_URL, params={
                    "action": "fetchV2", "storeName": store, "apiKey": API_KEY
                }, timeout=20)
                data = resp.json()
                if isinstance(data, list):
                    # 매장 PC의 자동인쇄 로직과 동일한 기준(출력선택=TRUE, 인쇄완료=FALSE)
                    pending = [o for o in data if isinstance(o, dict)
                               and o.get('isQueued') and not o.get('isPrinted')]
                else:
                    pending = []
            except Exception as e:
                err = str(e)

            def apply():
                self.fetch_btn.configure(state="normal", text="미인쇄 주문 가져오기")
                if pending is None:
                    messagebox.showerror("오류", f"주문 조회에 실패했습니다.\n\n{err}")
                    self.status_var.set("조회 실패 — 네트워크 상태를 확인해주세요.")
                    return
                self.orders = pending
                self._populate_list()
            self.root.after(0, apply)
        threading.Thread(target=worker, daemon=True).start()

    def _populate_list(self):
        self.listbox.delete(0, tk.END)
        for o in self.orders:
            label = (f"{o.get('orderNo', '')}  |  {o.get('customerName', '')}  |  "
                      f"{o.get('menuName', '')} x{o.get('quantity', '')}  |  "
                      f"{o.get('deliveryTime', '')}")
            self.listbox.insert(tk.END, label)
        if self.orders:
            self.listbox.select_set(0, tk.END)
            self.print_btn.configure(state="normal")
            self.status_var.set(f"미인쇄 주문 {len(self.orders)}건 — 전체 선택됨. 확인 후 인쇄하세요.")
        else:
            self.print_btn.configure(state="disabled")
            self.status_var.set("미인쇄 주문이 없습니다.")

    def on_print_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showwarning("경고", "인쇄할 주문을 선택해주세요.")
            return
        targets = [self.orders[i] for i in sel]
        if not messagebox.askyesno(
                "인쇄 확인",
                f"선택한 {len(targets)}건을 이 PC의 프린터로 인쇄합니다.\n"
                f"인쇄 완료로 처리되며, 이후 매장 PC에서는 다시 나타나지 않습니다.\n\n계속할까요?"):
            return
        self.print_btn.configure(state="disabled", text="인쇄 중...")
        self.fetch_btn.configure(state="disabled")

        def worker():
            printer = self.printer_combo.get().strip()
            ok_count = 0
            fail_list = []
            for o in targets:
                order_no = o.get('orderNo', '')
                try:
                    receipt = _build_receipt_bytes(o)
                    if not print_raw_text(receipt, printer):
                        fail_list.append(f"{order_no} — 인쇄 실패 (프린터 확인 필요)")
                        continue
                    if send_mark_done(o.get('rowIndex', ''), order_no):
                        ok_count += 1
                    else:
                        ok_count += 1
                        fail_list.append(f"{order_no} — 인쇄는 됐지만 완료 처리 실패 (관리자 확인 필요)")
                except Exception as e:
                    fail_list.append(f"{order_no} — 오류: {e}")

            def apply():
                self.print_btn.configure(state="normal", text="선택 항목 인쇄")
                self.fetch_btn.configure(state="normal")
                msg = f"{ok_count}건 인쇄 완료."
                if fail_list:
                    messagebox.showwarning("일부 문제 발생", msg + "\n\n" + "\n".join(fail_list))
                else:
                    messagebox.showinfo("완료", msg)
                self.status_var.set(msg)
                self.on_fetch()  # 처리된 건을 목록에서 빼기 위해 자동 재조회
            self.root.after(0, apply)
        threading.Thread(target=worker, daemon=True).start()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    BackupApp().run()
