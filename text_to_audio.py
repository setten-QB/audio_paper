#!/usr/bin/env python3
"""
text/ ディレクトリ配下のテキストファイルを読み上げ音声に変換し、Podcastフィードに追加する。

使い方:
  python text_to_audio.py text/想定QA.txt
  python text_to_audio.py text/想定QA.txt --lang ja
  python text_to_audio.py text/想定QA.txt --episode-name my_qa_session
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from summarize_to_audio import (
    get_mp3_size,
    lesson_to_speech,
    load_config,
    text_to_speech,
)

CONFIG_PATH = Path(__file__).parent / "config.yaml"
PROJECT_ROOT = Path(__file__).parent

# TTS の速度目安: 英語 約150語/分、日本語 約350文字/分
# 1パートあたり最大5分に収まるようにする
MAX_EN_WORDS_PER_PART = 700
MAX_JA_CHARS_PER_PART = 1700


def _is_english(text: str) -> bool:
    if not text:
        return False
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return ascii_chars / len(text) > 0.7


def detect_language(text: str) -> str:
    """テキスト全体の言語を判定する。"""
    return "en" if _is_english(text) else "ja"


def split_text_into_parts(text: str, lang: str) -> list[str]:
    """テキストをTTS制限に収まるパートに分割する。"""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

    if lang == "en":
        max_size = MAX_EN_WORDS_PER_PART
        measure = lambda t: len(t.split())
    else:
        max_size = MAX_JA_CHARS_PER_PART
        measure = lambda t: len(t)

    parts = []
    current = []
    current_size = 0

    for para in paragraphs:
        para_size = measure(para)
        if current and current_size + para_size > max_size:
            parts.append("\n\n".join(current))
            current = [para]
            current_size = para_size
        else:
            current.append(para)
            current_size += para_size

    if current:
        parts.append("\n\n".join(current))

    return parts


def has_mixed_languages(text: str) -> bool:
    """テキストに日英混在があるか判定する。"""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    langs = {detect_language(p) for p in paragraphs if p}
    return len(langs) > 1


def update_feed(audio_entries: list[dict], config: dict) -> None:
    """episodes.json にエントリを追加し、feed.xml を再生成する。"""
    episodes_json_path = PROJECT_ROOT / "episodes.json"
    feed_path = PROJECT_ROOT / "feed.xml"
    base_url = config.get("github_pages_base_url", "").rstrip("/")

    if episodes_json_path.exists():
        with open(episodes_json_path, encoding="utf-8") as f:
            all_episodes = json.load(f)
    else:
        all_episodes = []

    now = datetime.now(timezone.utc)
    pub_date = now.strftime("%a, %d %b %Y %H:%M:%S +0000")

    for entry in audio_entries:
        all_episodes.append({
            "title": entry["title"],
            "description": entry.get("description", ""),
            "path": entry["path"],
            "length": entry["length"],
            "pub_date": pub_date,
        })

    with open(episodes_json_path, "w", encoding="utf-8") as f:
        json.dump(all_episodes, f, ensure_ascii=False, indent=2)

    # feed.xml 再生成
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
    print(f"RSSフィード更新: {feed_path}")


def main():
    parser = argparse.ArgumentParser(
        description="テキストファイルから読み上げ音声を生成する"
    )
    parser.add_argument("source", help="テキストファイルのパス (例: text/想定QA.txt)")
    parser.add_argument(
        "--lang", default=None, choices=["en", "ja", "mixed"],
        help="言語を指定 (デフォルト: 自動検出。mixed: 日英混在のまま言語切替しながら読み上げ)",
    )
    parser.add_argument(
        "--episode-name", "-e", default=None,
        help="エピソード名 (デフォルト: ファイル名から自動生成)",
    )
    parser.add_argument(
        "--en-voice", default="alloy",
        help="英語音声の声 (デフォルト: alloy)",
    )
    parser.add_argument(
        "--ja-voice", default="alloy",
        help="日本語音声の声 (デフォルト: alloy)",
    )
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_file():
        print(f"Error: '{source}' が見つかりません", file=sys.stderr)
        sys.exit(1)

    config = load_config()

    # テキスト読み込み
    text = source.read_text(encoding="utf-8").strip()
    if not text:
        print("Error: テキストが空です", file=sys.stderr)
        sys.exit(1)

    print(f"テキスト読み込み: {len(text)} 文字")

    # 言語判定
    if args.lang:
        lang = args.lang
    elif has_mixed_languages(text):
        lang = "mixed"
    else:
        lang = detect_language(text)
    print(f"言語: {lang}")

    # 出力ディレクトリ
    episode_name = args.episode_name or source.stem
    output_dir = PROJECT_ROOT / "episodes" / episode_name
    if output_dir.exists() and any(output_dir.iterdir()):
        base = output_dir
        suffix = 2
        while output_dir.exists() and any(output_dir.iterdir()):
            output_dir = base.parent / f"{base.name}_{suffix}"
            suffix += 1
        print(f"既存ディレクトリと衝突するため {output_dir.name} に出力します")
    output_dir.mkdir(parents=True, exist_ok=True)
    episode_rel = output_dir.relative_to(PROJECT_ROOT)

    # パート分割と音声生成
    if lang == "mixed":
        # 日英混在: lesson_to_speech と同じ方式でセグメント切替
        parts = split_text_into_parts(text, "en")  # 英語ベースで分割
    else:
        parts = split_text_into_parts(text, lang)

    print(f"パート数: {len(parts)}")

    audio_entries = []
    for i, part in enumerate(parts, 1):
        part_name = f"part_{i}" if len(parts) > 1 else "audio"
        audio_path = str(output_dir / f"{part_name}.mp3")

        print(f"  パート {i}/{len(parts)}: 音声生成中...")

        if lang == "mixed":
            lesson_to_speech(
                part, audio_path, config,
                en_voice=args.en_voice, ja_voice=args.ja_voice,
            )
        else:
            voice = args.en_voice if lang == "en" else args.ja_voice
            text_to_speech(part, audio_path, config, voice=voice, lang=lang)

        print(f"  保存: {audio_path}")

        file_size = str(get_mp3_size(audio_path))
        part_label = f" パート {i}" if len(parts) > 1 else ""
        audio_entries.append({
            "path": f"{episode_rel}/{part_name}.mp3",
            "title": f"[{episode_name}]{part_label}",
            "description": f"{source.name} の読み上げ音声",
            "length": file_size,
        })

    # フィード更新
    update_feed(audio_entries, config)

    print(f"\n完了! 出力ディレクトリ: {output_dir}")
    print(f"RSSフィードURL: {config.get('github_pages_base_url', '').rstrip('/')}/feed.xml")


if __name__ == "__main__":
    main()
