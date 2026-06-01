"""
Dashboard Publisher — generates a static HTML dashboard from command_executions.json
and pushes it to the gh-pages branch of the configured GitHub repository.
"""

import os
import json
import base64
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Set

import requests

from src.utils.logger import get_logger

EXECUTIONS_FILE = Path("data/command_executions.json")

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kernel BZ Polling — Execution Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
     background:#0d1117;color:#c9d1d9;padding:20px;line-height:1.5}
h1{font-size:1.6rem;margin-bottom:4px;color:#58a6ff}
.subtitle{color:#8b949e;font-size:.85rem;margin-bottom:20px}
.cards{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:24px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px 20px;min-width:130px;text-align:center}
.card .value{font-size:1.8rem;font-weight:700}
.card .label{font-size:.75rem;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-top:2px}
.card.success .value{color:#3fb950}
.card.failed .value{color:#f85149}
.card.timeout .value{color:#d29922}
.card.total .value{color:#58a6ff}
.card.running .value{color:#a371f7}
.filters{margin-bottom:14px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.filters select,.filters input{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;
  border-radius:6px;padding:6px 10px;font-size:.85rem}
.filters label{font-size:.8rem;color:#8b949e}
table{width:100%;border-collapse:collapse;font-size:.85rem}
thead th{position:sticky;top:0;background:#161b22;color:#8b949e;text-align:left;
  padding:10px 12px;border-bottom:2px solid #30363d;cursor:pointer;user-select:none;white-space:nowrap}
thead th:hover{color:#58a6ff}
thead th .arrow{margin-left:4px;font-size:.7rem}
tbody tr{border-bottom:1px solid #21262d}
tbody tr:hover{background:#161b22}
td{padding:8px 12px;vertical-align:top}
.status{display:inline-block;padding:2px 10px;border-radius:12px;font-size:.78rem;font-weight:600}
.status.success{background:#23312a;color:#3fb950}
.status.failed{background:#3d1f20;color:#f85149}
.status.timeout{background:#3d2e00;color:#d29922}
.status.error{background:#3d1f20;color:#f85149}
.status.running{background:#2a1f3d;color:#a371f7}
.status.skipped{background:#21262d;color:#8b949e}
.status.excluded{background:#2d2121;color:#f0883e}
.vtype{display:inline-block;padding:2px 10px;border-radius:12px;font-size:.78rem;font-weight:600}
.vtype.pre{background:#1f3d2a;color:#56d364}
.vtype.post{background:#1f2a3d;color:#79c0ff}
.vtype.na{background:#21262d;color:#8b949e}
.duration{font-variant-numeric:tabular-nums}
.reason{max-width:300px;font-size:.78rem;color:#f0883e}
.command{max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:monospace;font-size:.78rem;color:#8b949e}
.command:hover{white-space:normal;word-break:break-all}
@media(max-width:800px){.cards{flex-direction:column} table{font-size:.78rem}}
</style>
</head>
<body>
<h1>Kernel BZ Polling — Execution Dashboard</h1>
<p class="subtitle">Last updated: <span id="ts">__TIMESTAMP__</span></p>

<div class="cards" id="cards"></div>

<div class="filters">
  <div><label>Status:</label>
    <select id="fStatus"><option value="">All</option></select></div>
  <div><label>Platform:</label>
    <select id="fPlatform"><option value="">All</option></select></div>
  <div><label>Search:</label>
    <input id="fSearch" placeholder="JIRA ID or kernel…" /></div>
</div>

<table>
<thead><tr id="hdr"></tr></thead>
<tbody id="tbody"></tbody>
</table>

<script>
const EMBEDDED_DATA = __DATA__;
let DATA = EMBEDDED_DATA;

async function init(){
  try {
    const resp = await fetch('executions.json');
    if(resp.ok) DATA = await resp.json();
  } catch(e){ /* use embedded fallback */ }
  rows.length = 0;
  rows.push(...Object.values(DATA));
  renderCards();
  populateFilters();
  render();
}

const COLS = [
  {key:'jira_id',     label:'JIRA ID'},
  {key:'platform',    label:'Platform'},
  {key:'verification_type', label:'Verification Type'},
  {key:'jira_kernel', label:'JIRA Kernel'},
  {key:'fix_version', label:'JIRA Fix Version'},
  {key:'status',      label:'Status'},
  {key:'reason',      label:'Reason'},
  {key:'duration_seconds', label:'Duration'},
  {key:'start_time',  label:'Started'},
  {key:'log_dir',     label:'Logs'},
  {key:'command',     label:'Command'},
];

function logUrl(path){
  if(!path) return null;
  return path.replace(/^\/ceph\//, 'http://magna002.ceph.redhat.com/');
}

let sortCol = 'start_time', sortAsc = false;
const rows = Object.values(DATA);

/* summary cards */
function renderCards(){
  const total = rows.length;
  const counts = {success:0, failed:0, timeout:0, error:0, running:0, skipped:0, excluded:0};
  rows.forEach(r => { const s = r.status||'unknown'; if(s in counts) counts[s]++; else if(s==='unknown') counts.error++; });
  const c = document.getElementById('cards');
  c.innerHTML = `
    <div class="card total"><div class="value">${total}</div><div class="label">Total</div></div>
    <div class="card success"><div class="value">${counts.success}</div><div class="label">Passed</div></div>
    <div class="card failed"><div class="value">${counts.failed+counts.error}</div><div class="label">Failed</div></div>
    <div class="card timeout"><div class="value">${counts.timeout}</div><div class="label">Timeout</div></div>
    <div class="card running"><div class="value">${counts.running}</div><div class="label">Running</div></div>
    <div class="card running"><div class="value">${counts.skipped}</div><div class="label">Skipped</div></div>
    <div class="card timeout"><div class="value">${counts.excluded}</div><div class="label">Excluded</div></div>`;
}

/* filters */
function populateFilters(){
  const statuses = [...new Set(rows.map(r=>r.status||'unknown'))].sort();
  const platforms = [...new Set(rows.map(r=>r.platform||'N/A'))].sort();
  const fS = document.getElementById('fStatus');
  const fP = document.getElementById('fPlatform');
  statuses.forEach(s=>{const o=document.createElement('option');o.value=s;o.textContent=s;fS.appendChild(o);});
  platforms.forEach(p=>{const o=document.createElement('option');o.value=p;o.textContent=p;fP.appendChild(o);});
}

function getFiltered(){
  const s = document.getElementById('fStatus').value;
  const p = document.getElementById('fPlatform').value;
  const q = document.getElementById('fSearch').value.toLowerCase();
  return rows.filter(r=>{
    if(s && (r.status||'unknown')!==s) return false;
    if(p && (r.platform||'N/A')!==p) return false;
    if(q){
      const hay = JSON.stringify(r).toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
}

/* sorting */
function cmp(a,b){
  let va=a[sortCol], vb=b[sortCol];
  if(va==null) va='';
  if(vb==null) vb='';
  if(typeof va==='number'&&typeof vb==='number') return sortAsc?va-vb:vb-va;
  va=String(va); vb=String(vb);
  return sortAsc?va.localeCompare(vb):vb.localeCompare(va);
}

/* render */
function renderHeader(){
  const tr = document.getElementById('hdr');
  tr.innerHTML = '';
  COLS.forEach(c=>{
    const th = document.createElement('th');
    let arrow = '';
    if(c.key===sortCol) arrow = `<span class="arrow">${sortAsc?'▲':'▼'}</span>`;
    th.innerHTML = c.label + arrow;
    th.onclick = ()=>{ if(sortCol===c.key) sortAsc=!sortAsc; else {sortCol=c.key;sortAsc=true;} render(); };
    tr.appendChild(th);
  });
}

function fmtDuration(s){
  if(s==null) return '-';
  if(s<60) return s+'s';
  const m=Math.floor(s/60); const sec=s%60;
  if(m<60) return m+'m '+sec+'s';
  const h=Math.floor(m/60); return h+'h '+(m%60)+'m';
}

function fmtTime(t){
  if(!t) return '-';
  try{ const d=new Date(t); return d.toLocaleString(); }catch(e){ return t; }
}

function renderTable(){
  const data = getFiltered().sort(cmp);
  const tb = document.getElementById('tbody');
  tb.innerHTML = '';
  data.forEach(r=>{
    const tr = document.createElement('tr');
    COLS.forEach(c=>{
      const td = document.createElement('td');
      const v = r[c.key];
      if(c.key==='status'){
        td.innerHTML = `<span class="status ${v||'unknown'}">${v||'unknown'}</span>`;
      } else if(c.key==='verification_type'){
        const cls = (v||'N/A').toLowerCase().replace('/','-');
        const label = v||'N/A';
        td.innerHTML = `<span class="vtype ${cls==='n/a'?'na':cls}">${label}</span>`;
      } else if(c.key==='reason'){
        td.className='reason';
        td.textContent = v||'-';
        td.title = v||'';
      } else if(c.key==='duration_seconds'){
        td.className='duration';
        td.textContent = fmtDuration(v);
      } else if(c.key==='start_time'){
        td.textContent = fmtTime(v);
      } else if(c.key==='log_dir'){
        const url = logUrl(v);
        if(url){ td.innerHTML = `<a href="${url}" target="_blank" style="color:#58a6ff">View Logs</a>`; }
        else { td.textContent = '-'; }
      } else if(c.key==='command'){
        td.className='command';
        td.textContent = v||'';
        td.title = v||'';
      } else {
        td.textContent = v!=null?v:'-';
      }
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
}

function render(){ renderHeader(); renderTable(); }

document.getElementById('fStatus').onchange = render;
document.getElementById('fPlatform').onchange = render;
document.getElementById('fSearch').oninput = render;
init();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def _github_config():
    """Return (token, repo, branch) from env or empty strings."""
    return (
        os.environ.get("GITHUB_TOKEN", ""),
        os.environ.get("GITHUB_REPO", ""),
        os.environ.get("GITHUB_PAGES_BRANCH", "main"),
    )


def _github_headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def _push_file_to_github(
    filepath: str, content: bytes, token: str, repo: str, branch: str
) -> bool:
    """Create or update a single file in the dashboard repo via Contents API."""
    api_url = f"https://api.github.com/repos/{repo}/contents/{filepath}"
    headers = _github_headers(token)

    sha: Optional[str] = None
    try:
        resp = requests.get(api_url, headers=headers, params={"ref": branch}, timeout=15)
        if resp.status_code == 200:
            sha = resp.json().get("sha")
    except Exception:
        pass

    payload = {
        "message": f"Update {filepath} — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "content": base64.b64encode(content).decode(),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    try:
        resp = requests.put(api_url, headers=headers, json=payload, timeout=30)
        if resp.status_code in (200, 201):
            logger.info(f"Pushed {filepath} to {repo}")
            return True
        logger.warning(f"Push {filepath} failed (HTTP {resp.status_code}): {resp.text[:300]}")
        return False
    except Exception as e:
        logger.warning(f"Push {filepath} error: {e}")
        return False


def _fetch_remote_executions(token: str, repo: str, branch: str) -> Dict:
    """Download executions.json from the dashboard repo. Returns empty dict on failure."""
    api_url = f"https://api.github.com/repos/{repo}/contents/executions.json"
    headers = _github_headers(token)
    try:
        resp = requests.get(api_url, headers=headers, params={"ref": branch}, timeout=15)
        if resp.status_code == 200:
            raw = base64.b64decode(resp.json()["content"])
            return json.loads(raw)
    except Exception as e:
        logger.debug(f"Could not fetch remote executions.json: {e}")
    return {}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def generate_dashboard_html(executions: dict) -> str:
    """Render the dashboard HTML with execution data embedded."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    data_json = json.dumps(executions, default=str)
    html = _HTML_TEMPLATE.replace("__TIMESTAMP__", timestamp)
    html = html.replace("__DATA__", data_json)
    return html


def _make_history_key(record: dict) -> str:
    """Build the unique key for the history JSON: jira_id:verification_type."""
    jira_id = record.get("jira_id", "UNKNOWN")
    vtype = record.get("verification_type", "N/A")
    return f"{jira_id}:{vtype}"


def publish_dashboard() -> bool:
    """Load local executions, merge with remote history, push both JSON and HTML."""
    token, repo, branch = _github_config()
    if not token or not repo:
        logger.warning("GITHUB_TOKEN or GITHUB_REPO not set, skipping dashboard publish")
        return False

    if not EXECUTIONS_FILE.exists():
        logger.warning(f"{EXECUTIONS_FILE} not found, skipping dashboard publish")
        return False

    try:
        with open(EXECUTIONS_FILE) as f:
            local_data = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read {EXECUTIONS_FILE}: {e}")
        return False

    if local_data:
        # Fetch existing remote history and merge
        remote_history = _fetch_remote_executions(token, repo, branch)
        for _key, record in local_data.items():
            hkey = _make_history_key(record)
            existing = remote_history.get(hkey, {})
            existing.update({k: v for k, v in record.items() if v is not None})
            remote_history[hkey] = existing
        publish_data = remote_history
    else:
        logger.info("Local execution data is empty — publishing clean dashboard")
        publish_data = {}

    # Push executions.json
    json_bytes = json.dumps(publish_data, indent=2, default=str).encode()
    ok_json = _push_file_to_github("executions.json", json_bytes, token, repo, branch)

    # Push index.html
    html = generate_dashboard_html(publish_data)
    ok_html = _push_file_to_github("index.html", html.encode(), token, repo, branch)

    return ok_json and ok_html


def fetch_passed_jiras() -> Set[str]:
    """Fetch executions.json from the dashboard repo and return the set of
    'jira_id:verification_type' keys where status == 'success'.

    Returns an empty set if the file cannot be fetched.
    """
    token, repo, branch = _github_config()
    if not token or not repo:
        return set()

    remote = _fetch_remote_executions(token, repo, branch)
    passed = set()
    for key, record in remote.items():
        if record.get("status") == "success":
            passed.add(key)
    return passed
