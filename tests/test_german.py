"""德语门槛判定的语料测试。

语料都是德国 JD 里真实出现的写法。改了 german.py 的词表后跑一遍这个文件。
    python -m tests.test_german
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from radar.german import assess_german  # noqa: E402

# ---------------------------------------------------------------- 必须判 required
REQUIRED = [
    "Fließende Deutschkenntnisse in Wort und Schrift",
    "Verhandlungssicheres Deutsch und gutes Englisch",
    "Sehr gute Deutsch- und Englischkenntnisse runden dein Profil ab.",
    "Du sprichst Deutsch auf mindestens B2-Niveau.",
    "Gute Deutschkenntnisse sind erforderlich.",
    "Wir setzen sehr gute Deutschkenntnisse voraus.",
    "Sichere Deutschkenntnisse werden vorausgesetzt.",
    "Muttersprachliche Deutschkenntnisse",
    "Deutsch als Muttersprache oder auf muttersprachlichem Niveau",
    "Deutschkenntnisse sind zwingend notwendig",
    "You are fluent in German and English.",
    "Fluency in German (C1) is required for this role.",
    "German language skills are mandatory.",
    "Excellent command of German, both written and spoken",
    "Good German skills",
    "Professional working proficiency in German",
    "You must speak German.",
    "Native-level German speaker",
    "Business fluent German is essential",
    "Strong German and English communication skills",
    "Deutsch und Englisch fließend in Wort und Schrift",
    "Very good knowledge of German (min. B2)",
]

# ---------------------------------------------------------------- 必须判 optional（保留）
OPTIONAL = [
    "Deutschkenntnisse sind von Vorteil.",
    "Gute Deutschkenntnisse sind von Vorteil, aber kein Muss.",
    "German language skills are a plus.",
    "German is a nice-to-have.",
    "Knowledge of German would be an advantage.",
    "English is our company language, German is not required.",
    "Basic German (A2) is enough — we will support you learning more.",
    "Grundkenntnisse in Deutsch",
    "Willingness to learn German",
    "Deutschkenntnisse wünschenswert",
    "Our working language is English; German skills are welcome but optional.",
    "German A2 or above",
]

# ---------------------------------------------------------------- 必须判 none（根本没提语言要求）
NONE = [
    "We are a startup based in Berlin, Deutschland, building developer tools.",
    "You will work closely with our German office and international teams.",
    "Experience with the German market is helpful for understanding our users.",
    "Deutsche Bahn is one of our enterprise customers.",
    "You will build RAG pipelines with Python and LangChain.",
]


def run() -> int:
    failures = []

    for text, want in (
        [(t, "required") for t in REQUIRED]
        + [(t, "optional") for t in OPTIONAL]
        + [(t, "none") for t in NONE]
    ):
        got = assess_german(text)
        if got.level != want:
            failures.append((text, want, got.level, got.evidence))

    total = len(REQUIRED) + len(OPTIONAL) + len(NONE)
    print(f"{total - len(failures)}/{total} passed")
    for text, want, got, ev in failures:
        print(f"\n  FAIL  want={want:9s} got={got:9s}")
        print(f"        {text}")
        if ev:
            print(f"        evidence: {ev}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
