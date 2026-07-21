# 바이오의약품 뉴스클리핑 자동화

Google News RSS 기반으로 키워드별 최신 기사를 매일 수집해 마크다운 다이제스트와
**스타일이 적용된 웹페이지**를 만드는 스크립트입니다. GitHub Pages로 호스팅하면
매일 정해진 URL 하나만 즐겨찾기 해두고 접속해서 확인하면 됩니다 (이메일/SMTP 설정 불필요).
필요하면 Slack/이메일 전송도 선택적으로 켤 수 있습니다.

## 1. 설치

기본 사용(번역 포함, API 키 불필요):

```bash
pip install feedparser requests python-dateutil --break-system-packages
```

- 제목 번역: 원문 제목을 한국어로 번역 (Google Translate 무료 엔드포인트를 직접 호출, API 키 불필요, 6초 타임아웃 적용)
  - 비공식 엔드포인트라 완전히 안정적이진 않습니다. 번역 실패 시 자동으로 원문 제목만 표시되고
    전체 실행은 계속 진행됩니다.
  - 대량으로 자주 호출하면 일시적으로 막힐 수 있으니, 하루 1회 정도의 사용량이면 문제없습니다.

요약 기능은 기본적으로 꺼져 있습니다 (Anthropic API 키가 없으면 사용 안 함).
나중에 API 키가 생기면 아래처럼 켤 수 있습니다.

```bash
pip install trafilatura anthropic --break-system-packages
export ANTHROPIC_API_KEY=sk-ant-xxxxxxxx
export ENABLE_SUMMARY=1
```

## 2. 다국어 수집 범위

현재 4개 지역을 동시에 검색합니다: 미국(en-US), 한국(ko-KR), 일본(ja-JP), 중국(zh-CN, 간체).
일본/중국 기사는 PMDA·CDE 관련 규제 뉴스나 현지 CDMO/바이오텍 동향을 원문으로 잡기 위한 것입니다.

**제목 번역**: 한국어가 아닌 기사는 원문 제목 아래에 "🇰🇷 번역: ..." 형태로 한국어 번역 제목이
자동으로 함께 표시됩니다(요약과 무관하게 항상 동작). 끄고 싶으면:

```bash
export ENABLE_TITLE_TRANSLATION=0
```

지역을 더 추가하고 싶다면 `LANG_REGIONS`에 `{"hl": "...", "gl": "...", "ceid": "..."}` 형태로 넣으면 됩니다.
(예: 유럽 EMA 동향이 필요하면 `en-GB`/`GB` 또는 `de`/`DE` 추가)

**실행 속도 참고**: RSS 수집(최대 17키워드 × 6지역 ≈ 100여 회)과 제목 번역(기사별 병렬 처리, 최대 8개 동시)에는
각각 타임아웃이 걸려 있어(RSS 10초, 번역 6초) 특정 요청이 응답 없이 멈춰도 전체가 무한정 늘어지지 않습니다.
기사 수·지역 수에 따라 보통 2~5분 정도 소요됩니다.

## 3. 요약 기능 (선택, API 키 있을 때만)

요약은 기본 꺼져 있습니다. `ENABLE_SUMMARY=1`로 켜면 그룹당 최신 상위 N건만 본문을 가져와
Claude Haiku로 3줄 요약을 생성합니다(비용 관리를 위한 제한이며, 요약을 안 쓰면 이 제한은
적용되지 않고 수집된 기사 전체가 다이제스트에 표시됩니다).

```bash
export SUMMARIZE_TOP_N_PER_GROUP=8   # 그룹당 요약 개수 조정
export ANTHROPIC_MODEL=claude-haiku-4-5-20251001  # 필요시 다른 모델로 변경
```

## 4. 키워드 수정

`clip_news.py` 상단의 `KEYWORD_GROUPS` 딕셔너리에서 그룹/키워드를 자유롭게 추가·삭제하세요.
현재 기본 세팅:

- CDMO / 위탁생산
- Biologics, Biopharmaceuticals, recombinant DNA technology
- CAR-T, CGT, AAV
- 항체 계열(-mab), GLP-1(glutide 계열)
- 백신, 보툴리눔 톡신
- 규제기관: FDA(IND 포함), PMDA, CDE, MFDS, HHS

> 참고: 검색 정확도를 위해 `IND FDA`, `FDA approval biologics`처럼 2단어 조합으로 넣었습니다.
> `IND`, `Abs`, `mab` 처럼 너무 짧거나 일반적인 단어는 노이즈가 많으니 조합형 검색어를 권장합니다.


## 5. 실행

```bash
python3 clip_news.py
```

실행하면 다음 두 가지가 생성됩니다.

- `digests/digest_YYYYMMDD.md` — 마크다운 원본 (git 기록용)
- `docs/index.html` — **오늘의 다이제스트 웹페이지** (그대로 GitHub Pages로 호스팅)
- `docs/archive/digest_YYYYMMDD.html` — 지난 다이제스트 아카이브, `docs/archive/index.html`에서 목록 확인 가능

로컬에서 결과를 바로 보고 싶으면 `docs/index.html` 파일을 더블클릭해서 브라우저로 열면 됩니다.

## 6. 매일 자동 실행 + 웹페이지로 확인 — GitHub Pages (추천, 무료)

가장 간단한 구성입니다: GitHub Actions가 매일 새벽에 스크립트를 실행해서 `docs/` 폴더를
레포에 커밋하면, GitHub Pages가 그 폴더를 자동으로 웹사이트로 서빙합니다.

### 6-1. 리포지토리 준비

1. GitHub에 새 저장소를 만들고 (private도 가능) 이 폴더의 파일들을 올립니다.
2. 저장소 **Settings → Pages** 로 이동합니다.
3. **Source**를 "Deploy from a branch"로, **Branch**를 `main` / 폴더는 `/docs`로 지정하고 저장합니다.
4. 잠시 후 `https://[깃허브아이디].github.io/[저장소이름]/` 주소가 활성화됩니다 — 이 주소를 즐겨찾기 하세요.

### 6-2. 매일 자동 실행 워크플로우

`.github/workflows/daily-clip.yml` 파일을 추가합니다:

```yaml
name: Daily Bio News Clipping

on:
  schedule:
    - cron: '0 23 * * *'   # UTC 23:00 = 한국시간 08:00
  workflow_dispatch: {}     # 수동 실행 버튼도 활성화

jobs:
  clip:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install feedparser requests python-dateutil
      - run: python3 clip_news.py
      - name: Commit digest & webpage to repo
        run: |
          git config user.name "news-bot"
          git config user.email "bot@example.com"
          git add digests/ docs/
          git commit -m "Daily digest $(date +%Y-%m-%d)" || echo "no changes"
          git push
```

- `workflow_dispatch`가 있어서 Actions 탭에서 "Run workflow" 버튼으로 바로 테스트 실행도 가능합니다.
- 매일 커밋되면 GitHub Pages가 몇 분 내로 자동 갱신되고, 즐겨찾기한 주소에서 항상 "오늘 것"이 보입니다.
- Repository가 Private이어도 GitHub Pages는 (개인/Pro 계정 기준) 그대로 동작합니다. 다만 Pages로 배포된 페이지 자체는 공개 URL이라는 점은 참고하세요 — 민감한 내용이 없다면 문제 없습니다.

## 7. (선택) 이메일/Slack도 함께 받고 싶다면

웹페이지 확인이 기본이지만, 필요하면 이메일·Slack 전송도 추가로 켤 수 있습니다.
`SMTP_*` 또는 `SLACK_WEBHOOK_URL` 환경변수를 설정하면 자동으로 함께 전송됩니다 (설정 안 하면 조용히 건너뜁니다).

**메일플러그(kobia.kr) 사용 예시** — 포트는 465(SSL) 고정이며, 그룹웨어 로그인 비밀번호가 아니라
별도로 발급받는 "앱 비밀번호"를 써야 합니다 (`login.mailplug.com` → 환경설정 → 앱 비밀번호):

```bash
export SMTP_HOST=smtp.mailplug.co.kr
export SMTP_PORT=465
export SMTP_USER=sohk@kobia.kr
export SMTP_PASS=발급받은_앱비밀번호
export MAIL_TO=sohk@kobia.kr
```

Gmail을 쓰는 경우 (포트 587, STARTTLS):

```bash
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=your_email@gmail.com
export SMTP_PASS=앱비밀번호
export MAIL_TO=받을주소@company.com
```

스크립트는 포트가 465면 자동으로 SSL 방식, 그 외(587 등)는 STARTTLS 방식으로 접속합니다.

GitHub Actions에서 이메일/Slack까지 함께 쓰려면 워크플로우의 `env`에 아래를 추가하세요
(Settings → Secrets and variables → Actions 에 값 등록 필요):

```yaml
      - run: python3 clip_news.py
        env:
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}
          MAIL_TO: ${{ secrets.MAIL_TO }}
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
```

## 8. 매일 자동 실행 — 방법 B: 로컬/서버 cron (Linux/Mac)

GitHub 대신 개인 서버나 상시 켜진 PC에서 돌리고 싶다면:

```bash
crontab -e
```

아래 줄 추가 (매일 오전 8시 실행):

```
0 8 * * * cd /path/to/bio_news_clipper && /usr/bin/python3 clip_news.py >> run.log 2>&1
```

이 경우 `docs/index.html`을 로컬 웹서버(`python3 -m http.server` 등)로 열거나,
Dropbox/OneDrive 동기화 폴더에 두고 브라우저 즐겨찾기(`file:///...`)로 열어도 됩니다.

- 나중에 요약 기능을 켜려면 `ANTHROPIC_API_KEY` 환경변수(또는 GitHub Secret)를 추가하고,
  install 줄에 `trafilatura anthropic`을 더하고, `ENABLE_SUMMARY=1`을 설정하면 됩니다.

## 9. 고도화 아이디어

- **중요도 필터**: 특정 언론사(FiercePharma, Endpoints News, BioPharma Dive, 팜뉴스, 메디게이트 등)
  가중치를 줘서 상단에 노출.
- **경쟁사/파이프라인 트래킹**: 키워드에 특정 회사명·약물명을 추가해 개별 모니터링.
- **DB 적재**: Notion API/Google Sheets API로 다이제스트를 자동 기록해 검색 가능한 아카이브 구축.
- **번역 품질 개선**: 일본어/중국어 원문 제목도 한국어로 병기하고 싶다면 요약 프롬프트에
  "제목도 한국어로 번역해서 함께 출력" 지시를 추가하면 됩니다.
- **웹페이지 꾸미기**: `clip_news.py`의 `HTML_STYLE` 안 CSS 변수(`--accent`, `--bg` 등)만 바꿔도
  색상 테마를 손쉽게 바꿀 수 있습니다.
