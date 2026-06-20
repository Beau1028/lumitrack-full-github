# LumiTrack Render 배포 가이드

Streamlit Community Cloud에서 7일 수집이 계속 멈추면 Render 유료 서버로 옮기는 것을 권장합니다.

## 왜 Render인가

- GitHub 저장소를 바로 연결할 수 있습니다.
- Dockerfile을 지원해서 Playwright/Chromium 설치가 안정적입니다.
- Persistent Disk를 붙이면 SQLite DB가 재시작 후에도 유지됩니다.
- Streamlit Cloud보다 장시간 수집 작업에 유리합니다.

## 예상 비용

추천:

- Render Web Service `Standard`: 월 $25, RAM 2GB
- Persistent Disk 1GB: 월 $0.25

테스트만 할 때:

- Render Web Service `Starter`: 월 $7, RAM 512MB

단, Starter는 Chromium 수집에 메모리가 부족할 수 있습니다. 전체 매장 7일 수집까지 생각하면 Standard가 훨씬 안전합니다.

## Render에서 만드는 법

1. https://render.com 에 가입합니다.
2. GitHub 계정을 연결합니다.
3. `New` -> `Blueprint`를 누릅니다.
4. LumiTrack GitHub 저장소를 선택합니다.
5. `render.yaml`을 인식하면 그대로 진행합니다.
6. Plan은 가능하면 `Standard`를 선택합니다.
7. Disk가 `/var/data`로 잡혀 있는지 확인합니다.
8. Deploy를 누릅니다.

배포가 끝나면 아래처럼 주소가 생깁니다.

```text
https://lumitrack.onrender.com
```

## 업데이트 후 해야 할 일

코드를 고친 뒤에는:

1. GitHub Desktop에서 `Commit to main`
2. `Push origin`
3. Render가 자동으로 다시 배포

Render에서 자동 배포가 안 되면:

1. Render 대시보드에서 LumiTrack 서비스 클릭
2. `Manual Deploy`
3. `Deploy latest commit`

## 주의

- SQLite DB는 `/var/data/data/escape_room.db`에 저장됩니다.
- 첫 실행 때 현재 GitHub에 들어있는 `data/escape_room.db`를 `/var/data`로 복사합니다.
- 이후 수집 데이터는 Render Disk에 남습니다.
- 전체 매장 7일 수집은 가능하지만, 너무 자주 누르면 사이트와 서버 모두에 부담이 됩니다.
