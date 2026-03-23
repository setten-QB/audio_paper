#!/usr/bin/env python3
"""
URLまたはPDFファイルから文書を読み込み、英語・日本語それぞれ5分以内の要約音声ファイルを生成する。

必要な設定 (config.yaml):
  azure_openai_endpoint  - Azure OpenAI エンドポイント
  azure_openai_api_key   - Azure OpenAI APIキー

使い方:
  python summarize_to_audio.py <URL or PDF path> [--output-dir OUTPUT_DIR]
"""

import argparse
import base64
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import fitz  # PyMuPDF
import requests
import yaml
from bs4 import BeautifulSoup
from openai import AzureOpenAI, OpenAI

# TTS の速度は約150語/分(英語)、約350文字/分(日本語)
# 5分 = 750語(英語)、1750文字(日本語) を目安にする
MAX_EN_WORDS = 700
MAX_JA_CHARS = 1700

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    """config.yaml から設定を読み込む。"""
    if not CONFIG_PATH.exists():
        print(f"Error: {CONFIG_PATH} が見つかりません", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    for key in ("azure_openai_endpoint", "azure_openai_api_key"):
        if not config.get(key):
            print(f"Error: config.yaml に '{key}' を設定してください", file=sys.stderr)
            sys.exit(1)
    return config


def extract_text_from_pdf(pdf_path: str) -> str:
    """PDFファイルからテキストを抽出する。"""
    doc = fitz.open(pdf_path)
    texts = []
    for page in doc:
        texts.append(page.get_text())
    doc.close()
    return "\n".join(texts)


def extract_text_from_url(url: str) -> str:
    """URLからテキストを抽出する。PDFリンクの場合はPDFとして処理する。"""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AudioPaperBot/1.0)"}
    resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")
    if "application/pdf" in content_type or url.lower().endswith(".pdf"):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(resp.content)
            tmp_path = f.name
        try:
            return extract_text_from_pdf(tmp_path)
        finally:
            os.unlink(tmp_path)

    soup = BeautifulSoup(resp.text, "html.parser")
    # 不要な要素を除去
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def generate_summary(text: str, lang: str, config: dict) -> str:
    """Microsoft Foundry の GPT-5.4 (Responses API) で要約を生成する。"""
    client = OpenAI(
        api_key=config["azure_openai_api_key"],
        base_url=config["azure_openai_endpoint"].rstrip("/") + "/openai/v1/",
    )

    if lang == "en":
        prompt = f"""Summarize the following document in English for an audience of computer science graduate students. The summary will be converted to speech audio.
Requirements:
- The summary must be under {MAX_EN_WORDS} words (to fit within 5 minutes of audio)
- Write in a natural, spoken style suitable for listening
- Assume the listener has strong CS fundamentals — skip basic explanations and focus on what is novel or significant
- Cover the key contributions, technical approach, experimental results, and implications
- Start directly with the content (no preamble like "Here is a summary...")

Document:
{text[:80000]}"""
    else:
        prompt = f"""以下の文書を、情報科学系の大学院生向けに日本語で要約してください。要約は音声に変換されます。
要件:
- 要約は{MAX_JA_CHARS}文字以内にしてください（音声5分以内に収めるため）
- 聞き取りやすい自然な話し言葉のスタイルで書いてください
- 聞き手はCS全般の基礎知識があることを前提とし、基本的な概念の説明は省略してください
- 新規性のある貢献、技術的アプローチ、実験結果、意義に焦点を当ててください
- 内容から直接始めてください（「以下は要約です」のような前置きは不要です）

文書:
{text[:80000]}"""

    response = client.responses.create(
        model="gpt-5.4",
        input=prompt,
    )
    return response.output_text


def generate_lesson_script(paragraph: str, paragraph_index: int, config: dict) -> str:
    """英語パラグラフから「英文読み上げ→日本語解説」形式のレッスン台本を生成する。"""
    client = OpenAI(
        api_key=config["azure_openai_api_key"],
        base_url=config["azure_openai_endpoint"].rstrip("/") + "/openai/v1/",
    )

    prompt = f"""あなたは情報科学系の大学院生向けの英語教師です。
以下の英語パラグラフについて、音声読み上げ用のレッスン台本を作成してください。

## フォーマット (厳守)
パラグラフ内の各文について、以下の構成を繰り返してください:

1. まず英文を1文そのまま記載
2. 次に「---」(区切り線)
3. その後に日本語で簡潔に解説:
   - 文全体の日本語訳
   - 学術英語として重要な表現や、CS分野特有の用語があれば簡潔に補足
4. 空行を入れて次の文へ

## 注意
- 聞き手はCS全般の基礎知識があるため、基本的な単語や文法の説明は不要です
- 専門用語の解説も、大学院生が知っていそうなものは省略してください
- 解説は簡潔に、論文読解に役立つポイントに絞ってください
- 「第{paragraph_index}パラグラフです。」という導入から始めてください

## 英語パラグラフ
{paragraph}"""

    response = client.responses.create(
        model="gpt-5.4",
        input=prompt,
    )
    return response.output_text


def text_to_speech(text: str, output_path: str, config: dict, voice: str = "alloy", lang: str = "en") -> None:
    """Microsoft Foundry の gpt-audio-1.5 で音声ファイルを生成する。"""
    client = AzureOpenAI(
        api_key=config["azure_openai_api_key"],
        azure_endpoint=config["azure_openai_endpoint"],
        api_version="2025-01-01-preview",
    )

    # 出力フォーマットを拡張子から決定
    ext = Path(output_path).suffix.lstrip(".")
    audio_format = ext if ext in ("wav", "mp3", "flac", "opus", "aac") else "mp3"

    if lang == "ja":
        system_prompt = "あなたは日本語のネイティブスピーカーです。自然な日本語の発音とイントネーションで読み上げてください。"
        user_prompt = f"以下のテキストを、そのまま正確に読み上げてください。余計なコメントは加えないでください:\n\n{text}"
    else:
        system_prompt = "You are a native English speaker."
        user_prompt = f"Please read the following text aloud exactly as written, without adding any commentary:\n\n{text}"

    completion = client.chat.completions.create(
        model="gpt-audio-1.5",
        modalities=["text", "audio"],
        audio={"voice": voice, "format": audio_format},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    audio_data = base64.b64decode(completion.choices[0].message.audio.data)
    with open(output_path, "wb") as f:
        f.write(audio_data)


def get_mp3_size(file_path: str) -> int:
    """ファイルサイズをバイト単位で返す。"""
    return os.path.getsize(file_path)


def update_feed(output_dir: Path, episode_title: str, audio_files: list[dict], config: dict) -> None:
    """Podcast RSSフィード (feed.xml) を生成・更新する。

    既存エピソード情報は episodes.json で管理し、feed.xml は毎回テンプレートから再生成する。
    audio_files: [{"path": "episodes/xxx/summary_en.mp3", "title": "...", "description": "..."}, ...]
    """
    project_root = Path(__file__).parent
    base_url = config.get("github_pages_base_url", "").rstrip("/")
    feed_path = project_root / "feed.xml"
    episodes_json_path = project_root / "episodes.json"

    # エピソード情報を永続化 (episodes.json)
    if episodes_json_path.exists():
        with open(episodes_json_path, encoding="utf-8") as f:
            all_episodes = json.load(f)
    else:
        all_episodes = []

    now = datetime.now(timezone.utc)
    pub_date = now.strftime("%a, %d %b %Y %H:%M:%S +0000")

    for af in audio_files:
        file_size = str(get_mp3_size(str(output_dir / Path(af["path"]).name)))
        all_episodes.append({
            "title": af["title"],
            "description": af.get("description", ""),
            "path": af["path"],
            "length": file_size,
            "pub_date": pub_date,
        })

    with open(episodes_json_path, "w", encoding="utf-8") as f:
        json.dump(all_episodes, f, ensure_ascii=False, indent=2)

    # feed.xml をテンプレートから生成
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
        description="文書を読み込み、英語・日本語の要約音声を生成する"
    )
    parser.add_argument("source", help="URLまたはPDFファイルのパス")
    parser.add_argument(
        "--episode-name", "-e", default=None,
        help="エピソード名 (デフォルト: 日時から自動生成)。episodes/<name>/ に出力される",
    )
    parser.add_argument(
        "--output-dir", "-o", default=None,
        help="出力ディレクトリを直接指定 (--episode-name より優先)",
    )
    parser.add_argument(
        "--en-voice",
        default="alloy",
        help="英語音声の声 (デフォルト: alloy, 選択肢: alloy/ash/ballad/coral/echo/sage/shimmer/verse/marin/cedar)",
    )
    parser.add_argument(
        "--ja-voice",
        default="alloy",
        help="日本語音声の声 (デフォルト: alloy, 選択肢: alloy/ash/ballad/coral/echo/sage/shimmer/verse/marin/cedar)",
    )
    args = parser.parse_args()

    config = load_config()

    # 出力ディレクトリの決定
    project_root = Path(__file__).parent
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        episode_name = args.episode_name or datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = project_root / "episodes" / episode_name
    output_dir.mkdir(parents=True, exist_ok=True)
    # episodes/ からの相対パス (RSSフィード用)
    try:
        episode_rel = output_dir.relative_to(project_root)
    except ValueError:
        episode_rel = Path("episodes") / output_dir.name

    # 1. テキスト抽出
    source = args.source
    if source.startswith("http://") or source.startswith("https://"):
        print(f"URLからテキストを抽出中: {source}")
        text = extract_text_from_url(source)
    elif os.path.isfile(source):
        print(f"PDFからテキストを抽出中: {source}")
        text = extract_text_from_pdf(source)
    else:
        print(f"Error: '{source}' はURLでもファイルでもありません", file=sys.stderr)
        sys.exit(1)

    if not text.strip():
        print("Error: テキストを抽出できませんでした", file=sys.stderr)
        sys.exit(1)

    print(f"抽出したテキスト: {len(text)} 文字")

    # 2. 要約生成
    print("英語要約を生成中...")
    summary_en = generate_summary(text, "en", config)
    print(f"英語要約: {len(summary_en.split())} words")

    print("日本語要約を生成中...")
    summary_ja = generate_summary(text, "ja", config)
    print(f"日本語要約: {len(summary_ja)} 文字")

    # 要約テキストも保存
    en_txt_path = output_dir / "summary_en.txt"
    ja_txt_path = output_dir / "summary_ja.txt"
    en_txt_path.write_text(summary_en, encoding="utf-8")
    ja_txt_path.write_text(summary_ja, encoding="utf-8")
    print(f"要約テキスト保存: {en_txt_path}, {ja_txt_path}")

    # 3. 音声生成
    en_audio_path = str(output_dir / "summary_en.mp3")
    ja_audio_path = str(output_dir / "summary_ja.mp3")

    print(f"英語音声を生成中 (voice: {args.en_voice})...")
    text_to_speech(summary_en, en_audio_path, config, voice=args.en_voice, lang="en")
    print(f"英語音声保存: {en_audio_path}")

    print(f"日本語音声を生成中 (voice: {args.ja_voice})...")
    text_to_speech(summary_ja, ja_audio_path, config, voice=args.ja_voice, lang="ja")
    print(f"日本語音声保存: {ja_audio_path}")

    # 4. 英語解説音声の生成（パラグラフごと）
    paragraphs = [p.strip() for p in summary_en.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [p.strip() for p in summary_en.split("\n") if p.strip()]

    print(f"\n英語解説音声を生成中 ({len(paragraphs)} パラグラフ)...")
    lesson_scripts = []

    for i, para in enumerate(paragraphs, 1):
        print(f"  パラグラフ {i}/{len(paragraphs)}: レッスン台本生成中...")
        script = generate_lesson_script(para, i, config)
        lesson_scripts.append(script)

        print(f"  パラグラフ {i}/{len(paragraphs)}: 音声生成中...")
        lesson_audio_path = str(output_dir / f"lesson_para_{i}.mp3")
        text_to_speech(script, lesson_audio_path, config, voice=args.ja_voice, lang="ja")
        print(f"  保存: {lesson_audio_path}")

        # パラグラフごとの台本テキストも保存
        lesson_txt_path = output_dir / f"lesson_para_{i}.txt"
        lesson_txt_path.write_text(script, encoding="utf-8")

    print(f"英語解説音声保存: lesson_para_1.mp3 ~ lesson_para_{len(paragraphs)}.mp3")
    print(f"レッスン台本保存: lesson_para_1.txt ~ lesson_para_{len(paragraphs)}.txt")

    # 5. RSSフィード更新
    episode_title = output_dir.name
    audio_entries = [
        {
            "path": f"{episode_rel}/summary_en.mp3",
            "title": f"[{episode_title}] English Summary",
            "description": "英語要約の音声",
        },
        {
            "path": f"{episode_rel}/summary_ja.mp3",
            "title": f"[{episode_title}] 日本語要約",
            "description": "日本語要約の音声",
        },
    ]
    for i in range(1, len(paragraphs) + 1):
        audio_entries.append({
            "path": f"{episode_rel}/lesson_para_{i}.mp3",
            "title": f"[{episode_title}] 英語解説 パラグラフ {i}",
            "description": f"パラグラフ{i}の英文読み上げと日本語解説",
        })

    update_feed(output_dir, episode_title, audio_entries, config)

    print(f"\n完了! 出力ディレクトリ: {output_dir}")
    print(f"RSSフィードURL: {config.get('github_pages_base_url', '').rstrip('/')}/feed.xml")


if __name__ == "__main__":
    main()
