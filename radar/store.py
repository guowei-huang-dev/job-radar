"""SQLite 存储与去重。

去重键是 (source, external_id)。同一个岗位公司反复重发时标题会变、
链接会变，但 jobId 不变 —— 所以永远别拿标题去重。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    title         TEXT,
    company       TEXT,
    location      TEXT,
    url           TEXT,
    posted_at     TEXT,
    description   TEXT,
    first_seen    TEXT NOT NULL,
    -- 抓 JD 全文试过几次；超过上限才放弃，中途 Ctrl+C 不会让岗位被误判丢弃
    desc_attempts INTEGER NOT NULL DEFAULT 0,
    -- 处理状态: new -> filtered / scored
    stage         TEXT NOT NULL DEFAULT 'new',
    drop_reason   TEXT,
    german_level  TEXT,
    german_evidence TEXT,
    score         INTEGER,
    verdict       TEXT,
    direction     TEXT,
    analysis      TEXT,          -- 模型返回的完整 JSON
    reported_at   TEXT,
    -- 申请状态，在面板上点按钮维护
    -- new 待看 / shortlisted 想投 / applied 已投 / interview 面试 / rejected 被拒 / dismissed 忽略
    app_status    TEXT NOT NULL DEFAULT 'new',
    status_at     TEXT,          -- 状态最近一次变更时间
    applied_at    TEXT,          -- 首次标记为"已投"的时间
    note          TEXT,          -- 你自己写的备注
    PRIMARY KEY (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_stage ON jobs(stage);
CREATE INDEX IF NOT EXISTS idx_score ON jobs(score DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path = "jobs.db"):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """老版本建的库补上后加的列。"""
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(jobs)")}
        for col, ddl in (
            ("desc_attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("status_at", "TEXT"),
            ("applied_at", "TEXT"),
            ("note", "TEXT"),
        ):
            if col not in have:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
        # 旧库里默认状态是 'none'，统一成 'new'
        self.conn.execute("UPDATE jobs SET app_status='new' WHERE app_status='none'")

    def close(self) -> None:
        self.conn.close()

    # -------------------------------------------------- 写入

    def add_if_new(self, job: dict[str, Any]) -> bool:
        """返回 True 表示这是新岗位（之前没见过）。"""
        cur = self.conn.execute(
            "SELECT 1 FROM jobs WHERE source=? AND external_id=?",
            (job["source"], job["external_id"]),
        )
        if cur.fetchone():
            return False
        self.conn.execute(
            """INSERT INTO jobs (source, external_id, title, company, location,
                                 url, posted_at, description, first_seen)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                job["source"],
                job["external_id"],
                job.get("title"),
                job.get("company"),
                job.get("location"),
                job.get("url"),
                job.get("posted_at"),
                job.get("description"),
                _now(),
            ),
        )
        self.conn.commit()
        return True

    def set_description(self, source: str, external_id: str, text: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET description=? WHERE source=? AND external_id=?",
            (text, source, external_id),
        )
        self.conn.commit()

    def bump_desc_attempt(self, source: str, external_id: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET desc_attempts = desc_attempts + 1 "
            "WHERE source=? AND external_id=?",
            (source, external_id),
        )
        self.conn.commit()

    def flag_rule(self, source: str, external_id: str, reason: str,
                  german_level: str | None = None,
                  german_evidence: list[str] | None = None) -> None:
        """只打标签，不改 stage —— 岗位照常往下走，该抓 JD 抓 JD，该打分打分。

        同一个岗位可能被多条规则命中（比如标题不合 + 要求德语），用 "；" 累加，
        不覆盖已有的标签。
        """
        row = self.get_job(source, external_id)
        old = (row["drop_reason"] or "") if row else ""
        parts = [p for p in old.split("；") if p]
        if reason not in parts:
            parts.append(reason)
        self.conn.execute(
            """UPDATE jobs SET drop_reason=?,
                   german_level = COALESCE(?, german_level),
                   german_evidence = COALESCE(?, german_evidence)
               WHERE source=? AND external_id=?""",
            ("；".join(parts), german_level,
             json.dumps(german_evidence, ensure_ascii=False) if german_evidence else None,
             source, external_id),
        )
        self.conn.commit()

    def mark_dropped(self, source: str, external_id: str, reason: str,
                     german_level: str | None = None,
                     german_evidence: list[str] | None = None) -> None:
        self.conn.execute(
            """UPDATE jobs SET stage='filtered', drop_reason=?, german_level=?,
                               german_evidence=? WHERE source=? AND external_id=?""",
            (reason, german_level, json.dumps(german_evidence or [], ensure_ascii=False),
             source, external_id),
        )
        self.conn.commit()

    def save_score(self, source: str, external_id: str, analysis: dict[str, Any],
                   german_level: str | None = None,
                   german_evidence: list[str] | None = None) -> None:
        self.conn.execute(
            """UPDATE jobs SET stage='scored', score=?, verdict=?, direction=?,
                               analysis=?, german_level=?, german_evidence=?
               WHERE source=? AND external_id=?""",
            (
                analysis.get("score"),
                analysis.get("verdict"),
                analysis.get("direction"),
                json.dumps(analysis, ensure_ascii=False),
                german_level,
                json.dumps(german_evidence or [], ensure_ascii=False),
                source,
                external_id,
            ),
        )
        self.conn.commit()

    def mark_reported(self, rows: Iterable[sqlite3.Row]) -> None:
        ts = _now()
        self.conn.executemany(
            "UPDATE jobs SET reported_at=? WHERE source=? AND external_id=?",
            [(ts, r["source"], r["external_id"]) for r in rows],
        )
        self.conn.commit()

    # -------------------------------------------------- 读取

    def needing_description(self, limit: int, max_attempts: int = 3) -> list[sqlite3.Row]:
        """还没拿到 JD 全文、且重试次数没用完的岗位。

        上一次运行被 Ctrl+C 打断留下的半成品，会在这里被自动捡回来。
        """
        return list(self.conn.execute(
            """SELECT * FROM jobs
               WHERE stage='new' AND (description IS NULL OR description='')
                 AND source='linkedin' AND desc_attempts < ?
               ORDER BY first_seen DESC LIMIT ?""",
            (max_attempts, limit),
        ))

    def pending(self, limit: int) -> list[sqlite3.Row]:
        """还没走完流程的岗位。"""
        return list(self.conn.execute(
            "SELECT * FROM jobs WHERE stage='new' ORDER BY first_seen DESC LIMIT ?",
            (limit,),
        ))

    def unreported_matches(self, min_score: int, limit: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            """SELECT * FROM jobs
               WHERE stage='scored' AND reported_at IS NULL AND score >= ?
               ORDER BY score DESC LIMIT ?""",
            (min_score, limit),
        ))

    # -------------------------------------------------- 面板用

    STATUSES = ("new", "shortlisted", "applied", "interview", "rejected", "dismissed")
    # 还需要你处理的状态（"今天有几个能投"算的就是这两类）
    OPEN_STATUSES = ("new", "shortlisted")
    # 捞得回来的状态：这些标签页不限 stage，被过滤的岗位标了也要出现
    RESCUABLE = ("shortlisted", "applied", "interview", "rejected", "dismissed")

    # 丢弃原因里带了具体值（"地点不匹配：Cologne"），下拉框按大类归并，
    # 否则有多少个城市就有多少个选项。
    DROP_CATEGORIES = (
        "地点不匹配",
        "标题命中排除词",
        "标题不含目标岗位类型词",
        "明确要求德语",
        "模型复核：要求德语",
        "JD 整篇为德语",
        "重试 3 次仍拿不到 JD 全文",
    )

    @classmethod
    def drop_category(cls, reason: str | None) -> str:
        for c in cls.DROP_CATEGORIES:
            if reason and reason.startswith(c):
                return c
        return reason or ""

    def get_job(self, source: str, external_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE source=? AND external_id=?",
            (source, external_id),
        ).fetchone()

    def query_jobs(self, status: str | None = None, min_score: int = 0,
                   direction: str | None = None, german: str | None = None,
                   source: str | None = None, q: str | None = None,
                   reason: str | None = None, flagged: bool = False,
                   stage: str = "scored", sort: str = "score",
                   limit: int = 300) -> list[sqlite3.Row]:
        """面板的主查询。status='open' 表示 new + shortlisted。"""
        # stage="any"：不限阶段。用于「想投/已投」这些标签页 ——
        # 从被过滤堆里捞回来的岗位 stage 仍是 filtered，但你标了状态它就该出现在那些页里。
        where: list[str] = []
        args: list[Any] = []
        if stage and stage != "any":
            where.append("stage = ?")
            args.append(stage)

        if status == "open":
            where.append(f"app_status IN ({','.join('?' * len(self.OPEN_STATUSES))})")
            args += list(self.OPEN_STATUSES)
        elif status and status != "all":
            where.append("app_status = ?")
            args.append(status)

        if stage == "scored" and min_score and status != "filtered":
            where.append("score >= ?")
            args.append(min_score)
        if direction and direction != "all":
            where.append("direction = ?")
            args.append(direction)
        if german and german != "all":
            where.append("german_level = ?")
            args.append(german)
        if source and source != "all":
            where.append("source = ?")
            args.append(source)
        if q:
            where.append("(title LIKE ? OR company LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        if flagged:
            # 「规则标记」页：被任意一条规则点过名的岗位，不管打没打分
            where.append("drop_reason IS NOT NULL AND drop_reason != ''")
        if reason and reason != "all":
            # 按大类匹配。一个岗位可能有多个标签（"；" 分隔），所以两头都用通配
            where.append("drop_reason LIKE ?")
            args.append(f"%{reason}%")

        order = {
            "score": "score DESC, first_seen DESC",
            "date": "first_seen DESC, score DESC",
            "company": "company COLLATE NOCASE ASC",
            "status": "app_status ASC, score DESC",
        }.get(sort, "score DESC, first_seen DESC")

        args.append(limit)
        clause = " AND ".join(where) if where else "1=1"
        return list(self.conn.execute(
            f"SELECT * FROM jobs WHERE {clause} ORDER BY {order} LIMIT ?", args,
        ))

    def set_status(self, source: str, external_id: str, status: str) -> bool:
        if status not in self.STATUSES:
            return False
        now = _now()
        # applied_at 只记第一次，之后改状态不覆盖
        self.conn.execute(
            """UPDATE jobs SET app_status=?, status_at=?,
                   applied_at = CASE WHEN ?='applied' AND applied_at IS NULL
                                     THEN ? ELSE applied_at END
               WHERE source=? AND external_id=?""",
            (status, now, status, now, source, external_id),
        )
        self.conn.commit()
        return True

    def set_note(self, source: str, external_id: str, note: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET note=? WHERE source=? AND external_id=?",
            (note, source, external_id),
        )
        self.conn.commit()

    def dashboard_stats(self, min_score: int = 55) -> dict[str, Any]:
        """顶部卡片和标签页要的数字。

        关键：每个标签页的计数必须和它点开后 query_jobs 的条件完全一致，
        否则会出现"标签写 16、列表只有 4 条"。所以这里的每一条都对应
        query_jobs 的一种参数组合，改一边就要改另一边。
        """
        def one(sql: str, args: tuple = ()) -> int:
            return self.conn.execute(sql, args).fetchone()[0] or 0

        # 待处理 / 全部：stage='scored' 且分数过线（和列表一致）
        passed = "stage='scored' AND score >= ?"
        tabs = {
            "open": one(f"SELECT COUNT(*) FROM jobs WHERE {passed} "
                        f"AND app_status IN ('new','shortlisted')", (min_score,)),
            "all": one(f"SELECT COUNT(*) FROM jobs WHERE {passed}", (min_score,)),
            # 规则标记：被规则点过名的，不管打没打分（和列表的 flagged=True 一致）
            "flagged": one("SELECT COUNT(*) FROM jobs "
                           "WHERE drop_reason IS NOT NULL AND drop_reason != ''"),
        }
        # 想投/已投/…：不限 stage（和列表一致），这样从被过滤堆里捞回来的也算数
        for st in self.RESCUABLE:
            tabs[st] = one("SELECT COUNT(*) FROM jobs WHERE app_status=?", (st,))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        applied = tabs["applied"] + tabs["interview"] + tabs["rejected"]

        return {
            "actionable": tabs["open"],          # 主数字 = 待处理标签页
            "new_today": one(
                f"SELECT COUNT(*) FROM jobs WHERE {passed} AND substr(first_seen,1,10)=?",
                (min_score, today)),
            "shortlisted": tabs["shortlisted"],
            "applied_total": applied,
            "interview": tabs["interview"],
            "rejected": tabs["rejected"],
            "reply_rate": round(100 * tabs["interview"] / applied) if applied else None,
            "tabs": tabs,
            "filtered_total": tabs["flagged"],
            "german_dropped": one(
                "SELECT COUNT(*) FROM jobs WHERE stage='filtered' AND german_level='required'"),
            "pending": one("SELECT COUNT(*) FROM jobs WHERE stage='new'"),
            "min_score": min_score,
        }

    def facets(self) -> dict[str, list[str]]:
        """筛选下拉框的可选项，只列库里真实出现过的值。

        方向和德语只有走到打分阶段的岗位才有；被过滤的岗位大多在标题阶段就砍了，
        这两个字段是空的 —— 所以它们的选项只从 scored 里取，而丢弃原因只从
        filtered 里取。面板会按当前标签页决定显示哪几个筛选器。
        """
        def col(name: str, stage: str | None = "scored") -> list[str]:
            sql = f"SELECT DISTINCT {name} FROM jobs WHERE {name} IS NOT NULL AND {name} != ''"
            args: list[Any] = []
            if stage:
                sql += " AND stage = ?"
                args.append(stage)
            return [r[0] for r in self.conn.execute(sql + " ORDER BY 1", args)]

        # 规则标记现在遍布各个 stage，所以不限 stage 取；一个岗位可能有多个标签
        raw = col("drop_reason", None)
        reasons = sorted({self.drop_category(part)
                          for r in raw for part in r.split("；") if part})
        return {"direction": col("direction"), "german": col("german_level"),
                "source": col("source", None), "reason": reasons}

    def stats(self) -> dict[str, int]:
        row = self.conn.execute(
            """SELECT COUNT(*) total,
                      SUM(stage='new') pending,
                      SUM(stage='filtered') filtered,
                      SUM(stage='scored') scored
               FROM jobs"""
        ).fetchone()
        return {k: (row[k] or 0) for k in ("total", "pending", "filtered", "scored")}
