import requests
import time
import win32print
from datetime import datetime, timedelta
import pystray
from PIL import Image
import threading
import sys
import os
import winsound
import re
import serial
import json
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
import customtkinter as ctk
import subprocess
import winreg
import shutil

# ==========================================
# [UI 전역 설정]
# ==========================================
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# ==========================================
# [설정] 버전 및 URL
# ==========================================
CURRENT_VERSION = 4.3
TARGET_EXE_NAME = "LunchPop_Master.exe"

WEB_APP_URL = "https://script.google.com/macros/s/AKfycbzG_q6m1svwhZZny0DAz1s29qEGfVUO_gdnUOelX5QmIKPjTM8kvYjYhro_b7b_7w/exec"
# GAS 백엔드 인증용 공유 키. 서버(버전관리 시트 E2)가 비어있으면 무시되고,
# 값이 설정되면 이 키를 실어 보내는 클라이언트만 통과(과도기: 키 없어도 일단 허용).
API_KEY = "7d7bcc91e8ba535b5c7d43ed4b81c9865141ad2d15a52e17"
CHECK_INTERVAL = 60

# ==========================================
# 경로 설정
# ==========================================
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "lunchpop_config.json")
LOCAL_LOG_FILE = os.path.join(BASE_DIR, "system_debug.log")
LOCAL_LOG_OLD = os.path.join(BASE_DIR, "system_debug.old.log")
TARGET_EXE_PATH = os.path.join(BASE_DIR, TARGET_EXE_NAME)
BACKUP_EXE_PATH = os.path.join(BASE_DIR, "LunchPop_Master.backup.exe")
PENDING_ACK_FILE = os.path.join(BASE_DIR, "pending_ack.json")


def resource_path(relative_path):
    """번들된 리소스 경로 반환 (PyInstaller _MEIPASS 우선)"""
    try:
        return os.path.join(sys._MEIPASS, relative_path)
    except Exception:
        return os.path.join(BASE_DIR, relative_path)


# alarm.wav: exe 옆에 있으면 그걸 우선 사용 (교체 가능), 없으면 번들본 사용
_alarm_override = os.path.join(BASE_DIR, "alarm.wav")
ALARM_FILE = _alarm_override if os.path.exists(_alarm_override) else resource_path("alarm.wav")


# ==========================================
# 전역 상태 (스레드 안전)
# ==========================================
orders_lock = threading.Lock()
_print_lock = threading.Lock()   # 동시 인쇄 방지 (폴링 자동인쇄 + UI 재출력 충돌 방지)
GLOBAL_ORDERS = []
printed_ids = set()          # 인쇄 완료 orderNo 세트 (서버 재응답 시 덮어쓰기 방지)
reprint_in_progress = set()  # 재출력 진행 중인 orderNo (중복 출력 방지)
alerted_ids = set()          # 알람 재생한 orderNo 세트 (매 60초 중복 알람 방지)
dashboard = None
PRINTER_SETTING = "기본 프린터"
MY_STORE_NAME = ""
consecutive_fail_count = 0
_polling_thread = None          # 폴링 스레드 레퍼런스 (watchdog용)


# ==========================================
# 시스템 유틸리티 및 로그
# ==========================================
def _rotate_log_if_needed():
    try:
        if os.path.exists(LOCAL_LOG_FILE) and os.path.getsize(LOCAL_LOG_FILE) > 5 * 1024 * 1024:
            if os.path.exists(LOCAL_LOG_OLD):
                os.remove(LOCAL_LOG_OLD)
            os.rename(LOCAL_LOG_FILE, LOCAL_LOG_OLD)
    except Exception:
        pass


def write_local_log(text):
    try:
        _rotate_log_if_needed()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOCAL_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{now}] {text}\n")
    except Exception:
        pass


def write_remote_log(text):
    write_local_log(text)
    store = CONFIG.get("storeName", "미설정매장")
    try:
        requests.get(WEB_APP_URL, params={"action": "log", "storeName": store, "logMsg": text, "apiKey": API_KEY}, timeout=10)
    except Exception as e:
        write_local_log(f"[ERR] 원격 로그 전송 실패: {e}")


def fetch_with_retry(url, params=None, retries=3, timeout=20):
    """GAS 요청 공통 헬퍼 - 지수 백오프 재시도"""
    for i in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.Timeout:
            write_local_log(f"[WARN] 요청 타임아웃 ({i + 1}/{retries})")
            if i < retries - 1:
                time.sleep(2 ** i)
        except requests.HTTPError as e:
            write_local_log(f"[ERR] HTTP 오류: {e}")
            break
        except requests.RequestException as e:
            write_local_log(f"[ERR] 네트워크 오류: {e}")
            if i < retries - 1:
                time.sleep(2 ** i)
    return None


def set_autostart_registry(enable=True):
    """자동 시작 등록 — Launcher가 있으면 Launcher 우선, 없으면 Master 직접 등록"""
    LAUNCHER_PATH = os.path.join(BASE_DIR, "LunchPop_Launcher.exe")
    target = LAUNCHER_PATH if os.path.exists(LAUNCHER_PATH) else TARGET_EXE_PATH
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        reg = winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER)
        key_obj = winreg.OpenKey(reg, key_path, 0, winreg.KEY_ALL_ACCESS)
        # 구버전 키 항상 정리 (enable 여부 무관)
        for old_name in ("LunchPopMaster",):
            try:
                winreg.DeleteValue(key_obj, old_name)
            except FileNotFoundError:
                pass
        if enable:
            winreg.SetValueEx(key_obj, "LunchPopAlrimi", 0, winreg.REG_SZ, f'"{target}"')
        else:
            try:
                winreg.DeleteValue(key_obj, "LunchPopAlrimi")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key_obj)
    except Exception as e:
        write_local_log(f"[ERR] 레지스트리 오류: {e}")


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            write_local_log(f"[ERR] 설정 로드 실패: {e}")
    return {"storeName": "", "printer": "기본 프린터", "autostart": True, "dash_x": None, "dash_y": None}


def save_config(config_data):
    """원자적 저장 - 임시 파일 쓰기 후 rename"""
    tmp_file = CONFIG_FILE + ".tmp"
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        shutil.move(tmp_file, CONFIG_FILE)
        return True
    except Exception as e:
        write_local_log(f"[ERR] 설정 저장 실패: {e}")
        try:
            os.remove(tmp_file)
        except Exception:
            pass
        return False


CONFIG = load_config()


# ==========================================
# markDone 실패 재시도 큐 (재시작 후 중복 인쇄 방지)
# ==========================================
pending_ack_lock = threading.Lock()


def load_pending_ack():
    try:
        if os.path.exists(PENDING_ACK_FILE):
            with open(PENDING_ACK_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        write_local_log(f"[ERR] pending_ack 로드 실패: {e}")
    return []


def save_pending_ack(items):
    try:
        with open(PENDING_ACK_FILE, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False)
    except Exception as e:
        write_local_log(f"[ERR] pending_ack 저장 실패: {e}")


pending_ack = load_pending_ack()


def queue_pending_ack(row_index, order_no):
    with pending_ack_lock:
        pending_ack.append({"rowIndex": row_index, "orderNo": order_no})
        save_pending_ack(pending_ack)


def send_mark_done(row_index, order_no, retries=3):
    """markDone 요청 후 실제 status=='success'까지 확인해야 성공으로 판정.
    GAS는 unauthorized/invalid rowIndex 등 논리적 실패도 HTTP 200으로 반환하므로
    resp가 truthy(HTTP 2xx)라는 것만으로는 서버가 실제로 처리했는지 알 수 없음."""
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


def flush_pending_ack():
    """이전에 실패한 markDone 재시도. 재시작 직후 fetchV2보다 먼저 호출되어야
    서버에 인쇄완료가 반영된 뒤 주문 목록을 받아 중복 인쇄를 막을 수 있음."""
    global pending_ack
    with pending_ack_lock:
        if not pending_ack:
            return
        remaining = []
        for item in pending_ack:
            if send_mark_done(item.get('rowIndex', ''), item.get('orderNo', ''), retries=1):
                write_local_log(f"[INFO] 지연된 markDone 재전송 성공: {item.get('orderNo', '')}")
            else:
                remaining.append(item)
        pending_ack = remaining
        save_pending_ack(pending_ack)


# ==========================================
# [1] 초기 설정 마법사 UI
# ==========================================
def _set_icon(window):
    """모든 창에 로고 아이콘 적용 (CTk는 초기화 후 덮어쓰므로 200ms 지연)"""
    def _apply():
        try:
            window.iconbitmap(resource_path("logo.ico"))
        except Exception:
            pass
    try:
        window.after(200, _apply)
    except Exception:
        pass


def _center_window(win):
    """창을 화면 중앙에 배치 (콘텐츠 크기에 맞게 자동 조정)"""
    win.update_idletasks()
    w = win.winfo_reqwidth()
    h = win.winfo_reqheight()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    x = (sw - w) // 2
    y = (sh - h) // 3
    win.geometry(f"{w}x{h}+{x}+{y}")


def select_store_ui():
    global PRINTER_SETTING, MY_STORE_NAME

    root = ctk.CTk()
    root.title(f"런치팝 초기 설정 (v{CURRENT_VERSION})")
    root.attributes('-topmost', True)
    root.resizable(False, False)
    _set_icon(root)

    # ttk.Combobox 스타일
    _style = ttk.Style(root)
    _style.theme_use("default")
    _style.configure("LP.TCombobox",
        fieldbackground="white", background="#e74c3c",
        foreground="#2c3e50", selectbackground="#fdecea",
        selectforeground="#2c3e50", arrowcolor="white",
        bordercolor="#dce1e7", lightcolor="#dce1e7", darkcolor="#dce1e7",
        arrowsize=20, padding=(12, 10), font=("맑은 고딕", 11))
    _style.map("LP.TCombobox",
        background=[("active", "#c0392b"), ("!active", "#e74c3c")],
        fieldbackground=[("readonly", "white")],
        foreground=[("readonly", "#2c3e50")])
    root.option_add("*TCombobox*Listbox.font", ("맑은 고딕", 11))
    root.option_add("*TCombobox*Listbox.background", "white")
    root.option_add("*TCombobox*Listbox.foreground", "#2c3e50")
    root.option_add("*TCombobox*Listbox.selectBackground", "#fdecea")
    root.option_add("*TCombobox*Listbox.selectForeground", "#2c3e50")

    def on_close():
        if messagebox.askyesno("종료 확인", "설정을 완료하지 않으면 프로그램이 종료됩니다.\n종료하시겠습니까?"):
            root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)

    # 헤더
    header = ctk.CTkFrame(root, fg_color="#e74c3c", corner_radius=0, height=90)
    header.pack(fill="x")
    header.pack_propagate(False)
    ctk.CTkLabel(header, text="🍱  런치팝 POS",
                  font=ctk.CTkFont("맑은 고딕", 22, "bold"),
                  text_color="white").pack(pady=(18, 0))
    ctk.CTkLabel(header, text=f"v{CURRENT_VERSION}  —  매장 초기 설정",
                  font=ctk.CTkFont("맑은 고딕", 10),
                  text_color="#fadbd8").pack()

    # 배경 프레임
    bg = ctk.CTkFrame(root, fg_color="#f4f6f7", corner_radius=0)
    bg.pack(fill="both", expand=True)

    # 카드
    card = ctk.CTkFrame(bg, corner_radius=16, fg_color="white",
                         border_width=1, border_color="#e8ecef")
    card.pack(fill="both", expand=True, padx=24, pady=20)

    # 매장명
    ctk.CTkLabel(card, text="매장명 선택",
                  font=ctk.CTkFont("맑은 고딕", 12, "bold"),
                  text_color="#2c3e50").pack(anchor="w", padx=20, pady=(20, 2))
    ctk.CTkLabel(card, text="운영 중인 매장명을 선택해주세요",
                  font=ctk.CTkFont("맑은 고딕", 10),
                  text_color="#95a5a6").pack(anchor="w", padx=20)

    store_combo = ttk.Combobox(card, style="LP.TCombobox",
                                font=("맑은 고딕", 11), width=32, state="normal")
    store_combo.pack(padx=20, pady=(8, 0), anchor="w")
    store_combo.set("로딩 중...")

    ctk.CTkFrame(card, height=1, fg_color="#f0f0f0").pack(fill="x", padx=20, pady=16)

    # 프린터
    ctk.CTkLabel(card, text="프린터 선택",
                  font=ctk.CTkFont("맑은 고딕", 12, "bold"),
                  text_color="#2c3e50").pack(anchor="w", padx=20, pady=(0, 2))
    ctk.CTkLabel(card, text="영수증 프린터 연결 방식을 선택해주세요",
                  font=ctk.CTkFont("맑은 고딕", 10),
                  text_color="#95a5a6").pack(anchor="w", padx=20)

    printer_list = ["기본 프린터"]
    try:
        installed = win32print.EnumPrinters(
            win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS, None, 2)
        printer_list += [p[2] for p in installed]
    except Exception:
        pass
    printer_list += [f"COM{i}" for i in range(1, 13)]

    printer_combo = ttk.Combobox(card, style="LP.TCombobox",
                                  font=("맑은 고딕", 11), width=32,
                                  values=printer_list, state="readonly")
    printer_combo.pack(padx=20, pady=(8, 0), anchor="w")
    printer_combo.set(CONFIG.get("printer", "기본 프린터"))

    ctk.CTkFrame(card, height=1, fg_color="#f0f0f0").pack(fill="x", padx=20, pady=16)

    autostart_var = tk.BooleanVar(value=True)
    ctk.CTkCheckBox(card, text="윈도우 시작 시 자동 실행 (권장)",
                     variable=autostart_var,
                     font=ctk.CTkFont("맑은 고딕", 11),
                     checkmark_color="white", fg_color="#e74c3c",
                     hover_color="#c0392b").pack(anchor="w", padx=20)

    confirm_btn = ctk.CTkButton(card, text="설정 완료 및 시작  →",
                                  font=ctk.CTkFont("맑은 고딕", 13, "bold"),
                                  fg_color="#bdc3c7", hover_color="#95a5a6",
                                  height=46, corner_radius=10, state="disabled")
    confirm_btn.pack(fill="x", padx=20, pady=(16, 20))

    def on_store_change(*_):
        val = store_combo.get().strip()
        if val and val not in ("로딩 중...", ""):
            confirm_btn.configure(state="normal", fg_color="#e74c3c", hover_color="#c0392b")
        else:
            confirm_btn.configure(state="disabled", fg_color="#bdc3c7", hover_color="#95a5a6")

    store_combo.bind("<<ComboboxSelected>>", on_store_change)
    store_combo.bind("<KeyRelease>", on_store_change)

    def load_stores():
        try:
            resp = requests.get(f"{WEB_APP_URL}?action=getStores", timeout=10)
            if resp.status_code == 200:
                stores = resp.json()
                def _apply_stores():
                    store_combo["values"] = stores
                    if stores:
                        store_combo.set(stores[0])
                    on_store_change()
                root.after(0, _apply_stores)
                return
        except Exception as e:
            write_local_log(f"[ERR] 매장 목록 로드 실패: {e}")
        fallback = ["덮밥천재", "직접입력"]
        def _apply_fallback():
            store_combo["values"] = fallback
            store_combo.set(fallback[0])
            on_store_change()
        root.after(0, _apply_fallback)

    threading.Thread(target=load_stores, daemon=True).start()

    def on_confirm():
        global PRINTER_SETTING, MY_STORE_NAME
        MY_STORE_NAME = store_combo.get().strip()
        if not MY_STORE_NAME:
            messagebox.showwarning("경고", "매장명을 선택해주세요.")
            return
        PRINTER_SETTING = printer_combo.get().strip()
        CONFIG["storeName"] = MY_STORE_NAME
        CONFIG["printer"] = PRINTER_SETTING
        CONFIG["autostart"] = autostart_var.get()
        if not save_config(CONFIG):
            messagebox.showerror("오류", "설정 저장에 실패했습니다.\n디스크 용량을 확인해주세요.")
            return
        set_autostart_registry(autostart_var.get())
        root.destroy()

    confirm_btn.configure(command=on_confirm)
    _center_window(root)
    root.mainloop()


# ==========================================
# [2] 인쇄 핵심 로직
# ==========================================
def print_raw_text(receipt_bytes, printer_override=None):
    """프린터에 RAW 데이터 전송. _print_lock으로 동시 인쇄 방지.
    printer_override 지정 시 전역 PRINTER_SETTING을 건드리지 않고 해당 프린터로만 시도
    (테스트 출력 중 실제 주문이 잘못된 프린터로 나가는 레이스 컨디션 방지)."""
    last_err = ""
    printer = printer_override if printer_override is not None else PRINTER_SETTING
    with _print_lock:
        for attempt in range(3):
            try:
                if printer == "기본 프린터":
                    p_name = win32print.GetDefaultPrinter()
                    hPrinter = win32print.OpenPrinter(p_name)
                    try:
                        win32print.StartDocPrinter(hPrinter, 1, ("LunchPopOrder", None, "RAW"))
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
                            write_remote_log(f"[WARN] {printer} 용지 없음!")
                            return False
                        ser.write(receipt_bytes)
                        time.sleep(0.5)
                    return True
                else:
                    hPrinter = win32print.OpenPrinter(printer)
                    try:
                        win32print.StartDocPrinter(hPrinter, 1, ("LunchPopOrder", None, "RAW"))
                        win32print.StartPagePrinter(hPrinter)
                        win32print.WritePrinter(hPrinter, receipt_bytes)
                        win32print.EndPagePrinter(hPrinter)
                        win32print.EndDocPrinter(hPrinter)
                    finally:
                        win32print.ClosePrinter(hPrinter)
                    return True
            except Exception as e:
                last_err = str(e)
                write_local_log(f"[WARN] 인쇄 시도 {attempt + 1}/3 실패: {e}")
                time.sleep(1)
        write_remote_log(f"[ERR] PRINT FATAL ({printer}): {last_err}")
        return False


def process_test_print(printer_override=None):
    CMD_INIT = b'\x1B\x40'
    CMD_ALIGN_CENTER = b'\x1B\x61\x01'
    CMD_CUT = b'\x1D\x56\x42\x00'
    test_data = (CMD_INIT + CMD_ALIGN_CENTER +
                 "\n[ 런치팝 프린터 테스트 ]\n\n정상적으로 연결되었습니다.\n\n바쁜 일상이 좀 더 편해지도록, 런치팝\n\n\n\n\n"
                 .encode('cp949', errors='replace') + CMD_CUT)
    if print_raw_text(test_data, printer_override):
        messagebox.showinfo("성공", "테스트 용지가 출력되었습니다.")
    else:
        messagebox.showerror("실패", f"프린터 연결을 확인해주세요.\n현재 설정: {printer_override or PRINTER_SETTING}")


def _sanitize(text):
    """제어문자 제거 — 주문 데이터에 섞인 제어문자가 ESC/POS 명령으로
    해석되어 캐시서랍 오픈/용지 낭비 등을 일으키는 것을 방지."""
    return re.sub(r'[\x00-\x1f\x7f]', '', str(text))


def _build_receipt_bytes(order, is_reprint=False):
    CMD_ALIGN_CENTER = b'\x1B\x61\x01'
    CMD_ALIGN_LEFT = b'\x1B\x61\x00'
    CMD_SIZE_LARGE = b'\x1D\x21\x22'
    CMD_SIZE_NORMAL = b'\x1D\x21\x00'
    CMD_CUT = b'\x1D\x56\x42\x00'

    customer_name = _sanitize(order.get('customerName', ''))
    menu_name = _sanitize(order.get('menuName', ''))
    quantity = _sanitize(order.get('quantity', ''))
    order_no = _sanitize(order.get('orderNo', ''))
    delivery_time = _sanitize(order.get('deliveryTime', ''))

    reprint_tag = "[ 재출력 ]\n" if is_reprint else ""
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

    # 인코딩 불가 문자(이모지 등)는 실패 대신 '?'로 대체 — 특정 주문이 영구히
    # 인쇄 불가능해지는 것(CP949 인코딩 예외로 매 폴링마다 실패 반복)을 방지
    body = (
        CMD_ALIGN_CENTER + CMD_SIZE_LARGE +
        "LUNCH POP\n\n".encode('cp949', errors='replace') +
        CMD_SIZE_NORMAL + CMD_ALIGN_LEFT +
        (f"{reprint_tag}"
         f"------------------------------------------\n"
         f"[ {MY_STORE_NAME} ]\n"
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
        "바쁜 일상이 좀 더 편해지도록, 런치팝\n\n\n\n\n".encode('cp949', errors='replace') + \
        CMD_CUT

    return body + menu_info + footer


def process_print(order, is_reprint=False):
    ono = order.get('orderNo', '') if isinstance(order, dict) else ''
    try:
        tag = "[재출력]" if is_reprint else "[인쇄]"
        write_remote_log(f"{tag} 시작: {ono} ({order.get('customerName', '')})")

        receipt_bytes = _build_receipt_bytes(order, is_reprint)
        if print_raw_text(receipt_bytes):
            # 재출력이라도 서버에 아직 "인쇄완료"로 기록되지 않은 주문(자동인쇄 실패 후
            # 주문리스트에서 수동으로 재출력하는 경우)은 사실상 첫 성공 인쇄이므로
            # markDone을 보내야 함. 이미 인쇄완료된 주문의 순수 사본 재출력만 건너뜀.
            # (건너뛰지 않으면: 다음 폴링에서 서버가 여전히 미인쇄로 응답 → 자동으로 또 인쇄됨)
            should_ack = (not is_reprint) or (not order.get('isPrinted', False))
            if should_ack:
                if not send_mark_done(order.get('rowIndex', ''), ono):
                    write_local_log(f"[WARN] markDone 전송 실패 (재시도 큐 등록): {ono}")
                    queue_pending_ack(order.get('rowIndex', ''), ono)
                if ono:  # 빈 문자열은 추가 금지 (전체 주문 차단 버그 방지)
                    with orders_lock:
                        printed_ids.add(ono)
            write_remote_log(f"{tag} 완료: {ono}")
            return True
        else:
            write_remote_log(f"{tag} 실패: {ono} (프린터 오류)")
            if dashboard:
                dashboard.root.after(0, lambda o=order: dashboard.show_print_error(o))
            return False
    except Exception as e:
        write_remote_log(f"[ERR] process_print: {ono} / {e}")
        if dashboard:
            dashboard.root.after(0, lambda o=order: dashboard.show_print_error(o))
        return False


# ==========================================
# [3] 스케줄러 (폴링)
# ==========================================


def check_printer_online():
    """프린터 온라인 상태 사전 확인"""
    try:
        if PRINTER_SETTING.startswith("COM"):
            with serial.Serial(PRINTER_SETTING, 9600, timeout=1):
                return True
        else:
            p_name = win32print.GetDefaultPrinter() if PRINTER_SETTING == "기본 프린터" else PRINTER_SETTING
            hPrinter = win32print.OpenPrinter(p_name)
            try:
                info = win32print.GetPrinter(hPrinter, 2)
            finally:
                win32print.ClosePrinter(hPrinter)
            # Status 0 = 정상, 그 외 = 오프라인/오류
            return info['Status'] == 0
    except Exception:
        return False


def run_auto_printer():
    global GLOBAL_ORDERS, dashboard, consecutive_fail_count, alerted_ids

    write_remote_log(f"[INFO] CONNECTED v{CURRENT_VERSION} ({PRINTER_SETTING})")
    last_cleanup_date = ""
    heartbeat_counter = 0
    printer_ok = True          # 프린터 이전 상태 (변경 시에만 알림)

    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            # 프린터 사전 상태 체크 (상태 변경 시에만 대시보드 업데이트)
            current_printer_ok = check_printer_online()
            if current_printer_ok != printer_ok:
                printer_ok = current_printer_ok
                if dashboard:
                    if not printer_ok:
                        dashboard.root.after(0, lambda: dashboard.show_print_error(
                            {"orderNo": "0000", "customerName": "프린터 오프라인 — 연결을 확인하세요"}))
                        write_local_log(f"[WARN] 프린터 오프라인 감지: {PRINTER_SETTING}")
                    else:
                        dashboard.root.after(0, dashboard.dismiss_error)
                        write_local_log(f"[INFO] 프린터 온라인 복구: {PRINTER_SETTING}")

            # 새벽 4시 - 인쇄 완료된 주문만 정리 (미완료 주문 보존)
            if now.hour == 4 and last_cleanup_date != today_str:
                with orders_lock:
                    GLOBAL_ORDERS = [o for o in GLOBAL_ORDERS if not o.get('isPrinted')]
                    printed_ids.clear()
                last_cleanup_date = today_str
                write_remote_log("[INFO] 새벽 메모리 정리 완료")

            # 이전에 실패한 markDone 재전송 (fetchV2보다 먼저 — 재시작 시 중복 인쇄 방지)
            flush_pending_ack()

            resp = fetch_with_retry(WEB_APP_URL, params={
                "action": "fetchV2",
                "storeName": MY_STORE_NAME,
                "apiKey": API_KEY
            }, retries=3, timeout=20)

            if resp:
                try:
                    server_data = resp.json()
                except Exception as e:
                    write_local_log(f"[ERR] 응답 파싱 실패: {e}")
                    server_data = None

                if isinstance(server_data, list):
                    consecutive_fail_count = 0
                    if dashboard:
                        dashboard.root.after(0, lambda: dashboard.update_sync_status(True))

                    with orders_lock:
                        for o in server_data:
                            ono = o.get('orderNo', '')
                            if not ono:
                                continue  # orderNo 없는 주문은 추적 제외
                            if ono in printed_ids:
                                o['isPrinted'] = True
                            elif o.get('isPrinted'):
                                printed_ids.add(ono)
                        GLOBAL_ORDERS = server_data

                    with orders_lock:
                        pending = [o for o in GLOBAL_ORDERS if o.get('isQueued') and not o.get('isPrinted')]
                        total = len(GLOBAL_ORDERS)
                        done_count = len([o for o in GLOBAL_ORDERS if o.get('isPrinted')])

                    if dashboard:
                        t, d, p = total, done_count, len(pending)
                        dashboard.root.after(0, lambda _t=t, _d=d, _p=p: dashboard.update_counts(_t, _d, _p))

                    # 알람: 이전에 알람 안 울린 신규 대기 주문이 있을 때만 재생
                    pending_ids = {o.get('orderNo', '') for o in pending}
                    new_alerts = pending_ids - alerted_ids
                    if new_alerts and os.path.exists(ALARM_FILE):
                        try:
                            winsound.PlaySound(ALARM_FILE, winsound.SND_FILENAME | winsound.SND_ASYNC)
                        except Exception as e:
                            write_local_log(f"[WARN] 알람 재생 실패: {e}")
                        alerted_ids.update(pending_ids)
                    # 완료된 주문은 alerted_ids에서 제거 (재입고 등 대비)
                    current_ids = {o.get('orderNo', '') for o in GLOBAL_ORDERS}
                    alerted_ids &= current_ids

                    for o in pending:
                        if process_print(o):
                            with orders_lock:
                                o['isPrinted'] = True
                else:
                    # 서버가 200을 반환했지만 예상한 목록 형식이 아님 (할당량 초과 등) — 실패로 취급
                    consecutive_fail_count += 1
                    write_local_log(f"[WARN] fetchV2 응답 형식 이상: {server_data}")
                    if dashboard:
                        c = consecutive_fail_count
                        dashboard.root.after(0, lambda _c=c: dashboard.update_sync_status(False, _c))
            else:
                consecutive_fail_count += 1
                if dashboard:
                    c = consecutive_fail_count
                    dashboard.root.after(0, lambda _c=c: dashboard.update_sync_status(False, _c))

            heartbeat_counter += 1
            if heartbeat_counter >= (3600 // CHECK_INTERVAL):
                write_remote_log(f"[INFO] Heartbeat - 정상 가동 중 (연속실패: {consecutive_fail_count})")
                heartbeat_counter = 0

        except Exception as e:
            write_local_log(f"[ERR] 폴링 루프 오류: {e}")
            if dashboard:
                dashboard.root.after(0, lambda: dashboard.update_sync_status(False))

        time.sleep(CHECK_INTERVAL)


def run_thread_watchdog():
    """폴링 스레드가 죽으면 자동 재시작하는 감시자"""
    global _polling_thread
    time.sleep(120)  # 초기 기동 여유 시간
    while True:
        try:
            if _polling_thread is not None and not _polling_thread.is_alive():
                write_local_log("[WARN] 폴링 스레드 비정상 종료 감지 — 재시작 시도")
                write_remote_log("[WARN] 폴링 스레드 재시작")
                _polling_thread = threading.Thread(target=run_auto_printer, daemon=True)
                _polling_thread.start()
                write_local_log("[INFO] 폴링 스레드 재시작 완료")
                if dashboard:
                    dashboard.root.after(0, lambda: dashboard.update_sync_status(False, 0))
        except Exception as e:
            write_local_log(f"[ERR] watchdog 오류: {e}")
        time.sleep(120)


# ==========================================
# [4] 스마트 대시보드 UI
# ==========================================
class SmartDashboard:
    DASH_BG    = "#1a1a2e"
    PENDING_BG = "#c0392b"
    BTN_BG     = "#16213e"
    BTN_HOVER  = "#0f3460"

    def __init__(self):
        self.root = ctk.CTk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.base_w = 680
        self.base_h = 80
        self._has_error = False
        self.list_win = None
        self.scroll_frame = None
        self._list_above = False  # 리스트가 대시보드 위에 붙어있는지 여부

        # 위치 복원 (화면 밖이면 기본 위치로)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        pos_x, pos_y = CONFIG.get("dash_x"), CONFIG.get("dash_y")
        if (pos_x is not None and
                0 <= pos_x <= sw - self.base_w and
                0 <= pos_y <= sh - self.base_h):
            self.root.geometry(f"{self.base_w}x{self.base_h}+{pos_x}+{pos_y}")
        else:
            self.root.geometry(f"{self.base_w}x{self.base_h}+{sw - self.base_w - 10}+{sh - self.base_h - 50}")

        self.root.configure(fg_color=self.DASH_BG)

        # 드래그 바인딩
        self.root.bind("<Button-1>", self.start_move)
        self.root.bind("<B1-Motion>", self.do_move)
        self.root.bind("<ButtonRelease-1>", self.save_pos)

        # ── 메인 프레임 ──
        self.main_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=14, pady=10)

        # 왼쪽: 매장명 + 카운트
        self.left_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.left_frame.pack(side="left", fill="both", expand=True)

        self.store_label = ctk.CTkLabel(
            self.left_frame, text=f"[ {MY_STORE_NAME} ]",
            font=ctk.CTkFont("맑은 고딕", 9), text_color="#7f8c8d")
        self.store_label.pack(anchor="w")

        self.info_label = ctk.CTkLabel(
            self.left_frame, text="서버 연결 중...",
            font=ctk.CTkFont("맑은 고딕", 13, "bold"), text_color="white")
        self.info_label.pack(anchor="w")

        # 오른쪽: 상태 + 버튼
        self.right_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.right_frame.pack(side="right")

        self.sync_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.sync_frame.pack(anchor="e", pady=(0, 4))

        self.sync_dot = ctk.CTkLabel(
            self.sync_frame, text="●",
            font=ctk.CTkFont("Arial", 9), text_color="#2ecc71")
        self.sync_dot.pack(side="left")
        self.sync_label = ctk.CTkLabel(
            self.sync_frame, text="연결 중",
            font=ctk.CTkFont("맑은 고딕", 9), text_color="#7f8c8d")
        self.sync_label.pack(side="left", padx=3)

        self.btn_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.btn_frame.pack(anchor="e")

        self.btn_list = ctk.CTkButton(
            self.btn_frame, text="주문리스트 ▼", width=114, height=28,
            font=ctk.CTkFont("맑은 고딕", 10, "bold"),
            fg_color=self.BTN_BG, hover_color=self.BTN_HOVER,
            corner_radius=8, command=self.toggle_list)
        self.btn_list.pack(side="left", padx=2)

        ctk.CTkButton(
            self.btn_frame, text="설정", width=52, height=28,
            font=ctk.CTkFont("맑은 고딕", 10),
            fg_color=self.BTN_BG, hover_color=self.BTN_HOVER,
            corner_radius=8, command=self.open_settings).pack(side="left", padx=2)

        ctk.CTkButton(
            self.btn_frame, text="─", width=32, height=28,
            font=ctk.CTkFont("Arial", 10),
            fg_color=self.BTN_BG, hover_color=self.BTN_HOVER,
            corner_radius=8, command=lambda: self.root.withdraw()).pack(side="left", padx=2)

        # ── 에러 배너 (기본 숨김) ──
        self.error_frame = ctk.CTkFrame(self.root, fg_color="#e74c3c", corner_radius=0, height=36)
        self.error_label = ctk.CTkLabel(
            self.error_frame, text="",
            font=ctk.CTkFont("맑은 고딕", 10, "bold"), text_color="white")
        self.error_label.pack(side="left", padx=10, pady=6)
        ctk.CTkButton(
            self.error_frame, text="확인", width=50, height=24,
            font=ctk.CTkFont("맑은 고딕", 9),
            fg_color="#c0392b", hover_color="#a93226",
            corner_radius=6, command=self.dismiss_error).pack(side="right", padx=8)

    def update_sync_status(self, success, fail_count=0):
        if success:
            self.sync_dot.configure(text_color="#2ecc71")
            self.sync_label.configure(
                text=datetime.now().strftime("%H:%M"), text_color="#7f8c8d")
        else:
            self.sync_dot.configure(text_color="#e74c3c")
            if fail_count >= 3:
                self.sync_label.configure(text="서버 오류", text_color="#e74c3c")
            else:
                self.sync_label.configure(text="연결 중...", text_color="#e67e22")

    def update_counts(self, total, done, pending):
        self.info_label.configure(
            text=f"오늘 {total}건  |  완료 {done}  |  대기 {pending}",
            text_color="#ff6b6b" if pending > 0 else "white"
        )
        if pending > 0:
            self.root.configure(fg_color=self.PENDING_BG)
        else:
            self.root.configure(fg_color=self.DASH_BG)
            self.store_label.configure(text_color="#7f8c8d")

    def show_print_error(self, order):
        self._has_error = True
        order_no = order.get('orderNo', '')[-4:]
        name = order.get('customerName', '')
        self.error_label.configure(
            text=f"  인쇄 실패  [{order_no}] {name}  —  주문리스트에서 재출력하세요")
        self.error_frame.pack(fill="x", side="bottom")
        self.root.geometry(
            f"{self.base_w}x{self.base_h + 36}+{self.root.winfo_x()}+{self.root.winfo_y()}")
        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass

    def dismiss_error(self):
        self._has_error = False
        self.error_frame.pack_forget()
        self.root.geometry(
            f"{self.base_w}x{self.base_h}+{self.root.winfo_x()}+{self.root.winfo_y()}")

    def start_move(self, e):
        self._x, self._y = e.x, e.y

    def do_move(self, e):
        nx = self.root.winfo_x() + (e.x - self._x)
        ny = self.root.winfo_y() + (e.y - self._y)
        self.root.geometry(f"+{nx}+{ny}")
        # 리스트 창이 열려 있으면 대시보드에 붙어서 같이 이동 (위/아래 실시간 재계산)
        if self.list_win and self.list_win.winfo_exists():
            lh = self.list_win.winfo_height()
            sh = self.root.winfo_screenheight()
            dash_h = self.root.winfo_height()  # 에러 배너 포함 실제 높이
            self._list_above = (ny + dash_h + lh > sh)
            ly = ny - lh if self._list_above else ny + dash_h
            self.list_win.geometry(f"+{nx}+{ly}")

    def save_pos(self, _e):
        CONFIG["dash_x"] = self.root.winfo_x()
        CONFIG["dash_y"] = self.root.winfo_y()
        save_config(CONFIG)

    def restore_from_tray(self):
        self.root.deiconify()
        self.root.attributes("-topmost", True)

    def open_settings(self):
        win = ctk.CTkToplevel(self.root)
        win.title("런치팝 설정")
        win.attributes("-topmost", True)
        win.grab_set()
        win.resizable(False, False)
        win.configure(fg_color="white")
        _set_icon(win)

        W, H = 370, 530
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 3}")

        # ttk.Combobox 스타일 (설정창은 root와 같은 Tk 인스턴스 공유 → theme_use 중복 방지)
        _style = ttk.Style(self.root)
        if "LP.TCombobox" not in _style.theme_names() or \
                not _style.lookup("LP.TCombobox", "background"):
            _style.configure("LP.TCombobox",
                fieldbackground="white", background="#e74c3c",
                foreground="#2c3e50", selectbackground="#fdecea",
                selectforeground="#2c3e50", arrowcolor="white",
                bordercolor="#dce1e7", lightcolor="#dce1e7", darkcolor="#dce1e7",
                arrowsize=20, padding=(12, 10), font=("맑은 고딕", 11))
            _style.map("LP.TCombobox",
                background=[("active", "#c0392b"), ("!active", "#e74c3c")],
                fieldbackground=[("readonly", "white")],
                foreground=[("readonly", "#2c3e50")])

        # ── 헤더 ──
        header = ctk.CTkFrame(win, fg_color="#e74c3c", corner_radius=0, height=58)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(header, text="런치팝 설정",
                      font=ctk.CTkFont("맑은 고딕", 16, "bold"),
                      text_color="white").pack(side="left", padx=20, pady=16)
        ctk.CTkLabel(header, text=f"v{CURRENT_VERSION}",
                      font=ctk.CTkFont("맑은 고딕", 10),
                      text_color="#fadbd8").pack(side="right", padx=16, pady=20)

        # ── 커스텀 탭바 ──
        tab_bar = ctk.CTkFrame(win, fg_color="#f5f5f5", corner_radius=0, height=40)
        tab_bar.pack(fill="x")
        tab_bar.pack_propagate(False)

        # 콘텐츠 영역 (탭 전환용 프레임)
        content_area = ctk.CTkFrame(win, fg_color="white", corner_radius=0)
        content_area.pack(fill="both", expand=True)

        frame_settings = ctk.CTkFrame(content_area, fg_color="white", corner_radius=0)
        frame_log      = ctk.CTkFrame(content_area, fg_color="white", corner_radius=0)

        def show_tab(name):
            if name == "settings":
                frame_settings.pack(fill="both", expand=True)
                frame_log.pack_forget()
                btn_tab_settings.configure(fg_color="white", text_color="#e74c3c",
                                            border_width=0, border_color="#e74c3c")
                btn_tab_log.configure(fg_color="#f5f5f5", text_color="#7f8c8d",
                                       border_width=0)
            else:
                frame_log.pack(fill="both", expand=True)
                frame_settings.pack_forget()
                btn_tab_log.configure(fg_color="white", text_color="#e74c3c",
                                       border_width=0)
                btn_tab_settings.configure(fg_color="#f5f5f5", text_color="#7f8c8d",
                                            border_width=0)
                load_log()

        btn_tab_settings = ctk.CTkButton(
            tab_bar, text="설정", width=80, height=40,
            font=ctk.CTkFont("맑은 고딕", 11, "bold"),
            fg_color="white", text_color="#e74c3c",
            hover_color="#fef0ef", corner_radius=0,
            command=lambda: show_tab("settings"))
        btn_tab_settings.pack(side="left")

        # 탭 하단 강조선 (설정)
        ctk.CTkFrame(tab_bar, width=2, fg_color="#e8e8e8").pack(side="left", fill="y", pady=8)

        btn_tab_log = ctk.CTkButton(
            tab_bar, text="로그", width=80, height=40,
            font=ctk.CTkFont("맑은 고딕", 11),
            fg_color="#f5f5f5", text_color="#7f8c8d",
            hover_color="#ebebeb", corner_radius=0,
            command=lambda: show_tab("log"))
        btn_tab_log.pack(side="left")

        # 탭바 하단 구분선
        ctk.CTkFrame(win, fg_color="#e8e8e8", height=1, corner_radius=0).pack(fill="x")

        # ── 탭1: 설정 콘텐츠 ──
        def _save():
            global PRINTER_SETTING, MY_STORE_NAME
            new_store = store_entry.get().strip()
            if not new_store:
                messagebox.showwarning("경고", "매장명을 입력해주세요.", parent=win)
                return
            PRINTER_SETTING = cb.get()
            MY_STORE_NAME = new_store
            CONFIG["storeName"] = MY_STORE_NAME
            CONFIG["printer"] = PRINTER_SETTING
            CONFIG["autostart"] = av.get()
            if save_config(CONFIG):
                set_autostart_registry(av.get())
                self.store_label.configure(text=f"[ {MY_STORE_NAME} ]")
                messagebox.showinfo("완료", "설정이 저장되었습니다.", parent=win)
                win.destroy()
            else:
                messagebox.showerror("오류", "설정 저장 실패!\n디스크 용량을 확인해주세요.", parent=win)

        scroll_s = ctk.CTkScrollableFrame(frame_settings, fg_color="white", corner_radius=0)
        scroll_s.pack(fill="both", expand=True, padx=0, pady=0)

        PAD = 20  # 좌우 여백

        # 매장명
        ctk.CTkLabel(scroll_s, text="매장명",
                      font=ctk.CTkFont("맑은 고딕", 11, "bold"),
                      text_color="#2c3e50").pack(anchor="w", padx=PAD, pady=(16, 4))
        store_entry = ttk.Combobox(scroll_s, style="LP.TCombobox",
                                    font=("맑은 고딕", 11), width=28, state="normal")
        store_entry.pack(anchor="w", padx=PAD)
        store_entry.set(MY_STORE_NAME)

        def _load_stores_bg():
            try:
                resp = requests.get(f"{WEB_APP_URL}?action=getStores", timeout=10)
                if resp.status_code == 200:
                    stores = resp.json()
                    def _apply():
                        store_entry["values"] = stores
                    win.after(0, _apply)
            except Exception:
                pass
        threading.Thread(target=_load_stores_bg, daemon=True).start()

        ctk.CTkFrame(scroll_s, height=1, fg_color="#f0f0f0").pack(fill="x", padx=PAD, pady=14)

        # 프린터
        ctk.CTkLabel(scroll_s, text="프린터 선택",
                      font=ctk.CTkFont("맑은 고딕", 11, "bold"),
                      text_color="#2c3e50").pack(anchor="w", padx=PAD, pady=(0, 4))

        printer_list = ["기본 프린터"]
        try:
            installed = win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS, None, 2)
            printer_list += [p[2] for p in installed]
        except Exception:
            pass
        printer_list += [f"COM{i}" for i in range(1, 13)]

        cb = ttk.Combobox(scroll_s, style="LP.TCombobox",
                           font=("맑은 고딕", 11), width=28,
                           values=printer_list, state="readonly")
        cb.pack(anchor="w", padx=PAD)
        cb.set(PRINTER_SETTING)

        def do_test():
            btn_test.configure(text="출력 중...", state="disabled")
            test_printer = cb.get()

            def _run():
                try:
                    process_test_print(test_printer)
                finally:
                    try:
                        win.after(0, lambda: btn_test.configure(text="테스트 출력", state="normal"))
                    except Exception:
                        pass
            threading.Thread(target=_run, daemon=True).start()

        btn_test = ctk.CTkButton(
            scroll_s, text="테스트 출력", width=110, height=30,
            font=ctk.CTkFont("맑은 고딕", 10),
            fg_color="#95a5a6", hover_color="#7f8c8d",
            corner_radius=8, command=do_test)
        btn_test.pack(anchor="w", padx=PAD, pady=(10, 0))

        ctk.CTkFrame(scroll_s, height=1, fg_color="#f0f0f0").pack(fill="x", padx=PAD, pady=14)

        # 시스템
        ctk.CTkLabel(scroll_s, text="시스템 설정",
                      font=ctk.CTkFont("맑은 고딕", 11, "bold"),
                      text_color="#2c3e50").pack(anchor="w", padx=PAD, pady=(0, 8))
        av = tk.BooleanVar(value=CONFIG.get("autostart", True))
        ctk.CTkCheckBox(
            scroll_s, text="윈도우 시작 시 자동실행",
            variable=av, font=ctk.CTkFont("맑은 고딕", 11),
            checkmark_color="white", fg_color="#e74c3c",
            hover_color="#c0392b").pack(anchor="w", padx=PAD)

        # 저장 버튼 (스크롤 영역 밖 - 항상 하단 고정)
        bottom = ctk.CTkFrame(frame_settings, fg_color="white", corner_radius=0)
        bottom.pack(fill="x", padx=PAD, pady=12)
        ctk.CTkButton(bottom, text="설정 저장",
                       font=ctk.CTkFont("맑은 고딕", 12, "bold"),
                       fg_color="#e74c3c", hover_color="#c0392b",
                       height=44, corner_radius=10, command=_save).pack(fill="x")

        # ── 탭2: 로그 콘텐츠 ──
        log_text = scrolledtext.ScrolledText(
            frame_log, font=("Consolas", 8), state="disabled",
            bg="#1e272e", fg="#dfe6e9", insertbackground="white",
            relief="flat", bd=0)
        log_text.pack(fill="both", expand=True, padx=0, pady=0)

        def load_log():
            log_text.config(state="normal")
            log_text.delete("1.0", "end")
            try:
                if os.path.exists(LOCAL_LOG_FILE):
                    with open(LOCAL_LOG_FILE, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    tail = lines[-60:] if len(lines) > 60 else lines
                    log_text.insert("end", "".join(tail))
                    log_text.see("end")
                else:
                    log_text.insert("end", "로그 파일이 없습니다.")
            except Exception as e:
                log_text.insert("end", f"로그 로드 실패: {e}")
            log_text.config(state="disabled")

        log_btn_bar = ctk.CTkFrame(frame_log, fg_color="white", corner_radius=0)
        log_btn_bar.pack(fill="x", padx=16, pady=8)
        ctk.CTkButton(
            log_btn_bar, text="새로고침", command=load_log,
            font=ctk.CTkFont("맑은 고딕", 10),
            fg_color="#27ae60", hover_color="#1e8449",
            height=32, corner_radius=8).pack(anchor="e")

        # 기본 탭: 설정
        show_tab("settings")

    def toggle_list(self):
        if self.list_win and self.list_win.winfo_exists():
            self.list_win.destroy()
            self.list_win = None
            self.scroll_frame = None
            self.btn_list.configure(text="주문리스트 ▼")
        else:
            self.show_list()
            self.btn_list.configure(text="주문리스트 ▲")

    def show_list(self):
        self._last_list_key = None  # 창 새로 열 때 항상 새로 렌더링
        h = 400
        x, y = self.root.winfo_x(), self.root.winfo_y()
        dash_h = self.root.winfo_height()  # 에러 배너 포함 실제 높이
        self._list_above = (y + dash_h + h > self.root.winfo_screenheight())
        ny = y - h if self._list_above else y + dash_h

        # tk.Toplevel 사용 — CTkToplevel은 생성 시 부모 창을 Z뒤로 밀어냄
        self.list_win = tk.Toplevel(self.root)
        self.list_win.overrideredirect(True)
        self.list_win.attributes("-topmost", True)
        self.list_win.geometry(f"{self.base_w}x{h}+{x}+{ny}")
        self.list_win.configure(bg="#f4f6f7")

        # 대시보드를 리스트 창보다 앞으로 유지
        self.root.after(80, lambda: self.root.lift())

        # 헤더
        hdr = ctk.CTkFrame(self.list_win, fg_color="#1a1a2e", corner_radius=0, height=44)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text=f"오늘의 주문 현황  [ {MY_STORE_NAME} ]",
                      font=ctk.CTkFont("맑은 고딕", 11, "bold"),
                      text_color="white").pack(side="left", padx=14)

        def _close_list():
            self.list_win.destroy()
            self.list_win = None
            self.scroll_frame = None
            self.btn_list.configure(text="주문리스트 ▼")

        ctk.CTkButton(hdr, text="✕", width=32, height=28,
                       fg_color="transparent", hover_color="#e74c3c",
                       font=ctk.CTkFont("Arial", 12), corner_radius=6,
                       command=_close_list).pack(side="right", padx=8, pady=7)

        # 스크롤 영역
        self.scroll_frame = ctk.CTkScrollableFrame(
            self.list_win, fg_color="#f4f6f7", corner_radius=0)
        self.scroll_frame.pack(fill="both", expand=True, padx=8, pady=8)

        self._render_order_list()
        self._schedule_list_refresh()

    def _render_order_list(self):
        if not (self.list_win and self.list_win.winfo_exists()):
            return
        if self.scroll_frame is None:
            return

        with orders_lock:
            orders_snapshot = list(reversed(GLOBAL_ORDERS))

        # 주문 상태 변화 없으면 전체 재렌더 건너뜀 (위젯 메모리 절약)
        snapshot_key = [
            (o.get('orderNo', ''), o.get('isPrinted'), o.get('isQueued'))
            for o in orders_snapshot
        ]
        if snapshot_key == getattr(self, '_last_list_key', None):
            return
        self._last_list_key = snapshot_key

        for w in self.scroll_frame.winfo_children():
            w.destroy()

        if not orders_snapshot:
            ctk.CTkLabel(self.scroll_frame, text="오늘 주문이 없습니다.",
                          font=ctk.CTkFont("맑은 고딕", 11),
                          text_color="#7f8c8d").pack(pady=30)
            return

        for o in orders_snapshot:
            is_printed = o.get('isPrinted', False)
            is_fail = o.get('isQueued') and not is_printed

            if is_fail:
                row_color, icon, icon_color = "#fdecea", "✕", "#e74c3c"
            elif is_printed:
                row_color, icon, icon_color = "#eafaf1", "✓", "#27ae60"
            else:
                row_color, icon, icon_color = "white", "●", "#3498db"

            r = ctk.CTkFrame(self.scroll_frame, fg_color=row_color,
                               corner_radius=10, height=62)
            r.pack(fill="x", pady=3)
            r.pack_propagate(False)

            ctk.CTkLabel(r, text=icon,
                          font=ctk.CTkFont("Arial", 14, "bold"),
                          text_color=icon_color, width=32).pack(side="left", padx=10)

            info = ctk.CTkFrame(r, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, pady=6)
            ctk.CTkLabel(info,
                          text=f"[{o.get('orderNo', '')[-4:]}] {o.get('customerName', '')}",
                          font=ctk.CTkFont("맑은 고딕", 10, "bold"),
                          text_color="#2c3e50").pack(anchor="w")
            ctk.CTkLabel(info,
                          text=f"{o.get('menuName', '')[:20]}  |  {o.get('deliveryTime', '')}",
                          font=ctk.CTkFont("맑은 고딕", 9),
                          text_color="#7f8c8d").pack(anchor="w")

            btn_color = "#e74c3c" if is_fail else "#bdc3c7"
            btn = ctk.CTkButton(r, text="재출력", width=70, height=30,
                                  font=ctk.CTkFont("맑은 고딕", 9),
                                  fg_color=btn_color, hover_color="#c0392b",
                                  corner_radius=8)
            btn.pack(side="right", padx=10)
            btn.configure(command=self._make_reprint_cmd(o, btn))

    def _make_reprint_cmd(self, order, button):
        def cmd():
            ono = order.get('orderNo', '')
            if ono in reprint_in_progress:
                return
            reprint_in_progress.add(ono)
            button.configure(text="출력 중...", state="disabled", fg_color="#95a5a6")

            def do():
                try:
                    process_print(order, is_reprint=True)
                finally:
                    reprint_in_progress.discard(ono)
                    # UI 업데이트는 반드시 메인 스레드에서
                    try:
                        self.root.after(0, lambda: button.configure(
                            text="재출력", state="normal", fg_color="#bdc3c7"))
                    except Exception:
                        pass

            threading.Thread(target=do, daemon=True).start()
        return cmd

    def _schedule_list_refresh(self):
        if self.list_win and self.list_win.winfo_exists():
            self._render_order_list()
            self.root.after(30000, self._schedule_list_refresh)


# ==========================================
# [5] 엔트리 포인트
# ==========================================
if __name__ == '__main__':
    # ── 중복 실행 방지 (업데이트 재시작 시에도 안전하게 처리) ──
    import ctypes
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "LunchPopMaster_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        import time as _t; _t.sleep(2)
        _mutex2 = ctypes.windll.kernel32.CreateMutexW(None, False, "LunchPopMaster_SingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:
            try:
                root_tmp = tk.Tk(); root_tmp.withdraw()
                messagebox.showwarning("런치팝 알리미", "프로그램이 이미 실행 중입니다.")
                root_tmp.destroy()
            except Exception:
                pass
            sys.exit(0)

    write_local_log(f"--- Application Started (v{CURRENT_VERSION}) ---")

    # 업데이트 후 정상 시작 확인 → 백업 파일 삭제
    if os.path.exists(BACKUP_EXE_PATH):
        try:
            os.remove(BACKUP_EXE_PATH)
            write_local_log("[INFO] 업데이트 성공 확인 — 백업 파일 삭제 완료")
        except Exception:
            pass

    if not CONFIG.get("storeName"):
        select_store_ui()

    if not CONFIG.get("storeName"):
        write_local_log("[INFO] 매장 미설정으로 종료")
        sys.exit(0)

    MY_STORE_NAME = CONFIG["storeName"]
    PRINTER_SETTING = CONFIG.get("printer", "기본 프린터")
    set_autostart_registry(CONFIG.get("autostart", True))

    dashboard = SmartDashboard()
    _polling_thread = threading.Thread(target=run_auto_printer, daemon=True)
    _polling_thread.start()
    threading.Thread(target=run_thread_watchdog, daemon=True).start()
    # 업데이트는 Launcher가 담당 — Master에서 run_auto_updater 스레드 제거

    def setup_tray():
        try:
            img = Image.open(resource_path("logo.ico"))
        except Exception:
            img = Image.new('RGB', (64, 64), color=(231, 76, 60))

        m = pystray.Menu(
            pystray.MenuItem(f'매장: {MY_STORE_NAME}', None, enabled=False),
            pystray.MenuItem(f'버전: v{CURRENT_VERSION}', None, enabled=False),
            pystray.MenuItem('대시보드 열기',
                              lambda *_: dashboard.root.after(0, dashboard.restore_from_tray),
                              default=True),
            pystray.MenuItem('프로그램 종료', lambda *_: os._exit(0))
        )
        pystray.Icon("LunchPop", img, f"런치팝 알리미 — {MY_STORE_NAME}", m).run()

    threading.Thread(target=setup_tray, daemon=True).start()
    dashboard.root.mainloop()
