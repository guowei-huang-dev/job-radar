"""采集层。

LinkedIn 走的是不需要登录的 guest 接口 —— 没有 cookie，就没有账号风险。
代价是这属于非公开接口，LinkedIn 改版时解析会失效，所以每个解析步骤
都要能单独失败、跳过、记日志，不能让整个脚本崩。

限速是硬要求：request_delay 别往下调，429 之后退避等待。
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Any, Iterator

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

LINKEDIN_SEARCH = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
)
LINKEDIN_DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.6",
}


class Fetcher:
    def __init__(self, delay: tuple[float, float] = (3, 6), max_retries: int = 3):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.delay = delay
        self.max_retries = max_retries

    def _sleep(self) -> None:
        time.sleep(random.uniform(*self.delay))

    def _get(self, url: str, params: dict | None = None) -> str | None:
        for attempt in range(self.max_retries):
            try:
                r = self.session.get(url, params=params, timeout=20)
            except requests.RequestException as e:
                log.warning("请求失败 %s (%s/%s): %s", url, attempt + 1, self.max_retries, e)
                time.sleep(5 * (attempt + 1))
                continue

            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return None                      # 岗位已下架，正常情况
            if r.status_code in (429, 999):      # 999 = LinkedIn 的反爬拒绝
                wait = 30 * (attempt + 1)
                log.warning("被限速 (%s)，等待 %ss 后重试", r.status_code, wait)
                time.sleep(wait)
                continue
            log.warning("意外状态码 %s: %s", r.status_code, url)
            return None
        return None

    # ------------------------------------------------------------------
    # LinkedIn 列表页：返回的是 HTML 片段，不是 JSON
    # ------------------------------------------------------------------

    def linkedin_search(self, keywords: str, location: str, time_filter: str,
                        max_pages: int = 4) -> Iterator[dict[str, Any]]:
        for page in range(max_pages):
            params = {
                "keywords": keywords,
                "location": location,
                "f_TPR": time_filter,
                "start": page * 25,
            }
            html = self._get(LINKEDIN_SEARCH, params)
            self._sleep()
            if not html or not html.strip():
                return                       # 没有更多结果

            cards = BeautifulSoup(html, "lxml").select("li")
            if not cards:
                return
            found = 0
            for card in cards:
                job = self._parse_card(card)
                if job:
                    found += 1
                    yield job
            if found == 0:
                return

    @staticmethod
    def _parse_card(card) -> dict[str, Any] | None:
        try:
            urn_el = card.select_one("[data-entity-urn]")
            job_id = None
            if urn_el:
                urn = urn_el.get("data-entity-urn", "")
                m = re.search(r"(\d{6,})", urn)
                job_id = m.group(1) if m else None

            link_el = card.select_one("a.base-card__full-link") or card.select_one("a[href]")
            url = (link_el.get("href") or "").split("?")[0] if link_el else None
            if not job_id and url:
                m = re.search(r"-(\d{6,})(?:/|$)", url)
                job_id = m.group(1) if m else None
            if not job_id:
                return None

            def txt(sel):
                el = card.select_one(sel)
                return el.get_text(strip=True) if el else None

            time_el = card.select_one("time")
            return {
                "source": "linkedin",
                "external_id": job_id,
                "title": txt("h3.base-search-card__title") or txt("h3"),
                "company": txt("h4.base-search-card__subtitle a") or txt("h4"),
                "location": txt("span.job-search-card__location"),
                "url": url or f"https://www.linkedin.com/jobs/view/{job_id}/",
                "posted_at": time_el.get("datetime") if time_el else None,
                "description": None,
            }
        except Exception as e:                       # 解析失败就跳过这一条
            log.debug("卡片解析失败: %s", e)
            return None

    # ------------------------------------------------------------------
    # LinkedIn 详情页：拿 JD 全文
    # ------------------------------------------------------------------

    def linkedin_detail(self, job_id: str) -> str | None:
        html = self._get(LINKEDIN_DETAIL.format(job_id=job_id))
        self._sleep()
        if not html:
            return None
        try:
            soup = BeautifulSoup(html, "lxml")
            body = soup.select_one("div.show-more-less-html__markup") or soup.select_one(
                "div.description__text"
            )
            parts = []
            if body:
                parts.append(body.get_text("\n", strip=True))
            # 附带把 Seniority / Employment type 这些结构化条目也带上
            for item in soup.select("li.description__job-criteria-item"):
                label = item.select_one("h3")
                value = item.select_one("span")
                if label and value:
                    parts.append(f"{label.get_text(strip=True)}: {value.get_text(strip=True)}")
            text = "\n".join(p for p in parts if p)
            return text or None
        except Exception as e:
            log.debug("详情页解析失败 %s: %s", job_id, e)
            return None

    # ------------------------------------------------------------------
    # Arbeitnow：完全合规的公开 JSON API，作为补充源
    # ------------------------------------------------------------------

    def arbeitnow(self, pages: int = 3, keywords: list[str] | None = None
                  ) -> Iterator[dict[str, Any]]:
        keywords = [k.lower() for k in (keywords or [])]
        for page in range(1, pages + 1):
            try:
                r = self.session.get(
                    "https://www.arbeitnow.com/api/job-board-api",
                    params={"page": page}, timeout=20,
                )
                data = r.json()
            except Exception as e:
                log.warning("Arbeitnow 拉取失败: %s", e)
                return
            time.sleep(1)
            items = data.get("data") or []
            if not items:
                return
            for it in items:
                title = it.get("title") or ""
                if keywords and not any(k in title.lower() for k in keywords):
                    continue
                yield {
                    "source": "arbeitnow",
                    "external_id": str(it.get("slug") or it.get("url")),
                    "title": title,
                    "company": it.get("company_name"),
                    "location": it.get("location"),
                    "url": it.get("url"),
                    "posted_at": str(it.get("created_at") or ""),
                    # Arbeitnow 直接给全文，省一次详情页请求
                    "description": BeautifulSoup(
                        it.get("description") or "", "lxml"
                    ).get_text("\n", strip=True),
                }
