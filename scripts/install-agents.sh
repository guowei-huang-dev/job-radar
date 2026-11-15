#!/bin/bash
# 安装/重装两个 LaunchAgent。从 .plist.template 生成本机路径的 plist 再加载。
# 项目挪了位置之后重跑一次就行。
set -eu

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
UID_="$(id -u)"

case "$PROJECT" in
  "$HOME"/Desktop/*|"$HOME"/Documents/*|"$HOME"/Downloads/*)
    echo "✗ 项目在 桌面/文稿/下载 里，macOS 会拒绝后台任务访问这些目录。"
    echo "  把项目挪到主目录下（如 ~/auto_job/job-radar）再运行本脚本。"
    exit 1 ;;
esac

mkdir -p "$AGENTS" "$PROJECT/logs"

for label in com.kowai.job-radar com.kowai.job-radar-web; do
  tpl="$PROJECT/scripts/$label.plist.template"
  out="$PROJECT/scripts/$label.plist"
  [ -f "$tpl" ] || { echo "✗ 缺少模板 $tpl"; exit 1; }

  sed "s|{{PROJECT}}|$PROJECT|g" "$tpl" > "$out"
  plutil -lint "$out" >/dev/null || { echo "✗ 生成的 plist 格式有问题：$out"; exit 1; }
  cp "$out" "$AGENTS/$label.plist"

  # bootout 是异步的，不等一下紧接着 bootstrap 会报 Input/output error
  launchctl bootout "gui/$UID_/$label" 2>/dev/null || true
  sleep 3
  launchctl bootstrap "gui/$UID_" "$AGENTS/$label.plist"
  echo "✓ $label"
done

echo
echo "当前状态（第一列是进程号，面板服务应该有，定时任务没有是正常的）："
launchctl list | grep job-radar
echo
echo "面板： http://127.0.0.1:8000"
