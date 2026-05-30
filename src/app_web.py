#!/usr/bin/env python3
"""
File Organizer v4 — Web 控制面板
自愈引擎：内容分析 + 事务移动 + 撤销 + 反馈学习
"""
import os, sys, json, time, threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))
from engine_v4 import (
    SmartOrganizer, log, LOG_FILE, CONFIG_FILE, VERSION,
    load_config, save_config, organize_now, preview_now,
    undo_last, undo_all, health_report, auto_heal, rebuild_index,
    HOME, MoveJournal,
)

PORT = 8899
organizer = SmartOrganizer()
monitor_thread = None
monitoring = True  # v4: 默认开启

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>File Organizer v4</title>
<style>
:root {
  --bg: #0f0f1a; --surface: #1a1a2e; --surface2: #2a2a3e;
  --fg: #e0e0f0; --accent: #7eb8ff; --green: #5cdb8b;
  --red: #ff5e7a; --yellow: #f0c060; --orange: #ff8c52;
  --border: #333355; --radius: 10px;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
  background: var(--bg); color: var(--fg); min-height: 100vh; }
header { background: var(--surface); padding: 14px 28px; display: flex;
  align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); }
header h1 { font-size: 18px; color: var(--accent); }
.status-row { display: flex; align-items: center; gap: 10px; font-size: 13px; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot.on { background: var(--green); box-shadow: 0 0 6px var(--green); }
.dot.off { background: var(--red); }
.dot.warn { background: var(--yellow); }
nav { display: flex; gap: 2px; padding: 10px 28px; background: var(--surface); border-bottom: 1px solid var(--border); }
nav button { background: none; border: none; color: #8888aa; padding: 8px 18px;
  border-radius: var(--radius); cursor: pointer; font-size: 13px; transition: .2s; }
nav button:hover { color: var(--fg); background: var(--surface2); }
nav button.active { background: var(--accent); color: var(--bg); font-weight: 600; }
main { padding: 20px 28px; max-width: 960px; margin: 0 auto; }
.tab { display: none; }
.tab.active { display: block; }

.cards { display: grid; grid-template-columns: repeat(5,1fr); gap: 10px; margin-bottom: 18px; }
.card { background: var(--surface); padding: 16px; border-radius: var(--radius);
  border: 1px solid var(--border); text-align: center; }
.card .num { font-size: 26px; font-weight: 700; margin: 4px 0; }
.card .num.g { color: var(--green); } .card .num.b { color: var(--accent); }
.card .num.y { color: var(--yellow); } .card .num.r { color: var(--red); }
.card .num.o { color: var(--orange); }
.card .lbl { font-size: 11px; opacity: 0.6; }

.actions { display: flex; gap: 8px; margin-bottom: 18px; flex-wrap: wrap; }
.btn { padding: 9px 20px; border: none; border-radius: var(--radius);
  font-size: 13px; cursor: pointer; transition: .2s; font-weight: 500; }
.btn.p { background: var(--accent); color: var(--bg); }
.btn.s { background: var(--green); color: var(--bg); }
.btn.d { background: var(--red); color: #fff; }
.btn.w { background: var(--orange); color: #fff; }
.btn.g { background: var(--surface); color: var(--fg); border: 1px solid var(--border); }
.btn:hover { opacity: .85; transform: translateY(-1px); }
.btn:disabled { opacity: .4; cursor: not-allowed; transform: none; }

.table-wrap { background: var(--surface); border-radius: var(--radius);
  border: 1px solid var(--border); overflow: auto; max-height: 420px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { background: var(--surface2); padding: 8px 12px; text-align: left; font-weight: 600;
  position: sticky; top: 0; }
td { padding: 7px 12px; border-bottom: 1px solid rgba(255,255,255,0.04); }
tr:hover { background: rgba(255,255,255,0.03); }
.badge { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 10px;
  font-weight: 600; }
.badge.h { background: var(--accent); color: var(--bg); }
.badge.w { background: var(--yellow); color: var(--bg); }
.badge.c { background: var(--orange); color: var(--bg); }
.badge.f { background: var(--red); color: #fff; }
.badge.g { background: var(--green); color: var(--bg); }

.form-group { margin-bottom: 12px; }
.form-group label { display: block; font-size: 12px; margin-bottom: 3px; color: var(--accent); }
.form-group input, .form-group select { width: 100%; padding: 8px 12px;
  background: var(--surface); border: 1px solid var(--border); border-radius: 6px;
  color: var(--fg); font-size: 13px; }
.form-row { display: flex; gap: 12px; }
.form-row .form-group { flex: 1; }

.log-view { background: #0a0a16; border-radius: var(--radius); border: 1px solid var(--border);
  padding: 14px; font-family: "SF Mono", Monaco, monospace; font-size: 11px;
  max-height: 450px; overflow-y: auto; white-space: pre-wrap; line-height: 1.5;
  color: #aabbcc; }

.progress-wrap { display: none; margin: 12px 0; background: var(--surface);
  border-radius: var(--radius); padding: 12px; border: 1px solid var(--border); }
.progress-bar { height: 6px; background: var(--surface2); border-radius: 3px; overflow: hidden; }
.progress-fill { height: 100%; background: var(--accent); width: 0%;
  transition: width .3s; border-radius: 3px; }
.progress-text { font-size: 12px; margin-top: 6px; text-align: center; }

.toast { position: fixed; bottom: 24px; right: 24px; padding: 12px 24px;
  border-radius: var(--radius); font-weight: 600; font-size: 13px;
  opacity: 0; transition: opacity .3s; pointer-events: none; z-index: 999; }
.toast.ok { background: var(--green); color: var(--bg); }
.toast.err { background: var(--red); color: #fff; }
.toast.show { opacity: 1; }

.confirm-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6);
  z-index: 1000; justify-content: center; align-items: center; }
.confirm-overlay.show { display: flex; }
.confirm-box { background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 24px; max-width: 480px; width: 90%; }
.confirm-box h3 { margin-bottom: 12px; }
.confirm-box .btn { margin: 4px; }

.check-row { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
.check-row input[type=checkbox] { width: 16px; height: 16px; accent-color: var(--accent); }
.fade { opacity: .4; font-size: 12px; }
</style>
</head>
<body>

<header>
  <div>
    <h1>📁 File Organizer <span style="font-size:13px;opacity:.5;">v4.0</span></h1>
  </div>
  <div class="status-row">
    <span id="healthText">健康</span>
    <span class="dot on" id="healthDot"></span>
    <span style="opacity:.5;">|</span>
    <span id="statusText">监控中</span>
    <span class="dot on" id="statusDot"></span>
  </div>
</header>

<nav>
  <button class="active" onclick="switchTab('dashboard',this)">📊 仪表盘</button>
  <button onclick="switchTab('organize',this)">📦 整理</button>
  <button onclick="switchTab('settings',this)">⚙ 设置</button>
  <button onclick="switchTab('rules',this)">📋 规则</button>
  <button onclick="switchTab('log',this)">📜 日志</button>
</nav>

<main>
  <!-- Dashboard -->
  <div class="tab active" id="tab-dashboard">
    <div class="cards">
      <div class="card"><div class="lbl">已整理</div><div class="num g" id="sOrganized">0</div></div>
      <div class="card"><div class="lbl">桌面保留</div><div class="num b" id="sKept">0</div></div>
      <div class="card"><div class="lbl">已学规则</div><div class="num y" id="sRules">0</div></div>
      <div class="card"><div class="lbl">全局索引</div><div class="num y" id="sIndexed">0</div></div>
      <div class="card"><div class="lbl">错误</div><div class="num o" id="sErrors">0</div></div>
    </div>
    <div class="actions">
      <button class="btn p" onclick="quickOrganize()">▶ 快速整理</button>
      <button class="btn g" onclick="loadPreview()">👁 预览待整理</button>
      <button class="btn g" onclick="undoLast()">↩ 撤销上一步</button>
      <button class="btn w" onclick="apiPost('/desktop-cleanup')">🧹 桌面瘦身</button>
      <button class="btn g" onclick="checkHealth()">💚 健康检查</button>
    </div>
    <!-- 查找文件 -->
    <div style="display:flex;gap:8px;margin-bottom:14px;">
      <input id="traceInput" placeholder="🔍 我的文件去哪了？输入文件名关键词..." style="flex:1;padding:8px 12px;background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--fg);font-size:13px;" onkeydown="if(event.key==='Enter')traceFile()">
      <button class="btn p" onclick="traceFile()">查找</button>
    </div>
    <div id="traceResults" style="display:none;margin-bottom:14px;background:var(--surface);border-radius:var(--radius);border:1px solid var(--accent);padding:12px;font-size:12px;"></div>
    <!-- 最近移动 -->
    <div style="font-size:13px;margin-bottom:4px;color:var(--accent);">📋 最近移动记录（文件去哪了）</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>时间</th><th>文件</th><th>原来位置 → 现在位置</th><th>原因</th></tr></thead>
        <tbody id="recentTable"><tr><td colspan="4" class="fade">暂无活动</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- Organize Tab -->
  <div class="tab" id="tab-organize">
    <h3 style="margin-bottom:12px;">待整理文件预览</h3>
    <p style="font-size:12px;opacity:.6;margin-bottom:12px;">仅显示根目录散落文件和"未分类"文件夹中的文件，不动已整理好的</p>
    <div class="actions">
      <button class="btn p" onclick="loadPreview()">🔄 刷新预览</button>
      <button class="btn s" id="btnExecute" onclick="executePlan()" disabled>✅ 执行整理</button>
      <button class="btn d" id="btnUndoAll" onclick="undoAll()">↩ 全部撤销</button>
    </div>
    <div class="progress-wrap" id="progressWrap">
      <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
      <div class="progress-text" id="progressText">准备中...</div>
    </div>
    <div class="table-wrap" style="max-height:450px;">
      <table>
        <thead><tr><th>文件</th><th>热度</th><th>操作</th><th>目标路径</th><th>原因</th></tr></thead>
        <tbody id="previewTable"><tr><td colspan="5" class="fade">点击「刷新预览」查看</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- Settings -->
  <div class="tab" id="tab-settings">
    <div class="form-group"><label>监控目录（逗号分隔）</label><input id="watchDirs"></div>
    <div class="form-row">
      <div class="form-group"><label>热文件(天)</label><input id="hotDays" type="number"></div>
      <div class="form-group"><label>温文件(天)</label><input id="warmDays" type="number"></div>
      <div class="form-group"><label>冷文件(天)</label><input id="coldDays" type="number"></div>
    </div>
    <div class="form-row">
      <div class="form-group"><label>桌面最多文件数</label><input id="desktopMax" type="number"></div>
      <div class="form-group"><label>冷却时间(秒)</label><input id="cooldown" type="number"></div>
      <div class="form-group"><label>轮询间隔(秒)</label><input id="pollInterval" type="number"></div>
    </div>
    <div class="check-row"><input type="checkbox" id="autoOrganize"><label>自动整理</label></div>
    <div class="check-row"><input type="checkbox" id="learnEnabled"><label>全局索引学习</label></div>
    <div class="check-row"><input type="checkbox" id="contentAnalysis"><label>内容分析</label></div>
    <div class="check-row"><input type="checkbox" id="feedbackLearning"><label>反馈学习</label></div>
    <div class="check-row"><input type="checkbox" id="notifications"><label>桌面通知</label></div>
    <div class="actions" style="margin-top:12px;">
      <button class="btn p" onclick="saveSettings()">💾 保存设置</button>
      <button class="btn w" onclick="apiPost('/learn')">🔄 重建索引</button>
    </div>
  </div>

  <!-- Rules -->
  <div class="tab" id="tab-rules">
    <div class="form-row" style="margin-bottom:10px;">
      <div class="form-group"><label>关键词（逗号分隔）</label><input id="ruleKeywords" placeholder="如: 风环境, 微气候"></div>
      <div class="form-group"><label>目标路径</label><input id="ruleTarget" placeholder="如: 学习/建筑学/风环境模拟"></div>
    </div>
    <button class="btn p" onclick="addRule()">+ 添加规则</button>
    <div class="table-wrap" style="margin-top:12px;">
      <table>
        <thead><tr><th>类型</th><th>关键词</th><th>目标路径</th><th>操作</th></tr></thead>
        <tbody id="rulesTable"></tbody>
      </table>
    </div>
  </div>

  <!-- Log -->
  <div class="tab" id="tab-log">
    <div class="actions">
      <button class="btn g" onclick="loadLog()">🔄 刷新</button>
      <button class="btn d" onclick="apiPost('/clearlog')">🗑 清空</button>
      <button class="btn g" onclick="checkHealth()">💚 健康检查</button>
    </div>
    <div class="log-view" id="logView">加载中...</div>
  </div>
</main>

<div id="confirmOverlay" class="confirm-overlay">
  <div class="confirm-box">
    <h3>确认执行整理</h3>
    <p id="confirmMsg" style="font-size:13px;margin-bottom:12px;"></p>
    <div style="display:flex;gap:8px;justify-content:flex-end;">
      <button class="btn g" onclick="closeConfirm()">取消</button>
      <button class="btn s" id="confirmOk" onclick="confirmedExecute()">确认执行</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let previewData = [];
let monitorOn = true;

function switchTab(name, btn) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  if(btn) btn.classList.add('active');
  if(name==='log') loadLog();
  if(name==='settings') loadSettings();
  if(name==='rules') loadRules();
  if(name==='organize') loadPreview();
}

function toast(msg, cls='ok') {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = 'toast '+cls+' show';
  setTimeout(() => t.classList.remove('show'), 2800);
}

async function apiPost(path, body) {
  try {
    const opts = {method: 'POST'};
    if(body) opts.body = JSON.stringify(body);
    const r = await fetch(path, opts);
    const d = await r.json();
    if(d.message) toast(d.message, d.error?'err':'ok');
    refreshDashboard();
    return d;
  } catch(e) { toast('连接失败: '+e.message, 'err'); }
}

async function refreshDashboard() {
  try {
    const r = await fetch('/api/status'); const s = await r.json();
    document.getElementById('sOrganized').textContent = (s.total_organized||0);
    document.getElementById('sKept').textContent = (s.kept_hot||0);
    document.getElementById('sRules').textContent = (s.learned_rules||0);
    document.getElementById('sIndexed').textContent = (s.indexed_files||0)+'/'+ (s.indexed_extensions||0);
    document.getElementById('sErrors').textContent = (s.errors||0);
    const hd = document.getElementById('healthDot');
    const ht = document.getElementById('healthText');
    if(s.health==='unhealthy') { hd.className='dot warn'; ht.textContent='需关注'; }
    else { hd.className='dot on'; ht.textContent='健康'; }
  } catch(e) {}
  // 加载最近移动记录
  try {
    const r2 = await fetch('/recent-moves'); const m = await r2.json();
    const tbody = document.getElementById('recentTable');
    if(m.moves && m.moves.length) {
      tbody.innerHTML = m.moves.map(e =>
        `<tr>
          <td style="font-size:10px;opacity:.6;">${e.time||''}</td>
          <td><b>${e.file.slice(0,30)||''}</b></td>
          <td style="font-size:11px;"><span style="opacity:.5;">~/${e.from_dir||''}/</span> → <span style="color:var(--accent);">~/${e.to_path||''}</span></td>
          <td style="font-size:10px;opacity:.5;">${e.reason||''}</td>
        </tr>`
      ).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="4" class="fade">暂无移动记录</td></tr>';
    }
  } catch(e) {}
}

async function traceFile() {
  const q = document.getElementById('traceInput').value.trim();
  if(!q) return toast('请输入文件名关键词','err');
  const r = await fetch('/trace?q='+encodeURIComponent(q));
  const d = await r.json();
  const el = document.getElementById('traceResults');
  if(!d.results || !d.results.length) {
    el.style.display = 'block';
    el.innerHTML = '❌ 未找到包含「'+q+'」的文件移动记录。试试其他关键词，或者文件可能未被移动过。';
    return;
  }
  el.style.display = 'block';
  el.innerHTML = '<b>找到 '+d.results.length+' 条记录：</b><br><br>' + d.results.map(e =>
    `<div style="margin-bottom:6px;">📄 <b>${e.file}</b><br>`
    + `<span style="opacity:.5;">~/${e.from}/</span> → <span style="color:var(--green);">~/${e.to}</span>`
    + `<span style="opacity:.4;font-size:10px;margin-left:8px;">${e.time}</span></div>`
  ).join('');
}

async function loadPreview() {
  const r = await fetch('/preview'); const d = await r.json();
  previewData = d.decisions || [];
  const tbody = document.getElementById('previewTable');
  const btn = document.getElementById('btnExecute');
  if(!previewData.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="fade">✅ 没有需要整理的文件（根目录干净，未分类为空）</td></tr>';
    btn.disabled = true;
    return;
  }
  btn.disabled = false;
  tbody.innerHTML = previewData.map((d,i) =>
    `<tr>
      <td title="${d.source_path||''}">${d.file.slice(0,45)}</td>
      <td><span class="badge ${d.heat}">${d.heat}</span></td>
      <td>${d.action==='move'?'📦 移走':d.action==='archive'?'📂 归档':d.action}</td>
      <td style="font-size:11px;">${d.destination}</td>
      <td style="font-size:11px;opacity:.7;">${d.reason}</td>
    </tr>`
  ).join('');
  toast(`预览: ${previewData.length} 个文件待整理`);
}

async function executePlan() {
  if(!previewData.length) return;
  document.getElementById('confirmMsg').textContent =
    `将整理 ${previewData.length} 个文件到各自的目标位置。确认执行？`;
  document.getElementById('confirmOverlay').classList.add('show');
}

function closeConfirm() {
  document.getElementById('confirmOverlay').classList.remove('show');
}

async function confirmedExecute() {
  closeConfirm();
  const wrap = document.getElementById('progressWrap');
  const fill = document.getElementById('progressFill');
  const text = document.getElementById('progressText');
  wrap.style.display = 'block';
  fill.style.width = '0%';
  text.textContent = '执行中...';

  const r = await fetch('/execute', {method:'POST'});
  const d = await r.json();

  fill.style.width = '100%';
  text.textContent = `完成: 移动 ${d.moved||0} 个, 失败 ${d.errors||0} 个`;
  setTimeout(() => { wrap.style.display = 'none'; }, 3000);

  toast(`✅ 已整理 ${d.moved||0} 个文件` + (d.errors ? `, ${d.errors} 个失败` : ''));
  previewData = [];
  document.getElementById('btnExecute').disabled = true;
  document.getElementById('previewTable').innerHTML =
    '<tr><td colspan="5" class="fade">整理完成，点击刷新预览查看最新状态</td></tr>';
  refreshDashboard();
}

async function undoLast() {
  const r = await fetch('/undo', {method:'POST'});
  const d = await r.json();
  toast(d.message||'已撤销', d.error?'err':'ok');
  refreshDashboard();
  loadPreview();
}

async function undoAll() {
  if(!confirm('确认撤销所有未回滚的移动操作？')) return;
  const r = await fetch('/undo-all', {method:'POST'});
  const d = await r.json();
  toast(d.message||'已全部撤销', d.error?'err':'ok');
  refreshDashboard();
  loadPreview();
}

async function quickOrganize() {
  await loadPreview();
  if(previewData.length) {
    document.getElementById('confirmMsg').textContent =
      `快速整理: ${previewData.length} 个文件待处理。确认执行？`;
    document.getElementById('confirmOverlay').classList.add('show');
  }
}

async function checkHealth() {
  const r = await fetch('/health'); const d = await r.json();
  if(d.issues && d.issues.length) {
    toast('⚠ ' + d.issues.join('; '), 'err');
  } else {
    toast('✅ 系统健康: ' + (d.ok||[]).join(', '));
  }
}

async function loadSettings() {
  const r = await fetch('/api/settings'); const s = await r.json();
  document.getElementById('watchDirs').value = (s.watch_dirs||[]).join(', ');
  document.getElementById('hotDays').value = s.hot_days||7;
  document.getElementById('warmDays').value = s.warm_days||30;
  document.getElementById('coldDays').value = s.cold_days||90;
  document.getElementById('desktopMax').value = s.desktop_max_files||20;
  document.getElementById('cooldown').value = s.cooldown_sec||30;
  document.getElementById('pollInterval').value = s.poll_interval_sec||60;
  document.getElementById('autoOrganize').checked = s.auto_organize!==false;
  document.getElementById('learnEnabled').checked = s.use_global_index!==false;
  document.getElementById('contentAnalysis').checked = s.use_content_analysis!==false;
  document.getElementById('feedbackLearning').checked = s.use_feedback_learning!==false;
  document.getElementById('notifications').checked = s.notifications!==false;
}

async function saveSettings() {
  const s = {
    watch_dirs: document.getElementById('watchDirs').value.split(',').map(x=>x.trim()).filter(Boolean),
    hot_days: parseInt(document.getElementById('hotDays').value)||7,
    warm_days: parseInt(document.getElementById('warmDays').value)||30,
    cold_days: parseInt(document.getElementById('coldDays').value)||90,
    desktop_max_files: parseInt(document.getElementById('desktopMax').value)||20,
    cooldown_sec: parseInt(document.getElementById('cooldown').value)||30,
    poll_interval_sec: parseInt(document.getElementById('pollInterval').value)||60,
    auto_organize: document.getElementById('autoOrganize').checked,
    use_global_index: document.getElementById('learnEnabled').checked,
    use_content_analysis: document.getElementById('contentAnalysis').checked,
    use_feedback_learning: document.getElementById('feedbackLearning').checked,
    notifications: document.getElementById('notifications').checked,
  };
  await apiPost('/api/settings', s);
}

async function loadRules() {
  const r = await fetch('/api/rules'); const d = await r.json();
  document.getElementById('rulesTable').innerHTML = (d.rules||[]).map((r,i) =>
    `<tr><td>${r.type}</td><td>${r.keywords}</td><td>${r.target}</td>
    <td>${r.type==='自定义'?`<button class="btn d" style="padding:3px 10px;font-size:11px;" onclick="delRule(${i})">删除</button>`:'-'}</td></tr>`
  ).join('');
}

async function addRule() {
  const kw = document.getElementById('ruleKeywords').value.trim();
  const tgt = document.getElementById('ruleTarget').value.trim();
  if(!kw||!tgt) return toast('请填写完整', 'err');
  await apiPost('/api/rules', {keywords:kw, target:tgt});
  document.getElementById('ruleKeywords').value='';
  document.getElementById('ruleTarget').value='';
  loadRules();
}

async function delRule(i) {
  await fetch('/api/rules/'+i, {method:'DELETE'});
  loadRules();
  toast('规则已删除');
}

async function loadLog() {
  const r = await fetch('/api/log');
  document.getElementById('logView').textContent = (await r.json()).log||'(空)';
  document.getElementById('logView').scrollTop = document.getElementById('logView').scrollHeight;
}

refreshDashboard();
setInterval(refreshDashboard, 8000);
</script>
</body>
</html>"""

# ═══════════════════════════════════════
# HTTP API
# ═══════════════════════════════════════
class APIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

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
                    with open(LOG_FILE) as f:
                        for line in f.readlines()[-60:]:
                            if "→" in line:
                                parts = line.strip().split(" ", 2)
                                time_str = parts[0][11:19] if len(parts) > 0 else ""
                                rest = parts[-1][:100] if parts else line.strip()
                                # 解析文件名和目标
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
                except Exception:
                    pass
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
            from engine_v4 import BUILTIN_RULES
            all_rules = []
            for kws, target in BUILTIN_RULES:
                all_rules.append({"type": "内置", "keywords": ", ".join(kws), "target": target})
            for kws, target in organizer.rules.custom:
                all_rules.append({"type": "自定义", "keywords": ", ".join(kws), "target": target})
            self._send({"rules": all_rules})

        elif path == "/api/log":
            log_content = ""
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    log_content = "".join(f.readlines()[-300:])
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
                n = organizer.desktop_cleanup()
                log.info(f"桌面瘦身: {n}个文件")
            threading.Thread(target=_cleanup, daemon=True).start()
            self._send({"message": "正在桌面瘦身..."})

        elif path == "/learn":
            def _learn():
                result = organizer.rebuild_index()
                log.info(f"重建索引: {result}")
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
                organizer.access = __import__('engine_v4').AccessAnalyzer(current)
                self._send({"message": "设置已保存"})
            except Exception as e:
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
                self._send({"error": True, "message": str(e)}, 400)

        elif path == "/clearlog":
            with open(LOG_FILE, "w") as f:
                f.write("")
            self._send({"message": "日志已清空"})

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

# ═══════════════════════════════════════
# 后台监控
# ═══════════════════════════════════════
def monitor_loop():
    # 首次启动等待 120 秒，避免刚启动就移动文件
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
            # 每小时自检
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

# ═══════════════════════════════════════
# 主入口
# ═══════════════════════════════════════
def main():
    start_monitor()
    # 启动时自检
    health = organizer.health_check()
    if health["issues"]:
        log.warning(f"健康检查发现问题: {health['issues']}")
        organizer.auto_heal()

    print(f"""
  ╔══════════════════════════════════════╗
  ║   File Organizer v4.0               ║
  ║   自愈智能引擎                        ║
  ║                                     ║
  ║   👉 http://localhost:{PORT}         ║
  ║                                     ║
  ║   按 Ctrl+C 退出                     ║
  ╚══════════════════════════════════════╝
    """)
    # 不再自动打开浏览器，用户手动访问 localhost:8899
    server = HTTPServer(("127.0.0.1", PORT), APIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_monitor()
        server.shutdown()
        print("\nFile Organizer v4 已停止")

if __name__ == "__main__":
    main()
