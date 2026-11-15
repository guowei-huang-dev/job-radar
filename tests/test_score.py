"""用假的 DeepSeek 响应验证解析、纠错重试、字段校验。"""
import os, sys, types
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ["DEEPSEEK_API_KEY"] = "sk-test"
from radar.score import Scorer, JobScore

GOOD = '{"score":82,"verdict":"apply","direction":"ai_llm","german_requirement":"optional","is_student_role":true,"hours_per_week":"20h/week","one_line":"搭 RAG 的岗位","match_reasons":["LangChain 重合"],"gaps":[],"red_flags":[]}'
FENCED = "```json\n" + GOOD + "\n```"
BAD = '{"score":"很高","verdict":"apply"}'

def make(responses):
    s = Scorer.__new__(Scorer)          # 跳过 __init__，不建真 client
    s.model, s.temperature = "deepseek-flash", 1.0
    s.profile, s.system = "p", "sys"
    it = iter(responses)
    s._call = lambda messages: next(it, None)
    return s

job = {"title":"t","company":"c","location":"Berlin","description":"jd","external_id":"1"}

r = make([GOOD]).score(job)
print("1. 正常 JSON      ->", r["score"], r["direction"])

r = make([FENCED]).score(job)
print("2. 带 ```json 包裹 ->", r["score"], "(已剥掉代码块)")

r = make([BAD, GOOD]).score(job)
print("3. 首次不合规重试  ->", r["score"], "(第二次成功)")

r = make([BAD, BAD]).score(job)
print("4. 两次都不合规    ->", r, "(正确地跳过)")

r = make([None]).score(job)
print("5. 拿不到响应      ->", r)

# 越界分数必须被拦
try:
    JobScore.model_validate_json('{"score":150,"verdict":"apply","direction":"ai_llm","german_requirement":"none","is_student_role":true,"hours_per_week":"x","one_line":"x","match_reasons":[],"gaps":[],"red_flags":[]}')
    print("6. score=150       -> 没拦住 ✗")
except Exception:
    print("6. score=150       -> 被 pydantic 拦下 ✓")
