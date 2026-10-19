# claudi (클로디) 🐾

[![CI](https://github.com/mingeonho1/claudi/actions/workflows/ci.yml/badge.svg)](https://github.com/mingeonho1/claudi/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[English](README.md) | 한국어

**Claude Code 를 여러 세션 병렬로 돌릴 때 쓰는 "데스크톱 관제 키트"**
— 바탕화면 펫 [claudlet](https://github.com/YeeDochi/Claudlet) 의 기본 크리처 **클로디**를
알림 센터로 바꾸고, 세션 관제·세션 시작 리마인더·Slack 요약 루틴을 한 번에 설치합니다. 모델 설정은 건드리지 않습니다.

> 제가 실제로 매일 쓰고 있는 구성 그대로를 공개한 것입니다. 무료로 쓰시고, 고쳐서 쓰시고, 기여해 주세요.
> 프로젝트는 영어 우선이고, 영어 문서는 [README.md](README.md) 에 있습니다. 설치는 한국어로 말해도 똑같이 됩니다.

---

## 무엇이 들어 있나요

| 구성 | 하는 일 |
|---|---|
| **클로디 펫 + '알림' 탭** | claudlet 2.6.0 을 설치하고 `pet.py` 에 작은 패치(A~F)를 적용해, 펫 대화창의 탭을 **알림 센터**로 바꿉니다. 판단이 필요한 세션(🟡)·실패한 세션(🔴)·중요 Slack 이 한 줄씩 쌓이고, 클릭하면 그 세션으로 바로 이동합니다. |
| **독립 펫 상시 유지** | launchd 가 로그인부터 펫 1마리를 계속 살려 둡니다(1시간마다 점검). 세션 훅에 묶이지 않아 세션이 많아도 펫이 시끄러워지지 않습니다. |
| **세션 관제 (`/session-monitor`)** | 다른 세션이 끝나거나 권한·입력 대기에 걸리면 훅이 이벤트를 남기고, 관제 세션이 그 세션 transcript 를 요약해 "목표 대비 진행 / 방향 / 트레이드오프 / 내가 판단할 것 / 다음 액션"으로 보고합니다. |
| **세션 시작 리마인더** | 세션 첫 프롬프트에 한 번, 내가 잊기 쉬운 운영 상태(Slack 요약 멈춤, 펫 꺼짐)를 상기시킵니다. |
| **루틴 템플릿** | 매시간 Slack 중요건을 추려 펫 알림 탭에 띄우는 `slack-digest` 예약 작업 |
| **SwiftBar 플러그인** (선택) | 메뉴 막대에 모든 세션 상태(❓🔴🟢🟡✔)를 10초마다 표시 |

요구 사항: **macOS**, Python 3.9+, [Claude Code](https://docs.claude.com/en/docs/claude-code), `pipx`(`brew install pipx`).
선택: `terminal-notifier`(클릭 가능한 macOS 알림), [SwiftBar](https://github.com/swiftbar/SwiftBar), Claude 데스크톱 앱(예약 작업·세션 딥링크).
Linux 에서는 훅·관제 스크립트만 동작하고 펫/launchd 단계는 자동으로 건너뜁니다.

---

## 레포 구조

서로 독립적인 네 부분입니다.

| # | 부분 | 위치 |
|---|---|---|
| 1 | **펫 + 알림** — 알림 센터로 패치된 claudlet 펫 | `claude/enforce/` |
| 2 | **세션 관제** — 훅, 이벤트 로그, 관제 스킬 | `claude/monitor/` + `claude/skills/session-monitor` |
| 3 | **설치기** — 멱등 설치기, 훅 설정 프래그먼트, 출력 언어, LaunchAgent 템플릿 | `installer/`, `config/`, `launchd/` |
| 4 | **선택** — 예약 작업 템플릿 | `routines/` |

위 전체에 대한 테스트는 `tests/` 에 있습니다.

---

## 🚀 Claude 로 바로 설치하기

터미널에서 한 줄이면 됩니다. Claude 가 [INSTALL.md](INSTALL.md) 를 읽고, 무엇을 설치할지 물어본 뒤,
미리보기(dry-run) → 설치 → 검증까지 진행합니다.

```bash
git clone https://github.com/mingeonho1/claudi.git ~/claudi && cd ~/claudi && claude "INSTALL.md 를 읽고 claudi 를 설치해줘"
```

이미 Claude Code 세션 안이라면 이렇게 말해도 됩니다.

> https://github.com/mingeonho1/claudi 를 ~/claudi 에 클론하고 INSTALL.md 대로 설치해줘

영어로 말해도 똑같이 됩니다 — 예: *"Read INSTALL.md and install claudi"*.

## 🛠 직접 설치하기

```bash
git clone https://github.com/mingeonho1/claudi.git ~/claudi
cd ~/claudi
./install.sh --dry-run      # 무엇을 바꿀지 먼저 확인 (아무것도 쓰지 않음)
./install.sh                # 설치 (기존 파일·settings.json 은 .bak-claudi-<시각> 으로 백업)
```

설치기가 하는 일:

1. `claude/**` 를 `~/.claude/` 로 복사(같은 파일은 건너뛰고, 다른 파일은 백업 후 교체)
2. `pipx install claudlet==2.6.0` → claudlet 스킬(`/claudlet`) 링크
3. `patch-claudlet.py` 로 펫 패치 적용 + `--check` 검증
4. 독립 펫 플래그 생성 + LaunchAgent 2개(`com.claudi.pet`, `com.claudi.enforcer`) 등록
5. `~/.claude/settings.json` 에 훅 병합(내 기존 훅·설정은 그대로 둠, 중복 추가 없음)
6. 출력 언어 저장 + `~/.claude/CLAUDE.md` 에 `@~/.claude/claudi/lang.md` 한 줄 추가

여러 번 실행해도 결과가 같습니다(멱등).

### 설치 옵션

| 옵션 | 설명 |
|---|---|
| `--lang en\|ko` | Claude 응답·훅/설치기 메시지의 출력 언어(기본 `en`). [출력 언어](#출력-언어) 참고. |
| `--dry-run` | 계획만 출력하고 아무것도 쓰지 않음 |
| `--yes` | 묻지 않고 진행 |
| `--link` | 복사 대신 심볼릭 링크 — 레포를 고치면 바로 반영(기여자용) |
| `--no-claudlet` / `--no-patch` | 펫 설치 / 펫 패치 건너뛰기 |
| `--no-launchd` | 독립 펫 상시 유지(LaunchAgent) 안 함 |
| `--no-hooks` | settings.json 훅 병합 안 함 |
| `--force-claudlet` | 다른 버전 claudlet 이 있어도 2.6.0 으로 재설치 |
| `--home DIR` | 테스트용 HOME (실제 환경을 건드리지 않고 시험) |

`CLAUDE_CONFIG_DIR` 를 쓰고 있다면 그 디렉터리에 설치됩니다.

### 출력 언어

claudi 는 첫 설치 때 출력 언어를 묻습니다. 설치기는 대화형에서 이 질문을 **가장 먼저** 합니다
(기본 `en`; `--yes` 면 이전 선택을 유지하거나 `en`). 비대화형으로는 `--lang en` 또는 `--lang ko` 로 넘깁니다.

선택한 값은 `~/.claude/claudi/config.json` (`{"lang"}`) 과 `~/.claude/claudi/lang.md` 에 기록되고,
`@~/.claude/claudi/lang.md` 가 `~/.claude/CLAUDE.md` 에 추가됩니다(제거 시 삭제). 이후 Claude 응답,
훅/알림/설치기 메시지가 그 언어를 따릅니다. 레포 안의 프롬프트·스킬·문서는 모두
영어로 유지되고, 사용자에게 보이는 출력만 선택한 언어를 따릅니다.

---

## 🧭 사용법

### 펫 띄우기

설치 직후 launchd 가 자동으로 띄웁니다. 수동으로는:

```bash
claudlet-attach --standalone
```

펫을 클릭하면 대화창이 열리고 **Alerts(알림)** 탭에 세션 알림이 쌓입니다. 행을 클릭하면 해당 세션(데스크톱 앱 딥링크 또는 터미널)으로 이동하고 처리됨으로 표시됩니다.
펫 모양·동작은 claudlet 기능 그대로입니다(`/claudlet setting`, `/claudlet jump` 등).

### 세션 관제

새 Claude Code 세션 하나를 관제용으로 열고:

```
/session-monitor
```

이후 다른 세션들이 끝나거나(🟢), 판단이 필요하거나(🟡), 실패하면(🔴) 이 세션이 요약 보고하고, 🟡/🔴 는 펫 알림 탭과 macOS 알림으로도 보냅니다.
관제 세션은 다른 세션을 **읽기만** 합니다.

### 루틴 (예약 작업)

Claude 데스크톱 앱의 Scheduled Tasks + Slack 커넥터가 있으면 [`routines/slack-digest`](routines/README.md) 를 등록해 펫 알림 탭 하단에 "지금 봐야 할 Slack" 을 띄울 수 있습니다.

> routines/slack-digest/SKILL.md 를 내 정보로 채워서 평일 매시간 예약 작업으로 등록해줘

### SwiftBar (선택)

```bash
ln -s ~/.claude/monitor/bin/swiftbar-sessions.10s.py "<SwiftBar 플러그인 폴더>/"
```

---

## 🏗 동작 구조

```
 다른 Claude Code 세션들
   │  Stop / StopFailure / Notification 훅
   ▼
 monitor-hook.py ──▶ ~/.claude/monitor/events.jsonl   (+ attention.json: 클릭 대상)
                              │
                              ▼  wait-event.py (블로킹 대기)
                     /session-monitor 관제 세션
                       └ digest-transcript.py 로 요약 → 채팅 보고
                       └ 🟡/🔴 → notify.py
                                   ├ 펫 말풍선 (claudlet TCP notice)
                                   ├ macOS 알림 (terminal-notifier / osascript)
                                   └ notices.json ──▶ 펫 'Alerts' 탭 (patch E)
 slack-digest 루틴 ──▶ slack-items.json ───────────▶ 펫 'Alerts' 탭 Slack 영역 (patch F)

 펫/알림 클릭 → claudlet_click.py → focus_session.py → claude:// 딥링크 or 터미널 포커스
 launchd (1시간) → ensure-pet.py (독립 펫 살리기) / launchd-check.py (상태 점검)
 UserPromptSubmit → session-start-hook.py (세션 첫 프롬프트 1회 리마인더)
```

펫 패치 상세: [`claude/enforce/README.md`](claude/enforce/README.md) · 관제 상세: [`claude/monitor/README.md`](claude/monitor/README.md)

| 패치 | 변경 |
|---|---|
| A | 펫 클릭 → 주의가 필요한 세션으로 이동 |
| B | 세션 응답을 말풍선으로 중계하지 않음(조용한 펫) |
| C | 대화 기록에 남지 않는 `notice` 말풍선 명령 추가 |
| D | 원래의 '세션 활동' 타임라인 비활성화 |
| E | 그 탭을 `notices.json` 기반 **Alerts** 탭으로 교체 |
| F | Alerts 탭 하단에 Slack 중요건 영역 추가 |

모든 패치는 마커로 표시되고 멱등이며, `python3 ~/.claude/enforce/bin/patch-claudlet.py --revert` 로 원본으로 되돌릴 수 있습니다.
claudlet 을 업데이트하면 패치가 지워지므로 `patch-claudlet.py` 를 다시 실행하세요(지원 버전: 2.6.0).

---

## 🧹 제거

```bash
./uninstall.sh            # 훅 제거, 펫 패치 되돌리기, LaunchAgent 해제, CLAUDE.md import 줄 제거
./uninstall.sh --purge    # + 설치한 파일과 런타임 데이터까지 삭제
```

내가 원래 갖고 있던 훅·설정은 지우지 않습니다. 바뀐 파일은 모두 `.bak-claudi-<시각>` 백업이 남습니다.

## 🩺 문제 해결

| 증상 | 확인 |
|---|---|
| 펫이 안 보임 | `claudlet-attach --standalone` 실행, `~/.claude/enforce/logs/pet.log` 확인, `claudlet-doctor` |
| 알림 탭이 '세션 활동'으로 돌아감 | claudlet 이 재설치됨 → `python3 ~/.claude/enforce/bin/patch-claudlet.py` |
| 관제 세션이 조용함 | `tail ~/.claude/monitor/events.jsonl` 로 훅이 이벤트를 남기는지, settings.json 훅 경로 확인 |
| 알림 클릭이 안 됨 | `brew install terminal-notifier` (없으면 osascript 알림으로 대체되어 클릭 이동 불가) |

## 🔗 관련 프로젝트

예전에 claudi 에 함께 들어 있던 것들은 [ai-agent](https://github.com/mingeonho1/ai-agent) 로 옮겼습니다. 필요하면 가져다 쓰세요.

- [ai-agent](https://github.com/mingeonho1/ai-agent) — 역할별 모델 구성(표준 / 토큰 절약 `--mode eco`), 듀얼 계정 분리(`dual-account/`)
- [Fable 오케스트레이션 모드](https://github.com/mingeonho1/ai-agent/tree/main/fable-mode) — 메인은 계획·분배, 실행은 서브에이전트
- [추가 서브에이전트](https://github.com/mingeonho1/ai-agent/tree/main/extras) — 추가 서브에이전트 정의 모음
- [회사 프로필 예시 (PatSol)](https://github.com/mingeonho1/ai-agent/tree/main/examples/patsol) — 회사 업무에 Claude 를 어떻게 커스텀했는지

## 🤝 기여

이슈·PR 환영합니다. [CONTRIBUTING.md](CONTRIBUTING.md) 를 참고해 주세요.

```bash
python3 -m unittest discover -s tests -v
```

## 📄 라이선스 · 크레딧

- claudi: [MIT](LICENSE)
- 펫: [claudlet](https://github.com/YeeDochi/Claudlet) by YeeDochi (CC0). claudi 는 claudlet 을 재배포하지 않고 PyPI 에서 설치한 뒤 내 컴퓨터에서만 패치합니다. 자세한 내용은 [NOTICE](NOTICE).
- claudi 는 Anthropic 과 무관한 개인 프로젝트입니다.
