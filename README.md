# 🍱 LunchPop POS Notifier

> 공유주방 케이터링 서비스 **런치팝**의 입점 매장용 자동 주문 수신 및 영수증 출력 프로그램

[![GitHub release](https://img.shields.io/github/v/release/YOUR_USERNAME/lunchpop-pos)](https://github.com/YOUR_USERNAME/lunchpop-pos/releases)
[![Python](https://img.shields.io/badge/Python-3.11-blue)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)](https://github.com/YOUR_USERNAME/lunchpop-pos/releases)

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
| **자동 업데이트** | GAS 버전 확인 → GitHub Release에서 자동 다운로드 및 재시작 |
| **인쇄 실패 알림** | 프린터 오류 시 시각적 배너 + 경고음 |
| **시스템 트레이** | 백그라운드 실행, 더블클릭으로 대시보드 복원 |
| **자동 시작** | Windows 부팅 시 자동 실행 (레지스트리 등록) |

---

## 🛠 기술 스택

**클라이언트 (Python)**
- `tkinter` — 플로팅 대시보드 UI
- `win32print` / `pyserial` — 열영수증 프린터 출력 (드라이버 설치 / Serial 직결 모두 지원)
- `pystray` — 시스템 트레이 아이콘
- `requests` — GAS 통신 및 자동 업데이트 다운로드
- `threading` / `Lock` — 멀티스레드 안전 처리
- `PyInstaller` — 단일 exe 빌드

**백엔드 (Google Apps Script)**
- Google Sheets를 DB로 활용
- `doGet` Web App으로 REST-like API 구현
- 주문 조회, 인쇄 완료 처리, 원격 로깅, 버전 관리

**CI/CD (GitHub Actions)**
- `v*` 태그 push 시 자동 빌드
- Windows 환경에서 PyInstaller 빌드 후 GitHub Release 자동 생성

---

## 🏗 아키텍처

```
[Google Sheets] ←─── 주문 데이터 입력
      ↕ GAS Web App
[LunchPop POS]  ──→  열영수증 프린터
  (Windows exe)

[GitHub Actions] ──→ exe 자동 빌드 → GitHub Release
[LunchPop POS]   ──→ 업데이트 감지 → 자동 다운로드 및 재시작
```

---

## 📁 파일 구조

```
lunchpop-pos/
├── .github/
│   └── workflows/
│       └── build.yml        # GitHub Actions 자동 빌드
├── assets/
│   ├── logo.ico             # 앱 아이콘
│   └── alarm.wav            # 신규 주문 알림음
├── lunchpop_master.py       # 메인 소스코드
├── gas_server.js            # GAS 백엔드 코드
└── README.md
```

---

## 🚀 설치 및 실행

### 일반 사용자 (입점 매장)

1. [Releases](https://github.com/YOUR_USERNAME/lunchpop-pos/releases/latest)에서 `LunchPop_Master.exe` 다운로드
2. 원하는 폴더에 저장 후 실행
3. 초기 설정 마법사에서 매장명 및 프린터 선택
4. 완료 — 이후 업데이트는 자동으로 처리됩니다

> ⚠️ 최초 실행 시 Windows SmartScreen 경고가 뜰 수 있습니다.
> **"추가 정보" → "실행"** 클릭

### 개발 환경

```bash
# 의존성 설치
pip install requests pywin32 pystray Pillow pyserial

# 실행
python lunchpop_master.py

# 빌드
python -m PyInstaller --onefile --noconsole --name "LunchPop_Master" \
  --icon "assets/logo.ico" \
  --add-data "assets/alarm.wav;." \
  --add-data "assets/logo.ico;." \
  lunchpop_master.py
```

---

## 🔄 업데이트 배포 방법

```bash
# 코드 수정 후
git add .
git commit -m "feat: 변경 내용 설명"

# 새 버전 태그 push → GitHub Actions 자동 빌드 시작
git tag v3.3
git push origin main
git push origin v3.3
```

이후 Google Sheets **버전관리** 시트에서:
- `A2` = 새 버전 번호 (예: `3.3`)
- `B2` = GitHub Release asset URL

→ 실행 중인 모든 입점 매장 프로그램이 다음 업데이트 확인 시 자동 업데이트됩니다.

---

## 📊 GAS 스프레드시트 구조

| 시트명 | 용도 |
|--------|------|
| `data` | 주문 데이터 (A~L열) |
| `매장목록` | 입점 매장명 목록 |
| `버전관리` | 버전 번호, 다운로드 URL, sha256 |
| `log` | 원격 디버그 로그 |

---

## 🔒 보안

- GAS Web App URL은 난수화된 엔드포인트로 외부 추측 불가
- 프린터 설정 및 매장 정보는 로컬 `lunchpop_config.json`에 저장
- 업데이트 파일 무결성 sha256 검증 지원

---

## 📝 버전 히스토리

| 버전 | 주요 변경사항 |
|------|-------------|
| v3.2 | 스레드 안전성 개선, 인쇄 실패 알림, 자동 업데이트 sha256 검증, UI 개선 |
| v3.1 | GAS fetchV2 연동, 대시보드 UI 추가 |
| v3.0 | 초기 안정화 버전 |

---

## 👤 개발자

**Jaewoo Heo**
- 기획 / 개발 / 운영 — 런치팝 케이터링 서비스 전반
