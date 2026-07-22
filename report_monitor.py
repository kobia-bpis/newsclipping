#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
보고서 모니터링 모듈
====================
컨설팅사·기관 인사이트 페이지에서 최신 보고서 목록을 수집한다.

동작 방식이 두 가지로 나뉜다:
1. SCRAPE_SOURCES: 정적 HTML이라 실제로 제목·링크를 긁어올 수 있는 곳
   (href의 URL 패턴으로 매칭 — CSS 클래스에 의존하지 않아 사이트 개편에 비교적 안전)
2. STATIC_LINK_SOURCES: 자바스크립트 렌더링이거나(Deloitte, IQVIA Points of View),
   자동 접근이 차단됐거나(NIFDS, BCG), 페이지 자체에 목록이 없는(PwC) 곳.
   이런 곳은 개별 보고서 추출을 포기하고 "바로가기" 카드만 제공한다.

새로 나타난 항목은 이전 실행 결과(docs/seen_reports.json)와 비교해 NEW 뱃지를 붙인다.
"""

import os
import re
import json
import requests
from bs4 import BeautifulSoup

SEEN_STATE_PATH = "docs/seen_reports.json"
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ReportMonitorBot/1.0; +https://github.com)"}
REQUEST_TIMEOUT = 12

# -----------------------------
# 1. 실제로 긁어오는 소스 (href URL 패턴 매칭 방식)
# -----------------------------

SCRAPE_SOURCES = [
    {
        "label": "한국바이오협회 - 리포트",
        "url": "https://koreabio.org/board/board.php?bo_table=report",
        "pattern": r"bo_table=report&idx=\d+",
        "limit": 6,
    },
    {
        "label": "한국바이오협회 - 브리프",
        "url": "https://koreabio.org/board/board.php?bo_table=brief",
        "pattern": r"bo_table=brief&idx=\d+",
        "limit": 6,
    },
    {
        "label": "IQVIA Institute",
        "url": "https://www.iqvia.com/insights/the-iqvia-institute",
        "pattern": r"/insights/the-iqvia-institute/reports-and-publications/reports/[a-z0-9]",
        "limit": 8,
    },
    {
        "label": "Evaluate Thought Leadership",
        "url": "https://www.evaluate.com/thought-leadership/",
        "pattern": r"/thought-leadership/[a-zA-Z0-9][a-zA-Z0-9\-]{4,}",
        "exclude_exact": [
            "https://www.evaluate.com/thought-leadership/",
            "https://www.evaluate.com/ja/thought-leadership/",
        ],
        "limit": 8,
    },
    {
        "label": "KPMG Insights (제약·바이오 필터)",
        "url": "https://kpmg.com/kr/ko/insights.html",
        "pattern": r"/insights/(eri|aci|tkc)/",
        "limit": 30,  # 필터링 전 넉넉히 수집
        "keyword_filter": [
            "제약", "바이오", "헬스케어", "의료", "생명과학", "건강", "의약품", "병원",
            "Pharma", "Health", "Bio", "Life Science",
        ],
        "post_limit": 6,  # 필터링 후 최종 개수
    },
]

# -----------------------------
# 2. 자동 수집이 어려운 소스 (바로가기만 제공)
# -----------------------------

STATIC_LINK_SOURCES = [
    {"label": "PwC Healthcare", "url": "https://www.pwc.com/kr/ko/industry/healthcare.html",
     "note": "페이지에 개별 보고서 목록이 없어 바로가기만 제공합니다."},
    {"label": "Deloitte 제약·바이오", "url": "https://www.deloitte.com/kr/ko/Industries/life-sciences.html",
     "note": "자바스크립트 렌더링 페이지라 바로가기만 제공합니다."},
    {"label": "IQVIA Points of View", "url": "https://www.iqvia.com/insights/points-of-view",
     "note": "자바스크립트 렌더링 페이지라 바로가기만 제공합니다."},
    {"label": "BCG Insights", "url": "https://www.bcg.com/search?q=&f3=00000184-8641-d1ac-a9ef-c7d178680000&f7=00000171-f17b-d394-ab73-f3fbae0d0000&s=1",
     "note": "사이트가 자동 접근을 차단해 바로가기만 제공합니다."},
    {"label": "식품의약품안전평가원(NIFDS)", "url": "https://www.nifds.go.kr/brd/m_18/list.do",
     "note": "사이트가 자동 접근을 차단해 바로가기만 제공합니다."},
]


def fetch(url, timeout=REQUEST_TIMEOUT):
    return requests.get(url, headers=REQUEST_HEADERS, timeout=timeout)


def load_seen():
    if os.path.exists(SEEN_STATE_PATH):
        try:
            with open(SEEN_STATE_PATH, encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(seen_links):
    os.makedirs(os.path.dirname(SEEN_STATE_PATH), exist_ok=True)
    with open(SEEN_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_links), f, ensure_ascii=False)


def scrape_by_href_pattern(source):
    """href URL 패턴으로 보고서 링크를 찾는 범용 스크래퍼.
    CSS 클래스명에 의존하지 않아 사이트 디자인이 바뀌어도 비교적 안전하다."""
    label = source["label"]
    try:
        resp = fetch(source["url"])
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"[WARN] {label} 요청 실패: {e}")
        return []

    pattern = re.compile(source["pattern"])
    exclude_exact = set(source.get("exclude_exact", []))
    keyword_filter = source.get("keyword_filter")
    limit = source.get("limit", 8)

    items = []
    seen_links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not pattern.search(href):
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 4:
            continue
        full_link = href if href.startswith("http") else requests.compat.urljoin(source["url"], href)
        if full_link in exclude_exact or full_link in seen_links:
            continue
        if keyword_filter and not any(kw in title for kw in keyword_filter):
            continue
        seen_links.add(full_link)
        items.append({"title": title, "link": full_link, "source": label})
        if len(items) >= limit:
            break

    post_limit = source.get("post_limit")
    if post_limit:
        items = items[:post_limit]

    print(f"[INFO] {label}: {len(items)}건 수집")
    return items


def collect_reports():
    """전체 소스에서 보고서를 수집하고 NEW 여부를 표시한다."""
    seen = load_seen()
    all_items = []

    for source in SCRAPE_SOURCES:
        items = scrape_by_href_pattern(source)
        for item in items:
            item["is_new"] = item["link"] not in seen
        all_items.extend(items)

    new_seen = seen | {item["link"] for item in all_items}
    save_seen(new_seen)

    return all_items


# -----------------------------
# 3. HTML 렌더링
# -----------------------------

def escape_html(text):
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_report_panel_html(items):
    """보고서 모니터링 패널의 내부 HTML(카드들)을 생성. 전체 <html> 래핑은 하지 않는다."""
    grouped = {}
    order = []
    for item in items:
        src = item["source"]
        if src not in grouped:
            grouped[src] = []
            order.append(src)
        grouped[src].append(item)

    parts = ['<div class="panel-header"><h2>📑 보고서 모니터링</h2></div>']

    if not order:
        parts.append('<p class="empty">수집된 보고서가 없습니다.</p>')
    else:
        for src in order:
            parts.append('<div class="src-block">')
            parts.append(f'<h3>{escape_html(src)}</h3>')
            for item in grouped[src]:
                new_badge = ' <span class="new-badge">NEW</span>' if item.get("is_new") else ""
                parts.append(
                    f'<div class="src-item"><a href="{escape_html(item["link"])}" target="_blank" '
                    f'rel="noopener">{escape_html(item["title"])}</a>{new_badge}</div>'
                )
            parts.append("</div>")

    # 자동 수집 불가 소스 -> 바로가기 카드
    parts.append('<div class="src-block"><h3>바로가기 (자동 수집 불가)</h3>')
    for s in STATIC_LINK_SOURCES:
        parts.append(
            f'<div class="src-item quicklink"><a href="{escape_html(s["url"])}" target="_blank" '
            f'rel="noopener">{escape_html(s["label"])} →</a>'
            f'<div class="quicklink-note">{escape_html(s["note"])}</div></div>'
        )
    parts.append("</div>")

    return "\n".join(parts)


if __name__ == "__main__":
    reports = collect_reports()
    print(f"[INFO] 총 {len(reports)}건 수집 완료")
    html = build_report_panel_html(reports)
    os.makedirs("docs", exist_ok=True)
    with open("docs/_report_panel_preview.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("[INFO] docs/_report_panel_preview.html 에 미리보기 저장")
