"""打分层：DeepSeek 对照 profile.md 给每个岗位打分。

DeepSeek 的 API 是 OpenAI 兼容格式，所以用 openai 这个 SDK，只是把 base_url
指到 api.deepseek.com。

它只支持 JSON 模式（response_format={"type": "json_object"}），没有 OpenAI 那种
强制 schema 的 structured outputs —— 所以做法是：提示词里给出 JSON 格式样例，
拿到结果后用 pydantic 校验，校验失败就带着错误信息重试一次。

官方文档还提到 JSON 模式偶尔会返回空内容，这里也一并重试。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Literal

from openai import OpenAI
from openai import APIError, APIConnectionError, RateLimitError
from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

Direction = Literal[
    "ai_llm",              # AI / LLM 应用开发
    "tooling_automation",  # 内部工具与自动化
    "efficiency",          # 流程 / 效率优化
    "data_mlops",          # 数据工程 / MLOps
    "general_swe",         # 通用软件工程
    "other",
]


class JobScore(BaseModel):
    score: int = Field(ge=0, le=100)
    verdict: Literal["apply", "maybe", "skip"]
    direction: Direction
    german_requirement: Literal["required", "optional", "none", "unclear"]
    is_student_role: bool
    hours_per_week: str
    one_line: str
    match_reasons: list[str]
    gaps: list[str]
    red_flags: list[str]


# 提示词里必须出现 "json" 这个词并给出格式样例，这是 DeepSeek JSON 模式的硬性要求
SCHEMA_EXAMPLE = """{
  "score": 82,
  "verdict": "apply",
  "direction": "ai_llm",
  "german_requirement": "optional",
  "is_student_role": true,
  "hours_per_week": "20h/week",
  "one_line": "用 LangChain 搭内部文档问答系统的 AI 工程 Werkstudent。",
  "match_reasons": ["JD 点名 LangChain + FastAPI，和他的 RAG 项目栈重合", "20h/week 符合"],
  "gaps": ["没有生产环境部署经验"],
  "red_flags": []
}"""

SYSTEM = """你在帮一位在柏林读研的学生筛选 Werkstudent / 实习岗位。

下面是他的完整画像，这是你唯一的打分基准：

<candidate_profile>
{profile}
</candidate_profile>

打分规则：

1. 方向优先级决定分数上限。AI/LLM 应用开发 与 内部工具/自动化 是他的主攻方向，
   这两类岗位上限 100；数据工程/MLOps、通用软件工程上限 85；
   其他方向（哪怕他过去做过，比如供应链、SAP、产品经理）上限 40 —— 他正在主动转向，
   不要因为他有那些背景就给这类岗位高分。
2. 德语是硬门槛。JD 里任何"必须会德语"的表述（fließend、verhandlungssicher、
   Wort und Schrift、B2/C1/C2、good German skills、required 等）都算 required。
   写明"von Vorteil / a plus / nice to have / not required"的算 optional。
   判断不了就填 unclear。german_requirement 填 required 时，score 一律不超过 20。
3. 岗位内容要能让他动手写代码或造工具。偏协调、偏运营、偏文档的岗位降分。
4. 学生身份友好度：明确 Werkstudent/Working Student/Intern 且 15–20h/week 最好；
   要求全职或 6 个月以上强制实习的降分。
5. 分数校准：85+ 是"立刻投"；70–84 是"值得投"；55–69 是"有空再说"；
   55 以下是"不用看"。不要给所有岗位都打 70 分左右，该低就低。

match_reasons 要具体到 JD 里的原话或技术栈，不要写"技能匹配"这种空话。
所有文字字段用中文回答。

只输出一个 json 对象，不要加解释、不要用 markdown 代码块包裹。
字段和格式严格照这个样例：

{example}

字段取值约束：
- score: 0–100 的整数
- verdict: "apply" | "maybe" | "skip"
- direction: "ai_llm" | "tooling_automation" | "efficiency" | "data_mlops" | "general_swe" | "other"
- german_requirement: "required" | "optional" | "none" | "unclear"
- is_student_role: true | false
- hours_per_week: JD 里写的周工时，没写就填 "unknown"
- match_reasons 最多 4 条，gaps 最多 3 条，red_flags 没有就给空数组"""

USER = """岗位信息：

标题：{title}
公司：{company}
地点：{location}

正则预判的德语要求：{german_level}
（触发判定的原句：{german_evidence}）

JD 全文：
<job_description>
{description}
</job_description>

按要求输出 json。"""


class Scorer:
    def __init__(self, profile_path: str | Path = "profile.md",
                 model: str = "deepseek-flash", temperature: float = 1.0,
                 base_url: str = DEEPSEEK_BASE_URL):
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "没有找到 DEEPSEEK_API_KEY —— 检查 .env 里是否填了，"
                "或者用 --dry-run 跳过打分"
            )
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.temperature = temperature
        self.profile = Path(profile_path).read_text(encoding="utf-8")
        self.system = SYSTEM.format(profile=self.profile, example=SCHEMA_EXAMPLE)

    def _call(self, messages: list[dict]) -> str | None:
        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=self.temperature,
                    max_tokens=2000,
                )
            except RateLimitError:
                wait = 20 * (attempt + 1)
                log.warning("DeepSeek 限流，等待 %ss", wait)
                time.sleep(wait)
                continue
            except (APIConnectionError, APIError) as e:
                log.warning("DeepSeek 请求失败（%s/3）：%s", attempt + 1, e)
                time.sleep(5 * (attempt + 1))
                continue

            content = (resp.choices[0].message.content or "").strip()
            if content:
                return content
            # 官方文档提到 JSON 模式偶尔返回空内容
            log.warning("返回内容为空，重试（%s/3）", attempt + 1)
        return None

    @staticmethod
    def _strip_fence(text: str) -> str:
        """模型偶尔还是会套一层 ```json ``` —— 剥掉。"""
        t = text.strip()
        if t.startswith("```"):
            t = t.split("\n", 1)[-1] if "\n" in t else t
            t = t.rsplit("```", 1)[0]
        return t.strip()

    def score(self, job: dict, german_level: str = "none",
              german_evidence: list[str] | None = None) -> dict | None:
        user = USER.format(
            title=job.get("title") or "",
            company=job.get("company") or "",
            location=job.get("location") or "",
            german_level=german_level,
            german_evidence=" / ".join(german_evidence or []) or "无",
            description=job.get("description") or "",
        )
        messages = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": user},
        ]

        raw = self._call(messages)
        if raw is None:
            log.error("打分失败（拿不到响应）：%s", job.get("external_id"))
            return None

        try:
            return JobScore.model_validate_json(self._strip_fence(raw)).model_dump()
        except (ValidationError, json.JSONDecodeError) as e:
            # 带着报错让它自己改一次
            log.warning("返回格式不合规，要求重新输出：%s", str(e)[:150])
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user", "content":
                    f"上面的 json 不符合要求，报错是：\n{str(e)[:500]}\n"
                    f"请只输出修正后的 json 对象，不要任何解释。"},
            ]
            raw2 = self._call(messages)
            if raw2 is None:
                return None
            try:
                return JobScore.model_validate_json(self._strip_fence(raw2)).model_dump()
            except (ValidationError, json.JSONDecodeError) as e2:
                log.error("重试后仍不合规，跳过 %s：%s",
                          job.get("external_id"), str(e2)[:150])
                return None
