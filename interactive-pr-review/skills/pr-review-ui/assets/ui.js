// Claude replaces the token below with the grouping JSON described in the skill.
const DATA = /*__PR_REVIEW_DATA__*/ null;

const HAS_HLJS = typeof hljs !== "undefined";

// Comment stores.
//  lineComments keyed by "path::side::line" where `line` is the range's LAST line (the
//  line GitHub anchors a comment to); each entry also carries `startLine` (equal to `line`
//  for a single-line comment). fileComments keyed by "path".
const lineComments = new Map();
const fileComments = new Map();

// Files the reviewer has ticked off as reviewed, keyed by path. Progress tracking only —
// local to this page and never exported. Keyed by path (not per group) so a file appearing
// in several groups stays in sync everywhere it's shown.
const reviewedFiles = new Set();
// Files whose diff is folded away, keyed by path. Ticking Reviewed folds a file; the header
// caret folds/unfolds any file independently. Presentation only — never exported.
const collapsedFiles = new Set();
// Per-file DOM registry: path -> [{ wrap, input, fold }], so one tick or fold updates every
// rendered copy of that file.
const reviewedNodes = new Map();
// Per-group progress updaters, run whenever any checkbox changes.
const groupProgress = [];

// Count of AI insight bubbles rendered (read-only; not part of the review).
let insightCount = 0;
// Count of groups carrying a "things worth confirming" list (advisory; not part of the review).
let confirmCount = 0;

// Current diff view mode: "unified" | "split" | "full".
let viewMode = "unified";
// Registry of per-file body re-render fns, so a view-mode switch redraws every file.
const fileBodies = [];

/* ---------- draft persistence (survives a reload of the same page) ---------- */

// EVERY file:// page shares ONE localStorage, so the key MUST identify this exact review or
// one PR's draft would surface in another's page. Keying on the head SHA also expires the
// draft for free: new commits mean a new page, no stored draft, and no comments restored
// onto line numbers that have since moved.
const DRAFT_PREFIX = "pr-review-draft:";
const DRAFT_KEY = (() => {
  const pr = (DATA && DATA.pr) || {};
  return pr.number && pr.headSha
    ? DRAFT_PREFIX + pr.number + ":" + pr.headSha : null;
})();
// Drafts older than this are pruned on load; browser storage is not reachable from the
// cleanup command, so the page has to tidy up after itself.
const DRAFT_TTL_MS = 30 * 24 * 60 * 60 * 1000;

/** Run `fn`, returning `fb` if storage is unavailable (private mode, disabled, quota). */
function safeStore(fn, fb) {
  try { return fn(window.localStorage); } catch (e) { return fb; }
}

function saveDraft() {
  if (!DRAFT_KEY) return;
  const summaryEl = document.getElementById("summary");
  const draft = {
    savedAt: Date.now(),
    summary: summaryEl ? summaryEl.value : "",
    lineComments: [...lineComments.values()],
    fileComments: [...fileComments.values()],
    reviewed: [...reviewedFiles],
    collapsed: [...collapsedFiles],
  };
  const empty = !draft.summary && !draft.lineComments.length &&
    !draft.fileComments.length && !draft.reviewed.length && !draft.collapsed.length;
  safeStore(ls => empty ? ls.removeItem(DRAFT_KEY)
                        : ls.setItem(DRAFT_KEY, JSON.stringify(draft)));
}

/** Drop drafts for other reviews once they pass the TTL. Never touches this page's key. */
function pruneDrafts() {
  safeStore(ls => {
    const now = Date.now();
    for (const k of Object.keys(ls)) {
      if (!k.startsWith(DRAFT_PREFIX) || k === DRAFT_KEY) continue;
      let at = 0;
      try { at = (JSON.parse(ls.getItem(k)) || {}).savedAt || 0; } catch (e) { at = 0; }
      if (!at || now - at > DRAFT_TTL_MS) ls.removeItem(k);
    }
  });
}

/**
 * Rehydrate the comment stores from a stored draft. Must run BEFORE the first render so the
 * saved rows are drawn from state like any other comment. Returns how many were restored.
 */
function loadDraft() {
  if (!DRAFT_KEY) return 0;
  const raw = safeStore(ls => ls.getItem(DRAFT_KEY), null);
  if (!raw) return 0;
  let d;
  try { d = JSON.parse(raw); } catch (e) { return 0; }
  if (!d || typeof d !== "object") return 0;

  (d.lineComments || []).forEach(c => {
    if (!c || !c.path || !c.body || c.line == null) return;
    const side = c.side === "LEFT" ? "LEFT" : "RIGHT";
    const line = Number(c.line);
    const start = c.startLine != null ? Number(c.startLine) : line;
    lineComments.set(lineKey(c.path, side, line),
      { path: c.path, side, line, startLine: start, body: String(c.body) });
  });
  (d.fileComments || []).forEach(c => {
    if (c && c.path && c.body) fileComments.set(c.path, { path: c.path, body: String(c.body) });
  });
  (d.reviewed || []).forEach(p => reviewedFiles.add(p));
  (d.collapsed || []).forEach(p => collapsedFiles.add(p));

  const summaryEl = document.getElementById("summary");
  if (summaryEl && d.summary) summaryEl.value = d.summary;
  return lineComments.size + fileComments.size;
}

function discardDraft() {
  lineComments.clear();
  fileComments.clear();
  reviewedFiles.clear();
  collapsedFiles.clear();
  const summaryEl = document.getElementById("summary");
  if (summaryEl) summaryEl.value = "";
  safeStore(ls => ls.removeItem(DRAFT_KEY));
  // Full re-render, not a body redraw: file-comment slots and reviewed/fold headers are
  // built by render(), which also resets every per-file registry.
  render();
  flash("Cached comments deleted");
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
// A comment is keyed by its LAST line — the line GitHub anchors it to; a range's start is
// stored alongside, so a single-line and a multi-line comment ending on the same line are one.
function lineKey(path, side, line) { return path + "::" + side + "::" + line; }

// Human-readable location of a comment or selection.
function locLabel(path, side, start, end) {
  return esc(path) + " · " + side + (start === end
    ? " line " + end : " lines " + start + "–" + end);
}

// Map a file language hint / extension to a highlight.js language name.
function hlLang(file) {
  if (file.language) {
    const l = String(file.language).toLowerCase();
    const alias = { ts: "typescript", tsx: "typescript", js: "javascript",
      jsx: "javascript", yml: "yaml", sh: "bash", shell: "bash", md: "markdown",
      py: "python", "c++": "cpp", "c#": "csharp" };
    return alias[l] || l;
  }
  const m = /\.([a-z0-9]+)$/i.exec(file.path || "");
  const ext = m ? m[1].toLowerCase() : "";
  const byExt = { ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
    mjs: "javascript", cjs: "javascript", json: "json", yml: "yaml", yaml: "yaml",
    sh: "bash", bash: "bash", md: "markdown", py: "python", rb: "ruby", go: "go",
    rs: "rust", java: "java", kt: "kotlin", css: "css", scss: "scss", html: "xml",
    xml: "xml", sql: "sql", php: "php", swift: "swift", c: "c", h: "c", cpp: "cpp",
    cs: "csharp" };
  return byExt[ext] || null;
}

function highlight(text, lang) {
  if (!HAS_HLJS || !lang || !hljs.getLanguage(lang)) return esc(text);
  try { return hljs.highlight(text, { language: lang, ignoreIllegals: true }).value; }
  catch (e) { return esc(text); }
}

function statusClass(s) {
  s = (s || "modified").toLowerCase();
  return ["added", "modified", "removed", "renamed", "binary"].includes(s) ? s : "modified";
}

function render() {
  const root = document.getElementById("groups");
  if (!DATA) {
    root.innerHTML = "<p style='color:var(--muted)'>No PR data was injected into this template.</p>";
    return;
  }
  const pr = DATA.pr || {};
  document.getElementById("pr-title").innerHTML =
    (pr.url ? "<a href='" + esc(pr.url) + "' target='_blank'>#" + esc(pr.number) + "</a> " : "#" + esc(pr.number) + " ") +
    esc(pr.title);
  document.getElementById("pr-sub").innerHTML =
    "by <strong>" + esc(pr.author) + "</strong><br>" +
    esc(pr.base) + " ← " + esc(pr.head);
  document.getElementById("pr-stats").innerHTML =
    "<span class='stat add'>+" + esc(pr.additions) + "</span>" +
    "<span class='stat del'>−" + esc(pr.deletions) + "</span>" +
    "<span>" + esc(pr.changedFiles) + " file(s)</span>";

  const toc = document.getElementById("toc");
  toc.innerHTML = "";
  root.innerHTML = "";
  // Per-file DOM registries are rebuilt below. `reviewedFiles`/`collapsedFiles` are user
  // state, NOT DOM state — they survive a re-render (so a restored draft keeps its folds);
  // discardDraft() clears them explicitly.
  fileBodies.length = 0;
  reviewedNodes.clear();
  groupProgress.length = 0;

  // Overview: holistic summary of what the PR achieves, above the groups.
  if (DATA.overview && String(DATA.overview).trim()) {
    const ov = document.createElement("div");
    ov.className = "overview";
    const paras = String(DATA.overview).trim().split(/\n\s*\n/)
      .map(p => "<p>" + esc(p.trim()) + "</p>").join("");
    ov.innerHTML = "<div class='overview-title'>What this PR does</div>" + paras;
    root.appendChild(ov);
  }
  // Count insights once, from the data (bubbles are re-rendered many times).
  insightCount = (DATA.groups || []).reduce((sum, g) =>
    sum + (g.files || []).reduce((s, f) => s + (f.insights || []).length, 0), 0);
  // Count groups that carry a "things worth confirming" list.
  confirmCount = (DATA.groups || []).filter(g =>
    Array.isArray(g.thingsToConfirm) && g.thingsToConfirm.length).length;

  // First-occurrence jump target per file (a file spanning concerns appears in several
  // groups; the tree links to the first, since that's where its diff first shows).
  const fileNav = new Map();

  (DATA.groups || []).forEach((g, gi) => {
    const gid = g.id || ("g" + (gi + 1));
    const files = g.files || [];
    const fileCount = files.length;

    // TOC entry
    const a = document.createElement("a");
    a.href = "#" + gid;
    a.innerHTML = "<span>" + esc(g.title) + "</span><span class='n'>" + fileCount + "</span>";
    toc.appendChild(a);

    const details = document.createElement("details");
    details.className = "group";
    details.id = gid;
    if (gi === 0) details.open = true;
    const confirm = Array.isArray(g.thingsToConfirm) && g.thingsToConfirm.length
      ? "<div class='confirm'><div class='confirm-title'>✓ Things worth confirming</div><ul>" +
        g.thingsToConfirm.map(t => "<li>" + esc(t) + "</li>").join("") + "</ul></div>"
      : "";

    // Per-file DOM ids so the manifest can link to each file within this group.
    const fileId = (fi) => gid + "-f" + fi;

    // Record each file's first-seen jump target for the sidebar file tree.
    files.forEach((f, fi) => {
      if (f.path && !fileNav.has(f.path)) {
        fileNav.set(f.path, { gid, domId: fileId(fi),
          additions: f.additions, deletions: f.deletions });
      }
    });

    // Manifest: how the files in this group fit together. A table keeps the file and role
    // columns aligned regardless of how long any path or role is. Uses each file's `role`
    // (short phrase) if present, else its neutral description.
    const manifest = fileCount
      ? "<div class='manifest'><div class='manifest-title'>📄 Files in this group</div>" +
        "<table class='manifest-table'><thead><tr><th class='mfile'>File</th>" +
        "<th class='mrole'>Role</th></tr></thead><tbody>" +
        files.map((f, fi) => {
          const role = f.role || f.description || "";
          const name = (f.path || "").split("/").pop();
          return "<tr><td class='mfile'><a href='#" + fileId(fi) + "'>" + esc(name) + "</a>" +
            "<span class='mpath'>" + esc(f.path) + "</span></td>" +
            "<td class='mrole'>" + esc(role) + "</td></tr>";
        }).join("") + "</tbody></table></div>"
      : "";

    details.innerHTML =
      "<summary><span class='title'>" + esc(g.title) + "</span>" +
      "<span class='count'>" + fileCount + " file(s)</span>" +
      "<span class='progress' title='Files marked reviewed in this group'></span></summary>" +
      (g.reasoning ? "<div class='reasoning'>" + esc(g.reasoning) + "</div>" : "") +
      confirm + manifest;

    files.forEach((f, fi) => details.appendChild(renderFile(f, fileId(fi))));
    root.appendChild(details);

    // Reviewed-progress indicator for this group. Counts distinct paths, so a file listed
    // twice in one group isn't double-counted.
    const groupPaths = [...new Set(files.map(f => f.path))];
    const pill = details.querySelector("summary .progress");
    const updateProgress = () => {
      const done = groupPaths.filter(p => reviewedFiles.has(p)).length;
      pill.textContent = done + "/" + groupPaths.length + " reviewed";
      pill.classList.toggle("done", groupPaths.length > 0 && done === groupPaths.length);
    };
    groupProgress.push(updateProgress);
    updateProgress();
  });
  updateCount();
  buildFileTree(fileNav);
  document.getElementById("insight-count").textContent = insightCount;

  // Insights toggle: default on; disabled when there are none.
  const toggle = document.getElementById("toggle-insights");
  if (insightCount === 0) {
    toggle.checked = false; toggle.disabled = true;
    document.body.classList.add("hide-insights");
  }

  // Things-to-confirm toggle: default on; disabled when no group has a list.
  document.getElementById("confirm-count").textContent = confirmCount;
  const confirmToggle = document.getElementById("toggle-confirm");
  if (confirmCount === 0) {
    confirmToggle.checked = false; confirmToggle.disabled = true;
    document.body.classList.add("hide-confirm");
  }

  // Files-table toggle: disabled when no group has files to tabulate.
  const manifestToggle = document.getElementById("toggle-manifest");
  if (!root.querySelector(".manifest")) {
    manifestToggle.checked = false; manifestToggle.disabled = true;
  }
}

// Build the sidebar file tree: a real nested hierarchy, one foldable level per directory
// segment (indenting further at each depth), with clickable file leaves. Clicking a file
// unfolds its group and scrolls. Navigation only — the fold state carries no review meaning.
function buildFileTree(fileNav) {
  const tree = document.getElementById("filetree");
  tree.innerHTML = "";
  document.getElementById("file-count").textContent = fileNav.size;

  // Sort by path so sibling dirs and files render in a stable, grouped order.
  const entries = [...fileNav.entries()].sort((a, b) => a[0].localeCompare(b[0]));

  // Nest every path segment into a directory tree: each node holds child dirs (kept in
  // insertion order) and file leaves, giving one indent level per directory.
  const root = { dirs: new Map(), files: [] };
  entries.forEach(([path, nav]) => {
    const parts = path.split("/");
    const name = parts.pop();
    let node = root;
    parts.forEach(seg => {
      if (!node.dirs.has(seg)) node.dirs.set(seg, { dirs: new Map(), files: [] });
      node = node.dirs.get(seg);
    });
    node.files.push({ name, path, nav });
  });

  // Render a node's subdirectories (foldable) then its files. Each directory's contents go
  // into a .ft-children box whose left border draws that level's vertical depth guide, so
  // indentation and the guide lines come from the same nesting rather than padding math.
  (function renderNode(node, parent) {
    node.dirs.forEach((child, seg) => {
      const det = document.createElement("details");
      det.className = "ft-node";
      det.open = true;
      const sum = document.createElement("summary");
      sum.className = "ft-dir";
      sum.textContent = seg + "/";
      det.appendChild(sum);
      const kids = document.createElement("div");
      kids.className = "ft-children";
      det.appendChild(kids);
      parent.appendChild(det);
      renderNode(child, kids);
    });
    node.files.forEach(({ name, path, nav }) => {
      const stats = [];
      if (nav.additions != null) stats.push("<span class='add'>+" + esc(nav.additions) + "</span>");
      if (nav.deletions != null) stats.push("<span class='del'>−" + esc(nav.deletions) + "</span>");
      const btn = document.createElement("button");
      btn.className = "ft-file";
      btn.type = "button";
      btn.title = path;
      btn.innerHTML = "<span class='ft-name'>" + esc(name) + "</span>" +
        (stats.length ? "<span class='ft-stats'>" + stats.join("") + "</span>" : "");
      btn.addEventListener("click", () => jumpToFile(nav.gid, nav.domId));
      parent.appendChild(btn);
    });
  })(root, tree);
}

// Scroll to a file, unfolding its (possibly collapsed) group first, and flash it briefly.
function jumpToFile(gid, domId) {
  const group = document.getElementById(gid);
  if (group && group.tagName === "DETAILS") group.open = true;
  const el = document.getElementById(domId);
  if (!el) return;
  // Unfold the file if it was collapsed, so the jump always lands on a visible diff.
  if (el.dataset.path && collapsedFiles.has(el.dataset.path)) setCollapsed(el.dataset.path, false);
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.classList.remove("ft-jumped");
  void el.offsetWidth;            // restart the animation if the same file is re-clicked
  el.classList.add("ft-jumped");
}

// Whether any file carries embedded full content (enables expandable context).
function anyFullContent() {
  return (DATA.groups || []).some(g => (g.files || [])
    .some(f => typeof f.fullContent === "string"));
}

const VIEW_HINTS = {
  unified: "Changes inline, one column.",
  split: "Old on the left, new on the right."
};

// Wire the sidebar controls once (render() draws file bodies; these switch/redraw them).
function wireControls() {
  const toggle = document.getElementById("toggle-insights");
  toggle.addEventListener("change", () =>
    document.body.classList.toggle("hide-insights", !toggle.checked));

  const confirmToggle = document.getElementById("toggle-confirm");
  confirmToggle.addEventListener("change", () =>
    document.body.classList.toggle("hide-confirm", !confirmToggle.checked));

  const manifestToggle = document.getElementById("toggle-manifest");
  manifestToggle.addEventListener("change", () =>
    document.body.classList.toggle("hide-manifest", !manifestToggle.checked));

  // Sidebar collapse: plain class flip, no animation. `sb-collapsed` on <body> lets the
  // toast recentre over the widened main column.
  const sbBtn = document.getElementById("sb-collapse");
  const sidebar = document.querySelector("aside.sidebar");
  sbBtn.addEventListener("click", () => {
    const collapsed = sidebar.classList.toggle("collapsed");
    document.body.classList.toggle("sb-collapsed", collapsed);
    sbBtn.textContent = collapsed ? "»" : "«";
    sbBtn.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
  });

  const view = document.getElementById("view-mode");
  const hint = document.getElementById("view-hint");
  const expandable = anyFullContent();
  hint.textContent = VIEW_HINTS[viewMode] + (expandable ? " Use ⋯ to expand surrounding lines." : "");
  view.value = viewMode;
  view.addEventListener("change", () => {
    viewMode = view.value;
    hint.textContent = VIEW_HINTS[viewMode] + (expandable ? " Use ⋯ to expand surrounding lines." : "");
    document.body.classList.toggle("view-split", viewMode === "split");
    redrawFiles();
  });
}

function renderFile(f, domId) {
  const wrap = document.createElement("div");
  wrap.className = "file";
  if (domId) wrap.id = domId;
  wrap.dataset.path = f.path;
  const sc = statusClass(f.status);
  const lang = hlLang(f);

  // Header: status, path (+ rename), meta (lang, +/-), AI description, file-comment button.
  const header = document.createElement("div");
  header.className = "file-header";
  const rename = f.previousPath && f.previousPath !== f.path
    ? "<span class='file-rename'>" + esc(f.previousPath) + " → </span>" : "";
  const meta = [];
  if (lang) meta.push("<span class='lang'>" + esc(lang) + "</span>");
  if (f.additions != null) meta.push("<span class='stat add'>+" + esc(f.additions) + "</span>");
  if (f.deletions != null) meta.push("<span class='stat del'>−" + esc(f.deletions) + "</span>");
  header.innerHTML =
    "<div class='top'>" +
      "<button class='fold-btn' type='button' title='Fold/unfold this file'>▾</button>" +
      "<span class='file-status " + sc + "'>" + esc(f.status || "modified") + "</span>" +
      "<span class='file-path'>" + rename + esc(f.path) + "</span>" +
      "<label class='reviewed-box' title='Mark this file as reviewed'>" +
        "<input type='checkbox' class='reviewed-input' />Reviewed</label>" +
    "</div>" +
    (meta.length ? "<div class='file-meta'>" + meta.join("") + "</div>" : "") +
    (f.description ? "<div class='file-desc'>" + esc(f.description) + "</div>" : "") +
    (f.focusNote
      ? "<div class='file-focus'>🎯<span>This group's changes are at " +
        "<span class='flines'>line" + (/[–,]/.test(f.focusNote) ? "s" : "") + " " +
        esc(f.focusNote) + "</span>; the rest of the file's diff is shown for context.</span></div>"
      : "") +
    "<div class='actions'><button class='file-comment-btn' data-path='" + esc(f.path) +
      "'>💬 Comment on this file</button></div>";
  wrap.appendChild(header);

  const fileCommentSlot = document.createElement("div");
  wrap.appendChild(fileCommentSlot);
  header.querySelector(".file-comment-btn")
    .addEventListener("click", () => openFileComment(f.path, fileCommentSlot, header));

  // Reviewed checkbox + fold caret. Registered by path so ticking or folding one copy of a
  // multi-group file updates the others, then every group's X/Y indicator is refreshed.
  const reviewedInput = header.querySelector(".reviewed-input");
  const foldBtn = header.querySelector(".fold-btn");
  if (!reviewedNodes.has(f.path)) reviewedNodes.set(f.path, []);
  reviewedNodes.get(f.path).push({ wrap, input: reviewedInput, fold: foldBtn });
  reviewedInput.checked = reviewedFiles.has(f.path);
  wrap.classList.toggle("reviewed", reviewedFiles.has(f.path));
  applyCollapsed(wrap, foldBtn, collapsedFiles.has(f.path));
  // Ticking Reviewed folds the file away; unticking brings it back.
  reviewedInput.addEventListener("change", () => {
    setReviewed(f.path, reviewedInput.checked);
    setCollapsed(f.path, reviewedInput.checked);
  });
  // Manual fold works independently of reviewed state.
  foldBtn.addEventListener("click", () => setCollapsed(f.path, !collapsedFiles.has(f.path)));

  // The diff/file body lives in its own container that we can fully re-render
  // (on comment change or view-mode switch) without touching the header.
  const body = document.createElement("div");
  body.className = "file-body";
  wrap.appendChild(body);
  const draw = () => drawFileBody(f, body, lang);
  fileBodies.push({ path: f.path, draw });
  draw();
  return wrap;
}

// Normalize a file's insights. Bubbles render ABOVE the first line of their range,
// so we key them by `start`. `spanned` marks every line inside a range.
function fileInsights(f) {
  const byStart = new Map(); // "side::startLine" -> [insight,…] (bubble emitted before startLine)
  const spanned = new Map(); // "side::line" -> true if line is inside some insight range
  (f.insights || []).forEach(ins => {
    const side = ins.side === "LEFT" ? "LEFT" : "RIGHT";
    const end = ins.endLine != null ? ins.endLine : ins.line;
    const start = ins.startLine != null ? ins.startLine : (ins.line != null ? ins.line : end);
    const lo = Math.min(start, end), hi = Math.max(start, end);
    const level = ins.level === "notable" ? "notable" : "routine";
    const norm = { side, start: lo, end: hi, kind: ins.kind, text: ins.text, level };
    const k = side + "::" + lo;
    if (!byStart.has(k)) byStart.set(k, []);
    byStart.get(k).push(norm);
    for (let n = lo; n <= hi; n++) spanned.set(side + "::" + n, true);
  });
  return { byStart, spanned };
}

// Render one file's body in the current viewMode. Reconstructs everything from
// state (DATA + comment Maps + per-file expansion state), so it's safe to call repeatedly.
function drawFileBody(f, body, lang) {
  body.innerHTML = "";
  const split = viewMode === "split";
  const table = document.createElement("table");
  table.className = "diff" + (split ? " split" : "");
  table._path = f.path;
  const ins = fileInsights(f);
  const cols = split ? 4 : 3;

  // Full file text (if embedded) enables expanding surrounding context between hunks.
  const fullLines = typeof f.fullContent === "string" ? f.fullContent.split("\n") : null;
  const hunks = f.hunks || [];

  // Compute, per hunk, its first/last NEW-file line numbers (for context math).
  const firstNew = h => { for (const l of h.lines) if (l.newLine != null) return l.newLine; return null; };
  const lastNew  = h => { let v = null; for (const l of h.lines) if (l.newLine != null) v = l.newLine; return v; };

  wireRangeSelection(table);
  hunks.forEach((h, hi) => {
    // Context gap before this hunk (from end of previous hunk to this hunk's start).
    if (fullLines) {
      const prevEnd = hi === 0 ? 0 : (lastNew(hunks[hi - 1]) || 0);
      const thisStart = firstNew(h);
      if (thisStart != null && thisStart - prevEnd > 1) {
        table.appendChild(renderGap(f, table, lang, cols, prevEnd + 1, thisStart - 1,
          hi === 0 ? "up" : "between", ins, fullLines));
      }
    }
    // Hunk header row. A hunk not relevant to this group (the file appears here as full
    // context for another concern) is dimmed via `hunk-context`.
    const ctx = h.relevant === false;
    const hr = document.createElement("tr");
    hr.className = "hunk" + (ctx ? " hunk-context" : "");
    hr.innerHTML = split
      ? "<td class='ln'></td><td class='code'>" + esc(h.header || "") + "</td>" +
        "<td class='ln'></td><td class='code'>" + esc(h.header || "") + "</td>"
      : "<td class='ln'></td><td class='ln'></td><td class='code'>" + esc(h.header || "") + "</td>";
    table.appendChild(hr);
    if (split) drawHunkSplit(h, table, f, lang, ins, ctx);
    else drawHunkUnified(h, table, f, lang, ins, ctx);
  });

  // Context gap after the last hunk (down to end of file).
  if (fullLines && hunks.length) {
    const lastEnd = lastNew(hunks[hunks.length - 1]) || 0;
    if (lastEnd < fullLines.length) {
      table.appendChild(renderGap(f, table, lang, cols, lastEnd + 1, fullLines.length,
        "down", ins, fullLines));
    }
  }
  // Files with no textual hunks (binary, pure rename, mode change) still get a row, so
  // every changed file is visibly accounted for rather than rendering blank.
  if (!hunks.length) {
    const note = f.status === "binary"
      ? "Binary file — no textual diff to show."
      : (f.status === "renamed"
          ? "Renamed with no content change — no textual diff to show."
          : "No textual diff to show for this file.");
    const tr = document.createElement("tr");
    tr.innerHTML = "<td colspan='" + cols + "' class='nodiff'>" + esc(note) + "</td>";
    table.appendChild(tr);
  }
  body.appendChild(table);
}

// Render a context gap [from,to] (NEW-file line numbers). Parts the user has expanded
// (tracked in f._expanded) show as real context lines; the rest show as expander buttons.
// Returns a DocumentFragment. Expansion state survives redraws.
function renderGap(f, table, lang, cols, from, to, dir, ins, fullLines) {
  const frag = document.createDocumentFragment();
  // Merge the expanded sub-ranges that fall inside this gap.
  const clipped = (f._expanded || [])
    .map(([a, b]) => [Math.max(a, from), Math.min(b, to)])
    .filter(([a, b]) => a <= b)
    .sort((x, y) => x[0] - y[0]);
  const merged = [];
  clipped.forEach(r => {
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1] + 1) last[1] = Math.max(last[1], r[1]);
    else merged.push(r.slice());
  });
  let cursor = from;
  merged.forEach(([a, b]) => {
    if (a > cursor) frag.appendChild(expanderRow(f, cols, cursor, a - 1, "between"));
    for (let n = a; n <= b; n++) appendContextLine(frag, f, table, lang, cols, n, fullLines[n - 1], ins);
    cursor = b + 1;
  });
  if (cursor <= to) frag.appendChild(expanderRow(f, cols, cursor, to, dir));
  return frag;
}

// A clickable "expand context" row for the hidden sub-gap [from,to].
function expanderRow(f, cols, from, to, dir) {
  const count = to - from + 1;
  const row = document.createElement("tr");
  row.className = "expander";
  const label = dir === "up" ? "↑ Expand " + Math.min(20, count) + " above"
    : dir === "down" ? "↓ Expand " + Math.min(20, count) + " below"
    : "↕ Expand " + Math.min(20, count) + " hidden";
  row.innerHTML = "<td class='ln'>⋯</td>" +
    "<td class='code' colspan='" + (cols - 1) + "'>" +
      "<button class='expand-btn'>" + label + "</button>" +
      (count > 20 ? " <button class='expand-btn all'>show all " + count + "</button>" : "") +
    "</td>";
  f._expanded = f._expanded || [];
  const doExpand = (n) => {
    let a = from, b = to;
    if (dir === "up") a = Math.max(from, to - n + 1);   // reveal lines nearest the hunk below
    else b = Math.min(to, from + n - 1);                // reveal lines nearest the hunk above
    f._expanded.push([a, b]);
    redrawFiles(f.path);
  };
  row.querySelector(".expand-btn").addEventListener("click", () => doExpand(20));
  const allBtn = row.querySelector(".expand-btn.all");
  if (allBtn) allBtn.addEventListener("click", () => doExpand(count));
  return row;
}

// Append a revealed full-file context line (RIGHT/new-line n) to `frag`, with any
// insight bubble that STARTS on it placed above, and any saved comment below.
function appendContextLine(frag, f, table, lang, cols, n, text, ins) {
  ins.byStart.get("RIGHT::" + n)?.forEach(one => frag.appendChild(insightRow(one, cols)));
  const spanned = ins.spanned.has("RIGHT::" + n);
  const tr = document.createElement("tr");
  tr.className = "context commentable ctx-expanded" + (spanned ? " insight-scope" : "");
  const cell = "<span class='marker'> </span>" + highlight(text || "", lang);
  if (cols === 4) {
    tr.innerHTML =
      "<td class='ln'>" + n + "</td><td class='code'>" + cell + "</td>" +
      "<td class='ln'>" + n + "</td><td class='code'>" + cell + "</td>";
    markCommentable(tr.children[3], "RIGHT", n);
  } else {
    tr.innerHTML =
      "<td class='ln'></td><td class='ln'>" + n + "</td><td class='code'>" + cell + "</td>";
    markCommentable(tr.querySelector("td.code"), "RIGHT", n);
  }
  frag.appendChild(tr);
  const key = lineKey(f.path, "RIGHT", n);
  if (lineComments.has(key)) frag.appendChild(savedLineRow(table, f.path, "RIGHT", n, cols));
}

// Build one unified diff line <tr> plus its insight bubbles (above) / comment slot (below).
function drawHunkUnified(h, table, f, lang, ins, ctx) {
  (h.lines || []).forEach(ln => {
    const type = ln.type === "add" ? "add" : ln.type === "del" ? "del" : "context";
    const side = type === "del" ? "LEFT" : "RIGHT";
    const num = type === "del" ? ln.oldLine : ln.newLine;
    const spanned = num != null && ins.spanned.has(side + "::" + num);
    // Insight bubbles that START on this line render ABOVE it.
    if (num != null) ins.byStart.get(side + "::" + num)?.forEach(one => table.appendChild(insightRow(one, 3)));
    const tr = document.createElement("tr");
    tr.className = type + " commentable" + (spanned ? " insight-scope" : "") + (ctx ? " hunk-context" : "");
    const marker = type === "add" ? "+" : type === "del" ? "-" : " ";
    tr.innerHTML =
      "<td class='ln'>" + (ln.oldLine == null ? "" : ln.oldLine) + "</td>" +
      "<td class='ln'>" + (ln.newLine == null ? "" : ln.newLine) + "</td>" +
      "<td class='code'><span class='marker'>" + marker + "</span>" +
        highlight(ln.text, lang) + "</td>";
    if (num != null) markCommentable(tr.querySelector("td.code"), side, num);
    table.appendChild(tr);
    if (num != null) {
      const key = lineKey(f.path, side, num);
      if (lineComments.has(key)) table.appendChild(savedLineRow(table, f.path, side, num, 3));
    }
  });
}

// Split view: pair del/add lines into left|right columns; comments/insights span full width.
function drawHunkSplit(h, table, f, lang, ins, ctx) {
  const rows = pairSplitLines(h.lines || []);
  rows.forEach(pair => {
    const L = pair.left, R = pair.right;
    // Insight bubbles that START on either side render ABOVE this row.
    [["LEFT", L && L.oldLine], ["RIGHT", R && R.newLine]].forEach(([sd, n]) => {
      if (n != null) ins.byStart.get(sd + "::" + n)?.forEach(one => table.appendChild(insightRow(one, 4)));
    });
    const tr = document.createElement("tr");
    const lClass = L ? (L.type === "del" ? "del" : "context") : "empty";
    const rClass = R ? (R.type === "add" ? "add" : "context") : "empty";
    tr.className = "split-line" + (ctx ? " hunk-context" : "");
    tr.innerHTML =
      "<td class='ln " + lClass + "'>" + (L && L.oldLine != null ? L.oldLine : "") + "</td>" +
      "<td class='code " + lClass + "'>" + (L ? "<span class='marker'>" +
        (L.type === "del" ? "-" : " ") + "</span>" + highlight(L.text, lang) : "") + "</td>" +
      "<td class='ln " + rClass + "'>" + (R && R.newLine != null ? R.newLine : "") + "</td>" +
      "<td class='code " + rClass + "'>" + (R ? "<span class='marker'>" +
        (R.type === "add" ? "+" : " ") + "</span>" + highlight(R.text, lang) : "") + "</td>";
    if (L && L.oldLine != null) markCommentable(tr.children[1], "LEFT", L.oldLine);
    if (R && R.newLine != null) markCommentable(tr.children[3], "RIGHT", R.newLine);
    table.appendChild(tr);
    [["LEFT", L && L.oldLine], ["RIGHT", R && R.newLine]].forEach(([sd, n]) => {
      if (n == null) return;
      const key = lineKey(f.path, sd, n);
      if (lineComments.has(key)) table.appendChild(savedLineRow(table, f.path, sd, n, 4));
    });
  });
}

// Pair a hunk's lines for split view: dels on the left, adds on the right, context on both.
function pairSplitLines(lines) {
  const rows = [];
  let i = 0;
  while (i < lines.length) {
    const ln = lines[i];
    if (ln.type === "context") { rows.push({ left: ln, right: ln }); i++; continue; }
    // gather a run of dels then a run of adds, and zip them
    const dels = [], adds = [];
    while (i < lines.length && lines[i].type === "del") dels.push(lines[i++]);
    while (i < lines.length && lines[i].type === "add") adds.push(lines[i++]);
    const n = Math.max(dels.length, adds.length);
    for (let j = 0; j < n; j++) rows.push({ left: dels[j] || null, right: adds[j] || null });
    if (!dels.length && !adds.length) i++; // safety
  }
  return rows;
}

// A read-only 💡 bubble explaining code, rendered ABOVE the first line of its range.
// `cols` is the colspan for the current view.
function insightRow(ins, cols) {
  const row = document.createElement("tr");
  row.className = "insight-row";
  const kind = ins.kind ? "<span class='kind'>" + esc(ins.kind) + "</span>" : "";
  const sideLabel = ins.side === "LEFT" ? " (old)" : "";
  const range = ins.start !== ins.end
    ? "Lines " + ins.start + "–" + ins.end + sideLabel
    : "Line " + ins.end + sideLabel;
  const level = ins.level === "notable" ? "notable" : "routine";
  const icon = level === "notable" ? "🔎" : "💡";
  row.innerHTML =
    "<td colspan='" + cols + "'><div class='insight level-" + level + "'>" +
      "<span class='icon'>" + icon + "</span>" +
      "<span><span class='irange'>" + range + "</span>" + kind + esc(ins.text) + "</span>" +
    "</div></td>";
  return row;
}

/* ---------- line range selection (click = one line, drag = many) ---------- */

// Tag a code cell as commentable and record which diff line it represents, so the
// delegated range handlers on the table can resolve any cell to a (side, line) pair.
function markCommentable(cell, side, line) {
  if (!cell) return;
  cell.classList.add("commentable");
  cell.dataset.side = side;
  cell.dataset.line = String(line);
}

// The drag in progress: {table, side, anchor, current} while the pointer is down.
let dragSel = null;

// Wire pointer-based range selection on one diff table. A press on a code cell starts a
// selection; moving over cells on the SAME side extends it; release opens the editor for
// the resulting range (a press-and-release on one line behaves exactly like the old click).
function wireRangeSelection(table) {
  table.addEventListener("pointerdown", (e) => {
    const cell = e.target.closest("td.code.commentable");
    if (!cell || !table.contains(cell) || e.button !== 0) return;
    dragSel = { table, side: cell.dataset.side,
      anchor: Number(cell.dataset.line), current: Number(cell.dataset.line) };
  });
  table.addEventListener("pointermove", (e) => {
    if (!dragSel || dragSel.table !== table) return;
    const cell = e.target.closest("td.code.commentable");
    if (!cell || cell.dataset.side !== dragSel.side) return;
    const line = Number(cell.dataset.line);
    if (line === dragSel.current) return;
    dragSel.current = line;
    // Only once the drag leaves its anchor line do we take over from the browser's own text
    // selection — so a press-and-drag within one line still selects text to copy.
    document.body.classList.add("range-dragging");
    window.getSelection()?.removeAllRanges();
    paintRange(table);
  });
}

/**
 * Tint the commentable cells of [lo,hi] on `side`, clearing any previous tint first. Pass a
 * null `side` to just clear. Drives both the live drag and the range's editor: the tint
 * persists while the editor is open so the span you picked stays visible while you type.
 */
function paintLineRange(table, side, lo, hi) {
  table.querySelectorAll("td.code.in-range").forEach(c => c.classList.remove("in-range"));
  table.querySelectorAll("tr.in-range, tr.range-start, tr.range-end")
    .forEach(r => r.classList.remove("in-range", "range-start", "range-end"));
  if (side == null) return;
  table.querySelectorAll("td.code.commentable").forEach(cell => {
    if (cell.dataset.side !== side) return;
    const n = Number(cell.dataset.line);
    if (n < lo || n > hi) return;
    cell.classList.add("in-range");
    cell.parentElement.classList.add("in-range");
    if (n === lo) cell.parentElement.classList.add("range-start");
    if (n === hi) cell.parentElement.classList.add("range-end");
  });
}

// Paint the in-progress selection: tint every commentable cell inside the range.
function paintRange(table) {
  if (!dragSel) { paintLineRange(table, null); return; }
  paintLineRange(table, dragSel.side,
    Math.min(dragSel.anchor, dragSel.current), Math.max(dragSel.anchor, dragSel.current));
}

// Release anywhere ends the drag; if it ended on a valid range, open the editor for it.
document.addEventListener("pointerup", () => {
  if (!dragSel) return;
  const { table, side, anchor, current } = dragSel;
  dragSel = null;
  document.body.classList.remove("range-dragging");
  const start = Math.min(anchor, current), end = Math.max(anchor, current);
  const cols = table.classList.contains("split") ? 4 : 3;
  const anchorTr = lineRow(table, side, end);
  // The tint is NOT cleared here — toggleLineEditor repaints it for the range whose editor
  // it opens, so the picked span stays visible while you type.
  if (anchorTr) toggleLineEditor(table, anchorTr, table._path, side, start, end, cols);
  else paintRange(table);
});

// The <tr> whose commentable cell on `side` carries line `line`.
function lineRow(table, side, line) {
  const cell = [...table.querySelectorAll("td.code.commentable")]
    .find(c => c.dataset.side === side && Number(c.dataset.line) === line);
  return cell ? cell.parentElement : null;
}

/* ---------- line comments (slot-based, no sibling hunting) ---------- */

/**
 * Leading cells + the opening <td> for a comment/editor row, offset so the body cell starts
 * exactly at the commented side's code column — that way the box's left border continues the
 * commented lines' rail instead of starting further left.
 * Unified columns: ln|ln|code. Split: ln|code(LEFT)|ln|code(RIGHT).
 */
function commentRowCells(cols, side) {
  if (cols !== 4) return "<td class='ln'></td><td class='ln'></td><td class='cbody'>";
  return side === "LEFT"
    ? "<td class='ln'></td><td class='cbody' colspan='3'>"
    : "<td class='ln'></td><td></td><td class='ln'></td><td class='cbody'>";
}

// Open or close the inline editor for the line range [startLine,endLine], inserted right
// after `anchorTr` (the range's last line). A single-line comment passes start === end.
function toggleLineEditor(table, anchorTr, path, side, startLine, endLine, cols) {
  const key = lineKey(path, side, endLine);
  const existingEditor = table.querySelector("tr.comment-row.editing");
  // If the editor for THIS exact range is already open, treat the interaction as a cancel.
  if (existingEditor && existingEditor._key === key && existingEditor._start === startLine) {
    existingEditor.remove(); anchorTr.classList.remove("selected");
    paintLineRange(table, null);
    if (lineComments.has(key)) redrawFiles(path);
    return;
  }
  if (existingEditor) { existingEditor.remove();
    table.querySelectorAll("tr.selected").forEach(t => t.classList.remove("selected")); }

  // Remove any saved-row for this key while editing (we re-add on save/cancel via redraw).
  table.querySelectorAll("tr.saved-row").forEach(r => { if (r._key === key) r.remove(); });

  // Re-opening a saved comment keeps its stored range unless this call selected a new one.
  const existing = lineComments.get(key);
  const start = startLine, end = endLine;
  const row = document.createElement("tr");
  row.className = "comment-row editing";
  row._key = key;
  row._start = start;
  row.innerHTML =
    commentRowCells(cols, side) + "<div class='comment-box'>" +
    "<div class='loc'>" + locLabel(path, side, start, end) + "</div>" +
    "<textarea placeholder='" + (start === end
      ? "Leave a comment on this line…"
      : "Leave a comment on these " + (end - start + 1) + " lines…") + "'>" +
      esc(existing ? existing.body : "") + "</textarea>" +
    "<div class='row'>" +
      "<button class='sm primary save'>Save comment</button>" +
      "<button class='sm cancel'>Cancel</button>" +
      (existing ? "<button class='sm danger delete'>Delete</button>" : "") +
    "</div></div></td>";
  anchorTr.after(row);
  anchorTr.classList.add("selected");
  // Keep the range tinted for as long as this editor is open, so the lines the comment will
  // apply to stay visible while typing. Cleared on save/cancel/delete — after a save the
  // span is marked instead by the saved comment's own rail.
  paintLineRange(table, side, start, end);
  const ta = row.querySelector("textarea"); ta.focus();

  row.querySelector(".save").addEventListener("click", () => {
    const bodyText = ta.value.trim();
    if (!bodyText) { flash("Comment is empty"); return; }
    lineComments.set(key, { path, side, line: Number(end), startLine: Number(start),
      body: bodyText });
    redrawFiles(path);
  });
  row.querySelector(".cancel").addEventListener("click", () => { redrawFiles(path); });
  const del = row.querySelector(".delete");
  if (del) del.addEventListener("click", () => { lineComments.delete(key); redrawFiles(path); });
}

// A persistent saved-comment row (reconstructed from state on every redraw). Also rails the
// lines the comment spans, so a multi-line comment's extent stays visible.
function savedLineRow(table, path, side, line, cols) {
  const c = lineComments.get(lineKey(path, side, line));
  const start = c.startLine != null ? c.startLine : c.line;
  for (let n = start; n <= c.line; n++) {
    const tr = lineRow(table, side, n);
    if (tr) tr.querySelectorAll("td.code.commentable").forEach(cell => {
      if (cell.dataset.side === side) cell.classList.add("comment-scope");
    });
  }
  const row = document.createElement("tr");
  row.className = "saved-row";
  row._key = lineKey(path, side, line);
  row.innerHTML =
    commentRowCells(cols, side) + "<div class='saved-comment'>" +
    "<div class='loc'>" + locLabel(path, side, start, c.line) +
      " — click to edit</div>" + esc(c.body) + "</div></td>";
  row.querySelector(".saved-comment").addEventListener("click", () => {
    // Re-open editor anchored to the line row just above this saved row, on its saved range.
    const anchor = row.previousElementSibling;
    if (anchor) toggleLineEditor(table, anchor, path, side, start, c.line, cols);
  });
  return row;
}

/**
 * Redraw file bodies from current state. Pass a path to redraw only that file (every copy
 * of it — a file spanning concerns is rendered once per group and all copies must stay in
 * sync); omit it to redraw everything, which is what a view-mode switch needs.
 */
function redrawFiles(path) {
  fileBodies.forEach(fb => { if (path === undefined || fb.path === path) fb.draw(); });
  updateCount();
  saveDraft();
}

/* ---------- file-level comments ---------- */
function openFileComment(path, slot, header) {
  const existing = fileComments.get(path);
  slot.innerHTML =
    "<div class='file-comment'><div class='comment-box'>" +
    "<div class='loc'>File comment · " + esc(path) + "</div>" +
    "<textarea placeholder='Leave a comment about this whole file…'>" +
      esc(existing ? existing.body : "") + "</textarea>" +
    "<div class='row'>" +
      "<button class='sm primary save'>Save</button>" +
      "<button class='sm cancel'>Cancel</button>" +
      (existing ? "<button class='sm danger delete'>Delete</button>" : "") +
    "</div></div></div>";
  const ta = slot.querySelector("textarea"); ta.focus();

  slot.querySelector(".save").addEventListener("click", () => {
    const body = ta.value.trim();
    if (!body) { flash("Comment is empty"); return; }
    fileComments.set(path, { path, body });
    renderSavedFile(path, slot, header); updateCount(); saveDraft();
  });
  slot.querySelector(".cancel").addEventListener("click", () => {
    if (fileComments.has(path)) renderSavedFile(path, slot, header);
    else slot.innerHTML = "";
    setFileBtn(header, fileComments.has(path));
  });
  const del = slot.querySelector(".delete");
  if (del) del.addEventListener("click", () => {
    fileComments.delete(path); slot.innerHTML = ""; setFileBtn(header, false);
    updateCount(); saveDraft();
  });
}

function renderSavedFile(path, slot, header) {
  const c = fileComments.get(path);
  slot.innerHTML =
    "<div class='file-comment'><div class='saved-comment'>" +
    "<div class='loc'>File comment · " + esc(path) + " — click to edit</div>" +
    esc(c.body) + "</div></div>";
  slot.querySelector(".saved-comment").addEventListener("click",
    () => openFileComment(path, slot, header));
  setFileBtn(header, true);
}

function setFileBtn(header, has) {
  const btn = header.querySelector(".file-comment-btn");
  if (!btn) return;
  btn.classList.toggle("has", has);
  btn.textContent = has ? "💬 Edit file comment" : "💬 Comment on this file";
}

function updateCount() {
  document.getElementById("comment-count").textContent =
    lineComments.size + fileComments.size;
}

/** Persist the summary as it's typed, and wire the discard-draft button. */
function wireDraft() {
  const summaryEl = document.getElementById("summary");
  // 400ms after typing stops — a write per keystroke would serialize every comment each time.
  if (summaryEl) {
    let t = null;
    summaryEl.addEventListener("input", () => {
      clearTimeout(t); t = setTimeout(saveDraft, 400);
    });
  }
  const btn = document.getElementById("discard-btn");
  if (!btn) return;
  if (!DRAFT_KEY) { btn.style.display = "none"; return; }
  btn.addEventListener("click", () => {
    if (window.confirm("Discard your saved comments, summary, and reviewed marks for this PR?"))
      discardDraft();
  });
}

/**
 * Mark a file reviewed (or not) and sync every rendered copy of it plus all group
 * progress indicators. Progress state only — never exported with the comments.
 */
function setReviewed(path, checked) {
  if (checked) { reviewedFiles.add(path); } else { reviewedFiles.delete(path); }
  (reviewedNodes.get(path) || []).forEach(({ wrap, input }) => {
    input.checked = checked;
    wrap.classList.toggle("reviewed", checked);
  });
  groupProgress.forEach(fn => fn());
  saveDraft();
}

/** Set a single rendered file's folded appearance (class + caret glyph). */
function applyCollapsed(wrap, foldBtn, collapsed) {
  wrap.classList.toggle("collapsed", collapsed);
  foldBtn.textContent = collapsed ? "▸" : "▾";
  foldBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
}

/**
 * Fold or unfold a file's diff across every rendered copy of it. Ticking Reviewed calls
 * this to fold automatically; the header caret calls it directly, so a reviewed file can
 * still be unfolded (and an unreviewed one folded) by hand.
 */
function setCollapsed(path, collapsed) {
  if (collapsed) { collapsedFiles.add(path); } else { collapsedFiles.delete(path); }
  (reviewedNodes.get(path) || []).forEach(({ wrap, fold }) =>
    applyCollapsed(wrap, fold, collapsed));
  saveDraft();
}

function flash(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 1600);
}

document.getElementById("copy-btn").addEventListener("click", () => {
  const pr = (DATA && DATA.pr) || {};
  const comments = [];
  lineComments.forEach(c => {
    const one = { path: c.path, line: c.line, side: c.side, body: c.body };
    // Multi-line comments carry GitHub's start_line/start_side alongside the anchor line.
    if (c.startLine != null && c.startLine !== c.line) {
      one.start_line = c.startLine; one.start_side = c.side;
    }
    comments.push(one);
  });
  const fileLevel = [];
  fileComments.forEach(c => fileLevel.push({ path: c.path, body: c.body }));

  const payload = {
    pr: pr.number,
    headSha: pr.headSha,
    summary: document.getElementById("summary").value.trim(),
    comments,
    fileComments: fileLevel
  };
  if (!comments.length && !fileLevel.length && !payload.summary) {
    flash("Add a comment or a summary first"); return;
  }
  const json = JSON.stringify(payload, null, 2);
  navigator.clipboard.writeText(json).then(
    () => flash("Copied " + (comments.length + fileLevel.length) + " comment(s) — paste into Claude"),
    () => window.prompt("Copy this JSON and paste it into Claude:", json)
  );
});

// Restore before the first render so saved comments are drawn from state like any other.
pruneDrafts();
const restored = loadDraft();
render();
wireControls();
wireDraft();
if (restored) flash("Restored " + restored + " saved comment(s) from your last visit");
