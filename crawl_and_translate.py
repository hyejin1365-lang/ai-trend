"""
crawl_and_translate.py
────────────────────────────────────────────────────────────────
하루에 한 번(GitHub Actions) 실행되는 메인 파이프라인.

동작 순서:
  1. sources.yaml 의 RSS 피드를 모두 파싱
  2. 과거 수집분(data/seen.json)과 대조해 새 항목만 추림
  3. Claude API 로 (제목 번역 + 쉬운말 요약 + 실행 팁 + 분류 태그) 동시 생성
  4. data/daily/YYYY-MM-DD.json 에 저장
  5. data/latest.json (프론트엔드가 첫 로드 때 읽는 파일) 갱신
  6. seen.json 업데이트

환경변수:
  ANTHROPIC_API_KEY   : Claude API 키 (필수)
  MODEL_DAILY         : 기본값 "claude-haiku-4-5-20251001"
  MAX_ITEMS_PER_SOURCE: 기본값 5 (피드 한 개당 최근 N개만)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser  # type: ignore
import yaml  # type: ignore
from anthropic import Anthropic  # type: ignore

# ────────────────────────────────────────────────────────────────
# 경로 상수
# ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
SOURCES_YAML = Path(__file__).resolve().parent / "sources.yaml"
DATA_DIR = ROOT / "data"
DAILY_DIR = DATA_DIR / "daily"
SEEN_FILE = DATA_DIR / "seen.json"
LATEST_FILE = DATA_DIR / "latest.json"

MODEL = os.environ.get("MODEL_DAILY", "claude-haiku-4-5-20251001")
MAX_ITEMS_PER_SOURCE = int(os.environ.get("MAX_ITEMS_PER_SOURCE", "5"))
# 과도한 오래된 항목 필터: 이 일수보다 오래된 건 새로 추가해도 무시
MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "3"))

# ────────────────────────────────────────────────────────────────
# 프롬프트 — 참고앱(ai-trend.hamsterapp.net) 번역 톤을 모사
# ────────────────────────────────────────────────────────────────
TRANSLATE_PROMPT = """\
너는 크리에이티브·디자인·AI 영상 분야의 업데이트 뉴스를 한국어로 정리하는 에디터다.
제공된 영문 기사를 다음 JSON 스키마로 정확히 반환해라. 설명·백틱·코드펜스 없이 순수 JSON만.

규칙:
1. title_ko: 원제의 핵심을 담은 정확한 한국어 번역. 제품명·고유명사는 원어 유지(Figma, Runway, Gemini 등).
2. summary_ko: 제작 직무가 아닌 팀원도 바로 이해할 수 있는 구어체 한 줄. "~대", "~됨", "~짐" 같은 친근한 어미 사용.
   - 금지: 딱딱한 번역체("~합니다", "~됩니다"), 영어 용어 남발, 과장(엄청·최고·혁명).
   - 20~45자 내외 권장.
3. tip_ko: 실무에서 어떻게 확인·사용하는지 한 줄. URL·설정 경로·버튼명이 있으면 포함.
   - 없으면 null.
4. tag: 아래 중 하나 (없으면 null):
   model_release (새 모델/버전) | feature_add (기능 추가) | api_change (API 변경) |
   price_change (가격 변경) | tool_launch (신규 도구 출시) | integration (다른 서비스와 연동)
5. relevance: 디자인·영상·콘텐츠 제작자에게 의미 있는 업데이트면 true, 마이너 버그픽스·내부 조직 개편·채용 공고 등은 false.

반환 JSON 스키마:
{
  "title_ko": "string",
  "summary_ko": "string",
  "tip_ko": "string | null",
  "tag": "string | null",
  "relevance": true | false
}

[원제]
%TITLE%

[원문 발췌 (처음 1500자)]
%BODY%
"""

# ────────────────────────────────────────────────────────────────
# 유틸
# ────────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def hash_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def strip_html(text: str) -> str:
    """RSS description 안의 HTML 태그 제거."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_seen() -> dict[str, str]:
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    return {}


def save_seen(seen: dict[str, str]) -> None:
    SEEN_FILE.write_text(
        json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_date(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = getattr(entry, key, None) or entry.get(key) if hasattr(entry, "get") else None
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


# ────────────────────────────────────────────────────────────────
# 1. RSS 피드 수집
# ────────────────────────────────────────────────────────────────
def collect_from_sources(sources_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """모든 피드에서 아직 못 본 최신 항목을 긁어 리스트로 반환."""
    seen = load_seen()
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - (MAX_AGE_DAYS * 86400)

    new_items: list[dict[str, Any]] = []

    for src in sources_cfg["sources"]:
        sid = src["id"]
        url = src["url"]
        log(f"  · fetching [{sid}] {url}")

        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": "creative-trend/1.0"})
        except Exception as e:  # noqa: BLE001
            log(f"    ✗ {sid} 피드 파싱 실패: {e}")
            continue

        if getattr(feed, "bozo", False) and not feed.entries:
            log(f"    ✗ {sid} 빈 피드 또는 오류 (bozo={feed.bozo_exception!r})")
            continue

        count = 0
        for entry in feed.entries[: MAX_ITEMS_PER_SOURCE * 3]:
            link = entry.get("link", "").strip()
            if not link:
                continue
            key = hash_id(link)
            if key in seen:
                continue

            published = parse_date(entry)
            if published and published.timestamp() < cutoff:
                continue

            title = (entry.get("title") or "").strip()
            body = strip_html(
                entry.get("summary")
                or entry.get("description")
                or (entry.get("content", [{}])[0].get("value") if entry.get("content") else "")
                or ""
            )[:1500]

            if not title:
                continue

            new_items.append({
                "id": key,
                "source_id": sid,
                "source_name": src["display_name"],
                "category": src["category"],
                "priority": src["priority"],
                "emoji": src["emoji"],
                "link": link,
                "title_en": title,
                "body_en": body,
                "published_at": (published or now).isoformat(),
            })
            count += 1
            if count >= MAX_ITEMS_PER_SOURCE:
                break

        log(f"    → {count}개 신규")

    log(f"총 신규 항목: {len(new_items)}개")
    return new_items


# ────────────────────────────────────────────────────────────────
# 2. Claude 로 번역·요약
# ────────────────────────────────────────────────────────────────
def translate_one(client: Anthropic, item: dict[str, Any]) -> dict[str, Any] | None:
    prompt = (
        TRANSLATE_PROMPT
        .replace("%TITLE%", item["title_en"])
        .replace("%BODY%", item["body_en"] or "(본문 없음)")
    )

    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
            # 혹시 코드펜스가 섞여 들어오면 제거
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
            parsed = json.loads(raw)

            if not parsed.get("relevance", True):
                return None  # 비관련 → 드롭

            return {
                **item,
                "title_ko": parsed["title_ko"],
                "summary_ko": parsed["summary_ko"],
                "tip_ko": parsed.get("tip_ko"),
                "tag": parsed.get("tag"),
            }
        except json.JSONDecodeError as e:
            log(f"    ! JSON 파싱 실패(시도 {attempt+1}): {e}")
        except Exception as e:  # noqa: BLE001
            log(f"    ! Claude 호출 실패(시도 {attempt+1}): {e}")
        time.sleep(2 ** attempt)

    log(f"    ✗ 번역 최종 실패: {item['title_en'][:60]}")
    return None


def translate_batch(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log("✗ ANTHROPIC_API_KEY 환경변수가 없습니다.")
        sys.exit(1)

    client = Anthropic(api_key=api_key)
    out: list[dict[str, Any]] = []
    for i, item in enumerate(items, 1):
        log(f"  [{i}/{len(items)}] 번역 중: {item['title_en'][:70]}")
        translated = translate_one(client, item)
        if translated:
            out.append(translated)
    log(f"번역 완료: {len(out)}/{len(items)}개 채택")
    return out


# ────────────────────────────────────────────────────────────────
# 3. 저장
# ────────────────────────────────────────────────────────────────
def save_daily(items: list[dict[str, Any]]) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_path = DAILY_DIR / f"{today}.json"
    DAILY_DIR.mkdir(parents=True, exist_ok=True)

    existing: list[dict[str, Any]] = []
    if daily_path.exists():
        existing = json.loads(daily_path.read_text(encoding="utf-8"))

    # id 기준 중복 제거
    by_id = {it["id"]: it for it in existing}
    for it in items:
        by_id[it["id"]] = it

    merged = sorted(
        by_id.values(),
        key=lambda x: x.get("published_at", ""),
        reverse=True,
    )
    daily_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"저장 완료: {daily_path} ({len(merged)}개)")
    return daily_path


def update_latest(daily_path: Path) -> None:
    """프론트가 기본 로드하는 latest.json 갱신 + 오늘 날짜 메타 삽입."""
    daily_items = json.loads(daily_path.read_text(encoding="utf-8"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "today": today,
        "count": len(daily_items),
        "items": daily_items,
    }
    LATEST_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"latest.json 갱신 ({len(daily_items)}개)")


def mark_seen(items: list[dict[str, Any]]) -> None:
    seen = load_seen()
    now = datetime.now(timezone.utc).isoformat()
    for it in items:
        seen[it["id"]] = now
    save_seen(seen)


# ────────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────────
def main() -> None:
    log("=== 크리에이티브 트렌드 수집 시작 ===")
    cfg = load_yaml(SOURCES_YAML)

    new_items = collect_from_sources(cfg)
    # 수집 단계에서 seen에 기록해두면 번역 실패하더라도 다음날 재시도 안 함.
    # 실패한 건 재시도하게 두려면 아래 mark_seen은 번역 이후로 옮긴다.
    if not new_items:
        log("신규 항목 없음. 종료.")
        # latest.json 은 그대로 둠
        return

    translated = translate_batch(new_items)
    mark_seen(new_items)  # 번역 실패한 것도 무한 재시도 막기 위해 기록

    if translated:
        path = save_daily(translated)
        update_latest(path)
    log("=== 완료 ===")


if __name__ == "__main__":
    main()
