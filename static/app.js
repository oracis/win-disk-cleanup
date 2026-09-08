"use strict";

const TIER = {
  A: { color: "#3fb950", desc: "缓存/日志/残包/空目录，可直接清" },
  B: { color: "#d29922", desc: "不确定是否安全，请人工核对" },
  C: { color: "#db61a2", desc: "完整安装程序，请走卸载器" },
  D: { color: "#f85149", desc: "个人数据/系统关键，禁止删除" },
};
const TIER_ORDER = ["A", "B", "C", "D"];

let lastScan = null;
const selected = new Set();

// ---------- helpers ----------
async function api(path, method = "GET", body = null) {
  const opt = { method, headers: {} };
  if (body) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  return r.json().catch(() => ({}));
}
function el(id) { return document.getElementById(id); }

// ---------- 盘面 ----------
async function loadDisk() {
  const d = await api("/api/disk");
  const box = el("diskDrives");
  box.innerHTML = "";
  (d.drives || []).forEach(drv => {
    const pct = Math.max(0, Math.min(100, drv.pct));
    const col = pct > 85 ? "var(--red)" : pct > 70 ? "var(--yellow)" : "var(--green)";
    const div = document.createElement("div");
    div.className = "drive";
    div.innerHTML =
      '<div class="root">' + drv.root + '</div>' +
      '<div class="bar"><i style="width:' + pct + '%;background:' + col + '"></i></div>' +
      '<div class="meta">剩 ' + human(drv.free) + ' / 总 ' + human(drv.total) +
      ' · ' + pct.toFixed(1) + '%</div>';
    box.appendChild(div);
  });

  const sys = el("diskSys");
  let html = "";
  (d.sys_files || []).forEach(f => {
    html += '<span>' + f.label + ': <b>' + (f.size != null ? human(f.size) : "不存在") + '</b></span>';
  });
  if (d.upgrades && d.upgrades.length) {
    html += '<span style="color:var(--yellow)">升级残留: <b>' + d.upgrades.join(", ") + '</b>（用系统磁盘清理删）</span>';
  }
  const h = d.hibernate;
  html += '<span>休眠: <b>' + (h == null ? "读不到" : h ? "开启" : "关闭") + '</b></span>';
  sys.innerHTML = html;

  const b = el("adminBadge");
  if (d.admin) { b.textContent = "管理员 ✓"; b.className = "badge badge-ok"; }
  else { b.textContent = "非管理员"; b.className = "badge badge-warn"; }
}

function human(n) {
  n = Number(n) || 0;
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(2) + " " + u[i];
}

// ---------- 扫描 ----------
async function doScan() {
  const path = el("scanPath").value.trim();
  if (!path) { el("scanStatus").textContent = "请先填要扫描的目录"; return; }
  el("btnScan").disabled = true;
  el("scanStatus").textContent = "扫描中…（限时 " + el("scanBudget").value + "s，超时只显示已扫部分）";
  selected.clear();
  const res = await api("/api/scan", "POST", {
    path, budget: Number(el("scanBudget").value) || 60,
    top: 300, excludes: el("scanExclude").value,
  });
  el("btnScan").disabled = false;
  if (!res.ok) { el("scanStatus").textContent = "失败: " + (res.error || "未知"); return; }
  lastScan = res;
  el("scanStatus").textContent = "扫描完成：" + res.count + " 项，合计 " + res.total_h +
    (res.truncated ? "（已超时截断，数字偏低）" : "");
  renderResults(res);
}

function renderResults(res) {
  el("resultCard").style.display = "";
  // summary
  const counts = { A: 0, B: 0, C: 0, D: 0 };
  const sizes = { A: 0, B: 0, C: 0, D: 0 };
  res.items.forEach(it => { counts[it.tier]++; sizes[it.tier] += it.size; });
  const sum = el("tierSummary");
  sum.innerHTML = TIER_ORDER.map(t =>
    '<span class="tier-pill" style="color:' + TIER[t].color + ';border-color:' + TIER[t].color + '">' +
    TIER[t].label.replace(" ", "·") + ' ' + counts[t] + ' 项 / ' + human(sizes[t]) + '</span>'
  ).join("");

  // table grouped by tier
  const wrap = el("resultTable");
  wrap.innerHTML = "";
  TIER_ORDER.forEach(t => {
    const items = res.items.filter(it => it.tier === t);
    if (!items.length) return;
    const grp = document.createElement("div");
    grp.className = "tier-group";
    grp.innerHTML = '<h3><span class="tag" style="background:' + TIER[t].color +
      '22;color:' + TIER[t].color + '">' + TIER[t].label + '</span>' + TIER[t].desc + '</h3>';
    const tbl = document.createElement("table");
    tbl.innerHTML = '<thead><tr><th style="width:28px"></th><th>名称</th><th>大小</th>' +
      '<th>文件</th><th>说明</th></tr></thead>';
    const tb = document.createElement("tbody");
    items.forEach(it => {
      const tr = document.createElement("tr");
      if (t === "D") tr.className = "disabled";
      const cb = t === "D"
        ? ""
        : '<input type="checkbox" data-path="' + esc(it.path) + '">';
      tr.innerHTML = '<td>' + cb + '</td>' +
        '<td class="name" title="' + esc(it.path) + '">' + esc(it.name) +
        (it.tmo ? ' <span style="color:var(--yellow)">[TMO]</span>' : "") + '</td>' +
        '<td class="size">' + it.size_h + '</td>' +
        '<td>' + it.files + '</td>' +
        '<td class="reason">' + esc(it.reason) + '</td>';
      tb.appendChild(tr);
    });
    tbl.appendChild(tb);
    grp.appendChild(tbl);
    wrap.appendChild(grp);
  });

  // bind checkboxes
  wrap.querySelectorAll('input[type=checkbox]').forEach(cb => {
    cb.addEventListener("change", () => {
      const p = cb.getAttribute("data-path");
      if (cb.checked) selected.add(p); else selected.delete(p);
      updateSel();
    });
  });
  updateSel();
}

function updateSel() {
  let sz = 0;
  selected.forEach(p => {
    const it = lastScan && lastScan.items.find(x => x.path === p);
    if (it) sz += it.size;
  });
  el("selInfo").innerHTML = "已选 <b>" + selected.size + "</b> 项 · 合计 <b>" + human(sz) + "</b>";
  el("btnClean").disabled = selected.size === 0;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- 删除 ----------
function openConfirm() {
  const paths = [...selected];
  let sz = 0;
  paths.forEach(p => { const it = lastScan.items.find(x => x.path === p); if (it) sz += it.size; });
  el("confirmCount").textContent = paths.length;
  el("confirmSize").textContent = human(sz);
  el("confirmList").innerHTML = paths.map(p => "<li>" + esc(p) + "</li>").join("");
  el("confirmModal").style.display = "flex";
}
function closeConfirm() { el("confirmModal").style.display = "none"; }

async function doClean() {
  const paths = [...selected];
  el("btnDoClean").disabled = true;
  const res = await api("/api/clean", "POST", { paths, confirm: true });
  el("btnDoClean").disabled = false;
  closeConfirm();
  if (res.ok) {
    const ok = res.results.filter(r => r.ok).length;
    const fail = res.results.length - ok;
    alert("删除完成：成功 " + ok + " / 失败 " + fail + "\n释放 " + (res.freed_total_h || human(res.freed_total)));
    selected.clear();
    await loadLog();
    // 重新扫描当前路径看变化
    if (lastScan) doScan();
  } else {
    alert("删除被拒绝：" + (res.error || "未知"));
  }
}

// ---------- 日志 ----------
async function loadLog() {
  const r = await api("/api/log");
  el("logView").textContent = (r.lines && r.lines.length) ? r.lines.join("\n") : "（暂无日志）";
}

// ---------- 绑定 ----------
document.addEventListener("DOMContentLoaded", () => {
  el("btnScan").addEventListener("click", doScan);
  el("btnClean").addEventListener("click", openConfirm);
  el("btnCancel").addEventListener("click", closeConfirm);
  el("btnDoClean").addEventListener("click", doClean);
  el("btnLog").addEventListener("click", loadLog);
  document.querySelectorAll(".chip").forEach(c => {
    c.addEventListener("click", () => { el("scanPath").value = c.getAttribute("data-p"); });
  });
  loadDisk();
  loadLog();
});
