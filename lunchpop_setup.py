"""
런치팝 알리미 Setup v1.0
- 설치 폴더 자동 생성: %LOCALAPPDATA%\LunchPop\
- Launcher.exe 다운로드
- 바탕화면 바로가기 생성
- Windows 시작프로그램 등록
- Launcher 자동 실행 (Launcher가 Master 다운로드 처리)
"""
import os
import sys
import winreg
import hashlib
import requests
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

SETUP_VERSION = "1.0"
INSTALL_DIR   = os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Users\\Public"), "LunchPop")
LAUNCHER_NAME = "LunchPop_Launcher.exe"
LAUNCHER_URL  = "https://github.com/jaewoo-heo/lunchpop-pos/releases/latest/download/LunchPop_Launcher.exe"
LAUNCHER_PATH = os.path.join(INSTALL_DIR, LAUNCHER_NAME)
SHORTCUT_NAME = "런치팝 알리미.lnk"


# ── 진행 창 ────────────────────────────────────────────────
class SetupWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("런치팝 알리미 설치")
        self.root.geometry("420x220")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"420x220+{(sw-420)//2}+{(sh-220)//2}")

        tk.Label(self.root, text="🍱  런치팝 알리미 설치 중",
                 font=("맑은 고딕", 14, "bold")).pack(pady=(24, 6))

        self.status_var = tk.StringVar(value="설치를 시작합니다...")
        tk.Label(self.root, textvariable=self.status_var,
                 font=("맑은 고딕", 10), fg="#555").pack()

        self.pb = ttk.Progressbar(self.root, mode="indeterminate", length=360)
        self.pb.pack(pady=14)
        self.pb.start(12)

        self.detail_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.detail_var,
                 font=("맑은 고딕", 9), fg="#999").pack()

        self.root.update()

    def set_status(self, msg, detail=""):
        self.status_var.set(msg)
        self.detail_var.set(detail)
        self.root.update()

    def finish(self):
        self.pb.stop()
        self.pb.config(mode="determinate", value=100)
        self.root.update()

    def destroy(self):
        try:
            self.root.destroy()
        except Exception:
            pass


# ── 바탕화면 바로가기 생성 ─────────────────────────────────
def create_shortcut(target_path):
    try:
        import win32com.client
        desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
        shortcut_path = os.path.join(desktop, SHORTCUT_NAME)
        shell = win32com.client.Dispatch("WScript.Shell")
        sc = shell.CreateShortCut(shortcut_path)
        sc.Targetpath = target_path
        sc.WorkingDirectory = os.path.dirname(target_path)
        sc.IconLocation = target_path
        sc.Description = "런치팝 알리미"
        sc.save()
        return True
    except Exception as e:
        return False


# ── 시작프로그램 등록 ──────────────────────────────────────
def set_autostart(target_path):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "LunchPopAlrimi", 0, winreg.REG_SZ, f'"{target_path}"')
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


# ── Launcher 다운로드 ──────────────────────────────────────
def download_launcher(win):
    win.set_status("Launcher 다운로드 중...", LAUNCHER_URL)
    try:
        with requests.get(LAUNCHER_URL, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            with open(LAUNCHER_PATH, "wb") as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        mb = downloaded / (1024 * 1024)
                        win.set_status("Launcher 다운로드 중...",
                                       f"{mb:.1f} MB / {total/(1024*1024):.1f} MB")
                    win.root.update()
        return True
    except Exception as e:
        return False, str(e)


# ── 메인 설치 흐름 ─────────────────────────────────────────
def run_setup():
    win = SetupWindow()

    try:
        # 1. 폴더 생성
        win.set_status("설치 폴더 생성 중...", INSTALL_DIR)
        os.makedirs(INSTALL_DIR, exist_ok=True)

        # 2. Launcher 다운로드
        result = download_launcher(win)
        if result is not True:
            _, err = result
            win.destroy()
            messagebox.showerror("설치 실패", f"파일 다운로드 중 오류가 발생했습니다.\n\n{err}\n\n인터넷 연결을 확인하고 다시 시도해주세요.")
            return

        # 3. 바탕화면 바로가기
        win.set_status("바탕화면 바로가기 생성 중...")
        create_shortcut(LAUNCHER_PATH)

        # 4. 시작프로그램 등록
        win.set_status("시작프로그램 등록 중...")
        set_autostart(LAUNCHER_PATH)

        # 5. 완료
        win.finish()
        win.set_status("설치 완료!", f"설치 위치: {INSTALL_DIR}")
        win.root.update()

        import time
        time.sleep(1.2)
        win.destroy()

        messagebox.showinfo(
            "설치 완료",
            f"런치팝 알리미 설치가 완료되었습니다.\n\n"
            f"설치 위치: {INSTALL_DIR}\n\n"
            f"지금 바로 실행하시겠습니까?"
        )

        # 6. Launcher 실행
        subprocess.Popen([LAUNCHER_PATH])

    except Exception as e:
        win.destroy()
        messagebox.showerror("설치 오류", f"예기치 못한 오류가 발생했습니다.\n\n{e}")


# ── 진입점 ────────────────────────────────────────────────
if __name__ == "__main__":
    # 이미 설치되어 있으면 재설치 여부 확인
    if os.path.exists(LAUNCHER_PATH):
        root = tk.Tk()
        root.withdraw()
        answer = messagebox.askyesno(
            "런치팝 알리미",
            f"이미 설치되어 있습니다.\n\n"
            f"설치 위치: {INSTALL_DIR}\n\n"
            f"재설치하시겠습니까?"
        )
        root.destroy()
        if not answer:
            subprocess.Popen([LAUNCHER_PATH])
            sys.exit(0)

    run_setup()
