const $ = (id) => document.getElementById(id);
const BASE_PATH = document.querySelector('meta[name="base-path"]')?.content || "";
const appUrl = (path) => `${BASE_PATH}${path}`;
let csrfToken = "";
let lastFilter = {};
let eventsCache = {syslog: [], snmp: [], mail: []};

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (csrfToken && ["POST", "PUT", "PATCH", "DELETE"].includes((options.method || "GET").toUpperCase())) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(appUrl(path), {...options, headers});
  if (response.status === 401) { showLogin(); throw new Error("ログインしてください"); }
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).detail || message; } catch {}
    throw new Error(message);
  }
  return response;
}
function showLogin() { const d = $("login-dialog"); if (!d.open) d.showModal(); }
function utcDate(value) { return value ? new Date(value).toISOString().replace("Z", "+00:00") : ""; }
function queryFilters(kinds) {
  const p = new URLSearchParams();
  if ($("from").value) p.set("from", utcDate($("from").value));
  if ($("to").value) p.set("to", utcDate($("to").value));
  if ($("q").value.trim()) p.set("q", $("q").value.trim());
  for (const kind of kinds) p.append("kind", kind);
  for (const option of $("nodes").selectedOptions) {
    const [type, id] = option.value.split(":", 2);
    if (type === "n") p.append("node_id", id);
    else if (type === "g") p.append("group_id", id);
    else if (type === "u") p.append("source_ip", id);
  }
  return p;
}
function selectedKinds() { return [...document.querySelectorAll(".kinds input:checked")].map(x => x.value); }
function fmtDate(value) {
  if (!value) return "";
  const d = new Date(value);
  return Number.isNaN(d.valueOf()) ? value : new Intl.DateTimeFormat("ja-JP", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(d);
}
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }
function nodeLabel(e) { return e.node_name ? `${e.node_name} (${e.source_ip || ""})` : (e.source_ip || "—"); }
function render() {
  for (const kind of ["syslog", "snmp"]) {
    const list = $(kind + "-list"); const data = eventsCache[kind];
    $(kind + "-count").textContent = `${data.length} 件`;
    list.innerHTML = data.length ? data.map(e => `<div class="event-row" data-id="${e.id}"><span class="time">${esc(fmtDate(e.received_at))}</span><span class="source">${esc(nodeLabel(e))}</span><span class="summary" title="${esc(e.summary)}">${esc(e.parsed.display_trap_oid || e.summary || e.message)}</span></div>`).join("") : '<div class="empty">表示するイベントはありません</div>';
  }
  const mails = eventsCache.mail.slice(0, 10);
  $("mail-list").innerHTML = mails.length ? mails.map(e => `<div class="mail-row" data-id="${e.id}"><span class="time">${esc(fmtDate(e.event_at || e.received_at))}</span><span title="${esc(e.parsed.from)}">${esc(e.parsed.from || "(Fromなし)")}</span><span title="${esc(e.parsed.to)}">${esc(e.parsed.to || "(Toなし)")}</span><span class="mail-body" title="${esc(e.parsed.text || "")}"><b>${esc(e.parsed.subject || "(件名なし)")}</b>　${esc(e.parsed.text || "")}</span></div>`).join("") : '<div class="empty">受信メールはありません</div>';
}
async function refresh() {
  const kinds = selectedKinds();
  if (!kinds.length) { eventsCache = {syslog: [], snmp: [], mail: []}; render(); return; }
  lastFilter = Object.fromEntries(queryFilters(kinds));
  for (const kind of kinds) {
    const params = queryFilters([kind]);
    params.set("limit", kind === "mail" ? "10" : "200");
    const data = await (await api(`/api/events?${params}`)).json();
    eventsCache[kind] = data.events;
  }
  for (const kind of ["syslog", "snmp", "mail"]) if (!kinds.includes(kind)) eventsCache[kind] = [];
  render();
  const status = await (await api("/api/status")).json();
  $("status").textContent = `稼働中 · ${status.event_count} events · 最新 ${fmtDate(status.latest_event_at)} · Mailpit ${fmtDate(status.mailpit_last_success) || "未接続"}`;
  $("db-status").textContent = `DB ${(status.db_bytes / 1024 / 1024).toFixed(1)} MB`;
}
function detailTable(entries) { return `<div class="detail-grid">${entries.map(([k,v]) => `<b>${esc(k)}</b><span>${esc(v)}</span>`).join("")}</div>`; }
async function openDetail(id) {
  const e = await (await api(`/api/events/${id}`)).json();
  let html = `<button class="close" data-close>閉じる</button><h2>${esc(e.kind.toUpperCase())} 詳細 #${e.id}</h2>`;
  if (e.kind === "mail") {
    const p = e.parsed;
    html += detailTable([["日時", fmtDate(e.event_at || e.received_at)],["From",p.from],["To",p.to],["Cc",p.cc],["Subject",p.subject],["Mailpit ID",p.mailpit_id]]);
    html += `<h3>本文</h3><div class="detail-body">${esc(p.text || "(本文なし)")}</div>`;
  } else {
    html += detailTable([["受信日時",fmtDate(e.received_at)],["発生日時",fmtDate(e.event_at)],["送信元",nodeLabel(e)],["形式",e.parsed.format || e.parsed.version || ""]]);
    if (e.kind === "snmp" && e.parsed.varbinds) {
      html += `<h3>Trap OID: ${esc(e.parsed.display_trap_oid || e.parsed.trap_oid || "")}</h3><div class="raw">${e.parsed.varbinds.map(v => `${esc(v.display_oid || v.oid)} [${esc(v.type)}] = ${esc(v.display_value_oid ?? v.value ?? v.hex ?? "")}`).join("\n")}</div>`;
      html += `<h3>解析データ</h3>`;
    } else html += `<h3>メッセージ</h3><div class="detail-body">${esc(e.message)}</div>`;
    html += `<pre class="raw">${esc(JSON.stringify(e.parsed, null, 2))}</pre>`;
  }
  html += `<details><summary>受信元データ</summary><pre class="raw">${esc(e.raw_base64 || "(なし)")}</pre></details>`;
  $("detail-content").innerHTML = html;
  $("detail-dialog").showModal();
}
async function loadManage() {
  const data = await (await api("/api/nodes")).json();
  const filters = $("nodes");
  const current = new Set([...filters.selectedOptions].map(o => o.value));
  filters.innerHTML = `<optgroup label="ノード">${data.nodes.map(n => `<option value="n:${n.id}">${esc(n.name)} · ${esc(n.addresses || "IP未設定")}</option>`).join("")}</optgroup><optgroup label="グループ">${data.groups.map(g => `<option value="g:${g.id}">[G] ${esc(g.name)}</option>`).join("")}</optgroup><optgroup label="未登録">${data.unknown_addresses.map(ip => `<option value="u:${esc(ip)}">${esc(ip)}</option>`).join("")}</optgroup>`;
  [...filters.options].forEach(o => o.selected = current.has(o.value));
  $("node-groups").innerHTML = data.groups.map(g => `<option value="${g.id}">${esc(g.name)}</option>`).join("");
  $("group-items").innerHTML = data.groups.map(g => `<div class="item-row">${esc(g.name)} <span>${esc(g.description)}</span><button data-delete-group="${g.id}">削除</button></div>`).join("") || "グループなし";
  $("node-items").innerHTML = data.nodes.map(n => `<div class="item-row"><b>${esc(n.name)}</b><span>${esc(n.addresses || "IP未設定")}</span><button data-edit-node="${n.id}">編集</button><button data-delete-node="${n.id}">削除</button></div>`).join("") || "ノードなし";
  $("unknown-items").innerHTML = data.unknown_addresses.map(ip => `<div class="item-row">${esc(ip)}<button data-register-ip="${esc(ip)}">ノード登録</button></div>`).join("") || "未登録の送信元なし";
}
function resetNodeForm() { $("node-form").reset(); $("node-id").value = ""; }
async function saveNode(event) {
  event.preventDefault();
  const body = {name:$("node-name").value, addresses:$("node-addresses").value.split(/\s+/).filter(Boolean), description:$("node-description").value, group_ids:[...$("node-groups").selectedOptions].map(x=>Number(x.value))};
  const id = $("node-id").value;
  await api(id ? `/api/nodes/${id}` : "/api/nodes", {method:id ? "PUT":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  resetNodeForm(); await loadManage(); await refresh();
}
async function exportData() {
  const params = queryFilters(selectedKinds()); params.set("format", "json"); params.set("include_raw", "true");
  const response = await api(`/api/export?${params}`);
  const blob = await response.blob(); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href=url; a.download="log-view-evidence.json"; a.click(); URL.revokeObjectURL(url);
  alert(`Export SHA-256: ${response.headers.get("X-Content-SHA256") || ""}`);
}
async function downloadBackup() {
  const response = await api("/api/admin/backup", {method:"POST"}); const blob = await response.blob();
  const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href=url; a.download="log-view-backup.sqlite3"; a.click(); URL.revokeObjectURL(url);
  $("admin-result").textContent = `バックアップ SHA-256: ${response.headers.get("X-Content-SHA256") || ""}`;
}
async function loadMibs() {
  const data = await (await api("/api/mibs")).json();
  $("mib-items").innerHTML = data.files.length ? data.files.map(m => `<div class="item-row"><b>${esc(m.name)}</b><span>${(m.size/1024).toFixed(1)} KiB</span><button data-delete-mib="${esc(m.name)}">削除</button></div>`).join("") : '<div class="empty">MIBファイルがありません</div>';
}
async function retention(dry) {
  const info = await (await api("/api/admin/retention/default")).json();
  const result = await (await api(`/api/admin/retention?dry_run=${dry}`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({before:info.cutoff,confirm:!dry})})).json();
  $("admin-result").textContent = dry ? `${info.days}日より古いログ: ${result.deleted_or_matching}件` : `${result.deleted_or_matching}件を削除しました`;
  await refresh();
}

$("login-form").addEventListener("submit", async e => {
  e.preventDefault(); $("login-error").textContent = "";
  try { const r = await fetch(appUrl("/api/login"), {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({password:$("password").value})}); if(!r.ok) throw new Error("パスワードが違います"); const d=await r.json(); csrfToken=d.csrf_token; $("login-dialog").close(); await loadManage(); await refresh(); }
  catch(err) { $("login-error").textContent=err.message; }
});
$("search").onclick = () => refresh().catch(e=>alert(e.message));
$("clear").onclick = () => { $("from").value=""; $("to").value=""; $("q").value=""; [...$("nodes").options].forEach(o=>o.selected=false); refresh(); };
$("export").onclick = () => exportData().catch(e=>alert(e.message));
$("manage-open").onclick = async () => { await loadManage(); $("manage-dialog").showModal(); };
$("admin-open").onclick = async () => { const s=await (await api("/api/status")).json(); $("admin-info").textContent=`${s.event_count}件 · DB ${(s.db_bytes/1024/1024).toFixed(1)}MB`; $("admin-dialog").showModal(); };
$("mib-open").onclick = async () => { await loadMibs(); $("mib-dialog").showModal(); };
$("logout").onclick = async () => { await api("/api/logout",{method:"POST"}); csrfToken=""; showLogin(); };
$("node-form").onsubmit = e => saveNode(e).catch(err=>alert(err.message));
$("node-reset").onclick = resetNodeForm;
$("group-form").onsubmit = async e => { e.preventDefault(); try { await api("/api/groups",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:$("group-name").value,description:$("group-description").value})}); e.target.reset(); await loadManage(); } catch(err){alert(err.message);} };
$("mib-form").onsubmit = async e => { e.preventDefault(); const file=$("mib-file").files[0]; if(!file)return; const form=new FormData(); form.append("file",file); try { const data=await (await api("/api/mibs",{method:"POST",body:form})).json(); $("mib-result").textContent=`${data.name} を登録しました`; e.target.reset(); await loadMibs(); } catch(err) { $("mib-result").textContent=err.message; } };
$("backup").onclick = () => downloadBackup().catch(e=>alert(e.message));
$("retention-preview").onclick = () => retention(true).catch(e=>alert(e.message));
$("retention-delete").onclick = () => { if(confirm("保持期間より古いイベントを削除します。先にバックアップしましたか？")) retention(false).catch(e=>alert(e.message)); };
$("restore-file").onchange = async e => { const file=e.target.files[0]; if(!file)return; if(!confirm("現在のDBへバックアップを復元します。復元前のDBは別途バックアップしてください。続行しますか？"))return; const form=new FormData();form.append("file",file);form.append("confirm","true"); try { await api("/api/admin/restore",{method:"POST",body:form}); alert("復元しました。画面を再読み込みします。"); location.reload(); } catch(err){alert(err.message);} };
document.body.addEventListener("click", async e => {
  const close=e.target.closest("[data-close]"); if(close){close.closest("dialog").close();return;}
  const row=e.target.closest("[data-id]"); if(row){openDetail(row.dataset.id).catch(err=>alert(err.message));return;}
  const delNode=e.target.closest("[data-delete-node]"); if(delNode && confirm("ノードを削除しますか？過去イベントは残ります。")){await api(`/api/nodes/${delNode.dataset.deleteNode}`,{method:"DELETE"});await loadManage();await refresh();return;}
  const delGroup=e.target.closest("[data-delete-group]"); if(delGroup && confirm("グループを削除しますか？")){await api(`/api/groups/${delGroup.dataset.deleteGroup}`,{method:"DELETE"});await loadManage();return;}
  const delMib=e.target.closest("[data-delete-mib]"); if(delMib && confirm(`${delMib.dataset.deleteMib} を削除しますか？`)){try{await api(`/api/mibs/${encodeURIComponent(delMib.dataset.deleteMib)}`,{method:"DELETE"});$("mib-result").textContent="MIBを削除しました";await loadMibs();}catch(err){alert(err.message);}return;}
  const edit=e.target.closest("[data-edit-node]"); if(edit){const n=(await (await api("/api/nodes")).json()).nodes.find(x=>x.id==edit.dataset.editNode);$("node-id").value=n.id;$("node-name").value=n.name;$("node-addresses").value=(n.addresses||"").split(",").join("\n");$("node-description").value=n.description;const gids=(n.group_ids||"").split(",");[...$("node-groups").options].forEach(o=>o.selected=gids.includes(o.value));return;}
  const ip=e.target.closest("[data-register-ip]"); if(ip){$("node-addresses").value=ip.dataset.registerIp;$("node-name").value=ip.dataset.registerIp;$("node-id").value="";return;}
});

(async () => {
  const now = new Date(); const before = new Date(now.getTime() - 60 * 60 * 1000);
  const localInput = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  $("from").value = localInput(before); $("to").value = localInput(now);
  try { csrfToken=(await (await api("/api/session")).json()).csrf_token; $("login-dialog").close(); await loadManage(); await refresh(); }
  catch { showLogin(); }
  setInterval(() => { if (csrfToken) refresh().catch(e => { $("status").textContent=e.message; }); }, 2500);
})();

