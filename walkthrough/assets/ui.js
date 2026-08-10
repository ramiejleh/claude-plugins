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
  var reveals = [];
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

  function codeRow(no, markup, inFocus) {
    var row = document.createElement("div");
    row.className = "row " + (inFocus ? "is-focus" : "is-context");

    var gutter = document.createElement("span");
    gutter.className = "ln";
    gutter.textContent = String(no);

    var src = document.createElement("span");
    src.className = "src hljs";
    src.innerHTML = markup === "" ? "&nbsp;" : markup;

    row.appendChild(gutter);
    row.appendChild(src);
    return row;
  }

  /* A run of code, with a reveal placed directly beneath each highlighted
   * region rather than at the bottom of the file. The explanation belongs next
   * to the lines it explains — at the foot of a long view it would be attached
   * to nothing in particular. */
  function renderBlock(block, lang, counter) {
    var frag = document.createDocumentFragment();
    var markup = splitLines(highlight(block.lines.join("\n"), lang));

    block.lines.forEach(function (_, i) {
      var no = block.first + i;
      var region = null;
      for (var r = 0; r < block.regions.length; r++) {
        if (no >= block.regions[r].focus[0] && no <= block.regions[r].focus[1]) {
          region = block.regions[r];
          break;
        }
      }
      frag.appendChild(codeRow(no, markup[i], !!region));

      if (region && no === region.focus[1]) {
        var n = counter.next();
        var bubble = document.createElement("div");
        bubble.className = "bubble";
        bubble.textContent = region.bubble;
        bubble.hidden = true;

        var reveal = document.createElement("button");
        reveal.className = "reveal";
        reveal.type = "button";
        reveal.innerHTML = '<span class="pip">' + n + "</span>";
        reveal.appendChild(document.createTextNode("What does this do?"));
        reveal.addEventListener("click", function () {
          bubble.hidden = !bubble.hidden;
          reveal.lastChild.nodeValue = bubble.hidden ? "What does this do?" : "Hide";
        });

        counter.buttons.push(reveal);
        frag.appendChild(reveal);
        frag.appendChild(bubble);
      }
    });
    return frag;
  }

  /* Skipped lines stay one click away. Hiding them keeps the page readable;
   * removing them would mean the walkthrough decides what you are allowed to
   * see, which is the opposite of the point. */
  function renderGap(block, lang) {
    var bar = document.createElement("button");
    bar.className = "gap";
    bar.type = "button";
    bar.textContent = "⋯ " + block.count + " line" + (block.count === 1 ? "" : "s") + " hidden";
    bar.addEventListener("click", function () {
      var markup = splitLines(highlight(block.lines.join("\n"), lang));
      var frag = document.createDocumentFragment();
      block.lines.forEach(function (_, i) {
        frag.appendChild(codeRow(block.first + i, markup[i], false));
      });
      bar.parentNode.replaceChild(frag, bar);
    });
    return bar;
  }

  function renderFile(file, counter) {
    var wrap = document.createElement("section");
    wrap.className = "excerpt";

    var head = document.createElement("div");
    head.className = "excerpt-head";

    var path = document.createElement("button");
    path.className = "excerpt-path";
    path.type = "button";
    path.textContent = file.path;
    path.title = "Copy path";
    path.addEventListener("click", function () {
      if (navigator.clipboard) navigator.clipboard.writeText(file.path);
      var was = path.textContent;
      path.textContent = "copied";
      setTimeout(function () { path.textContent = was; }, 900);
    });

    var lines = document.createElement("span");
    lines.className = "excerpt-lines";
    lines.textContent = "lines " + file.span[0] + "–" + file.span[1];

    head.appendChild(path);
    head.appendChild(lines);
    wrap.appendChild(head);

    var code = document.createElement("div");
    code.className = "code";
    file.blocks.forEach(function (block) {
      code.appendChild(
        block.kind === "gap" ? renderGap(block, file.lang) : renderBlock(block, file.lang, counter)
      );
    });
    wrap.appendChild(code);
    return wrap;
  }

  function draw() {
    var step = doc.steps[current];
    seen[current] = true;

    el.count.textContent = "Step " + (current + 1) + " of " + doc.steps.length;
    el.stepTitle.textContent = step.title;
    el.stepNote.textContent = step.note || "";
    el.stepNote.hidden = !step.note;

    // Reveals are numbered across the whole step so the 1–9 shortcuts line up
    // with what the reader sees top to bottom, regardless of file boundaries.
    var counter = { n: 0, buttons: [], next: function () { return ++this.n; } };
    el.excerpts.textContent = "";
    step.files.forEach(function (file) {
      el.excerpts.appendChild(renderFile(file, counter));
    });
    reveals = counter.buttons;

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
      var target = reveals[Number(e.key) - 1];
      if (target) { target.click(); target.scrollIntoView({ block: "nearest" }); }
    }
  });

  try {
    var saved = parseInt(localStorage.getItem(storeKey), 10);
    if (!isNaN(saved) && saved >= 0 && saved < doc.steps.length) current = saved;
  } catch (e) {}

  app.hidden = false;
  draw();
})();
