"""
런치팝 알리미 Setup v1.1
- 설치 폴더 자동 생성: C:\LunchPop\
- Launcher.exe 다운로드
- 바탕화면 바로가기 생성
- Windows 시작프로그램 등록
- Launcher 자동 실행 (Launcher가 Master 다운로드 처리)
"""
import os
import sys
import winreg
import requests
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

SETUP_VERSION = "1.1"
INSTALL_DIR   = r"C:\LunchPop"
LAUNCHER_NAME = "LunchPop_Launcher.exe"
LAUNCHER_URL  = "https://github.com/jaewoo-heo/lunchpop-pos/releases/latest/download/LunchPop_Launcher.exe"
LAUNCHER_PATH = os.path.join(INSTALL_DIR, LAUNCHER_NAME)
LAUNCHER_TEMP_PATH = os.path.join(INSTALL_DIR, "LunchPop_Launcher.download.tmp")
SHORTCUT_NAME = "런치팝 알리미.lnk"
MIN_VALID_EXE_BYTES = 500 * 1024  # 정상 빌드본은 수 MB — 이보다 작으면 손상된 파일로 간주


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
    """임시 파일에 먼저 받고 크기 검증 후 최종 위치로 이동.
    중간에 끊겨도 LAUNCHER_PATH에는 손상된 파일이 남지 않도록 함."""
    win.set_status("Launcher 다운로드 중...", LAUNCHER_URL)
    try:
        with requests.get(LAUNCHER_URL, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            with open(LAUNCHER_TEMP_PATH, "wb") as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        mb = downloaded / (1024 * 1024)
                        win.set_status("Launcher 다운로드 중...",
                                       f"{mb:.1f} MB / {total/(1024*1024):.1f} MB")
                    win.root.update()

        if downloaded < MIN_VALID_EXE_BYTES:
            raise ValueError(f"다운로드된 파일이 너무 작습니다 ({downloaded} bytes) — 손상되었거나 중단됨")

        # os.replace()는 Windows에서도 대상 파일을 원자적으로 덮어쓰므로
        # 미리 삭제할 필요 없음 (미리 지우면 그 사이 파일이 없는 순간이 생김)
        os.replace(LAUNCHER_TEMP_PATH, LAUNCHER_PATH)
        return True
    except Exception as e:
        if os.path.exists(LAUNCHER_TEMP_PATH):
            try:
                os.remove(LAUNCHER_TEMP_PATH)
            except Exception:
                pass
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
        shortcut_ok = create_shortcut(LAUNCHER_PATH)

        # 4. 시작프로그램 등록
        win.set_status("시작프로그램 등록 중...")
        autostart_ok = set_autostart(LAUNCHER_PATH)

        # 5. 완료
        win.finish()
        win.set_status("설치 완료!", f"설치 위치: {INSTALL_DIR}")
        win.root.update()

        import time
        time.sleep(1.2)
        win.destroy()

        # 자동시작 등록 실패 시 조용히 "완료"로 넘어가면 안 됨 —
        # 이 앱의 핵심 기능(PC 켜면 자동 실행)이 다음날부터 작동하지 않게 됨
        if not autostart_ok or not shortcut_ok:
            failed = []
            if not shortcut_ok:
                failed.append("바탕화면 바로가기")
            if not autostart_ok:
                failed.append("윈도우 자동시작 등록")
            messagebox.showwarning(
                "일부 설정 실패",
                f"런치팝은 설치되었지만 다음 항목이 실패했습니다:\n"
                f"- {', '.join(failed)}\n\n"
                f"{'자동시작이 등록되지 않으면 PC를 켜도 주문을 받지 못합니다. ' if not autostart_ok else ''}"
                f"관리자에게 문의하거나 설치를 다시 시도해주세요."
            )
        else:
            messagebox.showinfo(
                "설치 완료",
                f"런치팝 알리미 설치가 완료되었습니다.\n\n"
                f"설치 위치: {INSTALL_DIR}\n\n"
                f"확인을 누르면 바로 시작됩니다."
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
        # 이전 다운로드가 중간에 끊겨 손상된 파일로 남아있는 경우, 재설치 여부를
        # 묻지 않고 바로 재설치 진행 (사용자가 "아니오"를 눌러도 실행이 안 되는 상황 방지)
        if os.path.getsize(LAUNCHER_PATH) < MIN_VALID_EXE_BYTES:
            run_setup()
            sys.exit(0)

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
