# LumiTrack 풀버전 GitHub 업로드 가이드

이 폴더는 LumiTrack의 풀버전 업로드용 폴더입니다.

포함된 것:

- Streamlit UI
- Playwright 예약 수집 코드
- APScheduler 자동수집 코드
- dry-run 도구
- 가격/매장 설정 YAML
- 전체 adapter 코드
- 현재 수집 DB `data/escape_room.db`
- 데모 DB `demo_data/lumitrack_demo.sqlite`
- Windows 실행파일 빌드용 코드와 spec 파일
- 테스트 코드

포함하지 않은 것:

- `.venv`: 내 컴퓨터 전용 설치 폴더라 GitHub에 올리면 안 됩니다.
- `build`, `dist`: 다시 만들 수 있는 빌드 결과물입니다.
- `logs`, `work`, `artifacts`: 실행 중 생긴 로그, 임시 캡쳐, 실험 파일입니다.
- `__pycache__`: 파이썬 캐시입니다.

## 내 컴퓨터에서 풀기능 실행

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
streamlit run app.py
```

수집 명령:

```powershell
python -m scraper.runner --days 7
python scheduler.py
python dry_run.py --store_id yumeplay_hongdae
```

## GitHub Desktop으로 올리기

1. GitHub Desktop을 엽니다.
2. `File` -> `Add local repository...`를 누릅니다.
3. 아래 폴더를 선택합니다.

   `C:\Users\보우\Documents\Codex\2026-06-12\python-playwright-sqlite-streamlit-apscheduler-yaml\outputs\lumitrack-full-github`

4. 안내가 뜨면 repository를 새로 만듭니다.
5. 이름은 `lumitrack-full` 또는 `lumitrack` 정도로 합니다.
6. `Commit to main`을 누릅니다.
7. `Publish repository`를 누릅니다.

## Streamlit Cloud에 올릴 때

풀기능 앱을 띄우려면 main file path를 아래처럼 둡니다.

```text
streamlit_app.py
```

다만 무료 Streamlit Cloud는 장시간 Playwright 수집이나 자동 스케줄러에는 안정적이지 않을 수 있습니다.
투자자에게 보여주는 읽기 전용 데모만 필요하면 main file path를 아래처럼 둡니다.

```text
streamlit_demo_app.py
```

풀 수집까지 계속 돌리는 정식 운영은 나중에 VPS, Render, Railway 같은 서버에서 하는 편이 더 안정적입니다.
