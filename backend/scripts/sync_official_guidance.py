"""Fetch official Douyin guidance and propose reviewable skeleton-rule updates.

The script intentionally never edits the production script templates.  It only
writes a compact JSON snapshot with source metadata and candidate guidance for
manual review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "runtime" / "guidance" / "official-guidance.json"
DEFAULT_SOURCES = (
    "https://www.oceanengine.com/solution/food-drink",
)
USER_AGENT = (
    "FoodIPStudioGuidanceSync/1.0 "
    "(+https://github.com/bbm-gt/food-ip; official-guidance-review)"
)
OFFICIAL_DOMAIN_SUFFIXES = ("douyin.com", "oceanengine.com", "jinritemai.com")
BLOCK_TAGS = {
    "article",
    "br",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "li",
    "main",
    "p",
    "section",
}
IGNORED_TAGS = {"script", "style", "noscript", "svg"}
TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hook": ("开场", "钩子", "前3秒", "前 3 秒", "吸引注意", "抓住注意"),
    "identity": ("人设", "账号定位", "品牌人设", "企业号", "品牌形象"),
    "value": ("产品卖点", "核心价值", "内容种草", "优质内容", "品牌内容"),
    "retention": ("完播", "播放时长", "持续影响", "反复触达", "用户互动"),
    "conversion": ("到店", "团购", "转化", "行动引导", "固定店铺入口", "引流"),
    "production": ("竖屏", "分辨率", "声音", "字幕", "画面", "短视频"),
}


class VisibleTextParser(HTMLParser):
    """Extract visible text without bringing in a third-party HTML parser."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._parts: list[str] = []
        self._title_depth = 0
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.lower()
        if normalized in IGNORED_TAGS:
            self._ignored_depth += 1
        if normalized == "title":
            self._title_depth += 1
        if normalized in BLOCK_TAGS and not self._ignored_depth:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized == "title" and self._title_depth:
            self._title_depth -= 1
        if normalized in IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
        if normalized in BLOCK_TAGS and not self._ignored_depth:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self._parts.append(data)
        if self._title_depth:
            self._title_parts.append(data)

    @property
    def text(self) -> str:
        lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(self._parts).splitlines()]
        return "\n".join(line for line in lines if line)

    @property
    def title(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._title_parts)).strip()


def is_official_url(url: str) -> bool:
    """Return True only for HTTPS URLs owned by configured official domains."""

    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in OFFICIAL_DOMAIN_SUFFIXES
    )


def _robots_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))


def robots_allows(client: httpx.Client, url: str) -> bool:
    """Check robots.txt; a missing robots file means no published restriction."""

    response = client.get(_robots_url(url), headers={"User-Agent": USER_AGENT})
    if response.status_code == 404:
        return True
    response.raise_for_status()
    parser = RobotFileParser()
    parser.set_url(_robots_url(url))
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def extract_candidates(text: str, source_url: str, limit: int = 30) -> list[dict[str, Any]]:
    """Extract short, tagged guidance statements for human review."""

    sentences = re.split(r"(?<=[。！？!?])\s*|\n+", text)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_sentence in sentences:
        sentence = re.sub(r"\s+", " ", raw_sentence).strip(" -|\t")
        if not 12 <= len(sentence) <= 260 or sentence in seen:
            continue
        tags = [
            tag
            for tag, keywords in TAG_KEYWORDS.items()
            if any(keyword.lower() in sentence.lower() for keyword in keywords)
        ]
        if not tags:
            continue
        seen.add(sentence)
        candidate_id = hashlib.sha256(
            f"{source_url}\n{sentence}".encode("utf-8")
        ).hexdigest()[:16]
        candidates.append(
            {
                "id": candidate_id,
                "tags": tags,
                "statement": sentence,
                "source_url": source_url,
                "review_status": "pending",
            }
        )
        if len(candidates) >= limit:
            break
    return candidates


def fetch_official_page(
    client: httpx.Client, url: str, *, max_candidates: int = 30
) -> dict[str, Any]:
    """Fetch one allow-listed official page and return a compact snapshot."""

    if not is_official_url(url):
        raise ValueError(f"不是允许的抖音官方域名：https URL required: {url}")
    if not robots_allows(client, url):
        raise PermissionError(f"robots.txt 不允许抓取：{url}")

    response = client.get(url, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    final_url = str(response.url)
    if not is_official_url(final_url):
        raise ValueError(f"官方页面重定向到了非允许域名：{final_url}")
    content_type = response.headers.get("content-type", "").lower()
    if "html" not in content_type and "text" not in content_type:
        raise ValueError(f"不支持的内容类型：{content_type or 'unknown'}")

    parser = VisibleTextParser()
    parser.feed(response.text)
    text = parser.text
    if len(text) < 40:
        raise ValueError("页面正文过短，可能需要浏览器渲染或页面结构已变化")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "url": url,
        "final_url": final_url,
        "title": parser.title,
        "content_sha256": content_hash,
        "text_length": len(text),
        "etag": response.headers.get("etag"),
        "last_modified": response.headers.get("last-modified"),
        "candidates": extract_candidates(text, final_url, max_candidates),
    }


def load_previous(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def sync_guidance(
    client: httpx.Client,
    urls: list[str],
    *,
    previous: dict[str, Any] | None = None,
    max_candidates: int = 30,
    delay_seconds: float = 1.0,
) -> dict[str, Any]:
    """Fetch sources and annotate whether each official page changed."""

    previous_hashes = {
        item.get("url"): item.get("content_sha256")
        for item in (previous or {}).get("sources", [])
        if isinstance(item, dict)
    }
    sources: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for index, url in enumerate(dict.fromkeys(urls)):
        try:
            source = fetch_official_page(
                client, url, max_candidates=max_candidates
            )
            old_hash = previous_hashes.get(url)
            source["change_status"] = (
                "new"
                if old_hash is None
                else "unchanged"
                if old_hash == source["content_sha256"]
                else "changed"
            )
            sources.append(source)
        except (httpx.HTTPError, OSError, PermissionError, ValueError) as exc:
            errors.append({"url": url, "message": str(exc)})
        if index < len(urls) - 1 and delay_seconds > 0:
            time.sleep(delay_seconds)

    candidates = [candidate for source in sources for candidate in source["candidates"]]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "review_required": True,
        "auto_apply": False,
        "sources": sources,
        "candidate_rules": candidates,
        "errors": errors,
    }


def write_snapshot(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抓取抖音/巨量引擎官方说明，输出待人工审核的骨架规则候选。"
    )
    parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        help="要抓取的官方 HTTPS 页面，可重复传入；省略则使用内置来源。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"JSON 输出路径（默认：{DEFAULT_OUTPUT}）。",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=30,
        help="每个页面最多保留多少条规则候选（默认：30）。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.max_candidates <= 100:
        raise SystemExit("--max-candidates 必须在 1 到 100 之间")
    urls = args.urls or list(DEFAULT_SOURCES)
    previous = load_previous(args.output)
    with httpx.Client(
        timeout=httpx.Timeout(20.0),
        follow_redirects=True,
        trust_env=False,
    ) as client:
        payload = sync_guidance(
            client,
            urls,
            previous=previous,
            max_candidates=args.max_candidates,
        )
    write_snapshot(args.output, payload)
    print(
        f"已写入 {args.output}："
        f"成功 {len(payload['sources'])} 个来源，"
        f"候选 {len(payload['candidate_rules'])} 条，"
        f"失败 {len(payload['errors'])} 个。"
    )
    return 0 if payload["sources"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
