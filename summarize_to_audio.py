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
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

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
        prompt = f"""Summarize the following document in English. The summary will be converted to speech audio.
Requirements:
- The summary must be under {MAX_EN_WORDS} words (to fit within 5 minutes of audio)
- Write in a natural, spoken style suitable for listening
- Cover the key points, methodology, results, and conclusions
- Start directly with the content (no preamble like "Here is a summary...")

Document:
{text[:80000]}"""
    else:
        prompt = f"""以下の文書を日本語で要約してください。要約は音声に変換されます。
要件:
- 要約は{MAX_JA_CHARS}文字以内にしてください（音声5分以内に収めるため）
- 聞き取りやすい自然な話し言葉のスタイルで書いてください
- 主要なポイント、手法、結果、結論をカバーしてください
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

    prompt = f"""あなたは日本の高校3年生向けの英語教師です。
以下の英語パラグラフについて、音声読み上げ用のレッスン台本を作成してください。

## フォーマット (厳守)
パラグラフ内の各文について、以下の構成を繰り返してください:

1. まず英文を1文そのまま記載
2. 次に「---」(区切り線)
3. その後に日本語で解説:
   - まず文全体の日本語訳
   - 重要な英単語・熟語の意味と発音のコツ（高校生が知らなそうな語を多めに）
   - 文法ポイント（あれば簡潔に）
4. 空行を入れて次の文へ

## 注意
- 解説は話し言葉で、聞いて分かりやすいスタイルにしてください
- 単語解説では「○○という単語は、△△という意味です」のように丁寧に説明してください
- 専門用語は特に詳しく解説してください
- 「第{paragraph_index}パラグラフを見ていきましょう。」という導入から始めてください

## 英語パラグラフ
{paragraph}"""

    response = client.responses.create(
        model="gpt-5.4",
        input=prompt,
    )
    return response.output_text


def text_to_speech(text: str, output_path: str, config: dict, voice: str = "alloy") -> None:
    """Microsoft Foundry の gpt-audio-1.5 で音声ファイルを生成する。"""
    client = AzureOpenAI(
        api_key=config["azure_openai_api_key"],
        azure_endpoint=config["azure_openai_endpoint"],
        api_version="2025-01-01-preview",
    )

    # 出力フォーマットを拡張子から決定
    ext = Path(output_path).suffix.lstrip(".")
    audio_format = ext if ext in ("wav", "mp3", "flac", "opus", "aac") else "mp3"

    completion = client.chat.completions.create(
        model="gpt-audio-1.5",
        modalities=["text", "audio"],
        audio={"voice": voice, "format": audio_format},
        messages=[
            {
                "role": "user",
                "content": f"Please read the following text aloud exactly as written, without adding any commentary:\n\n{text}",
            }
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

    Apple Podcast の要件を満たすRSS 2.0 + iTunes namespace のフィードを生成する。
    audio_files: [{"path": "episodes/xxx/summary_en.mp3", "title": "...", "description": "..."}, ...]
    """
    base_url = config.get("github_pages_base_url", "").rstrip("/")
    feed_path = Path(__file__).parent / "feed.xml"
    itunes_ns = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    ET.register_namespace("itunes", itunes_ns)

    # 既存フィードがあれば読み込み、なければ新規作成
    if feed_path.exists():
        tree = ET.parse(feed_path)
        rss = tree.getroot()
        channel = rss.find("channel")
    else:
        rss = ET.Element("rss", attrib={"version": "2.0"})
        # xmlns:itunes は register_namespace で自動付与される
        channel = ET.SubElement(rss, "channel")
        ET.SubElement(channel, "title").text = config.get("podcast_title", "Audio Paper Summary")
        ET.SubElement(channel, "link").text = base_url
        ET.SubElement(channel, "description").text = config.get("podcast_description", "")
        ET.SubElement(channel, "language").text = config.get("podcast_language", "en")
        # Apple Podcast 必須タグ
        ET.SubElement(channel, f"{{{itunes_ns}}}type").text = "episodic"
        ET.SubElement(channel, f"{{{itunes_ns}}}author").text = config.get("podcast_author", "")
        ET.SubElement(channel, f"{{{itunes_ns}}}explicit").text = "false"
        ET.SubElement(channel, f"{{{itunes_ns}}}category", attrib={"text": "Education"})
        ET.SubElement(channel, f"{{{itunes_ns}}}image", attrib={
            "href": f"{base_url}/cover.jpg",
        })
        owner = ET.SubElement(channel, f"{{{itunes_ns}}}owner")
        ET.SubElement(owner, f"{{{itunes_ns}}}name").text = config.get("podcast_author", "")
        ET.SubElement(owner, f"{{{itunes_ns}}}email").text = config.get("podcast_email", "noreply@example.com")
        tree = ET.ElementTree(rss)

    now = datetime.now(timezone.utc)
    pub_date = now.strftime("%a, %d %b %Y %H:%M:%S +0000")

    for i, af in enumerate(audio_files):
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = af["title"]
        ET.SubElement(item, "description").text = af.get("description", "")

        file_url = f"{base_url}/{af['path']}"
        file_size = str(get_mp3_size(str(output_dir / Path(af["path"]).name)))

        ET.SubElement(item, "enclosure", attrib={
            "url": file_url,
            "length": file_size,
            "type": "audio/mpeg",
        })
        ET.SubElement(item, "guid", attrib={"isPermaLink": "true"}).text = file_url
        ET.SubElement(item, "pubDate").text = pub_date
        ET.SubElement(item, f"{{{itunes_ns}}}episode").text = str(i + 1)

    ET.indent(tree, space="  ")
    tree.write(str(feed_path), encoding="unicode", xml_declaration=True)
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
    text_to_speech(summary_en, en_audio_path, config, voice=args.en_voice)
    print(f"英語音声保存: {en_audio_path}")

    print(f"日本語音声を生成中 (voice: {args.ja_voice})...")
    text_to_speech(summary_ja, ja_audio_path, config, voice=args.ja_voice)
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
        text_to_speech(script, lesson_audio_path, config, voice=args.ja_voice)
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
