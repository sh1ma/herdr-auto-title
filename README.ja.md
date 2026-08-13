# herdr auto title

Claude Code / Codex の会話内容から短いタイトルを自動生成して、[herdr](https://herdr.dev) のタブ名に反映する hook。

English version: [README.md](README.md)

![連番だったタブ名がプロンプトの内容に合わせて書き換わる様子](docs/demo.gif)

タイトルの生成は呼び出し元と同じ CLI に投げる。Claude Code なら `claude -p`、Codex なら `codex exec`。

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

## タイトルの言語

タイトルは英語で書かれる。ただしロケールが日本語のとき——`LC_ALL` / `LC_MESSAGES` / `LANG` が `ja` で始まるとき——は日本語になる。

`HERDR_AUTO_TITLE_LANG` で固定できる。

| 値 | タイトルの言語 |
| --- | --- |
| `auto`（既定） | ロケールに従う |
| `en` | 英語 |
| `ja` | 日本語 |

hook を実行するエージェントの環境から読むので、そのエージェントを起動する場所で export する。たとえば `~/.zshrc` に:

```sh
export HERDR_AUTO_TITLE_LANG=ja
```

反映されるのは、その後に起動したセッションから。

## アンインストール

```sh
./install.sh --uninstall            # 両方から外す
./install.sh --uninstall --codex    # 片方だけ
```

登録を消し、置いたスクリプトを削除する。状態ファイル（`~/.claude/herdr-auto-title/`）は残るので、不要なら手で消す。

## 開発

lint とフォーマットは [ruff](https://docs.astral.sh/ruff/)（設定は `ruff.toml`）、`install.sh` は [shellcheck](https://www.shellcheck.net/) で見ている。どちらもプルリクエストごとに CI で走る。

```sh
uvx ruff check          # lint
uvx ruff format         # フォーマット
uvx --from shellcheck-py shellcheck install.sh
```

スクリプトは Python 3.9 の構文で動く状態を保っていて、それも CI で確認している。

## ライセンス

MIT
