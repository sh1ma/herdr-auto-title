#!/bin/sh
# Register herdr auto title as a UserPromptSubmit hook for Claude Code / Codex.
#
#   ./install.sh              install / update for every agent found
#   ./install.sh --claude     Claude Code only
#   ./install.sh --codex      Codex only
#   ./install.sh --uninstall  uninstall (narrow it down with --claude / --codex)
set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
source_file="$script_dir/herdr_auto_title.py"
claude_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
codex_dir="${CODEX_HOME:-$HOME/.codex}"

mode=install
targets=""

for arg in "$@"; do
  case "$arg" in
    --uninstall) mode=uninstall ;;
    --claude) targets="$targets claude" ;;
    --codex) targets="$targets codex" ;;
    -h | --help)
      sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "usage: $0 [--claude] [--codex] [--uninstall]" >&2
      exit 2
      ;;
  esac
done

command -v python3 >/dev/null 2>&1 || {
  echo "python3 not found" >&2
  exit 1
}

# 対象の指定がなければ、入っているエージェントを探して全部に入れる
if [ -z "$targets" ]; then
  if [ -d "$claude_dir" ] || command -v claude >/dev/null 2>&1; then
    targets="$targets claude"
  fi
  if [ -d "$codex_dir" ] || command -v codex >/dev/null 2>&1; then
    targets="$targets codex"
  fi
fi
if [ -z "$targets" ]; then
  echo "neither Claude Code nor Codex found ($claude_dir / $codex_dir)" >&2
  exit 1
fi

if [ "$mode" = install ]; then
  [ -f "$source_file" ] || {
    echo "herdr_auto_title.py not found: $source_file" >&2
    exit 1
  }
fi

for target in $targets; do
  case "$target" in
    claude)
      hook_file="$claude_dir/hooks/herdr-auto-title.py"
      settings_file="$claude_dir/settings.json"
      label="Claude Code"
      ;;
    codex)
      hook_file="$codex_dir/hooks/herdr-auto-title.py"
      settings_file="$codex_dir/hooks.json"
      label="Codex"
      ;;
  esac

  echo "--- $label"
  if [ "$mode" = install ]; then
    mkdir -p "$(dirname -- "$hook_file")"
    cp "$source_file" "$hook_file"
    chmod +x "$hook_file"
  fi

  HAT_MODE="$mode" HAT_SETTINGS="$settings_file" HAT_TARGET="$hook_file" python3 - <<'PY'
import json
import os
import shutil
from pathlib import Path

mode = os.environ["HAT_MODE"]
settings_path = Path(os.environ["HAT_SETTINGS"])
target = os.environ["HAT_TARGET"]
command = f"python3 '{target}'"
event = "UserPromptSubmit"

settings = {}
if settings_path.exists():
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"cannot read {settings_path}: {exc}")
    if not isinstance(settings, dict):
        raise SystemExit(f"{settings_path} does not contain an object")

hooks = settings.setdefault("hooks", {})
if not isinstance(hooks, dict):
    raise SystemExit(f"hooks in {settings_path} is not an object")
entries = hooks.get(event) or []
if not isinstance(entries, list):
    raise SystemExit(f"hooks.{event} in {settings_path} is not an array")


def is_ours(hook):
    return isinstance(hook, dict) and "herdr-auto-title.py" in str(hook.get("command", ""))


# 既存の登録を取り除いてから、必要なら入れ直す (冪等かつパス変更にも追従する)
cleaned = []
for entry in entries:
    if not isinstance(entry, dict):
        cleaned.append(entry)
        continue
    inner = [h for h in entry.get("hooks", []) if not is_ours(h)]
    if inner:
        entry = dict(entry, hooks=inner)
        cleaned.append(entry)
    elif not entry.get("hooks"):
        cleaned.append(entry)

changed_to = None
if mode == "install":
    cleaned.append({
        "hooks": [{"type": "command", "command": command, "timeout": 10}]
    })
    changed_to = command

if cleaned:
    hooks[event] = cleaned
else:
    hooks.pop(event, None)
if not hooks:
    settings.pop("hooks", None)

backup = settings_path.with_suffix(settings_path.suffix + ".bak")
if settings_path.exists():
    shutil.copyfile(settings_path, backup)
settings_path.parent.mkdir(parents=True, exist_ok=True)
settings_path.write_text(
    json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

if mode == "install":
    print(f"installed: {event} -> {changed_to}")
else:
    print(f"uninstalled: {event}")
print(f"settings: {settings_path} (backup: {backup})")
PY

  if [ "$mode" = uninstall ]; then
    rm -f "$hook_file"
    echo "removed: $hook_file"
  fi
done

echo
echo "The new setting takes effect from the next session you start."
case " $targets " in
  *" codex "*)
    if [ "$mode" = install ]; then
      echo "Codex does not run hooks it has not been told to trust. Trust it on the review screen at the next startup, or via /hooks."
    fi
    ;;
esac
