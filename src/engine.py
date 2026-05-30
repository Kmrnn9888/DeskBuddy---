#!/usr/bin/env python3
"""
File Organizer v4 — Self-Healing Smart Engine
修复清单:
  P0: 统一引擎(废弃v1/v2/v3), launchd对接, 服务器持久化
  P1: 撤销功能, 预览确认, 事务性移动
  P2: 内容分析, 用户反馈学习, jieba分词
  P3: 菜单栏, 扩展覆盖, 自愈能力
"""
import os, re, sys, time, json, shutil, logging, hashlib, subprocess
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter, defaultdict
from typing import Optional, Generator

HOME = os.path.expanduser("~")
APP_DIR = os.path.join(HOME, ".file-organizer")
DATA_DIR = os.path.join(APP_DIR, "data")
LOG_DIR = os.path.join(APP_DIR, "logs")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

GLOBAL_INDEX_FILE = os.path.join(DATA_DIR, "global_index.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
RULES_FILE = os.path.join(DATA_DIR, "custom_rules.json")
JOURNAL_FILE = os.path.join(DATA_DIR, "move_journal.jsonl")
FEEDBACK_FILE = os.path.join(DATA_DIR, "feedback.json")
HEALTH_FILE = os.path.join(DATA_DIR, "health.json")
LOG_FILE = os.path.join(LOG_DIR, "organizer.log")
VERSION = "4.0.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("organizer-v4")

# ═══════════════════════════════════════
# Tokenizer — jieba优先，回退regex
# ═══════════════════════════════════════
try:
    import jieba
    jieba.setLogLevel(20)  # 静默
    _has_jieba = True
except ImportError:
    _has_jieba = False

def tokenize(text: str) -> list[str]:
    """中英文混合分词，jieba优先"""
    text = text.lower().strip()
    tokens = []
    # 英文单词
    for m in re.finditer(r'[a-z]{2,}', text):
        tokens.append(m.group())
    # 中文分词
    chinese = ''.join(re.findall(r'[\u4e00-\u9fff]', text))
    if chinese:
        if _has_jieba:
            tokens.extend([w for w in jieba.cut(chinese) if len(w.strip()) >= 1])
        else:
            # fallback: bigram + unigram
            for i in range(len(chinese) - 1):
                tokens.append(chinese[i] + chinese[i+1])
            for c in chinese:
                tokens.append(c)
    # 数字串
    for m in re.finditer(r'\d{2,}', text):
        tokens.append(m.group())
    return tokens

def extract_clean_name(filename: str) -> str:
    """从文件名提取有意义的描述"""
    name = os.path.splitext(filename)[0]
    name = re.sub(r'[a-f0-9]{32,}', '', name)
    name = re.sub(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', '', name)
    name = re.sub(r'_\d{13,}', '', name)
    name = re.sub(r'@+dapi-[^@]+@+', '', name)
    name = re.sub(r'@@@.*$', '', name)
    name = re.sub(r'_\d+x\d+$', '', name)
    name = re.sub(r'_{2,}', '_', name)
    name = re.sub(r'[\-\s]+v?\d+[\.\d]*$', '', name)
    name = name.strip("_- .")
    return name[:80] if len(name) >= 2 else "未知文件"

SYSTEM_FILES = {".DS_Store", ".localized", "desktop.ini", "Thumbs.db", ".DS_Store?"}
SKIP_DIRS = {"Library", ".Trash", ".cache", "node_modules", ".git",
             "Applications", ".local", ".file-organizer", "Parallels",
             ".claude", ".config", ".ssh", ".gnupg", "__pycache__",
             ".npm", ".cargo", ".rustup", ".docker", ".gradle", ".m2"}

# ═══════════════════════════════════════
# 配置
# ═══════════════════════════════════════
DEFAULT_CONFIG = {
    "watch_dirs": ["~/Desktop", "~/Downloads"],
    "hot_days": 7,
    "warm_days": 30,
    "cold_days": 90,
    "archive_dir": "~/Archive",
    "desktop_max_files": 20,
    "cooldown_sec": 30,
    "poll_interval_sec": 60,
    "auto_organize": True,
    "use_global_index": True,
    "use_content_analysis": True,
    "use_feedback_learning": True,
    "notifications": True,
    "version": VERSION,
}

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return dict(DEFAULT_CONFIG)

def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# ═══════════════════════════════════════
# 内容分析器 — 读取文件内容辅助分类
# ═══════════════════════════════════════
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".html", ".htm", ".json", ".xml",
                   ".py", ".js", ".ts", ".css", ".yaml", ".yml", ".toml",
                   ".log", ".sh", ".bash", ".zsh", ".cfg", ".ini", ".conf"}

class ContentAnalyzer:
    """读取文本文件内容，提取关键词辅助分类"""
    MAX_SIZE = 2 * 1024 * 1024  # 2MB max

    @staticmethod
    def read_content(filepath: str) -> Optional[str]:
        ext = os.path.splitext(filepath)[1].lower()
        if ext not in TEXT_EXTENSIONS:
            return None
        try:
            size = os.path.getsize(filepath)
            if size > ContentAnalyzer.MAX_SIZE or size == 0:
                return None
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read(4096)  # 只读前4KB
        except (OSError, UnicodeDecodeError):
            return None

    @staticmethod
    def extract_keywords(content: str) -> list[str]:
        """从文本内容提取关键词"""
        if not content:
            return []
        # 清理常见噪声
        content = re.sub(r'[^\w\u4e00-\u9fff\s]', ' ', content)
        content = re.sub(r'\s+', ' ', content)
        tokens = tokenize(content[:2000])
        # 取频率最高的15个词
        freq = Counter(tokens)
        return [w for w, _ in freq.most_common(15) if len(w) >= 2]

# ═══════════════════════════════════════
# 全局文件索引
# ═══════════════════════════════════════
class GlobalIndex:
    """全盘文件分布索引 + 扩展名→标准归宿映射"""

    EXT_BASE = {
        ".pdf": "Documents", ".doc": "Documents", ".docx": "Documents",
        ".xls": "Documents", ".xlsx": "Documents", ".ppt": "Documents", ".pptx": "Documents",
        ".csv": "Documents", ".txt": "Documents", ".md": "Documents", ".rtf": "Documents",
        ".epub": "Documents", ".mobi": "Documents", ".pages": "Documents", ".numbers": "Documents",
        ".key": "Documents", ".html": "Documents", ".htm": "Documents",
        ".jpg": "Pictures", ".jpeg": "Pictures", ".png": "Pictures", ".gif": "Pictures",
        ".webp": "Pictures", ".bmp": "Pictures", ".tiff": "Pictures", ".svg": "Pictures",
        ".heic": "Pictures", ".raw": "Pictures", ".psd": "Pictures", ".ai": "Pictures",
        ".mp4": "Movies", ".mov": "Movies", ".avi": "Movies", ".mkv": "Movies",
        ".flv": "Movies", ".wmv": "Movies", ".m4v": "Movies",
        ".mp3": "Music", ".flac": "Music", ".aac": "Music", ".wav": "Music",
        ".m4a": "Music", ".ogg": "Music",
        ".dmg": "Downloads/Software", ".pkg": "Downloads/Software",
        ".exe": "Downloads/Software", ".msi": "Downloads/Software",
        ".zip": "Downloads", ".rar": "Downloads", ".7z": "Downloads",
        ".tar": "Downloads", ".gz": "Downloads", ".bz2": "Downloads",
        ".skp": "Documents/学习/建筑学", ".dwg": "Documents/学习/建筑学",
    }

    def __init__(self):
        self.ext_index: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.kw_index: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.total_files = 0
        self.last_scan: Optional[str] = None

    def scan(self, cfg: dict) -> dict:
        """全盘扫描构建索引"""
        log.info("开始全盘扫描...")
        start = time.time()
        skip = set(cfg.get("skip_dirs", SKIP_DIRS))
        scan_roots = [
            os.path.join(HOME, d) for d in
            ["Desktop", "Downloads", "Documents", "Pictures", "Movies", "Music"]
            if os.path.isdir(os.path.join(HOME, d))
        ]
        scanned = 0
        for root in scan_roots:
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in skip]
                depth = dirpath.replace(root, '').count(os.sep)
                if depth > 5:
                    dirnames[:] = []
                    continue
                scanned += 1
                for fname in filenames:
                    if fname.startswith('.') or fname in SYSTEM_FILES:
                        continue
                    ext = os.path.splitext(fname)[1].lower()
                    if not ext:
                        continue
                    parent_rel = os.path.relpath(dirpath, HOME)
                    self.ext_index[ext][parent_rel] += 1
                    self.total_files += 1
                    clean = extract_clean_name(fname)
                    for token in tokenize(clean):
                        self.kw_index[token][parent_rel] += 1.0
                    dir_token = tokenize(os.path.basename(dirpath))
                    for token in dir_token:
                        self.kw_index[token][parent_rel] += 0.5
                if time.time() - start > 180:
                    log.warning("扫描超时")
                    break
        # 归一化
        for token in self.kw_index:
            total = sum(self.kw_index[token].values())
            if total > 0:
                for d in self.kw_index[token]:
                    self.kw_index[token][d] /= total
        self.last_scan = datetime.now().isoformat()
        self._save()
        elapsed = time.time() - start
        log.info(f"索引: {self.total_files}文件 {len(self.ext_index)}扩展名 {len(self.kw_index)}关键词 {elapsed:.1f}s")
        return {"files": self.total_files, "extensions": len(self.ext_index),
                "keywords": len(self.kw_index), "time": round(elapsed, 1)}

    def predict(self, filepath: str) -> Optional[tuple]:
        """预测文件最佳归宿 → (绝对路径, 置信度0-1, 理由)"""
        if not self.ext_index:
            self._load()
            if not self.ext_index:
                return None
        fname = os.path.basename(filepath)
        ext = os.path.splitext(fname)[1].lower()
        tokens = tokenize(extract_clean_name(fname))
        source_dir = os.path.dirname(os.path.abspath(filepath))
        base = self.EXT_BASE.get(ext, "Documents")
        scores: dict[str, float] = defaultdict(float)
        # 扩展名分布匹配（深度惩罚：过深的目录降低权重）
        if ext in self.ext_index:
            ext_total = sum(self.ext_index[ext].values()) or 1
            for d, count in self.ext_index[ext].items():
                depth = d.count(os.sep)
                depth_penalty = max(0.2, 1.0 - depth * 0.15)
                if d.startswith(base):
                    scores[d] += (count / ext_total) * 0.6 * depth_penalty
                elif os.path.commonpath([d, base]) not in ("", "/", HOME):
                    scores[d] += (count / ext_total) * 0.15 * depth_penalty
        # 关键词匹配
        for token in tokens:
            if token in self.kw_index:
                for d, weight in self.kw_index[token].items():
                    if d.startswith(base):
                        scores[d] += weight * 0.6
                    else:
                        scores[d] += weight * 0.1
        if not scores:
            return (os.path.join(HOME, base), 0.3, f"默认归宿 ~/{base}")
        best_dir = max(scores, key=scores.get)
        confidence = min(scores[best_dir] * 3, 0.95)
        if best_dir == base:
            subs = {d: s for d, s in scores.items() if d.startswith(base + os.sep)}
            if subs:
                best_dir = max(subs, key=subs.get)
                confidence = min(confidence * 0.85, 0.85)
        abs_dir = os.path.join(HOME, best_dir)
        if os.path.abspath(abs_dir) == os.path.abspath(source_dir):
            abs_dir = os.path.join(HOME, base)
            confidence = 0.25
        reason = f"{ext}→~/{base}"
        if best_dir != base:
            reason += f"/{os.path.relpath(best_dir, base)}"
        return (abs_dir, confidence, reason)

    def _save(self):
        data = {
            "ext_index": {k: dict(v) for k, v in self.ext_index.items()},
            "kw_index": {k: dict(v) for k, v in self.kw_index.items()},
            "total_files": self.total_files,
            "last_scan": self.last_scan,
        }
        with open(GLOBAL_INDEX_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False)

    def _load(self):
        if os.path.exists(GLOBAL_INDEX_FILE):
            with open(GLOBAL_INDEX_FILE) as f:
                data = json.load(f)
                self.ext_index = defaultdict(lambda: defaultdict(int), {
                    k: defaultdict(int, v) for k, v in data.get("ext_index", {}).items()})
                self.kw_index = defaultdict(lambda: defaultdict(float), {
                    k: defaultdict(float, v) for k, v in data.get("kw_index", {}).items()})
                self.total_files = data.get("total_files", 0)
                self.last_scan = data.get("last_scan")

# ═══════════════════════════════════════
# 访问热度分析
# ═══════════════════════════════════════
class AccessAnalyzer:
    def __init__(self, config: dict):
        self.hot_days = config.get("hot_days", 7)
        self.warm_days = config.get("warm_days", 30)
        self.cold_days = config.get("cold_days", 90)

    def classify(self, filepath: str) -> str:
        try:
            stat = os.stat(filepath)
            last_used = max(
                datetime.fromtimestamp(stat.st_atime),
                datetime.fromtimestamp(stat.st_mtime)
            )
            days = (datetime.now() - last_used).days
            if days <= self.hot_days:
                return "hot"
            elif days <= self.warm_days:
                return "warm"
            elif days <= self.cold_days:
                return "cold"
            return "frozen"
        except OSError:
            return "warm"

    def age_days(self, filepath: str) -> int:
        try:
            stat = os.stat(filepath)
            last_used = max(
                datetime.fromtimestamp(stat.st_atime),
                datetime.fromtimestamp(stat.st_mtime)
            )
            return (datetime.now() - last_used).days
        except OSError:
            return 0

# ═══════════════════════════════════════
# 规则引擎 + 反馈学习
# ═══════════════════════════════════════
BUILTIN_RULES = [
    (["建筑", "建筑学长", "咖啡厅", "咖啡店", "Peet", "皮爷", "星巴克", "Starbucks",
      "渲染", "剖面", "Section", "立面", "室内设计", "小店经济",
      "SketchUp", "skp", "Revit", "BIM", "DWG", "dwg", "rhino", "grasshopper"],
     "Documents/学习/建筑学"),
    (["英语", "六级", "四级", "CET", "词汇", "托福", "雅思", "单词"],
     "Documents/学习/英语"),
    (["心理", "社交", "焦虑", "人格", "MBTI"],
     "Documents/学习/心理学"),
    (["AutoSleep", "睡眠", "sleep", "健康", "health", "心率"],
     "Documents/健康数据"),
    (["发票", "收据", "账单", "invoice", "receipt"],
     "Documents/财务"),
    (["Canva", "canva"], "Downloads/Software/Canva"),
    (["Cherry", "cherry"], "Downloads/Software/CherryStudio"),
    (["Parallels", "PD"], "Downloads/Software/Parallels"),
    (["Steam", "steam"], "Downloads/Software/Steam"),
    (["node", "Node", "nodejs"], "Downloads/Software/Nodejs"),
    (["D5", "D5Deploy"], "Downloads/Software/D5"),
    (["Gaomon", "高漫"], "Downloads/Software/Gaomon"),
    (["Arma", "arma"], "Downloads/Software/Arma3"),
    (["i4Tools", "爱思"], "Downloads/Software/i4Tools"),
    (["AnyGo", "anygo"], "Downloads/Software/AnyGo"),
    (["UsbEAm", "usbeam"], "Downloads/Software/UsbEAm"),
    (["Quark", "夸克"], "Downloads/Software/Quark"),
    (["IMG_", "Screenshot", "截图", "屏幕快照", "截屏"],
     "Pictures/照片"),
    (["壁纸", "wallpaper", "Wallpaper"], "Pictures/壁纸"),
    (["简历", "CV", "Resume", "resume"], "Documents/简历"),
    (["注册机", "激活", "keygen", "crack", "patch"],
     "Downloads/Software/工具"),
    (["logi", "Logitech", "罗技", "options"], "Downloads/Software/Logitech"),
    (["Flux", "flux"], "Downloads/Software/Flux"),
]

class RuleEngine:
    def __init__(self):
        self.custom: list[tuple[list[str], str]] = []
        self.feedback: dict[str, dict[str, float]] = {}  # {token: {target: weight}}
        self._load()

    def _load(self):
        if os.path.exists(RULES_FILE):
            try:
                with open(RULES_FILE) as f:
                    data = json.load(f)
                # 校验格式：应为 [[keywords_list, target_str], ...]
                if isinstance(data, list):
                    self.custom = [
                        (list(item[0]) if isinstance(item[0], list) else [str(item[0])], str(item[1]))
                        for item in data
                        if isinstance(item, (list, tuple)) and len(item) >= 2
                    ]
            except (json.JSONDecodeError, KeyError, IndexError):
                self.custom = []
        if os.path.exists(FEEDBACK_FILE):
            with open(FEEDBACK_FILE) as f:
                self.feedback = json.load(f)

    def save(self):
        with open(RULES_FILE, "w") as f:
            json.dump(self.custom, f, indent=2, ensure_ascii=False)

    def save_feedback(self):
        with open(FEEDBACK_FILE, "w") as f:
            json.dump(self.feedback, f, indent=2, ensure_ascii=False)

    def all_rules(self) -> list[tuple[list[str], str]]:
        return BUILTIN_RULES + self.custom

    def match(self, filepath: str) -> Optional[str]:
        """规则匹配 → 目标绝对路径"""
        fname = os.path.basename(filepath).lower()
        clean = extract_clean_name(filepath).lower()
        search_text = f"{fname} {clean}"
        # 先查自定义规则
        for kws, target in self.custom:
            for kw in kws:
                if kw.lower() in search_text:
                    return os.path.join(HOME, target)
        # 再查内置规则
        for kws, target in BUILTIN_RULES:
            for kw in kws:
                if kw.lower() in search_text:
                    return os.path.join(HOME, target)
        # 反馈学习权重
        if self.feedback:
            tokens = tokenize(clean)
            scores: dict[str, float] = defaultdict(float)
            for token in tokens:
                if token in self.feedback:
                    for target, weight in self.feedback[token].items():
                        scores[target] += weight
            if scores:
                best = max(scores, key=scores.get)
                if scores[best] > 0.3:
                    return os.path.join(HOME, best)
        return None

    def learn_feedback(self, filename: str, chosen_target: str):
        """用户手动移动文件后，学习这个行为"""
        clean = extract_clean_name(filename)
        tokens = tokenize(clean)
        target_rel = os.path.relpath(chosen_target, HOME) if chosen_target.startswith(HOME) else chosen_target
        for token in tokens:
            if token not in self.feedback:
                self.feedback[token] = {}
            self.feedback[token][target_rel] = self.feedback[token].get(target_rel, 0) + 0.3
        # 衰减旧权重
        for token in self.feedback:
            for tgt in self.feedback[token]:
                self.feedback[token][tgt] *= 0.95
        self.save_feedback()
        log.info(f"反馈学习: '{clean[:30]}' → {target_rel}")

# ═══════════════════════════════════════
# 移动日志 — 事务性 + 撤销支持
# ═══════════════════════════════════════
class MoveJournal:
    """记录每次移动，支持撤销"""
    def __init__(self):
        self.entries: list[dict] = []
        self._load()

    def record(self, source: str, dest: str, checksum: str = "", reason: str = ""):
        entry = {
            "time": datetime.now().isoformat(),
            "source": source,
            "destination": dest,
            "checksum": checksum,
            "reason": reason,
            "rolled_back": False,
        }
        self.entries.append(entry)
        self._append(entry)
        # 只保留最近500条
        if len(self.entries) > 500:
            self.entries = self.entries[-500:]
            self._rewrite()

    def undo_last(self) -> Optional[dict]:
        """撤销最后一次移动（文件或文件夹）"""
        for entry in reversed(self.entries):
            if entry.get("rolled_back"):
                continue
            src = entry["source"]
            dst = entry["destination"]
            is_folder = "文件夹移动" in entry.get("reason", "")
            if os.path.exists(dst) and not os.path.exists(src):
                try:
                    os.makedirs(os.path.dirname(src), exist_ok=True)
                    shutil.move(dst, src)
                    # 清理可能变空的目标父目录
                    dst_parent = os.path.dirname(dst)
                    try:
                        if os.path.isdir(dst_parent) and not os.listdir(dst_parent):
                            os.rmdir(dst_parent)
                    except OSError:
                        pass
                    entry["rolled_back"] = True
                    self._rewrite()
                    what = "文件夹" if is_folder else "文件"
                    log.info(f"撤销{what}: {os.path.basename(dst)} → {os.path.dirname(src)}")
                    return entry
                except Exception as e:
                    log.error(f"撤销失败: {e}")
                    return None
        return None

    def undo_all(self) -> int:
        """撤销所有未回滚的移动"""
        count = 0
        while self.undo_last():
            count += 1
        return count

    def recent(self, n: int = 20) -> list[dict]:
        return [e for e in self.entries[-n:] if not e.get("rolled_back")]

    def _append(self, entry: dict):
        with open(JOURNAL_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _rewrite(self):
        with open(JOURNAL_FILE, "w") as f:
            for e in self.entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def _load(self):
        if os.path.exists(JOURNAL_FILE):
            with open(JOURNAL_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self.entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

# ═══════════════════════════════════════
# 智能调度器 v4
# ═══════════════════════════════════════
class SmartOrganizer:
    def __init__(self):
        self.cfg = load_config()
        self.index = GlobalIndex()
        self.access = AccessAnalyzer(self.cfg)
        self.rules = RuleEngine()
        self.journal = MoveJournal()
        self.stats = {"organized": 0, "kept_hot": 0, "archived": 0, "errors": 0}
        self._ensure_index()
        if self.cfg.get("version") != VERSION:
            self.cfg["version"] = VERSION
            save_config(self.cfg)

    def _ensure_index(self):
        if not os.path.exists(GLOBAL_INDEX_FILE):
            log.info("首次运行，构建全局索引...")
            self.index.scan(self.cfg)
        else:
            self.index._load()

    def rebuild_index(self) -> dict:
        return self.index.scan(self.cfg)

    def checksum(self, filepath: str) -> str:
        """快速SHA256"""
        try:
            h = hashlib.sha256()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()[:16]
        except OSError:
            return ""

    def notify(self, title: str, message: str):
        if not self.cfg.get("notifications", True):
            return
        try:
            subprocess.run([
                "osascript", "-e",
                f'display notification "{message[:200]}" with title "{title}" sound name "Glass"'
            ], timeout=3, capture_output=True)
        except Exception:
            pass

    def triage_file(self, filepath: str) -> dict:
        """对单个文件做出智能分派决策"""
        if not os.path.isfile(filepath):
            return {"action": "skip", "reason": "非文件"}
        fname = os.path.basename(filepath)
        if fname in SYSTEM_FILES or fname.startswith("._"):
            return {"action": "skip", "reason": "系统文件"}

        result = {"filename": fname, "source": os.path.dirname(filepath)}
        heat = self.access.classify(filepath)
        days = self.access.age_days(filepath)
        result["heat"] = heat
        result["days_since_use"] = days

        is_on_desktop = os.path.expanduser("~/Desktop") in os.path.abspath(filepath)
        is_installer = fname.lower().endswith(('.dmg', '.pkg', '.exe', '.msi'))

        # 热文件在桌面：非安装包保留，安装包移到软件目录
        if heat == "hot" and is_on_desktop:
            if is_installer:
                result["action"] = "move"
                result["destination"] = os.path.join(HOME, "Downloads", "Software")
                result["method"] = "热安装包→软件库"
                result["reason"] = f"热门安装包({days}天前)，移入软件库"
                result["heat"] = heat
                result["days_since_use"] = days
                return result
            else:
                result["action"] = "keep"
                result["reason"] = f"热文件({days}天前使用)，保留桌面"
                return result

        if heat == "frozen":
            archive_dir = os.path.expanduser(self.cfg.get("archive_dir", "~/Archive"))
            year = datetime.now().strftime("%Y")
            result["action"] = "archive"
            result["destination"] = os.path.join(archive_dir, year)
            result["reason"] = f"冻结({days}天未用)，归档"
            return result

        dest = None
        method = ""

        # 1. 规则匹配（优先级最高）
        rule_match = self.rules.match(filepath)
        if rule_match:
            dest = rule_match
            method = "规则匹配"

        # 2. 全局索引预测（无意义文件名提高阈值，避免误分类）
        if not dest and self.cfg.get("use_global_index", True):
            clean_name = extract_clean_name(fname)
            name_tokens = tokenize(clean_name)
            # 文件名太短或无意义 → 提高阈值
            min_conf = 0.25
            if len(clean_name) < 4 or len(name_tokens) < 2:
                min_conf = 0.55
            pred = self.index.predict(filepath)
            if pred and pred[1] > min_conf:
                dest = pred[0]
                # 如果目标目录太深(>3层)且置信度不高，回退
                dest_depth = os.path.relpath(dest, HOME).count(os.sep)
                if dest_depth > 3 and pred[1] < 0.6:
                    dest = os.path.join(HOME, self.index.EXT_BASE.get(
                        os.path.splitext(fname)[1].lower(), "Documents"))
                    method = f"索引过深回退"
                else:
                    method = f"全局索引({pred[1]:.0%})"

        # 3. 内容分析增强
        if not dest or (method.startswith("全局索引") and "0." in method):
            content_kws = ContentAnalyzer.extract_keywords(
                ContentAnalyzer.read_content(filepath) or ""
            )
            if content_kws:
                for kw in content_kws[:5]:
                    kw_target = self.rules.match(os.path.join("/", kw))
                    if kw_target:
                        dest = kw_target
                        method = f"内容分析('{kw}'匹配)"
                        break

        # 4. 扩展名默认
        if not dest:
            ext = os.path.splitext(fname)[1].lower()
            ext_fallback = {
                ".jpg": "Pictures/照片", ".jpeg": "Pictures/照片", ".png": "Pictures/照片",
                ".gif": "Pictures/图片", ".webp": "Pictures/图片", ".heic": "Pictures/照片",
                ".mp4": "Movies", ".mov": "Movies", ".mkv": "Movies", ".avi": "Movies",
                ".mp3": "Music", ".flac": "Music", ".aac": "Music", ".wav": "Music",
                ".pdf": "Documents", ".doc": "Documents", ".docx": "Documents",
                ".xlsx": "Documents", ".pptx": "Documents", ".csv": "Documents",
                ".zip": "Downloads/压缩包", ".rar": "Downloads/压缩包", ".7z": "Downloads/压缩包",
                ".dmg": "Downloads/Software", ".pkg": "Downloads/Software",
                ".exe": "Downloads/Software", ".msi": "Downloads/Software",
                ".skp": "Documents/学习/建筑学", ".dwg": "Documents/学习/建筑学",
                ".html": "Documents", ".txt": "Documents", ".md": "Documents",
            }
            target = ext_fallback.get(ext)
            if target:
                dest = os.path.join(HOME, target)
                method = "扩展名默认"
            else:
                dest = os.path.join(HOME, "Downloads", "未分类")
                method = "未分类兜底"

        # 避免把文件移回原目录（no-op）
        source_abs = os.path.abspath(result["source"])
        if dest and os.path.abspath(dest) == source_abs:
            result["action"] = "keep"
            result["reason"] = f"已在目标位置 (热度:{heat}, {days}天)"
            return result

        result["action"] = "move"
        result["destination"] = dest
        result["method"] = method
        result["reason"] = f"{method} (热度:{heat}, {days}天)"
        return result

    def triage_folder(self, folderpath: str) -> Optional[dict]:
        """判断文件夹级别的分派"""
        if not os.path.isdir(folderpath):
            return None
        dirname = os.path.basename(folderpath)
        if dirname.startswith('.') or dirname in SKIP_DIRS:
            return None

        source_parent = os.path.dirname(folderpath)
        is_root = any(
            os.path.abspath(source_parent) == os.path.abspath(os.path.expanduser(wd))
            for wd in self.cfg.get("watch_dirs", [])
        )
        if not is_root:
            return None

        name_lower = dirname.lower()
        folder_rules = [
            (["kamran", "建筑", "architecture", "设计", "design", "项目", "project",
              "博物馆", "museum", "咖啡", "coffee", "sketchup", "revit", "cad", "bim"],
             "Documents/学习/建筑学/"),
            (["照片", "photo", "图片", "image", "picture", "截图", "screenshot"],
             "Pictures/"),
            (["视频", "video", "movie", "电影"], "Movies/"),
            (["音乐", "music", "audio", "歌曲", "mp3"], "Music/"),
            (["software", "软件", "工具", "tool", "driver", "驱动", "安装包"],
             "Downloads/Software/"),
            (["文档", "document", "资料", "doc"], "Documents/"),
            (["下载", "download", "压缩", "zip", "rar"], "Downloads/"),
            (["健康", "health", "autosleep", "睡眠"], "Documents/健康数据/"),
        ]
        for kws, target_base in folder_rules:
            for kw in kws:
                if kw.lower() in name_lower:
                    dest = os.path.join(HOME, target_base, dirname)
                    dest_abs = os.path.abspath(dest)
                    src_abs = os.path.abspath(folderpath)
                    # 防止自移动: 目标路径等于或包含源路径
                    if dest_abs == src_abs or dest_abs.startswith(src_abs + os.sep):
                        return None
                    return {
                        "action": "move_folder",
                        "filename": dirname,
                        "source": source_parent,
                        "destination": dest,
                        "heat": "warm",
                        "reason": f"文件夹匹配'{kw}'→~/{target_base}{dirname}",
                    }
        return None

    def execute_move(self, decision: dict) -> bool:
        """执行文件移动（事务性 + 校验）"""
        filepath = os.path.join(decision["source"], decision["filename"])
        dest_dir = decision["destination"]
        if not os.path.isfile(filepath):
            return False
        try:
            cksum = self.checksum(filepath)
            os.makedirs(dest_dir, exist_ok=True)
            base, ext = os.path.splitext(decision["filename"])
            new_name = decision["filename"]
            dest_path = os.path.join(dest_dir, new_name)
            counter = 1
            while os.path.exists(dest_path):
                new_name = f"{base}_{counter}{ext}"
                dest_path = os.path.join(dest_dir, new_name)
                counter += 1
            shutil.move(filepath, dest_path)
            # 校验
            dest_cksum = self.checksum(dest_path)
            if cksum and dest_cksum and cksum != dest_cksum:
                log.error(f"校验失败! {cksum} ≠ {dest_cksum}")
                shutil.move(dest_path, filepath)  # 回滚
                self.stats["errors"] += 1
                return False
            self.journal.record(filepath, dest_path, cksum, decision.get("reason", ""))
            rel = os.path.relpath(dest_dir, HOME)
            log.info(f"[{decision['action']}] {decision['filename']} → ~/{rel}/  "
                     f"({decision.get('method','')}, {decision.get('heat','')})")
            if decision["action"] == "archive":
                self.stats["archived"] += 1
            else:
                self.stats["organized"] += 1
            return True
        except Exception as e:
            log.error(f"移动失败 {decision['filename']}: {e}")
            self.stats["errors"] += 1
            return False

    def execute_folder_move(self, decision: dict) -> bool:
        """移动整个文件夹（含日志记录，支持撤销）"""
        folderpath = os.path.join(decision["source"], decision["filename"])
        dest_path = decision["destination"]
        if not os.path.isdir(folderpath):
            return False
        try:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            if os.path.exists(dest_path):
                # 合并模式：逐项移动
                moved_items = []
                for item in os.listdir(folderpath):
                    src = os.path.join(folderpath, item)
                    dst = os.path.join(dest_path, item)
                    if not os.path.exists(dst):
                        shutil.move(src, dst)
                        moved_items.append(item)
                try:
                    os.rmdir(folderpath)
                except OSError:
                    pass
            else:
                shutil.move(folderpath, dest_path)
            rel = os.path.relpath(dest_path, HOME)
            # 记录到日志（支持撤销：移动整个文件夹回到原位）
            self.journal.record(
                folderpath, dest_path, "",
                f"文件夹移动: {decision.get('reason','')}"
            )
            log.info(f"[folder] {decision['filename']} → ~/{rel}")
            self.notify("File Organizer",
                       f"📁 {decision['filename']}\n移至 ~/{rel}")
            self.stats["organized"] += 1
            # 自动学习：如果文件夹有组织良好的子目录结构，提取规则
            if self.cfg.get("use_feedback_learning", True):
                try:
                    self.learn_from_folder(dest_path)
                except Exception:
                    pass
            return True
        except Exception as e:
            log.error(f"文件夹移动失败 {folderpath}: {e}")
            self.stats["errors"] += 1
            return False

    def scan_loose_files(self, directory: str) -> list[str]:
        """扫描目录根层的散落文件（不递归进已整理子目录）"""
        results = []
        watch = os.path.expanduser(directory)
        if not os.path.isdir(watch):
            return results
        try:
            entries = os.listdir(watch)
        except PermissionError:
            return results
        for entry in entries:
            fpath = os.path.join(watch, entry)
            if not os.path.isfile(fpath) or os.path.islink(fpath):
                continue
            if entry in SYSTEM_FILES or entry.startswith("._"):
                continue
            try:
                if time.time() - os.path.getmtime(fpath) < self.cfg.get("cooldown_sec", 30):
                    continue
            except OSError:
                continue
            results.append(fpath)
        return results

    def scan_uncategorized(self, directory: str) -> list[str]:
        """递归扫描目录中所有'未分类'文件夹内的文件"""
        results = []
        watch = os.path.expanduser(directory)
        if not os.path.isdir(watch):
            return results
        for root, dirs, files in os.walk(watch):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in SKIP_DIRS]
            if os.path.basename(root) != "未分类":
                continue
            for f in files:
                if f in SYSTEM_FILES or f.startswith("._"):
                    continue
                fpath = os.path.join(root, f)
                if os.path.isfile(fpath) and not os.path.islink(fpath):
                    try:
                        if time.time() - os.path.getmtime(fpath) >= self.cfg.get("cooldown_sec", 30):
                            results.append(fpath)
                    except OSError:
                        pass
        return results

    def preview_plan(self, filepaths: list[str]) -> list[dict]:
        """生成预览计划（不执行）"""
        plan = []
        for fp in filepaths:
            d = self.triage_file(fp)
            if d.get("action") in ("skip", "keep"):
                continue
            dest = d.get("destination", "")
            plan.append({
                "file": d.get("filename", os.path.basename(fp)),
                "source_path": fp,
                "heat": d.get("heat", "?"),
                "action": d.get("action", "?"),
                "destination": f"~/{os.path.relpath(dest, HOME)}" if dest else "-",
                "reason": d.get("reason", ""),
                "days": d.get("days_since_use", 0),
            })
        return plan

    def execute_plan(self, decisions: list[dict], progress_callback=None) -> dict:
        """执行整理计划，返回统计"""
        moved = 0
        errors = 0
        total = len(decisions)
        for i, d in enumerate(decisions):
            # 重建完整文件路径
            fpath = d.get("source_path", os.path.join(d.get("source", ""), d.get("file", "")))
            if not os.path.isfile(fpath):
                errors += 1
                continue
            full_decision = self.triage_file(fpath)  # 重新决策（确保一致性）
            if full_decision["action"] in ("skip", "keep"):
                continue
            if self.execute_move(full_decision):
                moved += 1
            else:
                errors += 1
            if progress_callback:
                progress_callback(i + 1, total)
        if moved > 0:
            self.notify("File Organizer", f"已整理 {moved} 个文件" + (f"，{errors} 个失败" if errors else ""))
        return {"moved": moved, "errors": errors, "total": total}

    def organize_loose_and_uncategorized(self, preview: bool = False) -> list[dict]:
        """只处理根目录散落文件和未分类文件夹（不动已整理好的）"""
        all_files = []
        for wd in self.cfg.get("watch_dirs", []):
            all_files.extend(self.scan_loose_files(wd))
            all_files.extend(self.scan_uncategorized(wd))
        return self.preview_plan(all_files)

    def organize_watched(self) -> list[dict]:
        """整理所有监控目录（文件夹+文件，完整流程）"""
        decisions = []
        for wd in self.cfg.get("watch_dirs", []):
            watch = os.path.expanduser(wd)
            if not os.path.isdir(watch):
                continue
            # 文件夹级分派
            try:
                entries = os.listdir(watch)
            except PermissionError:
                log.warning(f"无权限访问 {watch}，跳过")
                continue
            for entry in entries:
                fpath = os.path.join(watch, entry)
                if os.path.isdir(fpath) and not entry.startswith('.'):
                    fd = self.triage_folder(fpath)
                    if fd:
                        decisions.append(fd)
                        self.execute_folder_move(fd)
            # 根层散落文件
            try:
                for fpath in self.scan_loose_files(wd):
                    d = self.triage_file(fpath)
                    if d["action"] not in ("skip", "keep"):
                        decisions.append(d)
                        self.execute_move(d)
                # 未分类文件
                for fpath in self.scan_uncategorized(wd):
                    d = self.triage_file(fpath)
                    if d["action"] not in ("skip", "keep"):
                        decisions.append(d)
                        self.execute_move(d)
            except PermissionError:
                log.warning(f"扫描 {wd} 时权限不足")
        if decisions:
            folder_n = sum(1 for d in decisions if d.get("action") == "move_folder")
            file_n = len(decisions) - folder_n
            msg = []
            if folder_n:
                msg.append(f"{folder_n}个文件夹")
            if file_n:
                msg.append(f"{file_n}个文件")
            self.notify("File Organizer", f"整理完成: {', '.join(msg)}")
        return decisions

    def desktop_cleanup(self) -> int:
        """桌面瘦身"""
        desktop = os.path.expanduser("~/Desktop")
        if not os.path.isdir(desktop):
            return 0
        max_files = self.cfg.get("desktop_max_files", 20)
        files = []
        try:
            entries = os.listdir(desktop)
        except PermissionError:
            return 0
        for entry in entries:
            fpath = os.path.join(desktop, entry)
            if os.path.isfile(fpath) and entry not in SYSTEM_FILES:
                files.append((self.access.age_days(fpath), fpath))
        files.sort()
        moved = 0
        for i, (days, fpath) in enumerate(files):
            if i < max_files and days <= self.cfg.get("hot_days", 7):
                continue
            d = self.triage_file(fpath)
            if d["action"] not in ("skip", "keep"):
                if self.execute_move(d):
                    moved += 1
        if moved:
            log.info(f"桌面瘦身: {moved}个文件移走")
        return moved

    # ── 清理残留：检测并处理部分移动后的残留目录 ──
    def cleanup_leftovers(self, dry_run: bool = True) -> list[dict]:
        """检测因不完全合并留下的残留目录"""
        leftovers = []
        for wd in self.cfg.get("watch_dirs", []):
            watch = os.path.expanduser(wd)
            if not os.path.isdir(watch):
                continue
            try:
                for entry in os.listdir(watch):
                    fpath = os.path.join(watch, entry)
                    if not os.path.isdir(fpath) or entry.startswith('.'):
                        continue
                    # 检查这个目录是否在其他地方有更大的同名目录
                    name = entry
                    for search_dir in [os.path.join(HOME, "Documents"),
                                       os.path.join(HOME, "Pictures"),
                                       os.path.join(HOME, "Movies")]:
                        if not os.path.isdir(search_dir):
                            continue
                        for root, dirs, _ in os.walk(search_dir):
                            dirs[:] = [d for d in dirs if not d.startswith('.')]
                            if name in dirs:
                                target = os.path.join(root, name)
                                if os.path.samefile(fpath, target):
                                    continue
                                src_count = sum(1 for _ in Path(fpath).rglob("*") if _.is_file() and _.name != '.DS_Store')
                                dst_count = sum(1 for _ in Path(target).rglob("*") if _.is_file() and _.name != '.DS_Store')
                                if src_count > 0 and dst_count >= src_count * 0.8:
                                    leftovers.append({
                                        "src": fpath,
                                        "src_files": src_count,
                                        "dst": target,
                                        "dst_files": dst_count,
                                        "action": "merge" if src_count > 0 else "remove_empty",
                                    })
                                break  # 找到第一个匹配即可
            except PermissionError:
                continue
        return leftovers

    # ── 文件追溯：回答"我的文件去哪了" ──
    def trace_file(self, keyword: str) -> list[dict]:
        """根据关键词搜索移动日志，找到文件去向"""
        results = []
        keyword_lower = keyword.lower()
        for entry in reversed(self.journal.entries):
            if entry.get("rolled_back"):
                continue
            src = entry.get("source", "")
            dst = entry.get("destination", "")
            if keyword_lower in os.path.basename(src).lower() or keyword_lower in src.lower():
                results.append({
                    "file": os.path.basename(src),
                    "from": os.path.relpath(os.path.dirname(src), HOME),
                    "to": os.path.relpath(dst, HOME),
                    "time": entry.get("time", ""),
                    "reason": entry.get("reason", ""),
                })
            if len(results) >= 20:
                break
        return results

    def recent_moves(self, n: int = 15) -> list[dict]:
        """最近移动记录（含完整路径面包屑）"""
        results = []
        for entry in reversed(self.journal.entries):
            if entry.get("rolled_back"):
                continue
            src = entry.get("source", "")
            dst = entry.get("destination", "")
            results.append({
                "file": os.path.basename(src) if src else "",
                "from_dir": os.path.relpath(os.path.dirname(src), HOME) if src else "",
                "to_path": os.path.relpath(dst, HOME) if dst else "",
                "time": entry.get("time", "")[:19],
                "reason": entry.get("reason", ""),
            })
            if len(results) >= n:
                break
        return results

    # ── 学习文件夹秩序：从用户已有组织提取规则 ──
    def learn_from_folder(self, folderpath: str) -> dict:
        """扫描一个已整理好的文件夹，提取其内部结构作为分类规则"""
        if not os.path.isdir(folderpath):
            return {"error": "文件夹不存在"}

        extracted = {}  # {subdir_name: [keywords]}
        folder_name = os.path.basename(folderpath)

        # 扫描一级子目录，提取关键词
        try:
            for entry in os.listdir(folderpath):
                subpath = os.path.join(folderpath, entry)
                if not os.path.isdir(subpath) or entry.startswith('.'):
                    continue
                # 从子目录名提取关键词
                dir_tokens = tokenize(entry)
                # 从子目录内的文件名提取关键词
                file_tokens = []
                try:
                    for f in os.listdir(subpath)[:50]:
                        if os.path.isfile(os.path.join(subpath, f)):
                            file_tokens.extend(tokenize(extract_clean_name(f)))
                except PermissionError:
                    pass

                # 过滤掉单字和无意义短词，按频率排序取前 10
                filtered = [t for t in set(dir_tokens + file_tokens)
                           if len(t) >= 2 and not t.isdigit() and t not in ('的','了','是','在','和','有','个','我','他','她','它','们','这','那','不','都','也','就')]
                all_tokens = sorted(filtered, key=lambda x: (dir_tokens + file_tokens).count(x), reverse=True)[:10]
                if all_tokens:
                    extracted[entry] = all_tokens
        except PermissionError:
            return {"error": "权限不足"}

        if not extracted:
            return {"error": "未找到可学习的子目录结构"}

        # 将提取的结构注册为自定义规则
        rules_added = 0
        for subdir, keywords in extracted.items():
            target = os.path.join(
                os.path.relpath(folderpath, HOME),
                subdir
            )
            # 检查是否已有类似规则（防御性：跳过格式不正确的条目）
            exists = False
            for rule in self.rules.custom:
                if not isinstance(rule, (list, tuple)) or len(rule) < 2:
                    continue
                existing_kws = rule[0]
                if not isinstance(existing_kws, (list, tuple)):
                    continue
                existing_lower = [str(k).lower() for k in existing_kws]
                if any(str(kw).lower() in existing_lower for kw in keywords):
                    exists = True
                    break
            if not exists:
                self.rules.custom.append((keywords, target))
                rules_added += 1

        if rules_added:
            self.rules.save()
            log.info(f"从 {folder_name} 学习了 {rules_added} 条规则: {list(extracted.keys())}")

        return {
            "folder": folder_name,
            "rules_added": rules_added,
            "structure": {k: v[:5] for k, v in extracted.items()},
            "message": f"从 '{folder_name}' 的 {len(extracted)} 个子目录中提取了 {rules_added} 条分类规则",
        }

    # ── 自愈能力 ──
    def health_check(self) -> dict:
        """自诊断"""
        issues = []
        ok = []

        # 检查索引
        if not os.path.exists(GLOBAL_INDEX_FILE):
            issues.append("全局索引缺失")
        elif self.index.total_files < 100:
            issues.append(f"索引文件过少({self.index.total_files})，建议重建")

        # 检查配置
        try:
            cfg = load_config()
            ok.append(f"配置正常(v{cfg.get('version','?')})")
        except Exception as e:
            issues.append(f"配置损坏: {e}")

        # 检查日志大小
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 10 * 1024 * 1024:
            issues.append("日志文件超过10MB")

        # 检查未分类堆积
        uncat = os.path.join(HOME, "Downloads", "未分类")
        if os.path.isdir(uncat):
            count = sum(1 for _ in Path(uncat).rglob("*") if _.is_file())
            if count > 50:
                issues.append(f"未分类堆积({count}个文件)，需要整理")

        # 检查磁盘空间
        try:
            stat = os.statvfs(HOME)
            free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
            if free_gb < 5:
                issues.append(f"磁盘空间不足({free_gb:.1f}GB)")
            else:
                ok.append(f"磁盘空间({free_gb:.1f}GB)")
        except Exception:
            pass

        status = "unhealthy" if issues else "healthy"
        report = {"status": status, "issues": issues, "ok": ok, "time": datetime.now().isoformat()}
        with open(HEALTH_FILE, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return report

    def auto_heal(self) -> dict:
        """自动修复可修复的问题"""
        fixed = []
        health = self.health_check()
        for issue in health["issues"]:
            if "索引缺失" in issue or "索引文件过少" in issue:
                try:
                    self.rebuild_index()
                    fixed.append("已重建全局索引")
                except Exception as e:
                    fixed.append(f"索引重建失败: {e}")
            if "日志文件超过" in issue:
                try:
                    with open(LOG_FILE, "w") as f:
                        f.write(f"# 日志已轮转 {datetime.now().isoformat()}\n")
                    fixed.append("已轮转日志")
                except Exception as e:
                    fixed.append(f"日志轮转失败: {e}")
        return {"fixed": fixed, "health": self.health_check()}

# ═══════════════════════════════════════
# 全局单例 + 快速入口
# ═══════════════════════════════════════
_organizer: Optional[SmartOrganizer] = None

def get_organizer() -> SmartOrganizer:
    global _organizer
    if _organizer is None:
        _organizer = SmartOrganizer()
    return _organizer

def organize_now() -> list[dict]:
    return get_organizer().organize_watched()

def preview_now() -> list[dict]:
    return get_organizer().organize_loose_and_uncategorized(preview=True)

def undo_last() -> Optional[dict]:
    return get_organizer().journal.undo_last()

def undo_all() -> int:
    return get_organizer().journal.undo_all()

def health_report() -> dict:
    return get_organizer().health_check()

def auto_heal() -> dict:
    return get_organizer().auto_heal()

def rebuild_index() -> dict:
    return get_organizer().rebuild_index()
