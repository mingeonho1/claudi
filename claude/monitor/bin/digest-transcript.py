#!/usr/bin/env python3
# ~/.claude/monitor/bin/digest-transcript.py
#
# Compress a Claude Code session transcript (jsonl) for monitor analysis.
#   Usage: digest-transcript.py <transcript_path> [--turns N]
#
# Output (human-readable summary in the output language, within 15k chars):
#   - META: session name (ai-title), cwd, git branch, Claude Code version.
#   - GOAL: first real user request (the task goal).
#   - RECENT TURNS: last N turns of user messages / assistant text / tool calls
#     (name + key args on one line) / errors.
#   - LAST ASSISTANT MESSAGE: full text of the last assistant message.
#   - PENDING: highlighted if a question is waiting (AskUserQuestion/ExitPlanMode) or an
#     approval is pending (last tool_use with no result).
#
# Standard library only. Never dies on any input; emits the best summary it can.
#
# Claude Code 세션 transcript(jsonl)를 관제 분석용으로 압축한다.
#   사용법: digest-transcript.py <transcript_path> [--turns N]
#
# 출력(사람이 읽는 요약, 출력 언어 기준, 1.5만자 이내):
#   - META: 세션 이름(ai-title), cwd, git 브랜치, Claude Code 버전.
#   - GOAL: 첫 실제 사용자 요청(작업 목표).
#   - RECENT TURNS: 최근 N턴의 사용자 메시지 / 어시스턴트 텍스트 / 툴 호출
#     (이름 + 핵심 인자 한 줄) / 에러.
#   - LAST ASSISTANT MESSAGE: 마지막 어시스턴트 텍스트 전문.
#   - PENDING: 대기 중인 질문(AskUserQuestion/ExitPlanMode) 또는 승인 대기
#     (응답 없는 마지막 tool_use)이 있으면 강조.
#
# 표준 라이브러리만. 어떤 입력이든 죽지 않고 최선의 요약을 낸다.

import argparse
import json
import os
import subprocess
import sys

# Shared language helper (monitor/bin -> ../../claudi/lib); fail-safe: English.
# 공용 언어 헬퍼 로드(monitor/bin -> ../../claudi/lib, 실패 시 영어 폴백).
_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                    "claudi", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
try:
    from claudi_i18n import t
except Exception:
    def t(en, ko):  # fail-safe fallback: English / 폴백: 영어
        return en

MAX_OUT = 15000
DEFAULT_TURNS = 6

# Tool name → key arg keys shown in the one-line summary (priority order).
# 툴 이름 → 한 줄 요약에 보여줄 핵심 인자 키(우선순위).
TOOL_KEYS = {
    "Bash": ["command", "description"],
    "Read": ["file_path"],
    "Edit": ["file_path"],
    "MultiEdit": ["file_path"],
    "Write": ["file_path"],
    "NotebookEdit": ["notebook_path"],
    "Grep": ["pattern"],
    "Glob": ["pattern"],
    "Skill": ["skill", "args"],
    "Task": ["subagent_type", "description"],
    "Agent": ["subagent_type", "description"],
    "WebFetch": ["url"],
    "WebSearch": ["query"],
    "TodoWrite": [],
    "AskUserQuestion": ["questions"],
    "ExitPlanMode": [],
}

WRAP_PREFIXES = (
    "<local-command-caveat>", "<local-command-stdout>", "<local-command-stderr>",
    "<command-name>", "<command-message>", "<command-args>",
    "<command-stdout>", "<command-contents>",
    "<user-prompt-submit-hook>", "<system-reminder>",
)


def load(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def text_from_content(content):
    """Extract only human-readable text from content (str|list).
    content(str|list) 에서 사람이 읽는 텍스트만 뽑는다."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text", ""))
        return "\n".join(out)
    return ""


def is_real_user_prompt(row):
    """Is this a real prompt typed by a human (not a slash-command wrapper/meta/tool_result-only)?
    슬래시 커맨드 래퍼/메타/tool_result 전용이 아닌, 사람이 친 실제 프롬프트인가."""
    if row.get("type") != "user":
        return False
    if row.get("isMeta") or row.get("isSidechain"):
        return False
    msg = row.get("message")
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if isinstance(content, list):
        # Only tool_result blocks → not a user utterance.
        # tool_result 만 들어 있으면 사용자 발화가 아님.
        has_text = any(isinstance(b, dict) and b.get("type") == "text"
                       and b.get("text", "").strip() for b in content)
        if not has_text:
            return False
    txt = text_from_content(content).strip()
    if not txt:
        return False
    low = txt.lstrip()
    if low.startswith(WRAP_PREFIXES):
        return False
    return True


def one_line(s, n=200):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def tool_summary(block):
    name = block.get("name", "tool")
    inp = block.get("input") or {}
    keys = TOOL_KEYS.get(name)
    parts = []
    if keys is None:
        keys = list(inp.keys())[:2]
    for k in keys:
        if k not in inp:
            continue
        v = inp[k]
        if k == "questions" and isinstance(v, list) and v:
            q = v[0]
            if isinstance(q, dict):
                v = q.get("question") or q.get("header") or ""
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        parts.append("%s=%s" % (k, one_line(str(v), 160)))
    arg = " ".join(parts)
    return "%s(%s)" % (name, arg) if arg else name


def tool_use_blocks(row):
    msg = row.get("message")
    if not isinstance(msg, dict):
        return []
    c = msg.get("content")
    if not isinstance(c, list):
        return []
    return [b for b in c if isinstance(b, dict) and b.get("type") == "tool_use"]


def error_snippets(row):
    """One line per errored tool_result in a user line's content.
    user 라인 content 의 tool_result 중 에러만 한 줄씩."""
    msg = row.get("message")
    out = []
    if not isinstance(msg, dict):
        return out
    c = msg.get("content")
    if not isinstance(c, list):
        return out
    for b in c:
        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
            tc = b.get("content")
            if isinstance(tc, list):
                tc = text_from_content(tc)
            out.append(one_line(str(tc), 180))
    return out


def git_branch(rows, cwd):
    # Prefer gitBranch from the transcript (fall back to git when HEAD/empty).
    # transcript 에 적힌 gitBranch 우선(HEAD/빈값이면 git 명령 폴백).
    branch = ""
    for r in reversed(rows):
        gb = r.get("gitBranch")
        if gb and gb != "HEAD":
            branch = gb
            break
    if branch:
        return branch
    if cwd and os.path.isdir(cwd):
        try:
            out = subprocess.run(
                ["git", "-C", cwd, "branch", "--show-current"],
                capture_output=True, text=True, timeout=2)
            b = (out.stdout or "").strip()
            if b:
                return b
        except Exception:
            pass
    return branch or "HEAD"


def session_title(rows):
    title = ""
    for r in rows:
        if r.get("type") == "ai-title" and r.get("aiTitle"):
            title = str(r["aiTitle"]).strip()
    return title


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("transcript")
    ap.add_argument("--turns", type=int, default=DEFAULT_TURNS)
    args, _ = ap.parse_known_args()

    path = args.transcript
    rows = load(path)
    if not rows:
        print(t("(transcript unreadable or empty: %s)",
                "(transcript 를 읽을 수 없거나 비어 있음: %s)") % path)
        return 0

    # Meta. / 메타.
    cwd = ""
    version = ""
    for r in reversed(rows):
        if not cwd and r.get("cwd"):
            cwd = r.get("cwd")
        if not version and r.get("version"):
            version = r.get("version")
        if cwd and version:
            break
    branch = git_branch(rows, cwd)
    title = session_title(rows)

    # Goal: first real user prompt. / 목표: 첫 실제 사용자 프롬프트.
    goal = ""
    for r in rows:
        if is_real_user_prompt(r):
            goal = text_from_content(r["message"]["content"]).strip()
            break

    # Split the timeline into turns (at real user prompt boundaries).
    # 타임라인을 턴으로 분할(실제 사용자 프롬프트 경계).
    turns = []       # [{user, lines:[...]}]
    cur = None
    for r in rows:
        typ = r.get("type")
        if is_real_user_prompt(r):
            cur = {"user": text_from_content(r["message"]["content"]).strip(),
                   "lines": []}
            turns.append(cur)
            continue
        if cur is None:
            cur = {"user": t("(activity before the first user prompt)",
                             "(첫 사용자 프롬프트 이전 활동)"), "lines": []}
            turns.append(cur)
        if typ == "assistant":
            txt = text_from_content(r.get("message", {}).get("content")).strip()
            if txt:
                cur["lines"].append(("say", one_line(txt, 400)))
            for b in tool_use_blocks(r):
                cur["lines"].append(("tool", tool_summary(b)))
        elif typ == "user":
            for e in error_snippets(r):
                cur["lines"].append(("err", e))

    recent = turns[-args.turns:] if args.turns > 0 else turns

    # Full last assistant message. / 마지막 어시스턴트 메시지 전문.
    last_assistant = ""
    for r in reversed(rows):
        if r.get("type") == "assistant":
            txt = text_from_content(r.get("message", {}).get("content")).strip()
            if txt:
                last_assistant = txt
                break

    # PENDING: last assistant's AskUserQuestion/ExitPlanMode, or a tool_use with no result.
    # PENDING: 마지막 어시스턴트의 AskUserQuestion/ExitPlanMode, 또는 응답 없는 tool_use.
    pending = []
    # (a) Question tools. / (a) 질문형 도구.
    last_asst_row = None
    for r in reversed(rows):
        if r.get("type") == "assistant":
            last_asst_row = r
            break
    if last_asst_row:
        for b in tool_use_blocks(last_asst_row):
            nm = b.get("name")
            inp = b.get("input") or {}
            if nm == "AskUserQuestion":
                qs = inp.get("questions") or []
                for q in qs:
                    if isinstance(q, dict):
                        opts = q.get("options") or []
                        labels = []
                        for o in opts:
                            if isinstance(o, dict):
                                labels.append(str(o.get("label") or o.get("name") or ""))
                            else:
                                labels.append(str(o))
                        pending.append(t("Question: %s  [options: %s]", "질문: %s  [선택지: %s]")
                                       % (one_line(str(q.get("question") or q.get("header") or ""), 300),
                                          ", ".join([x for x in labels if x])))
            elif nm == "ExitPlanMode":
                pending.append(t("Awaiting plan approval (ExitPlanMode): %s",
                                 "계획 승인 대기(ExitPlanMode): %s")
                               % one_line(str(inp.get("plan") or ""), 400))
    # (b) Last tool_use with no tool_result → awaiting tool approval/execution.
    # (b) 응답(tool_result) 없는 마지막 tool_use → 도구 승인/실행 대기.
    answered = set()
    last_tool_uses = []
    for r in rows:
        if r.get("type") == "user":
            msg = r.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), list):
                for b in msg["content"]:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tid = b.get("tool_use_id")
                        if tid:
                            answered.add(tid)
        for b in tool_use_blocks(r):
            last_tool_uses.append(b)
    for b in last_tool_uses:
        tid = b.get("id")
        if tid and tid not in answered and b.get("name") not in ("AskUserQuestion", "ExitPlanMode"):
            pending.append(t("Awaiting tool approval/result: %s",
                             "도구 승인/결과 대기: %s") % tool_summary(b))

    # --- assemble output / 출력 조립 ---
    L = []
    L.append("# SESSION DIGEST")
    L.append(t("Session name: %s", "세션이름: %s")
             % (title or t("(untitled)", "(제목 미정)")))
    L.append("cwd: %s" % (cwd or "?"))
    L.append("branch: %s" % branch)
    L.append("version: %s" % (version or "?"))
    L.append("transcript: %s" % path)
    L.append("")
    L.append(t("## GOAL (first user request)", "## GOAL (첫 사용자 요청)"))
    L.append(one_line(goal, 1200) if goal else t("(not identified)", "(식별 못 함)"))
    L.append("")
    if pending:
        L.append(t("## ⚠ PENDING (waiting — top priority)",
                   "## ⚠ PENDING (대기 중 — 최우선)"))
        for p in pending:
            L.append("- " + p)
        L.append("")
    L.append(t("## RECENT TURNS (last %d)", "## RECENT TURNS (최근 %d턴)") % len(recent))
    for i, trn in enumerate(recent, 1):
        L.append("")
        L.append(t("### Turn %d — user:", "### 턴 %d — 사용자:") % i)
        L.append(one_line(trn["user"], 600))
        for kind, val in trn["lines"]:
            if kind == "say":
                L.append(t("  · reply: %s", "  · 답: %s") % val)
            elif kind == "tool":
                L.append("  · 🔧 %s" % val)
            elif kind == "err":
                L.append("  · ❌ %s" % val)
    L.append("")
    L.append(t("## LAST ASSISTANT MESSAGE (full)", "## LAST ASSISTANT MESSAGE (전문)"))
    L.append(last_assistant or t("(none)", "(없음)"))

    out = "\n".join(L)
    if len(out) > MAX_OUT:
        out = out[:MAX_OUT - 40] + t("\n…(digest truncated: 15k-char limit)",
                                       "\n…(요약이 잘림: 1.5만자 한도)")
    print(out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(t("(digest failed: %s)", "(digest 실패: %s)") % e)
        sys.exit(0)
