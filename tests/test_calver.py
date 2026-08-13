"""Calendar Versioning の計算 (scripts/calver.py) のテスト。"""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import calver  # noqa: E402

TODAY = dt.date(2026, 8, 13)


class ParseVersionTest(unittest.TestCase):
    def test_accepts_bare_and_prefixed_versions(self):
        self.assertEqual(calver.parse_version("2026.08.13.0"), (2026, 8, 13, 0))
        self.assertEqual(calver.parse_version("v2026.08.13.7"), (2026, 8, 13, 7))

    def test_rejects_other_shapes(self):
        for text in (
            "2026.8.13.0",  # 月日はゼロ埋め必須
            "2026.08.13",  # MICRO なし
            "v2026.08.13.0-rc1",
            "1.2.3",
            "vnext",
            "",
        ):
            with self.subTest(text=text):
                self.assertIsNone(calver.parse_version(text))


class NextVersionTest(unittest.TestCase):
    def test_first_release_of_the_day_starts_at_zero(self):
        self.assertEqual(calver.next_version(TODAY, []), "2026.08.13.0")

    def test_same_day_release_increments_micro(self):
        self.assertEqual(calver.next_version(TODAY, ["v2026.08.13.0"]), "2026.08.13.1")
        self.assertEqual(
            calver.next_version(TODAY, ["v2026.08.13.0", "v2026.08.13.1"]),
            "2026.08.13.2",
        )

    def test_micro_follows_the_highest_not_the_count(self):
        # タグを1つ消した後でも既存バージョンを踏まないこと
        self.assertEqual(
            calver.next_version(TODAY, ["v2026.08.13.0", "v2026.08.13.5"]),
            "2026.08.13.6",
        )

    def test_micro_resets_on_a_new_day(self):
        self.assertEqual(calver.next_version(TODAY, ["v2026.08.12.3"]), "2026.08.13.0")
        self.assertEqual(
            calver.next_version(dt.date(2026, 9, 1), ["v2026.08.13.9"]),
            "2026.09.01.0",
        )

    def test_ignores_tags_that_are_not_calver(self):
        self.assertEqual(
            calver.next_version(TODAY, ["v1.0.0", "nightly", "v2026.08.13.0"]),
            "2026.08.13.1",
        )

    def test_pads_single_digit_month_and_day(self):
        self.assertEqual(calver.next_version(dt.date(2027, 1, 5), []), "2027.01.05.0")


class SourceFileTest(unittest.TestCase):
    def test_reads_the_version_written_in_the_hook(self):
        sys.path.insert(0, str(REPO_ROOT))
        import herdr_auto_title

        self.assertEqual(calver.read_version(), herdr_auto_title.__version__)

    def test_write_version_replaces_exactly_one_line(self):
        original = calver.SOURCE_FILE.read_text(encoding="utf-8")
        self.addCleanup(calver.SOURCE_FILE.write_text, original, encoding="utf-8")

        calver.write_version("2026.08.13.4")
        self.assertEqual(calver.read_version(), "2026.08.13.4")

        updated = calver.SOURCE_FILE.read_text(encoding="utf-8")
        self.assertEqual(len(updated.splitlines()), len(original.splitlines()))
        self.assertEqual(updated.count('__version__ = "'), 1)


class TodayTest(unittest.TestCase):
    def test_explicit_date_is_used_as_given(self):
        self.assertEqual(calver.today_from("2026-08-13"), TODAY)

    def test_no_date_means_a_local_date(self):
        # ローカルタイムゾーンの日付。境界をまたぐ可能性があるので前後1日を許す
        today = dt.datetime.now().astimezone().date()
        self.assertIn(
            calver.today_from(None),
            (today - dt.timedelta(days=1), today, today + dt.timedelta(days=1)),
        )


if __name__ == "__main__":
    unittest.main()
