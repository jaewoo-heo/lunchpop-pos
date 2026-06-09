# 🍱 LunchPop POS Notifier

> 공유주방 케이터링 서비스 **런치팝**의 입점 매장용 자동 주문 수신 및 영수증 출력 프로그램

[![GitHub release](https://img.shields.io/github/v/release/jaewoo-heo/lunchpop-pos)](https://github.com/jaewoo-heo/lunchpop-pos/releases)
[![Python](https://img.shields.io/badge/Python-3.11-blue)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)](https://github.com/jaewoo-heo/lunchpop-pos/releases)

---

## 📌 프로젝트 개요

공유주방(키친밸리)에 입점한 매장을 대상으로, 점심 구독형 케이터링 서비스 런치팝의 주문을 자동으로 수신하고 열영수증 프린터로 출력하는 Windows 데스크톱 애플리케이션입니다.

Google Apps Script(GAS)를 백엔드로 활용하여 별도 서버 없이 Google Sheets를 데이터베이스로 사용합니다.

---

## ✨ 주요 기능

| 기능 | 설명 |
|------|------|
| **자동 주문 수신** | GAS 연동으로 60초마다 신규 주문 폴링 |
| **자동 영수증 출력** | 열영수증 프린터 (win32print / Serial COM) 지원 |
| **플로팅 대시보드** | 항상 위에 표시되는 미니 상태 바 (드래그 이동 가능) |
| **주문 리스트** | 당일 주문 현황 실시간 조회 및 재출력 |
| **자동 업데이트** | SHA256 기반 무결성 검증 후 자동 다운로드 적용 |
| **시스템 트레이** | 백그라운드 실행, 더블클릭으로 대시보드 복원 |
| **자동 시작** | Windows 부팅 시 자동 실행 (레지스트리 등록) |
| **스레드 감시** | 폴링 스레드 비정상 종료 감지 및 자동 재시작 |

---

## 🏗 아키텍처

```
[Google Sheets] ←── 주문 데이터 입력
      ↕ GAS Web App (fetchV2 / markDone / log / checkUpdate)
      ↕
[LunchPop_Launcher.exe]  ← PC 시작 시 자동 실행
  SHA256 비교 → 업데이트 시 Master 자동 교체
      ↓
[LunchPop_Master.exe]    ← 주문 수신 + 영수증 출력
      ↓
  열영수증 프린터

[GitHub Actions] ── v* 태그 push → 자동 빌드 → Release 업로드
```

---

## 📁 파일 구조

```
lunchpop-pos/
├── .github/
│   └── workflows/
│       └── build.yml           # GitHub Actions 자동 빌드 (Master + Launcher + Setup)
├── assets/
│   ├── logo.ico                # 앱 아이콘
│   └── alarm.wav               # 신규 주문 알림음
├── docs/
│   └── index.html              # GitHub Pages 설치 페이지
├── lunchpop_master.py          # 주문 수신 + 인쇄 메인 앱 (v4.x)
├── lunchpop_launcher.py        # 업데이트 관리 + Master 실행 (v1.x)
├── lunchpop_setup.py           # 최초 설치 인스톨러 (v1.x)
├── setup_admin.manifest        # UAC 관리자 권한 요청 (Setup용)
├── gas_server.js               # GAS 백엔드 코드 (v2.x)
└── README.md
```

---

## 🚀 설치 방법 (입점 매장)

### 설치 페이지 접속

```
https://jaewoo-heo.github.io/lunchpop-pos/
```

1. `LunchPop_Setup.exe` 다운로드
2. 실행 (UAC 관리자 권한 승인)
3. 자동으로 `C:\LunchPop\` 에 설치, 바탕화면 바로가기 생성
4. 매장명 및 프린터 설정 후 완료

> ⚠️ Windows SmartScreen 경고 시: **"추가 정보" → "실행"** 클릭

---

## 🔄 버전 릴리즈 방법 (관리자)

```bash
# 코드 수정 후
git add .
git commit -m "feat: 변경 내용"
git tag v4.x
git push origin main
git push origin v4.x
```

GitHub Actions가 자동으로 세 파일을 빌드하여 Release에 업로드합니다.

이후 Google Sheets **버전관리** 시트 업데이트:

| 셀 | 값 |
|----|----|
| `A2` | 새 버전 번호 (예: `4.2`) |
| `B2` | Release의 `LunchPop_Master.exe` 다운로드 URL |
| `C2` | Release 본문에 표시된 Master SHA256 |

→ 입점 매장 PC 재시작 시 Launcher가 자동으로 최신 Master를 적용합니다.

---

## 🛠 개발 환경

```bash
# 의존성 설치
pip install requests pywin32 pystray Pillow pyserial customtkinter pywin32-ctypes

# Master 실행
python lunchpop_master.py

# 빌드 (로컬)
python -m PyInstaller --onefile --noconsole --name "LunchPop_Master" \
  --icon "assets/logo.ico" \
  --add-data "assets/alarm.wav;." \
  --add-data "assets/logo.ico;." \
  lunchpop_master.py
```

---

## 📊 GAS 스프레드시트 구조

### data 시트 (A~P열)

| 열 | 내용 |
|----|------|
| B | 고객명 |
| C | 메뉴명 |
| D | 수량 |
| E | 매장명 |
| G | 예약일 (필터 기준) |
| H | 주문번호 |
| I | 배달 시간 |
| K | 대기 여부 (isQueued) |
| L | 인쇄 완료 (isPrinted) |
| P | 서비스 날짜 기준 (P2 셀) |

### 기타 시트

| 시트명 | 용도 |
|--------|------|
| `매장목록` | 입점 매장명 목록 |
| `버전관리` | 버전 번호 / Master URL / SHA256 |
| `log` | 원격 디버그 로그 (2000행 초과 시 자동 정리) |

---

## 🔒 보안

- GAS Web App URL은 난수화된 엔드포인트로 외부 추측 불가
- 매장 설정은 로컬 `lunchpop_config.json`에 저장
- 업데이트 파일 SHA256 무결성 검증 후 적용

---

## 📝 버전 히스토리

| 버전 | 주요 변경사항 |
|------|-------------|
| v4.2 | LunchPop_Setup.exe 인스톨러 추가, 설치 경로 C:\LunchPop\ 통일 |
| v4.1 | print_raw_text 락 버그 수정, run_auto_updater 제거, Launcher 로그 로테이션 |
| v4.0 | Launcher/Master 분리 아키텍처 도입, BAT 기반 자동 업데이트 제거 |
| v3.6 | GAS 날짜 필터 버그 수정(한국어 요일 처리), 주문 목록 캐시 버그 수정 |
| v3.5 | 스레드 watchdog, 빈 orderNo 중복 인쇄 버그 수정, 위젯 메모리 최적화 |
| v3.2 | GAS fetchV2 연동, rowIndex O(1) markDone, SHA256 업데이트 검증 |

---

## 👤 개발자

**Jaewoo Heo**
- 기획 / 개발 / 운영 — 런치팝 케이터링 서비스 전반
