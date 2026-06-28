"""The Phase 8 web demo: a single self-contained page served at GET / (D49).

The recruiter demo is ONE static HTML document — inline CSS and vanilla JS, no build step
and no external assets — held here as a module constant and served by ``main.py`` at
``GET /``. The page POSTs to the existing ``POST /v1/analyze`` on the SAME origin (so there
is no CORS to configure) with ``include_fix_suggestion`` turned on, then renders the detected
properties, each bug's failing input and severity, and the collapsible verified fix.

Keeping the markup as a Python string (rather than a static file) means it ships inside the
wheel and the existing Docker image with zero static-asset packaging or path concerns, and
the route returns it directly. ``INDEX_HTML`` is a RAW triple-quoted literal so the page's JS
escape sequences survive verbatim; the example function's triple-quoted docstring is built up
in JS from single double-quote characters, so three double-quotes never appear in this file
(which would otherwise close the literal).
"""

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TypeWright — property-based bug finder</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><text y='14' font-size='14'>🔍</text></svg>">
<style>
:root {
    --bg: #0f1117; --panel: #171a21; --line: #272b35; --fg: #e6e8ee;
    --muted: #9aa3b2; --accent: #6ea8fe; --good: #4ade80; --bad: #f87171; --warn: #fbbf24;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
    font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
header { padding: 30px 20px 6px; text-align: center; }
header h1 { margin: 0; font-size: 26px; letter-spacing: -0.02em; }
header p { margin: 8px auto 0; color: var(--muted); max-width: 560px; }
main { max-width: 820px; margin: 0 auto; padding: 16px 20px 60px; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 16px; }
label { display: block; font-size: 13px; color: var(--muted); margin: 0 0 6px; }
textarea { width: 100%; min-height: 220px; resize: vertical; background: #0b0d12; color: var(--fg);
    border: 1px solid var(--line); border-radius: 8px; padding: 12px;
    font-family: var(--mono); font-size: 13.5px; line-height: 1.5; }
.row { display: flex; gap: 12px; flex-wrap: wrap; align-items: end; margin-top: 12px; }
.row > div { flex: 1 1 160px; }
input[type=text], select { width: 100%; background: #0b0d12; color: var(--fg);
    border: 1px solid var(--line); border-radius: 8px; padding: 9px 10px; font-size: 14px; }
button { background: var(--accent); color: #0b0d12; border: 0; border-radius: 8px;
    font-weight: 600; padding: 11px 22px; font-size: 14px; cursor: pointer; white-space: nowrap; }
button:disabled { opacity: 0.6; cursor: progress; }
#error { color: var(--bad); margin-top: 14px; white-space: pre-wrap; }
#error:empty { display: none; }
#results { margin-top: 20px; display: grid; gap: 14px; }
.summary { font-size: 17px; font-weight: 600; padding: 12px 14px; border-radius: 10px;
    border: 1px solid var(--line); }
.summary.good { color: var(--good); }
.summary.bad { color: var(--bad); }
h3 { margin: 2px 0 0; font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
code { font-family: var(--mono); font-size: 0.92em; background: #0b0d12; padding: 1px 5px; border-radius: 5px; }
ul.props { list-style: none; padding: 0; margin: 8px 0 0; display: grid; gap: 6px; }
ul.props li { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.tag { background: #1e2a44; color: var(--accent); border-radius: 999px; padding: 2px 9px; font-size: 12px; }
.conf { color: var(--muted); font-size: 12px; margin-left: auto; }
.bug { border: 1px solid var(--line); border-left: 3px solid var(--bad); border-radius: 8px;
    padding: 12px 14px; background: #181210; }
.bug-head { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
.sev { font-weight: 600; }
.sev-crash { color: var(--bad); }
.sev-property_violation { color: var(--warn); }
.bug-body div { margin: 3px 0; }
.muted { color: var(--muted); }
details.fix { border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; background: #0d1410; }
details.fix summary { cursor: pointer; font-weight: 600; }
.verified { color: var(--good); margin-left: 6px; font-weight: 600; }
.unverified { color: var(--warn); margin-left: 6px; font-weight: 600; }
details.fix pre { background: #0b0d12; border: 1px solid var(--line); border-radius: 8px;
    padding: 12px; overflow: auto; }
details.fix pre code { background: none; padding: 0; }
.disclaimer { color: var(--warn); font-size: 12.5px; }
#share { margin-top: 16px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
#share:empty { display: none; }
.share-label { color: var(--muted); font-size: 13px; }
#share code { font-size: 12.5px; word-break: break-all; }
#share button { padding: 5px 12px; font-size: 12px; }
footer { text-align: center; color: var(--muted); font-size: 12px; padding: 0 20px 36px; max-width: 640px; margin: 0 auto; }
</style>
</head>
<body>
<header>
    <h1>TypeWright</h1>
    <p>Paste a Python function. TypeWright infers the properties it should satisfy, generates
        Hypothesis tests, runs them in a sandbox, and shows the bugs — with a verified fix.</p>
</header>
<main>
    <div class="panel">
    <label for="code">Python function</label>
    <textarea id="code" spellcheck="false"></textarea>
    <div class="row">
        <div>
        <label for="fname">Function name <span class="muted">(optional)</span></label>
        <input id="fname" type="text" placeholder="auto-detected">
        </div>
        <div>
        <label for="tier">Model tier</label>
        <select id="tier">
            <option value="">default</option>
            <option value="economy">economy</option>
            <option value="standard">standard</option>
            <option value="premium">premium</option>
        </select>
        </div>
        <div style="flex: 0 0 auto;">
        <label>&nbsp;</label>
        <button id="analyze">Analyze</button>
        </div>
    </div>
    <div id="error"></div>
    </div>
    <div id="share"></div>
    <div id="results"></div>
</main>
<footer>TypeWright finds silent wrong-answer bugs by checking implementation-independent
    properties — not by re-running your own code as its own oracle.</footer>

<script>
const $ = (id) => document.getElementById(id);

const Q3 = '"' + '"' + '"';
const EXAMPLE = [
'def absolute(x: int) -> int:',
'    ' + Q3 + 'Return the absolute value of x (always >= 0).' + Q3,
'    return x  # bug: negatives are returned unchanged',
].join('\n');

function esc(s) {
return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
}

function setBusy(busy) {
const b = $('analyze');
b.disabled = busy;
b.textContent = busy ? 'Analyzing…' : 'Analyze';
}

function showError(status, data) {
const d = data && data.detail;
const detail = typeof d === 'string' ? d : JSON.stringify(d == null ? data : d);
let msg;
if (status === 400) msg = 'Could not analyze this function: ' + detail;
else if (status === 422) msg = 'Invalid request: ' + detail;
else if (status === 429) msg = 'Rate limited — please try again in a moment.';
else if (status === 500) msg = 'Pipeline error' + (data && data.stage ? ' at stage "' + data.stage + '"' : '') +
': ' + detail;
else if (status === 504) msg = 'The test run exceeded its time budget: ' + detail;
else msg = 'Error ' + status + ': ' + detail;
$('error').textContent = msg;
}

function render(data) {
const bugs = data.bugs_found || [];
const fn = (data.function && data.function.name) || '(unknown)';
const out = [];

out.push(
    '<div class="summary ' + (bugs.length ? 'bad' : 'good') + '">' +
    (bugs.length
    ? '🔍 Found ' + bugs.length + ' issue' + (bugs.length > 1 ? 's' : '') + ' in <code>' + esc(fn) + '</code>'
    : '✅ No property violations found in <code>' + esc(fn) + '</code>') +
    '</div>'
);

const props = (data.properties && data.properties.detected) || [];
if (props.length) {
    out.push('<div><h3>Properties checked</h3><ul class="props">');
    for (const p of props) {
    out.push(
        '<li><span class="tag">' + esc(p.property_class) + '</span>' +
        '<code>' + esc(p.relation) + '</code>' +
        '<span class="conf">' + Math.round((p.confidence || 0) * 100) + '% conf.</span></li>'
    );
    }
    out.push('</ul></div>');
}

for (const b of bugs) {
    const sev = b.severity === 'crash' ? '💥 crash' : '⚠️ wrong result';
    out.push(
    '<div class="bug">' +
        '<div class="bug-head"><span class="sev sev-' + esc(b.severity) + '">' + sev + '</span>' +
        '<code>' + esc(b.violated_property) + '</code></div>' +
        '<div class="bug-body">' +
        '<div><b>Failing input:</b> <code>' + esc(b.failing_input) + '</code></div>' +
        '<div><b>Error:</b> <code>' + esc(b.error) + '</code> <span class="muted">(' + esc(b.test_name) +
')</span></div>' +
        '</div>' +
    '</div>'
    );
}

const fix = data.fix_suggestion;
if (fix) {
    const badge = fix.verified
    ? '<span class="verified">✓ verified — passes the same tests</span>'
    : '<span class="unverified">unverified — no confident fix</span>';
    out.push(
    '<details class="fix"' + (fix.verified ? ' open' : '') + '>' +
        '<summary>Suggested fix ' + badge + '</summary>' +
        '<p>' + esc(fix.explanation) + '</p>' +
        '<pre><code>' + esc(fix.code) + '</code></pre>' +
        '<p class="disclaimer">⚠️ ' + esc(fix.disclaimer) + '</p>' +
    '</details>'
    );
}

$('results').innerHTML = out.join('');
}

async function run() {
setBusy(true);
$('error').textContent = '';
$('results').innerHTML = '';
try {
    const res = await fetch('/v1/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        code: $('code').value,
        function_name: $('fname').value.trim() || null,
        model_tier: $('tier').value || null,
        include_fix_suggestion: true,
    }),
    });
    let data;
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!res.ok) { showError(res.status, data); return; }
    render(data);
    showShareBar(data.analysis_id);
} catch (e) {
    $('error').textContent = 'Network error: ' + e.message;
} finally {
    setBusy(false);
}
}

function shareUrl(id) {
return location.origin + location.pathname + '?run=' + encodeURIComponent(id);
}

function showShareBar(id) {
const url = shareUrl(id);
$('share').innerHTML =
    '<span class="share-label">🔗 Shareable link</span>' +
    '<code id="share-url">' + esc(url) + '</code>' +
    '<button id="copy-link" type="button">Copy</button>';
$('copy-link').addEventListener('click', () => {
    navigator.clipboard.writeText(url).then(() => {
    $('copy-link').textContent = 'Copied!';
    setTimeout(() => { $('copy-link').textContent = 'Copy'; }, 1500);
    }).catch(() => {
    const range = document.createRange();
    range.selectNode($('share-url'));
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
    });
});
}

async function loadShared(id) {
$('results').innerHTML = '<div class="summary">Loading shared result…</div>';
try {
    const res = await fetch('/v1/runs/' + encodeURIComponent(id));
    let data;
    try { data = await res.json(); } catch (e) { data = {}; }
    if (res.status === 404) {
    $('results').innerHTML = '';
    $('error').textContent = 'This shared result was not found (it may have expired).';
    return;
    }
    if (!res.ok) { $('results').innerHTML = ''; showError(res.status, data); return; }
    render(data);
    showShareBar(id);
} catch (e) {
    $('results').innerHTML = '';
    $('error').textContent = 'Network error: ' + e.message;
}
}

window.addEventListener('DOMContentLoaded', () => {
$('analyze').addEventListener('click', run);
const sharedId = new URLSearchParams(location.search).get('run');
if (sharedId) {
    loadShared(sharedId);
} else {
    $('code').value = EXAMPLE;
}
});
</script>
</body>
</html>
"""