"""日报生成：同一份数据出 HTML（邮件）和 Markdown（存档）。"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

VERDICT_LABEL = {"apply": "立刻投", "maybe": "值得看", "skip": "备选"}
DIRECTION_LABEL = {
    "ai_llm": "AI / LLM",
    "tooling_automation": "工具 / 自动化",
    "efficiency": "效率优化",
    "data_mlops": "数据 / MLOps",
    "general_swe": "软件工程",
    "other": "其他",
}


def _rows_to_items(rows) -> list[dict]:
    items = []
    for r in rows:
        try:
            a = json.loads(r["analysis"] or "{}")
        except json.JSONDecodeError:
            a = {}
        items.append({
            "title": r["title"] or "(无标题)",
            "company": r["company"] or "?",
            "location": r["location"] or "?",
            "url": r["url"],
            "source": r["source"],
            "score": r["score"] or 0,
            "verdict": a.get("verdict", "maybe"),
            "direction": a.get("direction", "other"),
            "one_line": a.get("one_line", ""),
            "hours": a.get("hours_per_week", "unknown"),
            "german": a.get("german_requirement", r["german_level"] or "none"),
            "reasons": a.get("match_reasons", []),
            "gaps": a.get("gaps", []),
            "flags": a.get("red_flags", []),
        })
    return items


def _score_color(score: int) -> str:
    if score >= 85:
        return "#1a7f37"
    if score >= 70:
        return "#9a6700"
    return "#57606a"


def render_html(rows, stats: dict, tz: str = "Europe/Berlin", title: str = "每日岗位雷达") -> str:
    items = _rows_to_items(rows)
    today = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")

    cards = []
    for i in items:
        badges = [
            f'<span style="background:#eef;color:#334;padding:2px 8px;border-radius:10px;'
            f'font-size:12px;margin-right:6px;">{DIRECTION_LABEL.get(i["direction"], "其他")}</span>',
            f'<span style="background:#f3f3f3;color:#444;padding:2px 8px;border-radius:10px;'
            f'font-size:12px;margin-right:6px;">{VERDICT_LABEL.get(i["verdict"], "")}</span>',
        ]
        if i["hours"] and i["hours"] != "unknown":
            badges.append(
                f'<span style="background:#f3f3f3;color:#444;padding:2px 8px;'
                f'border-radius:10px;font-size:12px;margin-right:6px;">{i["hours"]}</span>'
            )
        if i["german"] == "optional":
            badges.append(
                '<span style="background:#fff4e5;color:#8a5a00;padding:2px 8px;'
                'border-radius:10px;font-size:12px;">德语加分项</span>'
            )
        elif i["german"] == "unclear":
            badges.append(
                '<span style="background:#fff4e5;color:#8a5a00;padding:2px 8px;'
                'border-radius:10px;font-size:12px;">德语要求待确认</span>'
            )

        reasons = "".join(f"<li>{r}</li>" for r in i["reasons"][:4])
        gaps = "".join(f"<li>{g}</li>" for g in i["gaps"][:3])
        flags = (
            f'<div style="margin-top:8px;color:#a40e26;font-size:13px;">⚠ '
            f'{"；".join(i["flags"])}</div>' if i["flags"] else ""
        )

        cards.append(f"""
<div style="border:1px solid #e1e4e8;border-radius:10px;padding:16px;margin-bottom:14px;">
  <div style="display:flex;justify-content:space-between;align-items:baseline;">
    <a href="{i['url']}" style="font-size:17px;font-weight:600;color:#0969da;
       text-decoration:none;">{i['title']}</a>
    <span style="font-size:22px;font-weight:700;color:{_score_color(i['score'])};
       margin-left:12px;">{i['score']}</span>
  </div>
  <div style="color:#57606a;font-size:14px;margin:4px 0 10px;">
    {i['company']} · {i['location']} · <span style="color:#8b949e;">{i['source']}</span>
  </div>
  <div style="margin-bottom:10px;">{''.join(badges)}</div>
  <div style="font-size:14px;color:#24292f;margin-bottom:8px;">{i['one_line']}</div>
  <div style="font-size:13px;color:#24292f;">
    <b>匹配</b><ul style="margin:4px 0 8px;padding-left:18px;">{reasons}</ul>
    {'<b>欠缺</b><ul style="margin:4px 0;padding-left:18px;color:#57606a;">' + gaps + '</ul>' if gaps else ''}
  </div>
  {flags}
</div>""")

    body = "".join(cards) or (
        '<p style="color:#57606a;">今天没有符合条件的新岗位。</p>'
    )

    return f"""<html><body style="margin:0;padding:24px;background:#fafbfc;
font-family:-apple-system,'Helvetica Neue','PingFang SC',sans-serif;">
<div style="max-width:680px;margin:0 auto;">
  <h2 style="margin:0 0 4px;color:#24292f;">{title} · {today}</h2>
  <p style="color:#57606a;font-size:13px;margin:0 0 20px;">
    新抓取 {stats.get('fetched', 0)} 条 ·
    规则过滤掉 {stats.get('filtered', 0)} 条（其中德语门槛 {stats.get('german_dropped', 0)} 条）·
    打分 {stats.get('scored', 0)} 条 · 入选 {len(items)} 条
  </p>
  {body}
  <p style="color:#8b949e;font-size:12px;margin-top:24px;border-top:1px solid #e1e4e8;
     padding-top:12px;">
    要标记申请状态、按方向筛选、看被过滤掉的岗位，在电脑上跑
    <code style="background:#f3f3f3;padding:1px 5px;border-radius:4px;">python -m radar.web</code>
    打开面板。<br>
    分数是相对你 profile.md 的匹配度，不是岗位好坏。想调整口味就改 profile.md。
  </p>
</div></body></html>"""


def render_markdown(rows, stats: dict, tz: str = "Europe/Berlin") -> str:
    items = _rows_to_items(rows)
    today = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")
    lines = [
        f"# 岗位雷达 {today}",
        "",
        f"新抓取 {stats.get('fetched', 0)} · 过滤 {stats.get('filtered', 0)} "
        f"（德语 {stats.get('german_dropped', 0)}） · 打分 {stats.get('scored', 0)} "
        f"· 入选 {len(items)}",
        "",
    ]
    for i in items:
        lines += [
            f"## [{i['score']}] {i['title']} — {i['company']}",
            "",
            f"{i['location']} · {DIRECTION_LABEL.get(i['direction'], '其他')} · "
            f"{VERDICT_LABEL.get(i['verdict'], '')} · {i['hours']} · 德语：{i['german']}",
            "",
            i["one_line"],
            "",
            "**匹配**：" + "；".join(i["reasons"]),
        ]
        if i["gaps"]:
            lines.append("**欠缺**：" + "；".join(i["gaps"]))
        if i["flags"]:
            lines.append("**⚠**：" + "；".join(i["flags"]))
        lines += ["", f"<{i['url']}>", ""]
    if not items:
        lines.append("今天没有符合条件的新岗位。")
    return "\n".join(lines)
