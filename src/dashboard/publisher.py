"""
Dashboard Publisher — generates a static HTML dashboard from command_executions.json
and pushes it to the gh-pages branch of the configured GitHub repository.
"""

import os
import json
import base64
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

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
.header-row{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;margin-bottom:20px}
.header-left{flex:1}
.subtitle{color:#8b949e;font-size:.85rem}
.effort-banner{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 20px;
  font-size:.85rem;color:#8b949e;white-space:nowrap}
.effort-banner .effort-value{color:#3fb950;font-weight:700}
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
<div class="header-row">
  <div class="header-left">
    <h1>Kernel BZ Polling — Execution Dashboard</h1>
    <p class="subtitle">Last updated: <span id="ts">__TIMESTAMP__</span></p>
  </div>
  <div class="effort-banner" id="effortBanner"></div>
</div>

<div class="cards" id="cards"></div>

<div class="filters">
  <div><label>Status:</label>
    <select id="fStatus"><option value="">All</option></select></div>
  <div><label>Platform:</label>
    <select id="fPlatform"><option value="">All</option></select></div>
  <div><label>Verification:</label>
    <select id="fVtype"><option value="">All</option></select></div>
  <div><label>Search:</label>
    <input id="fSearch" placeholder="JIRA ID or kernel…" /></div>
</div>

<table>
<thead><tr id="hdr"></tr></thead>
<tbody id="tbody"></tbody>
</table>

<script>
const EMBEDDED_DATA = __DATA__;
const JIRA_BASE = '__JIRA_SERVER__';
const LOG_PUBLIC_BASE = '__LOG_PUBLIC_BASE_URL__';
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
  if(/^https?:\/\//i.test(path)) return path;
  const base = (LOG_PUBLIC_BASE || '').replace(/\/+$/, '');
  const suffix = String(path).replace(/^\/+/, '');
  return base ? base + '/' + suffix : suffix;
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

  const sprintPoints = counts.success + counts.failed + counts.timeout + counts.error;
  const days = (sprintPoints / 2).toFixed(1);
  const eb = document.getElementById('effortBanner');
  eb.innerHTML = `Manual Effort Saved: <span class="effort-value">${days} days</span> (${sprintPoints} Sprint Points)`;
}

/* filters */
function populateFilters(){
  const statuses = [...new Set(rows.map(r=>r.status||'unknown'))].sort();
  const platforms = [...new Set(rows.map(r=>r.platform||'N/A'))].sort();
  const vtypes = [...new Set(rows.map(r=>r.verification_type||'N/A'))].sort();
  const fS = document.getElementById('fStatus');
  const fP = document.getElementById('fPlatform');
  const fV = document.getElementById('fVtype');
  statuses.forEach(s=>{const o=document.createElement('option');o.value=s;o.textContent=s;fS.appendChild(o);});
  platforms.forEach(p=>{const o=document.createElement('option');o.value=p;o.textContent=p;fP.appendChild(o);});
  vtypes.forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;fV.appendChild(o);});
}

function getFiltered(){
  const s = document.getElementById('fStatus').value;
  const p = document.getElementById('fPlatform').value;
  const v = document.getElementById('fVtype').value;
  const q = document.getElementById('fSearch').value.toLowerCase();
  return rows.filter(r=>{
    if(s && (r.status||'unknown')!==s) return false;
    if(p && (r.platform||'N/A')!==p) return false;
    if(v && (r.verification_type||'N/A')!==v) return false;
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
      if(c.key==='jira_id'){
        if(v && JIRA_BASE){ td.innerHTML = `<a href="${JIRA_BASE}/browse/${v}" target="_blank" style="color:#58a6ff;text-decoration:none;font-weight:600">${v}</a>`; }
        else { td.textContent = v||'-'; }
      } else if(c.key==='status'){
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
document.getElementById('fVtype').onchange = render;
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


def _get_file_sha(
    filepath: str, token: str, repo: str, branch: str
) -> Optional[str]:
    """Return the current Contents-API blob sha for filepath, or None if missing."""
    api_url = f"https://api.github.com/repos/{repo}/contents/{filepath}"
    headers = _github_headers(token)
    try:
        resp = requests.get(
            api_url, headers=headers, params={"ref": branch}, timeout=15
        )
        if resp.status_code == 200:
            return resp.json().get("sha")
        if resp.status_code != 404:
            logger.warning(
                f"GET {filepath} sha failed (HTTP {resp.status_code}): "
                f"{resp.text[:200]}"
            )
    except Exception as e:
        logger.warning(f"GET {filepath} sha error: {e}")
    return None


def _push_file_to_github(
    filepath: str,
    content: bytes,
    token: str,
    repo: str,
    branch: str,
    retries: int = 3,
) -> bool:
    """Create or update a single file via Contents API, retrying on sha conflicts."""
    api_url = f"https://api.github.com/repos/{repo}/contents/{filepath}"
    headers = _github_headers(token)

    for attempt in range(1, retries + 1):
        sha = _get_file_sha(filepath, token, repo, branch)
        payload = {
            "message": (
                f"Update {filepath} — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            ),
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
            if resp.status_code in (409, 422) and attempt < retries:
                logger.warning(
                    f"Push {filepath} conflict (HTTP {resp.status_code}), "
                    f"retry {attempt}/{retries} with fresh sha..."
                )
                time.sleep(1.0 * attempt)
                continue
            logger.warning(
                f"Push {filepath} failed (HTTP {resp.status_code}): "
                f"{resp.text[:300]}"
            )
            return False
        except Exception as e:
            if attempt < retries:
                logger.warning(
                    f"Push {filepath} error ({e}), retry {attempt}/{retries}..."
                )
                time.sleep(1.0 * attempt)
                continue
            logger.warning(f"Push {filepath} error: {e}")
            return False
    return False


def _push_files_commit(
    files: List[Tuple[str, bytes]],
    token: str,
    repo: str,
    branch: str,
    message: str,
    retries: int = 3,
) -> bool:
    """Update multiple files in one Git commit (Git Data API), with retries.

    Used so executions.json and index.html land together after a test result.
    """
    headers = _github_headers(token)
    base = f"https://api.github.com/repos/{repo}"

    for attempt in range(1, retries + 1):
        try:
            ref_resp = requests.get(
                f"{base}/git/ref/heads/{branch}", headers=headers, timeout=15
            )
            if ref_resp.status_code != 200:
                logger.warning(
                    f"GET ref heads/{branch} failed (HTTP {ref_resp.status_code}): "
                    f"{ref_resp.text[:200]}"
                )
                return False
            parent_sha = ref_resp.json()["object"]["sha"]

            commit_resp = requests.get(
                f"{base}/git/commits/{parent_sha}", headers=headers, timeout=15
            )
            if commit_resp.status_code != 200:
                logger.warning(
                    f"GET commit {parent_sha[:7]} failed "
                    f"(HTTP {commit_resp.status_code}): {commit_resp.text[:200]}"
                )
                return False
            base_tree_sha = commit_resp.json()["tree"]["sha"]

            tree_items = []
            for filepath, content in files:
                blob_resp = requests.post(
                    f"{base}/git/blobs",
                    headers=headers,
                    json={
                        "content": base64.b64encode(content).decode(),
                        "encoding": "base64",
                    },
                    timeout=30,
                )
                if blob_resp.status_code not in (200, 201):
                    logger.warning(
                        f"Create blob for {filepath} failed "
                        f"(HTTP {blob_resp.status_code}): {blob_resp.text[:200]}"
                    )
                    return False
                tree_items.append(
                    {
                        "path": filepath,
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob_resp.json()["sha"],
                    }
                )

            tree_resp = requests.post(
                f"{base}/git/trees",
                headers=headers,
                json={"base_tree": base_tree_sha, "tree": tree_items},
                timeout=30,
            )
            if tree_resp.status_code not in (200, 201):
                logger.warning(
                    f"Create tree failed (HTTP {tree_resp.status_code}): "
                    f"{tree_resp.text[:200]}"
                )
                return False
            new_tree_sha = tree_resp.json()["sha"]

            commit_create = requests.post(
                f"{base}/git/commits",
                headers=headers,
                json={
                    "message": message,
                    "tree": new_tree_sha,
                    "parents": [parent_sha],
                },
                timeout=30,
            )
            if commit_create.status_code not in (200, 201):
                logger.warning(
                    f"Create commit failed (HTTP {commit_create.status_code}): "
                    f"{commit_create.text[:200]}"
                )
                return False
            new_commit_sha = commit_create.json()["sha"]

            ref_update = requests.patch(
                f"{base}/git/refs/heads/{branch}",
                headers=headers,
                json={"sha": new_commit_sha, "force": False},
                timeout=15,
            )
            if ref_update.status_code in (200, 201):
                names = ", ".join(path for path, _ in files)
                logger.info(
                    f"Pushed {names} to {repo}@{branch} "
                    f"in commit {new_commit_sha[:7]}"
                )
                return True

            if ref_update.status_code in (409, 422) and attempt < retries:
                logger.warning(
                    f"Ref update conflict (HTTP {ref_update.status_code}), "
                    f"retry {attempt}/{retries}..."
                )
                time.sleep(1.0 * attempt)
                continue

            logger.warning(
                f"Ref update failed (HTTP {ref_update.status_code}): "
                f"{ref_update.text[:300]}"
            )
            return False
        except Exception as e:
            if attempt < retries:
                logger.warning(
                    f"Combined dashboard push error ({e}), "
                    f"retry {attempt}/{retries}..."
                )
                time.sleep(1.0 * attempt)
                continue
            logger.warning(f"Combined dashboard push error: {e}")
            return False
    return False


def _decode_executions_payload(raw: bytes) -> Dict:
    """Parse executions.json bytes into a dict. Raises on invalid content."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"executions.json must be a JSON object, got {type(data).__name__}")
    return data


def _fetch_remote_executions(
    token: str, repo: str, branch: str, retries: int = 3
) -> Optional[Dict]:
    """Download executions.json from the dashboard repo.

    Returns:
        dict on success (including ``{}`` when the file is missing / 404)
        None after retries are exhausted on transient or unexpected failures

    Never treats a failed fetch as empty history — callers must abort publish
    when this returns None to avoid wiping the dashboard table.
    """
    api_url = f"https://api.github.com/repos/{repo}/contents/executions.json"
    headers = _github_headers(token)

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(
                api_url, headers=headers, params={"ref": branch}, timeout=15
            )
            if resp.status_code == 404:
                return {}
            if resp.status_code == 200:
                payload = resp.json()
                content = payload.get("content")
                if content:
                    raw = base64.b64decode(content)
                    return _decode_executions_payload(raw)

                download_url = payload.get("download_url")
                if download_url:
                    dl = requests.get(download_url, headers=headers, timeout=30)
                    if dl.status_code == 200:
                        return _decode_executions_payload(dl.content)
                    err = (
                        f"download_url fetch failed "
                        f"(HTTP {dl.status_code}): {dl.text[:200]}"
                    )
                else:
                    err = "Contents API 200 but no content or download_url"
            elif resp.status_code in (403, 429) or resp.status_code >= 500:
                err = (
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
            else:
                # Non-retryable client error (e.g. 401) — fail immediately
                logger.warning(
                    f"Could not fetch remote executions.json "
                    f"(HTTP {resp.status_code}): {resp.text[:200]}"
                )
                return None
        except Exception as e:
            err = str(e)

        if attempt < retries:
            logger.warning(
                f"Fetch remote executions.json failed ({err}), "
                f"retry {attempt}/{retries}..."
            )
            time.sleep(1.0 * attempt)
            continue

        logger.warning(
            f"Could not fetch remote executions.json after {retries} attempts: {err}"
        )
        return None

    return None


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def generate_dashboard_html(executions: dict) -> str:
    """Render the dashboard HTML with execution data embedded."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    jira_server = os.environ.get("JIRA_SERVER", "").rstrip("/")
    log_public_base = os.environ.get(
        "LOG_PUBLIC_BASE_URL", "http://9.11.121.102/logs"
    ).rstrip("/")
    data_json = json.dumps(executions, default=str)
    html = _HTML_TEMPLATE.replace("__TIMESTAMP__", timestamp)
    html = html.replace("__JIRA_SERVER__", jira_server)
    html = html.replace("__LOG_PUBLIC_BASE_URL__", log_public_base)
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

    # Always load remote history first so we never publish a wipe on fetch failure
    remote_history = _fetch_remote_executions(token, repo, branch)
    if remote_history is None:
        logger.warning(
            "Skipping dashboard publish: could not load remote executions.json "
            "(refusing to risk overwriting history)"
        )
        return False

    if not local_data:
        if remote_history:
            logger.warning(
                "Skipping dashboard publish: local execution data is empty but "
                f"remote history has {len(remote_history)} entries "
                "(refusing to wipe dashboard)"
            )
            return False
        logger.info("Local and remote execution data are empty — nothing to publish")
        return True

    remote_count_before = len(remote_history)

    for _key, record in local_data.items():
        jira_id = record.get("jira_id", "UNKNOWN")
        vtype = record.get("verification_type")
        is_excluded = record.get("status") == "excluded" or (
            "reason" in record and not vtype
        )

        if is_excluded and (not vtype or vtype == "N/A"):
            # Excluded entry with no verification type: update ALL existing
            # remote entries for this JIRA ID instead of creating a new :N/A row
            matched = False
            for rkey, rval in remote_history.items():
                if rkey.startswith(f"{jira_id}:"):
                    rval.update({k: v for k, v in record.items() if v is not None})
                    matched = True
            if not matched:
                hkey = _make_history_key(record)
                remote_history[hkey] = dict(record)
        else:
            hkey = _make_history_key(record)
            existing = remote_history.get(hkey, {})
            for k, v in record.items():
                if v is not None:
                    existing[k] = v
                elif k == "reason":
                    existing.pop(k, None)
            remote_history[hkey] = existing

    # Remove any stale :N/A entries if a typed entry exists for the same JIRA
    stale_keys = []
    for rkey in remote_history:
        if rkey.endswith(":N/A"):
            jid = rkey.rsplit(":", 1)[0]
            for other in remote_history:
                if other.startswith(f"{jid}:") and not other.endswith(":N/A"):
                    stale_keys.append(rkey)
                    break
    for sk in stale_keys:
        del remote_history[sk]
        logger.info(f"Removed stale dashboard entry: {sk}")

    publish_data = remote_history

    # Safety net: refuse to publish if merge somehow dropped most of history
    if remote_count_before > 0:
        min_allowed = max(1, int(0.5 * remote_count_before))
        if len(publish_data) < min_allowed:
            logger.warning(
                "Skipping dashboard publish: merged history shrank from "
                f"{remote_count_before} to {len(publish_data)} entries "
                f"(below {min_allowed} threshold); refusing to wipe dashboard"
            )
            return False

    # Push executions.json + index.html together in one commit
    json_bytes = json.dumps(publish_data, indent=2, default=str).encode()
    html_bytes = generate_dashboard_html(publish_data).encode()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    ok = _push_files_commit(
        [
            ("executions.json", json_bytes),
            ("index.html", html_bytes),
        ],
        token,
        repo,
        branch,
        message=f"Update dashboard — {stamp}",
    )
    if ok:
        return True

    # Fallback: Contents API per file (also retries on missing/stale sha)
    logger.warning("Combined dashboard commit failed; falling back to per-file push")
    ok_json = _push_file_to_github(
        "executions.json", json_bytes, token, repo, branch
    )
    ok_html = _push_file_to_github("index.html", html_bytes, token, repo, branch)
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
    if remote is None:
        logger.warning(
            "Could not load remote executions.json for passed-JIRA lookup; "
            "treating as empty"
        )
        return set()

    passed = set()
    for key, record in remote.items():
        if record.get("status") == "success":
            passed.add(key)
    return passed
