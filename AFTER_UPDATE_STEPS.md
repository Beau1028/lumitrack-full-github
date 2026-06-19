# LumiTrack 업데이트 후 해야 할 일

Codex가 코드를 고쳐준 뒤에는 항상 아래 순서대로 진행하면 됩니다.

## 1. GitHub Desktop에서 반영

1. GitHub Desktop을 엽니다.
2. 왼쪽 `Changes`에 변경 파일이 보이는지 확인합니다.
3. Summary에 이번 변경 내용을 짧게 적습니다.
   예: `Fix Streamlit Cloud 7-day crawl stability`
4. `Commit to main`을 누릅니다.
5. 위쪽에 `Push origin`이 보이면 누릅니다.

## 2. Streamlit Cloud에서 다시 실행

1. Streamlit Cloud 앱 페이지로 갑니다.
2. `Manage app`을 누릅니다.
3. `Reboot app`을 누릅니다.
4. 앱이 다시 뜰 때까지 기다립니다.

## 3. 에러가 나면

1. Streamlit Cloud에서 `Logs`를 엽니다.
2. 빨간 에러 문구를 복사합니다.
3. Codex에게 그대로 보냅니다.

특히 아래 문구가 보이면 중요합니다.

```text
ModuleNotFoundError
playwright
chromium
sqlite
memory
timeout
No such file
```

## 4. 이번 7일 수집 관련 팁

Streamlit Cloud에서는 서버 자원이 작아서 전체 매장 7일 수집이 오래 걸릴 수 있습니다.
이번 버전부터 Cloud에서는 자동으로 서버 안전 모드가 켜집니다.

- 병렬 수집 수를 낮춤
- 느린 페이지는 제한 시간 안에 넘김
- 실패해도 흰 화면 대신 오류 안내를 표시

그래도 계속 하얀 화면이 나오면 Logs를 먼저 확인해야 합니다.
