"""
build_monthly.py
────────────────────────────────────────────────────────────────
매월 1일에 전월(前月)을 대상으로 실행. 월간 인사이트 생성.

출력: data/monthly/YYYY-MM.json
구조:
  {
    "month": "2026-04",
    "period": { "from": "2026-04-01", "to": "2026-04-30" },
    "generated_at": "...",
    "theme": "이달의 한 줄 테마",
    "summary": "3~5줄 서술형 요약",
    "top_insights": [ "핵심 흐름 5개" ],
    "by_category_highlights": {
      "image_video": [ "카테고리별 핵심 업데이트 3~5개 요약 문장" ],
      ...
    },
    "stats": {
      "total_updates": 120,
      "by_category": { "image_video": 45, ... },
      "by_source": { "Runway": 8, ... }
    }
  }
"""

from __future__ import annotations

import json
import os
import re
from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from anthropic import Anthropic  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT / "data" / "daily"
MONTHLY_DIR = ROOT / "data" / "monthly"

MODEL = os.environ.get("MODEL_MONTHLY", "claude-sonnet-4-6")


MONTHLY_PROMPT = """\
너는 크리에이티브·AI 콘텐츠 업계를 월 단위로 정리하는 시니어 에디터다. 아래는 지난 한 달간 수집된 업데이트 카드 전체다.
디자인·영상·콘텐츠 제작 팀이 "이번 달 뭐가 중요했지?" 를 30초 안에 파악할 수 있게 다음 JSON 으로 정리해라.
설명·백틱·코드펜스 없이 순수 JSON만 반환.

{
  "theme": "이달의 한 줄 테마 (60자 이내)",
  "summary": "서술형 3~5문장. 전체 흐름과 맥락을 에디토리얼 톤으로.",
  "top_insights": [
    "핵심 흐름·변곡점 5개. 각 100자 이내."
  ],
  "by_category_highlights": {
    "image_video": ["카테고리별 주요 업데이트 3~5개 요약. 각 80자 이내."],
    "design_tool": [...],
    "ai_platform": [...],
    "video_motion": [...]
  }
}

[입력: 이번 달 카드 목록]
%ITEMS%
"""


def load_month_items(year: int, month: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    last_day = monthrange(year, month)[1]
    d = date(year, month, 1)
    while d <= date(year, month, last_day):
        p = DAILY_DIR / f"{d.isoformat()}.json"
        if p.exists():
            items.extend(json.loads(p.read_text(encoding="utf-8")))
        d += timedelta(days=1)
    seen: set[str] = set()
    dedup: list[dict[str, Any]] = []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        dedup.append(it)
    return dedup


def compute_stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total_updates": len(items),
        "by_category": dict(Counter(it["category"] for it in items)),
        "by_source": dict(Counter(it["source_name"] for it in items).most_common(15)),
    }


def llm_summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {
            "theme": "이번 달 수집된 업데이트가 없습니다.",
            "summary": "",
            "top_insights": [],
            "by_category_highlights": {},
        }

    trimmed = [
        {
            "source": it["source_name"],
            "category": it["category"],
            "title_ko": it["title_ko"],
            "summary_ko": it["summary_ko"],
            "tag": it.get("tag"),
            "published_at": it["published_at"][:10],
        }
        for it in items
    ]

    # 너무 길면 LLM 입력 초과. 대략 200개 이상이면 카테고리별로 균형 샘플링.
    if len(trimmed) > 200:
        by_cat: dict[str, list] = {}
        for it in trimmed:
            by_cat.setdefault(it["category"], []).append(it)
        sampled: list[dict[str, Any]] = []
        per_cat = max(20, 200 // max(len(by_cat), 1))
        for cat_items in by_cat.values():
            sampled.extend(cat_items[:per_cat])
        trimmed = sampled

    prompt = MONTHLY_PROMPT.replace("%ITEMS%", json.dumps(trimmed, ensure_ascii=False))
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", resp.content[0].text.strip(), flags=re.MULTILINE).strip()
    return json.loads(raw)


def main() -> None:
    # 전월을 대상으로 실행 (이번 달 1일에 크론이 돈다는 가정)
    today = datetime.now(timezone.utc).date()
    first_of_this_month = today.replace(day=1)
    last_of_prev = first_of_this_month - timedelta(days=1)
    year, month = last_of_prev.year, last_of_prev.month
    month_id = f"{year:04d}-{month:02d}"

    print(f"[monthly] 대상: {month_id}")

    items = load_month_items(year, month)
    print(f"  → 수집된 아이템 {len(items)}개")

    llm = llm_summarize(items)
    stats = compute_stats(items)

    payload = {
        "month": month_id,
        "period": {
            "from": date(year, month, 1).isoformat(),
            "to": last_of_prev.isoformat(),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "theme": llm.get("theme", ""),
        "summary": llm.get("summary", ""),
        "top_insights": llm.get("top_insights", []),
        "by_category_highlights": llm.get("by_category_highlights", {}),
        "stats": stats,
    }

    MONTHLY_DIR.mkdir(parents=True, exist_ok=True)
    out = MONTHLY_DIR / f"{month_id}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 저장: {out}")


if __name__ == "__main__":
    main()
