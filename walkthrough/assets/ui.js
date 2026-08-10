/* walkthrough — pagination, highlighting and the hidden bubbles.
 *
 * The bubbles deliberately do NOT remember what you revealed. Position is
 * restored so you can leave and come back, but a second pass should test you
 * again; remembering would trade the whole mechanism for a small convenience.
 */
(function () {
  "use strict";

  var doc = window.WALKTHROUGH;
  if (!doc || !doc.steps || !doc.steps.length) return;

  var app = document.getElementById("app");
  var current = 0;
  var seen = {};
  var storeKey = "walkthrough:" + doc.id;

  var el = {
    title: document.getElementById("doc-title"),
    subtitle: document.getElementById("doc-subtitle"),
    count: document.getElementById("step-count"),
    stepTitle: document.getElementById("step-title"),
    stepNote: document.getElementById("step-note"),
    excerpts: document.getElementById("excerpts"),
    page: document.getElementById("page"),
    prev: document.getElementById("prev"),
    next: document.getElementById("next"),
    dots: document.getElementById("dots"),
    toc: document.getElementById("toc"),
    tocList: document.getElementById("toc-list"),
    tocBtn: document.getElementById("toc-btn"),
    themeBtn: document.getElementById("theme-btn"),
    colophon: document.getElementById("colophon")
  };

  /* Both branches return escaped markup: hljs escapes text as part of its
   * contract, and the fallback round-trips through textContent. Nothing here
   * concatenates raw file content into HTML, which is what makes the innerHTML
   * below safe for source that itself contains angle brackets or <script>. */
  function highlight(code, lang) {
    if (window.hljs && lang && lang !== "plaintext") {
      try {
        return window.hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
      } catch (e) { /* fall through to plain text */ }
    }
    var div = document.createElement("div");
    div.textContent = code;
    return div.innerHTML;
  }

  /* Split highlighted markup into one balanced string per line.
   *
   * Highlighting the block as a whole is necessary — a template literal or a
   * block comment is only recognised in full context. But hljs then emits spans
   * that straddle newlines, so a naive split leaves each row with unbalanced
   * tags. Close every open span at the line break and reopen it on the next
   * line, so each row stands alone. hljs emits only spans, so </span> is always
   * the right closer. */
  function splitLines(html) {
    var lines = [];
    var open = [];
    var buf = "";
    var i = 0;

    function shut() { return new Array(open.length + 1).join("</span>"); }

    while (i < html.length) {
      var ch = html.charAt(i);
      if (ch === "<") {
        var end = html.indexOf(">", i);
        if (end === -1) { buf += html.slice(i); break; }
        var tag = html.slice(i, end + 1);
        if (tag.charAt(1) === "/") open.pop();
        else if (tag.charAt(tag.length - 2) !== "/") open.push(tag);
        buf += tag;
        i = end + 1;
      } else if (ch === "\n") {
        lines.push(buf + shut());
        buf = open.join("");
        i++;
      } else {
        buf += ch;
        i++;
      }
    }
    lines.push(buf + shut());
    return lines;
  }

  function renderExcerpt(ex, index) {
    var wrap = document.createElement("section");
    wrap.className = "excerpt";

    var head = document.createElement("div");
    head.className = "excerpt-head";

    var path = document.createElement("button");
    path.className = "excerpt-path";
    path.type = "button";
    path.textContent = ex.path;
    path.title = "Copy path";
    path.addEventListener("click", function () {
      var value = ex.path;
      if (navigator.clipboard) navigator.clipboard.writeText(value);
      var was = path.textContent;
      path.textContent = "copied";
      setTimeout(function () { path.textContent = was; }, 900);
    });

    var lines = document.createElement("span");
    lines.className = "excerpt-lines";
    lines.textContent = ex.focus[0] === ex.focus[1]
      ? "line " + ex.focus[0]
      : "lines " + ex.focus[0] + "–" + ex.focus[1];

    head.appendChild(path);
    head.appendChild(lines);
    wrap.appendChild(head);

    var whole = splitLines(highlight(ex.lines.join("\n"), ex.lang));

    var code = document.createElement("div");
    code.className = "code";
    ex.lines.forEach(function (_, i) {
      var no = ex.first + i;
      var inFocus = no >= ex.focus[0] && no <= ex.focus[1];
      var row = document.createElement("div");
      row.className = "row " + (inFocus ? "is-focus" : "is-context");

      var gutter = document.createElement("span");
      gutter.className = "ln";
      gutter.textContent = String(no);

      var src = document.createElement("span");
      src.className = "src hljs";
      src.innerHTML = whole[i] === "" ? "&nbsp;" : whole[i];

      row.appendChild(gutter);
      row.appendChild(src);
      code.appendChild(row);
    });
    wrap.appendChild(code);

    var bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = ex.bubble;
    bubble.hidden = true;

    var reveal = document.createElement("button");
    reveal.className = "reveal";
    reveal.type = "button";
    reveal.innerHTML = '<span class="pip">' + (index + 1) + "</span>";
    reveal.appendChild(document.createTextNode("What does this do?"));
    reveal.addEventListener("click", function () {
      bubble.hidden = !bubble.hidden;
      reveal.lastChild.nodeValue = bubble.hidden ? "What does this do?" : "Hide";
    });

    wrap.appendChild(reveal);
    wrap.appendChild(bubble);
    return wrap;
  }

  function draw() {
    var step = doc.steps[current];
    seen[current] = true;

    el.count.textContent = "Step " + (current + 1) + " of " + doc.steps.length;
    el.stepTitle.textContent = step.title;
    el.stepNote.textContent = step.note || "";
    el.stepNote.hidden = !step.note;

    el.excerpts.textContent = "";
    step.excerpts.forEach(function (ex, i) {
      el.excerpts.appendChild(renderExcerpt(ex, i));
    });

    el.prev.disabled = current === 0;
    el.next.disabled = current === doc.steps.length - 1;

    Array.prototype.forEach.call(el.dots.children, function (dot, i) {
      dot.className = "dot" + (seen[i] ? " seen" : "") + (i === current ? " here" : "");
    });
    Array.prototype.forEach.call(el.tocList.children, function (li, i) {
      li.setAttribute("aria-current", i === current ? "true" : "false");
    });

    try { localStorage.setItem(storeKey, String(current)); } catch (e) {}
    el.page.scrollIntoView({ block: "start" });
    el.page.focus({ preventScroll: true });
  }

  function go(index) {
    if (index < 0 || index >= doc.steps.length) return;
    current = index;
    draw();
  }

  // ---- chrome ----

  el.title.textContent = doc.title;
  el.subtitle.textContent = doc.subtitle || "";
  el.subtitle.hidden = !doc.subtitle;
  el.colophon.textContent =
    doc.steps.length + " steps · " +
    (doc.commit ? "at commit " + doc.commit + " · " : "") +
    "generated " + (doc.generated_at || "").slice(0, 10);

  doc.steps.forEach(function (step, i) {
    var dot = document.createElement("button");
    dot.className = "dot";
    dot.type = "button";
    dot.title = step.title;
    dot.addEventListener("click", function () { go(i); });
    el.dots.appendChild(dot);

    var li = document.createElement("li");
    var link = document.createElement("button");
    link.type = "button";
    link.textContent = step.title;
    link.addEventListener("click", function () { go(i); el.toc.hidden = true; });
    li.appendChild(link);
    el.tocList.appendChild(li);
  });

  el.prev.addEventListener("click", function () { go(current - 1); });
  el.next.addEventListener("click", function () { go(current + 1); });
  el.tocBtn.addEventListener("click", function () { el.toc.hidden = !el.toc.hidden; });

  el.themeBtn.addEventListener("click", function () {
    var root = document.documentElement;
    var dark = root.getAttribute("data-theme") === "dark" ||
      (!root.getAttribute("data-theme") &&
        window.matchMedia("(prefers-color-scheme: dark)").matches);
    root.setAttribute("data-theme", dark ? "light" : "dark");
  });

  document.addEventListener("keydown", function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    var typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName);
    if (typing) return;

    if (e.key === "ArrowRight") { go(current + 1); e.preventDefault(); }
    else if (e.key === "ArrowLeft") { go(current - 1); e.preventDefault(); }
    else if (e.key === "t") { el.toc.hidden = !el.toc.hidden; }
    else if (e.key === "Escape") { el.toc.hidden = true; }
    else if (/^[1-9]$/.test(e.key)) {
      var buttons = el.excerpts.querySelectorAll(".reveal");
      var target = buttons[Number(e.key) - 1];
      if (target) target.click();
    }
  });

  try {
    var saved = parseInt(localStorage.getItem(storeKey), 10);
    if (!isNaN(saved) && saved >= 0 && saved < doc.steps.length) current = saved;
  } catch (e) {}

  app.hidden = false;
  draw();
})();
