#!/usr/bin/env python3
"""herdr auto title

Claude Code / Codex の UserPromptSubmit hook として動作し、会話内容から短い
タイトルを生成して herdr のタブ名 (tab.rename) に反映する。

hook は即座に制御を返し、タイトル生成はバックグラウンドで走る。
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from shutil import which

SOURCE = "herdr-auto-title"

CLAUDE = "claude"
CODEX = "codex"

# ---------------------------------------------------------------- 設定


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


STATE_DIR = Path(
    os.environ.get("HERDR_AUTO_TITLE_STATE_DIR")
    or (Path.home() / ".claude" / "herdr-auto-title")
)
# タイトル生成に使う CLI。auto は hook を呼んだエージェント側を優先する
BACKEND = (os.environ.get("HERDR_AUTO_TITLE_BACKEND") or "auto").strip().lower()
MODEL = os.environ.get("HERDR_AUTO_TITLE_MODEL") or ""
DEFAULT_MODEL = {CLAUDE: "haiku", CODEX: "gpt-5.4-mini"}
# タブバーに収まる表示幅 (半角を 1, 全角を 2 として数える)
MAX_WIDTH = _env_int("HERDR_AUTO_TITLE_MAX_WIDTH", 28)
# 何プロンプトごとにタイトルを作り直すか。既定の 0 は「最初に付けたら以後変えない」
REGEN_EVERY = _env_int("HERDR_AUTO_TITLE_REGEN_EVERY", 0)
TIMEOUT_SEC = _env_int("HERDR_AUTO_TITLE_TIMEOUT", 90)
MAX_BUDGET_USD = os.environ.get("HERDR_AUTO_TITLE_MAX_BUDGET_USD") or "0.05"
# claude -p に渡す会話ログの最大文字数
MAX_LOG_CHARS = _env_int("HERDR_AUTO_TITLE_MAX_LOG_CHARS", 1500)
DEBUG = _env_flag("HERDR_AUTO_TITLE_DEBUG")

JA = "ja"
EN = "en"


def resolve_language() -> str:
    """タイトルを書く言語を決める。

    HERDR_AUTO_TITLE_LANG が最優先。既定の auto ではロケールを見て、日本語
    ロケールなら日本語、それ以外は英語にする。
    """
    raw = (os.environ.get("HERDR_AUTO_TITLE_LANG") or "auto").strip().lower()
    if raw.startswith(JA):
        return JA
    if raw and raw != "auto":
        return EN
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if (os.environ.get(name) or "").strip().lower().startswith(JA):
            return JA
    return EN


LANG = resolve_language()

SYSTEM_PROMPTS = {
    JA: (
        "あなたは作業ログから短いタイトルを作るツールです。"
        "入力された会話から、その作業内容を表す日本語タイトルを1つだけ出力します。"
        f"制約: 全角{MAX_WIDTH // 2}文字以内 / 体言止め / "
        "記号・引用符・句読点・絵文字を使わない / "
        "説明や前置きを一切書かず、タイトル本文のみを1行で出力する。"
    ),
    EN: (
        "You are a tool that writes a short title for a work log. "
        "Given a conversation, you output exactly one English title describing the work. "
        f"Constraints: at most {MAX_WIDTH} characters / a noun phrase with no trailing period / "
        "no symbols, quotes, punctuation or emoji / "
        "no explanation or preamble, output only the title itself on a single line."
    ),
}
SYSTEM_PROMPT = SYSTEM_PROMPTS[LANG]


def log(message: str) -> None:
    if not DEBUG:
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with (STATE_DIR / "debug.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- herdr socket API


def herdr_call(method: str, params: dict, timeout: float = 3.0) -> dict | None:
    """herdr の Unix ソケット API を 1 リクエストだけ呼ぶ。"""
    socket_path = os.environ.get("HERDR_SOCKET_PATH")
    if not socket_path:
        return None
    request = {
        "id": f"{SOURCE}:{time.time_ns()}",
        "method": method,
        "params": params,
    }
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(timeout)
        client.connect(socket_path)
        client.sendall((json.dumps(request) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = client.recv(65536)
            if not chunk:
                break
            buf += chunk
        client.close()
    except OSError as exc:
        log(f"socket error on {method}: {exc}")
        return None
    line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        log(f"bad response for {method}: {line[:200]}")
        return None


def resolve_tab_id() -> str | None:
    """このペインが今いるタブの ID を返す。

    ペインが別ワークスペースへ移動していると環境変数の HERDR_TAB_ID は古くなる
    ため、pane.get の結果を優先する。
    """
    pane_id = os.environ.get("HERDR_PANE_ID")
    if pane_id:
        response = herdr_call("pane.get", {"pane_id": pane_id})
        tab_id = (response or {}).get("result", {}).get("pane", {}).get("tab_id")
        if isinstance(tab_id, str) and tab_id:
            return tab_id
    fallback = os.environ.get("HERDR_TAB_ID")
    return fallback or None


def get_tab_label(tab_id: str) -> str | None:
    response = herdr_call("tab.get", {"tab_id": tab_id})
    label = (response or {}).get("result", {}).get("tab", {}).get("label")
    return label if isinstance(label, str) else None


# ---------------------------------------------------------------- transcript の読み取り

SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
COMMAND_TAG_RE = re.compile(r"</?command-[a-z-]+>", re.IGNORECASE)
TAG_LINE_RE = re.compile(r"<(command-message|command-contents)>.*?</\1>", re.DOTALL)


def clean_prompt_text(text: str) -> str:
    """ユーザー発言から hook やスラッシュコマンドの機械的なノイズを取り除く。"""
    if "<local-command-stdout>" in text:
        return ""
    text = SYSTEM_REMINDER_RE.sub(" ", text)
    text = TAG_LINE_RE.sub(" ", text)

    command = COMMAND_NAME_RE.search(text)
    args = COMMAND_ARGS_RE.search(text)
    if command:
        # スラッシュコマンド呼び出しは「コマンド名 + 引数」に畳む
        parts = [command.group(1).strip()]
        if args and args.group(1).strip():
            parts.append(args.group(1).strip())
        text = " ".join(parts)

    text = COMMAND_TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def iter_transcript_entries(transcript_path: str | None):
    """transcript (JSON Lines) を 1 エントリずつ返す。"""
    if not transcript_path:
        return
    try:
        raw = Path(transcript_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log(f"cannot read transcript: {exc}")
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            yield entry


def claude_user_prompts(transcript_path: str | None) -> list[str]:
    """Claude Code の transcript から人間の入力だけを時系列で取り出す。"""
    prompts: list[str] = []
    for entry in iter_transcript_entries(transcript_path):
        if entry.get("type") != "user":
            continue
        # ツール結果・メタ挿入・サブエージェントの発言は人間の入力ではない
        if entry.get("toolUseResult") is not None:
            continue
        if entry.get("isMeta") or entry.get("isSidechain"):
            continue
        if entry.get("sourceToolUseID"):
            continue

        content = entry.get("message", {}).get("content")
        if isinstance(content, str):
            chunks = [content]
        elif isinstance(content, list):
            chunks = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
        else:
            continue

        cleaned = clean_prompt_text(" ".join(chunks))
        if cleaned:
            prompts.append(cleaned)
    return prompts


def codex_user_prompts(transcript_path: str | None) -> list[str]:
    """Codex の rollout から人間の入力だけを時系列で取り出す。

    rollout には response_item として role=user のメッセージも並ぶが、そちらには
    <environment_context> のようにモデル向けへ合成されたものが混ざる。実際に人が
    打った発言は event_msg / user_message だけなので、そこからしか読まない。
    """
    prompts: list[str] = []
    for entry in iter_transcript_entries(transcript_path):
        if entry.get("type") != "event_msg":
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "user_message":
            continue
        message = payload.get("message")
        if not isinstance(message, str):
            continue
        cleaned = clean_prompt_text(message)
        if cleaned:
            prompts.append(cleaned)
    return prompts


def extract_user_prompts(transcript_path: str | None, agent: str) -> list[str]:
    if agent == CODEX:
        return codex_user_prompts(transcript_path)
    return claude_user_prompts(transcript_path)


def build_conversation_log(prompts: list[str]) -> str:
    """先頭の発言を必ず残しつつ、上限文字数に収まるログを作る。"""
    if not prompts:
        return ""
    head = prompts[0][:MAX_LOG_CHARS]
    lines = [f"1. {head}"]
    used = len(head)
    # 直近の発言ほど現在の作業内容を表すので、後ろから詰めていく
    tail: list[str] = []
    for index in range(len(prompts) - 1, 0, -1):
        remaining = MAX_LOG_CHARS - used
        if remaining <= 40:
            break
        piece = prompts[index][:remaining]
        tail.append(f"{index + 1}. {piece}")
        used += len(piece)
    lines.extend(reversed(tail))
    return "\n".join(lines)


# ---------------------------------------------------------------- タイトル生成

TRIM_CHARS = " \t　\"'`「」『』【】[]()<>*#-—…。．.:："
PREFIX_RE = re.compile(r"^(タイトル|title)\s*[:：]\s*", re.IGNORECASE)


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def truncate(text: str, max_width: int) -> str:
    if display_width(text) <= max_width:
        return text
    out: list[str] = []
    width = 0
    for ch in text:
        step = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if width + step > max_width - 1:
            break
        out.append(ch)
        width += step
    return "".join(out).rstrip(TRIM_CHARS) + "…"


def sanitize_title(raw: str) -> str:
    """モデル出力を 1 行のタブ名として使える形に整える。"""
    text = "".join(ch for ch in raw if ch == "\n" or unicodedata.category(ch)[0] != "C")
    # 空行を除いた最後の行を採る (思考めいた前置きが混ざった場合の保険)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    title = PREFIX_RE.sub("", lines[-1]).strip(TRIM_CHARS)
    title = re.sub(r"\s+", " ", title)
    if not title:
        return ""
    return truncate(title, MAX_WIDTH)


def user_message_for(conversation_log: str, agent: str) -> str:
    name = "Codex" if agent == CODEX else "Claude Code"
    if LANG == JA:
        return (
            f"以下は {name} の作業セッションでユーザーが入力した発言のログです。"
            "この作業に付けるタイトルを1行で出力してください。\n\n"
            f"{conversation_log}"
        )
    return (
        f"Below is a log of the prompts a user typed during a {name} session. "
        "Output a title for this work on a single line.\n\n"
        f"{conversation_log}"
    )


def child_env() -> dict:
    env = dict(os.environ)
    # 呼び出し先のエージェントを herdr 連携の対象にしない
    for key in ("HERDR_ENV", "HERDR_PANE_ID", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID"):
        env.pop(key, None)
    env.pop("CLAUDECODE", None)
    return env


def run_generator(command: list[str], stdin_text: str | None) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            command,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
            # プロジェクト固有の設定を巻き込まないよう作業ディレクトリを離す
            cwd=str(Path.home()),
            env=child_env(),
        )
    except FileNotFoundError:
        log(f"{command[0]} command not found")
    except subprocess.TimeoutExpired:
        log(f"{command[0]} timed out")
    return None


def generate_with_claude(conversation_log: str, model: str) -> str:
    """claude -p を最小構成で呼び、タイトル 1 行を得る。"""
    command = [
        "claude",
        "-p",
        # --safe-mode で CLAUDE.md / skills / plugins / hooks / MCP を読み込ませない。
        # 起動が速くなるうえ、この呼び出しが herdr integration hook を再入させない。
        "--safe-mode",
        "--model",
        model,
        "--tools",
        "",
        "--no-session-persistence",
        "--max-budget-usd",
        MAX_BUDGET_USD,
        "--system-prompt",
        SYSTEM_PROMPT,
    ]
    completed = run_generator(command, user_message_for(conversation_log, CLAUDE))
    if completed is None:
        return ""
    if completed.returncode != 0:
        log(f"claude -p failed ({completed.returncode}): {completed.stderr[:300]}")
        return ""
    return sanitize_title(completed.stdout)


def generate_with_codex(conversation_log: str, model: str) -> str:
    """codex exec を最小構成で呼び、タイトル 1 行を得る。

    codex には --system-prompt が無いのでプロンプトへ前置きし、最終メッセージだけを
    --output-last-message で受け取る (途中経過が stdout に混ざらない)。
    """
    prompt = f"{SYSTEM_PROMPT}\n\n{user_message_for(conversation_log, CODEX)}"
    with tempfile.TemporaryDirectory(prefix="herdr-auto-title-") as tmp:
        out_file = Path(tmp) / "title.txt"
        command = [
            "codex",
            "exec",
            # config.toml を読ませない。MCP や hooks を巻き込まず起動が速くなるうえ、
            # この呼び出しが herdr 連携 hook を再入させない。
            "--ignore-user-config",
            "--disable",
            "hooks",
            # セッションファイルを残さない
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--model",
            model,
            # AGENTS.md を読ませない
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            "model_reasoning_effort=none",
            "--output-last-message",
            str(out_file),
            prompt,
        ]
        completed = run_generator(command, None)
        if completed is None:
            return ""
        if completed.returncode != 0:
            log(f"codex exec failed ({completed.returncode}): {completed.stderr[-300:]}")
            return ""
        try:
            raw = out_file.read_text(encoding="utf-8")
        except OSError as exc:
            log(f"cannot read codex output: {exc}")
            return ""
    return sanitize_title(raw)


def resolve_backend(agent: str) -> str:
    """どの CLI でタイトルを作るか決める。"""
    if BACKEND in (CLAUDE, CODEX):
        return BACKEND
    # 既定は hook を呼んだエージェント自身。無ければもう一方に回す
    other = CODEX if agent == CLAUDE else CLAUDE
    for candidate in (agent, other):
        if which(candidate):
            return candidate
    return agent


def generate_title(conversation_log: str, agent: str) -> str:
    backend = resolve_backend(agent)
    model = MODEL or DEFAULT_MODEL[backend]
    log(f"generating with {backend} ({model})")
    if backend == CODEX:
        return generate_with_codex(conversation_log, model)
    return generate_with_claude(conversation_log, model)


# ---------------------------------------------------------------- 状態


def state_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id) or "unknown"
    return STATE_DIR / f"{safe}.json"


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    try:
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log(f"cannot save state: {exc}")


AUTO_LABEL_RE = re.compile(r"^\d+$")


def may_overwrite(label: str | None, previous_title: str | None) -> bool:
    """このタブ名を上書きしてよいか判定する。

    herdr が付ける既定ラベル (連番) と、前回自分が付けたタイトルだけを対象にし、
    ユーザーが手で付けた名前は尊重して触らない。
    """
    if label is None or not label.strip():
        return True
    if AUTO_LABEL_RE.match(label.strip()):
        return True
    return previous_title is not None and label == previous_title


# ---------------------------------------------------------------- 本体


def detect_agent(hook_input: dict) -> str:
    """hook を呼んだのが Claude Code と Codex のどちらかを見分ける。

    hook 入力のフィールドはおおむね共通だが、turn_id は Codex にしかない。
    """
    if hook_input.get("turn_id"):
        return CODEX
    return CLAUDE


def run(hook_input: dict) -> None:
    session_id = hook_input.get("session_id") or "unknown"
    path = state_path(session_id)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    lock_file = path.with_suffix(".lock")
    try:
        lock_handle = lock_file.open("w")
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another run holds the lock; skipping")
        return

    state = load_state(path)
    prompt_count = int(state.get("prompt_count", 0)) + 1
    generated_at = int(state.get("generated_at_prompt", 0))
    previous_title = state.get("title")

    state["prompt_count"] = prompt_count
    save_state(path, state)

    if previous_title is None:
        due = True
    elif REGEN_EVERY > 0:
        due = prompt_count - generated_at >= REGEN_EVERY
    else:
        due = False
    if not due:
        log(f"prompt {prompt_count}: not due (last generated at {generated_at})")
        return

    tab_id = resolve_tab_id()
    if not tab_id:
        log("cannot resolve tab id")
        return

    label = get_tab_label(tab_id)
    if not may_overwrite(label, previous_title):
        log(f"tab {tab_id} has a manual label {label!r}; leaving it alone")
        return

    agent = detect_agent(hook_input)
    log(f"prompt {prompt_count}: agent={agent}")
    prompts = extract_user_prompts(hook_input.get("transcript_path"), agent)
    current = clean_prompt_text(hook_input.get("prompt") or "")
    if current and (not prompts or prompts[-1] != current):
        # UserPromptSubmit の時点では今回の発言がまだ transcript に無いことがある
        prompts.append(current)
    conversation_log = build_conversation_log(prompts)
    if not conversation_log:
        log("no usable user prompts")
        return

    title = generate_title(conversation_log, agent)
    if not title:
        log("no title generated")
        return

    response = herdr_call("tab.rename", {"tab_id": tab_id, "label": title})
    if not response or "result" not in response:
        log(f"tab.rename failed: {response}")
        return

    state["title"] = title
    state["tab_id"] = tab_id
    state["generated_at_prompt"] = prompt_count
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    save_state(path, state)
    log(f"tab {tab_id} renamed to {title!r} at prompt {prompt_count}")


def daemonize() -> None:
    """親を即座に終了させ、以降の処理を Claude Code から切り離して続ける。"""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    if devnull > 2:
        os.close(devnull)


def main() -> int:
    raw = sys.stdin.read()
    try:
        hook_input = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        hook_input = {}

    if _env_flag("HERDR_AUTO_TITLE_DISABLE"):
        return 0
    if os.environ.get("HERDR_ENV") != "1":
        return 0
    if not os.environ.get("HERDR_SOCKET_PATH"):
        return 0
    if not os.environ.get("HERDR_PANE_ID") and not os.environ.get("HERDR_TAB_ID"):
        return 0
    # サブエージェントからの呼び出しではタブ名を触らない
    if hook_input.get("agent_id"):
        return 0

    if os.environ.get("HERDR_AUTO_TITLE_FOREGROUND") == "1":
        run(hook_input)
        return 0

    try:
        daemonize()
    except OSError as exc:
        log(f"fork failed: {exc}")
        return 0

    try:
        run(hook_input)
    except Exception as exc:  # hook がユーザー体験を壊さないよう握りつぶす
        log(f"unexpected error: {exc!r}")
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
