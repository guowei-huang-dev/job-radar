"""本地面板。

    python -m radar.web          然后浏览器开 http://127.0.0.1:8000

读的是抓取脚本写的同一个 jobs.db，所以两边可以同时跑：
GitHub Actions 那边抓取，你这边 git pull 之后刷新页面就是最新的。

只监听 127.0.0.1 —— 数据只在你自己机器上，不对局域网暴露。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .store import Store

BASE = Path(__file__).parent
STATIC = BASE / "static"

app = FastAPI(title="job-radar", docs_url=None, redoc_url=None)

# 路径走环境变量而不是模块全局：开了 --reload 之后 uvicorn 会另起一个子进程
# 重新 import 本模块，那个子进程不会执行 main()，只有环境变量传得过去。
_settings: dict[str, Any] = {
    "db": os.getenv("RADAR_DB", "jobs.db"),
    "config": os.getenv("RADAR_CONFIG", "config.yaml"),
}


def store() -> Store:
    return Store(_settings["db"])


def min_score() -> int:
    try:
        cfg = yaml.safe_load(Path(_settings["config"]).read_text(encoding="utf-8"))
        return int(cfg.get("scoring", {}).get("min_score_to_report", 55))
    except Exception:
        return 55


def row_to_job(r: sqlite3.Row) -> dict[str, Any]:
    try:
        a = json.loads(r["analysis"] or "{}")
    except json.JSONDecodeError:
        a = {}
    try:
        evidence = json.loads(r["german_evidence"] or "[]")
    except json.JSONDecodeError:
        evidence = []

    return {
        "source": r["source"],
        "id": r["external_id"],
        "title": r["title"] or "(无标题)",
        "company": r["company"] or "?",
        "location": r["location"] or "?",
        "url": r["url"],
        "posted_at": r["posted_at"],
        "first_seen": (r["first_seen"] or "")[:10],
        "score": r["score"] or 0,
        "status": r["app_status"] or "new",
        "applied_at": (r["applied_at"] or "")[:10] or None,
        "note": r["note"] or "",
        "drop_reason": r["drop_reason"],
        # 注意区分 "none"（判定过，JD 里确实没提德语）和 "unknown"（压根没判定过）。
        # 在标题/地点阶段就被砍掉的岗位根本没抓 JD，不能说它"未提德语"。
        "german": a.get("german_requirement") or r["german_level"] or "unknown",
        "german_evidence": evidence,
        # 同理：没打过分的岗位没有方向，别默认成 "other"
        "direction": a.get("direction") or None,
        "verdict": a.get("verdict", "maybe"),
        "hours": a.get("hours_per_week", "unknown"),
        "one_line": a.get("one_line", ""),
        "reasons": a.get("match_reasons", []),
        "gaps": a.get("gaps", []),
        "flags": a.get("red_flags", []),
    }


# ---------------------------------------------------------------- 页面

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "dashboard.html")


# ---------------------------------------------------------------- 接口

@app.get("/api/stats")
def api_stats() -> dict[str, Any]:
    s = store()
    try:
        data = s.dashboard_stats(min_score())
        data["facets"] = s.facets()
        return data
    finally:
        s.close()


# 注意：路由函数的注解必须用 Optional[int] 而不是 int | None ——
# FastAPI 会在运行时求值这些注解，而 X | None 要 Python 3.10 才支持。
@app.get("/api/jobs")
def api_jobs(status: str = "open", direction: str = "all", german: str = "all",
             source: str = "all", q: str = "", sort: str = "score",
             reason: str = "all", stage: str = "scored", flagged: bool = False,
             min_score_override: Optional[int] = None,
             ) -> dict[str, Any]:
    s = store()
    try:
        rows = s.query_jobs(
            status=status,
            min_score=min_score() if min_score_override is None else min_score_override,
            direction=direction, german=german, source=source,
            q=q.strip() or None, reason=reason, flagged=flagged,
            sort=sort, stage=stage,
        )
        return {"jobs": [row_to_job(r) for r in rows], "count": len(rows)}
    finally:
        s.close()


class StatusPatch(BaseModel):
    status: str = Field(description="new|shortlisted|applied|interview|rejected|dismissed")


class NotePatch(BaseModel):
    note: str = ""


@app.post("/api/jobs/{source}/{external_id}/status")
def api_set_status(source: str, external_id: str, patch: StatusPatch) -> dict[str, Any]:
    s = store()
    try:
        if s.get_job(source, external_id) is None:
            raise HTTPException(404, "岗位不存在")
        if not s.set_status(source, external_id, patch.status):
            raise HTTPException(400, f"未知状态：{patch.status}")
        return row_to_job(s.get_job(source, external_id))
    finally:
        s.close()


@app.post("/api/jobs/{source}/{external_id}/note")
def api_set_note(source: str, external_id: str, patch: NotePatch) -> dict[str, Any]:
    s = store()
    try:
        if s.get_job(source, external_id) is None:
            raise HTTPException(404, "岗位不存在")
        s.set_note(source, external_id, patch.note)
        return {"ok": True}
    finally:
        s.close()


@app.get("/api/keywords")
def api_keywords() -> dict[str, Any]:
    cfg = yaml.safe_load(Path(_settings["config"]).read_text(encoding="utf-8"))
    li = cfg.get("sources", {}).get("linkedin", {})
    return {
        "keywords": [x.get("keywords") for x in li.get("searches", [])],
        "location": li.get("location", ""),
        "time_filter": li.get("time_filter", "r86400"),
    }


class KeywordsPatch(BaseModel):
    keywords: list[str]


def replace_searches(text: str, keywords: list) -> str:
    """只替换 config.yaml 里 searches: 底下的列表项，其余一个字符都不动。

    不能用 yaml.safe_dump 整个回写 —— PyYAML 不保留注释，
    保存一次就会把 config.yaml 里的说明注释全部抹掉。
    """
    lines = text.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^\s*searches:\s*(#.*)?$", ln):
            start = i
            break
    if start is None:
        raise HTTPException(500, "config.yaml 里找不到 searches: 这一段")

    indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines):
        ln = lines[end]
        if not ln.strip():                                  # 空行 → 块结束
            break
        if len(ln) - len(ln.lstrip()) <= indent:            # 回到同级/外层 → 块结束
            break
        end += 1

    item_indent = " " * (indent + 2)
    items = [f'{item_indent}- keywords: "{k}"' for k in keywords]
    return "\n".join(lines[:start + 1] + items + lines[end:])


@app.post("/api/keywords")
def api_set_keywords(patch: KeywordsPatch) -> dict[str, Any]:
    path = Path(_settings["config"])
    cleaned = [k.strip().replace('"', "'") for k in patch.keywords if k and k.strip()]
    if not cleaned:
        raise HTTPException(400, "关键词不能全为空")

    text = path.read_text(encoding="utf-8")
    new_text = replace_searches(text, cleaned)

    # 写之前先确认改出来的还是合法 YAML 且结构没坏
    try:
        cfg = yaml.safe_load(new_text)
        got = [s["keywords"] for s in cfg["sources"]["linkedin"]["searches"]]
    except Exception as e:
        raise HTTPException(500, f"生成的 config.yaml 不合法，已放弃写入：{e}")
    if got != cleaned:
        raise HTTPException(500, "写回校验不通过，已放弃写入")

    path.write_text(new_text, encoding="utf-8")
    return {"ok": True, "keywords": cleaned}


@app.exception_handler(HTTPException)
def http_error(_request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


# ---------------------------------------------------------------- 入口

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="radar.web", description="job-radar 本地面板")
    p.add_argument("--db", default="jobs.db")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--reload", action="store_true",
                   help="代码改动后自动重启，省得每次手动 kickstart（需要 watchfiles）")
    args = p.parse_args(argv)

    # 子进程靠环境变量拿到这两个路径
    os.environ["RADAR_DB"] = _settings["db"] = args.db
    os.environ["RADAR_CONFIG"] = _settings["config"] = args.config

    print(f"\n  面板已启动 →  http://{args.host}:{args.port}\n  Ctrl+C 停止\n", flush=True)
    uvicorn.run(
        "radar.web:app" if args.reload else app,
        host=args.host, port=args.port, log_level="warning",
        reload=args.reload,
        # 只盯 Python 文件；jobs.db 每天被写不该触发重启
        reload_dirs=[str(BASE)] if args.reload else None,
        reload_includes=["*.py"] if args.reload else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
