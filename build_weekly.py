"""
build_weekly.py
────────────────────────────────────────────────────────────────
일요일마다 실행. 지난 7일치 daily 파일을 모아서 주간 리포트 생성.

출력: data/weekly/YYYY-Www.json
구조:
  {
    "week": "2026-W16",
    "period": { "from": "2026-04-13", "to": "2026-04-19" },
    "generated_at": "...",
    "headline": "이번 주 핵심 한 줄",
    "insights": [ "…", "…", "…" ],   # 3~5개
    "by_category": {
      "image_video": [ item, ... (Top 5) ],
      "design_tool": [ ... ],
      ...
    },
    "all_items": [ ... ]  # 주간 전체, 정렬된
  }
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from anthropic import Anthropic  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT / "data" / "daily"
WEEKLY_DIR = ROOT / "data" / "weekly"

MODEL = os.environ.get("MODEL_WEEKLY", "claude-sonnet-4-6")


INSIGHT_PROMPT = """\
너는 크리에이티브 트렌드를 짚는 에디터다. 아래는 최근 일주일간 AI 영상·이미지 생성, 디자인 툴, AI 플랫폼,
영상 편집 분야에서 수집된 업데이트 카드 목록이다. 이걸 바탕으로 **디자인/영상 팀원 누구나** 이번 주 흐름을
빠르게 파악할 수 있게 다음 JSON 으로 정리해라. 설명·백틱 없이 순수 JSON만.

{
  "headline": "한 줄 요약 (50자 이내, 구어체. 예: '영상 생성 AI 3파전 본격화, Figma는 AI 디자인 에이전트 공개')",
  "insights": [
    "핵심 인사이트 문장 3~5개. 각 80자 이내. 어떤 제품/회사가 무엇을 했고 왜 중요한지 한 문장에 담을 것."
  ]
}

[입력: 이번 주 카드 목록 JSON]
%ITEMS%
"""


def load_weekly_items(from_date: datetime, to_date: datetime) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = from_date
    while cursor <= to_date:
        p = DAILY_DIR / f"{cursor.strftime('%Y-%m-%d')}.json"
        if p.exists():
            items.extend(json.loads(p.read_text(encoding="utf-8")))
        cursor += timedelta(days=1)
    # id 중복 제거
    seen: set[str] = set()
    dedup: list[dict[str, Any]] = []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        dedup.append(it)
    return sorted(dedup, key=lambda x: x.get("published_at", ""), reverse=True)


def group_by_category(items: list[dict[str, Any]], top_n: int = 5) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for it in items:
        grouped.setdefault(it["category"], []).append(it)
    # 각 카테고리에서 우선순위 높은 항목 + 최신순으로 Top N 뽑기
    for cat in grouped:
        grouped[cat] = sorted(
            grouped[cat],
            key=lambda x: (x.get("priority", 3), -parse_ts(x.get("published_at", ""))),
        )[:top_n]
    return grouped


def parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def generate_insights(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {"headline": "이번 주 수집된 업데이트가 없습니다.", "insights": []}

    # 토큰 절약을 위해 번역된 핵심 필드만 LLM 에 넘김
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
    prompt = INSIGHT_PROMPT.replace("%ITEMS%", json.dumps(trimmed, ensure_ascii=False))

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", resp.content[0].text.strip(), flags=re.MULTILINE).strip()
    return json.loads(raw)


def main() -> None:
    today = datetime.now(timezone.utc).date()
    # 지난 주(월~일)
    weekday = today.weekday()  # 월=0, 일=6
    last_sunday = today - timedelta(days=weekday + 1)
    last_monday = last_sunday - timedelta(days=6)
    iso_year, iso_week, _ = last_monday.isocalendar()
    week_id = f"{iso_year}-W{iso_week:02d}"

    print(f"[weekly] {week_id} ({last_monday} ~ {last_sunday})")

    items = load_weekly_items(
        datetime.combine(last_monday, datetime.min.time(), tzinfo=timezone.utc),
        datetime.combine(last_sunday, datetime.max.time(), tzinfo=timezone.utc),
    )
    print(f"  → 수집된 아이템 {len(items)}개")

    insights = generate_insights(items)
    grouped = group_by_category(items)

    payload = {
        "week": week_id,
        "period": {
            "from": last_monday.isoformat(),
            "to": last_sunday.isoformat(),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
        "headline": insights.get("headline", ""),
        "insights": insights.get("insights", []),
        "by_category": grouped,
        "all_items": items,
    }

    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    out = WEEKLY_DIR / f"{week_id}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 저장: {out}")


if __name__ == "__main__":
    main()
