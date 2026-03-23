# Audio Paper

論文（PDF/URL）から英語・日本語の要約音声と英語解説音声を生成し、Podcast RSS フィードとして配信するツール。

## セットアップ

### 依存パッケージのインストール

```bash
pip install PyMuPDF requests pyyaml beautifulsoup4 openai
```

### 設定ファイル

`config.yaml` に以下を記述する。

```yaml
azure_openai_endpoint: "https://your-resource.cognitiveservices.azure.com"
azure_openai_api_key: "your-api-key"

# Podcast (GitHub Pages) 設定
github_pages_base_url: "https://your-user.github.io/audio_paper"
podcast_title: "Audio Paper Summary"
podcast_description: "論文の要約を音声で配信するPodcast"
podcast_author: "your-name"
podcast_language: "en"
```

## 使い方

### 基本

```bash
# URLから
python summarize_to_audio.py https://example.com/paper.pdf

# ローカルPDFから
python summarize_to_audio.py path/to/paper.pdf
```

### オプション

```bash
# エピソード名を指定（episodes/<name>/ に出力）
python summarize_to_audio.py paper.pdf -e my_episode

# 出力ディレクトリを直接指定
python summarize_to_audio.py paper.pdf -o ./output

# 音声の声を変更
python summarize_to_audio.py paper.pdf --en-voice sage --ja-voice shimmer
```

利用可能な声: `alloy` / `ash` / `ballad` / `coral` / `echo` / `sage` / `shimmer` / `verse` / `marin` / `cedar`

## 出力

`episodes/<エピソード名>/` 以下に生成される。

| ファイル | 内容 |
|---|---|
| `summary_en.mp3` / `.txt` | 英語要約の音声とテキスト |
| `summary_ja.mp3` / `.txt` | 日本語要約の音声とテキスト |
| `lesson_para_N.mp3` / `.txt` | パラグラフごとの英文読み上げ＋日本語解説 |

また、プロジェクトルートの `feed.xml`（Podcast RSS フィード）と `episodes.json`（エピソード管理）が更新される。

## 処理の流れ

1. URL または PDF からテキストを抽出
2. GPT-5.4 で英語・日本語の要約を生成（情報科学系の大学院生向け）
3. gpt-audio-1.5 で要約を音声に変換
4. 英語要約をパラグラフ単位で分割し、各パラグラフの英文読み上げ＋日本語解説音声を生成
5. RSS フィード (`feed.xml`) を更新
