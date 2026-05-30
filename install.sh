#!/bin/bash
set -e
# File Organizer v4.1 — 一键安装脚本
# 用法: git clone <repo> && cd file-organizer && bash install.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$HOME/.file-organizer"
LAUNCHD_DIR="$HOME/Library/LaunchAgents"
WEB_PLIST="$LAUNCHD_DIR/com.fileorganizer.web.plist"
WATCH_PLIST="$LAUNCHD_DIR/com.fileorganizer.watch.plist"

echo "╔══════════════════════════════════════╗"
echo "║  File Organizer v4.1                 ║"
echo "║  智能文件管家 — 零操作自动整理         ║"
echo "╚══════════════════════════════════════╝"

# 1. 停止旧服务
echo "[1/6] 停止旧服务..."
launchctl unload "$WATCH_PLIST" 2>/dev/null || true
launchctl unload "$WEB_PLIST" 2>/dev/null || true
pkill -f "app_web.py" 2>/dev/null || true
pkill -f "launcher.py" 2>/dev/null || true
lsof -ti:8899 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

# 2. 安装依赖
echo "[2/6] 安装 Python 依赖..."
python3 -m pip install --quiet jieba 2>/dev/null || true

# 3. 部署文件
echo "[3/6] 部署引擎文件..."
mkdir -p "$APP_DIR"/{src,data,logs}
cp "$SCRIPT_DIR/src/engine.py" "$APP_DIR/src/"
cp "$SCRIPT_DIR/src/app_web.py" "$APP_DIR/src/"
cp "$SCRIPT_DIR/src/launcher.py" "$APP_DIR/src/"

# 创建 launchd 入口脚本
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/file-organizer" << 'PYEOF'
#!/usr/bin/env python3
"""File Organizer — launchd 入口"""
import sys, os
sys.path.insert(0, os.path.expanduser("~/.file-organizer/src"))
from engine import get_organizer, log
try:
    org = get_organizer()
    decisions = org.organize_watched()
    org.desktop_cleanup()
    log.info(f"launchd: {len(decisions)} 项已处理")
except Exception as e:
    log.error(f"launchd: {e}")
PYEOF
chmod +x "$HOME/.local/bin/file-organizer"

mkdir -p "$HOME/.local/log"

# 4. 写入 launchd 配置
echo "[4/6] 配置后台服务..."

# WatchPaths 文件监控
cat > "$WATCH_PLIST" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.fileorganizer.watch</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>$HOME/.local/bin/file-organizer</string>
    </array>
    <key>WatchPaths</key>
    <array>
        <string>$HOME/Desktop</string>
        <string>$HOME/Downloads</string>
    </array>
    <key>ThrottleInterval</key>
    <integer>60</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/.local/log/fo-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/.local/log/fo-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin</string>
    </dict>
</dict>
</plist>
PLISTEOF

# Web 面板 (KeepAlive 自动重启)
cat > "$WEB_PLIST" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.fileorganizer.web</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>$APP_DIR/src/app_web.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/.local/log/fo-web-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/.local/log/fo-web-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin</string>
    </dict>
</dict>
</plist>
PLISTEOF

# 5. 加载服务
echo "[5/6] 启动后台服务..."
launchctl load "$WATCH_PLIST"
launchctl load "$WEB_PLIST"

# 6. 首次索引
echo "[6/6] 构建全局文件索引..."
python3 -c "
import sys
sys.path.insert(0, '$HOME/.file-organizer/src')
from engine import SmartOrganizer
org = SmartOrganizer()
org.rebuild_index()
org.health_check()
print('索引构建完成')
" 2>&1 || echo "  (索引将在首次运行时自动构建)"

sleep 2
echo ""
echo "╔══════════════════════════════════════╗"
echo "║  ✅ File Organizer v4.1 安装完成      ║"
echo "║                                      ║"
echo "║  🌐 控制面板: http://localhost:8899  ║"
echo "║  🔍 监控: Desktop + Downloads       ║"
echo "║  ⏱  文件变更后 60s 自动整理          ║"
echo "║                                      ║"
echo "║  管理:                               ║"
echo "║    停止: launchctl unload $WEB_PLIST ║"
echo "║    日志: tail -f ~/.local/log/fo-stdout.log ║"
echo "╚══════════════════════════════════════╝"
