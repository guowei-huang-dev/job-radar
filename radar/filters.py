"""硬过滤：零成本规则，砍掉大部分岗位，剩下的才送去给模型打分。

顺序刻意安排成"先便宜后贵"：
  标题黑名单 → 标题白名单 → 地点 → 德语门槛（需要 JD 全文）
"""

from __future__ import annotations

from dataclasses import dataclass

from .german import GermanVerdict, assess_german


@dataclass
class FilterResult:
    passed: bool
    reason: str = ""
    german: GermanVerdict | None = None


def _hit(text: str | None, needles: list[str]) -> str | None:
    low = (text or "").lower()
    for n in needles:
        if n.lower() in low:
            return n
    return None


def check_meta(job: dict, cfg: dict) -> FilterResult:
    """只看标题和地点，不需要 JD 全文 —— 在抓详情页之前先跑，省请求。"""
    f = cfg["filters"]

    bad = _hit(job.get("title"), f.get("title_exclude") or [])
    if bad:
        return FilterResult(False, f"标题命中排除词：{bad}")

    must = f.get("title_must_include") or []
    if must and not _hit(job.get("title"), must):
        return FilterResult(False, "标题不含目标岗位类型词")

    loc_must = f.get("location_must_include") or []
    if loc_must and not _hit(job.get("location"), loc_must):
        return FilterResult(False, f"地点不匹配：{job.get('location')}")

    return FilterResult(True)


def check_description(job: dict, cfg: dict) -> FilterResult:
    """需要 JD 全文的规则 —— 目前主要是德语门槛。"""
    g = cfg["filters"]["german"]
    verdict = assess_german(job.get("description") or "")

    if verdict.level == "required" and g.get("drop_when_required", True):
        return FilterResult(False, "明确要求德语", verdict)

    if verdict.jd_is_german and g.get("german_jd_action") == "drop":
        return FilterResult(False, "JD 整篇为德语", verdict)

    return FilterResult(True, "", verdict)
