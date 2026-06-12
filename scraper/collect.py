"""
MMA WAVE — 自動収集スクリプト
実行: python scraper/collect.py
GitHub Actions で毎朝 6:00 JST に自動実行される想定

収集対象:
  ニュース : RSS フィード（Sherdog / BJPenn / MMA News / ONE公式）
            ・タイトルホルダー/有名選手/日本人選手関連を優先スコアリング
            ・1日最大5記事に絞り込み
            ・タイトル・本文を日本語に自動翻訳（Google Translate）
  イベント : Wikipedia + UFC公式
  チャンピオン: Wikipedia
"""

import io
import json
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import os

import feedparser
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

# .env 読み込み（GitHub Actions では環境変数から取得）
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

try:
    from groq import Groq as _Groq
    _groq_key = os.environ.get("GROQ_API_KEY", "")
    if _groq_key:
        GROQ_CLIENT = _Groq(api_key=_groq_key)
        GROQ_MODEL  = "llama-3.3-70b-versatile"
        print("  Groq: 有効")
    else:
        GROQ_CLIENT = None
except ImportError:
    GROQ_CLIENT = None

# ============================================================
# 設定
# ============================================================
DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

NEWS_FILE   = DATA_DIR / "news.json"
EVENTS_FILE = DATA_DIR / "events.json"
CHAMPS_FILE = DATA_DIR / "champions.json"

JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)
ONE_YEAR_AGO = NOW - timedelta(days=365)

MAX_PER_DAY = 5  # 1日の最大記事数

# RSS フィード（海外団体ニュースは MMA Junkie / MMA Fighting の2サイト中心。
# MMA Junkie はRSS廃止のためHTMLスクレイピング → scrape_mmajunkie_entries()）
RSS_FEEDS = [
    {"url": "https://www.mmafighting.com/rss/index.xml", "cat": "ufc", "source": "MMA Fighting", "lang": "en"},
]

# 静的チャンピオンデータ（自動取得できない団体）
RIZIN_CHAMPS_STATIC = [
    {"weight": "ヘビー級",            "name": "バダ・ハリ",                 "since": "2024年12月31日", "defenses": 0},
    {"weight": "ライトヘビー級",       "name": "ヴァレンティン・モルダフスキー", "since": "2023年6月24日",  "defenses": 1},
    {"weight": "ミドル級",            "name": "ジョニー・ケース",             "since": "2024年3月30日",  "defenses": 1},
    {"weight": "ライト級",            "name": "ホベルト・サトシ・ソウザ",     "since": "2025年3月29日",  "defenses": 0},
    {"weight": "フェザー級",           "name": "クレベル・コイケ",            "since": "2024年6月9日",   "defenses": 2},
    {"weight": "バンタム級",           "name": "朝倉 海",                    "since": "2025年9月21日",  "defenses": 1},
    {"weight": "フライ級",            "name": "神龍 誠",                    "since": "2024年6月9日",   "defenses": 1},
    {"weight": "女子スーパーアトム級", "name": "浜崎 朱加",                  "since": "2023年9月24日",  "defenses": 2},
]

ONE_CHAMPS_STATIC = [
    {"weight": "ヘビー級 MMA",     "name": "アナトリー・マリキン",        "since": "2024年3月22日",  "defenses": 1},
    {"weight": "ライトヘビー級 MMA","name": "ライナー・デリダス",          "since": "2023年9月29日",  "defenses": 2},
    {"weight": "ミドル級 MMA",     "name": "ファブリシオ・アンドラジ",    "since": "2024年5月3日",   "defenses": 1},
    {"weight": "ウェルター級 MMA", "name": "ゼバスチャン・カデスタム",    "since": "2023年12月1日",  "defenses": 2},
    {"weight": "ライト級 MMA",     "name": "クリスチャン・リー",          "since": "2022年8月26日",  "defenses": 3},
    {"weight": "フェザー級 MMA",   "name": "タン・カイ",                  "since": "2023年10月6日",  "defenses": 2},
    {"weight": "バンタム級 MMA",   "name": "ジャン・リーポン",            "since": "2024年1月26日",  "defenses": 2},
    {"weight": "フライ級 MMA",     "name": "デメトリアス・ジョンソン",    "since": "2018年3月11日",  "defenses": 7},
    {"weight": "ストロー級 MMA",   "name": "ジョシュア・パシオ",          "since": "2023年10月6日",  "defenses": 2},
    {"weight": "ライト級 キック",  "name": "レグ・クレベ",                "since": "2023年4月22日",  "defenses": 3},
    {"weight": "バンタム級 ムエタイ","name": "ロッタン・シットムアンノン", "since": "2020年2月28日",  "defenses": 8},
    {"weight": "女子アトム級 MMA", "name": "スタンプ・フェアテックス",    "since": "2024年9月6日",   "defenses": 1},
]

# ============================================================
# 重要度スコアリング
# ============================================================

# タイトルホルダー・上位ランカー（5点）
TIER1 = {
    "jon jones", "islam makhachev", "alex pereira", "dricus du plessis",
    "belal muhammad", "ilia topuria", "sean o'malley", "alexandre pantoja",
    "alexa grasso", "zhang weili", "raquel pennington",
    "conor mcgregor", "khabib", "francis ngannou", "stipe miocic",
    "max holloway", "charles oliveira", "justin gaethje", "dustin poirier",
    "leon edwards", "colby covington", "israel adesanya", "robert whittaker",
    "jan blachowicz", "jiří procházka", "ciryl gane", "tom aspinall",
    "christian lee", "rodtang", "stamp fairtex",
    # 日本人選手
    "horiguchi", "asakura", "tenshin", "nasukawa", "takeru",
    "kape", "aoki", "shinya aoki", "kai asakura",
}

# 上位ランカー・注目選手（3点）
TIER2 = {
    "paddy pimblett", "gilbert burns", "geoff neal", "sean brady",
    "merab dvalishvili", "henry cejudo", "jose aldo", "tj dillashaw",
    "derek brunson", "paulo costa", "sean strickland", "bo nickal",
    "ankalaev", "jamahal hill", "khalil rountree", "ryan spann",
    "fiziev", "beneil dariush", "michael chandler", "tony ferguson",
    "nate diaz", "jorge masvidal",
    # ONEスター
    "demetrious johnson", "eddie alvarez", "angela lee",
}

# 重要キーワード（加算点）
KEYWORD_SCORES = {
    "champion":        4,
    "championship":    4,
    "title fight":     4,
    "title shot":      3,
    "belt":            3,
    "interim":         3,
    "#1 contender":    4,
    "number one contender": 4,
    "pound-for-pound": 3,
    "p4p":             3,
    "main event":      2,
    "co-main":         1,
    "retirement":      3,
    "retires":         3,
    "stripped":        3,
    "vacated":         3,
    "injured":         2,
    "surgery":         2,
    "suspended":       2,
    "usada":           2,
    "drug test":       2,
    "ufc 3":           2,   # UFC 3xx 番台 = PPV
    "ufc freedom":     2,
    "contract":        1,
    "signing":         1,
    "japan":           2,
    "japanese":        2,
    "tokyo":           2,
    "osaka":           2,
    "rizin":           2,
    "one championship":1,
}


def detect_cat(feed_def: dict, title: str, summary: str) -> str:
    """日本語フィードはタイトルキーワードでカテゴリを判定"""
    if feed_def["lang"] != "ja":
        return feed_def["cat"]
    text = (title + " " + summary).upper()
    if "RIZIN" in text:
        return "rizin"
    if "ONE CHAMPIONSHIP" in text or "ONEチャンピオンシップ" in text:
        return "one"
    return feed_def["cat"]


def importance_score(title: str, excerpt: str = "") -> int:
    text = (title + " " + excerpt).lower()
    score = 0
    for name in TIER1:
        if name in text:
            score += 5
    for name in TIER2:
        if name in text:
            score += 3
    for kw, pts in KEYWORD_SCORES.items():
        if kw in text:
            score += pts
    return score


# ============================================================
# 翻訳（Google Translate 無料枠）
# ============================================================
_translator = GoogleTranslator(source="auto", target="ja")

# タイトル末尾から除去するパターン（体言止めに近づける）
_TRAILING_STRIP = re.compile(
    r'[、。]?(?:'
    r'と(?:語る|述べる|明かす|語った|述べた|明かした|コメント|主張する|主張した|説明する|示した|話す|話した)'
    r'|について(?:語る|述べる|語った|説明)'
    r'|を(?:語る|明かす|詳しく語る|述べる)'
    r'|と(?:いう|いった)(?:こと)?$'
    r')$'
)

# 「は〜」「が〜」の主語句だけ残す区切りパターン
_CLAUSE_SEP = re.compile(r'(?<=[^、])[、](?=[^、])')


def shorten_title(title: str, max_len: int = 38) -> str:
    """翻訳済みタイトルを体言止め風に短縮"""
    t = title.strip()

    # 末尾の常套句を繰り返し除去
    for _ in range(3):
        new = _TRAILING_STRIP.sub('', t).strip()
        if new == t:
            break
        t = new

    if len(t) <= max_len:
        return t

    # 「、」の前後が両方ある程度の長さなら後半を切る
    # ただし「元王者、〇〇が…」の形（前半が肩書のみ）はスキップ
    if '、' in t:
        parts = t.split('、', 1)
        first, second = parts[0], parts[1]
        # 前半 >= 12文字（人名 + 肩書レベル）かつ前半だけで意味が成立するなら
        if 12 <= len(first) <= max_len:
            t = first
        # 前半が短い肩書（役職・元王者など）の場合は「前半、後半」の後半を短縮
        elif len(first) < 12 and len(first + '、' + second) > max_len:
            combined = first + '、' + second[:max_len - len(first) - 1]
            t = combined

    if len(t) <= max_len:
        return t.strip()

    # 自然な区切り助詞で切る
    for sep in ['において', 'について', 'に関して', 'での', 'への', 'との', 'に関', 'を狙', 'で対']:
        idx = t.find(sep, max_len // 2)
        if 0 < idx <= max_len:
            t = t[:idx]
            return t.strip()

    # 最後の手段：文字数カット
    return t[:max_len].strip()


def translate(text: str, max_len: int = 400) -> str:
    if not text or not text.strip():
        return text
    # 既に日本語が多ければそのまま
    ja_ratio = sum(1 for c in text if '　' <= c <= '鿿') / max(len(text), 1)
    if ja_ratio > 0.3:
        return text
    try:
        result = _translator.translate(text[:max_len])
        time.sleep(0.3)  # レート制限対策
        return result or text
    except Exception:
        return text


def translate_title(text: str) -> str:
    """タイトル専用：翻訳→体言止め短縮（元から日本語の見出しはそのまま使う）"""
    ja_ratio = sum(1 for c in text if '　' <= c <= '鿿') / max(len(text), 1)
    if ja_ratio > 0.3:
        return text.strip()
    translated = translate(text, max_len=200)
    return shorten_title(translated)


def fetch_article_body(url: str) -> str:
    """記事URLから本文テキストを取得。失敗時は空文字を返す"""
    if not url:
        return ""
    try:
        resp = requests.get(url, headers=_REQ_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # よくある記事本文セレクタを優先順に試す
        for sel in ["article", ".article-body", ".entry-content", ".post-content",
                    ".article__body", ".story-body", "main"]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(" ", strip=True)
                if len(text) > 200:
                    return text[:4000]
        # フォールバック: body全体
        body = soup.find("body")
        return body.get_text(" ", strip=True)[:4000] if body else ""
    except Exception:
        return ""


def summarize_excerpt(title: str, text: str) -> str:
    """本文を日本語で5〜8文の詳細要約に。失敗時はGoogle翻訳にフォールバック"""
    if not text.strip():
        return text

    if GROQ_CLIENT:
        ja_ratio = sum(1 for c in text if '぀' <= c <= '鿿') / max(len(text), 1)
        is_ja = ja_ratio > 0.15

        if is_ja:
            prompt = (
                "以下のMMA格闘技記事を自然な日本語で5〜8文の詳細な要約にしてください。"
                "重要な発言・事実・背景をすべて含め、箇条書きにせず文章で書いてください。\n\n"
                + text[:3000]
            )
        else:
            prompt = (
                "以下の英語MMA格闘技記事を日本語に翻訳し、5〜8文の詳細な要約を書いてください。"
                "重要な発言・事実・背景をすべて含め、箇条書きにせず自然な日本語の文章で書いてください。\n\n"
                f"タイトル: {title}\n{text[:3000]}"
            )
        try:
            resp = GROQ_CLIENT.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1200,
            )
            time.sleep(0.3)
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"    Groq失敗({e}) → Google翻訳にフォールバック")

    # フォールバック：Google翻訳
    return translate(text, max_len=600)


# ============================================================
# ユーティリティ
# ============================================================
def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "-", text).strip("-")[:60]


def load_json(path: Path) -> list:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  saved → {path.name}  ({len(data)} items)")


# ============================================================
# ニュース収集（RSS + HTMLスクレイピング + スコアリング + 翻訳）
# ============================================================
def scrape_mmajunkie_entries() -> list:
    """MMA Junkie はRSS廃止のためニュース一覧ページをスクレイピング"""
    url = "https://mmajunkie.usatoday.com/category/news/"
    try:
        resp = requests.get(url, headers=_REQ_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ✗ MMA Junkie: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    seen = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"/story/.*?/(\d{4})/(\d{2})/(\d{2})/", a["href"])
        if not m:
            continue
        href = a["href"].split("?")[0]
        if href.startswith("/"):
            href = "https://mmajunkie.usatoday.com" + href
        if href in seen:
            continue
        title = a.get_text(" ", strip=True)
        if len(title) < 15:  # ナビゲーション等を除外
            continue
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                      12, 0, tzinfo=JST)
        seen[href] = {
            "title":   title,
            "link":    href,
            "dt":      dt,
            "summary": a.get("data-c-br", ""),
            "source":  "MMA Junkie",
            "cat":     "ufc",
            "bonus":   0,
        }
    print(f"  ✓ MMA Junkie(HTML): {len(seen)} エントリ")
    return list(seen.values())


def scrape_efight_entries() -> list:
    """eFight の WordPress REST API から RIZIN・ONE 記事を取得
    タグID: RIZIN=488, ONE=254"""
    EFIGHT_TAGS = [("rizin", 488, "rizin"), ("one", 254, "one")]
    out, seen = [], set()

    for label, tag_id, cat in EFIGHT_TAGS:
        url = (f"https://efight.jp/wp-json/wp/v2/posts"
               f"?tags={tag_id}&per_page=20"
               f"&_fields=id,title,link,date,excerpt")
        try:
            resp = requests.get(url, headers=_REQ_HEADERS, timeout=15)
            resp.raise_for_status()
            posts = resp.json()
        except Exception as e:
            print(f"  ✗ eFight({label}): {e}")
            continue

        for p in posts:
            link = p.get("link", "")
            if link in seen:
                continue
            seen.add(link)

            title   = BeautifulSoup(p["title"]["rendered"], "html.parser").get_text()
            excerpt = BeautifulSoup(p["excerpt"]["rendered"], "html.parser").get_text(" ", strip=True)[:800]
            dt = datetime.fromisoformat(p["date"]).replace(tzinfo=JST)
            if dt < ONE_YEAR_AGO:
                continue

            out.append({
                "title":   title,
                "link":    link,
                "dt":      dt,
                "summary": excerpt,
                "source":  "eFight",
                "cat":     cat,
                "bonus":   3,
            })
        time.sleep(0.3)

    print(f"  ✓ eFight(API): RIZIN/ONE {len(out)} 件")
    return out


def collect_news() -> None:
    print("\n[NEWS] RSS 収集開始...")
    existing     = load_json(NEWS_FILE)
    existing_ids = {a["id"] for a in existing}
    # 既存の日付ごとの記事数
    existing_by_date: dict[str, int] = defaultdict(int)
    for a in existing:
        existing_by_date[a["date"]] += 1

    candidates = []  # (score, article_dict)

    for feed_def in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_def["url"])
        except Exception as e:
            print(f"  ✗ {feed_def['source']}: {e}")
            continue

        for entry in feed.entries[:30]:
            pub = entry.get("published_parsed") or entry.get("updated_parsed")
            if not pub:
                continue
            dt = datetime(*pub[:6], tzinfo=timezone.utc).astimezone(JST)
            if dt < ONE_YEAR_AGO:
                continue

            title   = entry.get("title", "").strip()
            url     = entry.get("link", "")
            # content > summary > description の順で長い方を使う
            raw = ""
            if hasattr(entry, "content") and entry.content:
                raw = entry.content[0].get("value", "")
            if not raw:
                raw = entry.get("summary", entry.get("description", ""))
            summary = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)[:1500]

            cat      = detect_cat(feed_def, title, summary)
            date_str = dt.strftime("%Y.%m.%d")
            art_id   = f"{cat}-{slugify(title)}-{dt.strftime('%Y%m%d')}"

            if art_id in existing_ids:
                continue

            score = importance_score(title, summary)
            candidates.append((score, dt, {
                "id":          art_id,
                "cat":         cat,
                "date":        date_str,
                "title":       title,
                "excerpt":     summary,
                "source_url":  url,
                "source_name": feed_def["source"],
                "_score":      score,
            }))

        print(f"  ✓ {feed_def['source']}: {len(feed.entries)} エントリ / 候補 {sum(1 for c in candidates if c[2]['source_name']==feed_def['source'])} 件")

    # HTMLスクレイピングソース（MMA Junkie / スポーツナビ）
    for entry in scrape_mmajunkie_entries() + scrape_efight_entries():
        dt = entry["dt"]
        if dt < ONE_YEAR_AGO:
            continue
        title    = entry["title"]
        cat      = entry["cat"]
        date_str = dt.strftime("%Y.%m.%d")
        art_id   = f"{cat}-{slugify(title)}-{dt.strftime('%Y%m%d')}"
        if art_id in existing_ids:
            continue
        score = importance_score(title, entry["summary"]) + entry["bonus"]
        candidates.append((score, dt, {
            "id":          art_id,
            "cat":         cat,
            "date":        date_str,
            "title":       title,
            "excerpt":     entry["summary"][:1500],
            "source_url":  entry["link"],
            "source_name": entry["source"],
            "_score":      score,
        }))

    # スコア降順でソートし、日付ごとに MAX_PER_DAY まで採用
    candidates.sort(key=lambda x: (-x[0], x[1]))  # score降順、同点は古い順
    daily_count: dict[str, int] = defaultdict(int)
    new_articles = []

    for score, dt, article in candidates:
        d = article["date"]
        if daily_count[d] + existing_by_date.get(d, 0) >= MAX_PER_DAY:
            continue
        # 記事URLから全文取得 → Groqで詳細要約
        print(f"  要約中: [{score}点] {article['title'][:50]}")
        full_body = fetch_article_body(article["source_url"])
        body_text = full_body if len(full_body) > len(article["excerpt"]) else article["excerpt"]
        article["title"]   = translate_title(article["title"])
        article["excerpt"] = summarize_excerpt(article["title"], body_text)
        del article["_score"]
        new_articles.append(article)
        existing_ids.add(article["id"])
        daily_count[d] += 1

    # 1年以上前の記事を削除してマージ
    cutoff = ONE_YEAR_AGO.strftime("%Y.%m.%d")
    kept   = [a for a in existing if a["date"] >= cutoff]
    merged = sorted(new_articles + kept, key=lambda a: a["date"], reverse=True)

    save_json(NEWS_FILE, merged)
    print(f"  → 新規採用 {len(new_articles)} 件（候補 {len(candidates)} 件中）、合計 {len(merged)} 件")


# ============================================================
# イベント収集（Wikipedia + UFC公式）
# ============================================================
WIKI_HEADERS = {"User-Agent": "Mozilla/5.0 MMAWave/1.0"}

MONTH_EN = {
    "Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
    "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12,
    "January":1,"February":2,"March":3,"April":4,"June":6,
    "July":7,"August":8,"September":9,"October":10,"November":11,"December":12,
}

COUNTRY_STRIP = [
    ", United States", ", U.S.", ", Japan", ", United Arab Emirates",
    ", Thailand", ", Australia", ", Singapore", ", Brazil",
    ", United Kingdom", ", China", ", Serbia", ", Azerbaijan",
    ", Canada", ", France",
]

# UFC PPV の大体のJST開始時刻（ラスベガス会場=09:00, 海外=01:00〜03:00）
UFC_PPV_TIME = "10:00"
UFC_FN_TIME  = "09:00"

# ============================================================
# 日本向け視聴サービス設定（デフォルト値 ＋ 自動検出）
# ============================================================
JP_WATCH_DEFAULTS = {
    "ufc":   ["unext"],
    "rizin": ["rizin-live", "abema", "unext"],
    "one":   ["one-fc", "abema"],
}

JP_ORG_URLS = {
    "ufc":   "https://www.ufc.com/events",
    "rizin": "https://rizinff.com/",
    "one":   "https://www.onefc.com/events/",
}

_REQ_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def detect_watch_jp() -> dict:
    """
    日本語Wikipediaの放送・配信セクションから視聴サービスを自動検出。
    取得失敗時は JP_WATCH_DEFAULTS にフォールバック。
    """
    WIKI_SOURCES = {
        "ufc":   "https://ja.wikipedia.org/wiki/UFC",
        "rizin": "https://ja.wikipedia.org/wiki/RIZIN",
        "one":   "https://ja.wikipedia.org/wiki/ONE_Championship",
    }
    # キーワード → watch ID（優先度順に記述）
    SERVICE_KEYWORDS = [
        ("u-next",        "unext"),
        ("unext",         "unext"),
        ("rizin live",    "rizin-live"),
        ("live.rizinff",  "rizin-live"),
        ("abema",         "abema"),
        ("wowow",         "wowow"),
        ("ufc fight pass","ufc-fp"),
        ("one fc+",       "one-fc"),
        ("one fc ",       "one-fc"),
        ("amazon prime",  "amazon"),
    ]

    result = {k: list(v) for k, v in JP_WATCH_DEFAULTS.items()}

    for cat, url in WIKI_SOURCES.items():
        try:
            r = requests.get(url, headers=WIKI_HEADERS, timeout=12)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            body = soup.find("div", class_="mw-parser-output")
            full = body.get_text(" ", strip=True) if body else r.text

            # 「日本での放送・配信」セクションを抽出
            # 「終了した放送」以降は除外（過去の放送局を除くため）
            broadcast_markers = ["日本での放送", "放送・配信", "配信サービス"]
            end_markers       = ["終了した放送", "過去の放送", "かつての放送"]

            section = full
            for bm in broadcast_markers:
                idx = full.find(bm)
                if idx != -1:
                    section = full[idx:]
                    break
            for em in end_markers:
                idx = section.find(em)
                if idx != -1:
                    section = section[:idx]
                    break

            text = section.lower()

            seen, detected = set(), []
            for kw, svc_id in SERVICE_KEYWORDS:
                if kw in text and svc_id not in seen:
                    detected.append(svc_id)
                    seen.add(svc_id)
            # ONE FC+は自社プラットフォームなので常に含める
            if cat == "one" and "one-fc" not in seen:
                detected.insert(0, "one-fc")

            if detected:
                result[cat] = detected
                print(f"  {cat.upper()}視聴サービス検出 (Wikipedia): {detected}")
            else:
                print(f"  {cat.upper()}視聴: Wikipediaから検出できず → デフォルト {JP_WATCH_DEFAULTS[cat]}")
        except Exception as e:
            print(f"  {cat.upper()}視聴検出失敗({e}) → デフォルト {JP_WATCH_DEFAULTS[cat]}")

    return result


def city_short(location: str) -> str:
    v = location.strip()
    for c in COUNTRY_STRIP:
        v = v.replace(c, "")
    parts = [p.strip() for p in v.split(",")]
    return parts[0] if parts else v


def parse_wiki_date(s: str):
    """'Jun 14, 2026' or 'June 14, 2026' → date"""
    s = s.strip()
    m = re.match(r"(\w+)\s+(\d+),\s*(\d{4})", s)
    if not m:
        return None
    mon = MONTH_EN.get(m.group(1))
    if not mon:
        return None
    try:
        return datetime(int(m.group(3)), mon, int(m.group(2))).date()
    except ValueError:
        return None


def scrape_wiki_events(wiki_url: str, cat: str, watch: list,
                       time_default: str = "") -> list:
    try:
        resp = requests.get(wiki_url, headers=WIKI_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ✗ {wiki_url}: {e}")
        return []

    soup   = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table", class_="wikitable")
    if not tables:
        return []

    today   = NOW.date()
    horizon = today + timedelta(days=90)
    events  = []

    for table in tables:
        rows = table.find_all("tr")
        headers_row = rows[0] if rows else None
        if not headers_row:
            continue
        col_headers = [th.get_text(" ", strip=True).lower()
                       for th in headers_row.find_all(["th", "td"])]

        # 「date」列のインデックスを特定
        date_col  = next((i for i, h in enumerate(col_headers) if "date" in h), None)
        event_col = next((i for i, h in enumerate(col_headers) if "event" in h), None)
        venue_col = next((i for i, h in enumerate(col_headers) if "venue" in h or "location" in h), None)
        loc_col   = next((i for i, h in enumerate(col_headers)
                          if "location" in h and i != venue_col), venue_col)

        if date_col is None:
            continue

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) <= date_col:
                continue

            date_str = cells[date_col].get_text(" ", strip=True)
            dt = parse_wiki_date(date_str)
            if not dt or dt < today or dt > horizon:
                continue

            name = cells[event_col].get_text(" ", strip=True) if event_col is not None and event_col < len(cells) else ""
            # 括弧や注釈を除去
            name = re.sub(r"\[.*?\]|\(.*?\)", "", name).strip()

            venue = ""
            if venue_col is not None and venue_col < len(cells):
                venue = city_short(cells[venue_col].get_text(" ", strip=True))
            if not venue and loc_col is not None and loc_col < len(cells):
                venue = city_short(cells[loc_col].get_text(" ", strip=True))

            # UFC時刻はufc.comから取得するので空でOK
            events.append({
                "cat":     cat,
                "date":    dt.strftime("%Y-%m-%d"),
                "time":    time_default,
                "name":    name,
                "matchup": "",
                "venue":   venue,
                "watch":   watch,
            })

    return events


def collect_ufc_times(events: list) -> list:
    """UFC公式からmatchup・時刻・イベントURLを補完"""
    try:
        resp = requests.get(
            "https://www.ufc.com/events",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"  ✗ UFC公式補完失敗: {e}")
        return events

    soup = BeautifulSoup(resp.text, "html.parser")
    ufc_data = {}  # date → {time, matchup, url}

    for card in soup.select(".c-card-event--result"):
        txt = card.get_text(" ", strip=True)
        if "試合結果" in txt or "試合映像" in txt:
            continue
        date_m = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", txt)
        if not date_m:
            continue
        date_key = f"{date_m.group(1)}-{date_m.group(2).zfill(2)}-{date_m.group(3).zfill(2)}"
        time_m    = re.search(r"(\d{1,2}:\d{2})\s*JST", txt)
        matchup_m = re.match(r"^(.+?)\s+\d{4}\.", txt)
        # イベントページのURL取得
        link_el = card.select_one("a[href*='/event/']")
        event_url = ("https://www.ufc.com" + link_el["href"]) if link_el else None
        ufc_data[date_key] = {
            "time":    time_m.group(1).zfill(5) if time_m else "",
            "matchup": matchup_m.group(1).strip() if matchup_m else "",
            "url":     event_url,
        }

    for e in events:
        if e["cat"] != "ufc":
            continue
        # Wikipedia日付(米国時間)とJST日付が±1日ずれるため前後日も検索
        edate = datetime.strptime(e["date"], "%Y-%m-%d").date()
        for delta in (0, 1, -1):
            key = (edate + timedelta(days=delta)).strftime("%Y-%m-%d")
            if key in ufc_data:
                d = ufc_data[key]
                e["date"]    = key
                e["time"]    = d["time"] or e["time"]
                e["matchup"] = d["matchup"] or e["matchup"]
                e["url"]     = d["url"]
                break
        if "url" not in e:
            e["url"] = None
    return events


def fetch_rizin_event_urls() -> dict:
    """
    jp.rizinff.com の大会情報タグページからイベント名→URLのマップを取得。
    例: {'rizin landmark 15': 'https://jp.rizinff.com/_ct/17825995', ...}
    """
    url = "https://jp.rizinff.com/_tags/%E5%A4%A7%E4%BC%9A%E6%83%85%E5%A0%B1"
    try:
        resp = requests.get(url, headers=_REQ_HEADERS, timeout=12)
        resp.raise_for_status()
    except Exception as e:
        print(f"  RIZIN大会URL取得失敗: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    result = {}
    date_event_pat = re.compile(r"^\d{4}年\d+月\d+日(.+)$")

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if "/_ct/" not in href:
            continue
        m = date_event_pat.match(text)
        if not m:
            continue
        event_name = m.group(1).strip()
        # 正規化: 大文字化・スポンサー名除去
        key = re.sub(r"^.+?presents\s+", "", event_name, flags=re.IGNORECASE).strip().lower()
        full_url = href if href.startswith("http") else f"https://jp.rizinff.com{href}"
        if key not in result:   # 最初に見つかったURLを使用（大会情報ページ）
            result[key] = full_url

    print(f"  RIZIN大会URL取得: {len(result)} 件")
    return result


def build_event_url(cat: str, name: str, rizin_url_map: dict | None = None) -> str:
    """
    イベント名から公式/WikipediaURLを生成。
    RIZIN: jp.rizinff.com → ONE Fight Night: 英語Wikipedia → それ以外: 公式トップ
    """
    n = name.strip()

    if cat == "rizin":
        if rizin_url_map:
            # ドット・スペースを統一して比較
            def norm(s):
                return re.sub(r"[\.\s]+", " ", s).strip().lower()
            n_norm = norm(re.sub(r"^.+?presents\s+", "", n, flags=re.IGNORECASE))
            for key, url in rizin_url_map.items():
                if norm(key) in n_norm or n_norm in norm(key):
                    return url
        # フォールバック: jp.rizinff.comのトップ
        return "https://jp.rizinff.com/"

    if cat == "one":
        return JP_ORG_URLS["one"]

    return JP_ORG_URLS.get(cat, "")


def collect_events() -> None:
    print("\n[EVENTS] Wikipedia スクレイピング（UFC/RIZIN/ONE）...")

    # 日本向け視聴サービスを自動検出
    print("  [視聴サービス検出中...]")
    watch_jp = detect_watch_jp()

    all_events = []

    # UFC
    ufc = scrape_wiki_events(
        "https://en.wikipedia.org/wiki/List_of_UFC_events",
        cat="ufc", watch=watch_jp["ufc"],
    )
    print(f"  UFC (Wikipedia): {len(ufc)} 件")
    all_events.extend(ufc)

    # RIZIN
    year = NOW.year
    rizin = scrape_wiki_events(
        f"https://en.wikipedia.org/wiki/{year}_in_Rizin_Fighting_Federation",
        cat="rizin", watch=watch_jp["rizin"], time_default="17:00",
    )
    print(f"  RIZIN (Wikipedia): {len(rizin)} 件")
    rizin_url_map = fetch_rizin_event_urls()
    for e in rizin:
        e["url"] = build_event_url("rizin", e["name"], rizin_url_map)
    all_events.extend(rizin)

    # ONE Championship
    one = scrape_wiki_events(
        "https://en.wikipedia.org/wiki/List_of_ONE_Championship_events",
        cat="one", watch=watch_jp["one"], time_default="20:00",
    )
    print(f"  ONE (Wikipedia): {len(one)} 件")
    for e in one:
        e["url"] = build_event_url("one", e["name"])
    all_events.extend(one)

    # UFC に UFC公式から matchup・時刻・URLを補完
    all_events = collect_ufc_times(all_events)

    all_events.sort(key=lambda e: e["date"])
    save_json(EVENTS_FILE, all_events)
    print(f"  → 合計 {len(all_events)} イベント保存")


# ============================================================
# チャンピオン収集（UFC.com/rankings）
# ============================================================
UFC_RANKINGS_URL = "https://www.ufc.com/rankings"
UFC_RANKINGS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.9",
}


def scrape_ufc_rankings() -> dict:
    """UFC.com/rankings から男子・女子チャンピオンとP4Pランキングを取得"""
    try:
        resp = requests.get(UFC_RANKINGS_URL, headers=UFC_RANKINGS_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ✗ UFC.com/rankings: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    men, women, p4p_men, p4p_women = [], [], [], []

    for group in soup.select(".view-grouping"):
        header_el = group.select_one(".view-grouping-header")
        if not header_el:
            continue
        division = header_el.get_text(strip=True)

        # チャンピオン名
        champ_el = (group.select_one("caption h5 a")
                    or group.select_one("caption .views-field-title a")
                    or group.select_one("caption a"))
        champ_name = champ_el.get_text(strip=True) if champ_el else ""
        if not champ_name:
            continue

        is_women = "女子" in division
        is_p4p   = "ポンドフォーポンド" in division or "P4P" in division.upper()

        if is_p4p:
            # P4Pキャプションは1位と同じ選手なので、tbody行を1-10として使う
            ranks = []
            for i, row in enumerate(group.select("tbody tr")[:10], 1):
                name_el = (row.select_one(".views-field-title a")
                           or row.select_one("td a"))
                if name_el:
                    ranks.append({"rank": i, "name": name_el.get_text(strip=True)})
            if is_women:
                p4p_women = ranks
            else:
                p4p_men = ranks
        elif is_women:
            women.append({"weight": division, "name": champ_name})
        else:
            men.append({"weight": division, "name": champ_name})

    total = len(men) + len(women) + len(p4p_men) + len(p4p_women)
    print(f"  UFC.com/rankings: 男子{len(men)}件 女子{len(women)}件 P4P男{len(p4p_men)}件 P4P女{len(p4p_women)}件")
    return {"men": men, "women": women, "p4p_men": p4p_men, "p4p_women": p4p_women} if total else {}


RIZIN_WEIGHT_EN_TO_JA = {
    "light heavyweight":       "ライトヘビー級",
    "heavyweight":             "ヘビー級",
    "middleweight":            "ミドル級",
    "lightweight":             "ライト級",
    "featherweight":           "フェザー級",
    "bantamweight":            "バンタム級",
    "flyweight":               "フライ級",
    "women's super atomweight":"女子スーパーアトム級",
    "women's atomweight":      "女子アトム級",
    "women's strawweight":     "女子ストロー級",
}
MONTH_EN_TO_NUM = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
    "jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,"aug":8,
    "sep":9,"oct":10,"nov":11,"dec":12,
}


def scrape_rizin_champions() -> list:
    """日本語WikipediaのRIZINページから王者テーブルを取得（戴冠日は英語版で補完）"""
    url = "https://ja.wikipedia.org/wiki/RIZIN_FIGHTING_FEDERATION"
    try:
        resp = requests.get(url, headers=WIKI_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ✗ RIZIN 日本語Wikipedia取得失敗: {e}")
        return _scrape_rizin_champions_en()

    soup = BeautifulSoup(resp.text, "html.parser")
    result = []

    for table in soup.select("table.wikitable"):
        rows = table.select("tr")
        if not rows:
            continue
        headers = [th.get_text(" ", strip=True) for th in rows[0].select("th")]
        if "王者" not in headers or "階級" not in headers:
            continue

        col_div   = headers.index("階級")
        col_champ = headers.index("王者")
        col_def   = headers.index("防衛回数") if "防衛回数" in headers else -1

        for row in rows[1:]:
            cells = row.select("td")
            if len(cells) <= max(col_div, col_champ):
                continue
            weight = re.sub(r"\s+", "", cells[col_div].get_text(" ", strip=True))
            name   = cells[col_champ].get_text(" ", strip=True)
            if not name or name in ("空位", "-", "–"):
                continue
            defenses = 0
            if 0 <= col_def < len(cells):
                m = re.search(r"\d+", cells[col_def].get_text(strip=True))
                if m:
                    defenses = int(m.group())
            result.append({"weight": weight, "name": name,
                           "since": "", "defenses": defenses})
        if result:
            break

    if not result:
        print("  ⚠ RIZIN 日本語Wikipediaから王者取得失敗 — 英語版にフォールバック")
        return _scrape_rizin_champions_en()

    # 戴冠日を英語版からマージ（階級でマッチ）
    en_data = _scrape_rizin_champions_en()
    since_by_weight = {c["weight"]: c["since"] for c in en_data if c.get("since")}
    for c in result:
        c["since"] = since_by_weight.get(c["weight"], "")

    print(f"  RIZIN 日本語Wikipedia: {len(result)} 件取得")
    return result


def _scrape_rizin_champions_en() -> list:
    """英語WikipediaのRIZINページからチャンピオンテーブルを取得"""
    url = "https://en.wikipedia.org/wiki/Rizin_Fighting_Federation"
    try:
        resp = requests.get(url, headers=WIKI_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ✗ RIZIN Wikipedia取得失敗: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    result = []

    for table in soup.select("table.wikitable"):
        ths = [th.get_text(" ", strip=True).lower() for th in table.select("th")]
        # 階級・選手名・防衛回数のカラムがあるテーブルを探す
        if not any("division" in h or "weight" in h for h in ths):
            continue
        if not any("champion" in h or "fighter" in h for h in ths):
            continue

        # ヘッダー列のインデックスを特定
        header_row = table.select_one("tr")
        if not header_row:
            continue
        headers = [th.get_text(" ", strip=True).lower() for th in header_row.select("th")]

        col_div, col_champ, col_since, col_def = -1, -1, -1, -1
        for i, h in enumerate(headers):
            if "division" in h or "weight" in h:
                col_div = i
            elif "champion" in h or "fighter" in h:
                col_champ = i
            elif "since" in h or "reign" in h or "date" in h:
                col_since = i
            elif "defense" in h or "def" in h:
                col_def = i

        if col_div < 0 or col_champ < 0:
            continue

        for row in table.select("tr")[1:]:
            cells = row.select("td")
            if not cells or len(cells) <= max(col_div, col_champ):
                continue

            div_raw = cells[col_div].get_text(" ", strip=True).lower() if col_div < len(cells) else ""
            champ_raw = cells[col_champ].get_text(" ", strip=True) if col_champ < len(cells) else ""

            # Vacantはスキップ
            if not champ_raw or "vacant" in champ_raw.lower() or "empty" in champ_raw.lower():
                continue

            # 括弧内の国籍を除去
            champ_name = re.sub(r"\s*\([^)]+\)\s*$", "", champ_raw).strip()

            # 英語階級名を日本語に変換
            weight_ja = ""
            for en_key, ja_val in RIZIN_WEIGHT_EN_TO_JA.items():
                if en_key in div_raw:
                    weight_ja = ja_val
                    break
            if not weight_ja:
                continue  # 不明な階級はスキップ

            # 戴冠日
            since_str = ""
            if col_since >= 0 and col_since < len(cells):
                raw_date = cells[col_since].get_text(" ", strip=True)
                # "May 4, 2025" → "2025年5月4日"
                m = re.search(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", raw_date)
                if m:
                    mon = MONTH_EN_TO_NUM.get(m.group(1).lower(), 0)
                    if mon:
                        since_str = f"{m.group(3)}年{mon}月{m.group(2)}日"

            # 防衛回数
            defenses = 0
            if col_def >= 0 and col_def < len(cells):
                def_text = cells[col_def].get_text(strip=True)
                m2 = re.search(r"\d+", def_text)
                if m2:
                    defenses = int(m2.group())

            result.append({
                "weight":   weight_ja,
                "name":     champ_name,
                "since":    since_str,
                "defenses": defenses,
            })

        if result:
            break  # 最初に見つかったチャンピオンテーブルを使用

    if result:
        print(f"  RIZIN Wikipedia: {len(result)} 件取得")
    else:
        print("  ⚠ RIZIN Wikipediaからチャンピオン取得失敗")
    return result


def _en_date_to_ja(raw: str) -> str:
    """'May 4, 2025' / 'Jun 21, 2025' → '2025年5月4日'"""
    m = re.search(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", raw)
    if m:
        mon = MONTH_EN_TO_NUM.get(m.group(1).lower(), 0)
        if mon:
            return f"{m.group(3)}年{mon}月{int(m.group(2))}日"
    return ""


def _table_grid(table) -> list:
    """wikitableを rowspan/colspan 展開してテキストの2次元グリッドにする"""
    grid = []
    pending = {}  # col -> [text, remaining_rows]
    for tr in table.select("tr"):
        cells = tr.find_all(["th", "td"])
        row, col, ci = [], 0, 0
        while ci < len(cells) or col in pending:
            if col in pending:
                text, rem = pending[col]
                row.append(text)
                if rem > 1:
                    pending[col] = (text, rem - 1)
                else:
                    del pending[col]
                col += 1
                continue
            c = cells[ci]; ci += 1
            text = c.get_text(" ", strip=True)
            try:
                rs = int(c.get("rowspan", 1))
            except (TypeError, ValueError):
                rs = 1
            try:
                cs = int(c.get("colspan", 1))
            except (TypeError, ValueError):
                cs = 1
            for _ in range(cs):
                row.append(text)
                if rs > 1:
                    pending[col] = (text, rs - 1)
                col += 1
        grid.append(row)
    return grid


ONE_DISCIPLINE_JA = {
    "総合格闘技":               "MMA",
    "ムエタイ":                 "ムエタイ",
    "キックボクシング":         "キック",
    "サブミッショングラップリング": "グラップリング",
}
ONE_DISCIPLINE_EN = {
    "mixed martial arts":   "MMA",
    "muay thai":            "ムエタイ",
    "kickboxing":           "キック",
    "submission grappling": "グラップリング",
}
ONE_WEIGHT_EN_TO_JA = {
    "light heavyweight": "ライトヘビー級",
    "heavyweight":       "ヘビー級",
    "middleweight":      "ミドル級",
    "welterweight":      "ウェルター級",
    "lightweight":       "ライト級",
    "featherweight":     "フェザー級",
    "bantamweight":      "バンタム級",
    "flyweight":         "フライ級",
    "strawweight":       "ストロー級",
    "atomweight":        "アトム級",
}


def _scrape_one_since_en() -> dict:
    """英語Wikipediaから (種目, 性別, 階級) → 戴冠日 の辞書を作る"""
    url = "https://en.wikipedia.org/wiki/ONE_Championship"
    try:
        resp = requests.get(url, headers=WIKI_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ⚠ ONE 英語Wikipedia取得失敗: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    since_map = {}

    for table in soup.select("table.wikitable"):
        rows = table.select("tr")
        if not rows:
            continue
        headers = [th.get_text(" ", strip=True).lower() for th in rows[0].find_all(["th", "td"])]
        if "division" not in headers or "champion" not in headers or "since" not in headers:
            continue

        # 現王者テーブルは caption が Men / Women（トーナメント表を除外）
        caption = table.find("caption")
        cap_text = caption.get_text(" ", strip=True).lower() if caption else ""
        if cap_text.startswith("women"):
            gender = "女子"
        elif cap_text.startswith("men"):
            gender = "男子"
        else:
            continue

        col_div   = headers.index("division")
        col_champ = headers.index("champion")
        col_since = headers.index("since")
        disc = ""
        for row in _table_grid(table)[1:]:
            # 種目セパレーター行（全セル同一テキスト）
            uniq = set(row)
            if len(uniq) == 1 and row:
                d = row[0].lower()
                disc = next((v for k, v in ONE_DISCIPLINE_EN.items() if k in d), disc)
                continue
            if len(row) <= max(col_div, col_champ, col_since) or not disc:
                continue
            if "vacant" in row[col_champ].lower():
                continue
            div_raw = row[col_div].lower()
            weight_ja = next((v for k, v in ONE_WEIGHT_EN_TO_JA.items() if k in div_raw), "")
            since = _en_date_to_ja(row[col_since])
            if weight_ja and since:
                since_map[(disc, gender, weight_ja)] = since

    return since_map


def scrape_one_champions() -> list:
    """日本語WikipediaのONEページから全種目の王者を取得（戴冠日は英語版で補完）"""
    url = "https://ja.wikipedia.org/wiki/ONE_Championship"
    try:
        resp = requests.get(url, headers=WIKI_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ✗ ONE 日本語Wikipedia取得失敗: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    result = []

    for table in soup.select("table.wikitable"):
        rows = table.select("tr")
        if not rows:
            continue
        headers = [th.get_text(" ", strip=True) for th in rows[0].find_all(["th", "td"])]
        if "王者" not in headers or "階級" not in headers:
            continue

        # 種目はテーブル直前の見出しから
        prev = table.find_previous(re.compile("^h[2-4]$"))
        heading = prev.get_text(" ", strip=True) if prev else ""
        disc = next((v for k, v in ONE_DISCIPLINE_JA.items() if k in heading), "")
        if not disc:
            continue

        col_div   = headers.index("階級")
        col_champ = headers.index("王者")
        col_def   = headers.index("防衛回数") if "防衛回数" in headers else -1

        gender = "男子"
        for row in _table_grid(table)[1:]:
            uniq = set(row)
            if len(uniq) == 1 and row and row[0] in ("男子", "女子"):
                gender = row[0]
                continue
            if len(row) <= max(col_div, col_champ):
                continue
            champ_raw = row[col_champ]
            if "暫定" in champ_raw:          # 暫定王者はスキップ
                continue
            name = re.sub(r"（[^）]*）", "", champ_raw).strip()
            if not name or name in ("空位", "-", "–"):
                continue
            weight = re.sub(r"\s+", "", row[col_div])
            defenses = 0
            if 0 <= col_def < len(row):
                m = re.search(r"\d+", row[col_def])
                if m:
                    defenses = int(m.group())
            result.append({
                "_key":     (disc, gender, weight),
                "weight":   ("女子" if gender == "女子" else "") + f"{weight} {disc}",
                "name":     name,
                "since":    "",
                "defenses": defenses,
            })

    if not result:
        print("  ⚠ ONE 日本語Wikipediaから王者取得失敗")
        return []

    # 戴冠日を英語版からマージ
    since_map = _scrape_one_since_en()
    for c in result:
        c["since"] = since_map.get(c.pop("_key"), "")

    print(f"  ONE 日本語Wikipedia: {len(result)} 件取得")
    return result


UFC_WEIGHT_EN_TO_JA = {
    "light heavyweight": "ライトヘビー級",
    "heavyweight":       "ヘビー級",
    "middleweight":      "ミドル級",
    "welterweight":      "ウェルター級",
    "lightweight":       "ライト級",
    "featherweight":     "フェザー級",
    "bantamweight":      "バンタム級",
    "flyweight":         "フライ級",
    "strawweight":       "ストロー級",
}


def scrape_ufc_reign_info() -> dict:
    """英語Wikipedia List_of_UFC_champions から 階級(日本語) → {since, defenses}"""
    url = "https://en.wikipedia.org/wiki/List_of_UFC_champions"
    try:
        resp = requests.get(url, headers=WIKI_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ⚠ UFC 英語Wikipedia取得失敗: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    info = {}

    for table in soup.select("table.wikitable"):
        rows = table.select("tr")
        if not rows:
            continue
        headers = [th.get_text(" ", strip=True).lower() for th in rows[0].find_all(["th", "td"])]
        if not ("division" in headers and "champion" in headers and "since" in headers):
            continue
        # 現王者テーブルのみ（Men/Women 見出し直下）
        prev = table.find_previous(re.compile("^h[2-4]$"))
        heading = prev.get_text(" ", strip=True).lower() if prev else ""
        if heading.startswith("women"):
            prefix = "女子"
        elif heading.startswith("men"):
            prefix = ""
        else:
            continue

        col_div   = headers.index("division")
        col_since = headers.index("since")
        col_def   = headers.index("defenses") if "defenses" in headers else -1

        for row in _table_grid(table)[1:]:
            if len(row) <= max(col_div, col_since):
                continue
            div_raw = row[col_div].lower()
            weight_ja = next((v for k, v in UFC_WEIGHT_EN_TO_JA.items() if k in div_raw), "")
            if not weight_ja:
                continue
            since = _en_date_to_ja(row[col_since])
            defenses = 0
            if 0 <= col_def < len(row):
                m = re.search(r"\d+", row[col_def])
                if m:
                    defenses = int(m.group())
            if since:
                info[prefix + weight_ja] = {"since": since, "defenses": defenses}

    if info:
        print(f"  UFC 英語Wikipedia: 戴冠日 {len(info)} 件取得")
    return info


def collect_champions() -> None:
    print("\n[CHAMPIONS] UFC.com/rankings スクレイピング...")

    existing = {}
    if CHAMPS_FILE.exists():
        try:
            existing = json.loads(CHAMPS_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    ufc_data = scrape_ufc_rankings()
    if not ufc_data:
        print("  ⚠ UFC取得失敗 — 既存データを保持")
        ufc_data = existing.get("ufc", {"men": [], "women": [], "p4p_men": [], "p4p_women": []})

    # 戴冠日・防衛回数を英語Wikipediaからマージ
    reign_info = scrape_ufc_reign_info()
    if reign_info:
        for c in ufc_data.get("men", []) + ufc_data.get("women", []):
            r = reign_info.get(c["weight"])
            if r:
                c["since"]    = r["since"]
                c["defenses"] = r["defenses"]

    rizin_data = scrape_rizin_champions()
    if not rizin_data:
        print("  ⚠ RIZIN取得失敗 — 既存データを保持")
        rizin_data = existing.get("rizin") or RIZIN_CHAMPS_STATIC

    one_data = scrape_one_champions()
    if not one_data:
        print("  ⚠ ONE取得失敗 — 既存データを保持")
        one_data = existing.get("one") or ONE_CHAMPS_STATIC

    result = {
        "ufc":   ufc_data,
        "rizin": rizin_data,
        "one":   one_data,
    }

    save_json(CHAMPS_FILE, result)


# ============================================================
# メイン
# ============================================================
if __name__ == "__main__":
    print(f"=== MMA WAVE Collector  {NOW.strftime('%Y-%m-%d %H:%M')} JST ===")
    collect_news()
    collect_events()
    collect_champions()
    print("\n=== 完了 ===")
