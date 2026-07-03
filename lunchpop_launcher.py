"""
런치팝 Launcher v1.0
- GAS에서 버전 확인 → SHA256 비교 → 다를 때만 Master 업데이트
- Master 실행 후 종료 (자기 자신은 교체 안 함)
- Windows 시작 프로그램 자동 등록
"""
import requests
import hashlib
import subprocess
import sys
import os
import shutil
import winreg
import ctypes
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

LAUNCHER_VERSION = "1.1"
GAS_URL = "https://script.google.com/macros/s/AKfycbzG_q6m1svwhZZny0DAz1s29qEGfVUO_gdnUOelX5QmIKPjTM8kvYjYhro_b7b_7w/exec"
MASTER_EXE_NAME = "LunchPop_Master.exe"

BASE_DIR = (os.path.dirname(sys.executable)
            if getattr(sys, 'frozen', False)
            else os.path.dirname(os.path.abspath(__file__)))

MASTER_PATH  = os.path.join(BASE_DIR, MASTER_EXE_NAME)
BACKUP_PATH  = os.path.join(BASE_DIR, "LunchPop_Master.backup.exe")
TEMP_PATH    = os.path.join(BASE_DIR, "LunchPop_Master.update.exe")
LOG_FILE     = os.path.join(BASE_DIR, "launcher_debug.log")


# ── 로그 (5MB 초과 시 로테이션) ──────────────────────────
MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 MB

def log(msg):
    try:
        # 5MB 초과 시 .old 로 백업하고 새 파일 시작
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
            old_log = LOG_FILE.replace(".log", ".old.log")
            if os.path.exists(old_log):
                os.remove(old_log)
            os.rename(LOG_FILE, old_log)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


# ── SHA256 계산 ───────────────────────────────────────────
def sha256_of(path):
    if not os.path.exists(path):
        return ""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest().lower()


# ── Master 실행 여부 확인 (중복 실행/파일 잠금 방지) ──────────
def is_master_running():
    try:
        out = subprocess.check_output(
            ['tasklist', '/FI', f'IMAGENAME eq {MASTER_EXE_NAME}'],
            creationflags=subprocess.CREATE_NO_WINDOW, text=True, timeout=5)
        return MASTER_EXE_NAME.lower() in out.lower()
    except Exception as e:
        log(f"[WARN] 프로세스 확인 실패: {e}")
        return False


# ── 자동 시작 등록 ─────────────────────────────────────────
def set_autostart():
    try:
        launcher_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "LunchPopAlrimi", 0, winreg.REG_SZ, f'"{launcher_path}"')
        winreg.CloseKey(key)
        log("[INFO] 자동 시작 등록 완료")
    except Exception as e:
        log(f"[WARN] 자동 시작 등록 실패: {e}")


# ── 업데이트 진행 창 ──────────────────────────────────────
def make_progress_window():
    win = tk.Tk()
    win.title("런치팝 업데이트")
    win.geometry("380x130")
    win.resizable(False, False)
    win.attributes("-topmost", True)
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"380x130+{(sw-380)//2}+{(sh-130)//2}")

    tk.Label(win, text="🍱  런치팝 업데이트 중...",
             font=("맑은 고딕", 12, "bold")).pack(pady=(18, 6))
    pb = ttk.Progressbar(win, mode='indeterminate', length=320)
    pb.pack(pady=4)
    pb.start(12)
    lbl = tk.Label(win, text="새 버전 확인 중...",
                   font=("맑은 고딕", 9), fg="#555555")
    lbl.pack()
    win.update()
    return win, lbl


# ── 업데이트 실행 ─────────────────────────────────────────
def check_and_update():
    try:
        resp = requests.get(GAS_URL, params={"action": "checkUpdate"}, timeout=15)
        data = resp.json()
        if not isinstance(data, dict):
            log(f"[WARN] 업데이트 확인 응답 형식 이상: {data!r}")
            return
    except Exception as e:
        log(f"[WARN] 업데이트 확인 실패 (네트워크): {e}")
        return

    expected_sha256 = str(data.get("sha256", "")).lower().strip()
    download_url    = str(data.get("url", "")).strip()

    if not expected_sha256 or not download_url:
        log("[INFO] 버전 정보 없음 — 업데이트 건너뜀")
        return

    current_sha256 = sha256_of(MASTER_PATH)
    if current_sha256 == expected_sha256:
        log("[INFO] 최신 버전 확인됨 — 업데이트 불필요")
        return

    # Master가 이미 실행 중이면 exe 교체 시 파일 잠금으로 실패함 — 이번 회차는 건너뛰고
    # 다음 재시작(PC 재부팅) 때 다시 시도. 실행 중인 주문 처리를 방해하지 않기 위함.
    if is_master_running():
        log("[WARN] Master 실행 중 — 이번 업데이트는 건너뜀 (다음 재시작 시 재시도)")
        return

    log(f"[INFO] 업데이트 감지 — 다운로드 시작")
    win, lbl = make_progress_window()

    try:
        # 다운로드
        lbl.config(text="새 버전 다운로드 중...")
        win.update()

        h = hashlib.sha256()
        with requests.get(download_url, stream=True, timeout=90) as r:
            r.raise_for_status()
            with open(TEMP_PATH, 'wb') as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
                    h.update(chunk)
                    win.update()

        # 검증
        if h.hexdigest().lower() != expected_sha256:
            log("[ERR] SHA256 불일치 — 업데이트 취소")
            os.remove(TEMP_PATH)
            win.destroy()
            return

        # 교체
        lbl.config(text="업데이트 적용 중...")
        win.update()

        if os.path.exists(MASTER_PATH):
            shutil.copy2(MASTER_PATH, BACKUP_PATH)
        shutil.move(TEMP_PATH, MASTER_PATH)

        log("[INFO] 업데이트 완료")
        win.destroy()

    except Exception as e:
        log(f"[ERR] 업데이트 실패: {e}")
        try:
            win.destroy()
        except Exception:
            pass
        # 실패해도 기존 Master 실행
        if os.path.exists(TEMP_PATH):
            try:
                os.remove(TEMP_PATH)
            except Exception:
                pass
        # 사용자에게 알림 — 로그 파일만 남기면 아무도 원인을 모르고 계속 구버전 사용
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning(
                "런치팝 업데이트 실패",
                f"새 버전 적용에 실패했습니다. 기존 버전으로 계속 실행합니다.\n"
                f"문제가 반복되면 관리자에게 문의하세요.\n\n오류: {e}")
            root.destroy()
        except Exception:
            pass


# ── Master 실행 ───────────────────────────────────────────
def launch_master():
    if is_master_running():
        log("[INFO] Master 이미 실행 중 — 중복 실행 방지를 위해 새로 띄우지 않음")
        return

    if not os.path.exists(MASTER_PATH):
        log("[ERR] LunchPop_Master.exe 없음")
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "런치팝 오류",
            f"LunchPop_Master.exe를 찾을 수 없습니다.\n"
            f"폴더를 확인하거나 관리자에게 문의하세요.\n\n경로: {BASE_DIR}")
        root.destroy()
        return

    log(f"[INFO] Master 실행: {MASTER_PATH}")
    subprocess.Popen([MASTER_PATH])


# ── 진입점 ────────────────────────────────────────────────
if __name__ == '__main__':
    # 중복 실행 방지 (자동시작 + 수동 재실행이 겹쳐서 임시파일이 꼬이는 것 방지)
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "LunchPopLauncher_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        log("[INFO] Launcher 이미 실행 중 — 종료")
        sys.exit(0)

    log(f"=== Launcher v{LAUNCHER_VERSION} 시작 ===")
    set_autostart()
    try:
        check_and_update()
    except Exception as e:
        # 업데이트 로직에서 예상 못한 예외가 나도 Master는 반드시 실행되어야 함
        log(f"[ERR] check_and_update 예외: {e}")
    launch_master()
    log("=== Launcher 종료 ===")
