# 루나 Discord 봇

서버의 직접 멘션과 DM을 감지하고, 대화 맥락을 기억해 로컬 Ollama 모델 또는 OpenAI API로 답변하는 Discord 봇입니다.

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

`.env`에 Discord 토큰과 LLM 설정을 작성합니다. 기존 `.env`가 있다면 파일을 덮어쓰지 말고 필요한 항목만 추가합니다.

```env
DISCORD_TOKEN=여기에_새로_발급한_봇_토큰
LLM_PROVIDER=auto
OPENAI_API_KEY=
OPENAI_MODEL=gpt-6-luna
OPENAI_TIMEOUT_SECONDS=60
OPENAI_MAX_OUTPUT_TOKENS=256
OPENAI_REASONING_EFFORT=none
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gemma3:4b
OLLAMA_TIMEOUT_SECONDS=60
OLLAMA_NUM_CTX=2048
OLLAMA_KEEP_ALIVE=-1
OLLAMA_WARMUP_ON_START=true
DATABASE_PATH=data/bot.db
USER_COOLDOWN_SECONDS=2
IGNORE_BOT_MESSAGES=true
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

실제 Discord 토큰과 OpenAI API 키는 `.env.example`에 넣거나 Git에 커밋하지 마세요.

- `LLM_PROVIDER`: 기본값 `auto`는 `OPENAI_API_KEY`가 있으면 OpenAI, 없으면 Ollama를 선택합니다. `ollama` 또는 `openai`로 고정할 수도 있습니다.
- `OPENAI_API_KEY`: OpenAI API 키입니다. 기본 `auto` 모드에서는 이 키만 추가하면 OpenAI로 전환됩니다.
- `OPENAI_MODEL`: 기본값은 `gpt-6-luna`입니다. 계정에서 사용 가능한 다른 API 모델로 바꿀 수 있습니다.
- `OPENAI_TIMEOUT_SECONDS`: OpenAI 요청 제한 시간입니다. 기본값은 60초입니다.
- `OPENAI_MAX_OUTPUT_TOKENS`: 응답에 사용하는 출력·추론 토큰 상한입니다. 기본값은 256입니다. 토큰 상한으로 생성이 끝나지 않으면 잘린 답변을 보내거나 저장하지 않고 오류로 처리합니다.
- `OPENAI_REASONING_EFFORT`: 기본값 `none`은 GPT-6 Luna의 추론을 끄고 짧은 대화 응답에 사용합니다. 모델에 따라 지원하는 값이 다릅니다. 추론을 켜면 출력 토큰 상한도 늘리는 것이 좋습니다. 추론 설정을 지원하지 않는 모델에서는 값을 비워 해당 매개변수를 생략합니다.
- `USER_COOLDOWN_SECONDS`: 사용자별 요청 간격입니다.
- `OLLAMA_NUM_CTX`: Ollama 문맥 크기입니다. 기본값은 응답 속도를 고려해 `2048`입니다.
- `OLLAMA_KEEP_ALIVE`: 모델을 메모리에 유지할 시간입니다. `-1`은 봇이 실행되는 동안 계속 유지합니다.
- `OLLAMA_WARMUP_ON_START`: 봇 로그인 직후 모델을 미리 불러올지 정합니다.
- `IGNORE_BOT_MESSAGES`: 다른 봇의 메시지를 무시할지 정합니다. 자기 자신의 메시지는 이 설정과 관계없이 항상 무시합니다.
- `AUTO_ROLES_PATH`: 서버별 자동 지급 역할 설정 파일 경로입니다.
- `CONVERSATION_RETENTION_DAYS`: 대화 보존 일수입니다. `0`은 무기한이며 `7`, `30`처럼 설정할 수 있습니다.
- `GITHUB_WEBHOOK_ENABLED`: GitHub 푸시 알림 서버를 켤지 정합니다.
- `GITHUB_WEBHOOK_SECRET`: GitHub와 봇만 공유하는 Webhook 비밀키입니다.
- `GITHUB_WEBHOOK_CHANNEL_ID`: 저장소별 설정이 없을 때 사용할 선택적인 기본 Discord 채널 ID입니다. 비워 두면 매핑되지 않은 저장소는 알림을 보내지 않습니다.
- `GITHUB_WEBHOOK_CHANNELS_PATH`: 저장소별 Discord 채널 설정 파일 경로입니다.
- `GITHUB_WEBHOOK_BRANCHES_PATH`: 저장소별 허용 브랜치 설정 파일 경로입니다.
- `GITHUB_WEBHOOK_HOST`, `GITHUB_WEBHOOK_PORT`: 로컬 Webhook 수신 주소입니다.

## 3. LLM 준비

### Ollama 사용

API 키가 없거나 `LLM_PROVIDER=ollama`이면 기존 로컬 모델을 사용합니다.

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

### OpenAI API 사용

1. [OpenAI Platform](https://platform.openai.com/)에서 API 결제를 설정하고 [API 키](https://platform.openai.com/api-keys)를 발급합니다. ChatGPT 구독과 API 요금은 별도입니다.
2. **봇을 실행하는 서버**의 기존 `.env`에 다음 항목을 추가하거나 수정합니다.

```env
LLM_PROVIDER=auto
OPENAI_API_KEY=여기에_발급한_API_키
```

3. 봇을 재시작합니다. 터미널에 `LLM 설정: openai / gpt-6-luna`가 표시되면 OpenAI가 선택된 것입니다. Discord에서 멘션하거나 DM을 보내 실제 응답을 확인합니다.

추가 패키지 설치 없이 기존 `aiohttp`로 공식 Responses API를 호출합니다. 서버의 VRAM 용량과 관계없이 사용할 수 있으며, 서버에서 `api.openai.com`으로 HTTPS 접속이 가능해야 합니다. Tailscale로 서버를 관리하는 상태에서도 사용할 수 있습니다.

OpenAI 사용 중에는 Ollama 연결이나 모델 예열을 실행하지 않습니다. API 오류가 발생해도 다른 제공자로 자동 전환하지 않으며, 기존 오류 안내를 보냅니다. 키·잔액·모델 접근 권한 오류의 안내는 서버 터미널에서 확인할 수 있습니다. API 키와 OpenAI 오류 응답 본문은 로그에 기록하지 않습니다.

OpenAI에는 답변에 필요한 캐릭터 지침, 예시 대화, 최근 대화와 현재 메시지를 전달합니다. 대화 기억과 초기화는 기존 로컬 데이터베이스에서 관리하고, API에는 `store=false`로 요청합니다. 이 설정이 OpenAI의 모든 데이터 보관을 없애는 것은 아닙니다. [데이터 처리 안내](https://developers.openai.com/api/docs/guides/your-data)를 참고하세요.

일주일 뒤 Ollama로 돌아가려면 API 키와 Ollama 설정을 그대로 둔 채 `LLM_PROVIDER=ollama`로 바꾸고 봇을 재시작합니다. 다시 API를 사용하려면 `auto` 또는 `openai`로 바꾸면 됩니다.

기본 모델·요청 설정은 [GPT-6 Luna 모델 안내](https://developers.openai.com/api/docs/models/gpt-6-luna)와 [OpenAI 공식 시작 가이드](https://developers.openai.com/api/docs/quickstart)를 기준으로 합니다. 모델에 따라 사용 가능 여부와 과금이 달라집니다.

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

봇은 **매일 한국 시간(KST, UTC+9) 오전 4시**에 모든 사용자의 대화 기록을 초기화하고 자동으로 재시작합니다. Windows와 Linux 모두 실행 명령이나 외부 예약 작업을 추가할 필요 없이 적용되며, 서버의 시간대가 UTC여도 한국 시간을 기준으로 동작합니다.

- 진행 중인 요청과 데이터베이스 쓰기를 정리한 뒤, 전체 대화 메시지와 세션을 삭제하고 같은 Python 실행 옵션으로 새 봇 프로세스를 시작합니다.
- Discord 연결이 끊겨 재연결을 기다리는 중에도 예약 작업이 실행됩니다. 컴퓨터가 절전 상태였다면 복귀 후 실행됩니다.
- Discord 채널에 올라온 메시지, 자동 역할 설정, GitHub 알림 설정은 유지됩니다.
- `Ctrl+C`로 종료하면 자동으로 재시작하지 않습니다. 변경된 코드를 적용하려면 현재 실행 중인 봇을 한 번 재시작해 주세요.

## 6. 최근 대화 기억

- 데이터베이스는 첫 실행 시 `data/bot.db`에 자동 생성됩니다.
- 봇 프로세스를 시작하거나 재시작할 때 저장된 모든 사용자의 대화 기록을 삭제합니다.
- 매일 한국 시간 오전 4시에도 전체 기록을 초기화하고 봇 프로세스를 자동으로 재시작합니다.
- 서버 대화는 `서버 ID + 채널 ID + 사용자 ID`별로 분리됩니다.
- DM 대화는 `DM 채널 ID + 사용자 ID`별로 분리됩니다.
- 사용자 메시지와 루나의 답변 한 쌍을 대화 1회로 계산합니다.
- 각 세션은 최근 5회 대화, 즉 메시지 10개만 저장합니다.
- 6번째 대화가 저장되면 가장 오래된 1회 대화는 즉시 삭제합니다.
- 오래된 대화의 요약이나 장기 기억은 만들지 않습니다.
- 멘션되지 않은 서버 메시지, 다른 봇의 메시지, 오류 안내 메시지는 저장하지 않습니다.
- Discord 사용자 이름과 서버 닉네임은 저장하지 않습니다.
- 현재 사용자의 Discord 표시 이름이나 요청한 호칭을 사용하며, 대화에 등장한 다른 사람과 현재 사용자를 혼동하지 않도록 안내합니다.
- 정상적으로 생성된 답변은 항상 최근 대화에 저장됩니다.
- 봇이 실행 중인 동안에만 세션별 최근 5회 대화를 기억합니다.
- 대화 관련 슬래시 명령은 `/대화 초기화`만 제공합니다.

## 7. 대화 초기화

Discord에서 `/대화 초기화`를 실행하면 명령을 실행한 사용자의 모든 서버·채널·DM 최근 기록을 삭제합니다.

- 다른 사용자의 기록은 삭제하지 않습니다.
- 삭제 결과는 명령을 실행한 사용자에게만 보입니다.
- 봇을 시작하면 슬래시 명령을 Discord에 자동으로 동기화합니다.

운영자가 전체 사용자의 대화 메시지와 세션을 한 번에 초기화하려면 프로젝트 폴더에서 다음 명령을 실행합니다. 이 명령은 Discord에 로그인하거나 봇 프로세스를 시작하지 않고 데이터베이스만 초기화한 뒤 종료합니다.

```powershell
.\.venv\Scripts\python.exe main.py reset
```

Linux에서는 다음과 같이 실행합니다.

```bash
./.venv/bin/python main.py reset
```

## 8. 요청 처리와 안전 설정

- 사용자별 2초 쿨다운을 적용합니다.
- LLM 요청은 제공자와 관계없이 동시에 하나만 실행하고 나머지는 대기합니다.
- Ollama 사용 시 봇 시작에 모델을 예열하고 `OLLAMA_KEEP_ALIVE` 설정에 따라 메모리에 유지합니다. OpenAI 사용 시에는 예열하지 않습니다.
- 두 제공자 모두 같은 캐릭터 프롬프트, 예시 대화, 최근 대화와 답변 검사를 사용합니다. 기본 시스템 프롬프트는 존댓말, 이성적·논리적인 답변, 일반 답변 최대 3문장을 지시합니다. Ollama 생성은 최대 96토큰, OpenAI 생성은 기본 최대 256토큰으로 제한합니다.
- 터미널에는 메시지 원문 없이 LLM 응답 처리 시간만 기록합니다.
- 답변을 기다리는 동안 Discord의 입력 중 표시를 사용합니다.
- 일반 콘텐츠를 단어·주제에 따라 거절하던 입력·출력 필터와 설정 파일은 제거했습니다. 이전 MODERATION_CONFIG_PATH 환경 변수는 더 이상 사용하지 않습니다.
- 루나는 차분하고 논리적인 성격과 편안한 존댓말을 유지합니다. 반말·말투 변경 요청은 고정 안내로 거절하고, 생성 답변에서 명백한 반말 종결을 감지하면 한 번 다시 생성합니다. 재시도도 실패하면 정중한 고정 답변을 보내며, 잘못 생성된 답변은 보내거나 저장하지 않습니다.
- 사용자 자신의 호칭·별명과 대화 주제·기분은 반영할 수 있습니다. 루나 자신의 이름·별명 변경은 "저는 루나입니다."로 시작하는 고정 답변으로 거절하며 저장하지 않습니다.
- 봇 호출 멘션 하나만 제거하고 인용된 봇 멘션은 @루나로 보존합니다. 답변에서는 Discord 멘션 알림을 보내지 않습니다.
- 루나 또는 루나#숫자를 그대로 말하거나 @를 붙여 말하라는 짧은 요청은 모델 없이 처리합니다. 생성 답변이 내부 지시 문장을 복사하면 전송·저장을 막으며, 과거 답변에 남은 같은 노출도 문맥에서 제외합니다.
- 루나의 제작자는 한 명이며, 루멘·박시우·Lumen은 모두 동일 인물의 이름입니다. 현재 사용자 메시지에서 제작자를 언급하거나 묻거나 이 이름들을 직접 언급할 때만 제작자 정보를 답합니다. 인사와 일반 자기소개에서는 제작자를 먼저 소개하지 않으며, 표시 이름과 과거 대화의 언급도 현재 메시지의 언급으로 취급하지 않습니다. 원치 않은 제작자 소개나 여러 제작자로 잘못 소개한 답변은 한 번 다시 생성합니다. 재시도도 실패하면 제작자 언급이 있는 요청에만 정확한 제작자 소개를 고정 답변으로 보내고, 나머지는 제작자 정보 없는 정중한 안내를 보냅니다. 과거의 잘못되거나 요청하지 않은 제작자 소개도 문맥에서 제외합니다. 제작자 이름을 언급해도 관리자 권한이 생기지는 않습니다.
- 이모지 강제 삭제와 Discord 표시 이름만 호칭으로 사용하도록 강제하던 제한도 해제했습니다.
- 역할/턴 특수 토큰 위조와 이전 시스템 지시 무시·변조 요청은 LLM 호출 전에 차단하며 대화 기록에 저장하지 않습니다.
- 사진 사례의 reserved/unused 특수 토큰도 차단하며, 공격이 섞인 Discord 표시 이름은 시스템 메시지에 그대로 넣지 않습니다.
- 시스템·개발자 메시지, 숨겨진 프롬프트 또는 내부 규칙을 출력·복사·요약하려는 요청도 차단합니다.
- 봇 자신의 내부 코드·답변 생성 로직·비공개 설정을 공개·재구성·리팩터링하려는 요청에는 기능이 없는 HelloUser 클래스 예제 코드만 고정으로 보여줍니다. 실제 파일을 읽거나 Ollama를 호출하지 않고 기록에도 저장하지 않습니다. 시스템 지시·역할 토큰 공격은 기존 거절 응답을 유지하며, 일반 Python 질문은 허용합니다.
- 과거 기록에 섞인 시스템 접근·내부 코드 요청과 그에 이어진 봇 답변은 다음 LLM 문맥에서 완전히 제외합니다. 치환 문구도 넣지 않으며, 정상 대화 기록은 유지합니다.
- 대화 중 개발자·관리자·ADMIN·소유자라고 주장해도 시스템 권한으로 인정하지 않습니다.

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
