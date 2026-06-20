# LumiTrack Hetzner CX33 배포 가이드

이 문서는 LumiTrack을 Hetzner CX33 서버에 올리는 초보자용 순서입니다.

## 1. Hetzner 서버 만들기

1. https://console.hetzner.cloud 에 가입합니다.
2. 새 Project를 만듭니다.
3. `Add Server`를 누릅니다.
4. Location은 처음에는 Germany 또는 Finland 중 아무거나 선택해도 됩니다.
   한국에서 접속 속도를 더 신경 쓰면 Singapore가 낫지만 가격이 더 비쌀 수 있습니다.
5. Image는 `Ubuntu 24.04`를 선택합니다.
6. Type은 `CX33`을 선택합니다.
7. SSH Key를 모르면 일단 root password 방식으로 진행해도 됩니다.
8. 서버를 생성합니다.
9. 서버 IP 주소를 복사해 둡니다.

## 2. 서버 접속

Windows PowerShell에서 아래처럼 접속합니다.

```powershell
ssh root@서버IP
```

처음 접속할 때 `yes/no`가 나오면 `yes`를 입력합니다.

## 3. 코드 받기

서버에서 아래 명령을 실행합니다.

```bash
git clone https://github.com/beau1028/lumitrack-full-github.git lumitrack
cd lumitrack
```

GitHub 저장소 이름이 다르면 위 주소를 본인 저장소 주소로 바꿔야 합니다.

## 4. Docker 설치

서버에서 아래 명령을 실행합니다.

```bash
bash deploy/hetzner_install_docker.sh
```

설치가 끝나면 LumiTrack을 실행합니다.

```bash
bash deploy/hetzner_start.sh
```

## 5. 접속 확인

브라우저에서 아래 주소로 들어갑니다.

```text
http://서버IP
```

예:

```text
http://123.123.123.123
```

## 6. 업데이트 방법

Codex가 코드를 고쳐준 뒤에는 내 컴퓨터에서:

1. GitHub Desktop에서 `Commit to main`
2. `Push origin`

그 다음 서버에 접속해서:

```bash
cd lumitrack
bash deploy/hetzner_update.sh
```

## 7. 로그 보는 법

문제가 생기면 서버에서:

```bash
cd lumitrack
bash deploy/hetzner_logs.sh
```

여기 나오는 마지막 에러를 Codex에게 보내면 됩니다.

## 8. 7일 수집이 멈춘 것처럼 보일 때

7일 예약 업데이트는 앱 화면 안에서 끝까지 기다리지 않고 서버 백그라운드 작업으로 실행됩니다.
버튼을 누른 뒤에는 화면 상단의 수집 상태 카드에서 진행률을 확인합니다.

수집 로그는 앱 안의 `수집 로그 보기`에서 확인하거나, 서버에서 아래 명령으로 볼 수 있습니다.

```bash
cd lumitrack
docker compose exec lumitrack tail -n 120 /var/data/jobs/crawl_*.log
```

## 9. 비용 감각

CX33은 Render Standard보다 훨씬 싸고 RAM이 넉넉해서 LumiTrack에 잘 맞습니다.
다만 Render보다 직접 관리할 것이 조금 더 있습니다.

처음에는 도메인 없이 `http://서버IP`로 확인하고, 투자자에게 보여줄 준비가 되면 도메인과 HTTPS를 붙이는 것을 추천합니다.
