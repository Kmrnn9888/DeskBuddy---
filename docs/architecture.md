# Architecture

## Overview

DeskBuddy v4.1 is a local-first macOS file management system with a three-layer architecture:

```
┌─────────────────────────────────────────────┐
│                  Web UI                     │
│          (localhost:8899, SPA)              │
│    Dashboard │ Preview │ Settings │ Rules    │
└──────────────────┬──────────────────────────┘
                   │ HTTP API
┌──────────────────┴──────────────────────────┐
│            SmartOrganizer                   │
│  ┌──────────┐ ┌──────────┐ ┌─────────────┐ │
│  │ triage   │ │ execute  │ │ undo/recent  │ │
│  │ (decide) │ │ (move)   │ │ (rollback)   │ │
│  └──────────┘ └──────────┘ └─────────────┘ │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────┴──────────────────────────┐
│           Subsystems                        │
│  ┌───────────┐ ┌──────────┐ ┌────────────┐ │
│  │GlobalIndex│ │RuleEngine│ │MoveJournal │ │
│  │ (TF-IDF)  │ │(builtin+ │ │(undo log)  │ │
│  │           │ │ learned) │ │            │ │
│  └───────────┘ └──────────┘ └────────────┘ │
│  ┌────────────┐ ┌──────────┐ ┌───────────┐ │
│  │Access      │ │Content   │ │Health     │ │
│  │Analyzer    │ │Analyzer  │ │Monitor    │ │
│  │(hot/cold)  │ │(text)    │ │(self-heal)│ │
│  └────────────┘ └──────────┘ └───────────┘ │
└─────────────────────────────────────────────┘
```

## Components

### engine.py (core, ~900 lines)

The entire classification and file operation logic. Key classes:

| Class | Purpose |
|-------|---------|
| `CFG` | Hyperparameter constants — all magic numbers centralized |
| `SmartOrganizer` | Main orchestrator. Entry point for all operations. |
| `GlobalIndex` | Scans home directory, builds extension→location and keyword→location maps using TF-IDF. Outputs destination predictions with confidence scores. |
| `RuleEngine` | Built-in rules + custom rules + feedback learning. Keywords matched against filenames + content to determine target directories. |
| `AccessAnalyzer` | Classifies files as hot (≤7d), warm (≤30d), cold (≤90d), or frozen (>90d) based on atime/mtime. |
| `ContentAnalyzer` | Reads text content from .txt, .md, .json, .html, .csv, .log, source code files. Extracts keywords for classification enhancement. |
| `MoveJournal` | Append-only JSONL log of every file/folder move. Powers undo, trace, and recent moves display. Supports 500-entry rolling window. |

### Classification Pipeline

```
1. Rule match (custom → builtin → feedback)
   ↓ miss
2. Global index prediction (TF-IDF, with depth penalty)
   ↓ low confidence
3. Content analysis (read file, extract keywords)
   ↓ still no match
4. Extension default (built-in ext→directory map)
   ↓ no default
5. Uncategorized fallback
```

Confidence thresholds (all configurable via `CFG`):
- Normal files: minimum 25% confidence for index match
- Non-descriptive names (<4 chars): minimum 55% confidence
- Deep directories (>3 levels) + low confidence: auto-fallback

### app_web.py (HTTP server, ~320 lines)

A zero-dependency HTTP server using only `http.server` from stdlib. SPA loaded from `templates/index.html`. No frameworks.

API Endpoints:
- `GET /` — SPA dashboard
- `GET /api/status` — stats, health, version
- `GET /api/settings` — current config
- `GET /api/rules` — all rules (builtin + custom)
- `GET /preview` — files needing organization
- `GET /recent-moves` — last 30 moves with paths
- `GET /trace?q=keyword` — search move history
- `GET /leftovers` — detect partial-move remnants
- `GET /health` — self-diagnosis report
- `GET /export-config` — export settings + rules as JSON
- `POST /execute` — run preview plan with confirm
- `POST /undo` — undo last move
- `POST /undo-all` — undo all unrolled-back moves
- `POST /learn-folder` — extract rules from folder structure
- `POST /cleanup-empty-dirs` — remove empty directories

### launcher.py (menu bar app, ~190 lines)

macOS menu bar application using `rumps`. Features:
- Status indicator (✅ running / ⚠️ issues)
- Quick actions: organize, preview, undo, cleanup empty dirs, health check
- Server health monitoring (every 30s)
- Auto-restart crashed web server

### install.sh (deployment script)

Sets up the complete system:
1. Kills old processes
2. Installs `jieba` dependency
3. Copies source files + templates to `~/.file-organizer/src/`
4. Creates launchd plists for WatchPaths monitoring + web server KeepAlive
5. Loads launchd services
6. Builds initial global file index

## Data Flow

```
File created/modified on Desktop/Downloads
    │
    ▼ (60s throttle)
launchd WatchPaths triggers
    │
    ▼
engine.py triage_file() → classify → decide destination
    │
    ▼
execute_move() → SHA256 checksum → shutil.move → verify → journal
    │
    ▼
macOS notification: "file.pdf → Documents/Learning/"
    │
    ▼ (optional)
User sees notification → opens localhost:8899 → reviews → undo if wrong
    │
    ▼
System learns from correction → adjusts keyword weights
```

## Safety Features (v4.1)

| Feature | Implementation |
|---------|---------------|
| fcntl file locking | `_locked_read()` / `_locked_write()` wrappers on all JSON I/O |
| Path sanitization | `_safe_path()` uses `os.path.realpath()` to prevent traversal |
| Transactional moves | SHA256 before/after, auto-rollback on mismatch |
| Safe naming | `extract_clean_name()` strips path separators and UUIDs |
| Depth limits | `cleanup_leftovers()` capped at 4 levels of os.walk |

## Design Decisions

**Why no database?** — JSON files are sufficient for the data volume (config, rules, index, journal). Zero setup burden. fcntl locking prevents corruption.

**Why no web framework?** — `http.server` from stdlib avoids dependency hell. HTML now in separate `templates/index.html`.

**Why jieba?** — Best Chinese tokenization library with zero external dependencies. Falls back to regex bigram if not installed.

**Why launchd instead of a daemon?** — macOS-native. Survives reboots. WatchPaths avoids polling. KeepAlive handles crashes.

**Why not process PDFs/DOCX?** — Would require heavy dependencies (PyPDF2, python-docx). Text content analysis covers 80% of cases with zero cost.
