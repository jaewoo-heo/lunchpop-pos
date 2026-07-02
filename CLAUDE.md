# 런치팝 알리미 — 프로젝트 가이드

공유주방 입점 매장용 자동 주문 수신 + 열영수증 출력 Windows 앱.
GAS(Google Apps Script)를 백엔드로, GitHub Actions로 CI/CD 운영 중.

---

## 실행 파일 구조 (3개)

| 파일 | 역할 |
|------|------|
| `LunchPop_Setup.exe` | 최초 설치용. `C:\LunchPop\` 생성, Launcher 다운로드, 바탕화면 바로가기 등록 |
| `LunchPop_Launcher.exe` | PC 시작 시 자동 실행. GAS SHA256 비교 → Master 자동 업데이트 → Master 실행 후 종료 |
| `LunchPop_Master.exe` | 실제 앱. 60초 폴링으로 주문 수신 + 영수증 인쇄 |

입점사에 배포하는 것: `LunchPop_Setup.exe` 하나 (나머지는 자동 다운로드)

---

## 소스 파일

| 파일 | 설명 |
|------|------|
| `lunchpop_master.py` | Master 소스. `CURRENT_VERSION = 4.x` |
| `lunchpop_launcher.py` | Launcher 소스. `LAUNCHER_VERSION = "1.x"` |
| `lunchpop_setup.py` | Setup 소스. 설치 경로 `C:\LunchPop\` 하드코딩 |
| `gas_server.js` | GAS 백엔드. 배포 후 URL 변경 시 Launcher/Setup 재빌드 필요 |
| `setup_admin.manifest` | Setup UAC 관리자 권한 요청용 매니페스트 |
| `docs/index.html` | GitHub Pages 설치 페이지 |

---

## GAS 정보

```
URL: https://script.google.com/macros/s/AKfycbzG_q6m1svwhZZny0DAz1s29qEGfVUO_gdnUOelX5QmIKPjTM8kvYjYhro_b7b_7w/exec
```

**액션 목록:**
- `fetchV2` — 당일 주문 목록 (P2 셀 날짜 기준 필터)
- `markDone` — 인쇄 완료 처리 (rowIndex로 O(1))
- `log` — 원격 로그 기록
- `checkUpdate` — 버전/SHA256/URL 확인 (Launcher가 사용)

**스프레드시트 버전관리 시트:**
- A2: 버전 번호
- B2: Master.exe 다운로드 URL
- C2: Master.exe SHA256 ← 이걸 바꿔야 자동 업데이트 트리거됨

---

## 릴리즈 방법

```bash
git add .
git commit -m "feat: 변경 내용"
git tag v4.x
git push origin main
git push origin v4.x
```

GitHub Actions가 Master + Launcher + Setup 세 파일 자동 빌드.
빌드 완료 후 Release 본문에서 Master SHA256 확인 → 버전관리 시트 B2/C2 업데이트.

---

## 주요 글로벌 변수 (lunchpop_master.py)

```python
CURRENT_VERSION = 4.x
GLOBAL_ORDERS = []          # 현재 주문 목록
printed_ids = set()         # 인쇄 완료 orderNo 추적 (메모리, 재시작 시 초기화)
orders_lock                 # GLOBAL_ORDERS 스레드 안전 접근용
_print_lock                 # 동시 인쇄 방지 (print_raw_text 전체 감싸고 있음)
_polling_thread             # 60초 폴링 스레드 (watchdog이 감시)
```

---

## 알려진 설계 결정

- **printed_ids 영속화 불필요**: 재시작 후 서버 `isPrinted=true` 값으로 자동 복구됨
- **Launcher 업데이트 거의 없음**: GAS URL 변경 또는 Launcher 버그 시에만 필요
- **Master SHA256 기반 업데이트**: 버전 번호가 아닌 SHA256으로 감지 (A2는 기록용)
- **GAS 날짜 필터**: P2 셀과 G열 날짜를 `\D` 정규식으로 숫자만 추출해 비교

---

## 설치 페이지

```
https://jaewoo-heo.github.io/lunchpop-pos/
```
