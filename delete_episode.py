#!/usr/bin/env python3
"""
Podcastフィードからエピソードを削除する。

使い方:
  python delete_episode.py 想定QA_2
  python delete_episode.py 想定QA_2 --keep-files   # mp3ファイルは残す
  python delete_episode.py 想定QA_2 -y              # 確認なしで削除
"""

import argparse
import json
import shutil
import sys
from html import escape
from pathlib import Path

from summarize_to_audio import load_config

PROJECT_ROOT = Path(__file__).parent


def list_episodes() -> list[str]:
    """episodes.json に登録されているエピソード名の一覧を返す。"""
    episodes_json_path = PROJECT_ROOT / "episodes.json"
    if not episodes_json_path.exists():
        return []
    with open(episodes_json_path, encoding="utf-8") as f:
        all_episodes = json.load(f)
    names = sorted({ep["path"].split("/")[1] for ep in all_episodes if "/" in ep["path"]})
    return names


def regenerate_feed(all_episodes: list[dict], config: dict) -> None:
    """episodes.json を保存し feed.xml を再生成する。"""
    episodes_json_path = PROJECT_ROOT / "episodes.json"
    feed_path = PROJECT_ROOT / "feed.xml"
    base_url = config.get("github_pages_base_url", "").rstrip("/")

    with open(episodes_json_path, "w", encoding="utf-8") as f:
        json.dump(all_episodes, f, ensure_ascii=False, indent=2)

    items_xml = ""
    for i, ep in enumerate(all_episodes, 1):
        file_url = f"{base_url}/{ep['path']}"
        items_xml += f"""    <item>
      <title>{escape(ep["title"])}</title>
      <description>{escape(ep["description"])}</description>
      <enclosure url="{escape(file_url)}" length="{ep["length"]}" type="audio/mpeg" />
      <guid isPermaLink="true">{escape(file_url)}</guid>
      <pubDate>{ep["pub_date"]}</pubDate>
      <itunes:episode>{i}</itunes:episode>
    </item>
"""

    feed_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{escape(config.get("podcast_title", "Audio Paper Summary"))}</title>
    <link>{escape(base_url)}</link>
    <description>{escape(config.get("podcast_description", ""))}</description>
    <language>{escape(config.get("podcast_language", "en"))}</language>
    <itunes:type>episodic</itunes:type>
    <itunes:author>{escape(config.get("podcast_author", ""))}</itunes:author>
    <itunes:explicit>false</itunes:explicit>
    <itunes:category text="Education" />
    <itunes:image href="{escape(base_url)}/cover.jpg" />
    <itunes:owner>
      <itunes:name>{escape(config.get("podcast_author", ""))}</itunes:name>
      <itunes:email>{escape(config.get("podcast_email", "noreply@example.com"))}</itunes:email>
    </itunes:owner>
{items_xml}  </channel>
</rss>
"""

    feed_path.write_text(feed_xml, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Podcastフィードからエピソードを削除する"
    )
    parser.add_argument(
        "episode_name", nargs="?", default=None,
        help="削除するエピソード名 (例: 想定QA_2)",
    )
    parser.add_argument(
        "--list", "-l", action="store_true",
        help="登録されているエピソード一覧を表示",
    )
    parser.add_argument(
        "--keep-files", action="store_true",
        help="mp3ファイルを削除せずフィードからのみ除去する",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="確認なしで削除する",
    )
    args = parser.parse_args()

    if args.list or args.episode_name is None:
        names = list_episodes()
        if not names:
            print("登録されているエピソードはありません。")
        else:
            print("登録されているエピソード:")
            for name in names:
                print(f"  - {name}")
        if args.episode_name is None:
            return
        return

    episode_name = args.episode_name

    # episodes.json 読み込み
    episodes_json_path = PROJECT_ROOT / "episodes.json"
    if not episodes_json_path.exists():
        print("Error: episodes.json が見つかりません", file=sys.stderr)
        sys.exit(1)

    with open(episodes_json_path, encoding="utf-8") as f:
        all_episodes = json.load(f)

    # 該当エントリを検索
    prefix = f"episodes/{episode_name}/"
    to_remove = [ep for ep in all_episodes if ep["path"].startswith(prefix)]

    if not to_remove:
        print(f"Error: '{episode_name}' に一致するエピソードが見つかりません", file=sys.stderr)
        print("\n登録されているエピソード:")
        for name in list_episodes():
            print(f"  - {name}")
        sys.exit(1)

    # 確認
    print(f"以下の {len(to_remove)} 件のエントリを削除します:")
    for ep in to_remove:
        print(f"  - {ep['title']}")

    episode_dir = PROJECT_ROOT / "episodes" / episode_name
    if not args.keep_files and episode_dir.exists():
        print(f"\nディレクトリも削除します: {episode_dir}")

    if not args.yes:
        answer = input("\n実行しますか? [y/N]: ").strip().lower()
        if answer != "y":
            print("キャンセルしました。")
            return

    # episodes.json からエントリ削除
    remaining = [ep for ep in all_episodes if not ep["path"].startswith(prefix)]

    # feed.xml 再生成
    config = load_config()
    regenerate_feed(remaining, config)
    print(f"\nepisodes.json と feed.xml を更新しました ({len(to_remove)} 件削除)")

    # ファイル削除
    if not args.keep_files and episode_dir.exists():
        shutil.rmtree(episode_dir)
        print(f"ディレクトリ削除: {episode_dir}")

    print("完了!")


if __name__ == "__main__":
    main()
