#!/usr/bin/env python3
"""
File Organizer v4 — 菜单栏启动器 + 健康守护
- 菜单栏图标，显示状态
- 监控 Web 服务器健康，崩溃自动重启
- 快速操作：整理、预览、撤销
- 版本自检和更新
"""
import os, sys, time, json, subprocess, signal, threading, platform

# macOS 26 兼容：在 import rumps 之前 patch platform.mac_ver
_orig_mac_ver = platform.mac_ver
def _patched_mac_ver():
    try:
        ver = _orig_mac_ver()
        if ver[0] and int(ver[0].split('.')[0]) >= 26:
            return ('15.0',)
        return ver
    except Exception:
        return ('15.0',)
platform.mac_ver = _patched_mac_ver

import rumps

APP_DIR = os.path.expanduser("~/.file-organizer")
APP_SCRIPT = os.path.join(APP_DIR, "app_web.py")
HEALTH_FILE = os.path.join(APP_DIR, "data", "health.json")
LOG_FILE = os.path.join(APP_DIR, "logs", "organizer.log")
VERSION = "4.0.0"
PORT = 8899

class FileOrganizerApp(rumps.App):
    def __init__(self):
        super().__init__(
            name="File Organizer",
            title="📁",
            quit_button="退出 File Organizer",
        )
        self.server_process: subprocess.Popen | None = None
        self.health_timer: rumps.Timer | None = None

        # 菜单项
        self.menu_status = rumps.MenuItem(title="状态: 启动中...")
        self.menu.add(self.menu_status)
        self.menu.add(rumps.separator)

        self.menu.add(rumps.MenuItem(title="▶ 智能整理", callback=self.action_organize))
        self.menu.add(rumps.MenuItem(title="👁 预览待整理", callback=self.action_preview))
        self.menu.add(rumps.MenuItem(title="↩ 撤销上一步", callback=self.action_undo))
        self.menu.add(rumps.separator)

        self.menu.add(rumps.MenuItem(title="💚 健康检查", callback=self.action_health))
        self.menu.add(rumps.MenuItem(title="🔄 重建索引", callback=self.action_rebuild))
        self.menu.add(rumps.separator)

        self.menu.add(rumps.MenuItem(title="🌐 打开控制面板", callback=self.action_open_web))
        self.menu.add(rumps.MenuItem(title="📜 查看日志", callback=self.action_view_log))

    def start(self):
        """启动时：拉起 Web 服务器 + 健康检查定时器"""
        self.launch_server()
        self.health_timer = rumps.Timer(callback=self._health_tick, interval=30)
        self.health_timer.start()

    def launch_server(self):
        """启动 Web 服务器进程"""
        if self.server_process and self.server_process.poll() is None:
            return  # 已在运行

        try:
            self.server_process = subprocess.Popen(
                [sys.executable, APP_SCRIPT],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
            self.menu_status.title = "状态: ✅ 运行中"
            self.title = "📁"
        except Exception as e:
            self.menu_status.title = f"状态: ❌ 启动失败 {e}"
            self.title = "⚠️"

    def kill_server(self):
        """停止服务器"""
        if self.server_process:
            try:
                os.killpg(os.getpgid(self.server_process.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            self.server_process = None
        # 也杀掉可能残留的进程
        try:
            subprocess.run(["lsof", "-ti", f":{PORT}"],
                         capture_output=True, text=True)
        except Exception:
            pass

    def restart_server(self):
        self.kill_server()
        time.sleep(1)
        self.launch_server()

    def _health_tick(self, _):
        """定时健康检查"""
        # 1. 检查 Web 服务器是否存活
        server_alive = False
        if self.server_process and self.server_process.poll() is None:
            server_alive = True
        else:
            # 检查端口是否被占用
            try:
                result = subprocess.run(
                    ["lsof", "-ti", f":{PORT}"],
                    capture_output=True, text=True, timeout=3
                )
                if result.stdout.strip():
                    server_alive = True
                else:
                    server_alive = False
            except Exception:
                server_alive = False

        if not server_alive:
            self.menu_status.title = "状态: ⚠️ 服务离线，重启中..."
            self.title = "🔴"
            self.launch_server()
        else:
            self.menu_status.title = "状态: ✅ 运行中"
            self.title = "📁"

        # 2. 读取健康报告
        if os.path.exists(HEALTH_FILE):
            try:
                with open(HEALTH_FILE) as f:
                    h = json.load(f)
                    if h.get("status") == "unhealthy":
                        issues = h.get("issues", [])
                        if issues:
                            self.menu_status.title = f"状态: ⚠️ {issues[0][:30]}"
                            self.title = "🟡"
            except Exception:
                pass

    # ── 菜单回调 ──
    def action_organize(self, _):
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/organize", timeout=5)
            rumps.notification("File Organizer", "整理中...", "正在智能整理文件")
        except Exception as e:
            rumps.notification("File Organizer", "错误", f"无法连接: {e}")

    def action_preview(self, _):
        try:
            import urllib.request
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/preview")
            data = json.loads(urllib.request.urlopen(req, timeout=5).read())
            total = data.get("total", 0)
            if total == 0:
                rumps.notification("File Organizer", "预览", "✅ 没有需要整理的文件")
            else:
                rumps.notification("File Organizer", "预览", f"{total} 个文件待整理，打开面板查看")
        except Exception:
            pass

    def action_undo(self, _):
        try:
            import urllib.request
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/undo", method="POST")
            data = json.loads(urllib.request.urlopen(req, timeout=5).read())
            rumps.notification("File Organizer", "撤销", data.get("message", ""))
        except Exception:
            pass

    def action_health(self, _):
        try:
            import urllib.request
            data = json.loads(
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=5).read()
            )
            issues = data.get("issues", [])
            if issues:
                rumps.notification("File Organizer", "⚠️ 健康问题", "\n".join(issues[:3]))
            else:
                ok = data.get("ok", [])
                rumps.notification("File Organizer", "✅ 系统健康", ", ".join(ok[:3]))
        except Exception as e:
            rumps.notification("File Organizer", "健康检查", f"无法连接: {e}")

    def action_rebuild(self, _):
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/learn", timeout=5)
            rumps.notification("File Organizer", "重建索引", "正在全盘扫描学习...")
        except Exception as e:
            rumps.notification("File Organizer", "错误", f"无法连接: {e}")

    def action_open_web(self, _):
        import webbrowser
        webbrowser.open(f"http://localhost:{PORT}")

    def action_view_log(self, _):
        if os.path.exists(LOG_FILE):
            subprocess.Popen(["open", "-a", "Console", LOG_FILE])
        else:
            rumps.notification("File Organizer", "日志", "日志文件不存在")

    def clean_up(self):
        """退出时的清理"""
        self.kill_server()
        if self.health_timer:
            self.health_timer.stop()


def main():
    app = FileOrganizerApp()
    app.start()
    app.run()

if __name__ == "__main__":
    main()
