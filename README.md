# LumiTrack

방탈출 매장의 공개 예약 페이지를 모니터링하고, 예약률과 예상 매출을 분석하는 개인용 매출 모니터링 도구입니다.

## 포함 기능

- Streamlit 기반 대시보드
- Playwright 기반 공개 예약 페이지 수집
- SQLite 데이터 저장
- APScheduler 자동 수집
- 매장/테마/지역/요일/시간대별 예약률 분석
- 예상 일매출/월매출 분석
- 매장별 매출 지도
- 장르별/매장별/테마별 리포트
- 투자자 리포트 화면
- PDF/HTML 다운로드
- dry-run 수집 테스트
- Windows 실행 파일 빌드용 코드
- 데모 모드용 SQLite 데이터

## 중요한 사용 원칙

이 프로그램은 공개적으로 접근 가능한 예약 현황만 개인용으로 확인하기 위한 도구입니다.

하지 않는 것:

- 로그인 우회
- 캡차 우회
- 결제 자동화
- 실제 예약 자동화
- 비공개 API 호출
- 과도한 반복 요청

각 사이트의 robots.txt와 이용약관은 사용자가 직접 확인해야 합니다.

## 설치 방법

Windows PowerShell 기준입니다.

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

## 실행 방법

대시보드 실행:

```powershell
streamlit run app.py
```

또는 Streamlit Cloud용 진입점:

```powershell
streamlit run streamlit_app.py
```

7일 예약 수집:

```powershell
python -m scraper.runner --days 7
```

특정 날짜 수집:

```powershell
python -m scraper.runner --date 2026-06-15
```

자동 수집 스케줄러:

```powershell
python scheduler.py
```

새 매장 테스트:

```powershell
python dry_run.py --store_id yumeplay_hongdae
```

## 데이터 파일

현재 수집 DB:

```text
data/escape_room.db
```

읽기 전용 데모 DB:

```text
demo_data/lumitrack_demo.sqlite
```

매장 설정:

```text
stores.yaml
store_locations.yaml
manual_estimates.yaml
```

## Streamlit Cloud 배포

풀기능 앱을 띄우려면 Streamlit Cloud의 main file path를 아래처럼 둡니다.

```text
streamlit_app.py
```

Streamlit Cloud에서는 `requirements.txt`로 Python 패키지를 설치하고, `packages.txt`로 서버용 Chromium을 설치합니다.
배포 설정의 Python 버전은 가능하면 3.11 또는 3.12로 선택하는 것을 권장합니다.

단, 무료 Streamlit Cloud는 장시간 Playwright 수집이나 상시 스케줄러 실행에는 안정적이지 않을 수 있습니다.
특히 전체 매장 7일 수집은 서버 자원 때문에 한 번에 실행하지 않고 지역/매장 필터로 나누는 것을 권장합니다.

투자자에게 링크로 보여주는 읽기 전용 데모만 필요하면 main file path를 아래처럼 둡니다.

```text
streamlit_demo_app.py
```

## GitHub 업로드

초보자용 업로드 순서는 아래 파일에 따로 정리되어 있습니다.

```text
FULL_UPLOAD_GUIDE.md
```

## 새 어댑터 추가

예약 페이지 구조가 다른 매장은 `scraper/adapters/` 아래에 새 adapter 파일을 추가합니다.

모든 adapter는 `BaseAdapter` 인터페이스를 따릅니다.

필수 메서드:

```python
fetch_slots(store_config, target_date)
parse_slots(page, store_config, target_date)
```

추가 후 `scraper/adapters/__init__.py`에 adapter type을 연결하면 됩니다.

## 계산 방식

예약률:

```text
reserved 슬롯 수 / 전체 슬롯 수 * 100
```

예상 매출:

```text
예약 완료 슬롯일 때만 price * avg_people
```

예상 매출은 실제 매출이 아니라 공개 예약 현황과 객단가 가정에 기반한 추정치입니다.
