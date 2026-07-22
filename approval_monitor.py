#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
허가 모니터링 모듈
==================
4개국 규제기관에서 신약/생물의약품 허가 정보를 수집한다. 소스별로 신뢰도가 다르다:

- FDA CBER, FDA CDER : 정적 HTML 테이블 확인 완료, 안정적으로 동작
- PMDA               : 목록이 PDF/Excel 첨부파일로만 제공되어, 첨부파일 URL이 바뀌면
                        "갱신 감지" 알림만 표시 (개별 품목 추출은 하지 않음)
- 한국 MFDS, EU       : 페이지 구조를 사전 검증하지 못해 최초 버전은 베스트 에포트.
                        실제 실행 결과를 보고 선택자를 조정해야 할 수 있다.
"""

import os
import re
import json
import requests
from datetime import datetime
from bs4 import BeautifulSoup

SEEN_STATE_PATH = "docs/seen_approvals.json"
PMDA_STATE_PATH = "docs/pmda_attachments.json"
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ApprovalMonitorBot/1.0; +https://github.com)"}
REQUEST_TIMEOUT = 15


def fetch(url, timeout=REQUEST_TIMEOUT):
    return requests.get(url, headers=REQUEST_HEADERS, timeout=timeout)


def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


# -----------------------------
# 1. FDA CBER - "What's New for Biologics" (안정적)
# -----------------------------

def scrape_fda_cber(limit=10):
    label = "FDA CBER (생물학적제제)"
    url = "https://www.fda.gov/vaccines-blood-biologics/news-events-biologics/whats-new-biologics"
    try:
        resp = fetch(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"[WARN] {label} 요청 실패: {e}")
        return []

    items = []
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        date_text = cells[0].get_text(strip=True)
        link_tag = cells[1].find("a")
        if not link_tag:
            continue
        title = link_tag.get_text(strip=True)
        if "Approval Letter" not in title:
            continue  # 허가 레터만 (SOPP·가이던스 문서 등은 제외)
        href = link_tag.get("href", "")
        full_link = href if href.startswith("http") else "https://www.fda.gov" + href
        items.append({"date": date_text, "title": title, "link": full_link, "source": label})

    print(f"[INFO] {label}: {len(items)}건 수집")
    return items[:limit]


# -----------------------------
# 2. FDA CDER - Drugs@FDA 월간 승인 리포트 (안정적이나 노이즈 필터링 필요)
# -----------------------------

def scrape_fda_cder(limit=15):
    label = "FDA CDER"
    url = "https://www.accessdata.fda.gov/SCRIPTS/CDER/DAF/index.cfm?event=reportsSearch.process"
    try:
        resp = fetch(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"[WARN] {label} 요청 실패: {e}")
        return []

    tables = soup.find_all("table")
    if not tables:
        print(f"[WARN] {label}: 테이블을 찾지 못함")
        return []
    # 행이 가장 많은 테이블 = 승인 목록 테이블
    target = max(tables, key=lambda t: len(t.find_all("tr")))

    items = []
    for row in target.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 6:
            continue
        link_tag = cells[1].find("a") if len(cells) > 1 else None
        if not link_tag:
            continue
        drug_name = link_tag.get_text(strip=True)
        date_text = cells[0].get_text(strip=True)
        classification = cells[5].get_text(strip=True) if len(cells) > 5 else ""

        # 노이즈 제거: 생물의약품(BLA) 또는 진짜 신약(New Molecular Entity)만
        is_bla = "BLA" in drug_name
        is_nme = "New Molecular Entity" in classification
        if not (is_bla or is_nme):
            continue

        href = link_tag.get("href", "")
        full_link = href if href.startswith("http") else "https://www.accessdata.fda.gov" + href
        tag = "BLA(생물의약품)" if is_bla else "신물질(NME)"
        items.append({
            "date": date_text, "title": f"{drug_name} [{tag}]",
            "link": full_link, "source": label,
        })

    print(f"[INFO] {label}: {len(items)}건 수집 (BLA/신물질 필터 적용)")
    return items[:limit]


# -----------------------------
# 3. PMDA - 첨부파일(PDF/Excel) 갱신 감지 방식
# -----------------------------

PMDA_PAGES = [
    {"label": "PMDA 신의약품 승인목록", "url": "https://www.pmda.go.jp/review-services/drug-reviews/review-information/p-drugs/0040.html"},
    {"label": "PMDA 신재생의료등제품 승인목록", "url": "https://www.pmda.go.jp/review-services/drug-reviews/review-information/ctp/0018.html"},
]


def get_last_modified_date(url):
    """파일 URL의 HTTP Last-Modified 헤더로 실제 파일 갱신 날짜를 가져온다.
    헤더가 없으면 None 반환 (호출부에서 감지 날짜로 대체)."""
    try:
        resp = requests.head(url, headers=REQUEST_HEADERS, timeout=8, allow_redirects=True)
        lm = resp.headers.get("Last-Modified")
        if lm:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(lm)
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def check_pmda_updates():
    """PMDA는 개별 승인 품목이 HTML이 아닌 PDF/Excel 첨부파일로만 제공된다.
    첨부파일 URL이 이전 실행과 다르면(=파일이 갱신됐다는 뜻) NEW로 표시하고,
    가능하면 파일의 실제 갱신 날짜(Last-Modified 헤더)도 함께 보여준다."""
    prev_state = load_json(PMDA_STATE_PATH) or {}
    new_state = {}
    results = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    for page in PMDA_PAGES:
        label = page["label"]
        try:
            resp = fetch(page["url"])
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"[WARN] {label} 요청 실패: {e}")
            continue

        attachment_hrefs = sorted({
            a["href"] for a in soup.find_all("a", href=True)
            if a["href"].lower().endswith((".pdf", ".xlsx", ".xls"))
        })
        attachments_full = [
            href if href.startswith("http") else requests.compat.urljoin(page["url"], href)
            for href in attachment_hrefs
        ]

        new_state[label] = attachment_hrefs
        prev_attachments = set(prev_state.get(label, []))
        is_updated = bool(attachment_hrefs) and set(attachment_hrefs) != prev_attachments

        date_str = None
        if attachments_full:
            date_str = get_last_modified_date(attachments_full[0])
        if not date_str and is_updated:
            date_str = today_str  # 헤더로 못 가져오면 감지된(오늘) 날짜로 대체

        note = "최근 갱신 없음"
        if is_updated:
            note = f"첨부파일이 갱신되었습니다 ({date_str}) - 클릭해서 확인하세요" if date_str else "첨부파일이 갱신되었습니다 - 클릭해서 확인하세요"

        results.append({
            "title": label,
            "link": page["url"],
            "source": "PMDA (일본)",
            "is_new": is_updated,
            "date": date_str,
            "note": note,
        })
        print(f"[INFO] {label}: 첨부 {len(attachment_hrefs)}개, 갱신여부={is_updated}, 날짜={date_str}")

    save_json(PMDA_STATE_PATH, new_state)
    return results


# -----------------------------
# 4. 한국 MFDS(식약처) - 베스트 에포트 (실행 결과 보고 조정 필요)
# -----------------------------

MFDS_SOURCES = [
    {
        "label": "국내 생물의약품 허가",
        "url": "https://nedrug.mfds.go.kr/searchDrug?sort=&sortOrder=false&searchYn=true&ExcelRowdata=&page=1&searchDivision=detail&itemName=&itemEngName=&entpName=&entpEngName=&ingrName1=&ingrName2=&ingrName3=&ingrEngName=&itemSeq=&stdrCodeName=&atcCodeName=&indutyClassCode=C0&sClassNo=&narcoticKindCode=&cancelCode=&etcOtcCode=&makeMaterialGb=&searchConEe=AND&eeDocData=&searchConUd=AND&udDocData=&searchConNb=AND&nbDocData=&startPermitDate=&endPermitDate=",
    },
    {
        "label": "국내 첨단바이오의약품 허가",
        "url": "https://nedrug.mfds.go.kr/searchDrug?sort=&sortOrder=false&searchYn=true&ExcelRowdata=&page=1&searchDivision=detail&itemName=&itemEngName=&entpName=&entpEngName=&ingrName1=&ingrName2=&ingrName3=&ingrEngName=&itemSeq=&stdrCodeName=&atcCodeName=&indutyClassCode=J0&sClassNo=&narcoticKindCode=&cancelCode=&etcOtcCode=&makeMaterialGb=&searchConEe=AND&eeDocData=&searchConUd=AND&udDocData=&searchConNb=AND&nbDocData=&startPermitDate=&endPermitDate=",
    },
]


def scrape_mfds(source, limit=10):
    label = source["label"]
    try:
        resp = fetch(source["url"])
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"[WARN] {label} 요청 실패: {e}")
        return []

    tables = soup.find_all("table")
    if not tables:
        print(f"[WARN] {label}: 테이블을 찾지 못함 (페이지 구조 확인 필요 - 베스트 에포트 단계)")
        return []

    target = max(tables, key=lambda t: len(t.find_all("tr")))
    items = []
    for row in target.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        link_tag = row.find("a", href=True)
        title = cells[1].get_text(strip=True) if len(cells) > 1 else row.get_text(strip=True)[:80]
        if not title:
            continue
        href = link_tag["href"] if link_tag else None
        if href and href.startswith("http"):
            full_link = href
        elif href:
            full_link = "https://nedrug.mfds.go.kr" + (href if href.startswith("/") else "/" + href)
        else:
            full_link = source["url"]
        items.append({"title": title, "link": full_link, "source": label})
        if len(items) >= limit:
            break

    print(f"[INFO] {label}: {len(items)}건 수집 (베스트 에포트)")
    return items


# -----------------------------
# 5. EU 의약품 허가 - EMA "What's New" (안정적)
# -----------------------------
# 참고: 사용자가 처음 제시한 ec.europa.eu/health/documents/community-register 는
# EU가 사이트를 commission.europa.eu로 통합 이전하면서 껍데기만 남고 실제 목록이
# 사라진 상태(브라우저에서 열면 commission.europa.eu/index_en로 넘어감).
# 대신 EMA(유럽의약품청)의 "What's New" 피드가 실제로 작동하는 정적 테이블이라
# 이쪽으로 교체했다. Type이 "Medicine"인 항목만 필터링한다.
# 주의: 이 피드는 신규 허가와 기존 의약품의 라벨 변경 등 갱신을 구분하지 않고
# 모두 "Medicine" 항목으로 묶어서 보여준다 — 100% 신규 허가만은 아님.

EU_SOURCE = {
    "label": "EU 의약품 정보 갱신 (EMA)",
    "url": "https://www.ema.europa.eu/en/news-events/whats-new",
}


def scrape_eu(limit=15):
    label = EU_SOURCE["label"]
    try:
        resp = fetch(EU_SOURCE["url"])
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"[WARN] {label} 요청 실패: {e}")
        return []

    tables = soup.find_all("table")
    if not tables:
        print(f"[WARN] {label}: 테이블을 찾지 못함 (페이지 구조 확인 필요)")
        return []
    target = max(tables, key=lambda t: len(t.find_all("tr")))

    items = []
    for row in target.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        date_text = cells[0].get_text(strip=True)
        content_cell = cells[1]
        type_tag = content_cell.find("strong") or content_cell.find("b")
        content_type = type_tag.get_text(strip=True).rstrip(":") if type_tag else ""
        if content_type != "Medicine":
            continue  # 의약품 항목만 (문서·이벤트·PIP·Orphan 등은 제외)

        link_tag = content_cell.find("a")
        if not link_tag:
            continue
        title = link_tag.get_text(strip=True)
        href = link_tag.get("href", "")
        full_link = href if href.startswith("http") else "https://www.ema.europa.eu" + href
        items.append({"date": date_text, "title": title, "link": full_link, "source": label})
        if len(items) >= limit:
            break

    print(f"[INFO] {label}: {len(items)}건 수집 (Medicine 항목만 필터)")
    return items


# -----------------------------
# 6. 통합 수집 + NEW 뱃지
# -----------------------------

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


def collect_approvals():
    seen = load_seen()
    all_items = []

    all_items.extend(scrape_fda_cber())
    all_items.extend(scrape_fda_cder())
    for src in MFDS_SOURCES:
        all_items.extend(scrape_mfds(src))
    all_items.extend(scrape_eu())

    for item in all_items:
        item["is_new"] = item["link"] not in seen

    new_seen = seen | {item["link"] for item in all_items}
    save_seen(new_seen)

    pmda_results = check_pmda_updates()  # PMDA는 별도 상태 파일로 갱신 여부만 관리

    return all_items, pmda_results


# -----------------------------
# 7. HTML 렌더링
# -----------------------------

def escape_html(text):
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_approval_panel_html(items, pmda_results):
    grouped = {}
    order = []
    for item in items:
        src = item["source"]
        if src not in grouped:
            grouped[src] = []
            order.append(src)
        grouped[src].append(item)

    parts = ['<div class="panel-header"><h2>✅ 허가 모니터링</h2></div>']

    if not order and not pmda_results:
        parts.append('<p class="empty">수집된 허가 정보가 없습니다.</p>')
    else:
        for src in order:
            parts.append('<div class="src-block">')
            parts.append(f'<h3>{escape_html(src)}</h3>')
            for item in grouped[src]:
                new_badge = ' <span class="new-badge">NEW</span>' if item.get("is_new") else ""
                date_str = f' <span class="date-tag">{escape_html(item["date"])}</span>' if item.get("date") else ""
                parts.append(
                    f'<div class="src-item"><a href="{escape_html(item["link"])}" target="_blank" '
                    f'rel="noopener">{escape_html(item["title"])}</a>{date_str}{new_badge}</div>'
                )
            parts.append("</div>")

        if pmda_results:
            parts.append('<div class="src-block"><h3>PMDA (일본)</h3>')
            for r in pmda_results:
                new_badge = ' <span class="new-badge">갱신됨</span>' if r.get("is_new") else ""
                parts.append(
                    f'<div class="src-item"><a href="{escape_html(r["link"])}" target="_blank" '
                    f'rel="noopener">{escape_html(r["title"])}</a>{new_badge}'
                    f'<div class="quicklink-note">{escape_html(r["note"])}</div></div>'
                )
            parts.append("</div>")

    return "\n".join(parts)


if __name__ == "__main__":
    approvals, pmda = collect_approvals()
    print(f"[INFO] 총 {len(approvals)}건 수집 완료 (PMDA {len(pmda)}건 별도)")
    html = build_approval_panel_html(approvals, pmda)
    os.makedirs("docs", exist_ok=True)
    with open("docs/_approval_panel_preview.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("[INFO] docs/_approval_panel_preview.html 에 미리보기 저장")
