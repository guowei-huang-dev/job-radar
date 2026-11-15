"""主流程：抓取 → 去重 → 硬过滤 → 打分 → 日报 → 邮件。

    python -m radar.cli run              完整跑一遍
    python -m radar.cli run --dry-run    不调模型、不发邮件（只看抓取和过滤效果）
    python -m radar.cli run --no-mail    打分但只写本地 Markdown
    python -m radar.cli stats            看看库里有多少数据
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import yaml

from .fetch import Fetcher
from .filters import check_description, check_meta
from .german import assess_german
from .report import render_html, render_markdown
from .store import Store

log = logging.getLogger("radar")


def load_config(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


# ---------------------------------------------------------------- 各阶段

def make_fetcher(cfg: dict) -> Fetcher:
    li = cfg["sources"].get("linkedin", {})
    return Fetcher(
        delay=tuple(li.get("request_delay", [3, 6])),
        max_retries=li.get("max_retries", 3),
    )


def collect(cfg: dict, store: Store, fetcher: Fetcher) -> int:
    """只抓列表页 + 标题/地点过滤 + 入库。JD 全文交给下一阶段，
    这样中途中断也不会留下"入库了但永远没有 JD"的半成品。"""
    src = cfg["sources"]
    li = src.get("linkedin", {})
    score_all = cfg["filters"].get("score_everything", True)

    new_count = 0
    seen_this_run: set[tuple[str, str]] = set()

    if li.get("enabled", True):
        for search in li.get("searches", []):
            kw = search["keywords"]
            log.info("LinkedIn 搜索：%s", kw)
            try:
                stubs = list(fetcher.linkedin_search(
                    keywords=kw,
                    location=li.get("location", "Berlin, Germany"),
                    time_filter=li.get("time_filter", "r86400"),
                    max_pages=li.get("max_pages", 4),
                ))
            except Exception as e:
                log.warning("搜索 %s 失败，跳过：%s", kw, e)
                continue
            log.info("  拿到 %s 条", len(stubs))

            for job in stubs:
                key = (job["source"], job["external_id"])
                if key in seen_this_run:
                    continue
                seen_this_run.add(key)

                if store.add_if_new(job):
                    new_count += 1
                meta = check_meta(job, cfg)
                if not meta.passed:
                    if score_all:
                        # 只打标签，岗位照常往下走 —— 规则会误杀，让分数说话
                        store.flag_rule(*key, meta.reason)
                    else:
                        store.mark_dropped(*key, meta.reason)

    an = src.get("arbeitnow", {})
    if an.get("enabled", False):
        log.info("Arbeitnow 拉取")
        for job in fetcher.arbeitnow(an.get("pages", 3), an.get("keywords")):
            key = (job["source"], job["external_id"])
            if key in seen_this_run:
                continue
            seen_this_run.add(key)
            if store.add_if_new(job):
                new_count += 1
            meta = check_meta(job, cfg)
            if not meta.passed:
                if score_all:
                    store.flag_rule(*key, meta.reason)
                else:
                    store.mark_dropped(*key, meta.reason)

    return new_count


def fill_descriptions(cfg: dict, store: Store, fetcher: Fetcher, budget: int) -> int:
    """给还没有 JD 全文的岗位补抓详情页 —— 这是整个流程最慢的一段。

    每条一行进度输出，别让它看起来像卡死了。中断后重跑会接着补，
    重试 3 次还拿不到才放弃（岗位可能已经下架）。
    """
    todo = store.needing_description(budget)
    if not todo:
        return 0
    log.info("补抓 JD 全文：%s 条（每条约 %s–%s 秒，可以 Ctrl+C 中断，下次接着抓）",
             len(todo), *cfg["sources"]["linkedin"].get("request_delay", [3, 6]))

    filled = 0
    for n, row in enumerate(todo, 1):
        key = (row["source"], row["external_id"])
        store.bump_desc_attempt(*key)
        desc = fetcher.linkedin_detail(row["external_id"])
        if desc:
            store.set_description(*key, desc)
            filled += 1
        log.info("  %3s/%s  %s %s", n, len(todo),
                 "✓" if desc else "✗", (row["title"] or "")[:55])
    return filled


def screen_and_score(cfg: dict, store: Store, dry_run: bool) -> dict:
    """对 stage='new' 的岗位跑德语门槛，通过的送去打分。"""
    counters = {"filtered": 0, "german_dropped": 0, "scored": 0,
                "flagged": 0, "german_flagged": 0}
    score_all = cfg["filters"].get("score_everything", True)
    pending = store.pending(cfg["scoring"].get("max_jobs_per_run", 60))
    log.info("待处理 %s 条", len(pending))

    scorer = None
    if not dry_run and pending:
        from .score import Scorer
        scorer = Scorer(
            profile_path=cfg.get("profile_path", "profile.md"),
            model=cfg["scoring"].get("model", "deepseek-flash"),
            temperature=cfg["scoring"].get("temperature", 1.0),
        )

    for row in pending:
        job = dict(row)
        key = (job["source"], job["external_id"])

        if not job.get("description"):
            # 重试次数还没用完的，留到下次运行再补，不要在这里判死刑
            if (job.get("desc_attempts") or 0) < 3:
                continue
            store.mark_dropped(*key, "重试 3 次仍拿不到 JD 全文（可能已下架）")
            counters["filtered"] += 1
            continue

        res = check_description(job, cfg)
        if not res.passed:
            ge = (res.german.level if res.german else None,
                  res.german.evidence if res.german else None)
            if score_all:
                # 要求德语只是个标签，照样打分 —— 提示词里已经把这类岗位的分数压到 20 以下
                store.flag_rule(*key, res.reason, *ge)
                counters["flagged"] += 1
                if res.german and res.german.level == "required":
                    counters["german_flagged"] += 1
            else:
                store.mark_dropped(*key, res.reason, *ge)
                counters["filtered"] += 1
                if res.german and res.german.level == "required":
                    counters["german_dropped"] += 1
                continue

        g = res.german or assess_german(job["description"])

        if dry_run:
            flag = job.get("drop_reason") or ""
            log.info("  [待打分] %-52s | 德语=%-8s %s",
                     (job["title"] or "")[:52], g.level,
                     f"⚑ {flag}" if flag else "")
            continue

        analysis = scorer.score(job, g.level, g.evidence)
        if analysis is None:
            continue

        # 模型复核推翻正则
        if analysis.get("german_requirement") == "required":
            if score_all:
                store.flag_rule(*key, "模型复核：要求德语", "required", g.evidence)
                counters["german_flagged"] += 1
            elif cfg["filters"]["german"].get("drop_when_required", True):
                store.mark_dropped(*key, "模型复核：要求德语", "required", g.evidence)
                counters["filtered"] += 1
                counters["german_dropped"] += 1
                continue

        store.save_score(*key, analysis, g.level, g.evidence)
        counters["scored"] += 1
        log.info("  [%3s] %s", analysis.get("score"), (job["title"] or "")[:60])

    return counters


def build_report(cfg: dict, store: Store, stats: dict, send_mail: bool) -> None:
    rep = cfg["report"]
    rows = store.unreported_matches(
        cfg["scoring"].get("min_score_to_report", 55),
        rep.get("max_items", 25),
    )

    if not rows and not rep.get("send_when_empty", False):
        log.info("没有入选岗位，不发邮件")
        return

    html = render_html(rows, stats, rep.get("timezone", "Europe/Berlin"),
                       rep.get("title", "每日岗位雷达"))
    md = render_markdown(rows, stats, rep.get("timezone", "Europe/Berlin"))

    out = Path("reports")
    out.mkdir(exist_ok=True)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo(rep.get("timezone", "Europe/Berlin"))).strftime("%Y-%m-%d")
    (out / f"{day}.md").write_text(md, encoding="utf-8")
    log.info("日报已写入 reports/%s.md", day)

    if send_mail:
        from .mailer import MailConfigError, send
        subject = f"{rep.get('title', '岗位雷达')} · {len(rows)} 个新机会 · {day}"
        try:
            send(subject, html, md)
        except MailConfigError as e:
            log.error("邮件未发送：%s", e)
        except Exception as e:
            log.error("邮件发送失败：%s", e)

    store.mark_reported(rows)


# ---------------------------------------------------------------- 入口

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="radar")
    parser.add_argument("command", choices=["run", "stats"])
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--db", default="jobs.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="不调模型、不发邮件，只看抓取和过滤效果")
    parser.add_argument("--no-mail", action="store_true", help="打分但不发邮件")
    parser.add_argument("--max-details", type=int, default=400,
                        help="单次运行最多补抓多少个 JD 全文（控制单次时长，"
                             "没抓完的下次接着抓）")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="跳过抓取，只对库里已有的数据跑过滤和打分")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    load_dotenv()
    cfg = load_config(args.config)
    store = Store(args.db)

    try:
        if args.command == "stats":
            for k, v in store.stats().items():
                print(f"{k:10s} {v}")
            return 0

        fetched = 0
        if not args.skip_fetch:
            fetcher = make_fetcher(cfg)
            fetched = collect(cfg, store, fetcher)
            log.info("列表抓取完成，新增 %s 条", fetched)
            fill_descriptions(cfg, store, fetcher, args.max_details)

        counters = screen_and_score(cfg, store, args.dry_run)
        counters["fetched"] = fetched

        if not args.dry_run:
            build_report(cfg, store, counters, send_mail=not args.no_mail)
            open_now = store.dashboard_stats(
                cfg["scoring"].get("min_score_to_report", 55))["actionable"]
            log.info("待你处理 %s 个岗位 —— 跑 `python -m radar.web` 打开面板", open_now)
        else:
            log.info("dry-run 结束：%s", counters)
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
