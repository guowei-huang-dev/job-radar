# job-radar

Pulls job postings every morning at 07:00 → rule-based filtering → scores each one against your profile with an LLM → emails a digest and writes to a local dashboard. **Screens jobs only — it never applies on your behalf.**

```
LinkedIn guest endpoint ┐
                        ├→ SQLite dedupe → title/location filter → language gate → LLM scoring → email + dashboard
Arbeitnow public API    ┘
```

![Job Radar dashboard](docs/screenshot.png)

*The generated analysis text is in Chinese in this screenshot — the output language is set in the `SYSTEM` prompt in `radar/score.py`. See [Changing the output language](#changing-the-output-language).*

Running since September 2026: **7,100+ postings from 2,400+ companies** processed across two sources, narrowed to roughly 260 qualified leads. Every rejected posting keeps the reason it was dropped, so the filter itself can be audited.

Built for a specific situation — Berlin, Werkstudent-level, no German — but the filtering logic is generic. See [Adapting it to your own search](#adapting-it-to-your-own-search).

---

## Quick start

```bash
cd job-radar
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# This command prints nothing on success — that's expected
cp .env.example .env
# Open it and fill in DEEPSEEK_API_KEY and your SMTP details
open -e .env
# Confirm they landed
cat .env

# Start small: fetch only 20 job descriptions, no cost, no email. ~3 minutes.
python -m radar.cli run --dry-run --max-details 20

# Once you can see postings coming in and the filtering looks sane, run the full pipeline
python -m radar.cli run
```

On the first run, set `time_filter` in `config.yaml` to `r604800` (last 7 days) to build up a sample and see how the filter behaves. Switch back to `r86400` (24 hours) once it's tuned.

### How long it takes, and why it looks stuck

The slowness is deliberate. To avoid tripping LinkedIn's IP rate limiting, every request sleeps 3–6 seconds. A full run is roughly:

```
9 keyword groups × up to 4 pages  ≈ 36 listing requests        ≈ 3 minutes
+ 1 detail request per new posting (hundreds on the first run)  ≈ 8–12 minutes
```

So **10–15 minutes for the first full run is normal**. After that only new postings are fetched — usually 2–4 minutes.

The detail-fetching stage prints a progress line per posting (`23/120 ✓ Working Student AI …`), so it no longer goes silent for long stretches.

**You can Ctrl+C at any time.** Records are written to SQLite one at a time, so nothing is lost — the next run picks up where it left off. Use `--max-details N` to cap how many descriptions a single run fetches and leave the rest for later.

---

## How it works

### The language gate

This is the part that has to be right, so it does not do the crude thing of searching the whole description for keywords — that would flag `Deutschland`, `German office` and similar false positives. Instead:

1. **Split into sentences**, and only consider sentences that mention German.
2. **Strip non-language uses** — `German office`, `deutscher Markt`, `Deutsche Bahn` and friends are removed; if the sentence no longer mentions the language afterwards, skip it.
3. **Check for nice-to-have phrasing first** — `von Vorteil`, `nice to have`, `is a plus`, `not required`, `English is our company language` → `optional`, keep the posting.
4. **`A1/A2/B1`, `Grundkenntnisse`, `basic German`** → `optional`, within reach.
5. **Anything left that hits a requirement term** → `required`, drop it. The term list covers:
   - German fluency: `fließend` / `verhandlungssicher` / `in Wort und Schrift` / `muttersprachlich` / `sehr gute Deutschkenntnisse` / `sichere Deutschkenntnisse` / `beherrschen`
   - German requirement verbs: `erforderlich` / `vorausgesetzt` / `Voraussetzung` / `zwingend` / `notwendig` / `unerlässlich` / `mindestens`
   - English: `fluent` / `native` / `business fluent` / `good command of German` / `proficiency` / `required` / `mandatory` / `essential` / `must speak`
   - Levels: `B2` / `C1` / `C2` always count as a requirement
   - Constructions like `Deutsch und Englisch` that ask for both languages
6. **Anything undecidable (`unclear`)** is kept, labelled, and handed to the model to re-check during scoring. If the model returns `german_requirement: required`, it gets dropped too.

After editing the term list, run the corpus test:

```bash
# 39 real-world JD phrasings — all must pass
python tests/test_german.py
# LinkedIn card parsing
python tests/test_parse.py
# Score parsing, including the malformed-response retry path
python tests/test_score.py
```

### Scoring

The model receives `profile.md` plus the full job description and returns a structured result. Score ceilings are tiered by direction — the profile is a career transition, so roles matching the *previous* background are deliberately capped low:

| Direction | Ceiling |
| --- | --- |
| `ai_llm` — AI / LLM application development | 100 |
| `tooling_automation` — internal tooling and automation | 100 |
| `data_mlops` — data engineering / MLOps | 85 |
| `general_swe` — general software engineering | 85 |
| `efficiency` — process / efficiency work | 85 |
| `other` — everything else, including the previous background | 40 |

Two rules sit on top of the ceilings:

- **A German requirement caps the score at 20**, regardless of direction. This is a separate rule from the ceilings, so a perfect-fit AI role that demands C1 German still scores below the reporting threshold.
- **Calibration bands are stated explicitly**: 85+ is "apply now", 70–84 "worth applying", 55–69 "if there's time", below 55 "don't bother". The prompt also instructs the model *not* to cluster everything around 70, which otherwise happens.

The regex verdict from the language gate is passed into the prompt as a hint, along with the sentence that triggered it — the model re-checks rather than trusting it blindly, and a model verdict of `required` overrides a regex verdict of `unclear`.

The reporting threshold is `min_score_to_report` in `config.yaml`, default 55.

### The output schema

Defined as a pydantic model in `radar/score.py`:

| Field | Type | Constraint |
| --- | --- | --- |
| `score` | `int` | 0–100, enforced by pydantic |
| `verdict` | enum | `apply` / `maybe` / `skip` |
| `direction` | enum | the six values above |
| `german_requirement` | enum | `required` / `optional` / `none` / `unclear` |
| `is_student_role` | `bool` | |
| `hours_per_week` | `str` | free text from the posting; `"unknown"` if not stated |
| `one_line` | `str` | one-sentence summary of the role |
| `match_reasons` | `list[str]` | must quote the posting — see note below |
| `gaps` | `list[str]` | what the candidate is missing |
| `red_flags` | `list[str]` | empty array when there are none |

The whole object is stored as JSON in the `analysis` column, and the dashboard renders it when a row is expanded.

Note that the list length limits (`match_reasons` ≤ 4, `gaps` ≤ 3) are **prompt-level instructions, not schema constraints** — pydantic will happily accept longer lists. Add `max_length` to the `Field(...)` declarations if you want them enforced.

`match_reasons` is also instructed to quote the actual wording or tech stack from the posting rather than producing "skills match" boilerplate. That one instruction is what makes the dashboard worth reading.

### Tuning the scoring

Everything lives in `radar/score.py`. Which part to edit depends on what you want to change:

| I want to change… | Edit |
| --- | --- |
| **The score ceiling for a direction** | Rule 1 in the `SYSTEM` prompt |
| **The direction categories themselves** | Three places, in sync — see below |
| **How strict the German penalty is** | Rule 2 in `SYSTEM` (currently: `required` → score ≤ 20) |
| **What kind of work scores well** | Rules 3–4 in `SYSTEM` |
| **The calibration bands** | Rule 5 in `SYSTEM` |
| **The output fields** | The `JobScore` model, the `SCHEMA_EXAMPLE` constant, and the field-constraint list at the end of `SYSTEM` |
| **The language of the generated text** | The output-language line in `SYSTEM` |
| **Which model, temperature, token budget** | `config.yaml` → `scoring`; `max_tokens` is in `Scorer._call` |

#### Adding or removing a direction

This is the one change that breaks if done halfway, because the direction list appears in **three** places that must agree:

1. The `Direction` type alias — pydantic rejects any value not listed here, so a missing entry means every response fails validation.
2. The `direction` line in the field-constraint block at the end of `SYSTEM` — the model only emits values it has been shown.
3. Rule 1 in `SYSTEM`, which assigns the ceiling — a direction with no stated ceiling gets scored arbitrarily.

Also update `SCHEMA_EXAMPLE` if the direction you removed was the one used in the example.

Miss (1) and everything fails validation and gets skipped. Miss (2) and the model never emits the new value. Miss (3) and the scores are meaningless. The symptom differs in each case, which makes it worth changing all three in one pass.

#### Changing the output language

The language of the generated free-text fields is set by one line in `SYSTEM`. Change it and the schema and scoring logic are unaffected — they are language-independent.

Note that `SCHEMA_EXAMPLE` also carries a language: the model follows the example more closely than the instruction, so if the two disagree the output will drift between languages. Keep them consistent, or state in the prompt that the example shows format only.

Existing rows keep whatever language they were scored in. To re-score them:

```bash
python -m radar.cli run --skip-fetch
```

### Why not structured outputs

DeepSeek only supports JSON mode (`response_format={"type": "json_object"}`) — there is no schema-enforced structured output. The API also requires the word "json" and a format example to appear in the prompt, which is why `SCHEMA_EXAMPLE` exists.

So the flow is: show a JSON example in the prompt → validate the response with pydantic → on a validation error, hand the error text back to the model and let it correct itself once → if it still fails, skip that posting and log it.

### Two retry layers

They handle different failures and are easy to confuse:

| Layer | Where | Handles | Behaviour |
| --- | --- | --- | --- |
| Transport | `Scorer._call` | Rate limits, connection errors, empty responses | 3 attempts; 20/40/60s backoff on rate limits, 5/10/15s otherwise |
| Schema | `Scorer.score` | Malformed or invalid JSON | 1 retry, with the validation error fed back into the conversation |

The empty-response case is handled explicitly because DeepSeek's own documentation notes that JSON mode occasionally returns empty content. Responses sometimes also arrive wrapped in a ```` ```json ```` fence despite the instruction not to, so `_strip_fence` removes it before validation.

A posting that fails both layers is skipped rather than stored with a partial result — the row stays unscored and gets picked up on the next run.

After changing any of this:

```bash
python tests/test_score.py
```

The test covers each path with fake responses: valid, fence-wrapped, invalid once then corrected, invalid twice, and empty. If you added a field to `JobScore`, add a case for it.

### Cost

Only postings that survive the hard filters are sent for scoring. Of the Werkstudent roles posted in Berlin on a given day, usually somewhere between a dozen and a few dozen make it past the title, location and language filters. A job description runs about 1.5–2k tokens.

At `deepseek-flash` off-peak pricing ($0.15/1M input, $0.6/1M output), that works out to **roughly $0.02 per day — under a dollar a month**.

Two things worth knowing:

- DeepSeek prices by time of day. Peak hours (Mon–Fri 01:00–04:00 and 06:00–10:00 UTC) cost twice off-peak. The GitHub Actions workflow runs at 05:00 UTC, which falls between the two peaks.
- Cached input is 50× cheaper ($0.003/1M). The system prompt — your profile — is identical on every call and hits the cache automatically, so real spend is usually below the estimate above.

The cost controls in `config.yaml`: `max_jobs_per_run` (how many postings a single run will score), `model` (switch to `deepseek-v4-pro`, ~4× the price, if scoring feels imprecise), and `temperature` (lower from 1.0 to 0.3 for more stable scores).

---

## The dashboard

The dashboard is a local service that runs in the background and **starts on login — no terminal needed**. Open it in a browser (worth bookmarking):

```
http://127.0.0.1:8000
```

It reads the same `jobs.db` the fetcher writes to, so it has no ordering relationship with the email — whenever you open it, you see the current state.

A single large number at the top answers the only question that matters: **how many postings are waiting for you** (above the score threshold and not yet triaged). Four cards below it show shortlisted / applied / interviewing / response rate.

Tabs group postings by application status, each with a count:

| Status | Meaning |
| --- | --- |
| Inbox | Newly fetched, not yet reviewed (the default view — this is the morning view) |
| Shortlisted | Reviewed and worth applying to, application not written yet |
| Applied | Submitted; the date is recorded automatically |
| Interviewing / Rejected | Marked by hand once you hear back |
| Dismissed | Reviewed and not a fit, cleared out of the inbox |
| Filtered | Dropped at the rule stage — **check this periodically to confirm the language gate isn't over-filtering** |

Click any row to expand it: one-line summary, match reasons, gaps, red flags, the evidence behind the German verdict, and a link to the original posting — plus a row of status buttons and a notes box that saves on blur. The filter bar narrows by direction, language status, source and keyword, and sorts by score, date, company or status.

At the bottom, the search-keyword editor adds and removes keywords and writes them back to `config.yaml`. **Changes apply on the next fetch**, not immediately. It only touches the `searches` block; comments and other settings in `config.yaml` are left alone.

### Making it feel like an app

In Chrome: open the dashboard → three-dot menu → Cast, save and share → Create shortcut → tick "Open as window". You get a Dock icon that opens a standalone window with no browser chrome.

### Code changes don't need a restart

The dashboard service runs with `--reload`. uvicorn watches `.py` files under `radar/` and restarts itself on change — **just refresh the browser**. `dashboard.html` is read from disk on every request, so edits take effect on refresh anyway.

Only `.py` files are watched, so the daily writes to `jobs.db` don't trigger restarts. This needs the `watchfiles` package (already in `requirements.txt`).

To restart by hand (new dependency, or the service has wedged):

```bash
launchctl kickstart -k gui/$(id -u)/com.kowai.job-radar-web
```

### When the dashboard won't start

```bash
tail -20 ~/auto_job/job-radar/logs/web.err.log
```

`Address already in use` means something else holds port 8000. Add `--port 8010` to the launch arguments in `scripts/com.kowai.job-radar-web.plist` and reload.

To run it in the foreground for debugging (Ctrl+C to quit):

```bash
cd ~/auto_job/job-radar
.venv/bin/python -m radar.web
```

---

## Configuration

### What to change, and where

| I want to change… | Edit | What happens next |
| --- | --- | --- |
| **What counts as a match** (target roles, skills, hard exclusions) | `profile.md` | Applies on the next fetch |
| **Search keywords** | The keyword editor at the bottom of the dashboard, or `searches` in `config.yaml` | Applies on the next fetch; does not re-fetch immediately |
| **Search location, time window, page depth** | `config.yaml` → `sources.linkedin` | Same |
| **Score threshold** (below which postings stay out of the digest) | `config.yaml` → `scoring.min_score_to_report` | Same |
| **Which model, and what it costs** | `config.yaml` → `scoring.model` | Same |
| **How strict the language gate is** | `config.yaml` → `filters.german`; term list in `radar/german.py` | After editing the term list, run `python tests/test_german.py` |
| **Scoring rules** (per-direction ceilings, calibration, output language) | The `SYSTEM` prompt in `radar/score.py` — see [Tuning the scoring](#tuning-the-scoring) | Applies on the next fetch |
| **What time it runs** | `Hour` / `Minute` in `scripts/com.kowai.job-radar.plist` | Needs a reload — see [Scheduling](#scheduling) |
| **API key, mail password** | `.env` | Immediate |
| **Dashboard appearance** | `radar/static/dashboard.html` | Refresh the browser |

Day to day, only the first two rows move. The rest are set once.

### Applying a change without waiting until 07:00

`config.yaml` and `profile.md` are **read when the fetch job starts** — editing the files on their own triggers nothing. To see the effect immediately, run once by hand:

```bash
launchctl kickstart -p gui/$(id -u)/com.kowai.job-radar
tail -f ~/auto_job/job-radar/logs/daily.log
```

Refresh the dashboard when it finishes. (Ctrl+C out of `tail -f` whenever you like — it doesn't affect the background job.)

After widening the time window or adding keywords, the first run will be noticeably slower: every additional posting needs its full description fetched. Ten to fifteen minutes is normal, and Ctrl+C is safe — the next run continues from where it stopped.

**Don't confuse the two reload mechanisms**: the dashboard's auto-reload only covers Python code under `radar/` (edit, refresh browser, done). It has nothing to do with `config.yaml` or `profile.md` — those require a new fetch run.

---

## Scheduling

### Two background jobs

Two LaunchAgents are installed; both configs live in `scripts/`:

| Label | What it does | When it runs |
| --- | --- | --- |
| `com.kowai.job-radar` | Daily fetch + scoring + email | 07:00 daily; if the Mac was asleep, it runs on wake |
| `com.kowai.job-radar-web` | Dashboard service | Starts on login, stays resident, restarts on crash |

Install both at once:

```bash
cd ~/auto_job/job-radar
./scripts/install-agents.sh
```

The script generates plists with this machine's absolute paths from `scripts/*.plist.template`, installs them into `~/Library/LaunchAgents/` and loads them, handling the asynchronous `bootout` wait along the way. **If you move the project, just re-run this script** — no paths to edit by hand.

It also catches one trap: if the project sits in Desktop, Documents or Downloads it exits with an error telling you to move it (macOS blocks background jobs from those directories — see [Gotchas](#gotchas)).

Confirm both are loaded (you should see two lines):

```bash
launchctl list | grep job-radar
```

Remove everything:

```bash
launchctl bootout gui/$(id -u)/com.kowai.job-radar
launchctl bootout gui/$(id -u)/com.kowai.job-radar-web
rm ~/Library/LaunchAgents/com.kowai.job-radar*.plist
```

After editing either plist you must `bootout`, then `cp`, then `bootstrap`. Editing the file alone changes nothing.

### Option 1: launchd (recommended — runs on your own Mac)

Installation is above. Default is **07:00** local time, daily.

To change the time, edit `Hour` / `Minute` in `scripts/com.kowai.job-radar.plist`, then reload:

```bash
launchctl bootout gui/$(id -u)/com.kowai.job-radar
cp scripts/com.kowai.job-radar.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kowai.job-radar.plist
```

Trigger a run right now (to verify the config without waiting until morning):

```bash
launchctl kickstart -p gui/$(id -u)/com.kowai.job-radar
tail -f ~/auto_job/job-radar/logs/daily.log
```

**Why launchd and not cron**: cron simply skips a scheduled run if the Mac is asleep and never makes it up. launchd's `StartCalendarInterval` runs the missed job once the machine wakes. So if you're still asleep at 07:00 and open the laptop at 09:00, it runs at 09:00 and the results are in your inbox by the time the coffee is ready.

This assumes the Mac is powered on and you're logged in — a LaunchAgent runs inside the user session. Nothing runs while the machine is fully shut down; it catches up at the next login.

Logs are in `logs/daily.log`, trimmed to the last 2000 lines.

### Option 2: GitHub Actions (runs with the laptop closed)

`.github/workflows/daily.yml` is ready to go: weekdays at 05:00 UTC (07:00 Berlin), committing `jobs.db` and `reports/` back to the repository.

This requires pushing the project as a **private repository** (it holds your job-search data), then adding these under **Settings → Secrets and variables → Actions**: `DEEPSEEK_API_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM`, `MAIL_TO`.

⚠️ **The real risk**: GitHub Actions egress IPs sit in Azure datacenter ranges, and LinkedIn throttles those far more aggressively than residential connections. Expect widespread 429/999 responses and little data. Treat this as a supplement to launchd — for a week away from the machine, say — rather than the primary path.

For Gmail, `SMTP_PASS` must be an **app-specific password** (the 16-character one generated after enabling two-factor auth), not your account password.

Measured in practice: a scheduled run from GitHub Actions ran for 43 minutes and returned nothing. The `schedule` trigger is disabled in `daily.yml` for this reason; `workflow_dispatch` is kept for manual runs.

---

## Gotchas

Four things already learned the hard way.

### 1. The project cannot live in Desktop, Documents or Downloads

macOS TCC treats those three directories as protected. Running from a terminal is fine (Terminal has been granted access), but **jobs launched by launchd are denied**, with errors like:

```
shell-init: error retrieving current directory: getcwd: cannot access parent directories: Operation not permitted
/bin/bash: .../run-daily.sh: Operation not permitted
```

So the project lives at `~/auto_job/job-radar` — under the home directory, unprotected. If you want it reachable from the Desktop, symlink it rather than moving the folder back:

```bash
ln -s ~/auto_job ~/Desktop/auto_job
```

**If you ever relocate the project**, three absolute paths need updating in step: `scripts/run-daily.sh`, `scripts/com.kowai.job-radar.plist` and `scripts/com.kowai.job-radar-web.plist` — then reload both agents. (Or just re-run `install-agents.sh`, which does it for you.)

### 2. Don't paste trailing comments into zsh

Interactive zsh **does not treat `#` as a comment by default**, so `grep foo   # explanation` passes `#` and the explanation to grep as filenames. Every comment in this file is written on the line above its command, so pasting a whole block is safe.

### 3. Route handler annotations can't use `int | None`

This runs on Python 3.9, and `X | None` needs 3.10. Ordinary functions get away with it because `from __future__ import annotations` means the annotation is never evaluated — but **FastAPI force-evaluates route handler annotations**, so it crashes immediately. Use `Optional[int]` in routes.

### 4. Wait between `bootout` and `bootstrap` when reinstalling a LaunchAgent

`bootout` is asynchronous; returning doesn't mean the unload finished. Calling `bootstrap` straight after fails with `Bootstrap failed: 5: Input/output error`, meaning "that label is still registered". Put a `sleep 3` in between:

```bash
launchctl bootout gui/$(id -u)/com.kowai.job-radar-web 2>/dev/null
sleep 3
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kowai.job-radar-web.plist
launchctl list | grep job-radar
```

The last line has to show a record with a PID for the install to have worked.

---

## Adapting it to your own search

**The filtering logic is generic; the configuration is tuned for Berlin + no German + Werkstudent roles.** Change these four things, in order:

| File | What to change |
| --- | --- |
| `profile.md` | Replace it entirely. Copy `profile.example.md` and fill it in — this is the only thing scoring is measured against |
| `config.yaml` → `location` | Your city, e.g. `"Munich, Germany"` / `"Amsterdam, Netherlands"` |
| `config.yaml` → `searches` | Keywords for your direction. Keep German terms in German-speaking regions; use English everywhere else |
| `radar/score.py` → `SYSTEM` | The per-direction ceilings were written around one person's career transition — rewrite them for yours. See [Tuning the scoring](#tuning-the-scoring) |

**Whether to keep the language gate (`radar/german.py`):**

- Job hunting in Germany without German → keep it. It's the most valuable part of the system.
- Fluent in German → set `filters.german.drop_when_required` to `false` in `config.yaml`; verdicts are then labelled but nothing is dropped.
- Not in a German-speaking region → ignore it. Job descriptions won't mention German, the verdict will always be `none`, and nothing is affected.

You'll also need your own **DeepSeek API key** (billed separately — don't share one) and your own **SMTP app-specific password**.

Once installed, do a dry run first — `python -m radar.cli run --dry-run --max-details 20` — check that the filtering looks right, then enable the scheduled job.

---

## Compliance notes

Arbeitnow is used through its official public JSON API — fully sanctioned, and worth keeping enabled as a fallback source.

LinkedIn is accessed only through the unauthenticated **guest endpoint**: no login, no authentication bypass, public data only — so there is no risk to a LinkedIn account. Requests are rate-limited to one every 3–6 seconds by design; **do not lower this**. Scraping is nonetheless outside LinkedIn's terms of use, so this tool is intended for personal, non-commercial job search. The collected data is never published — `jobs.db` is gitignored for exactly that reason.

The practical risk sits at the IP layer (429 / 999 responses). Since the guest endpoint is undocumented, parsing will break whenever LinkedIn changes its markup. Every parsing step is wrapped in its own try/except, so a failure skips that record and logs it rather than taking down the run — but the digest will suddenly come back empty, and that's the signal to run `tests/test_parse.py`.

---

## Common commands

```bash
# Fetch and filter only — no spend, no email
python -m radar.cli run --dry-run
# Quick trial run, about 3 minutes
python -m radar.cli run --dry-run --max-details 20
# Don't fetch anything new; just score what's already in the database
python -m radar.cli run --skip-fetch
# Score and write to reports/, but don't send mail
python -m radar.cli run --no-mail
# Verbose logging
python -m radar.cli run -v
# What's in the database
python -m radar.cli stats
sqlite3 jobs.db "select title, drop_reason from jobs where stage='filtered' limit 20;"
```

That last one earns its keep: **look through the dropped postings periodically** to confirm the language gate isn't over-filtering.

---

## Requirements

Python 3.9 or newer (the macOS system 3.9 works — it just emits a harmless `NotOpenSSLWarning`). To silence it, install a newer build: `brew install python@3.12`, then recreate the venv.

The `openai` package in the dependency list is not there to call OpenAI. DeepSeek's API is OpenAI-compatible and that SDK is the officially recommended client; `base_url` simply points at `api.deepseek.com`. `radar/score.py` is the only file in the project that makes a network call to a model.

---

## License

MIT — see [`LICENSE`](LICENSE). For personal, non-commercial use. Respect the terms of whatever sources you configure.
