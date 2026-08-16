#!/usr/bin/env python3
"""Generate dynamic project documents from docs/project-status.yaml."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
STATUS_PATH = REPOSITORY_ROOT / "docs" / "project-status.yaml"
OUTPUT_PATH = REPOSITORY_ROOT / "docs" / "next-tasks.md"


def require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a mapping")
    return value


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def require_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return value


def load_status() -> dict[str, Any]:
    try:
        raw_status = yaml.safe_load(STATUS_PATH.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"cannot read {STATUS_PATH.relative_to(REPOSITORY_ROOT)}: {error}") from error
    except yaml.YAMLError as error:
        raise ValueError(f"invalid YAML in {STATUS_PATH.relative_to(REPOSITORY_ROOT)}: {error}") from error

    status = require_mapping(raw_status, "project status")
    if status.get("format_version") != 1:
        raise ValueError("format_version must be 1")
    require_string(status.get("updated_at"), "updated_at")

    current = require_mapping(status.get("current"), "current")
    for field in ("area", "milestone", "status"):
        require_string(current.get(field), f"current.{field}")

    completed = require_string_list(status.get("completed"), "completed")

    next_step = require_mapping(status.get("next"), "next")
    require_string(next_step.get("status"), "next.status")
    require_string(next_step.get("objective"), "next.objective")
    if not isinstance(next_step.get("implementation_allowed"), bool):
        raise ValueError("next.implementation_allowed must be a boolean")

    deferred = require_string_list(status.get("deferred"), "deferred")
    return {
        "current": current,
        "completed": completed,
        "next": next_step,
        "deferred": deferred,
    }


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render(status: dict[str, Any]) -> str:
    current = status["current"]
    next_step = status["next"]
    coding_instruction = (
        "在获得用户确认前，不得直接开始下一阶段编码。"
        if not next_step["implementation_allowed"]
        else "可在既定范围内开始下一阶段实施。"
    )

    return f"""<!-- 此文件由 docs/project-status.yaml 自动生成，请勿手工编辑。 -->

# 项目状态与下一任务

## 当前状态

- 范围：{current["area"]}
- 里程碑：{current["milestone"]}
- 状态：{current["status"]}

## 已完成

{bullet_list(status["completed"])}

## 下一步

当前状态：`{next_step["status"]}`。

{next_step["objective"]}

下一阶段必须先完成最小设计并取得用户确认。{coding_instruction}

## Deferred

{bullet_list(status["deferred"])}
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate docs/next-tasks.md from docs/project-status.yaml."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if docs/next-tasks.md is not up to date without modifying it",
    )
    args = parser.parse_args()

    try:
        expected = render(load_status())
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    try:
        actual = OUTPUT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        actual = None
    except OSError as error:
        print(
            f"error: cannot read {OUTPUT_PATH.relative_to(REPOSITORY_ROOT)}: {error}",
            file=sys.stderr,
        )
        return 2

    if args.check:
        if actual != expected:
            print(
                "error: docs/next-tasks.md is out of date; "
                "run `python scripts/generate_project_docs.py`.",
                file=sys.stderr,
            )
            return 1
        return 0

    try:
        OUTPUT_PATH.write_text(expected, encoding="utf-8", newline="\n")
    except OSError as error:
        print(
            f"error: cannot write {OUTPUT_PATH.relative_to(REPOSITORY_ROOT)}: {error}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
