#!/usr/bin/env python3
"""
DeskBuddy v4.1 — Web 控制面板
自愈引擎：内容分析 + 事务移动 + 撤销 + 反馈学习
"""
import os, sys, json, time, threading, re
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))
from engine import (
    SmartOrganizer, log, LOG_FILE, CONFIG_FILE, VERSION,
    load_config, save_config, organize_now, preview_now,
    undo_last, undo_all, health_report, auto_heal, rebuild_index,
    cleanup_empty_dirs,
    HOME, MoveJournal, BUILTIN_RULES,
)

PORT = 8899
organizer = SmartOrganizer()
monitor_thread = None
monitoring = True

# 从独立文件加载 HTML 模板
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
INDEX_HTML = os.path.join(TEMPLATE_DIR, "index.html")

def _load_html() -> str:
    """加载 HTML 模板，文件缺失时回退到内嵌版本"""
    if os.path.exists(INDEX_HTML):
        try:
            with open(INDEX_HTML, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            log.warning(f"HTML 模板读取失败，使用内嵌版本: {e}")
    # 内嵌回退 HTML（精简版，确保服务至少能启动）
    return _get_fallback_html()

def _get_fallback_html() -> str:
    """内嵌回退 HTML（模板文件缺失时使用）"""
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>DeskBuddy v4.1</title>
<style>body{font-family:-apple-system,sans-serif;background:#0f0f1a;color:#e0e0f0;padding:40px;text-align:center;}
a{color:#7eb8ff;}</style></head><body>
<h1>📁 DeskBuddy v4.1</h1>
<p>模板文件缺失，但服务正常运行。</p>
<p><a href="/api/status">查看 API 状态</a></p>
</body></html>"""

# 预加载 HTML
try:
    HTML = _load_html()
    log.info(f"HTML 模板已加载 ({len(HTML)} 字符)")
except Exception as e:
    log.error(f"HTML 加载失败: {e}")
    HTML = _get_fallback_html()


# ═══════════════════════════════════════════════════════════════
# HTTP API
# ═══════════════════════════════════════════════════════════════
class APIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 抑制标准库的日志输出

    def _send(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length).decode() if length else "{}"

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode())

        elif path == "/api/status":
            recent = []
            if os.path.exists(LOG_FILE):
                try:
                    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
                        for line in f.readlines()[-60:]:
                            if "→" in line:
                                parts = line.strip().split(" ", 2)
                                time_str = parts[0][11:19] if len(parts) > 0 else ""
                                rest = parts[-1][:100] if parts else line.strip()
                                file_match = re.search(r'\] (\S+) →', line)
                                dest_match = re.search(r'→ ~/(\S+)', line)
                                heat_match = re.search(r'\((hot|warm|cold|frozen)', line)
                                recent.append({
                                    "time": time_str,
                                    "file": file_match.group(1)[:40] if file_match else "",
                                    "dest": "~/" + dest_match.group(1)[:40] if dest_match else "",
                                    "heat": heat_match.group(1) if heat_match else "",
                                    "method": "",
                                })
                except Exception as e:
                    log.debug(f"状态解析日志失败: {e}")

            h = health_report()
            self._send({
                "total_organized": organizer.stats.get("organized", 0) + organizer.stats.get("archived", 0),
                "kept_hot": organizer.stats.get("kept_hot", 0),
                "archived": organizer.stats.get("archived", 0),
                "errors": organizer.stats.get("errors", 0),
                "indexed_files": organizer.index.total_files,
                "indexed_extensions": len(organizer.index.ext_index),
                "learned_rules": len(organizer.rules.custom),
                "feedback_words": len(organizer.rules.feedback),
                "monitoring": monitoring,
                "health": h.get("status", "unknown"),
                "version": VERSION,
                "recent": list(reversed(recent[-15:])),
            })

        elif path == "/api/settings":
            self._send(load_config())

        elif path == "/api/rules":
            all_rules = []
            for kws, target in BUILTIN_RULES:
                all_rules.append({"type": "内置", "keywords": ", ".join(kws), "target": target})
            for kws, target in organizer.rules.custom:
                all_rules.append({"type": "自定义", "keywords": ", ".join(kws), "target": target})
            self._send({"rules": all_rules})

        elif path == "/api/log":
            log_content = ""
            if os.path.exists(LOG_FILE):
                try:
                    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                        log_content = "".join(f.readlines()[-300:])
                except Exception as e:
                    log_content = f"(日志读取失败: {e})"
            self._send({"log": log_content or "(空)"})

        elif path == "/preview":
            decisions = organizer.organize_loose_and_uncategorized(preview=True)
            self._send({"decisions": decisions, "total": len(decisions)})

        elif path == "/health":
            self._send(organizer.health_check())

        elif path == "/leftovers":
            self._send({"leftovers": organizer.cleanup_leftovers(dry_run=True)})

        elif path == "/recent-moves":
            self._send({"moves": organizer.recent_moves(30)})

        elif path == "/trace":
            q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
            if not q:
                self._send({"error": "需要搜索关键词", "results": []})
            else:
                self._send({"query": q, "results": organizer.trace_file(q)})

        elif path == "/export-config":
            """导出完整配置（设置+规则）"""
            settings = load_config()
            all_rules = []
            for kws, target in organizer.rules.custom:
                all_rules.append({"type": "自定义", "keywords": ", ".join(kws), "target": target})
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=deskbuddy-config.json")
            self.end_headers()
            export_data = {
                "version": VERSION,
                "exported_at": datetime.now().isoformat(),
                "settings": {k: v for k, v in settings.items() if k != "version"},
                "custom_rules": all_rules,
            }
            self.wfile.write(json.dumps(export_data, indent=2, ensure_ascii=False).encode())

        else:
            self._send({"error": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/organize":
            threading.Thread(target=lambda: organizer.organize_watched(), daemon=True).start()
            self._send({"message": "正在智能整理..."})

        elif path == "/execute":
            decisions = organizer.organize_loose_and_uncategorized(preview=True)
            if not decisions:
                self._send({"message": "无可整理文件", "moved": 0, "errors": 0})
                return
            result = organizer.execute_plan(decisions)
            # 整理后自动清理空目录
            try:
                empty = organizer.cleanup_empty_dirs(dry_run=False)
                if empty:
                    log.info(f"清理了 {len(empty)} 个空目录")
            except Exception as e:
                log.debug(f"空目录清理失败: {e}")
            self._send({"message": f"已整理 {result['moved']} 个文件", **result})

        elif path == "/undo":
            entry = organizer.journal.undo_last()
            if entry:
                fname = os.path.basename(entry.get("destination", ""))
                self._send({"message": f"已撤销: {fname}"})
            else:
                self._send({"error": True, "message": "无可撤销的操作"})

        elif path == "/undo-all":
            n = organizer.journal.undo_all()
            self._send({"message": f"已撤销 {n} 个操作" if n > 0 else "无可撤销的操作"})

        elif path == "/desktop-cleanup":
            def _cleanup():
                try:
                    n = organizer.desktop_cleanup()
                    log.info(f"桌面瘦身: {n}个文件")
                except Exception as e:
                    log.error(f"桌面瘦身失败: {e}")
            threading.Thread(target=_cleanup, daemon=True).start()
            self._send({"message": "正在桌面瘦身..."})

        elif path == "/cleanup-empty-dirs":
            def _empty_cleanup():
                try:
                    removed = organizer.cleanup_empty_dirs(dry_run=False)
                    log.info(f"清理空目录: {len(removed)} 个")
                except Exception as e:
                    log.error(f"空目录清理失败: {e}")
            threading.Thread(target=_empty_cleanup, daemon=True).start()
            self._send({"message": "正在清理空目录..."})

        elif path == "/learn":
            def _learn():
                try:
                    result = organizer.rebuild_index()
                    log.info(f"重建索引: {result}")
                except Exception as e:
                    log.error(f"重建索引失败: {e}")
            threading.Thread(target=_learn, daemon=True).start()
            self._send({"message": "正在全盘扫描学习..."})

        elif path == "/toggle":
            global monitoring
            monitoring = not monitoring
            if monitoring:
                start_monitor()
                self._send({"message": "监控已恢复"})
            else:
                stop_monitor()
                self._send({"message": "监控已暂停"})

        elif path == "/heal":
            result = organizer.auto_heal()
            self._send({"message": f"修复了 {len(result['fixed'])} 项", **result})

        elif path == "/api/settings":
            try:
                body = json.loads(self._body())
                current = load_config()
                current.update(body)
                save_config(current)
                organizer.cfg = current
                # 重建访问分析器以应用新热度参数
                from engine import AccessAnalyzer
                organizer.access = AccessAnalyzer(current)
                self._send({"message": "设置已保存"})
            except Exception as e:
                log.error(f"保存设置失败: {e}")
                self._send({"error": True, "message": str(e)}, 400)

        elif path == "/api/rules":
            try:
                body = json.loads(self._body())
                kws = [k.strip() for k in body.get("keywords", "").split(",") if k.strip()]
                tgt = body.get("target", "").strip()
                if kws and tgt:
                    organizer.rules.custom.append((kws, tgt))
                    organizer.rules.save()
                    self._send({"message": "规则已添加"})
                else:
                    self._send({"error": True, "message": "缺少参数"}, 400)
            except Exception as e:
                log.error(f"添加规则失败: {e}")
                self._send({"error": True, "message": str(e)}, 400)

        elif path == "/clearlog":
            try:
                with open(LOG_FILE, "w") as f:
                    f.write(f"# 日志已清空 {datetime.now().isoformat()}\n")
                self._send({"message": "日志已清空"})
            except Exception as e:
                log.error(f"清空日志失败: {e}")
                self._send({"error": True, "message": str(e)}, 400)

        elif path == "/learn-folder":
            try:
                body = json.loads(self._body())
                folderpath = body.get("path", "").strip()
                if not folderpath:
                    self._send({"error": True, "message": "请提供文件夹路径"}, 400)
                else:
                    result = organizer.learn_from_folder(os.path.expanduser(folderpath))
                    self._send({"message": result.get("message", "完成"), **result})
            except Exception as e:
                log.error(f"学习文件夹失败: {e}")
                self._send({"error": True, "message": str(e)}, 400)

        else:
            self._send({"error": True, "message": "Not found"}, 404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/rules/"):
            try:
                idx = int(path.split("/")[-1])
                if 0 <= idx < len(organizer.rules.custom):
                    organizer.rules.custom.pop(idx)
                    organizer.rules.save()
                    self._send({"message": "规则已删除"})
                else:
                    self._send({"error": True, "message": "索引无效"}, 400)
            except ValueError:
                self._send({"error": True, "message": "无效索引"}, 400)
        else:
            self._send({"error": True, "message": "Not found"}, 404)


# ═══════════════════════════════════════════════════════════════
# 后台监控
# ═══════════════════════════════════════════════════════════════
def monitor_loop():
    first_run = True
    while monitoring:
        try:
            if first_run:
                first_run = False
                log.info("监控已启动，等待 120s 后开始自动整理...")
                for _ in range(12):
                    if not monitoring:
                        return
                    time.sleep(10)
            if organizer.cfg.get("auto_organize", True):
                organizer.organize_watched()
                organizer.desktop_cleanup()
            if datetime.now().minute == 0:
                organizer.health_check()
        except Exception as e:
            log.error(f"监控错误: {e}")
        time.sleep(organizer.cfg.get("poll_interval_sec", 60))

def start_monitor():
    global monitoring, monitor_thread
    monitoring = True
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()

def stop_monitor():
    global monitoring
    monitoring = False


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════
def main():
    start_monitor()
    health = organizer.health_check()
    if health["issues"]:
        log.warning(f"健康检查发现问题: {health['issues']}")
        organizer.auto_heal()

    print(f"""
  ╔══════════════════════════════════════╗
  ║   DeskBuddy v{VERSION}                     ║
  ║   自愈智能引擎                        ║
  ║                                     ║
  ║   👉 http://localhost:{PORT}         ║
  ║                                     ║
  ║   按 Ctrl+C 退出                     ║
  ╚══════════════════════════════════════╝
    """)
    server = HTTPServer(("127.0.0.1", PORT), APIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_monitor()
        server.shutdown()
        print(f"\nDeskBuddy v{VERSION} 已停止")

if __name__ == "__main__":
    main()
