"""德语门槛判定。

思路：不在整篇 JD 上做关键词匹配（会被 "Deutschland"、"German office" 这类词误伤），
而是先分句，只看提到德语的句子，再判断这句话是在"要求"还是在"加分"。

三种结论：
  required  —— 明确要求德语能力 → 直接丢弃
  optional  —— 德语只是加分/非必需 → 保留
  unclear   —— 提到了德语但句式判断不了 → 保留，交给模型复核
  none      —— 整篇没提德语 → 保留
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 1. 句子里是否提到了德语这门语言
#    注意排除 Deutschland / Deutsche Bahn / German office 这类非语言用法
# --------------------------------------------------------------------------

_MENTION = re.compile(
    r"""
    \b(
        deutschkenntniss\w*     # Deutschkenntnisse
      | deutschsprachig\w*
      | sprachkenntniss\w*      # 常和 deutsch 同句出现
      | deutsch(?!land|e\s+bahn|e\s+bank|e\s+telekom|e\s+post)\w*
      | german(?!y)\w*
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# "German" 后面跟这些名词时说的不是语言，是市场/公司/法律。
# 把这些用法从句子里抹掉后如果不再提德语，这句就跟语言无关。
_NON_LANGUAGE_USE = re.compile(
    r"""
    \b(german|deutsche[nrs]?)\s+
    (office|market|markt|customer|kunden|client|company|unternehmen|entity|subsidiary
     |niederlassung|standort|team|law|recht|gesetz|tax|steuer|labor|labour|arbeitsrecht
     |government|beh[öo]rde|user|nutzer|startup|engineering|headquarter|branch|site
     |holiday|feiertag|bureaucracy|b[üu]rokratie|health|insurance|versicherung
     |standard|norm|regulation|verordnung|partner|hochschule|universit[äa]t)\w*
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _language_related(sent: str) -> bool:
    """这句话是在谈德语这门语言，而不是德国市场/德国公司。"""
    if not _MENTION.search(sent):
        return False
    return bool(_MENTION.search(_NON_LANGUAGE_USE.sub(" ", sent)))


# --------------------------------------------------------------------------
# 2. "加分项 / 非必需" 标记 —— 优先级最高，命中就判 optional
#    （"gute Deutschkenntnisse von Vorteil" 整体是加分项，不是要求）
# --------------------------------------------------------------------------

_OPTIONAL_PATTERNS = [
    # --- 德语表述 ---
    r"von\s+vorteil",
    r"w[üu]nschenswert",
    r"ein\s+plus",
    r"pluspunkt",
    r"hilfreich",
    r"willkommen",
    r"kein\s+muss",
    r"nicht\s+(zwingend|erforderlich|notwendig|vorausgesetzt|n[öo]tig)",
    r"keine\s+deutschkenntnisse\s+(erforderlich|n[öo]tig|notwendig)",
    r"gerne\s+auch",
    r"optional",
    r"bereitschaft[^.]{0,30}deutsch\s+zu\s+lernen",
    r"deutsch\s+zu\s+lernen",
    # --- 英语表述 ---
    r"is\s+a\s+plus",
    r"are\s+a\s+plus",
    r"is\s+an?\s+(advantage|asset|bonus)",
    r"would\s+be\s+(a\s+plus|an?\s+(advantage|asset|bonus)|great|nice|beneficial|helpful)",
    r"nice[-\s]to[-\s]have",
    r"beneficial",
    r"not\s+(required|mandatory|necessary|a\s+requirement)",
    r"no\s+german\s+(required|needed|necessary)",
    r"don'?t\s+need\s+(to\s+speak\s+)?german",
    r"willing(ness)?\s+to\s+learn\s+german",
    r"appreciated",
    r"welcome",
    # --- "公司语言是英语" 是强烈的非德语信号 ---
    r"english\s+is\s+(our|the)\s+(only\s+)?(company|working|office|team)\s+language",
    r"(company|working|office|team)\s+language\s+is\s+english",
    r"englisch\s+ist\s+(unsere|die)\s+(unternehmens|arbeits|team)sprache",
    r"we\s+(work|communicate)\s+in\s+english",
]

# --------------------------------------------------------------------------
# 3. "硬性要求" 标记 —— 覆盖 JD 里各种花式写法
# --------------------------------------------------------------------------

_REQUIRED_PATTERNS = [
    # --- 德语：流利度描述 ---
    r"flie[ßs]end\w*",
    r"verhandlungssicher\w*",
    r"in\s+wort\s+und\s+schrift",
    r"muttersprach\w*",
    r"als\s+muttersprache",
    r"auf\s+(mutter)?sprachlichem\s+niveau",
    r"(sehr\s+)?gute?\s+deutschkenntniss\w*",
    r"sehr\s+gute?\s+kenntniss\w*\s+(in|der)\s+deutsch",
    r"sicher\w*\s+deutschkenntniss\w*",
    r"exzellente?\s+deutsch",
    r"ausgezeichnete?\s+deutsch",
    r"solide\s+deutschkenntniss\w*",
    r"fundierte\s+deutschkenntniss\w*",
    r"beherrsch\w*",
    r"gute\s+kenntnisse\s+der\s+deutschen\s+sprache",
    # --- 德语：要求动词 ---
    r"erforderlich",
    r"vorausgesetzt",
    r"voraussetzung",
    r"setzen\s+wir\s+voraus",
    r"zwingend",
    r"notwendig",
    r"unerl[äa]sslich",
    r"unbedingt",
    r"mindestens",
    r"du\s+sprichst\s+deutsch",
    r"sie\s+sprechen\s+deutsch",
    r"sprichst\s+flie[ßs]end",
    # --- 英语：流利度描述 ---
    r"fluent",
    r"fluency",
    r"native\s*(level|speaker)?",
    r"business[-\s]?(fluent|level|proficient)",
    r"professional\s+working\s+proficiency",
    r"full\s+professional\s+proficiency",
    r"(excellent|strong|very\s+good|good|solid|advanced|proficient|confident|comfortable)\s+"
    r"(command\s+of\s+|knowledge\s+of\s+|level\s+of\s+|written\s+and\s+spoken\s+|spoken\s+and\s+written\s+|"
    r"verbal\s+and\s+written\s+|in\s+)?(german|deutsch)",
    r"(german|deutsch)[^.]{0,25}(skills?|kenntniss\w*|proficiency|level)[^.]{0,25}"
    r"(required|essential|mandatory|necessary|a\s+must|expected)",
    # --- 英语：要求动词 ---
    r"\brequired\b",
    r"\brequirement\b",
    r"\bwe\s+require\b",
    r"\bmandatory\b",
    r"\bessential\b",
    r"\bmust[-\s]have\b",
    r"\bmust\s+(speak|be\s+able\s+to\s+speak|have)\b",
    r"\bneed\s+to\s+(speak|be\s+fluent)\b",
    r"\bis\s+a\s+must\b",
    r"\bat\s+least\b",
    r"\byou\s+speak\s+(german|fluent)",
    # --- 在谈语言能力的句子里，"gut/sehr gut" 基本等同于要求 ---
    r"\bsehr\s+gute?[rns]?\b",
    r"\bgute?[rns]?\b",
    # --- 语言等级：B2 及以上一律算要求 ---
    r"\b(b2|c1|c2)\b",
    r"niveau\s*(b2|c1|c2)",
    r"level\s*(b2|c1|c2)",
]

# 「德语 + 英语 都要」这种句式：单独处理，因为它不含上面任何要求动词也是硬要求
_BOTH_LANGUAGES = [
    r"deutsch\s+und\s+englisch",
    r"englisch\s+und\s+deutsch",
    r"german\s+and\s+english",
    r"english\s+and\s+german",
    r"deutsch\s*(&|/|\+)\s*englisch",
    r"german\s*(&|/|\+)\s*english",
]

# A1/A2/B1 属于低要求，遇到时不判 required（他有 A2）
_LOW_LEVEL = re.compile(r"\b(a1|a2|b1)\b|grundkenntniss|basic\s+german|basisc?\w*\s+deutsch", re.IGNORECASE)

_OPTIONAL_RE = [re.compile(p, re.IGNORECASE) for p in _OPTIONAL_PATTERNS]
_REQUIRED_RE = [re.compile(p, re.IGNORECASE) for p in _REQUIRED_PATTERNS]
_BOTH_RE = [re.compile(p, re.IGNORECASE) for p in _BOTH_LANGUAGES]

# 分句：句号/问号/感叹号/换行/项目符号都算边界
_SPLIT = re.compile(r"(?<=[.!?;:])\s+|\n+|\r+|•|·|‧|●|▪|•")


@dataclass
class GermanVerdict:
    level: str                       # required | optional | unclear | none
    evidence: list[str] = field(default_factory=list)   # 触发判定的原句，放进日报便于你自己复核
    jd_is_german: bool = False

    @property
    def blocks(self) -> bool:
        return self.level == "required"


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(text or "") if s and s.strip()]


# --------------------------------------------------------------------------
# JD 整体语言判定：德语功能词密度
# --------------------------------------------------------------------------

_DE_STOPWORDS = {
    "und", "oder", "mit", "für", "von", "bei", "auf", "das", "die", "der", "den", "dem",
    "ein", "eine", "einen", "einem", "einer", "nicht", "auch", "wir", "uns", "unsere",
    "unser", "du", "dich", "dein", "deine", "sie", "ihre", "sind", "ist", "hast", "haben",
    "werden", "wird", "sowie", "durch", "als", "im", "zu", "zur", "zum", "an", "aus",
}


def detect_german_jd(text: str, min_words: int = 60, threshold: float = 0.09) -> bool:
    words = re.findall(r"[a-zäöüß]+", (text or "").lower())
    if len(words) < min_words:
        return False
    hits = sum(1 for w in words if w in _DE_STOPWORDS)
    return hits / len(words) >= threshold


# --------------------------------------------------------------------------
# 主判定
# --------------------------------------------------------------------------

def assess_german(text: str) -> GermanVerdict:
    """判断一段 JD 对德语的要求等级。"""
    if not text:
        return GermanVerdict("none")

    jd_is_german = detect_german_jd(text)
    sentences = split_sentences(text)

    required_hits: list[str] = []
    optional_hits: list[str] = []
    unclear_hits: list[str] = []

    for sent in sentences:
        if not _language_related(sent):
            continue

        # 顺序很重要：加分项优先。"gute Deutschkenntnisse von Vorteil" 整句是加分项。
        if any(r.search(sent) for r in _OPTIONAL_RE):
            optional_hits.append(sent)
            continue

        # A1/A2/B1、基础德语 —— 他够得着，不算门槛
        if _LOW_LEVEL.search(sent) and not re.search(r"\b(b2|c1|c2)\b", sent, re.IGNORECASE):
            optional_hits.append(sent)
            continue

        if any(r.search(sent) for r in _REQUIRED_RE) or any(r.search(sent) for r in _BOTH_RE):
            required_hits.append(sent)
            continue

        unclear_hits.append(sent)

    if required_hits:
        return GermanVerdict("required", required_hits[:4], jd_is_german)
    if unclear_hits:
        return GermanVerdict("unclear", unclear_hits[:4], jd_is_german)
    if optional_hits:
        return GermanVerdict("optional", optional_hits[:4], jd_is_german)
    return GermanVerdict("none", [], jd_is_german)
