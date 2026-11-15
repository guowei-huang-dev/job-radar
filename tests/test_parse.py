"""用一段 LinkedIn guest 接口的真实结构做解析测试。

LinkedIn 改版导致解析失效时，这个文件会先红 —— 改 fetch.py 的选择器后跑它。
    python tests/test_parse.py
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from bs4 import BeautifulSoup
from radar.fetch import Fetcher

FIXTURE = """
<li>
  <div class="base-card relative job-search-card"
       data-entity-urn="urn:li:jobPosting:4021938477">
    <a class="base-card__full-link"
       href="https://de.linkedin.com/jobs/view/working-student-ai-at-nimbus-4021938477?refId=abc&amp;trk=guest">
      <span class="sr-only">Working Student AI (m/f/d)</span>
    </a>
    <div class="base-search-card__info">
      <h3 class="base-search-card__title">Working Student AI (m/f/d)</h3>
      <h4 class="base-search-card__subtitle">
        <a class="hidden-nested-link" href="https://de.linkedin.com/company/nimbus">Nimbus AI</a>
      </h4>
      <span class="job-search-card__location">Berlin, Berlin, Germany</span>
      <time class="job-search-card__listdate" datetime="2026-09-13">1 day ago</time>
    </div>
  </div>
</li>
"""

def run() -> int:
    card = BeautifulSoup(FIXTURE, "lxml").select_one("li")
    job = Fetcher._parse_card(card)
    want = {
        "external_id": "4021938477",
        "title": "Working Student AI (m/f/d)",
        "company": "Nimbus AI",
        "location": "Berlin, Berlin, Germany",
        "posted_at": "2026-09-13",
    }
    bad = []
    if job is None:
        print("FAIL 解析返回 None"); return 1
    for k, v in want.items():
        if job.get(k) != v:
            bad.append(f"  {k}: want={v!r} got={job.get(k)!r}")
    if job.get("url", "").count("?"):
        bad.append("  url 没有去掉查询参数")
    print("解析 OK" if not bad else "FAIL\n" + "\n".join(bad))
    return 1 if bad else 0

if __name__ == "__main__":
    raise SystemExit(run())
