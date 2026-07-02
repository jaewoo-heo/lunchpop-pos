# 런치팝 알리미 - 전체 기능 목록

분석 날짜: 2026-07-02 | 버전: Master 4.2, Launcher 1.0, Setup 1.0

---

## 📊 구성요소별 기능 요약

| 구성요소 | 역할 | 주요 기능 수 |
|---------|------|-----------|
| **Master.exe** | 메인 앱 | 15개 기능군 |
| **Launcher.exe** | 업데이트 관리 | 6개 기능군 |
| **Setup.exe** | 설치 프로그램 | 5개 기능군 |
| **GAS 백엔드** | 서버 | 6개 API 엔드포인트 |
| **GitHub Actions** | CI/CD | 자동 빌드/배포 |

---

## 🚀 Master.exe (v4.2) - 메인 애플리케이션

### 1️⃣ 초기 설정 및 구성
- ✅ 초기 설정 마법사 (매장명, 프린터 선택)
- ✅ GAS 백엔드에서 동적 매장 목록 로드
- ✅ 기본값 폴백 지원
- ✅ `lunchpop_config.json` 원자적 저장
- ✅ Windows 레지스트리 자동시작 등록

### 2️⃣ 사용자 인터페이스 (CustomTkinter)

#### 📱 스마트 대시보드
- 실시간 주문 개수 표시 (전체/완료/대기 중)
- 동기화 상태 지시기 (연결 시간 표시)
- 색상 코드 상태 표시 (녹색=연결, 빨강=오류)
- 드래그 가능 윈도우 (위치 자동 저장)
- 프린트 실패 에러 배너

#### 📋 주문 목록 윈도우
- 역시간순 주문 표시
- 상태별 색상 구분 (대기=노랑, 완료=녹색, 실패=빨강)
- 주문별 재출력 기능
- 자동 스크린 위치 조정 (대시보드 위/아래 동적 배치)
- 30초 자동 새로고침 (캐싱 적용)

#### ⚙️ 설정 대화상자 (탭 인터페이스)
- **설정 탭**: 매장명, 프린터 선택, 자동시작 체크박스
- **로그 탭**: 디버그 로그 마지막 60줄 표시
- 매장 드롭다운 (백그라운드 로딩)
- 테스트 프린트 기능
- 로그 파일 뷰어 및 새로고침

### 3️⃣ 프린터 기능

#### 🖨️ 트리플 프린터 지원
1. **Windows 기본 프린터** - win32print API
2. **시리얼 COM 포트** - COM1~12, 9600 보드
3. **네트워크 프린터** - 이름으로 지정

#### 🧾 영수증 포매팅
- ESC/POS 명령어 지원
- 정렬 제어 (중앙/좌측)
- 글자 크기 제어 (큼/보통)
- 종이 절단 명령
- CP949 인코딩 (한글 지원)
- 포함 정보:
  - 매장명, 고객명, 주문#
  - 메뉴, 배달시간
  - 조리시간 (배달 30분 전)
  - 재출력 태그 "[ 재출력 ]"

#### 🔒 동시성 제어
- `_print_lock` 뮤텍스로 동시 인쇄 방지
- 3회 재시도 (1초 간격)
- 종이 상태 감지 (COM 포트)
- 테스트 프린트 검증 기능

### 4️⃣ 주문 처리 및 폴링

#### 🔄 60초 폴링 루프
- GAS `fetchV2` 액션으로 주문 조회
- 스마트 상태 추적:
  - `GLOBAL_ORDERS` (스레드 안전)
  - `printed_ids` 중복 제거
  - `isPrinted`, `isQueued` 상태
- 일일 정리 (오전 4시)

#### 🤖 자동 인쇄 로직
- 새로운 queued 주문 감지
- 자동 프린트
- `rowIndex`로 GAS에 완료 처리 (O(1))

### 5️⃣ 사운드 및 알림
- 🔊 새 주문 시 음성 알람 (alarm.wav)
- 중복 알람 방지 (60초 내)
- 프린트 오류 시 시스템 비프음
- 완료 주문 알림 자동 제거

### 6️⃣ 로깅 시스템
- 로컬 디버그 로그 (5MB 회전)
- GAS 원격 로깅
- 타임스탐프 기록
- 선택적 DEBUG/WARNING/ERROR 레벨
- 형식: `[YYYY-MM-DD HH:MM:SS] message`

### 7️⃣ 스레드 아키텍처

| 스레드 | 역할 | 주기 |
|--------|------|------|
| **Polling** | 주문 조회 + 자동 프린트 | 60초 |
| **Watchdog** | Polling 스레드 모니터링 | 120초 |
| **Tray** | 시스템 트레이 관리 | 이벤트 기반 |
| **Main** | Tkinter UI 루프 | 실시간 |

### 8️⃣ 스레드 안전성
- `orders_lock` - GLOBAL_ORDERS 접근 제어
- `_print_lock` - 프린터 작업 동기화
- `root.after(0, callback)` - 크로스스레드 UI 업데이트

### 9️⃣ 시스템 트레이
- 아이콘 표시 (매장명)
- 우클릭 메뉴:
  - 매장명 (읽기 전용)
  - 버전 정보
  - 대시보드 복구
  - 프로그램 종료

### 🔟 오류 처리 및 복구
- 프린트 실패 감지 (대시보드 알림)
- 프린터 오프라인 감지
- 네트워크 오류 (지수 백오프)
- 뮤텍스 기반 단일 인스턴스 보호
- 백업 파일 자동 정리

### 1️⃣1️⃣ 네트워크 통신
- 공통 `fetch_with_retry()` 헬퍼
- 기본값: 3회 재시도, 20초 타임아웃
- 지수 백오프: 1s, 2s, 4s
- HTTP 오류 로깅

---

## 🔄 Launcher.exe (v1.0) - 업데이트 관리자

### 1️⃣ 버전 확인 및 업데이트
- GAS `checkUpdate` 조회:
  - 버전 번호, 다운로드 URL, SHA256
- SHA256 기반 업데이트 감지
- 파일 무결성 검증

### 2️⃣ 업데이트 프로세스 흐름
```
GAS 업데이트 정보 조회
    ↓
로컬 Master.exe SHA256 계산
    ↓
SHA256 비교 (다르면 다운로드)
    ↓
새 파일 검증 + 백업 생성
    ↓
원자적 파일 교체
```

### 3️⃣ 사용자 피드백
- 진행 윈도우:
  - 애니메이션 진행 바
  - 상태 레이블 ("새 버전 확인 중...", "다운로드 중...", "적용 중...")
  - MB 단위 진행률

### 4️⃣ 오류 처리
- 네트워크 타임아웃 (15초 체크, 90초 다운로드)
- SHA256 불일치 감지
- 오류 시 기존 Master.exe 폴백
- 자동 임시 파일 정리

### 5️⃣ 레지스트리 관리
- Windows 자동시작 등록
- 매번 재설정 (멱등성)

### 6️⃣ 로깅
- `launcher_debug.log` (5MB 회전)
- 타임스탐프 기록

---

## 📦 Setup.exe (v1.0) - 설치 프로그램

### 1️⃣ 설치 워크플로우
```
기존 설치 확인 (C:\LunchPop\)
    ↓
설치 디렉토리 생성
    ↓
Launcher.exe 다운로드 (GitHub Release)
    ↓
바탕화면 바로가기 생성
    ↓
Windows 시작 레지스트리 등록
    ↓
Launcher 실행
```

### 2️⃣ 사전 설치 감지
- 기존 설치 확인
- 재설치 여부 사용자 확인
- 거부 시 기존 실행 후 종료

### 3️⃣ 파일 작업
- `C:\LunchPop\` 디렉토리 생성 (고정 경로)
- `LunchPop_Launcher.exe` 다운로드
- 바탕화면 바로가기: "런치팝 알리미.lnk"

### 4️⃣ 바로가기 생성
- COM 인터페이스 활용
- 작업 디렉토리 설정
- 아이콘 복사
- 설명: "런치팝 알리미"

### 5️⃣ 사용자 인터페이스
- 420x220px 중앙 윈도우
- 애니메이션 진행 바
- 다중 라인 상태 표시:
  - 주 상태, 상세 메시지
  - 폴더 경로, URL, 진행률
- 단계: 폴더 생성 → 다운로드 → 바로가기 → 레지스트리 → 완료

---

## 🌐 GAS 백엔드 (gas_server.js v2.3)

### 📊 시트 구조

#### data 시트 (주문 데이터)
| 열 | 내용 |
|----|------|
| B | 고객명 |
| C | 메뉴명 |
| D | 수량 |
| E | 매장명 |
| G | 예약일자 |
| H | 주문번호 |
| I | 배달시간 |
| K | isQueued (TRUE/FALSE) |
| L | isPrinted (TRUE/FALSE) |
| P | 서비스 날짜 (필터 기준) |

#### 매장목록 시트
- A2 이하: 매장명 목록

#### 버전관리 시트
| 셀 | 내용 |
|----|------|
| A2 | 버전 번호 (예: "4.2") |
| B2 | Master.exe 다운로드 URL |
| C2 | SHA256 해시 |
| D2 | GitHub 토큰 (선택) |

#### log 시트
- 감사 로그, 자동 회전

### 🔌 API 엔드포인트

#### 1. `checkUpdate` - 버전 및 업데이트 정보
```
반환: version, url, sha256, gh_token
사용자: Launcher
```

#### 2. `getStores` - 매장 목록
```
반환: 버전관리 시트의 매장명 배열
사용자: Setup 마법사, 설정 UI
```

#### 3. `fetchV2` - 메인 주문 폴링 (신규)
```
파라미터: action=fetchV2, storeName=value
날짜 필터: P2(서비스 날짜) vs G열(예약일자)
정규화: \D 로 숫자만 추출 (2026.06.09 == 2026/06/09)

반환:
  - rowIndex (rowIndex 기반 O(1) markDone)
  - customerName, menuName, quantity
  - storeName, address, resDate
  - orderNo, deliveryTime
  - isQueued, isPrinted (불린 변환)
```

#### 4. `fetch` - 레거시 엔드포인트
```
필터: isQueued=TRUE AND isPrinted≠TRUE
반환: 기본 주문 정보 (rowIndex 없음)
목적: 이전 클라이언트 호환성
```

#### 5. `markDone` - 인쇄 완료 처리
```
파라미터: rowIndex (신규) 또는 orderNo (레거시)

동작:
  - rowIndex ≥ 2: K2:L2 업데이트 (O(1) 직접 접근)
  - orderNo: H열에서 검색 (선형 검색)
  
반환: success/not_found/error
```

#### 6. `log` - 원격 로깅
```
파라미터: action=log, storeName, logMsg

동작:
  - log 시트에 타임스탐프, 매장명, 메시지 추가
  - 행 > 2000 시 자동 회전 (2-501행 삭제)
  
타임스탐프 형식: "yyyy/MM/dd HH:mm:ss" (GMT+9)
```

### ⚡ 성능 최적화
- rowIndex 기반 O(1) 업데이트
- 날짜 정규화 정규식 (`\D` = 비숫자)
- 배치 로그 삭제 (500행씩)
- getDisplayValues() (렌더링된 값 사용)

### 🔔 이벤트 핸들러

#### onEdit 트리거 (P2 변경)
```
발동: P2(data 시트) 수정 시
동작: K2:L 초기화 (isQueued & isPrinted)
목적: 서비스 날짜 변경 시 상태 리셋
```

---

## 🤖 GitHub Actions 자동화

### 빌드 환경
- **OS**: Windows Latest
- **Python**: 3.11
- **빌드 도구**: PyInstaller

### 빌드 파이프라인

#### 📥 체크아웃 및 환경 설정
```yaml
- actions/checkout@v4
- actions/setup-python@v5
- pip install: pyinstaller, requests, pywin32, pystray, Pillow, pyserial, customtkinter
```

#### 🔨 Master.exe 빌드
```
옵션: --onefile --noconsole
아이콘: assets/logo.ico
번들 데이터:
  - assets/alarm.wav
  - assets/logo.ico
숨겨진 임포트: win32print, win32api, pywintypes, win32com.client
출력: dist/LunchPop_Master.exe
```

#### 🚀 Launcher.exe 빌드
```
옵션: --onefile --noconsole
아이콘: assets/logo.ico
출력: dist/LunchPop_Launcher.exe
```

#### 📦 Setup.exe 빌드
```
옵션: --onefile --noconsole
아이콘: assets/logo.ico
매니페스트: setup_admin.manifest (UAC 관리자 권한)
숨겨진 임포트: win32com.client, pywintypes
출력: dist/LunchPop_Setup.exe
```

#### 🔐 SHA256 계산
```
Master.exe SHA256 계산
Launcher.exe SHA256 계산
GITHUB_OUTPUT에 저장
```

#### 📢 GitHub Release 생성
```
이름: "런치팝 알리미 vX.X"
바디 템플릿: 변경사항, 설치 지침, 버전 관리 가이드
업로드 아티팩트:
  - LunchPop_Setup.exe
  - LunchPop_Launcher.exe
  - LunchPop_Master.exe
설정: 최신 릴리즈 표시
```

### 트리거
- 태그 푸시: `v*` 패턴 (예: `v4.2`)

---

## 📋 GAS 엔드포인트 (고정)

```
https://script.google.com/macros/s/AKfycbzG_q6m1svwhZZny0DAz1s29qEGfVUO_gdnUOelX5QmIKPjTM8kvYjYhro_b7b_7w/exec
```

---

## ⏱️ 타이밍 상수

| 항목 | 값 |
|------|-----|
| 폴링 간격 | 60초 |
| Watchdog 체크 | 120초 |
| 하트비트 로그 | 3600초 (1시간) |
| 일일 정리 | 오전 4시 |
| 리스트 새로고침 | 30초 |
| 경고 중복 방지 | 60초 |

---

## 🗂️ 파일 경로

| 구분 | 경로 |
|------|------|
| 설치 | `C:\LunchPop\` |
| 설정 파일 | `lunchpop_config.json` |
| Master 로그 | `system_debug.log`, `system_debug.old.log` |
| Launcher 로그 | `launcher_debug.log` |
| 로그 회전 | 5MB |

---

## 🔄 배포 흐름도

```
개발자: git tag v4.x
    ↓
GitHub Actions 트리거 (tag 푸시)
    ↓
Windows에서 3개 실행파일 빌드
    ↓
SHA256 계산 및 GitHub Release 생성
    ↓
관리자: Google Sheets 버전관리 시트 업데이트
    A2 = 버전 번호
    B2 = Master.exe URL
    C2 = SHA256 해시
    ↓
최종 사용자: PC 재시작
    ↓
Launcher 실행 → GAS 업데이트 확인
    ↓
SHA256 불일치 → Master.exe 다운로드 및 교체
    ↓
Master 시작 → 매장명 + 프린터 설정 입력
    ↓
60초 폴링으로 주문 수신
    ↓
새 주문 감지 → 자동 프린트
```

---

## 📈 코드 규모

| 파일 | 줄 수 | 언어 |
|------|------|------|
| lunchpop_master.py | 1,100+ | Python |
| lunchpop_launcher.py | 150+ | Python |
| lunchpop_setup.py | 37+ | Python |
| gas_server.js | 178 | JavaScript |
| docs/index.html | 193 | HTML |
| build.yml | 63 | YAML |
| **합계** | **1,720+** | - |

---

## 🎯 주요 설계 결정

| 결정 | 이유 |
|------|------|
| SHA256 기반 업데이트 | 버전 번호보다 정확한 변경 감지 |
| rowIndex O(1) 업데이트 | 큰 스프레드시트에서 성능 최적화 |
| printed_ids 메모리 저장 | 서버의 isPrinted로 자동 복구되어 영속화 불필요 |
| 60초 폴링 | 배터리/네트워크 효율성과 반응성의 균형 |
| 뮤텍스 기반 스레드 안전 | 간단하고 예측 가능한 동시성 제어 |
| 3회 자동 재시도 | 일시적 네트워크 오류 복구 |

---

**최종 업데이트**: 2026-07-02
