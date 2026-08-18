# 24 — `src/typewright/web.py` (the web demo page)

## 1. What this file is for

Everything TypeWright does already lives behind one HTTP endpoint: you `POST` a Python
function to `/v1/analyze` and get back JSON listing the bugs (and a verified fix). That's
perfect for a program, but a person can't paste a function into JSON by hand. **This file is
the friendly face on top of that endpoint** — a single web page where someone types or pastes
a function, clicks **Analyze**, and reads the results in plain language.

Think of `/v1/analyze` as a vending machine that only takes exact change (JSON). `web.py` is
the cashier standing in front of it: it takes your everyday request, makes the exact-change
call for you, and hands the result back in a form you can read.

The whole page is **one self-contained HTML document** — the layout, the styling, and the
little bit of JavaScript that talks to the API are all in the same string. There is no
separate website project, no build step, no JavaScript framework to install. It is stored as
a plain Python string (`INDEX_HTML`) and `main.py` serves it at the site's front door,
`GET /`.

## 2. A mental model

Three ideas make the rest obvious:

1. **Same front door, two doors inside.** The browser loads the page from `GET /` (this file),
   and the page itself then calls `POST /v1/analyze` (the real engine). Both are served by the
   *same* program at the *same* address. Because it's the same address ("same origin"), the
   browser doesn't need any special cross-site permission (no "CORS") — the call just works.

2. **The page is data, not a separate app.** Rather than keep the HTML in its own file that has
   to be found and shipped alongside the code, we keep it as a Python string *inside the
   package*. That means it travels everywhere the code travels — into the installed package and
   the Docker image — with nothing extra to package or any file path to get wrong.

3. **Be honest when things go wrong.** The engine can answer in several ways: success, "your
   code doesn't parse" (400), "you're going too fast" (429), "a stage failed" (500), "it took
   too long" (504). The page turns each of those into a readable sentence instead of a blank
   screen or a raw error blob.

## 3. The whole file

```python
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
  else if (status === 500) msg = 'Pipeline error' + (data && data.stage ? ' at stage "' + data.stage + '"' : '') + ': ' + detail;
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
          '<div><b>Error:</b> <code>' + esc(b.error) + '</code> <span class="muted">(' + esc(b.test_name) + ')</span></div>' +
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
  history.replaceState(null, '', '?run=' + encodeURIComponent(id));
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
```

## 4. Step-by-step

**The module docstring + the raw string.** The page lives in one variable, `INDEX_HTML`. Two
small things make that safe to do in Python:

- It's a **raw** string (`r"""..."""`). Inside the page's JavaScript there are escape codes like
  `'\n'` (meaning "a newline"). A normal Python string would *eat* those (turning `\n` into a real
  line break in the wrong place); a raw string leaves them exactly as written, so the browser sees
  the JavaScript the way we intended.
- The example function shown in the box has a Python docstring, which uses **three double-quotes**
  (`"""`). If those three characters appeared literally in our source, Python would think our
  `r"""..."""` string had ended early. So the page builds them at the last moment in JavaScript:
  `'"' + '"' + '"'`. Same result on screen, but the three quotes never sit next to each other in
  this file.

**`<style>` — the look.** A small dark theme defined with CSS variables (`--bg`, `--accent`, …).
Nothing here talks to the server; it's purely how things look. Monospace for code, a red left
border for bugs, a green tint for the verified-fix box.

**`<body>` — the shape of the page.** A heading, a panel with the **textarea** (where you paste
the function), an optional **function name** box, a **model tier** dropdown, and the **Analyze**
button. Below the panel sits an empty `#error` line and an empty `#results` area that JavaScript
fills in after a run.

**`esc()` — safety first.** Everything the server sends back (a failing input, an error message,
the fix code) is **escaped** before it's put on the page — `<` becomes `&lt;`, and so on. This is
the standard guard against a function's text accidentally (or maliciously) injecting HTML into the
page. We build result HTML by hand, so we must escape every value we drop into it.

**`run()` — the one network call.** When you click Analyze, `run()`:
1. disables the button and clears old output (`setBusy(true)`),
2. sends a `POST` to `/v1/analyze` with your code, the optional name, the chosen tier, and
   `include_fix_suggestion: true` (we always ask for a fix — the verified fix is the highlight),
3. reads the JSON reply,
4. if the status is not OK, shows a friendly error; otherwise renders the result,
5. re-enables the button no matter what (`finally`).

Note `function_name` and `model_tier` are sent as `null` when left blank — the API treats those as
"figure it out / use the default," exactly as a hand-written request would.

**`render()` — turning JSON into a readable card.** It builds, in order: a one-line **summary**
(red "found N issues" or green "no violations"), the **properties checked** (each detected property
class + the exact relation it tested, e.g. `absolute(x) >= 0`, with a confidence percentage), one
**bug card** per finding (severity icon, the violated property, the failing input, the error type),
and finally the **suggested fix** in a collapsible box — opened by default when it's verified,
labelled "unverified — no confident fix" when it isn't, and always carrying the
"AI suggestion — review carefully" disclaimer.

**`showError()` — honest failures.** It maps each HTTP status to a sentence a person can act on:
400 = "could not analyze this function" (your code didn't parse, or the named function wasn't
found), 429 = "rate limited," 500 = "pipeline error" (and it names the failing *stage* if the body
included one), 504 = "exceeded its time budget." A thrown network error (server down) is caught
separately and shown as "Network error."

**The startup line.** On page load we wire the button, then look at the URL. If it carries a
`?run=<id>` (someone opened a shared link), we load that saved result instead of starting fresh;
otherwise we drop the buggy `absolute` example into the box so a first-time visitor can click
**Analyze** immediately and watch a real bug surface — no typing required.

**Shared links (Unit 2b).** Two small pieces turn a result into something you can send someone:
- `showShareBar(id)` runs after every analysis (and after loading a shared result). It rewrites the
  browser's address to `?run=<id>` with `history.replaceState` (no page reload), then shows a
  "🔗 Shareable link [Copy]" bar. **Copy** uses the browser's `navigator.clipboard`; if that's
  blocked (some browsers only allow it over HTTPS), it falls back to selecting the link text so you
  can copy it by hand.
- `loadShared(id)` is the other direction: given an id from the URL, it `fetch`es
  `GET /v1/runs/{id}`, and on success renders that stored result with the *same* `render()` used for
  a live analysis. A 404 becomes a friendly "this shared result was not found (it may have expired)"
  instead of a blank page. The result is read straight from storage — no LLM, no sandbox — so a
  shared link opens fast and works even when Kestrel is down.

## 5. What could go wrong (and why the code is shaped to avoid it)

- **The page and the engine getting out of sync about the address.** The page calls `/v1/analyze`
  with a *relative* path, not a hard-coded `http://localhost:8001/...`. That's deliberate: a
  relative path always points back at wherever the page was served from, so the demo works
  unchanged on localhost, behind a tunnel, or on a real domain — and because it's the same origin,
  there's no CORS to configure. The test asserts the page actually contains `/v1/analyze`, so a
  rename can't silently break the wiring.
- **A function's text breaking the page layout (or worse).** Every server value is passed through
  `esc()` before being inserted. Without it, a function containing `</div>` or a `<script>` tag in
  a string could scramble the page or run code. (We escape because we build HTML by hand; a
  framework would do this for us.)
- **The triple-quote trap.** As above — the example docstring's `"""` is assembled in JavaScript so
  it never appears literally and never closes the Python string early. If you edit the example,
  keep that trick (or you'll get a confusing Python syntax error far from the real change).
- **A blank screen on failure.** Three layers prevent that: a non-OK status goes to `showError`
  with a specific message; a body that isn't JSON is swallowed (`data = {}`) instead of throwing;
  and a dropped connection is caught and shown as a network error. The button is always re-enabled
  in `finally`, so the page never gets stuck on "Analyzing…".
- **Looking frozen during a long run.** A real analysis is several LLM calls plus a sandbox run —
  tens of seconds. The button switches to a disabled "Analyzing…" state for the whole call, so the
  user knows it's working rather than clicking again.
- **Shipping the page as a separate file that goes missing.** Keeping the HTML as a Python string
  means it can't be "left behind" when the package is built or the image is assembled — there's no
  static-asset path to configure and nothing to forget (D49).

## 6. Change history

- **2026-06-28** — **Created (Phase 8, Unit 1, D49).** The web demo: one self-contained HTML page
  (inline CSS + vanilla JS, no build step, no external assets) stored as `INDEX_HTML` and served by
  `main.py` at `GET /`. It POSTs to the existing `POST /v1/analyze` on the same origin with
  `include_fix_suggestion` on, and renders the detected properties, each bug's failing input +
  severity, and the collapsible verified fix. Pre-fills a buggy `absolute` so the first click finds
  a real bug; degrades every non-200 (400/422/429/500-with-stage/504/network) into a readable line.
  This single unit meets the Phase 8 exit criterion; shareable links (`GET /v1/runs/{id}`) + storage
  and per-IP rate-limiting are deferred (Phase 9 owns limits). See `06_main.md` for the `GET /` route
  and `07_tests.md` for `test_web.py`.
- **2026-06-28** — **Shareable result view + copy-link (Phase 8, Unit 2b).** Four edits to the page,
  backed by the Unit 2a store (`25_store.md`, D50): a `<div id="share">` bar + its CSS; `showShareBar(id)`
  (rewrites the URL to `?run=<id>` via `history.replaceState` and renders a "🔗 Shareable link [Copy]"
  bar, copy via `navigator.clipboard` with a select-the-text fallback); `loadShared(id)` (fetches
  `GET /v1/runs/{id}` and renders the stored result read-only, with a friendly message on 404); and a
  rewritten `DOMContentLoaded` that loads a shared result when the URL has `?run=<id>`, else pre-fills the
  example. `run()` now calls `showShareBar(data.analysis_id)` after a fresh analysis. No API change.
  `test_web.py` gained a third test (`/v1/runs/` + `id="share"` present in the page). Verified live: an
  analysis produced a `?run=…` link that re-rendered the same result in a new tab (store read, no Kestrel).
  Suite 143 passed.
- **2026-06-28** — D57: `render()` now shows an **inferred-property disclaimer** under the summary when bugs
  are present ("findings are violations of AI-inferred properties — confirm each is one your function is meant
  to guarantee"). Honest framing for over-inferred metamorphic/postcondition relations (§8 risk 2).
- **2026-08-16** — Phase 10 launch framing (D64) + the access gate's client half (D62). The page now:
  (1) reframes the header as **an AI property-based test generator** whose fix is *proved by re-running the
  same tests*; (2) hoists a **verified** fix above the findings via a new `fixBlock(fix)` helper (an
  unverified one stays at the bottom) — the verified fix is the executed, grounded half of a result, while a
  finding is an inference; (3) finally renders **`unavailable_imports`** (returned by the API since D61 but
  silently dropped here), so a sandbox-skipped run reads as "not checked" rather than "no bugs"; (4) adds a
  collapsible **"How reliable is this?"** note publishing the measured precision (49 swept / 15 flagged / 3
  confirmed) and the two dominant false-positive classes; (5) reads `?code=` from its own URL, forwards it as
  `X-Demo-Access-Code` on every analyze call, and keeps it on generated share links; and (6) explains 403
  (needs the access code) and 503-with-`period` (daily/monthly budget paused, shared links still work).
