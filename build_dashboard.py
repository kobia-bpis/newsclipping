#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
바이오의약품 대시보드 - 마스터 빌드 스크립트
================================================
clip_news.py(뉴스클리핑) + report_monitor.py(보고서 모니터링) +
approval_monitor.py(허가 모니터링)를 한 페이지에 2단 구성으로 결합한다.

레이아웃: 왼쪽 = 뉴스클리핑 (넓게), 오른쪽 = 보고서 모니터링 + 허가 모니터링 (아래로 쌓임)

실행:
    python3 build_dashboard.py

GitHub Actions에서는 이 스크립트를 실행 진입점으로 사용한다 (clip_news.py 직접 실행 대신).
"""

import os
from datetime import datetime

import clip_news
import report_monitor
import approval_monitor

DOCS_DIR = "docs"
HTML_ARCHIVE_DIR = os.path.join(DOCS_DIR, "archive")

DASHBOARD_STYLE = """
<style>
  :root { --accent:#2563eb; --bg:#f7f8fa; --card:#ffffff; --text:#1f2430; --muted:#6b7280; --border:#e5e7eb; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic",sans-serif;
         background:var(--bg); color:var(--text); line-height:1.55; }
  .page-header { max-width:1400px; margin:0 auto; padding:28px 20px 0; }
  .page-header h1 { font-size:22px; margin:0 0 6px; }
  .page-header .meta { color:var(--muted); font-size:13px; }
  .page-header .meta a { color:var(--accent); text-decoration:none; }
  .dashboard { max-width:1400px; margin:0 auto; padding:20px 20px 80px;
               display:grid; grid-template-columns: 1.5fr 1fr; gap:20px; align-items:start; }
  @media (max-width: 900px) { .dashboard { grid-template-columns: 1fr; } }
  .col { display:flex; flex-direction:column; gap:20px; min-width:0; }

  /* 공통 카드 스타일 (뉴스/보고서/허가 패널 모두 공유) */
  .group, .side-panel { background:var(--card); border:1px solid var(--border); border-radius:10px;
           padding:16px 18px; margin-bottom:16px; }
  .side-panel { margin-bottom:0; }
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
  .empty { color:var(--muted); font-size:13px; }
  .panel-header { display:flex; justify-content:space-between; align-items:baseline; margin:0 0 14px; }
  .panel-header h2 { font-size:18px; margin:0; }
  .panel-count { color:var(--muted); font-size:13px; }

  /* 보고서/허가 패널 전용 */
  .src-block { margin-bottom:16px; }
  .src-block:last-child { margin-bottom:0; }
  .src-block h3 { font-size:13px; color:var(--muted); margin:0 0 8px; text-transform:uppercase;
                  letter-spacing:.02em; border-bottom:1px solid var(--border); padding-bottom:6px;
                  display:flex; justify-content:space-between; align-items:center; gap:8px; }
  .src-item { padding:6px 0; font-size:13.5px; }
  .src-item a { color:var(--text); text-decoration:none; font-weight:500; }
  .src-item a:hover { color:var(--accent); }
  .new-badge { display:inline-block; background:#fee2e2; color:#dc2626; font-size:10px; font-weight:700;
               border-radius:4px; padding:1px 5px; margin-left:5px; vertical-align:middle; }
  .date-tag { color:var(--muted); font-size:11.5px; margin-left:4px; }
  .quicklink-note { color:var(--muted); font-size:11.5px; margin-top:2px; }
  .quicklink-inline { color:var(--accent); font-size:11px; font-weight:600; text-transform:none;
                      letter-spacing:normal; text-decoration:none; white-space:nowrap; }
  .quicklink-inline:hover { text-decoration:underline; }

  footer { margin-top:32px; color:var(--muted); font-size:12px; text-align:center; }
  footer a { color:var(--accent); }
</style>
"""


def build_dashboard_html(news_articles, report_items, approval_items, pmda_results):
    today_str = datetime.now().strftime("%Y-%m-%d")

    news_panel = clip_news.build_news_panel_html(news_articles)
    report_panel = report_monitor.build_report_panel_html(report_items)
    approval_panel = approval_monitor.build_approval_panel_html(approval_items, pmda_results)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>바이오의약품 대시보드 - {today_str}</title>
{DASHBOARD_STYLE}
</head>
<body>
  <div class="page-header">
    <h1>바이오의약품 대시보드</h1>
    <div class="meta">{today_str} 업데이트 · <a href="archive/">지난 뉴스클리핑 다이제스트 보기</a></div>
  </div>
  <div class="dashboard">
    <div class="col">
      {news_panel}
    </div>
    <div class="col">
      <div class="side-panel">
        {report_panel}
      </div>
      <div class="side-panel">
        {approval_panel}
      </div>
    </div>
  </div>
  <footer>매일 자동 수집 · 뉴스: Google News RSS · 보고서/허가: 각 기관 공식 페이지</footer>
</body>
</html>"""
    return html


def save_dashboard(html_text):
    os.makedirs(HTML_ARCHIVE_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")

    index_path = os.path.join(DOCS_DIR, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_text)

    # 뉴스클리핑 부분은 기존처럼 아카이브에도 보관 (보고서/허가는 매일 갱신되는 상태 스냅샷 성격이라 별도 아카이브 없음)
    archive_path = os.path.join(HTML_ARCHIVE_DIR, f"digest_{today}.html")
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write(html_text)

    clip_news.build_archive_index()

    nojekyll_path = os.path.join(DOCS_DIR, ".nojekyll")
    if not os.path.exists(nojekyll_path):
        open(nojekyll_path, "w").close()

    print(f"[INFO] 대시보드 저장: {index_path} (아카이브: {archive_path})")


def main():
    print("=" * 60)
    print("[1/3] 뉴스클리핑 수집")
    print("=" * 60)
    news_articles = clip_news.collect_all()
    print(f"[INFO] 수집 완료: {len(news_articles)}건")

    if clip_news.ENABLE_TITLE_TRANSLATION:
        print("[INFO] 제목 번역 중...")
        news_articles = clip_news.enrich_with_translations(news_articles)

    print("[INFO] 유사 중복 기사 제거 중...")
    news_articles = clip_news.dedup_similar_articles(news_articles)
    print(f"[INFO] 최종 뉴스 기사 수: {len(news_articles)}건")

    if clip_news.ENABLE_SUMMARY:
        news_articles = clip_news.enrich_with_summaries(news_articles)

    # 뉴스클리핑 마크다운은 기존 방식대로 별도 보관
    md_text = clip_news.build_markdown(news_articles)
    clip_news.save_markdown(md_text)

    print()
    print("=" * 60)
    print("[2/3] 보고서 모니터링 수집")
    print("=" * 60)
    report_items = report_monitor.collect_reports()
    print(f"[INFO] 총 {len(report_items)}건 수집")

    print()
    print("=" * 60)
    print("[3/3] 허가 모니터링 수집")
    print("=" * 60)
    approval_items, pmda_results = approval_monitor.collect_approvals()
    print(f"[INFO] 총 {len(approval_items)}건 수집 (PMDA {len(pmda_results)}건 별도)")

    print()
    print("[INFO] 대시보드 페이지 생성 중...")
    html_text = build_dashboard_html(news_articles, report_items, approval_items, pmda_results)
    save_dashboard(html_text)

    # 선택 기능: 뉴스클리핑 전용 이메일/슬랙 전송 (기존과 동일하게 유지)
    clip_news.send_email(md_text)
    clip_news.send_slack(md_text)

    print("[INFO] 전체 완료")


if __name__ == "__main__":
    main()
