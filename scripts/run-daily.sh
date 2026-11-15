#!/bin/bash
# job-radar 每日任务。由 launchd 调用，也可以手动运行测试。
set -u
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"   # 脚本自己定位项目根目录
cd "$PROJECT" || exit 1

mkdir -p logs
echo "=========== $(date '+%Y-%m-%d %H:%M:%S') 开始 ===========" >> logs/daily.log

# 用 venv 里的 python，不依赖 PATH（launchd 的 PATH 跟终端不一样）
"$PROJECT/.venv/bin/python" -m radar.cli run >> logs/daily.log 2>&1
code=$?

echo "=========== 结束，退出码 $code ===========" >> logs/daily.log

# 只保留最近 2000 行日志，免得越滚越大
tail -n 2000 logs/daily.log > logs/daily.log.tmp && mv logs/daily.log.tmp logs/daily.log
exit $code
