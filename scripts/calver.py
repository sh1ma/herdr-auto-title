#!/usr/bin/env python3
"""Calendar Versioning (YYYY.0M.0D.MICRO) のバージョン計算。

同日に何度リリースしても衝突しないよう、末尾の MICRO を 0 から数える。

    2026.08.13.0   その日の 1 回目
    2026.08.13.1   同じ日の 2 回目
    2026.08.14.0   日付が変われば 0 に戻る

    calver.py current            ソースに書かれている現在のバージョンを出す
    calver.py next               既存タグと日付から次のバージョンを出す
    calver.py bump               次のバージョンを算出してソースに書き込む
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_FILE = REPO_ROOT / "herdr_auto_title.py"
TAG_PREFIX = "v"

VERSION_RE = re.compile(r"^(?P<year>\d{4})\.(?P<month>\d{2})\.(?P<day>\d{2})\.(?P<micro>\d+)$")
ASSIGN_RE = re.compile(r'^__version__ = ".*"$', re.MULTILINE)


def parse_version(text: str) -> tuple[int, int, int, int] | None:
    """バージョン文字列 (先頭の v は任意) を数値の組にする。形式外なら None。"""
    matched = VERSION_RE.match(text.removeprefix(TAG_PREFIX))
    if matched is None:
        return None
    return (
        int(matched["year"]),
        int(matched["month"]),
        int(matched["day"]),
        int(matched["micro"]),
    )


def format_version(today: dt.date, micro: int) -> str:
    return f"{today.year:04d}.{today.month:02d}.{today.day:02d}.{micro}"


def next_version(today: dt.date, known: list[str]) -> str:
    """同じ日付のバージョンがあれば MICRO を 1 つ進め、なければ 0 にする。"""
    micros = [
        parsed[3]
        for parsed in (parse_version(item) for item in known)
        if parsed is not None and parsed[:3] == (today.year, today.month, today.day)
    ]
    return format_version(today, max(micros) + 1 if micros else 0)


def git_tags() -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "tag", "--list", f"{TAG_PREFIX}*"],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.split()


def read_version() -> str:
    matched = ASSIGN_RE.search(SOURCE_FILE.read_text(encoding="utf-8"))
    if matched is None:
        raise SystemExit(f"__version__ が見つからない: {SOURCE_FILE}")
    return matched.group(0).split('"')[1]


def write_version(version: str) -> None:
    text = SOURCE_FILE.read_text(encoding="utf-8")
    updated, count = ASSIGN_RE.subn(f'__version__ = "{version}"', text)
    if count != 1:
        raise SystemExit(f"__version__ の書き換え箇所が {count} 個ある: {SOURCE_FILE}")
    SOURCE_FILE.write_text(updated, encoding="utf-8")


def today_from(value: str | None) -> dt.date:
    if value is None:
        return dt.datetime.now().astimezone().date()
    return dt.date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("current", "next", "bump"))
    parser.add_argument(
        "--date",
        help="基準日 (YYYY-MM-DD)。既定はローカルタイムゾーンの今日。",
    )
    args = parser.parse_args(argv)

    if args.command == "current":
        print(read_version())
        return 0

    version = next_version(today_from(args.date), git_tags())
    if args.command == "bump":
        write_version(version)
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
