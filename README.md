# herdr auto title

Claude Code / Codex の会話内容から短いタイトルを自動生成して、[herdr](https://herdr.dev) のタブ名に反映する hook。

herdr のタブは既定で `1`, `2`, ... という連番ラベルなので、同じワークスペースに複数タブを開くと何をしていたタブなのか分からなくなる。この hook はプロンプト送信のたびに（正確には後述の条件で）会話の内容を要約し、`tab.rename` でタブ名を書き換える。

```
[ 1 ] [ 2 ] [ 3 ]        →   [ herdr自動タイトル生成 ] [ D1マイグレーション修正 ] [ CVE-2026-1234調査 ]
```

## 仕組み

1. Claude Code / Codex の `UserPromptSubmit` hook として起動する
2. 即座に `fork` して制御を返す（プロンプト送信は待たされない。実測 0.03 秒弱）
3. バックグラウンドで transcript からユーザーの発言だけを抽出する
   - Claude Code: ツール結果・`<system-reminder>`・サブエージェントの発言などは除外する
   - Codex: rollout の `event_msg` / `user_message` だけを読む（`response_item` 側には `<environment_context>` などモデル向けに合成されたものが混ざるため）
   - スラッシュコマンドは `/goal 引数` の形に畳む
4. `claude -p` か `codex exec` に投げてタイトルを 1 行生成する
5. herdr の Unix ソケット API (`$HERDR_SOCKET_PATH`) に `tab.rename` を送る

どちらのエージェントから呼ばれたかは hook 入力で見分ける（`turn_id` は Codex にしかない）。既定では **呼び出し元と同じ CLI** でタイトルを作り、それが `PATH` になければもう一方に回す。`HERDR_AUTO_TITLE_BACKEND` で固定もできる。

生成はどちらも最小構成で呼ぶ。設定ファイルを読み込ませないので起動が速くなるうえ、この呼び出しが herdr 連携 hook を再入させることもない。

| | 起動オプション |
| --- | --- |
| `claude -p` | `--safe-mode`（CLAUDE.md・skills・plugins・hooks・MCP を読まない）/ `--tools ""` / `--no-session-persistence` / `--max-budget-usd` |
| `codex exec` | `--ignore-user-config`（config.toml を読まないので MCP も hooks も付いてこない）/ `--disable hooks` / `--ephemeral`（セッションを残さない）/ `--sandbox read-only` / `-c project_doc_max_bytes=0`（AGENTS.md を読まない） |

`codex exec` は `--system-prompt` を持たないので、制約はプロンプトに前置きし、結果は `--output-last-message` で受け取る（途中経過が stdout に混ざらない）。

## 必要なもの

- herdr のペイン内で動いている Claude Code か Codex（`HERDR_ENV=1` が立っていること）
- `python3`（標準ライブラリのみ使用）
- `PATH` の通った `claude` か `codex`

herdr の外で起動された場合は何もせずに終了する。

## インストール

```sh
git clone https://github.com/sh1ma/herdr-auto-title
cd herdr-auto-title
./install.sh              # 入っているエージェント全部に入れる
./install.sh --codex      # 片方だけにするとき
```

スクリプトを置き、`UserPromptSubmit` に登録する。

| | スクリプト | 登録先 |
| --- | --- | --- |
| Claude Code | `~/.claude/hooks/herdr-auto-title.py` | `~/.claude/settings.json` |
| Codex | `~/.codex/hooks/herdr-auto-title.py` | `~/.codex/hooks.json` |

（`CLAUDE_CONFIG_DIR` / `CODEX_HOME` が設定されていればそちらを見る）

既存の hook 設定は保持し、再実行しても登録が重複しない。書き換え前の設定は `.bak` に残る。

反映されるのは **次に起動するセッションから**。

Codex は未確認の hook を実行しない。初回起動時のレビュー画面か `/hooks` で信頼すると動き出す（信頼の対象は登録されたコマンド文字列なので、置き場所を変えない限り再インストールしても確認し直しにはならない）。

## タイトルを書き換える条件

タブ名を勝手に奪わないよう、次の条件をすべて満たすときだけ `tab.rename` する。

- 現在のタブ名が herdr 既定の連番（`1`, `2`, ...）か、空か、**前回この hook 自身が付けたタイトル**であること
  - 手動で付けたタブ名は上書きしない
- タイトル生成の対象プロンプトであること
  - 既定では **セッションで最初の1回だけ生成し、以後は変更しない**
  - `HERDR_AUTO_TITLE_REGEN_EVERY` に 1 以上を指定すると、そのプロンプト数ごとに作り直す
  - ただし生成に失敗してタイトルが付いていない間は、次のプロンプトで再試行する

セッションごとの状態は `~/.claude/herdr-auto-title/<session_id>.json` に持つ。同時起動は flock で 1 つに制限される。置き場所はエージェントによらず共通なので、Codex だけで使うときに `~/.claude` を作らせたくなければ `HERDR_AUTO_TITLE_STATE_DIR` を指定する。

ペインを別ワークスペースへ移動するとタブ ID が変わるため、環境変数の `HERDR_TAB_ID` ではなく `pane.get` で今いるタブを毎回解決している。

## 設定

すべて環境変数で上書きできる。

| 環境変数 | 既定値 | 内容 |
| --- | --- | --- |
| `HERDR_AUTO_TITLE_DISABLE` | – | `1` で hook を無効化する |
| `HERDR_AUTO_TITLE_BACKEND` | `auto` | タイトル生成に使う CLI。`claude` / `codex` で固定できる。`auto` は呼び出し元のエージェントを優先する |
| `HERDR_AUTO_TITLE_MODEL` | バックエンド依存 | タイトル生成に使うモデル。既定は `claude` なら `haiku`、`codex` なら `gpt-5.4-mini` |
| `HERDR_AUTO_TITLE_MAX_WIDTH` | `28` | タブ名の最大表示幅（半角 1 / 全角 2 で計算し、超えたら `…` で詰める） |
| `HERDR_AUTO_TITLE_REGEN_EVERY` | `0` | `0` は初回のみ（以後変更しない）。`1` 以上でそのプロンプト数ごとに作り直す |
| `HERDR_AUTO_TITLE_TIMEOUT` | `90` | 生成コマンドのタイムアウト秒 |
| `HERDR_AUTO_TITLE_MAX_BUDGET_USD` | `0.05` | 1 回の生成にかけてよい上限額（`claude` バックエンドのみ。`codex exec` に相当するオプションがない） |
| `HERDR_AUTO_TITLE_MAX_LOG_CHARS` | `1500` | モデルに渡す会話ログの最大文字数 |
| `HERDR_AUTO_TITLE_STATE_DIR` | `~/.claude/herdr-auto-title` | 状態ファイルの置き場所 |
| `HERDR_AUTO_TITLE_DEBUG` | – | `1` で `<state dir>/debug.log` に動作ログを書く |
| `HERDR_AUTO_TITLE_FOREGROUND` | – | `1` で fork せず同期実行する（デバッグ用） |

## 動作確認・デバッグ

hook を実際のイベントを待たずに手で走らせる。

```sh
python3 -c '
import json, os
print(json.dumps({
    "session_id": "manual-test",
    "transcript_path": "<transcript の .jsonl>",
    "prompt": "テストプロンプト",
    "hook_event_name": "UserPromptSubmit",
}))' \
| HERDR_AUTO_TITLE_FOREGROUND=1 HERDR_AUTO_TITLE_DEBUG=1 \
  python3 ~/.claude/hooks/herdr-auto-title.py

cat ~/.claude/herdr-auto-title/debug.log
```

Codex として扱わせたいときは `"turn_id": "manual-turn"` を足し、`transcript_path` に `~/.codex/sessions/<日付>/rollout-*.jsonl` を渡す。

タイトルが変わらないときは `debug.log` に理由が出る（`not due` / `has a manual label` / `claude -p failed` / `codex exec failed` など）。

## アンインストール

```sh
./install.sh --uninstall            # 両方から外す
./install.sh --uninstall --codex    # 片方だけ
```

登録を消し、置いたスクリプトを削除する。状態ファイル（`~/.claude/herdr-auto-title/`）は残るので、不要なら手で消す。

## ライセンス

MIT
