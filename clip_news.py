#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
바이오의약품 뉴스클리핑 자동화 스크립트 (다국어 + 제목 번역 + 웹페이지 버전)
================================================================
- Google News RSS로 en-US / ko-KR / ja-JP / zh-CN / en-GB / de-DE 6개 지역 키워드 뉴스 수집
- 최근 N시간 이내 기사만 필터링
- 1차: 완전 동일한 제목은 수집 단계에서 즉시 제거
- 2차: 번역된 제목 기준으로 유사도 비교해 다른 매체/언어로 중복 보도된 기사까지 제거
- 원문 제목은 그대로 두고, 한국어가 아닌 기사는 무료 번역(API 키 불필요)으로
  한국어 번역 제목을 함께 표시 (병렬 처리 + 타임아웃 적용)
- (선택, API 키 있을 때만) 그룹당 최신 N건은 본문 요약도 추가 가능
- 다이제스트 표시 방식을 최신순 전체 나열(기본) / 국가별 / 키워드별 중 선택 가능 (GROUP_BY)
- 마크다운 다이제스트 + 스타일이 적용된 HTML 웹페이지 생성
  (docs/index.html — GitHub Pages로 호스팅하면 매일 접속해서 확인 가능, 아카이브 자동 보관)
- (선택) 이메일 발송(Gmail/메일플러그 등 SMTP) / Slack Webhook 전송 — 필요 없으면 안 써도 됨
- cron 또는 GitHub Actions로 매일 자동 실행

사용 전 준비 (기본, API 키 불필요):
    pip install feedparser requests python-dateutil --break-system-packages

요약 기능까지 쓰려면 (선택, Anthropic API 키 필요):
    pip install trafilatura anthropic --break-system-packages
    export ANTHROPIC_API_KEY=sk-ant-xxxx
    export ENABLE_SUMMARY=1

환경변수:
    GROUP_BY                   다이제스트 정렬 방식: recent(기본, 최신순 전체) | country(국가별) | keyword(키워드별)
    DEDUP_SIMILARITY_THRESHOLD 유사 중복 판단 기준(0~1, 기본 0.82). 낮출수록 더 엄격하게(더 많이) 제거
    ENABLE_TITLE_TRANSLATION   기본 1 (켜짐), 끄려면 0
    ENABLE_SUMMARY             기본 0 (꺼짐), API 키 있으면 1로 설정
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_TO, MAIL_FROM   (선택, 이메일 발송)
    SLACK_WEBHOOK_URL                                                (선택, 슬랙 전송)
"""

import os
import re
import time
import json
import hashlib
import difflib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from dateutil import parser as dateparser

# -----------------------------
# 1. 설정: 키워드 / 검색 옵션
# -----------------------------

KEYWORD_GROUPS = {
    "위탁개발생산(CDMO)": ["CDMO", "biologics CDMO", "위탁개발생산"],
    "바이오의약품 전반": ["Biologics", "Biopharmaceuticals", "recombinant technology", "유전자재조합의약품", "生物由來製品", "バイオ後続品", "遺伝子組換え", "生物制品", "生物活性的制品"],
    "세포유전자치료제": ["CAR-T", "CGT", "gene therapy", "viral vector", "cell therapy", "cancer vaccine", "oncolytic virus", "CRISPR-Cas9", "유전자치료제", "세포치료제", "再生医療等製品", "细胞治疗和基因治疗产品"],
    "항체/치료제 모달리티": ["monoclonal antibody", "antibody", "bispecific antibody", "Antibody drug conjugate", "항체"],
    "백신/톡신": ["vaccine", "botulinum toxin", "백신", "보툴리눔 톡신","疫苗", ],
    "규제/인허가": ["IND FDA", "FDA approval biologics", "PMDA approval", "BLA approval"
                 "China drug approval", "식품의약품안전처", "HHS biologics policy", "clinical trial", "biologics guideline"],
}

# 검색 언어/지역: 미국, 한국, 일본(PMDA), 중국(CDE), 영국, 독일(EU/EMA)
LANG_REGIONS = [
    {"hl": "en-US", "gl": "US", "ceid": "US:en"},
    {"hl": "ko", "gl": "KR", "ceid": "KR:ko"},
    {"hl": "ja", "gl": "JP", "ceid": "JP:ja"},
    {"hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"},
    {"hl": "en-GB", "gl": "GB", "ceid": "GB:en"},
    {"hl": "de", "gl": "DE", "ceid": "DE:de"},
]

LANG_NAMES = {
    "en-US": "미국", "ko": "한국", "ja": "일본",
    "zh-CN": "중국", "en-GB": "영국", "de": "독일",
}

# 규제·정책/산업 분석에 신뢰도가 높은 전문 매체 목록 - 이 매체 기사는 정렬 시 항상 위쪽에 오고
# 별도 뱃지(⭐)로 표시된다. 기사의 출처(source) 문자열에 아래 키워드가 포함되면 매칭.
PRIORITY_SOURCES = [
    "Pink Sheet", "BioCentury", "Endpoints News", "RAPS", "Regulatory Focus",
    "Fierce Biotech", "FierceBiotech", "Fierce Pharma", "FiercePharma",
    "STAT", "BioPharma Dive", "BioWorld",
]


def is_priority_source(source_name):
    """기사 출처가 신뢰도 높은 전문 매체 목록에 포함되는지 확인 (대소문자 무시, 부분 일치)."""
    if not source_name:
        return False
    lower = source_name.lower()
    return any(p.lower() in lower for p in PRIORITY_SOURCES)


# 다이제스트 표시 방식: "recent"(최신순 전체 나열, 기본) | "country"(국가별 그룹) | "keyword"(기존 키워드별 그룹)
GROUP_BY = os.environ.get("GROUP_BY", "recent")

# 유사 중복 기사 제거 기준 (0~1, 높을수록 엄격). 기본 0.82
DEDUP_SIMILARITY_THRESHOLD = float(os.environ.get("DEDUP_SIMILARITY_THRESHOLD", "0.78"))

LOOKBACK_HOURS = 30
OUTPUT_DIR = "digests"
os.makedirs(OUTPUT_DIR, exist_ok=True)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"

# -----------------------------
# 2. 요약 / 번역 설정
# -----------------------------

# Claude API 요약 (API 키 있을 때만 사용, 기본 꺼짐)
ENABLE_SUMMARY = os.environ.get("ENABLE_SUMMARY", "0") == "1"
SUMMARIZE_TOP_N_PER_GROUP = int(os.environ.get("SUMMARIZE_TOP_N_PER_GROUP", "5"))
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
MAX_ARTICLE_CHARS = 3000

# 제목 한국어 번역 (API 키 불필요, 무료 Google Translate 비공식 엔드포인트 사용)
# 요약과 무관하게 항상 켜짐. 끄고 싶으면 ENABLE_TITLE_TRANSLATION=0
ENABLE_TITLE_TRANSLATION = os.environ.get("ENABLE_TITLE_TRANSLATION", "1") == "1"

_anthropic_client = None
_translation_cache = {}


def get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import Anthropic
        _anthropic_client = Anthropic()  # ANTHROPIC_API_KEY 환경변수 자동 사용
    return _anthropic_client


def translate_title_to_ko(title, lang):
    """원문 제목을 한국어로 번역 (API 키 불필요, Google Translate 무료 엔드포인트 직접 호출).
    이미 한국어면 번역하지 않고 그대로 반환. 실패/타임아웃 시 None 반환 (원문만 표시됨)."""
    if not ENABLE_TITLE_TRANSLATION:
        return None
    if lang == "ko":
        return None  # 이미 한국어 원문이므로 번역 불필요

    if title in _translation_cache:
        return _translation_cache[title]

    try:
        resp = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": "ko", "dt": "t", "q": title},
            timeout=6,  # 응답이 느리면 6초 후 포기 (전체 실행이 늘어지는 것 방지)
        )
        resp.raise_for_status()
        data = resp.json()
        translated = "".join(seg[0] for seg in data[0] if seg[0])
        _translation_cache[title] = translated
        return translated
    except Exception as e:
        print(f"[WARN] 제목 번역 실패 ({title[:30]}...): {e}")
        return None


# -----------------------------
# 3. 수집
# -----------------------------

def build_url(keyword, lang_region):
    query = requests.utils.quote(f'"{keyword}" when:2d')
    return GOOGLE_NEWS_RSS.format(query=query, **lang_region)


def strip_source_suffix(title, source):
    """Google News RSS 제목 끝에 자동으로 붙는 " - 언론사명"을 제거.
    출처(source)가 있으면 정확히 일치하는 접미사부터 우선 제거하고,
    못 찾으면 일반적인 트레일링 " - XXX" 패턴으로 한 번 더 시도한다."""
    if source:
        suffix = f" - {source}"
        if title.endswith(suffix):
            return title[: -len(suffix)].rstrip()

    m = re.search(r"\s*[-–]\s*[^-–]{1,40}$", title)
    if m:
        return title[: m.start()].rstrip()
    return title


def fetch_keyword_articles(keyword, lang_region):
    url = build_url(keyword, lang_region)
    try:
        # feedparser 자체는 타임아웃 옵션이 없어서, requests로 먼저 받아온 뒤 파싱
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        feed = feedparser.parse(resp.content)
    except Exception as e:
        print(f"[WARN] RSS 요청 타임아웃/실패 ({keyword}, {lang_region['hl']}): {e}")
        return []

    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    for entry in feed.entries:
        try:
            published = dateparser.parse(entry.published)
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        if published < cutoff:
            continue

        source = entry.get("source", {}).get("title", "") if hasattr(entry, "get") else ""
        clean_title = strip_source_suffix(entry.title, source)
        articles.append({
            "title": clean_title,
            "link": entry.link,
            "published": published,
            "source": source,
            "keyword": keyword,
            "lang": lang_region["hl"],
        })
    return articles


def normalize_title(title):
    t = re.sub(r"\s*-\s*[^-]+$", "", title)
    t = re.sub(r"[^\w\s]", "", t).lower().strip()
    return t


def collect_all():
    all_articles = []
    seen_hashes = set()

    for group, keywords in KEYWORD_GROUPS.items():
        for kw in keywords:
            for lr in LANG_REGIONS:
                try:
                    arts = fetch_keyword_articles(kw, lr)
                except Exception as e:
                    print(f"[WARN] {kw} ({lr['hl']}) 수집 실패: {e}")
                    continue

                for a in arts:
                    key = hashlib.md5(normalize_title(a["title"]).encode()).hexdigest()
                    if key in seen_hashes:
                        continue
                    seen_hashes.add(key)
                    a["group"] = group
                    all_articles.append(a)

                time.sleep(0.4)

    return all_articles


# -----------------------------
# 4. 본문 추출 + 요약
# -----------------------------

def resolve_real_url(google_news_url):
    """Google News RSS 링크(리다이렉트)를 실제 언론사 URL로 변환"""
    try:
        resp = requests.get(
            google_news_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
            allow_redirects=True,
        )
        return resp.url
    except Exception:
        return google_news_url


def extract_article_text(url):
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        text = trafilatura.extract(downloaded)
        return text
    except Exception:
        return None


def summarize_ko(title, text, lang):
    """제목+본문을 받아 한국어 3줄 요약 생성 (실패 시 None)"""
    if not ENABLE_SUMMARY:
        return None
    try:
        client = get_anthropic_client()
        body = (text or "")[:MAX_ARTICLE_CHARS]
        prompt = (
            "다음은 바이오의약품 관련 뉴스 기사입니다. "
            "핵심 내용을 한국어로 3줄 이내 불릿으로 간결하게 요약해줘. "
            "불필요한 서론 없이 요약만 출력해.\n\n"
            f"[제목]\n{title}\n\n[본문]\n{body if body else '(본문 추출 실패, 제목만으로 추정 요약)'}"
        )
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        return "\n".join(parts).strip() or None
    except Exception as e:
        print(f"[WARN] 요약 실패 ({title[:30]}...): {e}")
        return None


def enrich_with_translations(articles):
    """전체 기사 대상으로 제목 번역만 수행 (본문 요약과 무관, N건 제한 없음).
    번역 대상 기사가 많아도 병렬로 처리해 전체 실행 시간을 단축한다."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    targets = [a for a in articles if a["lang"] != "ko"]
    if not targets:
        return articles

    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_article = {
            executor.submit(translate_title_to_ko, a["title"], a["lang"]): a
            for a in targets
        }
        for future in as_completed(future_to_article):
            a = future_to_article[future]
            try:
                a["title_ko"] = future.result()
            except Exception:
                a["title_ko"] = None

    return articles


def dedup_norm(text):
    """중복 비교용 정규화: 언론사 접미사 제거, 특수문자 제거, 소문자화"""
    t = re.sub(r"\s*[-–|]\s*[^-–|]{1,30}$", "", text)  # " - 연합뉴스" 같은 접미사 제거
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def dedup_similar_articles(articles, threshold=None):
    """같은 사건을 다룬 유사 기사(다른 매체·다른 언어로 중복 보도된 경우)를 제거한다.
    번역된 한국어 제목이 있으면 그걸 기준으로 비교해 언어가 달라도 잡아낸다.
    최신 기사를 우선 유지하고, 이미 채택된 기사들과 유사도(0~1)가 threshold 이상이면 제거."""
    if threshold is None:
        threshold = DEDUP_SIMILARITY_THRESHOLD

    articles_sorted = sorted(articles, key=lambda x: x["published"], reverse=True)
    kept = []
    kept_norms = []
    removed = 0

    for a in articles_sorted:
        key_text = a.get("title_ko") or a["title"]
        norm = dedup_norm(key_text)
        is_dup = False
        for kn in kept_norms:
            if difflib.SequenceMatcher(None, norm, kn).ratio() >= threshold:
                is_dup = True
                break
        if is_dup:
            removed += 1
            continue
        kept.append(a)
        kept_norms.append(norm)

    if removed:
        print(f"[INFO] 유사 중복 기사 {removed}건 제거 (유사도 기준 {threshold})")
    return kept


def enrich_with_summaries(articles):
    """그룹별 최신 N개 기사에 대해서만 본문 추출 + 요약 수행 (비용 관리)"""
    grouped = {}
    for a in articles:
        grouped.setdefault(a["group"], []).append(a)

    for group, arts in grouped.items():
        arts_sorted = sorted(arts, key=lambda x: x["published"], reverse=True)
        for a in arts_sorted[:SUMMARIZE_TOP_N_PER_GROUP]:
            real_url = resolve_real_url(a["link"])
            text = extract_article_text(real_url)
            a["summary"] = summarize_ko(a["title"], text, a["lang"])

    return articles


# -----------------------------
# 5. 다이제스트 생성
# -----------------------------

def organize_articles(articles):
    """GROUP_BY 설정에 따라 (섹션 제목 또는 None, 기사 리스트) 튜플 리스트를 반환.
    - "recent": 그룹 없이 전체를 최신순으로 나열 (섹션 제목 None)
    - "country": 국가/지역별로 묶어서, 각 그룹 내부는 최신순
    - "keyword": 기존 방식(키워드 그룹별)
    각 그룹 내부는 "우선 매체(PRIORITY_SOURCES) 여부"를 최우선 기준으로,
    그 다음 최신순으로 정렬한다 — 우선 매체 기사가 항상 위쪽에 온다."""

    def sort_key(a):
        return (0 if is_priority_source(a.get("source")) else 1, -a["published"].timestamp())

    if GROUP_BY == "keyword":
        grouped = {}
        for a in articles:
            grouped.setdefault(a["group"], []).append(a)
        return [
            (g, sorted(grouped.get(g, []), key=sort_key))
            for g in KEYWORD_GROUPS
        ]

    if GROUP_BY == "country":
        order = [LANG_NAMES.get(lr["hl"], lr["hl"]) for lr in LANG_REGIONS]
        grouped = {name: [] for name in order}
        for a in articles:
            name = LANG_NAMES.get(a["lang"], a["lang"])
            grouped.setdefault(name, []).append(a)
            if name not in order:
                order.append(name)
        return [
            (name, sorted(grouped[name], key=sort_key))
            for name in order
        ]

    # 기본값: "recent" — 그룹 없이 우선매체 우선 + 최신순 전체 나열
    return [(None, sorted(articles, key=sort_key))]


def build_markdown(articles):
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# 바이오의약품 뉴스클리핑 - {today}\n"]
    lines.append(f"(최근 {LOOKBACK_HOURS}시간 이내, 총 {len(articles)}건 · 6개 지역: 미국/한국/일본/중국/영국/독일)\n")

    for title, arts in organize_articles(articles):
        if not arts:
            continue
        if title is not None:
            lines.append(f"\n## {title} ({len(arts)}건)\n")
        for a in arts:
            pub_str = a["published"].strftime("%Y-%m-%d %H:%M UTC")
            src = f" - {a['source']}" if a["source"] else ""
            lang_tag = f" [{a['lang']}]"
            star = " ⭐" if is_priority_source(a.get("source")) else ""
            lines.append(f"- `[{a['keyword']}]` **[{a['title']}]({a['link']})**{star}{src} ({pub_str}){lang_tag}")
            if a.get("title_ko"):
                lines.append(f"  - 🇰🇷 번역: {a['title_ko']}")
            if a.get("summary"):
                for sline in a["summary"].splitlines():
                    if sline.strip():
                        lines.append(f"  > {sline.strip()}")

    return "\n".join(lines)


def save_markdown(md_text):
    today = datetime.now().strftime("%Y%m%d")
    path = os.path.join(OUTPUT_DIR, f"digest_{today}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md_text)
    print(f"[INFO] 다이제스트 저장: {path}")
    return path


# -----------------------------
# 5-1. 웹페이지(HTML) 생성 — GitHub Pages용
# -----------------------------

HTML_DIR = "docs"                       # GitHub Pages 기본 소스 폴더
HTML_ARCHIVE_DIR = os.path.join(HTML_DIR, "archive")
os.makedirs(HTML_ARCHIVE_DIR, exist_ok=True)

HTML_STYLE = """
<style>
  :root { --accent:#2563eb; --bg:#f7f8fa; --card:#ffffff; --text:#1f2430; --muted:#6b7280; --border:#e5e7eb; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic",sans-serif;
         background:var(--bg); color:var(--text); line-height:1.55; }
  .wrap { max-width:820px; margin:0 auto; padding:28px 20px 80px; }
  header { margin-bottom:24px; }
  header h1 { font-size:22px; margin:0 0 6px; }
  header .meta { color:var(--muted); font-size:13px; }
  header .meta a { color:var(--accent); text-decoration:none; }
  .group { background:var(--card); border:1px solid var(--border); border-radius:10px;
           padding:16px 18px; margin-bottom:16px; }
  .group h2 { font-size:16px; margin:0 0 12px; display:flex; justify-content:space-between; align-items:center; }
  .group h2 .count { font-weight:400; color:var(--muted); font-size:13px; }
  .item { padding:10px 0; border-top:1px solid var(--border); }
  .item:first-child { border-top:none; padding-top:0; }
  .item a.title { font-size:14.5px; font-weight:600; color:var(--text); text-decoration:none; }
  .item a.title:hover { color:var(--accent); }
  .item .sub { font-size:12px; color:var(--muted); margin-top:3px; }
  .item .translated { font-size:13px; color:#0d6b3f; margin-top:4px; }
  .item .summary { font-size:13px; color:#374151; margin-top:6px; background:#f3f4f6;
                    border-radius:6px; padding:8px 10px; }
  .tag { display:inline-block; background:#eef2ff; color:var(--accent); border-radius:5px;
         padding:1px 6px; font-size:11px; font-weight:600; }
  .tagrow { margin-bottom:4px; }
  .priority-item { background:#fffbeb; margin:0 -10px; padding:10px 10px; border-radius:8px; border-top:none; }
  .priority-item:first-child { padding-top:10px; }
  .priority-badge { display:inline-block; background:#fef3c7; color:#92400e; border-radius:5px;
                     padding:1px 6px; font-size:11px; font-weight:700; margin-left:4px; }
  .empty { color:var(--muted); font-size:13px; }
  .panel-header { display:flex; justify-content:space-between; align-items:baseline; margin:0 0 14px; }
  .panel-header h2 { font-size:18px; margin:0; }
  .panel-count { color:var(--muted); font-size:13px; }
  footer { margin-top:32px; color:var(--muted); font-size:12px; text-align:center; }
  footer a { color:var(--accent); }
</style>
"""


def escape_html(text):
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_news_panel_html(articles):
    """뉴스클리핑 패널의 내부 HTML만 생성 (전체 <html> 래핑 없음).
    build_dashboard.py에서 다른 패널과 조합할 때 사용."""
    sections_data = organize_articles(articles)

    sections = ['<div class="panel-header"><h2>📰 뉴스클리핑</h2>'
                f'<span class="panel-count">총 {len(articles)}건</span></div>']
    for title, arts in sections_data:
        sections.append('<div class="group">')
        if title is not None:
            sections.append(
                f'<h2>{escape_html(title)} <span class="count">{len(arts)}건</span></h2>'
            )
        if not arts:
            sections.append('<p class="empty">최근 수집된 기사가 없습니다.</p>')
        else:
            for a in arts:
                pub_str = a["published"].strftime("%Y-%m-%d %H:%M UTC")
                src = f" · {escape_html(a['source'])}" if a["source"] else ""
                country = LANG_NAMES.get(a["lang"], a["lang"])
                priority = is_priority_source(a.get("source"))
                item_class = "item priority-item" if priority else "item"
                star_badge = ' <span class="priority-badge">⭐ 우선매체</span>' if priority else ""
                sections.append(f'<div class="{item_class}">')
                sections.append(
                    f'<div class="tagrow"><span class="tag">{escape_html(a["keyword"])}</span>{star_badge}</div>'
                )
                sections.append(
                    f'<a class="title" href="{escape_html(a["link"])}" target="_blank" rel="noopener">'
                    f'{escape_html(a["title"])}</a>'
                )
                sections.append(
                    f'<div class="sub">{pub_str}{src} · {escape_html(country)}</div>'
                )
                if a.get("title_ko"):
                    sections.append(f'<div class="translated">🇰🇷 {escape_html(a["title_ko"])}</div>')
                if a.get("summary"):
                    sections.append(
                        f'<div class="summary">{escape_html(a["summary"]).replace(chr(10), "<br>")}</div>'
                    )
                sections.append("</div>")
        sections.append("</div>")

    return "\n".join(sections)


def build_html(articles):
    """뉴스클리핑 단독 웹페이지 생성 (report_monitor 없이 clip_news.py만 실행할 때 사용)."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    body = build_news_panel_html(articles)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>바이오의약품 뉴스클리핑 - {today_str}</title>
{HTML_STYLE}
</head>
<body>
<div class="wrap">
  <header>
    <h1>바이오의약품 뉴스클리핑</h1>
    <div class="meta">{today_str} 업데이트 · 최근 {LOOKBACK_HOURS}시간 · 총 {len(articles)}건 ·
      <a href="archive/">지난 다이제스트 보기</a>
    </div>
  </header>
  {body}
  <footer>매일 자동 수집 · Google News RSS 기반 · 무료 번역(Google Translate)</footer>
</div>
</body>
</html>"""
    return html


def build_archive_index():
    """archive 폴더 안의 과거 다이제스트 목록 페이지 생성"""
    files = sorted(
        [f for f in os.listdir(HTML_ARCHIVE_DIR) if f.startswith("digest_") and f.endswith(".html")],
        reverse=True,
    )
    items = "\n".join(
        f'<li><a href="{f}">{f.replace("digest_", "").replace(".html", "")}</a></li>' for f in files
    )
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><title>지난 다이제스트</title>
<style>body{{font-family:sans-serif;max-width:600px;margin:40px auto;padding:0 20px;}}
a{{color:#2563eb;text-decoration:none;}} li{{margin:6px 0;}}</style></head>
<body>
<h2>지난 다이제스트 목록</h2>
<p><a href="../">← 오늘 다이제스트로</a></p>
<ul>
{items}
</ul>
</body></html>"""
    with open(os.path.join(HTML_ARCHIVE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


def save_html(html_text):
    today = datetime.now().strftime("%Y%m%d")
    # 오늘자 최신 페이지 (GitHub Pages 루트 index.html — 접속 시 항상 최신)
    index_path = os.path.join(HTML_DIR, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_text)
    # 아카이브에도 동일한 내용 보관
    archive_path = os.path.join(HTML_ARCHIVE_DIR, f"digest_{today}.html")
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write(html_text)
    build_archive_index()
    # GitHub Pages가 Jekyll로 재가공하지 않고 정적 파일 그대로 서빙하도록 설정
    nojekyll_path = os.path.join(HTML_DIR, ".nojekyll")
    if not os.path.exists(nojekyll_path):
        open(nojekyll_path, "w").close()
    print(f"[INFO] 웹페이지 저장: {index_path} (아카이브: {archive_path})")
    return index_path


# -----------------------------
# 6. (선택) 전송
# -----------------------------

def send_email(md_text):
    host = os.environ.get("SMTP_HOST")
    if not host:
        return

    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    pw = os.environ["SMTP_PASS"]
    to_addr = os.environ["MAIL_TO"]
    from_addr = os.environ.get("MAIL_FROM", user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[바이오의약품 뉴스클리핑] {datetime.now().strftime('%Y-%m-%d')}"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(md_text, "plain", "utf-8"))

    if port == 465:
        # SSL 방식 (메일플러그 등 대부분의 기업 메일이 여기 해당)
        with smtplib.SMTP_SSL(host, port) as server:
            if os.environ.get("SMTP_DEBUG") == "1":
                server.set_debuglevel(1)
            server.login(user, pw)
            server.sendmail(from_addr, [to_addr], msg.as_string())
    else:
        # STARTTLS 방식 (Gmail 587 등)
        with smtplib.SMTP(host, port) as server:
            if os.environ.get("SMTP_DEBUG") == "1":
                server.set_debuglevel(1)
            server.starttls()
            server.login(user, pw)
            server.sendmail(from_addr, [to_addr], msg.as_string())
    print("[INFO] 이메일 발송 완료")


def send_slack(md_text):
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        return
    payload = {"text": md_text[:3800]}
    requests.post(webhook, data=json.dumps(payload),
                  headers={"Content-Type": "application/json"})
    print("[INFO] Slack 전송 완료")


# -----------------------------
# 7. 메인
# -----------------------------

def main():
    print("[INFO] 뉴스 수집 시작 (미국/한국/일본/중국/영국/독일)...")
    articles = collect_all()
    print(f"[INFO] 수집 완료: {len(articles)}건 (완전 동일 제목 제거 후)")

    if ENABLE_TITLE_TRANSLATION:
        print("[INFO] 원문 기사 제목 한국어 번역 중...")
        articles = enrich_with_translations(articles)

    print("[INFO] 유사 중복 기사 제거 중...")
    articles = dedup_similar_articles(articles)
    print(f"[INFO] 최종 기사 수: {len(articles)}건")

    if ENABLE_SUMMARY:
        print(f"[INFO] 그룹별 최신 {SUMMARIZE_TOP_N_PER_GROUP}건 요약 생성 중...")
        articles = enrich_with_summaries(articles)

    md_text = build_markdown(articles)
    save_markdown(md_text)

    html_text = build_html(articles)
    save_html(html_text)

    send_email(md_text)
    send_slack(md_text)

    print("[INFO] 완료")


if __name__ == "__main__":
    main()
