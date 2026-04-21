# Creative Trend

AI 영상·이미지·디자인·편집 툴의 신기능과 기술 업데이트를 **매일 한국어로** 자동 정리하는 정적 웹앱.

크리에이티브 직무가 아닌 팀원도 바로 이해할 수 있게 구어체로 요약하고, 일일 → 주간 → 월간 리포트로 자동 누적합니다.

## 구조

```
creative-trend/
├── index.html              # SPA (혼자서 작동, CDN 없음)
├── feed.xml                # (선택) RSS 재발행
├── data/
│   ├── latest.json         # 프론트가 첫 로드 때 읽는 파일
│   ├── daily/YYYY-MM-DD.json
│   ├── weekly/YYYY-Www.json
│   ├── monthly/YYYY-MM.json
│   └── seen.json           # 중복 수집 방지 (ID 해시 저장소)
├── scripts/
│   ├── sources.yaml                # RSS 소스 설정 ★
│   ├── crawl_and_translate.py      # 매일 실행
│   ├── build_weekly.py             # 주간 리포트
│   ├── build_monthly.py            # 월간 인사이트
│   └── requirements.txt
└── .github/workflows/
    ├── daily.yml           # 매일 08:00 KST
    ├── weekly.yml          # 매주 월요일 09:00 KST
    └── monthly.yml         # 매월 1일 09:00 KST
```

## 배포 (GitHub Pages + Actions)

### 1. 저장소 생성 후 파일 업로드

```bash
git init
git add .
git commit -m "init: creative-trend"
git branch -M main
git remote add origin git@github.com:YOUR_ORG/creative-trend.git
git push -u origin main
```

### 2. Secrets 설정

저장소 **Settings → Secrets and variables → Actions** 에서:

- Secrets:
  - `ANTHROPIC_API_KEY` — [console.anthropic.com](https://console.anthropic.com) 에서 발급
- (선택) Variables:
  - `MODEL_DAILY` (기본: `claude-haiku-4-5-20251001`)
  - `MODEL_WEEKLY` (기본: `claude-sonnet-4-6`)
  - `MODEL_MONTHLY` (기본: `claude-sonnet-4-6`)

### 3. GitHub Pages 활성화

**Settings → Pages → Source: Deploy from a branch → main / (root)**

커밋 후 몇 분 뒤 `https://YOUR_ORG.github.io/creative-trend/` 로 접근 가능.

### 4. 첫 실행

**Actions 탭 → Daily Crawl & Translate → Run workflow** 를 클릭해 수동 트리거.
이후 매일 08:00 KST 에 자동 실행되며 새 카드를 커밋합니다.

## 로컬에서 UI 미리보기

```bash
cd creative-trend
python -m http.server 8000
# 브라우저에서 http://localhost:8000
```

`data/latest.json` 에 데모 데이터가 들어 있어 바로 렌더링됩니다.

## 파이프라인을 로컬에서 테스트

```bash
pip install -r scripts/requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
python scripts/crawl_and_translate.py
```

실행 후 `data/daily/오늘날짜.json` 과 `data/latest.json` 이 갱신됩니다.

## RSS 소스 추가·수정

`scripts/sources.yaml` 을 편집합니다. 각 소스는 다음 스키마를 따릅니다.

```yaml
- id: figma_releases              # 영문 슬러그 (고유)
  display_name: Figma              # 카드에 노출될 출처명
  url: https://www.figma.com/release-notes/feed.xml
  category: design_tool            # image_video | design_tool | ai_platform | video_motion | audio | misc
  priority: 1                      # 1(핵심) | 2(일반) | 3(마이너)
  emoji: "🟣"                      # 카드 제목 앞 이모지
  tags: [figma, design]            # 선택: 검색 메타
  fallback_scrape: https://...     # (선택) RSS 없는 사이트용 스크래핑 대상 URL
```

**현재 목록**: 이미지·영상 9개, 디자인 툴 5개, AI 플랫폼 4개, 영상·모션 3개, 오디오 2개 — 총 23개.

RSS가 없는 소스(Midjourney 등)는 `fallback_scrape` 필드만 채워두면, 나중에 간단한 스크래퍼를 붙여서 추가 수집 가능합니다. 현재 파이프라인은 RSS만 처리합니다.

## 번역 스타일 커스터마이징

`scripts/crawl_and_translate.py` 상단의 `TRANSLATE_PROMPT` 를 수정하세요.

현재 프롬프트는 참고앱(ai-trend.hamsterapp.net) 톤을 기준으로:
- `title_ko` — 정확한 한국어 제목 (제품명은 원어 유지)
- `summary_ko` — 구어체 한 줄 ("~대", "~됨", "~짐")
- `tip_ko` — 실행 팁 (URL·버튼명·경로 포함)
- `tag` — 분류 (`model_release` / `feature_add` / `api_change` / `price_change` / `tool_launch` / `integration`)
- `relevance` — 디자인·영상 팀에 의미 있는 업데이트인지 여부 (버그픽스·채용 공고는 자동 배제)

## 주요 동작

### 오늘의 첫 화면 규칙

- 기본 탭 `Today` 는 **당일(`published_at` === 오늘) 카드만** 렌더링합니다.
- 지난 일자 카드는 `This Week` 탭에서 확인 가능.
- 주간 리포트·월간 인사이트는 별도 탭.

### 일자 누락 방지

- `data/seen.json` 이 중복 ID를 추적하므로 같은 기사가 이중 카운트되지 않습니다.
- 어제치 데이터가 누락된 상태에서 오늘 실행되어도 `daily/어제.json` 은 그대로 남아있고, `This Week` 탭이 지난 6일 일일 파일을 자동으로 합쳐 보여줍니다.
- 수동 백필: 누락 일자의 RSS를 파일로 내려받은 뒤 파이프라인을 재실행하면 해당 일자로 기록됩니다.

### 원문 링크

- 카드 제목과 "원문 ↗" 링크 모두 RSS `<link>` 필드의 값을 그대로 사용합니다. 가공·리다이렉트 없음.

### 무의미 숫자 제거

- 참고앱과 동일하게 points·댓글·조회수 필드를 수집하지 않습니다. 카드 UI에도 표시되지 않습니다.

## 라이선스 / 주의

- RSS 원문의 저작권은 각 매체에 있습니다. 본 프로젝트는 요약·번역·큐레이션 목적으로만 사용하세요.
- Claude API 사용료는 유료이며 호출 규모에 따라 과금됩니다. `MAX_ITEMS_PER_SOURCE` 로 상한 조절 가능.
