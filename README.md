# job-radar

每天早上 7 点自动抓 LinkedIn 岗位 → 规则过滤 → DeepSeek 对照你的画像打分 →
发邮件日报 + 写进本地面板。**只筛选，不自动投递。**

```
LinkedIn guest 接口 ┐
                    ├→ SQLite 去重 → 标题/地点过滤 → 德语门槛 → DeepSeek 打分 → 邮件 + 面板
Arbeitnow 公开 API  ┘
```

---

## 想改什么，去改哪个文件

| 我想改…                   | 改这里                                                    | 改完要做什么                              |
| ---------------------- | ------------------------------------------------------ | ----------------------------------- |
| **匹配口味**（求职方向、技能、硬性排除） | `profile.md`                                           | 下次抓取自动生效                            |
| **搜索关键词**              | 面板底部「搜索关键词设置」，或 `config.yaml` 的 `searches`             | 下次抓取生效，不会立刻重抓                       |
| **搜索地点、时间范围、翻几页**      | `config.yaml` → `sources.linkedin`                     | 同上                                  |
| **分数线**（低于多少不进日报）      | `config.yaml` → `scoring.min_score_to_report`          | 同上                                  |
| **用哪个模型、贵不贵**          | `config.yaml` → `scoring.model`                        | 同上                                  |
| **德语门槛严格程度**           | `config.yaml` → `filters.german`；词表在 `radar/german.py` | 改词表后跑 `python tests/test_german.py` |
| **打分规则**（各方向的分数上限）     | `radar/score.py` 里的 `SYSTEM` 提示词                       | 下次抓取生效                              |
| **每天几点跑**              | `scripts/com.kowai.job-radar.plist` 的 `Hour`/`Minute`  | 要重新加载，见「定时」章节                       |
| **API key、邮箱密码**       | `.env`                                                 | 立刻生效                                |
| **面板长相**               | `radar/static/dashboard.html`                          | 刷新浏览器即可                             |

日常只会动前两行。其余基本一次配好不用管。

### 改完想立刻生效，不想等到明早 7 点

`config.yaml` 和 `profile.md` 都是**抓取任务启动时才读**的，改完文件本身不会触发任何事。
想马上看到效果就手动跑一次：

```bash
launchctl kickstart -p gui/$(id -u)/com.kowai.job-radar
tail -f ~/auto_job/job-radar/logs/daily.log
```

跑完刷新面板即可。（`tail -f` 看腻了按 `Ctrl+C` 退出，不影响后台任务。）

放宽了时间窗或加了关键词之后，第一次跑会明显变慢 —— 多出来的岗位都要逐个抓 JD 全文，
十几分钟很正常，中途 `Ctrl+C` 也没关系，下次接着抓。

**注意区分**：面板的自动重载只管 `radar/` 下的 Python 代码（改了刷新浏览器就行），
跟 `config.yaml` / `profile.md` 无关 —— 那两个必须重新跑抓取。

---

## ⚠️ 三个已经踩过的坑，别再踩

### 1. 项目不能放在「桌面 / 文稿 / 下载」里

macOS 的 TCC 机制把这三个目录列为受保护区域。你在终端里跑没问题（Terminal 有授权），
但 **launchd 启动的后台任务会被拒绝**，报错长这样：

```
shell-init: error retrieving current directory: getcwd: cannot access parent directories: Operation not permitted
/bin/bash: .../run-daily.sh: Operation not permitted
```

所以项目放在 `~/auto_job/job-radar`（主目录下，不受保护）。
想在桌面上留个入口，建软链接而不是把文件夹搬回去：

```bash
ln -s ~/auto_job ~/Desktop/auto_job
```

**如果哪天要移动项目位置**，记得同步改这三处的绝对路径：
`scripts/run-daily.sh`、`scripts/com.kowai.job-radar.plist`、
`scripts/com.kowai.job-radar-web.plist`，然后重新加载两个后台任务。

### 2. 从 README 复制命令时，别把行尾注释一起粘进 zsh

zsh 交互式默认**不把 `#` 当注释**，`grep foo   # 说明` 会把 `#` 和说明当成文件名传给 grep。
本文件里的注释都写在命令上一行，整段粘贴是安全的。

### 3. 路由函数的类型注解不能写 `int | None`

你的 Python 是 3.9，而 `X | None` 要 3.10。普通函数因为有
`from __future__ import annotations` 不会真的求值所以没事，
但 **FastAPI 会强制求值路由函数的注解**，一写就崩。路由里统一用 `Optional[int]`。

### 4. 重装 LaunchAgent 时 `bootout` 和 `bootstrap` 之间要等一下

`bootout` 是异步的，返回了不代表卸载干净。紧接着 `bootstrap` 会报
`Bootstrap failed: 5: Input/output error`，意思是"这个标签还在"。中间加一句 `sleep 3`：

```bash
launchctl bootout gui/$(id -u)/com.kowai.job-radar-web 2>/dev/null
sleep 3
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kowai.job-radar-web.plist
launchctl list | grep job-radar
```

最后一行要能看到带进程号的记录才算成功。

---

## 快速开始

```bash
cd job-radar
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 这条命令成功时没有任何输出，这是正常的
cp .env.example .env
# 用文本编辑器打开，填 DEEPSEEK_API_KEY 和 SMTP 信息
open -e .env
# 确认填进去了
cat .env

# 第一次先小步试：只抓 20 个 JD，不花钱、不发邮件，大约 3 分钟
python -m radar.cli run --dry-run --max-details 20

# 确认抓得到东西、过滤合理之后，跑完整流程
python -m radar.cli run
```

第一次跑建议先把 `config.yaml` 里的 `time_filter` 改成 `r604800`（过去 7 天），
攒一批数据看看过滤效果，调好了再改回 `r86400`（24 小时）。

### 它要跑多久？为什么看起来像卡住了

慢是故意的。为了不触发 LinkedIn 的 IP 限速，每个请求之间要 sleep 3–6 秒，
而一次完整运行的请求数大致是：

```
9 组关键词 × 最多 4 页  ≈ 36 次列表请求   ≈ 3 分钟
+ 每个新岗位 1 次详情请求（首次跑可能上百个）≈ 8–12 分钟
```

所以**首次全量跑 10–15 分钟是正常的**，之后每天只有新增岗位，通常 2–4 分钟。

补抓 JD 那一段现在每条都有一行进度输出（`23/120 ✓ Working Student AI …`），
不会再出现长时间没动静的情况。

**随时可以 Ctrl+C 停掉**，数据是逐条写进 SQLite 的，不会丢；下次运行会自动
从没抓完的地方接着补。用 `--max-details N` 可以限制单次最多抓多少个 JD，
剩下的留到下次。

---

## 面板

面板是一个常驻后台的本地服务，**开机自启，不用每次开终端**。
浏览器打开下面这个地址就行（建议存成书签）：

```
http://127.0.0.1:8000
```

面板读的是抓取脚本写的同一个 `jobs.db`，所以跟邮件没有先后关系 —— 任何时候打开，
看到的都是那一刻的最新数据。

顶上一个大数字回答唯一重要的问题：**还有几个岗位等你处理**（分数过线、且你还没标记过的）。
下面四张卡是想投 / 已投递 / 面试中 / 回复率。

标签页按申请状态分类，每个都带数量：

| 状态       | 含义                           |
| -------- | ---------------------------- |
| 待处理      | 新抓到、还没看过的（默认视图，早上就看这个）       |
| 想投       | 看过了觉得值得投，但还没写申请              |
| 已投       | 投出去了，会自动记录投递日期               |
| 面试中 / 被拒 | 有回音之后手动标                     |
| 忽略       | 看过觉得不合适，从待处理里清掉              |
| 被过滤      | 规则阶段就被淘汰的，**定期翻一下确认德语门槛没误杀** |

点表格任意一行展开详情：一句话概括、匹配理由、欠缺、红旗、德语判定依据、原岗位链接，
以及一排状态按钮和一个备注框（失焦自动保存）。筛选栏可以按方向、德语状态、来源、
关键词过滤，并按分数/日期/公司/状态排序。

底部「搜索关键词设置」可以增删关键词并写回 `config.yaml`。**下次抓取才生效**，
不会立刻重新抓。这里只改 `searches` 那几行，`config.yaml` 里的注释和其它配置不受影响。

### 想让它更像个 App

Chrome 打开面板 → 右上角三个点 → 投放、保存和分享 → 创建快捷方式 → 勾「在窗口中打开」。
Dock 里会多一个图标，点开是独立窗口，看不出是浏览器。

### 改了代码不用重启

面板服务带 `--reload`，uvicorn 会盯着 `radar/` 下的 `.py` 文件，一有改动自己重启，
**浏览器刷新即可**。`dashboard.html` 本来就是每次请求从磁盘读，改了直接刷新生效。

只监听 `.py`，所以每天写 `jobs.db` 不会触发重启。需要 `watchfiles` 包（已在
requirements.txt 里）。

真要手动重启（换了依赖、服务卡死）：

```bash
launchctl kickstart -k gui/$(id -u)/com.kowai.job-radar-web
```

### 面板起不来的时候

```bash
tail -20 ~/auto_job/job-radar/logs/web.err.log
```

出现 `Address already in use` 说明 8000 端口被别的程序占了，
改 `scripts/com.kowai.job-radar-web.plist` 里的启动参数加 `--port 8010`，重新加载即可。

手动前台启动（调试用，Ctrl+C 退出）：

```bash
cd ~/auto_job/job-radar
.venv/bin/python -m radar.web
```

---

## 德语门槛是怎么判的

这是整个过滤里最需要准的一环，所以没有用"整篇 JD 搜关键词"的糙办法
（那样 `Deutschland`、`German office` 都会误伤）。做法是：

1. **分句**，只看提到德语的句子
2. **剔除非语言用法** —— `German office` / `deutscher Markt` / `Deutsche Bahn` 这类被抹掉后如果整句不再提德语，就跳过
3. **加分项优先判定** —— `von Vorteil`、`nice to have`、`is a plus`、`not required`、
   `English is our company language` → `optional`，保留
4. **A1/A2/B1、Grundkenntnisse、basic German** → `optional`，你够得着
5. **剩下的命中要求词** → `required`，直接丢弃。词表覆盖：
   - 德语流利度：`fließend` / `verhandlungssicher` / `in Wort und Schrift` / `muttersprachlich` /
     `sehr gute Deutschkenntnisse` / `sichere Deutschkenntnisse` / `beherrschen`
   - 德语要求动词：`erforderlich` / `vorausgesetzt` / `Voraussetzung` / `zwingend` /
     `notwendig` / `unerlässlich` / `mindestens`
   - 英语：`fluent` / `native` / `business fluent` / `good command of German` /
     `proficiency` / `required` / `mandatory` / `essential` / `must speak`
   - 等级：`B2` / `C1` / `C2` 一律算要求
   - `Deutsch und Englisch` 这种"两种语言都要"的句式
6. **判断不了的（unclear）** 不丢，标注后交给模型在打分时复核；
   模型给出 `german_requirement: required` 的同样丢掉

改了词表之后跑一遍语料测试：

```bash
# 39 条真实 JD 写法，全过才算没改坏
python tests/test_german.py
# LinkedIn 卡片解析
python tests/test_parse.py
# 打分结果的解析与纠错重试
python tests/test_score.py
```

---

## 打分口径

DeepSeek 拿到 `profile.md` + JD 全文，返回结构化结果（分数 / 方向 / 德语要求 /
匹配理由 / 欠缺 / 红旗）。分数上限按方向分层：

| 方向                         | 上限  |
| -------------------------- | --- |
| AI / LLM 应用开发、内部工具与自动化     | 100 |
| 数据工程 / MLOps、通用软件工程        | 85  |
| 其他（含供应链、SAP、PM 这类旧背景对口的岗位） | 40  |

最后一行是刻意的：你在主动转向 AI 方向，不希望旧背景把这类岗位顶上来。
想改回去就改 `radar/score.py` 里的 `SYSTEM` 提示词，以及 `profile.md`。

分数线在 `config.yaml` 的 `min_score_to_report`，默认 55。

### 为什么不用 structured outputs

DeepSeek 只支持 JSON 模式（`response_format={"type": "json_object"}`），没有强制
schema 的结构化输出。所以流程是：提示词里给出 JSON 样例 → 拿到结果用 pydantic
校验 → 不合规就把报错甩回去让它改一次 → 还不行就跳过这条。

`tests/test_score.py` 用假响应覆盖了这几条路径（正常 / 带 ```json 包裹 /
首次不合规 / 连续不合规 / 空响应），改提示词后跑一遍。

---

## 成本

只有通过硬过滤的岗位才会送去打分。柏林每天新增的 Werkstudent 岗位过完
标题/地点/德语三道过滤，通常剩十几到几十条，一条 JD 大约 1.5–2k token。

按 `deepseek-flash`（非高峰期 $0.15/1M 输入、$0.6/1M 输出）估算，
**每天大约 $0.02，一个月不到 1 美元**。

两点值得知道：

- DeepSeek 按时段浮动计价，高峰期（UTC 周一到周五 01:00–04:00、06:00–10:00）
  是非高峰的两倍。GitHub Actions 定在 05:00 UTC 跑，正好卡在两个高峰之间。
- 命中缓存的输入便宜 50 倍（$0.003/1M）。系统提示词（你的 profile）每次都一样，
  会自动命中缓存，所以实际花费通常比上面估的还低。

`config.yaml` 里的闸门：`max_jobs_per_run`（单次最多打分几条）、
`model`（觉得打分不准就换成 `deepseek-v4-pro`，贵约 4 倍）、
`temperature`（想让分数更稳定就从 1.0 调到 0.3）。

---

## 定时

### 两个后台任务

项目装了两个 LaunchAgent，配置文件都在 `scripts/` 里：

| Label                     | 干什么             | 何时运行                |
| ------------------------- | --------------- | ------------------- |
| `com.kowai.job-radar`     | 每日抓取 + 打分 + 发邮件 | 每天 07:00，睡眠错过则唤醒后补跑 |
| `com.kowai.job-radar-web` | 面板服务            | 登录即启动，常驻；崩溃自动重启     |

一次装好两个：

```bash
cd ~/auto_job/job-radar
./scripts/install-agents.sh
```

脚本会从 `scripts/*.plist.template` 生成带本机绝对路径的 plist、装进
`~/Library/LaunchAgents/` 并加载，中间自动处理 `bootout` 的异步等待。
**项目换了位置之后重跑一次这个脚本就行**，不用手改任何路径。

它还会拦住一个坑：如果项目在 桌面/文稿/下载 里，直接报错退出并提示你搬走
（那三个目录 macOS 不让后台任务访问，见开头的踩坑清单）。

确认都装上了（应该看到两行）：

```bash
launchctl list | grep job-radar
```

全部卸载：

```bash
launchctl bootout gui/$(id -u)/com.kowai.job-radar
launchctl bootout gui/$(id -u)/com.kowai.job-radar-web
rm ~/Library/LaunchAgents/com.kowai.job-radar*.plist
```

改了任何一个 plist 之后，必须先 `bootout` 再 `cp` 再 `bootstrap`，
光改文件不重新加载是不生效的。

### 方式一：launchd（推荐，跑在你自己的 Mac 上）

安装命令见上面「两个后台任务」。默认每天本地时间 **07:00** 跑一次。

改时间：编辑 `scripts/com.kowai.job-radar.plist` 里的 `Hour` / `Minute`，然后重新加载：

```bash
launchctl bootout gui/$(id -u)/com.kowai.job-radar
cp scripts/com.kowai.job-radar.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kowai.job-radar.plist
```

立刻触发一次（不用等到明早，用来验证配置对不对）：

```bash
launchctl kickstart -p gui/$(id -u)/com.kowai.job-radar
tail -f ~/auto_job/job-radar/logs/daily.log
```

**为什么用 launchd 不用 cron**：cron 在 Mac 合盖休眠时会直接跳过，醒来也不补；
launchd 的 `StartCalendarInterval` 会在唤醒后补跑一次。也就是说你 7 点还在睡、
9 点打开电脑，它 9 点会自己跑，你泡杯咖啡的功夫结果就到邮箱了。

前提是 Mac 开着机并且你已登录（LaunchAgent 跑在用户会话里）。彻底关机期间不会跑，
下次开机登录后补。

日志在 `logs/daily.log`，只保留最近 2000 行。

### 方式二：GitHub Actions（电脑关着也能跑）

`.github/workflows/daily.yml` 已经配好：工作日 05:00 UTC（柏林 07:00）跑一次，
把 `jobs.db` 和 `reports/` 提交回仓库。

需要先把项目推成 **私有仓库**（里面有你的求职数据），然后在
**Settings → Secrets and variables → Actions** 里加：
`DEEPSEEK_API_KEY`、`SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASS`、
`MAIL_FROM`、`MAIL_TO`。

⚠️ **实际风险**：GitHub Actions 的出口 IP 属于 Azure 数据中心段，LinkedIn 对这类
IP 的限流比家庭宽带严厉得多，很可能大面积吃 429/999 而抓不到东西。
所以这条路更适合作为 launchd 的补充（比如你要出门一周），不建议当主力。

Gmail 的 `SMTP_PASS` 要用**应用专用密码**（开两步验证后生成的 16 位），不是登录密码。

---

## 给别人用要改什么

这套系统的**筛选逻辑是通用的，配置是为柏林 + 无德语 + Werkstudent 定制的**。
换个人用，按顺序改这四处：

| 文件 | 改什么 |
|---|---|
| `profile.md` | 整份换掉。复制 `profile.example.md` 照着填，这是打分的唯一基准 |
| `config.yaml` → `location` | 换成他的城市，如 `"Munich, Germany"` / `"Amsterdam, Netherlands"` |
| `config.yaml` → `searches` | 换成他方向的关键词，德语区留德语词，其它地区全用英语 |
| `radar/score.py` → `SYSTEM` | 各方向的分数上限是照着某个人的转型路径写的，得按他的情况重写 |

**德语门槛（`radar/german.py`）是否保留**，看他的情况：

- 在德国求职但不会德语 → 保留，这是整套系统最值钱的部分
- 德语流利 → 把 `config.yaml` 里 `filters.german.drop_when_required` 改成 `false`，
  判定结果只标注不丢弃
- 不在德语区 → 整段可以不管，JD 里不会提德语，判定结果永远是 `none`，不影响任何东西

他还需要自己的 **DeepSeek API key**（各自计费，别共用）和自己的 **SMTP 应用专用密码**。

装好之后建议先 `python -m radar.cli run --dry-run --max-details 20` 空跑一次，
看看过滤效果对不对，再开定时任务。

---

## 推到 GitHub

`.gitignore` 已经排除了不该提交的东西：`.env`（密钥）、`*.db`（你的求职数据，含 JD
全文和申请备注）、`logs/`、`reports/`，以及由模板生成、含本机绝对路径的 `scripts/*.plist`。

```bash
cd ~/auto_job/job-radar
git init
git add .
git status          # 提交前务必扫一眼这个清单
git commit -m "job-radar: 柏林 Werkstudent 岗位筛选与打分"
git remote add origin git@github.com:<你的用户名>/job-radar.git
git push -u origin main
```

**建议设成私有仓库**。`profile.md` 是你的求职画像，`config.yaml` 里是你的搜索策略，
虽然不含密钥，但都是个人信息。想公开当作品集的话，先把 `profile.md` 换成一份示例。

`git status` 里如果看到 `.env`、`jobs.db`、`logs/`、`reports/` 中的任何一个，
**先别提交**，说明 `.gitignore` 没生效（多半是这些文件在加 `.gitignore` 之前
就被 `git add` 过了）。

---

## 合规与风险

- 脚本**不登录** LinkedIn，只请求公开的 guest 接口，所以**没有账号被封的风险**。
- 但 LinkedIn 的用户协议本身是禁止自动化抓取的，公开页面也不例外。
  实际风险落在 IP 层（429 / 999），所以 `request_delay` 默认 3–6 秒，**不要往下调**。
- guest 接口是非公开接口，LinkedIn 改版时解析会失效。每个解析步骤都是独立
  try/except，失效时会跳过并记日志，不会让整个脚本崩 —— 但日报会突然变空，
  那就是该跑 `tests/test_parse.py` 的信号。
- Arbeitnow 是官方开放的免费 JSON API，完全合规，建议保持开启作为兜底源。

---

## 常用命令

```bash
# 只抓取+过滤，不花钱不发信
python -m radar.cli run --dry-run
# 快速试跑，约 3 分钟
python -m radar.cli run --dry-run --max-details 20
# 不抓新的，只对库里已有数据打分
python -m radar.cli run --skip-fetch
# 打分并写 reports/，但不发邮件
python -m radar.cli run --no-mail
# 详细日志
python -m radar.cli run -v
# 库里有多少数据
python -m radar.cli stats
sqlite3 jobs.db "select title, drop_reason from jobs where stage='filtered' limit 20;"
```

最后一条很有用：**定期看一眼被丢掉的岗位**，确认德语门槛没有误杀。

## 环境要求

Python 3.9 以上都能跑（macOS 自带的 3.9 可以，只是会有一条 `NotOpenSSLWarning`
的无害警告）。想消掉警告就装个新版：`brew install python@3.12`，然后重新建 venv。

依赖里的 `openai` 这个包不是用来调 OpenAI 的 —— DeepSeek 的接口是 OpenAI 兼容
格式，官方推荐就用这个 SDK，只是把 `base_url` 指到 `api.deepseek.com`。
全流程只有 `radar/score.py` 一个文件会联网调模型。
