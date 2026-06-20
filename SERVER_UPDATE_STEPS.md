# LumiTrack 서버 업데이트 순서

현재 Hetzner Docker 배포는 Streamlit 대신 FastAPI 웹앱을 실행합니다.

## 업데이트할 때

1. GitHub Desktop에서 변경사항을 commit 합니다.
2. `Push origin`을 누릅니다.
3. 서버에 접속합니다.

```powershell
ssh root@178.104.190.190
```

4. 서버에서 아래 명령을 실행합니다.

```bash
cd /root/lumitrack
bash deploy/hetzner_update.sh
```

5. 브라우저에서 확인합니다.

```text
http://178.104.190.190
```

## 문제가 있으면

서버에서 아래 명령을 실행한 뒤 나온 내용을 Codex에 보내면 됩니다.

```bash
cd /root/lumitrack
bash deploy/hetzner_debug.sh
```

## 이번 구조

- 서버 화면: `web_app.py`
- 디자인: `templates/`, `static/`
- 수집 엔진: 기존 `scraper/` 유지
- DB: 서버에서는 `/var/data/data/escape_room.db` 사용
- 7일 수집: 화면의 `수집 상태` 메뉴에서 실행/확인
