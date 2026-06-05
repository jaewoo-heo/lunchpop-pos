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
from tkinter import ttk, messagebox, scrolledtext
import subprocess
import winreg
import hashlib
import shutil

# ==========================================
# [설정] 버전 및 URL
# ==========================================
CURRENT_VERSION = 3.2
TARGET_EXE_NAME = "LunchPop_Master.exe"

WEB_APP_URL = "https://script.google.com/macros/s/AKfycbzG_q6m1svwhZZny0DAz1s29qEGfVUO_gdnUOelX5QmIKPjTM8kvYjYhro_b7b_7w/exec"
CHECK_INTERVAL = 60

# ==========================================
# 경로 설정
# ==========================================
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "lunchpop_config.json")
BAT_FILE = os.path.join(BASE_DIR, "updater.bat")
LOCAL_LOG_FILE = os.path.join(BASE_DIR, "system_debug.log")
LOCAL_LOG_OLD = os.path.join(BASE_DIR, "system_debug.old.log")
TARGET_EXE_PATH = os.path.join(BASE_DIR, TARGET_EXE_NAME)


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
GLOBAL_ORDERS = []
printed_ids = set()          # 인쇄 완료 orderNo 세트 (서버 재응답 시 덮어쓰기 방지)
reprint_in_progress = set()  # 재출력 진행 중인 orderNo (중복 출력 방지)
dashboard = None
PRINTER_SETTING = "기본 프린터"
MY_STORE_NAME = ""
consecutive_fail_count = 0


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
        requests.get(WEB_APP_URL, params={"action": "log", "storeName": store, "logMsg": text}, timeout=10)
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
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        reg = winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER)
        key_obj = winreg.OpenKey(reg, key_path, 0, winreg.KEY_ALL_ACCESS)
        if enable:
            winreg.SetValueEx(key_obj, "LunchPopMaster", 0, winreg.REG_SZ, f'"{TARGET_EXE_PATH}"')
        else:
            try:
                winreg.DeleteValue(key_obj, "LunchPopMaster")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key_obj)
    except Exception as e:
        write_local_log(f"[ERR] 레지스트리 오류: {e}")
        if dashboard:
            dashboard.root.after(0, lambda: messagebox.showwarning(
                "경고", f"시작 프로그램 등록 실패:\n{e}\n\n수동으로 설정이 필요할 수 있습니다."))


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
# [1] 초기 설정 마법사 UI
# ==========================================
def _set_icon(window):
    """모든 창에 로고 아이콘 적용"""
    try:
        window.iconbitmap(resource_path("logo.ico"))
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

    root = tk.Tk()
    root.title(f"런치팝 초기 설정 (v{CURRENT_VERSION})")
    root.attributes('-topmost', True)
    root.resizable(False, False)
    _set_icon(root)

    def on_close():
        if messagebox.askyesno("종료 확인", "설정을 완료하지 않으면 프로그램이 종료됩니다.\n종료하시겠습니까?"):
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    header = tk.Frame(root, bg="#e74c3c", pady=10)
    header.pack(fill="x")
    tk.Label(header, text="런치팝 포스 설정", fg="white", bg="#e74c3c",
             font=("맑은 고딕", 14, "bold")).pack()
    tk.Label(header, text=f"v{CURRENT_VERSION}", fg="#fadbd8", bg="#e74c3c",
             font=("맑은 고딕", 9)).pack()

    body = tk.Frame(root, padx=25, pady=10)
    body.pack(fill="x")

    tk.Label(body, text="1. 매장명을 선택해주세요", font=("맑은 고딕", 11, "bold")).pack(anchor="w", pady=(6, 3))
    store_combo = ttk.Combobox(body, values=["로딩 중..."], state="normal", width=30, font=("맑은 고딕", 11))
    store_combo.pack(anchor="w")
    store_combo.set("로딩 중...")

    def load_stores():
        try:
            resp = requests.get(f"{WEB_APP_URL}?action=getStores", timeout=10)
            if resp.status_code == 200:
                stores = resp.json()
                store_combo.config(values=stores)
                if stores:
                    store_combo.current(0)
                    on_store_change()
                return
        except Exception as e:
            write_local_log(f"[ERR] 매장 목록 로드 실패: {e}")
        fallback = ["덮밥천재", "직접입력"]
        store_combo.config(values=fallback)
        store_combo.current(0)
        on_store_change()

    threading.Thread(target=load_stores, daemon=True).start()

    tk.Label(body, text="2. 프린터 연결 방식을 선택해주세요", font=("맑은 고딕", 11, "bold")).pack(anchor="w", pady=(12, 3))

    printer_list = ["기본 프린터"]
    try:
        installed = win32print.EnumPrinters(
            win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS, None, 2)
        printer_list += [p[2] for p in installed]
    except Exception:
        pass
    printer_list += [f"COM{i}" for i in range(1, 13)]

    printer_combo = ttk.Combobox(body, values=printer_list, state="readonly", width=30, font=("맑은 고딕", 11))
    printer_combo.pack(anchor="w")
    printer_combo.set(CONFIG.get("printer", "기본 프린터"))

    autostart_var = tk.BooleanVar(value=True)
    tk.Checkbutton(body, text="윈도우 시작 시 자동 실행 (권장)", variable=autostart_var,
                   font=("맑은 고딕", 10)).pack(anchor="w", pady=(10, 0))

    confirm_btn = tk.Button(body, text="설정 완료 및 시작", state="disabled",
                             width=22, height=2, bg="#95a5a6", fg="white",
                             font=("맑은 고딕", 11, "bold"))
    confirm_btn.pack(pady=(12, 6))

    def on_store_change(*_args):
        val = store_combo.get().strip()
        if val and val not in ("로딩 중...", ""):
            confirm_btn.config(state="normal", bg="#27ae60")
        else:
            confirm_btn.config(state="disabled", bg="#95a5a6")

    store_combo.bind("<<ComboboxSelected>>", on_store_change)
    store_combo.bind("<KeyRelease>", on_store_change)

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

    confirm_btn.config(command=on_confirm)
    _center_window(root)
    root.mainloop()


# ==========================================
# [2] 인쇄 핵심 로직
# ==========================================
def print_raw_text(receipt_bytes):
    last_err = ""
    for attempt in range(3):
        try:
            if PRINTER_SETTING == "기본 프린터":
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
            elif PRINTER_SETTING.startswith("COM"):
                with serial.Serial(PRINTER_SETTING, 9600, timeout=2) as ser:
                    ser.write(b'\x10\x04\x04')
                    status = ser.read(1)
                    if status and (ord(status) & 0x60) == 0x60:
                        write_remote_log(f"[WARN] {PRINTER_SETTING} 용지 없음!")
                        return False
                    ser.write(receipt_bytes)
                    time.sleep(0.5)
                return True
            else:
                # 이름으로 직접 지정된 프린터
                hPrinter = win32print.OpenPrinter(PRINTER_SETTING)
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
    write_remote_log(f"[ERR] PRINT FATAL ({PRINTER_SETTING}): {last_err}")
    return False


def process_test_print():
    CMD_INIT = b'\x1B\x40'
    CMD_ALIGN_CENTER = b'\x1B\x61\x01'
    CMD_CUT = b'\x1D\x56\x42\x00'
    test_data = (CMD_INIT + CMD_ALIGN_CENTER +
                 "\n[ 런치팝 프린터 테스트 ]\n\n정상적으로 연결되었습니다.\n\n바쁜 일상이 좀 더 편해지도록, 런치팝\n\n\n\n\n"
                 .encode('cp949') + CMD_CUT)
    if print_raw_text(test_data):
        messagebox.showinfo("성공", "테스트 용지가 출력되었습니다.")
    else:
        messagebox.showerror("실패", f"프린터 연결을 확인해주세요.\n현재 설정: {PRINTER_SETTING}")


def _build_receipt_bytes(order, is_reprint=False):
    CMD_ALIGN_CENTER = b'\x1B\x61\x01'
    CMD_ALIGN_LEFT = b'\x1B\x61\x00'
    CMD_SIZE_LARGE = b'\x1D\x21\x22'
    CMD_SIZE_NORMAL = b'\x1D\x21\x00'
    CMD_CUT = b'\x1D\x56\x42\x00'

    reprint_tag = "[ 재출력 ]\n" if is_reprint else ""
    cook_time = ""
    if order.get('deliveryTime'):
        clean_str = str(order['deliveryTime']).replace("시 ", ":").replace("시", ":").replace("분", "")
        match = re.search(r'(\d{1,2}):\s?(\d{2})', clean_str)
        if match:
            h, m = int(match.group(1)), int(match.group(2))
            if "오후" in str(order['deliveryTime']) and h < 12:
                h += 12
            dt = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0) - timedelta(minutes=30)
            cook_time = f"{dt.hour:02d}시 {dt.minute:02d}분"

    body = (
        CMD_ALIGN_CENTER + CMD_SIZE_LARGE +
        "LUNCH POP\n\n".encode('cp949') +
        CMD_SIZE_NORMAL + CMD_ALIGN_LEFT +
        (f"{reprint_tag}"
         f"------------------------------------------\n"
         f"[ {MY_STORE_NAME} ]\n"
         f"------------------------------------------\n"
         f"주문자: {order.get('customerName', '')}\n"
         f"주문번호: {order.get('orderNo', '')}\n"
         f"배달예정: {order.get('deliveryTime', '')}\n"
         f"조리완료: {cook_time} (목표)\n"
         f"------------------------------------------\n").encode('cp949')
    )
    menu_info = f"{order.get('menuName', '')}   x{order.get('quantity', '')}\n".encode('cp949')
    footer = (
        "\n------------------------------------------\n"
        f"관리번호: {order.get('orderNo', '')[-4:]}\n"
        "------------------------------------------\n"
    ).encode('cp949') + \
        CMD_ALIGN_CENTER + \
        "바쁜 일상이 좀 더 편해지도록, 런치팝\n\n\n\n\n".encode('cp949') + \
        CMD_CUT

    return body + menu_info + footer


def process_print(order, is_reprint=False):
    try:
        receipt_bytes = _build_receipt_bytes(order, is_reprint)
        if print_raw_text(receipt_bytes):
            if not is_reprint:
                resp = fetch_with_retry(WEB_APP_URL, params={
                    "action": "markDone",
                    "rowIndex": order.get('rowIndex', ''),
                    "orderNo": order.get('orderNo', '')
                }, retries=3, timeout=10)
                if not resp:
                    write_local_log(f"[WARN] markDone 전송 실패 (로컬 완료 처리): {order.get('orderNo', '')}")
                with orders_lock:
                    printed_ids.add(order.get('orderNo', ''))
            return True
        else:
            if dashboard:
                dashboard.root.after(0, lambda o=order: dashboard.show_print_error(o))
            return False
    except Exception as e:
        write_remote_log(f"[ERR] process_print: {e}")
        if dashboard:
            dashboard.root.after(0, lambda o=order: dashboard.show_print_error(o))
        return False


# ==========================================
# [3] 스케줄러 (업데이트, 폴링)
# ==========================================
def run_auto_updater():
    while True:
        try:
            resp = fetch_with_retry(WEB_APP_URL, params={"action": "checkUpdate"}, retries=2, timeout=15)
            if resp:
                data = resp.json()
                server_version = float(data.get("version", 1.0))
                if server_version > CURRENT_VERSION:
                    update_url = data.get("url", "")
                    expected_sha256 = data.get("sha256", "")

                    # 메인 스레드에서 사용자 승인 요청
                    approved = [False]
                    done_event = threading.Event()

                    def ask():
                        approved[0] = messagebox.askyesno(
                            "업데이트 알림",
                            f"새 버전 v{server_version}이 있습니다.\n지금 업데이트하시겠습니까?\n(완료 후 자동 재시작됩니다)"
                        )
                        done_event.set()

                    if dashboard:
                        dashboard.root.after(0, ask)
                        done_event.wait(timeout=60)
                        if not approved[0]:
                            time.sleep(86400)
                            continue

                    temp_exe = os.path.join(BASE_DIR, "update_new.exe")
                    write_local_log(f"[INFO] 업데이트 다운로드 시작: v{server_version}")

                    # GitHub Private 저장소 대응: GAS가 token을 내려줄 경우 헤더에 포함
                    # Public 저장소이면 token 없이도 동작 (headers 무시됨)
                    gh_token = data.get("gh_token", "")
                    dl_headers = {"Authorization": f"token {gh_token}"} if gh_token else {}

                    sha256 = hashlib.sha256()
                    with requests.get(update_url, headers=dl_headers, stream=True, timeout=60) as r:
                        r.raise_for_status()
                        with open(temp_exe, 'wb') as f:
                            for chunk in r.iter_content(8192):
                                f.write(chunk)
                                sha256.update(chunk)

                    if expected_sha256 and sha256.hexdigest() != expected_sha256:
                        write_remote_log(f"[ERR] 업데이트 파일 검증 실패 (sha256 불일치), 업데이트 취소")
                        try:
                            os.remove(temp_exe)
                        except Exception:
                            pass
                        time.sleep(86400)
                        continue

                    with open(BAT_FILE, "w", encoding="cp949") as f:
                        f.write(
                            f'@echo off\ntimeout /t 2\ndel "{TARGET_EXE_PATH}"\n'
                            f'move "{temp_exe}" "{TARGET_EXE_PATH}"\n'
                            f'start "" "{TARGET_EXE_PATH}"\ndel "%~f0"'
                        )

                    write_remote_log(f"[INFO] 업데이트 설치: v{CURRENT_VERSION} → v{server_version}")
                    subprocess.Popen(["cmd.exe", "/c", BAT_FILE], creationflags=0x08000000)
                    os._exit(0)

        except Exception as e:
            write_local_log(f"[ERR] 업데이터 오류: {e}")

        time.sleep(86400)


def run_auto_printer():
    global GLOBAL_ORDERS, dashboard, consecutive_fail_count

    write_remote_log(f"[INFO] CONNECTED v{CURRENT_VERSION} ({PRINTER_SETTING})")
    last_cleanup_date = ""
    heartbeat_counter = 0

    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            # 새벽 4시 - 인쇄 완료된 주문만 정리 (미완료 주문 보존)
            if now.hour == 4 and last_cleanup_date != today_str:
                with orders_lock:
                    GLOBAL_ORDERS = [o for o in GLOBAL_ORDERS if not o.get('isPrinted')]
                    printed_ids.clear()
                last_cleanup_date = today_str
                write_remote_log("[INFO] 새벽 메모리 정리 완료")

            resp = fetch_with_retry(WEB_APP_URL, params={
                "action": "fetchV2",
                "storeName": MY_STORE_NAME
            }, retries=3, timeout=20)

            if resp:
                consecutive_fail_count = 0
                server_data = resp.json()
                if dashboard:
                    dashboard.root.after(0, lambda: dashboard.update_sync_status(True))

                if isinstance(server_data, list):
                    # 서버 데이터 병합: 로컬 인쇄 완료 상태 우선
                    with orders_lock:
                        for o in server_data:
                            if o.get('orderNo', '') in printed_ids:
                                o['isPrinted'] = True
                            # 초기 로드 시 서버의 isPrinted=True를 printed_ids에 반영
                            elif o.get('isPrinted'):
                                printed_ids.add(o.get('orderNo', ''))
                        GLOBAL_ORDERS = server_data

                    with orders_lock:
                        pending = [o for o in GLOBAL_ORDERS if o.get('isQueued') and not o.get('isPrinted')]
                        total = len(GLOBAL_ORDERS)
                        done_count = total - len(pending)

                    if dashboard:
                        t, d, p = total, done_count, len(pending)
                        dashboard.root.after(0, lambda _t=t, _d=d, _p=p: dashboard.update_counts(_t, _d, _p))

                    for o in pending:
                        if os.path.exists(ALARM_FILE):
                            try:
                                winsound.PlaySound(ALARM_FILE, winsound.SND_FILENAME | winsound.SND_ASYNC)
                            except Exception as e:
                                write_local_log(f"[WARN] 알람 재생 실패: {e}")
                        if process_print(o):
                            with orders_lock:
                                o['isPrinted'] = True
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


# ==========================================
# [4] 스마트 대시보드 UI
# ==========================================
class SmartDashboard:
    NORMAL_BG = "#2c3e50"
    PENDING_BG = "#c0392b"
    BTN_BG = "#34495e"

    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.base_w = 660
        self.base_h = 75
        self._current_bg = self.NORMAL_BG
        self._has_error = False
        self.list_win = None

        pos_x, pos_y = CONFIG.get("dash_x"), CONFIG.get("dash_y")
        if pos_x is not None:
            self.root.geometry(f"{self.base_w}x{self.base_h}+{pos_x}+{pos_y}")
        else:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.root.geometry(f"{self.base_w}x{self.base_h}+{sw - self.base_w - 10}+{sh - self.base_h - 50}")

        self.root.configure(bg=self.NORMAL_BG)

        # 드래그 바인딩 (에러 프레임 제외)
        self.root.bind("<Button-1>", self.start_move)
        self.root.bind("<B1-Motion>", self.do_move)
        self.root.bind("<ButtonRelease-1>", self.save_pos)

        # ── 메인 프레임 ──
        self.main_frame = tk.Frame(self.root, bg=self.NORMAL_BG)
        self.main_frame.pack(fill="x", padx=12, pady=6)

        # 왼쪽: 매장명 + 카운트
        self.left_frame = tk.Frame(self.main_frame, bg=self.NORMAL_BG)
        self.left_frame.pack(side="left", fill="both", expand=True)

        self.store_label = tk.Label(
            self.left_frame, text=f"[ {MY_STORE_NAME} ]",
            fg="#95a5a6", bg=self.NORMAL_BG, font=("맑은 고딕", 9))
        self.store_label.pack(anchor="w")

        self.info_label = tk.Label(
            self.left_frame, text="서버 연결 중...",
            fg="white", bg=self.NORMAL_BG, font=("맑은 고딕", 12, "bold"))
        self.info_label.pack(anchor="w")

        # 오른쪽: 상태 + 버튼
        self.right_frame = tk.Frame(self.main_frame, bg=self.NORMAL_BG)
        self.right_frame.pack(side="right")

        self.sync_frame = tk.Frame(self.right_frame, bg=self.NORMAL_BG)
        self.sync_frame.pack(anchor="e", pady=(0, 3))

        self.sync_dot = tk.Label(self.sync_frame, text="●", fg="#2ecc71", bg=self.NORMAL_BG, font=("Arial", 9))
        self.sync_dot.pack(side="left")
        self.sync_label = tk.Label(self.sync_frame, text="연결 중", fg="#95a5a6", bg=self.NORMAL_BG,
                                    font=("맑은 고딕", 9))
        self.sync_label.pack(side="left", padx=3)

        self.btn_frame = tk.Frame(self.right_frame, bg=self.NORMAL_BG)
        self.btn_frame.pack(anchor="e")

        self.btn_list = tk.Button(
            self.btn_frame, text="주문리스트 ▼", command=self.toggle_list,
            bg=self.BTN_BG, fg="white", relief="flat",
            font=("맑은 고딕", 10, "bold"), padx=8, pady=2)
        self.btn_list.pack(side="left", padx=2)

        tk.Button(self.btn_frame, text="설정", command=self.open_settings,
                  bg=self.BTN_BG, fg="white", relief="flat",
                  font=("맑은 고딕", 10), padx=8, pady=2).pack(side="left", padx=2)

        tk.Button(self.btn_frame, text="─", command=lambda: self.root.withdraw(),
                  bg=self.BTN_BG, fg="white", relief="flat",
                  font=("Arial", 10), padx=5, pady=2).pack(side="left", padx=2)

        # ── 에러 배너 (기본 숨김) ──
        self.error_frame = tk.Frame(self.root, bg="#e74c3c")
        self.error_label = tk.Label(self.error_frame, text="", fg="white", bg="#e74c3c",
                                     font=("맑은 고딕", 10, "bold"))
        self.error_label.pack(side="left", padx=10, pady=5)
        tk.Button(self.error_frame, text="확인", command=self.dismiss_error,
                  bg="#c0392b", fg="white", relief="flat",
                  font=("맑은 고딕", 9), padx=8).pack(side="right", padx=6)

        # 배경 변경 대상 위젯 목록 (버튼 제외)
        self._bg_targets = [
            self.main_frame, self.left_frame, self.right_frame,
            self.sync_frame, self.btn_frame,
            self.store_label, self.info_label, self.sync_dot, self.sync_label
        ]

    def _set_bg(self, color):
        self._current_bg = color
        self.root.configure(bg=color)
        for w in self._bg_targets:
            try:
                w.configure(bg=color)
            except Exception:
                pass

    def update_sync_status(self, success, fail_count=0):
        if success:
            self.sync_dot.config(fg="#2ecc71")
            self.sync_label.config(text=datetime.now().strftime("%H:%M"), fg="#95a5a6")
        else:
            self.sync_dot.config(fg="#e74c3c")
            if fail_count >= 3:
                self.sync_label.config(text="서버 오류", fg="#e74c3c")
            else:
                self.sync_label.config(text="연결 중...", fg="#e67e22")

    def update_counts(self, total, done, pending):
        self.info_label.config(
            text=f"오늘 {total}건  |  완료 {done}  |  대기 {pending}",
            fg="#ff7675" if pending > 0 else "white"
        )
        if pending > 0:
            self._set_bg(self.PENDING_BG)
            self.store_label.config(fg="#fadbd8")
        else:
            self._set_bg(self.NORMAL_BG)
            self.store_label.config(fg="#95a5a6")

    def show_print_error(self, order):
        self._has_error = True
        order_no = order.get('orderNo', '')[-4:]
        name = order.get('customerName', '')
        self.error_label.config(text=f"  인쇄 실패  [{order_no}] {name}  —  주문리스트에서 재출력하세요")
        self.error_frame.pack(fill="x")
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

    def save_pos(self, _e):
        CONFIG["dash_x"] = self.root.winfo_x()
        CONFIG["dash_y"] = self.root.winfo_y()
        save_config(CONFIG)

    def restore_from_tray(self):
        self.root.deiconify()
        self.root.attributes("-topmost", True)

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("런치팝 설정")
        win.attributes("-topmost", True)
        win.grab_set()
        win.resizable(False, False)
        _set_icon(win)

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=4)

        # ── 탭 1: 프린터 / 매장 설정 ──
        tab1 = tk.Frame(nb, padx=18, pady=10)
        nb.add(tab1, text="  설정  ")

        # 설정 저장 버튼을 하단에 고정 (먼저 pack해야 bottom이 우선 확보됨)
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
                self.store_label.config(text=f"[ {MY_STORE_NAME} ]")
                messagebox.showinfo("완료", "설정이 저장되었습니다.", parent=win)
                win.destroy()
            else:
                messagebox.showerror("오류", "설정 저장 실패!\n디스크 용량을 확인해주세요.", parent=win)

        tk.Button(tab1, text="설정 저장", command=_save,
                  bg="#2980b9", fg="white", font=("맑은 고딕", 11, "bold"),
                  width=16, height=2).pack(side="bottom", pady=(8, 0))

        # 매장명
        tk.Label(tab1, text="매장명", font=("맑은 고딕", 10, "bold")).pack(anchor="w", pady=(0, 3))
        store_entry = ttk.Combobox(tab1, font=("맑은 고딕", 10), width=32)
        store_entry.set(MY_STORE_NAME)
        store_entry.pack(anchor="w")

        def _load_stores_bg():
            try:
                resp = requests.get(f"{WEB_APP_URL}?action=getStores", timeout=10)
                if resp.status_code == 200:
                    stores = resp.json()
                    store_entry.config(values=stores)
            except Exception:
                pass
        threading.Thread(target=_load_stores_bg, daemon=True).start()

        ttk.Separator(tab1, orient="horizontal").pack(fill="x", pady=8)

        # 프린터
        tk.Label(tab1, text="프린터 선택", font=("맑은 고딕", 10, "bold")).pack(anchor="w", pady=(0, 3))

        printer_list = ["기본 프린터"]
        try:
            installed = win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS, None, 2)
            printer_list += [p[2] for p in installed]
        except Exception:
            pass
        printer_list += [f"COM{i}" for i in range(1, 13)]

        cb = ttk.Combobox(tab1, values=printer_list, state="readonly", font=("맑은 고딕", 10), width=32)
        cb.set(PRINTER_SETTING)
        cb.pack(anchor="w")

        def do_test():
            btn_test.config(text="출력 중...", state="disabled")
            win.update()
            process_test_print()
            btn_test.config(text="테스트 출력", state="normal")

        btn_test = tk.Button(tab1, text="테스트 출력", command=do_test,
                              bg="#7f8c8d", fg="white", font=("맑은 고딕", 10), width=16)
        btn_test.pack(anchor="w", pady=(6, 0))

        ttk.Separator(tab1, orient="horizontal").pack(fill="x", pady=8)

        # 시스템
        tk.Label(tab1, text="시스템 설정", font=("맑은 고딕", 10, "bold")).pack(anchor="w", pady=(0, 3))
        av = tk.BooleanVar(value=CONFIG.get("autostart", True))
        tk.Checkbutton(tab1, text="윈도우 시작 시 자동실행", variable=av, font=("맑은 고딕", 10)).pack(anchor="w")

        # ── 탭 2: 로그 뷰어 ──
        tab2 = tk.Frame(nb, padx=6, pady=6)
        nb.add(tab2, text="  로그  ")

        log_text = scrolledtext.ScrolledText(
            tab2, font=("Consolas", 8), height=16, state="disabled",
            bg="#1e272e", fg="#dfe6e9", insertbackground="white")
        log_text.pack(fill="both", expand=True)

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

        load_log()
        tk.Button(tab2, text="새로고침", command=load_log,
                  bg="#27ae60", fg="white", font=("맑은 고딕", 10)).pack(pady=4)

        _center_window(win)

    def toggle_list(self):
        if self.list_win and self.list_win.winfo_exists():
            self.list_win.destroy()
            self.list_win = None
            self.btn_list.config(text="주문리스트 ▼")
        else:
            self.show_list()
            self.btn_list.config(text="주문리스트 ▲")

    def show_list(self):
        h = 390
        x, y = self.root.winfo_x(), self.root.winfo_y()
        ny = y - h if y + h > self.root.winfo_screenheight() else y + self.base_h

        self.list_win = tk.Toplevel(self.root)
        self.list_win.overrideredirect(True)
        self.list_win.attributes("-topmost", True)
        self.list_win.geometry(f"{self.base_w}x{h}+{x}+{ny}")
        self.list_win.configure(bg="#f0f3f4")

        # 헤더
        hdr = tk.Frame(self.list_win, bg="#2c3e50", pady=7)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"오늘의 주문 현황  [ {MY_STORE_NAME} ]",
                 fg="white", bg="#2c3e50", font=("맑은 고딕", 11, "bold")).pack(side="left", padx=12)
        tk.Button(hdr, text="✕",
                  command=lambda: (self.list_win.destroy(),
                                   setattr(self, 'list_win', None),
                                   self.btn_list.config(text="주문리스트 ▼")),
                  bg="#2c3e50", fg="white", relief="flat", font=("Arial", 12)).pack(side="right", padx=6)

        # 스크롤 영역
        canvas = tk.Canvas(self.list_win, bg="#f0f3f4", highlightthickness=0)
        sb = ttk.Scrollbar(self.list_win, orient="vertical", command=canvas.yview)
        self.list_frame = tk.Frame(canvas, bg="#f0f3f4")
        self.list_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self._render_order_list()
        self._schedule_list_refresh()

    def _render_order_list(self):
        if not (self.list_win and self.list_win.winfo_exists()):
            return
        for w in self.list_frame.winfo_children():
            w.destroy()

        with orders_lock:
            orders_snapshot = list(reversed(GLOBAL_ORDERS))

        if not orders_snapshot:
            tk.Label(self.list_frame, text="오늘 주문이 없습니다.",
                     bg="#f0f3f4", font=("맑은 고딕", 11), fg="#7f8c8d").pack(pady=30)
            return

        for o in orders_snapshot:
            is_printed = o.get('isPrinted', False)
            is_fail = o.get('isQueued') and not is_printed

            row_bg = "#fadbd8" if is_fail else ("#eafaf1" if is_printed else "white")

            r = tk.Frame(self.list_frame, bg=row_bg, pady=8)
            r.pack(fill="x", padx=8, pady=2)

            # 상태 아이콘
            tk.Label(r, text="✓" if is_printed else ("!" if is_fail else "●"),
                     fg=("#27ae60" if is_printed else "#e74c3c"),
                     bg=row_bg, font=("Arial", 12, "bold"), width=2).pack(side="left", padx=5)

            # 정보
            info = tk.Frame(r, bg=row_bg)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=f"[{o.get('orderNo', '')[-4:]}] {o.get('customerName', '')}",
                     bg=row_bg, font=("맑은 고딕", 10, "bold")).pack(anchor="w")
            tk.Label(info,
                     text=f"{o.get('menuName', '')[:20]}  |  {o.get('deliveryTime', '')}",
                     bg=row_bg, font=("맑은 고딕", 9), fg="#555").pack(anchor="w")

            # 재출력 버튼
            btn = tk.Button(r, text="재출력", bg="#bdc3c7", fg="white",
                             font=("맑은 고딕", 9), padx=8, relief="flat")
            btn.pack(side="right", padx=8)
            btn.config(command=self._make_reprint_cmd(o, btn))

    @staticmethod
    def _make_reprint_cmd(order, button):
        def cmd():
            ono = order.get('orderNo', '')
            if ono in reprint_in_progress:
                return
            reprint_in_progress.add(ono)
            button.config(text="출력 중...", state="disabled", bg="#95a5a6")

            def do():
                try:
                    process_print(order, is_reprint=True)
                finally:
                    reprint_in_progress.discard(ono)
                    try:
                        button.config(text="재출력", state="normal", bg="#bdc3c7")
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
    write_local_log(f"--- Application Started (v{CURRENT_VERSION}) ---")

    if not CONFIG.get("storeName"):
        select_store_ui()

    if not CONFIG.get("storeName"):
        write_local_log("[INFO] 매장 미설정으로 종료")
        sys.exit(0)

    MY_STORE_NAME = CONFIG["storeName"]
    PRINTER_SETTING = CONFIG.get("printer", "기본 프린터")
    set_autostart_registry(CONFIG.get("autostart", True))

    dashboard = SmartDashboard()
    threading.Thread(target=run_auto_printer, daemon=True).start()
    threading.Thread(target=run_auto_updater, daemon=True).start()

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
