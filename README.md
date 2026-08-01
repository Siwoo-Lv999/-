# 루나 Discord 봇

서버의 직접 멘션과 DM을 감지하고, 대화 맥락을 기억해 로컬 Ollama 모델로 답변하는 Discord 봇입니다.

## 1. 가상환경과 패키지

Windows PowerShell에서 실행합니다.

```powershell
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

가상환경 활성화 없이 실행하려면 `python` 대신 `.\.venv\Scripts\python.exe`를 사용할 수 있습니다.

## 2. 환경 변수

```powershell
Copy-Item .env.example .env
```

`.env`에 Discord 토큰과 Ollama 설정을 작성합니다.

```env
DISCORD_TOKEN=여기에_새로_발급한_봇_토큰
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gemma3:4b
OLLAMA_TIMEOUT_SECONDS=60
OLLAMA_NUM_CTX=2048
OLLAMA_KEEP_ALIVE=-1
OLLAMA_WARMUP_ON_START=true
DATABASE_PATH=data/bot.db
USER_COOLDOWN_SECONDS=2
IGNORE_BOT_MESSAGES=true
MODERATION_CONFIG_PATH=config/moderation.yml
AUTO_ROLES_PATH=config/auto_roles.yml
CONVERSATION_RETENTION_DAYS=0
GITHUB_WEBHOOK_ENABLED=false
GITHUB_WEBHOOK_SECRET=
GITHUB_WEBHOOK_CHANNEL_ID=
GITHUB_WEBHOOK_CHANNELS_PATH=config/github_channels.yml
GITHUB_WEBHOOK_BRANCHES_PATH=config/github_branches.yml
GITHUB_WEBHOOK_HOST=127.0.0.1
GITHUB_WEBHOOK_PORT=8080
```

실제 토큰은 `.env.example`에 넣거나 Git에 커밋하지 마세요.

- `USER_COOLDOWN_SECONDS`: 사용자별 요청 간격입니다.
- `OLLAMA_NUM_CTX`: Ollama 문맥 크기입니다. 기본값은 응답 속도를 고려해 `2048`입니다.
- `OLLAMA_KEEP_ALIVE`: 모델을 메모리에 유지할 시간입니다. `-1`은 봇이 실행되는 동안 계속 유지합니다.
- `OLLAMA_WARMUP_ON_START`: 봇 로그인 직후 모델을 미리 불러올지 정합니다.
- `IGNORE_BOT_MESSAGES`: 다른 봇의 메시지를 무시할지 정합니다. 자기 자신의 메시지는 이 설정과 관계없이 항상 무시합니다.
- `MODERATION_CONFIG_PATH`: 대화 안전 필터 규칙 파일 경로입니다.
- `AUTO_ROLES_PATH`: 서버별 자동 지급 역할 설정 파일 경로입니다.
- `CONVERSATION_RETENTION_DAYS`: 대화 보존 일수입니다. `0`은 무기한이며 `7`, `30`처럼 설정할 수 있습니다.
- `GITHUB_WEBHOOK_ENABLED`: GitHub 푸시 알림 서버를 켤지 정합니다.
- `GITHUB_WEBHOOK_SECRET`: GitHub와 봇만 공유하는 Webhook 비밀키입니다.
- `GITHUB_WEBHOOK_CHANNEL_ID`: 저장소별 설정이 없을 때 사용할 기본 Discord 채널 ID입니다.
- `GITHUB_WEBHOOK_CHANNELS_PATH`: 저장소별 Discord 채널 설정 파일 경로입니다.
- `GITHUB_WEBHOOK_BRANCHES_PATH`: 저장소별 허용 브랜치 설정 파일 경로입니다.
- `GITHUB_WEBHOOK_HOST`, `GITHUB_WEBHOOK_PORT`: 로컬 Webhook 수신 주소입니다.

## 3. Ollama 준비

1. [Ollama 공식 사이트](https://ollama.com/download/windows)에서 Windows용 Ollama를 설치합니다.
2. 새 PowerShell을 열고 모델을 내려받습니다.

```powershell
ollama pull gemma3:4b
ollama list
```

Ollama는 설치 후 일반적으로 백그라운드에서 실행됩니다. 연결이 안 되면 다음 명령으로 직접 실행합니다.

```powershell
ollama serve
```

## 4. Discord Developer Portal

1. [Discord Developer Portal](https://discord.com/developers/applications)의 `Bot` 메뉴에서 봇 토큰을 발급합니다.
2. `Privileged Gateway Intents`에서 `Message Content Intent`와 `Server Members Intent`를 켭니다.
3. `OAuth2` > `URL Generator`에서 `bot` Scope를 선택합니다.
4. `View Channels`, `Send Messages`, `Read Message History`, `Manage Roles` 권한으로 테스트 서버에 초대합니다.

`Presence Intent`는 현재 필요하지 않습니다.

## 5. 실행

```powershell
.\.venv\Scripts\python.exe main.py
```

서버에서는 봇을 직접 멘션하고, DM에서는 멘션 없이 메시지를 보내 확인합니다. 봇을 종료하려면 `Ctrl+C`를 누릅니다.

## 6. 최근 대화 기억

- 데이터베이스는 첫 실행 시 `data/bot.db`에 자동 생성됩니다.
- 서버 대화는 `서버 ID + 채널 ID + 사용자 ID`별로 분리됩니다.
- DM 대화는 `DM 채널 ID + 사용자 ID`별로 분리됩니다.
- 사용자 메시지와 루나의 답변 한 쌍을 대화 1회로 계산합니다.
- 각 세션은 최근 5회 대화, 즉 메시지 10개만 저장합니다.
- 6번째 대화가 저장되면 가장 오래된 1회 대화는 즉시 삭제합니다.
- 오래된 대화의 요약이나 장기 기억은 만들지 않습니다.
- 멘션되지 않은 서버 메시지, 다른 봇의 메시지, 오류 안내 메시지는 저장하지 않습니다.
- Discord 사용자 이름과 서버 닉네임은 저장하지 않습니다.
- 정상적으로 생성된 답변은 항상 최근 대화에 저장됩니다.
- 업데이트 후 처음 실행할 때 기존 요약과 장기 기억을 삭제하고 각 세션을 최근 5회로 정리합니다.
- 대화 관련 슬래시 명령은 `/대화 초기화`만 제공합니다.

## 7. 대화 초기화

Discord에서 `/대화 초기화`를 실행하면 명령을 실행한 사용자의 모든 서버·채널·DM 최근 기록을 삭제합니다.

- 다른 사용자의 기록은 삭제하지 않습니다.
- 삭제 결과는 명령을 실행한 사용자에게만 보입니다.
- 봇을 시작하면 슬래시 명령을 Discord에 자동으로 동기화합니다.

## 8. 요청 처리와 안전 설정

- 사용자별 2초 쿨다운을 적용합니다.
- Ollama 요청은 동시에 하나만 실행하고 나머지는 대기합니다.
- 봇 시작 시 Ollama 모델을 예열하고 `OLLAMA_KEEP_ALIVE` 설정에 따라 메모리에 유지합니다.
- 일반 답변은 최대 96토큰으로 제한하고 최근 대화 문맥과 예시 수를 작게 유지합니다.
- 터미널에는 메시지 원문 없이 LLM 응답 처리 시간만 기록합니다.
- 답변을 기다리는 동안 Discord의 입력 중 표시를 사용합니다.
- 성적인 표현에는 `"그런 말은 하면 안 됩니다."`로 즉시 답하며 Ollama를 호출하지 않습니다.
- 혐오·심각한 괴롭힘, 현실적인 위협·위험 요청은 코드 단계에서 제한합니다.
- 필터 규칙과 답변은 `config/moderation.yml`에서 수정할 수 있습니다.
- 필터 로그는 `logs/moderation.log`에 시각과 분류만 기록하며 사용자 메시지 원문은 남기지 않습니다.

## 9. 새 멤버 역할 자동 지급

서버에서 `역할 관리` 권한이 있는 사용자가 다음 명령으로 자동 지급 역할을 관리할 수 있습니다.

- `/역할 자동지급 설정`: 역할 선택기에서 새 멤버에게 지급할 역할을 고릅니다.
- `/역할 자동지급 확인`: 현재 설정된 역할을 확인합니다.
- `/역할 자동지급 해제`: 역할 자동 지급을 끕니다.

설정은 서버별로 분리되어 `config/auto_roles.yml`에 즉시 저장됩니다. 봇 계정에는 역할을 지급하지 않으며, `@everyone`, 연동 서비스가 관리하는 역할, 관리자 권한이 포함된 역할은 설정할 수 없습니다.
자동 지급 설정, 확인, 해제 결과는 명령을 실행한 채널의 모든 사용자에게 표시됩니다.

Discord 서버 설정의 역할 목록에서 **봇의 역할을 자동 지급할 역할보다 위에 배치**해야 합니다. 또한 Developer Portal의 `Server Members Intent`와 봇 역할의 `역할 관리` 권한이 필요합니다. 설정 이후 새로 들어오는 사람부터 적용되며 기존 멤버에게는 소급 지급하지 않습니다.

## 10. GitHub 푸시 알림

GitHub가 공개 HTTPS 주소로 Webhook을 보내면, 봇이 서명을 확인한 뒤 지정한 Discord 채널에 저장소, 브랜치, 작성자와 최근 커밋을 임베드로 알립니다. `push` 외 이벤트는 무시하고, 같은 delivery ID가 다시 들어오면 중복 알림을 보내지 않습니다.

### Ubuntu 서버의 `.env` 설정

프로젝트 폴더에서 비밀키를 하나 만듭니다.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Discord에서 `사용자 설정` > `고급` > `개발자 모드`를 켠 뒤, 알림을 받을 채널을 우클릭해 `채널 ID 복사`를 누릅니다. 서버의 `.env`에 다음 값을 추가합니다.

```env
GITHUB_WEBHOOK_ENABLED=true
GITHUB_WEBHOOK_SECRET=방금_만든_비밀키
GITHUB_WEBHOOK_CHANNEL_ID=복사한_Discord_채널_ID
GITHUB_WEBHOOK_CHANNELS_PATH=config/github_channels.yml
GITHUB_WEBHOOK_BRANCHES_PATH=config/github_branches.yml
GITHUB_WEBHOOK_HOST=127.0.0.1
GITHUB_WEBHOOK_PORT=8080
```

systemd로 실행 중이라면 봇을 재시작하고 상태를 확인합니다.

```bash
sudo systemctl restart discordbot
sudo journalctl -u discordbot -n 50 --no-pager
curl http://127.0.0.1:8080/github/health
```

정상이면 마지막 명령에 `{"status": "ok"}`가 나옵니다. 봇 역할에는 알림 채널의 `채널 보기`, `메시지 보내기`, `링크 첨부` 권한이 필요합니다.

### 저장소별 Discord 채널 지정

`.env`의 `GITHUB_WEBHOOK_CHANNEL_ID`는 매핑되지 않은 저장소가 사용할 기본 채널입니다. 서버 관리 권한이 있는 사용자는 Discord에서 다음 명령으로 저장소별 채널을 관리할 수 있습니다.

- `/github 채널 설정`: `owner/repository` 저장소와 알림 채널을 지정합니다.
- `/github 채널 목록`: 현재 기본 채널과 저장소별 채널을 표시합니다.
- `/github 채널 삭제`: 해당 저장소의 Push 알림을 완전히 끕니다.

설정 명령을 실행할 때는 저장소에 `OWNER/REPOSITORY` 형식의 전체 이름을 입력하고, Discord 채널 선택기에서 알림 채널을 고릅니다. 봇에는 선택한 채널의 `채널 보기`, `메시지 보내기`, `링크 첨부` 권한이 필요합니다.

변경 내용은 `config/github_channels.yml`에 즉시 저장되고 실행 중인 Webhook에도 바로 적용되므로 봇을 재시작할 필요가 없습니다. 삭제한 저장소는 `알림 꺼짐` 상태로 남으며, `/github 채널 설정`을 다시 실행하면 알림이 켜집니다. 한 번도 설정하지 않은 저장소만 `.env`의 기본 채널을 사용합니다.

설정 파일을 직접 편집하려면 서버에서 예제 설정을 복사합니다.

```bash
cd ~/bots/-
cp config/github_channels.example.yml config/github_channels.yml
nano config/github_channels.yml
```

GitHub 저장소 URL이 `https://github.com/OWNER/REPOSITORY`라면 다음처럼 저장소 전체 이름과 Discord 채널 ID를 입력합니다.

```yaml
repositories:
  "OWNER/REPOSITORY": 123456789012345678
  "OWNER/ANOTHER-REPOSITORY": 234567890123456789
  "OWNER/MUTED-REPOSITORY": null
```

직접 편집한 경우에는 봇을 재시작합니다.

```bash
sudo systemctl restart discordbot
sudo journalctl -u discordbot -n 30 --no-pager
```

로그의 `GitHub 저장소별 Discord 채널 설정을 불러왔습니다` 뒤에 등록한 저장소 수가 표시됩니다. 저장소 이름은 대소문자를 구분하지 않으며, 설정에 없는 저장소는 기본 채널로 전송됩니다. 실제 `config/github_channels.yml`은 Git에서 제외됩니다.

### 저장소별 Push 브랜치 지정

서버 관리 권한이 있는 사용자는 Discord에서 저장소별 허용 브랜치를 관리할 수 있습니다.

- `/github 브랜치 추가`: 알림을 받을 브랜치를 허용 목록에 추가합니다.
- `/github 브랜치 삭제`: 브랜치를 허용 목록에서 삭제합니다.
- `/github 브랜치 목록`: 저장소의 현재 허용 브랜치를 확인합니다.
- `/github 브랜치 전체`: 필터를 제거하고 모든 브랜치 알림을 허용합니다.

브랜치 필터가 없는 저장소는 모든 브랜치의 Push 알림을 보냅니다. 브랜치를 하나 추가하는 순간부터 해당 저장소는 허용 목록의 브랜치만 알립니다. 마지막 브랜치를 삭제해 목록이 비면 어떤 브랜치도 알리지 않으며, `/github 브랜치 전체`로 전체 허용 상태를 복구할 수 있습니다. 브랜치 이름은 대소문자를 구분합니다.

변경 내용은 `config/github_branches.yml`에 즉시 저장되고 실행 중인 Webhook에도 바로 적용됩니다. GitHub Webhook 주소와 Secret은 변경할 필요가 없습니다.

설정 파일을 직접 편집하려면 다음 형식을 사용합니다.

```bash
cp config/github_branches.example.yml config/github_branches.yml
nano config/github_branches.yml
```

```yaml
repositories:
  "OWNER/REPOSITORY":
    - main
    - develop
  "OWNER/MUTED-REPOSITORY": []
```

직접 편집한 경우에는 봇을 재시작해야 합니다. 실제 `config/github_branches.yml`은 Git에서 제외됩니다.

### 공개 HTTPS 주소 연결

GitHub에서는 서버의 `127.0.0.1`로 직접 접속할 수 없습니다. Nginx, Caddy 또는 HTTPS 터널을 사용해 공개 주소의 `/github/webhook` 요청을 `http://127.0.0.1:8080/github/webhook`으로 전달해야 합니다.

예시 Payload URL:

```text
https://bot.example.com/github/webhook
```

### GitHub 저장소 설정

1. GitHub 저장소의 `Settings` > `Webhooks` > `Add webhook`을 엽니다.
2. `Payload URL`에 공개 HTTPS Webhook 주소를 입력합니다.
3. `Content type`은 `application/json`을 선택합니다.
4. `Secret`에는 `.env`의 `GITHUB_WEBHOOK_SECRET`과 정확히 같은 값을 입력합니다.
5. 이벤트는 `Just the push event`를 선택하고 `Active`를 켭니다.
6. Webhook을 만든 뒤 `Recent Deliveries`의 `ping` 응답이 `200`인지 확인합니다.

비밀키는 Git에 커밋하거나 Discord에 올리지 마세요. GitHub Webhook 설정을 저장해도 알림이 오지 않으면 먼저 공개 HTTPS 연결과 봇의 채널 권한을 확인합니다.
