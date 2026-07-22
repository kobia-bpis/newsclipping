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
import time
import requests
from datetime import datetime, timedelta
from dateutil import parser as dateparser
from bs4 import BeautifulSoup

SEEN_STATE_PATH = "docs/seen_approvals.json"
PMDA_STATE_PATH = "docs/pmda_attachments.json"
# 일부 사이트(FDA 등)가 "Bot"이 들어간 User-Agent를 자동 차단하므로 일반 브라우저처럼 위장한다.
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.com/",
}
REQUEST_TIMEOUT = 15


def normalize_date(date_text):
    """다양한 형식의 날짜 문자열을 YYYY-MM-DD로 통일. 파싱 실패 시 원본 텍스트 그대로 반환."""
    if not date_text:
        return ""
    try:
        dt = dateparser.parse(date_text, fuzzy=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return date_text


def fetch(url, timeout=REQUEST_TIMEOUT, retries=3, backoff=2):
    """연결이 자주 끊기거나(MFDS 등) 일시적으로 차단되는 사이트(FDA 등)를 위해
    재시도 로직을 넣은 요청 함수. HTTP 오류 상태코드도 재시도 대상에 포함한다."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < retries:
                wait = backoff * attempt
                print(f"[WARN] 요청 실패 ({attempt}/{retries}), {wait}초 후 재시도: {e}")
                time.sleep(wait)
    raise last_exc


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
        items.append({"date": normalize_date(date_text), "title": title, "link": full_link, "source": label})

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

    # 테이블 컬럼 순서: Approval Date | Drug Name | Submission | Active Ingredients |
    #                   Company | Submission Classification | Submission Status
    items = []
    for row in target.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        link_tag = cells[1].find("a") if len(cells) > 1 else None
        if not link_tag:
            continue
        drug_name = link_tag.get_text(strip=True)
        date_text = cells[0].get_text(strip=True)
        submission_type = cells[2].get_text(strip=True)  # 예: "ORIG-1", "SUPPL-23"

        # BLA(생물의약품)이면서, 신규 신청(Original)인 건만 — 라벨 변경 등 보충신청(SUPPL-*)은 제외
        is_bla = "BLA" in drug_name
        is_original = submission_type.upper().startswith("ORIG")
        if not (is_bla and is_original):
            continue

        href = link_tag.get("href", "")
        full_link = href if href.startswith("http") else "https://www.accessdata.fda.gov" + href
        items.append({
            "date": normalize_date(date_text), "title": f"{drug_name} [{submission_type}]",
            "link": full_link, "source": label,
        })

    print(f"[INFO] {label}: {len(items)}건 수집 (BLA + Original 신청건만 필터)")
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

def build_mfds_url(induty_class_code, page=1, years_back=2):
    """최근 N년(기본 2년) 허가 건만 검색하도록 startPermitDate/endPermitDate 필터를 넣어 URL을 만든다.
    두 값 모두 채워야 필터가 실제로 적용되고, 형식은 YYYY-MM-DD (대시 포함)여야 한다."""
    today = datetime.now()
    start_date = (today - timedelta(days=365 * years_back)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")
    return (
        f"https://nedrug.mfds.go.kr/searchDrug?sort=&sortOrder=false&searchYn=true&ExcelRowdata=&page={page}"
        "&searchDivision=detail&itemName=&itemEngName=&entpName=&entpEngName=&ingrName1=&ingrName2=&ingrName3="
        "&ingrEngName=&itemSeq=&stdrCodeName=&atcCodeName="
        f"&indutyClassCode={induty_class_code}"
        "&sClassNo=&narcoticKindCode=&cancelCode=&etcOtcCode=&makeMaterialGb=&searchConEe=AND&eeDocData="
        "&searchConUd=AND&udDocData=&searchConNb=AND&nbDocData="
        f"&startPermitDate={start_date}&endPermitDate={end_date}"
    )


MFDS_SOURCES = [
    {"label": "국내 생물의약품 허가", "induty_class_code": "C0"},
    {"label": "국내 첨단바이오의약품 허가", "induty_class_code": "J0"},
]


def strip_label_prefix(text, label):
    """모바일 반응형 테이블에서 셀 안에 숨겨진 컬럼명 라벨이 값 앞에 같이 붙어 나오는 경우
    (예: '제품명BMS수출용...') 그 라벨 접두어만 제거한다."""
    text = (text or "").strip()
    if label and text.startswith(label):
        return text[len(label):].strip()
    return text


NO_DATA_MARKERS = [
    "there is no data", "no data", "조회된 데이터가 없습니다", "검색결과가 없습니다", "데이터가 없습니다",
]


def parse_mfds_page(soup, label, page_url):
    """MFDS 검색 결과 페이지 하나에서 (제품명, 허가일, 링크) 목록을 뽑아낸다."""
    tables = soup.find_all("table")
    if not tables:
        return None  # 테이블 자체가 없음 (구조 문제 or 마지막 페이지)

    target = max(tables, key=lambda t: len(t.find_all("tr")))

    header_row = target.find("tr")
    header_cells = header_row.find_all(["th", "td"]) if header_row else []
    headers = [h.get_text(strip=True) for h in header_cells]

    def find_idx(keyword):
        for i, h in enumerate(headers):
            if keyword in h:
                return i
        return None

    idx_name = find_idx("제품명")
    idx_date = find_idx("허가일")

    items = []
    for row in target.find_all("tr")[1:]:
        cells = row.find_all("td")
        if not cells:
            continue

        row_text_lower = row.get_text(strip=True).lower()
        if any(marker in row_text_lower for marker in NO_DATA_MARKERS):
            continue  # "There is no data" 같은 결과없음 안내 행은 제품이 아니므로 건너뜀

        if idx_name is not None and idx_name < len(cells):
            title = strip_label_prefix(cells[idx_name].get_text(strip=True), "제품명")
        else:
            title = strip_label_prefix(cells[0].get_text(strip=True), "제품명") if cells else ""

        if not title:
            continue

        date_text = ""
        if idx_date is not None and idx_date < len(cells):
            date_text = normalize_date(strip_label_prefix(cells[idx_date].get_text(strip=True), "허가일"))

        link_tag = row.find("a", href=True)
        href = link_tag["href"] if link_tag else None
        if href and href.startswith("http"):
            full_link = href
        elif href:
            full_link = "https://nedrug.mfds.go.kr" + (href if href.startswith("/") else "/" + href)
        else:
            full_link = page_url

        items.append({"title": title, "link": full_link, "source": label, "date": date_text})

    return items


def scrape_mfds(source, limit=10, max_pages=5):
    """사이트 기본 정렬이 날짜순이 아니고, 날짜 필터(startPermitDate)가 실제로 먹는지도
    불확실하므로, 여러 페이지(기본 5페이지)를 모아 후보군을 늘린 뒤 허가일 기준으로
    다시 정렬해서 최신 것만 추린다."""
    label = source["label"]
    induty_class_code = source["induty_class_code"]

    all_items = []
    seen_titles = set()
    for page in range(1, max_pages + 1):
        url = build_mfds_url(induty_class_code, page=page)
        try:
            resp = fetch(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"[WARN] {label} (page {page}) 요청 실패: {e}")
            break

        page_items = parse_mfds_page(soup, label, url)
        if page_items is None:
            print(f"[WARN] {label} (page {page}): 테이블을 찾지 못함 (페이지 구조 확인 필요)")
            break
        if not page_items:
            break  # 더 이상 결과 없음 (마지막 페이지 도달)

        new_count = 0
        for item in page_items:
            if item["title"] in seen_titles:
                continue
            seen_titles.add(item["title"])
            all_items.append(item)
            new_count += 1

        if new_count == 0:
            break  # 페이지를 넘겨도 새 항목이 없으면 그만 (페이지네이션 미지원 등)

        time.sleep(1.5)  # 페이지 연속 요청으로 서버가 연결을 끊는 것을 방지

    # 허가일 내림차순 정렬 (날짜 파싱 실패/공란은 맨 뒤로) 후 최신 limit건만 사용
    all_items.sort(key=lambda x: x["date"] or "0000-00-00", reverse=True)
    items = all_items[:limit]

    print(f"[INFO] {label}: 총 {len(all_items)}건 후보 중 최신 {len(items)}건 선택 (허가일 기준 정렬)")
    return items


# -----------------------------
# 5. EU 의약품 신규 허가 - Community Register (Playwright 필요)
# -----------------------------
# 이 페이지는 자바스크립트로 데이터를 비동기로 불러와 그리는 방식이라(DataTables류),
# requests만으로는 빈 껍데기만 받아진다(실제 브라우저에서는 정상적으로 표가 보임).
# 실제 브라우저처럼 렌더링해야 하므로 Playwright(헤드리스 Chromium)를 사용한다.
#
# "Decision type" 컬럼이 정확히 "Centralised - Authorisation"인 행만 추려서
# 신규 허가만 가져온다 (Variation·Renewal·Withdrawal 등은 제외).

EU_SOURCE = {
    "label": "EU 신규 허가 (Community Register)",
    "url": "https://ec.europa.eu/health/documents/community-register/html/reg_last.htm",
}


def scrape_eu_playwright(limit=20):
    label = EU_SOURCE["label"]
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(f"[WARN] {label}: playwright가 설치되어 있지 않아 건너뜁니다 "
              f"(pip install playwright && playwright install --with-deps chromium 필요)")
        return []

    items = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(EU_SOURCE["url"], timeout=30000, wait_until="networkidle")
            page.wait_for_selector("table tr", timeout=15000)

            # 헤더 텍스트로 컬럼 위치를 동적으로 찾는다 (사이트 구조가 조금 바뀌어도 안전하도록)
            header_cells = page.query_selector_all("table thead tr th") or page.query_selector_all("table tr:first-of-type th")
            headers = [h.inner_text().strip().lower() for h in header_cells]

            def find_idx(keyword):
                for i, h in enumerate(headers):
                    if keyword in h:
                        return i
                return None

            idx_product = find_idx("product")
            idx_type = find_idx("decision type")
            idx_date = find_idx("decision date")

            rows = page.query_selector_all("table tbody tr") or page.query_selector_all("table tr")
            for row in rows:
                cells = row.query_selector_all("td")
                if not cells or idx_type is None or idx_type >= len(cells):
                    continue
                decision_type = cells[idx_type].inner_text().strip()
                if decision_type != "Centralised - Authorisation":
                    continue  # 신규 허가만 (Variation/Renewal/Withdrawal 등 제외)

                product_cell = cells[idx_product] if idx_product is not None and idx_product < len(cells) else cells[0]
                link_el = product_cell.query_selector("a")
                title = (link_el.inner_text() if link_el else product_cell.inner_text()).strip()
                if not title:
                    continue
                href = link_el.get_attribute("href") if link_el else None
                full_link = href if (href and href.startswith("http")) else EU_SOURCE["url"]
                date_text = cells[idx_date].inner_text().strip() if idx_date is not None and idx_date < len(cells) else ""

                items.append({"date": normalize_date(date_text), "title": title, "link": full_link, "source": label})
                if len(items) >= limit:
                    break

            browser.close()
    except Exception as e:
        print(f"[WARN] {label} 수집 실패: {e}")
        return []

    print(f"[INFO] {label}: {len(items)}건 수집 (Centralised - Authorisation 필터)")
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
    all_items.extend(scrape_eu_playwright())

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


# 항상 표시할 6개 소제목 (수집 성공 여부와 무관하게 항상 나오고, 각각 바로가기 링크를 가진다)
APPROVAL_SOURCE_META = [
    {"key": "FDA CBER (생물학적제제)", "home_url": "https://www.fda.gov/vaccines-blood-biologics/news-events-biologics/whats-new-biologics"},
    {"key": "FDA CDER", "home_url": "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=reportsSearch.process"},
    {"key": "EU 신규 허가 (Community Register)", "home_url": EU_SOURCE["url"]},
    {"key": "국내 생물의약품 허가", "home_url": build_mfds_url("C0", page=1)},
    {"key": "국내 첨단바이오의약품 허가", "home_url": build_mfds_url("J0", page=1)},
    {"key": "PMDA (일본)", "home_url": "https://www.pmda.go.jp/0017.html"},
]


def build_approval_panel_html(items, pmda_results):
    grouped = {}
    for item in items:
        grouped.setdefault(item["source"], []).append(item)

    parts = ['<div class="panel-header"><h2>✅ 허가 모니터링</h2></div>']

    for meta in APPROVAL_SOURCE_META:
        key = meta["key"]
        parts.append('<div class="src-block">')
        parts.append(
            f'<h3>{escape_html(key)} '
            f'<a class="quicklink-inline" href="{escape_html(meta["home_url"])}" target="_blank" rel="noopener">바로가기 →</a></h3>'
        )

        if key == "PMDA (일본)":
            if pmda_results:
                for r in pmda_results:
                    new_badge = ' <span class="new-badge">갱신됨</span>' if r.get("is_new") else ""
                    parts.append(
                        f'<div class="src-item"><a href="{escape_html(r["link"])}" target="_blank" '
                        f'rel="noopener">{escape_html(r["title"])}</a>{new_badge}'
                        f'<div class="quicklink-note">{escape_html(r["note"])}</div></div>'
                    )
            else:
                parts.append('<p class="empty">수집된 정보가 없습니다.</p>')
        else:
            src_items = grouped.get(key, [])
            if src_items:
                for item in src_items:
                    new_badge = ' <span class="new-badge">NEW</span>' if item.get("is_new") else ""
                    date_str = f' <span class="date-tag">{escape_html(item["date"])}</span>' if item.get("date") else ""
                    parts.append(
                        f'<div class="src-item"><a href="{escape_html(item["link"])}" target="_blank" '
                        f'rel="noopener">{escape_html(item["title"])}</a>{date_str}{new_badge}</div>'
                    )
            else:
                parts.append('<p class="empty">최근 수집된 항목이 없습니다.</p>')

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
