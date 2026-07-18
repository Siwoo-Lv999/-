# 케이 Discord 봇

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
OLLAMA_NUM_CTX=4096
DATABASE_PATH=data/bot.db
USER_COOLDOWN_SECONDS=2
IGNORE_BOT_MESSAGES=true
MODERATION_CONFIG_PATH=config/moderation.yml
CONVERSATION_RETENTION_DAYS=0
```

실제 토큰은 `.env.example`에 넣거나 Git에 커밋하지 마세요.

- `USER_COOLDOWN_SECONDS`: 사용자별 요청 간격입니다.
- `OLLAMA_NUM_CTX`: Ollama 문맥 크기입니다. 속도가 너무 느리면 `2048`로 낮춰 비교할 수 있습니다.
- `IGNORE_BOT_MESSAGES`: 다른 봇의 메시지를 무시할지 정합니다. 자기 자신의 메시지는 이 설정과 관계없이 항상 무시합니다.
- `MODERATION_CONFIG_PATH`: 대화 안전 필터 규칙 파일 경로입니다.
- `CONVERSATION_RETENTION_DAYS`: 대화 보존 일수입니다. `0`은 무기한이며 `7`, `30`처럼 설정할 수 있습니다.

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
2. `Privileged Gateway Intents`에서 `Message Content Intent`를 켭니다.
3. `OAuth2` > `URL Generator`에서 `bot` Scope를 선택합니다.
4. `View Channels`, `Send Messages`, `Read Message History` 권한으로 테스트 서버에 초대합니다.

`Server Members Intent`와 `Presence Intent`는 현재 필요하지 않습니다.

## 5. 실행

```powershell
.\.venv\Scripts\python.exe main.py
```

서버에서는 봇을 직접 멘션하고, DM에서는 멘션 없이 메시지를 보내 확인합니다. 봇을 종료하려면 `Ctrl+C`를 누릅니다.

## 6. 대화 기억

- 데이터베이스는 첫 실행 시 `data/bot.db`에 자동 생성됩니다.
- 서버 대화는 `서버 ID + 채널 ID + 사용자 ID`별로 분리됩니다.
- DM 대화는 `DM 채널 ID + 사용자 ID`별로 분리됩니다.
- 각 세션은 사용자와 봇의 메시지를 합쳐 최근 10개만 원문으로 유지합니다.
- 최근 10개를 넘긴 원문은 Ollama로 짧게 요약한 뒤 삭제합니다.
- 요약에는 비밀번호, 인증 코드, API 키, 토큰을 저장하지 않습니다.
- 멘션되지 않은 서버 메시지, 다른 봇의 메시지, 오류 안내 메시지는 저장하지 않습니다.
- Discord 사용자 이름과 서버 닉네임은 저장하지 않습니다.
- `/대화 기록`: 현재 세션의 이전 요약과 최근 메시지를 나에게만 표시합니다.
- `/대화 저장 끄기`: 답변은 계속 받되 이후 새 대화를 저장하지 않습니다.
- `/대화 저장 켜기`: 이후 새 대화 저장을 다시 시작합니다.
- 오래된 대화에서 장기적으로 유용한 정보는 `승인 대기` 후보로만 추출합니다.
- `/대화 기억 보기`: 장기 기억 후보와 승인된 기억을 나에게만 표시합니다.
- `/대화 기억 승인 번호`: 후보를 승인해 이후 답변에 사용합니다.
- `/대화 기억 삭제 번호`: 후보나 승인된 기억을 선택해 삭제합니다.

저장을 꺼도 기존 기록은 유지됩니다. 기존 기록까지 지우려면 `/대화 초기화`를 사용합니다.

## 7. 대화 초기화

Discord에서 `/대화 초기화`를 실행하면 명령을 실행한 사용자의 모든 서버·채널·DM 최근 기록, 요약, 장기 기억을 삭제합니다.

- 다른 사용자의 기록은 삭제하지 않습니다.
- 삭제 결과는 명령을 실행한 사용자에게만 보입니다.
- 봇을 시작하면 슬래시 명령을 Discord에 자동으로 동기화합니다.

## 8. 요청 처리와 안전 설정

- 사용자별 2초 쿨다운을 적용합니다.
- Ollama 요청은 동시에 하나만 실행하고 나머지는 대기합니다.
- 답변을 기다리는 동안 Discord의 입력 중 표시를 사용합니다.
- 명백한 성적 요청, 혐오·심각한 괴롭힘, 현실적인 위협·위험 요청은 코드와 프롬프트 양쪽에서 제한합니다.
- 필터 규칙과 답변은 `config/moderation.yml`에서 수정할 수 있습니다.
- 필터 로그는 `logs/moderation.log`에 시각과 분류만 기록하며 사용자 메시지 원문은 남기지 않습니다.
