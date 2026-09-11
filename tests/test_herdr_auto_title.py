"""hook 本体のうち、外部プロセスや herdr に触らない部分の回帰テスト。"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import herdr_auto_title as hat  # noqa: E402

VERSION_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}\.\d+$")


class VersionTest(unittest.TestCase):
    def test_version_is_calver_or_unreleased(self):
        self.assertTrue(
            hat.__version__ == "unreleased" or VERSION_RE.match(hat.__version__),
            f"想定外のバージョン表記: {hat.__version__!r}",
        )

    @mock.patch("builtins.print")
    def test_version_flag_prints_and_exits_zero(self, printed):
        with mock.patch.object(sys, "argv", ["herdr-auto-title.py", "--version"]):
            self.assertEqual(hat.main(), 0)
        printed.assert_called_once_with(f"{hat.SOURCE} {hat.__version__}")


class LanguageTest(unittest.TestCase):
    def resolve_with(self, **env):
        cleared = dict.fromkeys(("HERDR_AUTO_TITLE_LANG", "LC_ALL", "LC_MESSAGES", "LANG"), "")
        with mock.patch.dict(os.environ, {**cleared, **env}, clear=False):
            return hat.resolve_language()

    def test_explicit_setting_wins_over_locale(self):
        self.assertEqual(
            self.resolve_with(HERDR_AUTO_TITLE_LANG="en", LC_ALL="ja_JP.UTF-8"),
            hat.EN,
        )
        self.assertEqual(
            self.resolve_with(HERDR_AUTO_TITLE_LANG="ja", LC_ALL="en_US.UTF-8"),
            hat.JA,
        )

    def test_auto_follows_locale(self):
        self.assertEqual(self.resolve_with(LANG="ja_JP.UTF-8"), hat.JA)
        self.assertEqual(self.resolve_with(LANG="en_US.UTF-8"), hat.EN)
        self.assertEqual(self.resolve_with(), hat.EN)

    def test_unknown_value_falls_back_to_english(self):
        self.assertEqual(
            self.resolve_with(HERDR_AUTO_TITLE_LANG="fr", LC_ALL="ja_JP.UTF-8"),
            hat.EN,
        )


class WidthTest(unittest.TestCase):
    def test_display_width_counts_wide_characters_as_two(self):
        self.assertEqual(hat.display_width("abc"), 3)
        self.assertEqual(hat.display_width("あいう"), 6)
        self.assertEqual(hat.display_width("aあ"), 3)

    def test_truncate_keeps_short_text_untouched(self):
        self.assertEqual(hat.truncate("short title", 28), "short title")

    def test_truncate_appends_ellipsis_within_budget(self):
        result = hat.truncate("a" * 40, 10)
        self.assertTrue(result.endswith("…"))
        self.assertLessEqual(hat.display_width(result), 10)

    def test_truncate_does_not_split_wide_characters(self):
        result = hat.truncate("あ" * 20, 9)
        self.assertLessEqual(hat.display_width(result), 9)
        self.assertTrue(result.endswith("…"))


class SanitizeTitleTest(unittest.TestCase):
    def test_strips_label_prefix_and_decoration(self):
        self.assertEqual(hat.sanitize_title('Title: "Fix login bug".'), "Fix login bug")
        self.assertEqual(hat.sanitize_title("タイトル: ログイン修正"), "ログイン修正")

    def test_takes_last_non_empty_line(self):
        self.assertEqual(hat.sanitize_title("考えます\n\nログイン修正"), "ログイン修正")

    def test_collapses_whitespace(self):
        self.assertEqual(hat.sanitize_title("fix   login  bug"), "fix login bug")

    def test_drops_control_characters(self):
        self.assertEqual(hat.sanitize_title("fix\x1b[31m login"), "fix[31m login")
        # タブも制御文字なので、詰められるのではなく落ちる
        self.assertEqual(hat.sanitize_title("fix\tlogin"), "fixlogin")

    def test_empty_input_yields_empty_title(self):
        self.assertEqual(hat.sanitize_title(""), "")
        self.assertEqual(hat.sanitize_title("\n \n"), "")
        self.assertEqual(hat.sanitize_title('"""'), "")

    def test_result_fits_max_width(self):
        title = hat.sanitize_title("あ" * 100)
        self.assertLessEqual(hat.display_width(title), hat.MAX_WIDTH)


class CleanPromptTextTest(unittest.TestCase):
    def test_drops_local_command_output(self):
        self.assertEqual(hat.clean_prompt_text("<local-command-stdout>ok"), "")

    def test_removes_system_reminder(self):
        self.assertEqual(
            hat.clean_prompt_text("fix it <system-reminder>\nnoise\n</system-reminder>"),
            "fix it",
        )

    def test_folds_slash_command_into_name_and_args(self):
        text = (
            "<command-name>/goal</command-name>"
            "<command-message>goal</command-message>"
            "<command-args>release setup</command-args>"
        )
        self.assertEqual(hat.clean_prompt_text(text), "/goal release setup")

    def test_folds_slash_command_without_args(self):
        text = "<command-name>/clear</command-name><command-args></command-args>"
        self.assertEqual(hat.clean_prompt_text(text), "/clear")


class ConversationLogTest(unittest.TestCase):
    def test_empty_prompts_yield_empty_log(self):
        self.assertEqual(hat.build_conversation_log([]), "")

    def test_numbers_prompts_in_order(self):
        self.assertEqual(
            hat.build_conversation_log(["first", "second", "third"]),
            "1. first\n2. second\n3. third",
        )

    def test_keeps_first_prompt_and_stays_within_budget(self):
        prompts = ["head" * 200] + ["tail" * 200 for _ in range(10)]
        log = hat.build_conversation_log(prompts)
        self.assertTrue(log.startswith("1. head"))
        self.assertLessEqual(len(log), hat.MAX_LOG_CHARS + 200)


class MayOverwriteTest(unittest.TestCase):
    def test_overwrites_default_and_blank_labels(self):
        self.assertTrue(hat.may_overwrite(None, None))
        self.assertTrue(hat.may_overwrite("  ", None))
        self.assertTrue(hat.may_overwrite("3", None))

    def test_keeps_labels_the_user_typed(self):
        self.assertFalse(hat.may_overwrite("my tab", None))
        self.assertFalse(hat.may_overwrite("my tab", "old title"))

    def test_overwrites_its_own_previous_title(self):
        self.assertTrue(hat.may_overwrite("old title", "old title"))

    def test_overwrites_title_left_by_an_earlier_session_on_the_tab(self):
        # 新しいセッションには previous_title が無いが、タブの記録が一致すれば自分のもの
        self.assertTrue(hat.may_overwrite("old title", None, "old title"))
        self.assertTrue(hat.may_overwrite("old title", "other", "old title"))

    def test_keeps_manual_label_even_when_tab_record_exists(self):
        self.assertFalse(hat.may_overwrite("my tab", None, "old title"))


class DetectAgentTest(unittest.TestCase):
    def test_turn_id_means_codex(self):
        self.assertEqual(hat.detect_agent({"turn_id": "t1"}), hat.CODEX)

    def test_missing_turn_id_means_claude(self):
        self.assertEqual(hat.detect_agent({}), hat.CLAUDE)
        self.assertEqual(hat.detect_agent({"turn_id": ""}), hat.CLAUDE)


class TranscriptTest(unittest.TestCase):
    def write_jsonl(self, entries):
        path = Path(self.tmpdir.name) / "transcript.jsonl"
        path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
        return str(path)

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def test_claude_prompts_skip_non_human_entries(self):
        path = self.write_jsonl(
            [
                {"type": "user", "message": {"content": "first ask"}},
                {"type": "assistant", "message": {"content": "answer"}},
                {"type": "user", "message": {"content": "tool"}, "toolUseResult": {}},
                {"type": "user", "message": {"content": "meta"}, "isMeta": True},
                {"type": "user", "message": {"content": "sub"}, "isSidechain": True},
                {
                    "type": "user",
                    "message": {"content": [{"type": "text", "text": "second ask"}]},
                },
            ]
        )
        self.assertEqual(hat.claude_user_prompts(path), ["first ask", "second ask"])

    def test_codex_prompts_only_come_from_user_message_events(self):
        path = self.write_jsonl(
            [
                {
                    "type": "response_item",
                    "payload": {"role": "user", "content": "synthesised"},
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "real ask"},
                },
                {"type": "event_msg", "payload": {"type": "agent_message"}},
            ]
        )
        self.assertEqual(hat.codex_user_prompts(path), ["real ask"])

    def test_missing_transcript_is_not_an_error(self):
        self.assertEqual(hat.claude_user_prompts(None), [])
        self.assertEqual(hat.claude_user_prompts("/nope/missing.jsonl"), [])

    def test_broken_lines_are_skipped(self):
        path = Path(self.tmpdir.name) / "broken.jsonl"
        path.write_text(
            '{"type": "user", "message": {"content": "ok"}}\nnot json\n[]\n',
            encoding="utf-8",
        )
        self.assertEqual(hat.claude_user_prompts(str(path)), ["ok"])


class StatePathTest(unittest.TestCase):
    def test_session_id_is_reduced_to_a_safe_file_name(self):
        self.assertEqual(hat.state_path("abc-123").name, "abc-123.json")
        self.assertEqual(hat.state_path("../etc/passwd").name, ".._etc_passwd.json")
        self.assertEqual(hat.state_path("").name, "unknown.json")

    def test_tab_state_lives_under_its_own_directory(self):
        path = hat.tab_state_path("tab/1")
        self.assertEqual(path.parent, hat.STATE_DIR / "tabs")
        self.assertEqual(path.name, "tab_1.json")


class RunTest(unittest.TestCase):
    """herdr と生成 CLI を差し替えて run() の判断を通しで確認する。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tab = {"label": "3"}
        self.renames: list[str] = []
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(hat, "STATE_DIR", Path(self.tmpdir.name)))
        stack.enter_context(mock.patch.object(hat, "herdr_call", self.fake_herdr_call))
        stack.enter_context(mock.patch.object(hat, "resolve_tab_id", lambda: "t1"))
        stack.enter_context(mock.patch.object(hat, "generate_title", self.fake_generate_title))
        self.next_title = "first title"

    def fake_herdr_call(self, method, params, timeout=3.0):
        if method == "tab.get":
            return {"result": {"tab": {"id": params["tab_id"], "label": self.tab["label"]}}}
        if method == "tab.rename":
            self.tab["label"] = params["label"]
            self.renames.append(params["label"])
            return {"result": {}}
        raise AssertionError(f"unexpected call: {method}")

    def fake_generate_title(self, conversation_log, agent):
        return self.next_title

    def submit(self, session_id, prompt="do the thing"):
        hat.run({"session_id": session_id, "prompt": prompt})

    def test_first_prompt_renames_default_label(self):
        self.submit("s1")
        self.assertEqual(self.renames, ["first title"])
        self.assertEqual(self.tab["label"], "first title")

    def test_new_session_in_the_same_tab_renames_again(self):
        self.submit("s1")
        self.next_title = "second title"
        self.submit("s2")
        self.assertEqual(self.renames, ["first title", "second title"])
        self.assertEqual(self.tab["label"], "second title")

    def test_new_session_leaves_a_label_the_user_typed(self):
        self.submit("s1")
        self.tab["label"] = "my tab"
        self.next_title = "second title"
        self.submit("s2")
        self.assertEqual(self.renames, ["first title"])
        self.assertEqual(self.tab["label"], "my tab")

    def test_same_session_does_not_rename_twice_by_default(self):
        self.submit("s1")
        self.next_title = "second title"
        self.submit("s1")
        self.assertEqual(self.renames, ["first title"])


class MainGuardTest(unittest.TestCase):
    """herdr の外で起動されたときは何もせず抜ける。"""

    def run_main(self, env, stdin="{}"):
        """herdr 由来の環境変数だけを与えて main() を呼ぶ。fork と本処理は差し替える。"""
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
            stack.enter_context(mock.patch.object(sys, "stdin", io.StringIO(stdin)))
            forked = stack.enter_context(mock.patch.object(hat, "daemonize"))
            ran = stack.enter_context(mock.patch.object(hat, "run"))
            code = hat.main()
        return code, forked, ran

    def test_exits_without_herdr_env(self):
        code, forked, ran = self.run_main({})
        self.assertEqual(code, 0)
        forked.assert_not_called()
        ran.assert_not_called()

    def test_exits_when_disabled(self):
        code, forked, ran = self.run_main(
            {
                "HERDR_AUTO_TITLE_DISABLE": "1",
                "HERDR_ENV": "1",
                "HERDR_SOCKET_PATH": "/tmp/herdr.sock",
                "HERDR_PANE_ID": "p1",
            }
        )
        self.assertEqual(code, 0)
        forked.assert_not_called()
        ran.assert_not_called()

    def test_runs_in_foreground_when_asked(self):
        code, forked, ran = self.run_main(
            {
                "HERDR_ENV": "1",
                "HERDR_SOCKET_PATH": "/tmp/herdr.sock",
                "HERDR_PANE_ID": "p1",
                "HERDR_AUTO_TITLE_FOREGROUND": "1",
            }
        )
        self.assertEqual(code, 0)
        forked.assert_not_called()
        ran.assert_called_once_with({})

    def test_subagent_calls_are_ignored(self):
        code, _forked, ran = self.run_main(
            {
                "HERDR_ENV": "1",
                "HERDR_SOCKET_PATH": "/tmp/herdr.sock",
                "HERDR_PANE_ID": "p1",
                "HERDR_AUTO_TITLE_FOREGROUND": "1",
            },
            stdin='{"agent_id": "sub"}',
        )
        self.assertEqual(code, 0)
        ran.assert_not_called()


if __name__ == "__main__":
    unittest.main()
