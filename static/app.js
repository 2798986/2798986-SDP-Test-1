/* RAT frontend - single-page dashboard.
   One state object drives every view; each view is rebuilt from API
   responses. Charts are ECharts instances keyed by name and disposed
   whenever the content area is re-rendered. No framework, no build step. */
(function () {
  "use strict";

  /* ------------------------------------------------------------ helpers */
  var $ = function (sel, root) { return (root || document).querySelector(sel); };

  function el(tag, props, kids) {
    var node = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (k) {
        var v = props[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class") node.className = v;
        else if (k === "text") node.textContent = v;
        else if (k === "html") node.innerHTML = v;
        else if (k === "dataset") Object.keys(v).forEach(function (d) { node.dataset[d] = v[d]; });
        else if (k.indexOf("on") === 0 && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
        else if (k === "value") node.value = v;
        else node.setAttribute(k, v === true ? "" : v);
      });
    }
    if (kids) {
      (Array.isArray(kids) ? kids : [kids]).forEach(function (c) {
        if (c === null || c === undefined || c === false) return;
        node.appendChild(typeof c === "string" || typeof c === "number"
          ? document.createTextNode(String(c)) : c);
      });
    }
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var NF = new Intl.NumberFormat("en-US");
  var fmtInt = function (n) { return NF.format(n || 0); };
  var plural = function (n, word) { return fmtInt(n) + " " + word + (n === 1 ? "" : "s"); };
  var fmtRatio = function (x) { return (x || 0).toFixed(2); };
  var fmtPct = function (x, dp) { return ((x || 0) * 100).toFixed(dp === undefined ? 1 : dp) + "%"; };
  var signed = function (v) { return (v > 0 ? "+" : "") + fmtInt(v); };
  var clsSign = function (v) { return v > 0 ? "pos" : (v < 0 ? "neg" : ""); };
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function fmtDay(ts) {
    var d = new Date(ts * 1000);
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }
  function debounce(fn, ms) {
    var t = null;
    return function () {
      var args = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  }
  function shortHash(h) { return String(h || "").slice(0, 10); }

  /* ------------------------------------------------------------- icons */
  var ICONS = {
    caret: '<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M1.5 3.5L5 7l3.5-3.5" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>',
    dir: '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M1.5 3.5h4l1.2 1.6h5.8v6h-11z" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>',
    file: '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3.5 1.5h5l2.5 2.5v8.5h-7.5z" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M8.5 1.5v2.5h2.5" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>',
    back: '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M7.5 2l-4 4 4 4" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>',
    search: '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><circle cx="5" cy="5" r="3.4" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M8 8l2.6 2.6" stroke="currentColor" stroke-width="1.3"/></svg>',
    x: '<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M1.5 1.5l7 7M8.5 1.5l-7 7" stroke="currentColor" stroke-width="1.4"/></svg>',
    trash: '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M2 3.5h8M4.5 3.5V2h3v1.5M3.5 3.5l.5 7h4l.5-7" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>'
  };
  function glyph(name, cls) {
    return el("span", { class: cls || "icon", html: ICONS[name], "aria-hidden": "true" });
  }

  /* ------------------------------------------------------------ palette */
  var PAL = {};
  var MONO = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace';
  function readPalette() {
    var cs = getComputedStyle(document.documentElement);
    ["--ink", "--muted", "--hairline", "--hairline-strong", "--surface", "--accent",
      "--ser-1", "--ser-2", "--ser-3", "--ser-4", "--ser-5", "--ser-6"].forEach(function (n) {
      PAL[n] = cs.getPropertyValue(n).trim();
    });
  }
  function serColors() {
    return [PAL["--ser-1"], PAL["--ser-2"], PAL["--ser-3"], PAL["--ser-4"], PAL["--ser-5"], PAL["--ser-6"]];
  }

  /* ---------------------------------------------------------------- api */
  function api(method, path, body) {
    var init = { method: method, headers: {} };
    if (body !== undefined && body !== null) {
      if (body instanceof Blob || body instanceof File) init.body = body;
      else {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(body);
      }
    }
    return fetch(path, init).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var err = new Error((data && data.error) || ("Request failed (" + res.status + ")"));
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  /* -------------------------------------------------------------- state */
  var TABS = [
    { id: "overview", label: "Overview" },
    { id: "files", label: "Files" },
    { id: "authors", label: "Authors" },
    { id: "commits", label: "Commits" }
  ];

  var state = {
    repos: [],
    repoId: null,
    repo: null,
    tab: "overview",
    filters: { from: null, to: null, hashes: null, author: null },
    filesPath: "",
    filePath: null,
    view: {},             // repoId -> remembered {tab, filters, filesPath, filePath}
    authorsCache: {},     // repoId -> unfiltered authors list
    sort: {},             // tableId -> {key, dir}
    commits: { query: "", offset: 0, limit: 50, total: 0, rows: [], selection: {} },
    charts: {},
    renderSeq: 0,
    lastH: null,
    presetKey: null,
    job: null,
    jobPoll: null,
    openLayer: null,
    booting: true /* first /api/repos fetch in flight - show skeletons, not the welcome flash */
  };

  function filterQS(exceptHashes) {
    var f = state.filters, parts = [];
    if (!exceptHashes && f.hashes) parts.push("hashes=" + encodeURIComponent(f.hashes.join(",")));
    if (f.from !== null) parts.push("from=" + f.from);
    if (f.to !== null) parts.push("to=" + f.to);
    if (f.author !== null) parts.push("author=" + f.author);
    return parts.join("&");
  }
  function repoBase() { return "/api/repos/" + state.repoId; }
  function hasFilters() {
    var f = state.filters;
    return f.from !== null || f.to !== null || f.hashes !== null || f.author !== null;
  }
  function authorName(id) {
    var list = state.authorsCache[state.repoId] || [];
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i].name;
    return "author " + id;
  }

  /* ------------------------------------------------------------- toasts */
  function toast(message, kind) {
    var host = $("#toastHost");
    var t = el("div", { class: "toast" + (kind === "error" ? " toast-error" : ""), text: message, role: "status" });
    host.appendChild(t);
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 4600);
  }

  /* ------------------------------------------------------- layer manager */
  function openLayer(node, opts) {
    closeLayer();
    var layer = { node: node };
    opts = opts || {};
    if (!opts.plain) {
      var backdrop = el("div", { class: "backdrop" });
      backdrop.addEventListener("click", function () { closeLayer(); });
      layer.backdrop = backdrop;
      document.body.appendChild(backdrop);
    }
    if (opts.anchor) opts.anchor.appendChild(node);
    else document.body.appendChild(node);
    state.openLayer = layer;
    if (opts.onClose) layer.onClose = opts.onClose;
    var focusTarget = opts.focus ? $(opts.focus, node) : null;
    if (focusTarget) setTimeout(function () { focusTarget.focus(); }, 30);
    return layer;
  }

  function closeLayer() {
    var layer = state.openLayer;
    if (!layer) return;
    state.openLayer = null;
    if (layer.backdrop && layer.backdrop.parentNode) layer.backdrop.parentNode.removeChild(layer.backdrop);
    if (layer.node && layer.node.parentNode) layer.node.parentNode.removeChild(layer.node);
    if (layer.onClose) layer.onClose();
  }

  function drawer(title, sub) {
    var head = el("div", { class: "drawer-head" }, [
      el("div", {}, [
        el("div", { class: "drawer-title", text: title }),
        sub ? el("div", { class: "drawer-sub", text: sub }) : null
      ]),
      el("button", { class: "icon-btn", type: "button", title: "Close", "aria-label": "Close", onClick: closeLayer, html: ICONS.x })
    ]);
    var body = el("div", { class: "drawer-body" });
    var foot = el("div", { class: "drawer-foot" });
    var node = el("div", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-label": title }, [head, body, foot]);
    return { node: node, body: body, foot: foot };
  }

  /* ------------------------------------------------------- job progress */
  function setJobChip(job) {
    var area = $("#jobArea");
    clear(area);
    if (!job) return;
    if (job.status === "running") {
      area.appendChild(el("div", { class: "job-chip" }, [
        el("span", { class: "spinner" }),
        el("span", { class: "job-text", text: job.message || "Working…" }),
        el("span", { class: "job-track" },
          el("span", { class: "job-fill", style: "width:" + Math.round((job.progress || 0) * 100) + "%" }))
      ]));
    } else if (job.status === "error") {
      area.appendChild(el("div", { class: "job-chip error" }, [
        glyph("x"),
        el("span", { class: "job-text", text: job.message || "Ingestion failed" })
      ]));
    }
  }

  function updateRunningView(job) {
    var pct = Math.round((job.progress || 0) * 100);
    var fill = $("#content .track-lg .job-fill");
    if (fill) fill.style.width = pct + "%";
    var note = $("#content .running-note");
    if (note) note.textContent = pct + "% — " + (job.message || "Working…");
  }

  function stopJobPolling() {
    if (state.jobPoll) { clearInterval(state.jobPoll); state.jobPoll = null; }
  }

  function pollJob(repoId, jobId) {
    stopJobPolling();
    var tick = function () {
      api("GET", "/api/jobs/" + jobId).then(function (job) {
        state.job = job;
        setJobChip(job);
        updateRunningView(job);
        if (job.status === "done") {
          stopJobPolling();
          toast(job.message || "Ingestion complete");
          setTimeout(function () { setJobChip(null); }, 2600);
          refreshRepos().then(function () {
            if (state.repoId === repoId) loadTab();
          });
        } else if (job.status === "error") {
          stopJobPolling();
          toast(job.message || "Ingestion failed", "error");
          refreshRepos().then(function () {
            if (state.repoId === repoId) loadTab();
          });
        }
      }).catch(function (err) {
        stopJobPolling();
        setJobChip(null);
        toast(err.message, "error");
      });
    };
    tick();
    state.jobPoll = setInterval(tick, 1000);
  }

  function resumeRunningRepo() {
    var repo = state.repo;
    if (!repo || repo.status !== "running") { stopJobPolling(); setJobChip(null); state.job = null; return; }
    api("GET", repoBase() + "/job").then(function (job) {
      if (job && job.status === "running") { state.job = job; pollJob(repo.id, job.id); }
    }).catch(function () {});
  }

  /* ------------------------------------------------------ repo selector */
  function renderSelector() {
    var host = $("#repoSelector");
    clear(host);
    var current = null;
    state.repos.forEach(function (r) { if (r.id === state.repoId) current = r; });
    var btn = el("button", {
      class: "repo-btn", type: "button", "aria-haspopup": "true",
      "aria-expanded": state.openLayer ? "true" : "false",
      title: current ? current.name : "No repository selected"
    }, [
      el("span", { class: "status-dot " + (current ? current.status : "") }),
      el("span", { class: "repo-name", text: current ? current.name : "No repository" }),
      glyph("caret", "caret")
    ]);
    btn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      if (state.openLayer) { closeLayer(); return; }
      openRepoMenu(btn);
    });
    host.appendChild(btn);
  }

  function openRepoMenu(btn) {
    var items = [];
    if (!state.repos.length) {
      items.push(el("div", { class: "menu-empty", text: "No repositories yet - load the sample to begin." }));
    }
    state.repos.forEach(function (r) {
      var meta = r.status === "ready"
        ? plural(r.commits, "commit") + " · " + plural(r.files, "file") + " · " + plural(r.authors, "author")
        : (r.status === "running" ? "ingesting…" : (r.status === "error" ? "failed" : r.status));
      var del = el("span", {
        class: "icon-btn", role: "button", "aria-label": "Delete " + r.name, title: "Delete repository",
        html: ICONS.trash
      });
      del.addEventListener("click", function (ev) { ev.stopPropagation(); armDelete(item, r); });
      var item = el("div", {
        class: "repo-item", role: "menuitem", tabindex: "0",
        "aria-current": r.id === state.repoId ? "true" : "false"
      }, [
        el("span", { class: "status-dot " + r.status }),
        el("span", { class: "repo-label" }, [el("strong", { text: r.name }), el("span", { text: meta })]),
        del
      ]);
      item.addEventListener("click", function () { closeLayer(); selectRepo(r.id); });
      item.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") { closeLayer(); selectRepo(r.id); }
      });
      items.push(item);
    });
    var menu = el("div", { class: "repo-menu", role: "menu" },
      [el("div", { class: "menu-head", text: "Repositories" })].concat(items));
    menu.addEventListener("click", function (ev) { ev.stopPropagation(); });
    openLayer(menu, { anchor: btn.parentNode, plain: true });
  }

  function armDelete(item, repo) {
    clear(item);
    item.appendChild(el("span", { class: "repo-label" }, [
      el("strong", { text: "Delete " + repo.name + "?" }),
      el("span", { text: "Removes the repository and all ingested metrics." })
    ]));
    var del = el("button", { class: "btn btn-sm", type: "button", text: "Delete" });
    del.style.borderColor = "var(--danger)";
    del.style.color = "var(--danger)";
    del.addEventListener("click", function (ev) { ev.stopPropagation(); doDelete(repo); });
    var cancel = el("button", { class: "btn btn-sm btn-quiet", type: "button", text: "Cancel" });
    cancel.addEventListener("click", function (ev) { ev.stopPropagation(); closeLayer(); });
    item.appendChild(del);
    item.appendChild(cancel);
  }

  function doDelete(repo) {
    api("DELETE", "/api/repos/" + repo.id).then(function () {
      toast("Deleted \"" + repo.name + "\"");
      delete state.authorsCache[repo.id];
      delete state.view[repo.id];
      if (state.repoId === repo.id) {
        state.repoId = null;
        state.repo = null;
        state.lastH = null;
      }
      closeLayer();
      refreshRepos();
    }).catch(function (err) { toast(err.message, "error"); });
  }

  /* -------------------------------------------------- repo selection */
  function saveView() {
    if (state.repoId === null) return;
    state.view[state.repoId] = {
      tab: state.tab,
      filters: {
        from: state.filters.from, to: state.filters.to,
        hashes: state.filters.hashes ? state.filters.hashes.slice() : null,
        author: state.filters.author
      },
      filesPath: state.filesPath,
      filePath: state.filePath
    };
  }

  function loadView(id) {
    var v = state.view[id];
    state.filters = v
      ? {
        from: v.filters.from, to: v.filters.to,
        hashes: v.filters.hashes ? v.filters.hashes.slice() : null,
        author: v.filters.author
      }
      : { from: null, to: null, hashes: null, author: null };
    state.filesPath = v ? v.filesPath : "";
    state.filePath = v ? v.filePath : null;
    if (v && v.tab) state.tab = v.tab;
    else state.tab = "overview";
    if (location.hash !== "#" + state.tab) history.replaceState(null, "", "#" + state.tab);
  }

  function ensureAuthors() {
    if (state.authorsCache[state.repoId]) return Promise.resolve(state.authorsCache[state.repoId]);
    return api("GET", repoBase() + "/authors").then(function (list) {
      state.authorsCache[state.repoId] = list;
      return list;
    }).catch(function () { return []; });
  }

  function selectRepo(id) {
    if (state.repoId === id) { loadTab(); return; }
    saveView();
    state.repoId = id;
    state.repo = null;
    state.repos.forEach(function (r) { if (r.id === id) state.repo = r; });
    if (!state.repo) refreshRepos(); /* repos list stale - self-heal the selector */
    loadView(id);
    state.commits = { query: "", offset: 0, limit: 50, total: 0, rows: [], selection: {} };
    state.presetKey = null;
    state.lastH = null;
    closeLayer();
    renderSelector();
    renderTabs();
    ensureAuthors().then(function () {
      renderRail();
      renderScope();
      resumeRunningRepo();
      loadTab();
    });
  }

  function refreshRepos() {
    return api("GET", "/api/repos").then(function (repos) {
      state.booting = false;
      state.repos = repos;
      var stillThere = false;
      repos.forEach(function (r) { if (r.id === state.repoId) stillThere = true; });
      if (!stillThere) { state.repoId = null; state.repo = null; }
      else state.repos.forEach(function (r) { if (r.id === state.repoId) state.repo = r; });
      renderSelector();
      if (!state.repoId && repos.length) {
        var pick = null;
        repos.forEach(function (r) { if (!pick && r.status === "ready") pick = r; });
        pick = pick || repos[0];
        selectRepo(pick.id);
      } else if (!state.repoId) {
        renderRail();
        renderScope();
        loadTab();
      }
      return repos;
    });
  }

  /* --------------------------------------------------- add repo / sample */
  function openAddPopover() {
    var urlInput = el("input", {
      class: "input mono", type: "url", placeholder: "https://github.com/user/repo.git",
      "aria-label": "Repository clone URL"
    });
    var errBox = el("div", { class: "form-error", style: "display:none" });
    var cloneBtn = el("button", { class: "btn btn-accent", type: "button", text: "Clone repository" });
    cloneBtn.style.width = "100%";
    cloneBtn.style.justifyContent = "center";
    var fileInput = el("input", {
      type: "file", accept: ".zip", class: "input", "aria-label": "Repository zip archive",
      style: "padding:4px 8px;height:auto"
    });
    var fileNote = el("div", { class: "form-hint", text: "The zip must contain the repository's .git directory." });
    var uploadBtn = el("button", { class: "btn", type: "button", text: "Upload zip" });
    uploadBtn.style.width = "100%";
    uploadBtn.style.justifyContent = "center";
    var sampleBtn = el("button", { class: "btn btn-quiet", type: "button", text: "Load sample repository" });
    sampleBtn.style.width = "100%";
    sampleBtn.style.justifyContent = "center";

    function showErr(msg) {
      errBox.textContent = msg;
      errBox.style.display = msg ? "" : "none";
    }
    function busy(btn, on, label) {
      btn.disabled = on;
      btn.textContent = on ? "Working…" : label;
    }

    cloneBtn.addEventListener("click", function () {
      var url = urlInput.value.trim();
      if (!url) { showErr("Enter a public clone URL (http or https)."); return; }
      showErr("");
      busy(cloneBtn, true, "Clone repository");
      api("POST", "/api/repos", { url: url }).then(function (res) {
        closeLayer();
        toast("Cloning " + url + "…");
        refreshRepos().then(function () {
          if (res.repo_id) selectRepo(res.repo_id);
          pollJob(res.repo_id, res.job_id);
        });
      }).catch(function (err) {
        showErr(err.message);
        busy(cloneBtn, false, "Clone repository");
      });
    });

    fileInput.addEventListener("change", function () {
      var f = fileInput.files[0];
      fileNote.textContent = f
        ? f.name + " · " + fmtInt(Math.max(1, Math.round(f.size / 1024))) + " KB"
        : "The zip must contain the repository's .git directory.";
    });

    uploadBtn.addEventListener("click", function () {
      var f = fileInput.files[0];
      if (!f) { showErr("Choose a .zip archive first."); return; }
      showErr("");
      busy(uploadBtn, true, "Upload zip");
      api("POST", "/api/repos/upload?name=" + encodeURIComponent(f.name), f).then(function (res) {
        closeLayer();
        toast("Uploading " + f.name + "…");
        refreshRepos().then(function () {
          if (res.repo_id) selectRepo(res.repo_id);
          pollJob(res.repo_id, res.job_id);
        });
      }).catch(function (err) {
        showErr(err.message);
        busy(uploadBtn, false, "Upload zip");
      });
    });

    sampleBtn.addEventListener("click", function (ev) { ev.stopPropagation(); closeLayer(); loadSample(); });

    var pop = el("div", { class: "popover", role: "dialog", "aria-label": "Add repository" }, [
      el("div", { class: "popover-title", text: "Add repository" }),
      el("div", { class: "form-field" }, [
        el("label", { class: "form-label", text: "Clone from URL" }),
        urlInput,
        el("div", { class: "form-hint", text: "Full clone - the entire history is read locally." }),
        errBox,
        cloneBtn
      ]),
      el("div", { class: "popover-sep", text: "or" }),
      el("div", { class: "form-field" }, [
        el("label", { class: "form-label", text: "Upload a zip" }),
        fileInput,
        fileNote,
        uploadBtn
      ]),
      el("div", { class: "popover-sep", text: "or" }),
      sampleBtn
    ]);
    pop.addEventListener("click", function (ev) { ev.stopPropagation(); });
    openLayer(pop, { anchor: $("#headerActions"), plain: true, focus: "input" });
  }

  function loadSample() {
    api("POST", "/api/repos/sample").then(function (res) {
      if (res.existing) {
        toast("Sample repository already loaded");
        refreshRepos().then(function () {
          if (state.repoId !== res.repo_id) selectRepo(res.repo_id);
          else loadTab();
        });
        return;
      }
      toast("Loading sample repository…");
      refreshRepos().then(function () {
        selectRepo(res.repo_id);
        pollJob(res.repo_id, res.job_id);
      });
    }).catch(function (err) { toast(err.message, "error"); });
  }

  /* ------------------------------------------------------- filter rail */
  function section(idx, label, children) {
    var head = el("div", { class: "rail-label" }, [
      el("span", { class: "idx", text: idx }),
      document.createTextNode(label)
    ]);
    return el("div", { class: "rail-section" }, [head].concat(children.filter(function (c) { return c; })));
  }

  function checkRow(label, input, onChange) {
    input.addEventListener("change", function () { onChange(input.checked); });
    return el("label", { class: "check-row" }, [
      input,
      el("span", { class: "check-name", text: label })
    ]);
  }

  function radioRow(label, name, checked, onSelect, count) {
    var input = el("input", { type: "radio", name: name, "aria-label": label });
    input.checked = !!checked;
    input.addEventListener("change", function () { if (input.checked) onSelect(); });
    return el("label", { class: "check-row" }, [
      input,
      el("span", { class: "check-name", text: label }),
      count ? el("span", { class: "check-count", text: count }) : null
    ]);
  }

  function filterChip(kind, text, onRemove) {
    var x = el("button", { class: "chip-x", type: "button", "aria-label": "Remove " + kind + " filter", html: ICONS.x });
    x.addEventListener("click", function (ev) { ev.stopPropagation(); onRemove(); applyFilters(); });
    return el("span", { class: "filter-chip" }, [el("em", { text: kind }), document.createTextNode(text), x]);
  }

  function renderRail() {
    var rail = $("#filterRail");
    clear(rail);
    if (!state.repoId) {
      rail.appendChild(section("00", "Filters", [el("div", { class: "rail-hint", text: "Select or add a repository to filter its history." })]));
      return;
    }
    var f = state.filters;

    /* 01 - date range */
    var presets = el("div", { class: "rail-presets" });
    [["All time", 0], ["30 days", 30], ["90 days", 90], ["1 year", 365]].forEach(function (p) {
      var label = p[0], days = p[1];
      var active = (days === 0 && f.from === null && f.to === null) || state.presetKey === label;
      var chip = el("button", { class: "chip", type: "button", "aria-pressed": active ? "true" : "false", text: label });
      chip.addEventListener("click", function () {
        state.presetKey = label;
        if (days === 0) { f.from = null; f.to = null; }
        else {
          f.from = Math.floor(Date.now() / 1000) - days * 86400;
          f.to = null;
        }
        applyFilters();
      });
      presets.appendChild(chip);
    });

    var fromInput = el("input", {
      class: "input", type: "date", "aria-label": "From date",
      value: f.from !== null ? fmtDay(f.from) : ""
    });
    var toInput = el("input", {
      class: "input", type: "date", "aria-label": "To date, inclusive",
      value: f.to !== null ? fmtDay(f.to - 1) : ""
    });
    fromInput.addEventListener("change", function () {
      state.presetKey = null;
      f.from = fromInput.value ? Math.floor(new Date(fromInput.value + "T00:00:00").getTime() / 1000) : null;
      if (f.from !== null && f.to !== null && f.from >= f.to) {
        toast("From date must be earlier than To date", "error");
        renderRail();
        return;
      }
      applyFilters();
    });
    toInput.addEventListener("change", function () {
      state.presetKey = null;
      if (toInput.value) {
        var d = new Date(toInput.value + "T00:00:00");
        f.to = Math.floor(new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1).getTime() / 1000);
      } else f.to = null;
      if (f.from !== null && f.to !== null && f.from >= f.to) {
        toast("To date must be later than From date", "error");
        renderRail();
        return;
      }
      applyFilters();
    });

    var dateRow = el("div", { class: "date-row" }, [
      el("label", { class: "date-field" }, [el("span", { text: "From" }), fromInput]),
      el("label", { class: "date-field" }, [el("span", { text: "To" }), toInput])
    ]);

    /* 02 - authors */
    var authorList = el("div", { class: "rail-list" });
    authorList.appendChild(radioRow("All authors", "authorFilter", f.author === null, function () {
      f.author = null;
      applyFilters();
    }));
    (state.authorsCache[state.repoId] || []).forEach(function (a) {
      authorList.appendChild(radioRow(a.name, "authorFilter", f.author === a.id, function () {
        f.author = a.id;
        applyFilters();
      }, fmtInt(a.commits)));
    });

    /* 03 - commit set */
    var pick = el("button", { class: "btn wide-btn", type: "button" }, [
      el("span", { text: "Pick commits…" }),
      el("span", { class: "count", text: f.hashes ? fmtInt(f.hashes.length) + " pinned" : "" })
    ]);
    pick.addEventListener("click", function (ev) { ev.stopPropagation(); openPickerDrawer(); });

    /* 04 - active filter chips */
    var anyActive = hasFilters();
    var chips = el("div", { class: "filter-chips" });
    if (f.from !== null || f.to !== null) {
      chips.appendChild(filterChip("date",
        (f.from !== null ? fmtDay(f.from) : "…") + " → " + (f.to !== null ? fmtDay(f.to - 1) : "…"),
        function () { f.from = null; f.to = null; state.presetKey = null; }));
    }
    if (f.author !== null) {
      chips.appendChild(filterChip("author", authorName(f.author), function () { f.author = null; }));
    }
    if (f.hashes !== null) {
      chips.appendChild(filterChip("commits", fmtInt(f.hashes.length) + " pinned", function () { f.hashes = null; }));
    }
    var clearAll = el("button", { class: "btn btn-sm btn-quiet", type: "button", text: "Clear all filters" });
    clearAll.addEventListener("click", clearFilters);

    rail.appendChild(section("01", "Date range", [
      presets,
      dateRow,
      el("div", { class: "rail-hint", text: "To-date is inclusive; the last day is fully included." })
    ]));
    rail.appendChild(section("02", "Authors", [authorList]));
    rail.appendChild(section("03", "Commit set", [
      pick,
      el("div", { class: "rail-hint", text: "Pins an explicit list of commits as the filter (max 900). Combines with the other filters." })
    ]));
    rail.appendChild(section("04", "Active filters", [
      anyActive ? chips : el("div", { class: "rail-hint", text: "None - showing the full history." }),
      anyActive ? clearAll : null
    ]));
  }

  function applyFilters() {
    saveView();
    renderRail();
    renderScope();
    loadTab();
  }

  function clearFilters() {
    state.filters = { from: null, to: null, hashes: null, author: null };
    state.presetKey = null;
    applyFilters();
    toast("Filters cleared");
  }

  /* ------------------------------------------------------------- scope */
  function renderScope(hSize) {
    if (hSize !== undefined) state.lastH = hSize;
    var scope = $("#scopeLine");
    if (!scope) return;
    var parts = [];
    if (state.repo) parts.push(state.repo.name);
    if (state.lastH !== null && state.lastH !== undefined) {
      parts.push("H: " + fmtInt(state.lastH) + (state.lastH === 1 ? " commit" : " commits"));
    }
    var f = state.filters;
    if (f.from !== null || f.to !== null) {
      parts.push((f.from !== null ? fmtDay(f.from) : "start") + " → " + (f.to !== null ? fmtDay(f.to - 1) : "now"));
    }
    if (f.author !== null) parts.push("author: " + authorName(f.author));
    if (f.hashes !== null) parts.push(fmtInt(f.hashes.length) + " pinned");
    scope.textContent = parts.join("  ·  ");
  }

  /* -------------------------------------------------------------- tabs */
  function renderTabs() {
    var host = $("#tabs");
    clear(host);
    TABS.forEach(function (t) {
      var b = el("button", {
        class: "tab", type: "button", role: "tab",
        "aria-selected": state.tab === t.id ? "true" : "false",
        text: t.label
      });
      b.addEventListener("click", function () { setTab(t.id); });
      host.appendChild(b);
    });
  }

  function setTab(id) {
    if (state.tab === id) return;
    state.tab = id;
    if (location.hash !== "#" + id) history.replaceState(null, "", "#" + id);
    renderTabs();
    loadTab();
  }

  /* ------------------------------------------------------ tab dispatch */
  function loadTab() {
    var seq = ++state.renderSeq;
    disposeCharts();
    var host = $("#content");
    clear(host);
    var banner = $("#bannerHost");
    clear(banner);
    if (!state.repoId) {
      if (state.booting) { host.appendChild(skeletonView()); return; }
      renderWelcome(); return;
    }
    if (state.repo && state.repo.status === "running") { renderRunning(host); return; }
    if (state.repo && state.repo.status === "error") { showRepoBanner(); renderRepoFailed(host); return; }
    host.appendChild(skeletonView());
    if (state.tab === "overview") loadOverview(seq);
    else if (state.tab === "files") loadFiles(seq);
    else if (state.tab === "authors") loadAuthors(seq);
    else loadCommits(seq);
  }

  function skeletonView() {
    var frag = el("div");
    var tilesEl = el("div", { class: "tiles" });
    for (var i = 0; i < 5; i++) {
      tilesEl.appendChild(el("div", { class: "tile" }, [
        el("div", { class: "skeleton", style: "width:62%;height:11px;margin-bottom:12px" }),
        el("div", { class: "skeleton", style: "width:44%;height:24px" })
      ]));
    }
    frag.appendChild(tilesEl);
    for (var j = 0; j < 2; j++) {
      frag.appendChild(el("div", { class: "panel", style: "margin-top:12px" }, [
        el("div", { class: "skeleton", style: "width:180px;height:13px;margin-bottom:16px" }),
        el("div", { class: "skeleton", style: "width:100%;height:190px" })
      ]));
    }
    return frag;
  }

  function emptyForScope(message) {
    var box = el("div", { class: "empty-state" }, [
      el("h2", { text: "Nothing in scope" }),
      el("p", { text: message || "No commits match the current filters." })
    ]);
    if (hasFilters()) {
      var actions = el("div", { class: "empty-actions" });
      var btn = el("button", { class: "btn", type: "button", text: "Clear all filters" });
      btn.addEventListener("click", clearFilters);
      actions.appendChild(btn);
      box.appendChild(actions);
    }
    return box;
  }

  function renderWelcome() {
    var host = $("#content");
    clear(host);
    var mark = el("div", { class: "empty-mark", html: '<svg width="44" height="44" viewBox="0 0 22 22"><rect x="0.5" y="0.5" width="21" height="21" fill="none" stroke="currentColor"/><line x1="11" y1="3" x2="11" y2="8" stroke="currentColor"/><line x1="11" y1="14" x2="11" y2="19" stroke="currentColor"/><line x1="3" y1="11" x2="8" y2="11" stroke="currentColor"/><line x1="14" y1="11" x2="19" y2="11" stroke="currentColor"/><circle cx="11" cy="11" r="2" fill="none" stroke="currentColor"/></svg>' });
    var actions = el("div", { class: "empty-actions" });
    var b1 = el("button", { class: "btn btn-accent", type: "button", text: "Load sample repository" });
    b1.addEventListener("click", loadSample);
    var b2 = el("button", { class: "btn", type: "button", text: "Add repository" });
    b2.addEventListener("click", function (ev) { ev.stopPropagation(); openAddPopover(); });
    actions.appendChild(b1);
    actions.appendChild(b2);
    host.appendChild(el("div", { class: "empty-state" }, [
      mark,
      el("h2", { text: "No repositories yet" }),
      el("p", { text: "Load the bundled sample repository to explore RAT offline, or add one by clone URL or zip upload. Everything is read locally and stays on this machine." }),
      actions,
      el("div", { class: "empty-note", text: "demo/fixture.zip · 9 commits · 2 authors · fully offline" })
    ]));
  }

  function renderRunning(host) {
    var job = state.job;
    var pct = job ? Math.round((job.progress || 0) * 100) : 0;
    host.appendChild(el("div", { class: "empty-state" }, [
      el("span", { class: "spinner" }),
      el("h2", { text: "Ingesting " + (state.repo ? state.repo.name : "repository") + "…" }),
      el("p", { text: job ? (job.message || "Working…") : "Waiting for the ingestion job to start…" }),
      el("div", { class: "track-lg" }, el("span", { class: "job-fill", style: "width:" + pct + "%" })),
      el("div", { class: "empty-note running-note", text: pct + "% — " + (job ? (job.message || "Working…") : "queued") })
    ]));
  }

  function renderRepoFailed(host) {
    host.appendChild(el("div", { class: "empty-state" }, [
      el("h2", { text: "Ingestion failed" }),
      el("p", { text: state.repo.error || "The repository could not be ingested." }),
      el("p", { class: "empty-note", text: "Delete it from the repository menu, or try the clone again from the banner above." })
    ]));
  }

  function showRepoBanner() {
    var host = $("#bannerHost");
    clear(host);
    if (!state.repo || state.repo.status !== "error" || !state.repo.error) return;
    var actions = el("div", { style: "display:flex;gap:8px;align-items:center;flex:none" });
    if (/^https?:/.test(state.repo.source || "")) {
      var retry = el("button", { class: "btn btn-sm", type: "button", text: "Try again" });
      retry.addEventListener("click", function () {
        var url = state.repo.source;
        api("POST", "/api/repos", { url: url }).then(function (res) {
          toast("Cloning again…");
          refreshRepos().then(function () { pollJob(res.repo_id, res.job_id); });
        }).catch(function (err) { toast(err.message, "error"); });
      });
      actions.appendChild(retry);
    }
    var del = el("button", { class: "btn btn-sm btn-quiet", type: "button", text: "Delete repository" });
    del.addEventListener("click", function () { doDelete(state.repo); });
    actions.appendChild(del);
    host.appendChild(el("div", { class: "banner", role: "alert" }, [
      el("div", { class: "banner-text" }, [
        el("div", { class: "banner-title", text: "Ingestion failed" }),
        el("div", { class: "banner-msg", text: state.repo.error })
      ]),
      actions
    ]));
  }

  function renderError(err, retry) {
    var host = $("#content");
    clear(host);
    var actions = el("div", { class: "empty-actions" });
    if (retry) {
      var b = el("button", { class: "btn", type: "button", text: "Retry" });
      b.addEventListener("click", retry);
      actions.appendChild(b);
    }
    host.appendChild(el("div", { class: "empty-state" }, [
      el("h2", { text: "Something went wrong" }),
      el("p", { text: String((err && err.message) || err) }),
      actions
    ]));
  }

  /* ------------------------------------------------------- generic table */
  function dataTable(opts) {
    var tableId = opts.id;
    state.sort[tableId] = state.sort[tableId] || opts.defaultSort || null;
    var host;

    function sortState() { return state.sort[tableId]; }

    function rows() {
      var list = opts.rows.slice();
      var s = sortState();
      if (!s) return list;
      var col = null;
      opts.columns.forEach(function (c) { if (c.key === s.key) col = c; });
      if (!col) return list;
      list.sort(function (a, b) {
        var va = col.value ? col.value(a) : a[s.key];
        var vb = col.value ? col.value(b) : b[s.key];
        if (typeof va === "string" || typeof vb === "string") {
          var r = String(va).localeCompare(String(vb));
          return s.dir === "asc" ? r : -r;
        }
        va = va || 0; vb = vb || 0;
        return s.dir === "asc" ? va - vb : vb - va;
      });
      return list;
    }

    function build() {
      var wrap = el("div", { class: "table-wrap" });
      if (!opts.rows.length) {
        wrap.appendChild(el("div", { class: "empty-inline", text: opts.empty || "Nothing to show." }));
        return wrap;
      }
      var thead = el("thead");
      var trh = el("tr");
      opts.columns.forEach(function (c) {
        var th = el("th", { class: (c.num ? "num " : "") + (c.sortable ? "sortable" : "") });
        th.appendChild(document.createTextNode(c.label));
        var s = sortState();
        if (s && s.key === c.key) {
          th.appendChild(el("span", { class: "sort-ind", text: s.dir === "asc" ? "▲" : "▼" }));
        }
        if (c.sortable) {
          th.addEventListener("click", function () {
            var cur = sortState();
            state.sort[tableId] = (cur && cur.key === c.key)
              ? { key: c.key, dir: cur.dir === "asc" ? "desc" : "asc" }
              : { key: c.key, dir: c.num ? "desc" : "asc" };
            rerender();
          });
        }
        trh.appendChild(th);
      });
      thead.appendChild(trh);
      var tbody = el("tbody");
      rows().forEach(function (row) {
        var tr = el("tr", opts.rowAttrs ? opts.rowAttrs(row) : {});
        opts.columns.forEach(function (c) {
          var td = el("td", { class: c.num ? "num" : "" });
          var content = c.render ? c.render(row) : row[c.key];
          if (content === null || content === undefined) content = "";
          td.appendChild(typeof content === "string" || typeof content === "number"
            ? document.createTextNode(String(content)) : content);
          tr.appendChild(td);
        });
        if (opts.onRowClick) {
          tr.classList.add("click");
          tr.tabIndex = 0;
          tr.addEventListener("click", function (ev) {
            if (ev.target.closest("button,input,a")) return;
            opts.onRowClick(row);
          });
          tr.addEventListener("keydown", function (ev) {
            if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); opts.onRowClick(row); }
          });
        }
        tbody.appendChild(tr);
      });
      wrap.appendChild(el("table", { class: "data" }, [thead, tbody]));
      return wrap;
    }

    function rerender() {
      var fresh = build();
      if (host.parentNode) host.parentNode.replaceChild(fresh, host);
      host = fresh;
    }

    host = build();
    return host;
  }

  /* --------------------------------------------------------------- tiles */
  function tile(label, value, sub, cls) {
    var v = el("div", { class: "tile-value" + (cls ? " " + cls : ""), text: value });
    return el("div", { class: "tile" }, [
      el("div", { class: "tile-label", text: label }),
      v,
      sub ? el("div", { class: "tile-sub", text: sub }) : null
    ]);
  }

  function tiles(s) {
    return el("div", { class: "tiles" }, [
      tile("Commits |H|", fmtInt(s.commits), "selected commits"),
      tile("Files", fmtInt(s.files), "distinct paths in H"),
      tile("Authors", fmtInt(s.authors), "distinct identities"),
      tile("Added", s.added ? "+" + fmtInt(s.added) : "0", "l+ over H"),
      tile("Removed", s.removed ? "−" + fmtInt(s.removed) : "0", "l− over H"),
      tile("Growth", signed(s.growth), "l+ − l−", clsSign(s.growth)),
      tile("Churn", fmtInt(s.churn), "l+ + l−", "accent"),
      tile("Modifications", fmtInt(s.modifications), "commits with λ > 0"),
      tile("Mod freq", fmtRatio(s.frequency), "n_H ÷ |H|"),
      tile("Churn rate", fmtRatio(s.churn_rate), "λ_H ÷ |H|")
    ]);
  }

  function tilesForFile(d) {
    return el("div", { class: "tiles" }, [
      tile("Added", d.added ? "+" + fmtInt(d.added) : "0", "l+ over H"),
      tile("Removed", d.removed ? "−" + fmtInt(d.removed) : "0", "l− over H"),
      tile("Growth", signed(d.growth), "l+ − l−", clsSign(d.growth)),
      tile("Churn", fmtInt(d.churn), "l+ + l−", "accent"),
      tile("Modifications", fmtInt(d.modifications), "commits with λ > 0"),
      tile("Mod freq", fmtRatio(d.frequency), "n_H ÷ |H|"),
      tile("Churn rate", fmtRatio(d.churn_rate), "λ_H ÷ |H|")
    ]);
  }

  function ownBar(frac) {
    var pct = Math.max(0, Math.min(1, frac || 0)) * 100;
    return el("div", { class: "own" }, [
      el("span", { class: "own-track" }, el("span", { class: "own-fill", style: "width:" + pct + "%" })),
      el("span", { class: "own-pct", text: fmtPct(frac, 1) })
    ]);
  }

  /* -------------------------------------------------------------- charts */
  function disposeCharts() {
    Object.keys(state.charts).forEach(function (k) {
      try { state.charts[k].dispose(); } catch (e) { /* already detached */ }
      delete state.charts[k];
    });
  }

  function mountChart(key, domId, option) {
    var node = document.getElementById(domId);
    if (!node) return;
    if (typeof echarts === "undefined") {
      node.appendChild(el("div", { class: "empty-inline", text: "Chart library unavailable." }));
      return;
    }
    var chart = echarts.init(node, null, { renderer: "canvas" });
    chart.setOption(option);
    state.charts[key] = chart;
  }

  function tooltipBase() {
    return {
      backgroundColor: "#16181C", borderWidth: 0, padding: [7, 10], confine: true,
      textStyle: { color: "#F6F5F2", fontSize: 11, fontFamily: MONO }
    };
  }

  function legendItems(items) {
    return el("div", { class: "chart-legend" }, items.map(function (it) {
      return el("span", { class: "legend-item" }, [
        el("span", { class: "swatch", style: "background:" + it.color }),
        document.createTextNode(it.label),
        it.value !== undefined ? el("b", { text: it.value }) : null
      ]);
    }));
  }

  function seriesOf(data, name) {
    var found = null;
    data.series.forEach(function (s) { if (s.name === name) found = s; });
    return found ? found.data : [];
  }

  function churnOption(data) {
    var added = seriesOf(data, "Added");
    var removed = seriesOf(data, "Removed");
    var churn = seriesOf(data, "Churn");
    var rotate = data.labels.length > 12 ? 30 : 0;
    return {
      animationDuration: 240,
      grid: { left: 48, right: 16, top: 14, bottom: rotate ? 44 : 30 },
      tooltip: Object.assign({
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: PAL["--hairline-strong"] } },
        formatter: function (ps) {
          if (!ps || !ps.length) return "";
          var i = ps[0].dataIndex;
          var out = '<span style="font-family:' + MONO + '">' + esc(data.labels[i]) + "</span>";
          if (added.length) out += "<br/>Added&nbsp;&nbsp;&nbsp;" + fmtInt(added[i]);
          if (removed.length) out += "<br/>Removed&nbsp;&nbsp;" + fmtInt(removed[i]);
          if (churn.length) out += "<br/>Churn&nbsp;&nbsp;&nbsp;" + fmtInt(churn[i]);
          return out;
        }
      }, tooltipBase()),
      xAxis: {
        type: "category", data: data.labels,
        axisLine: { lineStyle: { color: PAL["--hairline-strong"] } },
        axisTick: { show: false },
        axisLabel: { color: PAL["--muted"], fontSize: 11, fontFamily: MONO, rotate: rotate },
        splitLine: { show: false }
      },
      yAxis: {
        type: "value", axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: PAL["--muted"], fontSize: 11, fontFamily: MONO },
        splitLine: { lineStyle: { color: PAL["--hairline"] } }
      },
      series: [
        { name: "Added", type: "bar", stack: "lines", data: added, itemStyle: { color: PAL["--ser-1"] }, barMaxWidth: 26 },
        { name: "Removed", type: "bar", stack: "lines", data: removed, itemStyle: { color: PAL["--ser-4"] }, barMaxWidth: 26 },
        churn.length ? {
          name: "Churn", type: "line", data: churn, symbol: "circle", symbolSize: 4,
          lineStyle: { color: PAL["--ser-3"], width: 1.4 }, itemStyle: { color: PAL["--ser-3"] }
        } : null
      ].filter(function (s) { return s; })
    };
  }

  function topfilesOption(data) {
    var churn = seriesOf(data, "Churn");
    var added = seriesOf(data, "Added");
    var removed = seriesOf(data, "Removed");
    return {
      animationDuration: 240,
      grid: { left: 4, right: 46, top: 8, bottom: 26, containLabel: true },
      tooltip: Object.assign({
        trigger: "item",
        formatter: function (p) {
          var i = p.dataIndex;
          return '<span style="font-family:' + MONO + '">' + esc(data.labels[i]) + "</span>" +
            "<br/>Churn&nbsp;&nbsp;&nbsp;" + fmtInt(churn[i]) +
            "<br/>Added&nbsp;&nbsp;&nbsp;" + fmtInt(added[i]) +
            "<br/>Removed&nbsp;&nbsp;" + fmtInt(removed[i]);
        }
      }, tooltipBase()),
      xAxis: {
        type: "value", axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: PAL["--muted"], fontSize: 11, fontFamily: MONO },
        splitLine: { lineStyle: { color: PAL["--hairline"] } }
      },
      yAxis: {
        type: "category", inverse: true, data: data.labels,
        axisLine: { lineStyle: { color: PAL["--hairline-strong"] } },
        axisTick: { show: false },
        axisLabel: { color: PAL["--ink"], fontSize: 11, fontFamily: MONO, width: 168, overflow: "truncate" },
        splitLine: { show: false }
      },
      series: [{
        name: "Churn", type: "bar", data: churn, barWidth: 13,
        itemStyle: { color: PAL["--ser-1"] },
        label: {
          show: true, position: "right", color: PAL["--muted"], fontFamily: MONO, fontSize: 11,
          formatter: function (p) { return fmtInt(p.value); }
        }
      }]
    };
  }

  function shareOption(data) {
    var colors = serColors();
    var values = seriesOf(data, "Churn");
    var total = data.total;
    if (total === undefined || total === null) {
      total = values.reduce(function (s, v) { return s + v; }, 0);
    }
    var pie = data.labels.map(function (name, i) {
      return {
        name: name, value: values[i],
        itemStyle: { color: colors[i % colors.length], borderColor: PAL["--surface"], borderWidth: 1 }
      };
    });
    return {
      animationDuration: 240,
      tooltip: Object.assign({
        trigger: "item",
        formatter: function (p) {
          return esc(p.name) + "<br/>Churn&nbsp;&nbsp;" + fmtInt(p.value) + " · " + p.percent + "%";
        }
      }, tooltipBase()),
      title: {
        text: fmtInt(total), subtext: "churn in scope",
        left: "50%", top: "36%", textAlign: "center",
        textStyle: { color: PAL["--ink"], fontSize: 22, fontWeight: 600, fontFamily: MONO },
        subtextStyle: { color: PAL["--muted"], fontSize: 10, fontFamily: MONO }
      },
      series: [{
        type: "pie", radius: ["60%", "84%"], center: ["50%", "44%"],
        label: { show: false }, labelLine: { show: false },
        emphasis: { scaleSize: 2 },
        data: pie
      }]
    };
  }

  /* ------------------------------------------------------------ overview */
  function loadOverview(seq) {
    var qs = filterQS();
    return Promise.all([
      api("GET", repoBase() + "/summary?" + qs),
      api("GET", repoBase() + "/chart?type=churn&" + qs),
      api("GET", repoBase() + "/chart?type=topfiles&" + qs),
      api("GET", repoBase() + "/chart?type=authorshare&" + qs)
    ]).then(function (rs) {
      if (seq !== state.renderSeq) return;
      var host = $("#content");
      clear(host);
      var s = rs[0];
      renderScope(s.commits);
      if (s.commits === 0) {
        host.appendChild(emptyForScope(
          (state.repo && state.repo.commits === 0)
            ? "This repository has no commits yet."
            : "No commits match the current filters. Clear them to see the full history."
        ));
        return;
      }
      host.appendChild(tiles(s));
      host.appendChild(churnPanel(rs[1]));
      host.appendChild(el("div", { class: "panel-grid" }, [topfilesPanel(rs[2]), sharePanel(rs[3])]));
      if (rs[1].labels.length) mountChart("churn", "chartChurn", churnOption(rs[1]));
      if (rs[2].labels.length) mountChart("topfiles", "chartTopfiles", topfilesOption(rs[2]));
      if (rs[3].labels.length) mountChart("share", "chartShare", shareOption(rs[3]));
    }).catch(function (err) {
      if (seq === state.renderSeq) renderError(err, function () { loadTab(); });
    });
  }

  function churnPanel(data) {
    var body = data.labels.length
      ? el("div", { class: "chart", id: "chartChurn" })
      : el("div", { class: "empty-inline", text: "No measured lines in scope." });
    return el("div", { class: "panel" }, [
      el("div", { class: "panel-head" }, [
        el("span", { class: "panel-title", text: "Churn over time" }),
        el("span", { class: "panel-meta", text: fmtInt(data.labels.length) + " " + data.bucket + " buckets" })
      ]),
      body,
      data.labels.length ? legendItems([
        { color: PAL["--ser-1"], label: "added" },
        { color: PAL["--ser-4"], label: "removed" },
        { color: PAL["--ser-3"], label: "churn (line)" }
      ]) : null
    ]);
  }

  function topfilesPanel(data) {
    var body = data.labels.length
      ? el("div", { class: "chart", id: "chartTopfiles" })
      : el("div", { class: "empty-inline", text: "No file churn in scope." });
    return el("div", { class: "panel" }, [
      el("div", { class: "panel-head" }, [
        el("span", { class: "panel-title", text: "Most volatile files" }),
        el("span", { class: "panel-meta", text: "top " + fmtInt(data.labels.length) + " by churn" })
      ]),
      body
    ]);
  }

  function sharePanel(data) {
    var colors = serColors();
    var values = seriesOf(data, "Churn");
    var total = data.total || 0;
    var body = data.labels.length
      ? el("div", { class: "chart chart-sm", id: "chartShare" })
      : el("div", { class: "empty-inline", text: "No author churn in scope." });
    var legend = data.labels.length
      ? legendItems(data.labels.map(function (name, i) {
        return {
          color: colors[i % colors.length], label: name,
          value: fmtInt(values[i]) + (total ? " · " + fmtPct(values[i] / total, 0) : "")
        };
      }))
      : null;
    return el("div", { class: "panel" }, [
      el("div", { class: "panel-head" }, [
        el("span", { class: "panel-title", text: "Author churn share" }),
        el("span", { class: "panel-meta", text: fmtInt(data.labels.length) + " authors" })
      ]),
      body,
      legend
    ]);
  }

  /* ---------------------------------------------------------- files tab */
  function loadFiles(seq) {
    if (state.filePath) return loadFileDetail(seq, state.filePath);
    var qs = filterQS();
    return api("GET", repoBase() + "/tree?path=" + encodeURIComponent(state.filesPath) + (qs ? "&" + qs : ""))
      .then(function (data) {
        if (seq !== state.renderSeq) return;
        renderExplorer(data);
      })
      .catch(function (err) {
        if (seq === state.renderSeq) renderError(err, function () { loadTab(); });
      });
  }

  function crumbs(path, onNavigate) {
    var box = el("div", { class: "crumb" });
    var root = el("button", { type: "button", text: state.repo ? state.repo.name : "root" });
    root.addEventListener("click", function () { onNavigate(""); });
    box.appendChild(root);
    if (path) {
      var segs = path.split("/");
      var acc = "";
      segs.forEach(function (seg, i) {
        acc += (acc ? "/" : "") + seg;
        box.appendChild(el("span", { class: "sep", text: "/" }));
        if (i === segs.length - 1) {
          box.appendChild(el("span", { class: "here", text: seg }));
        } else {
          var target = acc;
          var b = el("button", { type: "button", text: seg });
          b.addEventListener("click", function () { onNavigate(target); });
          box.appendChild(b);
        }
      });
    }
    return box;
  }

  function renderExplorer(data) {
    var host = $("#content");
    clear(host);
    host.appendChild(crumbs(state.filesPath, function (p) {
      state.filesPath = p;
      state.filePath = null;
      loadTab();
    }));
    var dirs = 0, files = 0;
    data.children.forEach(function (c) { if (c.kind === "dir") dirs++; else files++; });
    host.appendChild(el("div", { class: "view-head" }, [
      el("span", { class: "view-title", text: state.filesPath ? state.filesPath : (state.repo ? state.repo.name : "Repository") }),
      el("span", { class: "view-sub", text: plural(dirs, "directory") + " · " + plural(files, "file") })
    ]));
    host.appendChild(dataTable({
      id: "files:" + state.repoId + ":" + state.filesPath,
      columns: [
        {
          key: "name", label: "Name", sortable: true,
          render: function (row) {
            return el("div", { class: "row-title" }, [
              glyph(row.kind === "dir" ? "dir" : "file", "kind-icon"),
              el("span", { class: "name", text: row.name }),
              row.kind === "dir" ? el("span", { class: "check-count", text: "open →" }) : null
            ]);
          }
        },
        { key: "added", label: "Added", num: true, sortable: true, render: function (r) { return r.added ? "+" + fmtInt(r.added) : "0"; } },
        { key: "removed", label: "Removed", num: true, sortable: true, render: function (r) { return r.removed ? "−" + fmtInt(r.removed) : "0"; } },
        { key: "growth", label: "Growth", num: true, sortable: true, render: function (r) { return signed(r.growth); } },
        { key: "churn", label: "Churn", num: true, sortable: true, render: function (r) { return fmtInt(r.churn); } },
        { key: "modifications", label: "Mods", num: true, sortable: true, render: function (r) { return fmtInt(r.modifications); } },
        { key: "frequency", label: "Freq", num: true, sortable: true, render: function (r) { return fmtRatio(r.frequency); } },
        { key: "churn_rate", label: "Churn rate", num: true, sortable: true, render: function (r) { return fmtRatio(r.churn_rate); } }
      ],
      rows: data.children,
      defaultSort: null,
      onRowClick: function (row) {
        if (row.kind === "dir") state.filesPath = row.path;
        else state.filePath = row.path;
        loadTab();
      },
      empty: "This directory is empty in the current scope."
    }));
  }

  function loadFileDetail(seq, path) {
    var qs = filterQS();
    return api("GET", repoBase() + "/file?path=" + encodeURIComponent(path) + (qs ? "&" + qs : ""))
      .then(function (data) {
        if (seq !== state.renderSeq) return;
        renderFileDetail(data);
      })
      .catch(function (err) {
        if (seq === state.renderSeq) renderError(err, function () { loadTab(); });
      });
  }

  function renderFileDetail(data) {
    var host = $("#content");
    clear(host);
    var parentDir = data.path.split("/").slice(0, -1).join("/");
    var back = el("button", { class: "btn btn-sm", type: "button" }, [
      glyph("back"), document.createTextNode("Files")
    ]);
    back.addEventListener("click", function () {
      state.filePath = null;
      state.filesPath = parentDir;
      loadTab();
    });
    host.appendChild(el("div", { class: "view-head" }, [
      back,
      el("span", { class: "view-title", text: data.path, style: "font-size:var(--fs-3)" }),
      el("span", { class: "view-sub", text: fmtInt(data.authors.length) + " authors in scope" })
    ]));
    host.appendChild(tilesForFile(data));
    host.appendChild(el("div", { class: "panel-head", style: "margin-top:16px;margin-bottom:8px" }, [
      el("span", { class: "panel-title", text: "Ownership by author" }),
      el("span", { class: "panel-meta", text: "ω = author churn ÷ file churn" })
    ]));
    host.appendChild(dataTable({
      id: "fileAuthors:" + state.repoId + ":" + data.path,
      columns: [
        {
          key: "name", label: "Author", sortable: true,
          render: function (r) {
            return el("div", {}, [
              el("div", { text: r.name }),
              el("div", { class: "id-chip", text: r.email })
            ]);
          }
        },
        { key: "commits", label: "Commits", num: true, sortable: true, render: function (r) { return fmtInt(r.commits); } },
        { key: "modifications", label: "Mods", num: true, sortable: true, render: function (r) { return fmtInt(r.modifications); } },
        { key: "added", label: "Added", num: true, sortable: true, render: function (r) { return fmtInt(r.added); } },
        { key: "removed", label: "Removed", num: true, sortable: true, render: function (r) { return fmtInt(r.removed); } },
        { key: "churn", label: "Churn", num: true, sortable: true, render: function (r) { return fmtInt(r.churn); } },
        { key: "ownership", label: "Ownership", sortable: true, render: function (r) { return ownBar(r.ownership); } }
      ],
      rows: data.authors,
      defaultSort: { key: "churn", dir: "desc" },
      empty: "No authors touched this file in the current scope."
    }));
  }

  /* -------------------------------------------------------- authors tab */
  function loadAuthors(seq) {
    return api("GET", repoBase() + "/authors?" + filterQS())
      .then(function (rows) {
        if (seq !== state.renderSeq) return;
        renderAuthors(rows);
      })
      .catch(function (err) {
        if (seq === state.renderSeq) renderError(err, function () { loadTab(); });
      });
  }

  function renderAuthors(rows) {
    var host = $("#content");
    clear(host);
    host.appendChild(el("div", { class: "view-head" }, [
      el("span", { class: "view-title", text: "Authors" }),
      el("span", { class: "view-sub", text: fmtInt(rows.length) + " canonical authors in scope" })
    ]));
    if (!rows.length) {
      host.appendChild(emptyForScope("No authors have commits in the current scope."));
      return;
    }
    host.appendChild(dataTable({
      id: "authors:" + state.repoId,
      columns: [
        {
          key: "name", label: "Author", sortable: true,
          render: function (r) {
            var merge = el("button", { class: "btn btn-sm", type: "button", text: "Merge…", title: "Merge this author's identities" });
            merge.addEventListener("click", function (ev) { ev.stopPropagation(); openMergeDrawer(r.id); });
            var ids = el("div", { class: "identities" }, r.identities.map(function (i) {
              return el("span", { class: "id-chip" }, [
                document.createTextNode(i.name + " · " + i.email),
                i.id === r.id ? el("span", { class: "canon", text: "canonical" }) : null
              ]);
            }));
            return el("div", {}, [
              el("div", { class: "row-title" }, [el("span", { class: "name", style: "font-family:var(--sans)", text: r.name }), merge]),
              ids
            ]);
          }
        },
        { key: "commits", label: "Commits", num: true, sortable: true, render: function (r) { return fmtInt(r.commits); } },
        { key: "modifications", label: "Mods", num: true, sortable: true, render: function (r) { return fmtInt(r.modifications); } },
        { key: "churn", label: "Churn", num: true, sortable: true, render: function (r) { return fmtInt(r.churn); } },
        { key: "ownership", label: "Ownership", sortable: true, render: function (r) { return ownBar(r.ownership); } }
      ],
      rows: rows,
      defaultSort: { key: "churn", dir: "desc" },
      empty: "No authors in scope."
    }));
  }

  function openMergeDrawer(focusId) {
    var authors = (state.authorsCache[state.repoId] || []).slice();
    if (!authors.length) { toast("No authors available to merge", "error"); return; }
    var d = drawer("Merge identities", "Combine author identities into one canonical author");
    var canonicalId = focusId || authors[0].id;
    var absorb = {};

    function absorbCount() {
      return Object.keys(absorb).filter(function (k) { return absorb[k]; }).length;
    }

    function renderBody() {
      clear(d.body);
      d.body.appendChild(el("p", { class: "form-hint", style: "margin-bottom:12px" },
        "Pick the canonical author to keep, then tick the identity groups to fold into it. " +
        "Ownership and author tables are recomputed over the current scope."));
      var list = el("div", { class: "pick-list" });
      authors.forEach(function (a) {
        var isCanon = a.id === canonicalId;
        var radio = el("input", { type: "radio", name: "mergeCanon", "aria-label": "Keep " + a.name + " as canonical" });
        radio.checked = isCanon;
        radio.addEventListener("change", function () {
          if (!radio.checked) return;
          canonicalId = a.id;
          delete absorb[a.id];
          renderBody();
        });
        var box = el("input", { type: "checkbox", "aria-label": "Absorb " + a.name });
        box.checked = !!absorb[a.id];
        box.disabled = isCanon;
        box.addEventListener("change", function () {
          if (box.checked) absorb[a.id] = true; else delete absorb[a.id];
          renderFoot();
        });
        var row = el("label", { class: "pick-row" }, [
          radio, box,
          el("span", { class: "pick-main", text: a.name + " · " + a.email }),
          el("span", { class: "pick-meta", text: fmtInt(a.commits) + " commits · " + fmtInt(a.identities.length) + (a.identities.length === 1 ? " identity" : " identities") })
        ]);
        list.appendChild(row);
      });
      d.body.appendChild(list);
    }

    function renderFoot() {
      clear(d.foot);
      var cancel = el("button", { class: "btn btn-quiet", type: "button", text: "Cancel" });
      cancel.addEventListener("click", closeLayer);
      var apply = el("button", { class: "btn btn-accent", type: "button", text: "Merge selected" });
      apply.disabled = absorbCount() === 0;
      apply.addEventListener("click", function () {
        var mergeIds = Object.keys(absorb).filter(function (k) { return absorb[k]; }).map(Number);
        apply.disabled = true;
        apply.textContent = "Merging…";
        api("POST", repoBase() + "/authors/merge", { canonical_id: canonicalId, merge_ids: mergeIds })
          .then(function (res) {
            toast("Merged " + fmtInt(res.merged) + (res.merged === 1 ? " identity" : " identities"));
            delete state.authorsCache[state.repoId];
            closeLayer();
            ensureAuthors().then(function () { renderRail(); });
            loadTab();
          })
          .catch(function (err) {
            toast(err.message, "error");
            apply.disabled = false;
            apply.textContent = "Merge selected";
          });
      });
      d.foot.appendChild(cancel);
      d.foot.appendChild(apply);
    }

    renderBody();
    renderFoot();
    openLayer(d.node, { focus: "input" });
  }

  /* -------------------------------------------------------- commits tab */
  function loadCommits(seq) {
    var c = state.commits;
    var qs = ["limit=" + c.limit, "offset=" + c.offset];
    if (c.query) qs.push("query=" + encodeURIComponent(c.query));
    var f = filterQS();
    return api("GET", repoBase() + "/commits?" + qs.join("&") + (f ? "&" + f : ""))
      .then(function (data) {
        if (seq !== state.renderSeq) return;
        c.total = data.total;
        c.rows = data.commits;
        renderCommitsView();
      })
      .catch(function (err) {
        if (seq === state.renderSeq) renderError(err, function () { loadTab(); });
      });
  }

  function renderCommitsView() {
    var host = $("#content");
    clear(host);
    var c = state.commits;

    function selectedHashes() {
      return Object.keys(c.selection).filter(function (k) { return c.selection[k]; });
    }

    var search = el("input", {
      class: "input", type: "search", placeholder: "Search subject or hash prefix…",
      value: c.query, "aria-label": "Search commits"
    });
    search.addEventListener("input", debounce(function () {
      c.query = search.value.trim();
      c.offset = 0;
      loadTab();
    }, 320));
    search.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") {
        c.query = search.value.trim();
        c.offset = 0;
        loadTab();
      }
    });

    var pager = el("div", { class: "pager" });
    var from = c.total ? c.offset + 1 : 0;
    var to = Math.min(c.offset + c.rows.length, c.total);
    pager.appendChild(el("span", { text: fmtInt(from) + "–" + fmtInt(to) + " of " + fmtInt(c.total) }));
    var prev = el("button", { class: "btn btn-sm", type: "button", text: "Prev" });
    prev.disabled = c.offset <= 0;
    prev.addEventListener("click", function () { c.offset = Math.max(0, c.offset - c.limit); loadTab(); });
    var next = el("button", { class: "btn btn-sm", type: "button", text: "Next" });
    next.disabled = c.offset + c.rows.length >= c.total;
    next.addEventListener("click", function () { c.offset += c.limit; loadTab(); });
    pager.appendChild(prev);
    pager.appendChild(next);

    var searchWrap = el("div", { class: "search-wrap" }, [
      el("span", { class: "search-icon", html: ICONS.search }),
      search
    ]);
    host.appendChild(el("div", { class: "toolbar" }, [searchWrap, el("span", { class: "grow" }), pager]));

    if (state.filters.hashes) {
      var clearPin = el("button", { class: "btn btn-sm btn-quiet", type: "button", text: "Clear filter" });
      clearPin.addEventListener("click", function () {
        state.filters.hashes = null;
        applyFilters();
      });
      host.appendChild(el("div", { class: "sel-bar" }, [
        el("span", { class: "sel-count", text: fmtInt(state.filters.hashes.length) }),
        el("span", { text: "pinned commits are the commit-set filter" }),
        el("span", { class: "grow" }),
        clearPin
      ]));
    }

    var selHost = el("div");
    host.appendChild(selHost);

    function syncSelBar(selectAllBox) {
      clear(selHost);
      var sel = selectedHashes();
      if (!sel.length) return;
      var apply = el("button", { class: "btn btn-sm btn-accent", type: "button", text: "Apply as filter" });
      apply.addEventListener("click", function () {
        state.filters.hashes = sel.slice();
        state.commits.selection = {};
        state.tab = "overview";
        if (location.hash !== "#overview") history.replaceState(null, "", "#overview");
        renderTabs();
        toast(fmtInt(sel.length) + (sel.length === 1 ? " commit" : " commits") + " pinned to the filter");
        applyFilters();
      });
      var clearSel = el("button", { class: "btn btn-sm btn-quiet", type: "button", text: "Clear selection" });
      clearSel.addEventListener("click", function () {
        state.commits.selection = {};
        loadTab();
      });
      var bar = el("div", { class: "sel-bar" }, [
        el("span", { class: "sel-count", text: fmtInt(sel.length) }),
        el("span", { text: sel.length === 1 ? "commit selected" : "commits selected" }),
        el("span", { class: "grow" })
      ]);
      if (c.total > c.rows.length) {
        var all = el("button", { class: "btn btn-sm", type: "button", text: "Select all " + fmtInt(Math.min(c.total, 900)) + " matching" });
        all.addEventListener("click", selectAllMatching);
        bar.appendChild(all);
      }
      bar.appendChild(apply);
      bar.appendChild(clearSel);
      selHost.appendChild(bar);
    }

    function selectAllMatching() {
      var cap = Math.min(c.total, 900);
      var qs = ["limit=" + cap, "offset=0"];
      if (c.query) qs.push("query=" + encodeURIComponent(c.query));
      var f = filterQS();
      api("GET", repoBase() + "/commits?" + qs.join("&") + (f ? "&" + f : ""))
        .then(function (data) {
          data.commits.forEach(function (row) { c.selection[row.hash] = true; });
          if (c.total > cap) toast("Commit filters support up to 900 hashes; the most recent 900 were selected");
          loadTab();
        })
        .catch(function (err) { toast(err.message, "error"); });
    }

    if (!c.total) {
      host.appendChild(el("div", { class: "table-wrap" }, el("div", {
        class: "empty-inline",
        text: c.query ? "No commits match this search." : (hasFilters() ? "No commits match the current filters." : "This repository has no commits.")
      })));
      if (hasFilters() && !c.query) {
        var clearBox = el("div", { class: "empty-actions", style: "justify-content:center;margin-top:12px" });
        var clearBtn = el("button", { class: "btn", type: "button", text: "Clear all filters" });
        clearBtn.addEventListener("click", clearFilters);
        clearBox.appendChild(clearBtn);
        host.appendChild(clearBox);
      }
      return;
    }

    var table = el("table", { class: "data" });
    var headRow = el("tr");
    var allBox = el("input", { type: "checkbox", "aria-label": "Select all commits on this page" });
    allBox.checked = c.rows.length > 0 && c.rows.every(function (r) { return c.selection[r.hash]; });
    allBox.indeterminate = !allBox.checked && c.rows.some(function (r) { return c.selection[r.hash]; });
    allBox.addEventListener("change", function () {
      c.rows.forEach(function (r) {
        if (allBox.checked) c.selection[r.hash] = true;
        else delete c.selection[r.hash];
      });
      loadTab();
    });
    headRow.appendChild(el("th", { class: "cell-check" }, allBox));
    headRow.appendChild(el("th", { text: "Commit" }));
    headRow.appendChild(el("th", { text: "Date" }));
    headRow.appendChild(el("th", { text: "Author" }));
    headRow.appendChild(el("th", { text: "Subject" }));
    var thead = el("thead", {}, headRow);
    var tbody = el("tbody");
    c.rows.forEach(function (row) {
      var tr = el("tr", { class: "click" });
      var cb = el("input", { type: "checkbox", "aria-label": "Select commit " + shortHash(row.hash) });
      cb.checked = !!c.selection[row.hash];
      cb.addEventListener("change", function () {
        if (cb.checked) c.selection[row.hash] = true;
        else delete c.selection[row.hash];
        syncSelBar(allBox);
      });
      tr.appendChild(el("td", { class: "cell-check" }, cb));
      tr.appendChild(el("td", {}, el("span", { class: "hash-chip", text: shortHash(row.hash), title: row.hash })));
      tr.appendChild(el("td", { class: "cell-path", text: fmtDay(row.ts) }));
      tr.appendChild(el("td", { text: row.author }));
      tr.appendChild(el("td", {}, el("div", { class: "subj", text: row.subject || "(no subject)", title: row.subject || "" })));
      tr.addEventListener("click", function (ev) {
        if (ev.target.closest("input,button")) return;
        if (c.selection[row.hash]) delete c.selection[row.hash];
        else c.selection[row.hash] = true;
        cb.checked = !cb.checked;
        syncSelBar(allBox);
      });
      tbody.appendChild(tr);
    });
    var wrap = el("div", { class: "table-wrap" });
    wrap.appendChild(el("table", { class: "data" }, [thead, tbody]));
    host.appendChild(wrap);
    syncSelBar(allBox);
  }

  /* ------------------------------------------------------ commit picker */
  function openPickerDrawer() {
    var d = drawer("Commit picker", "Pin an explicit commit set as the H filter");
    var sel = {};
    (state.filters.hashes || []).forEach(function (h) { sel[h] = true; });
    var listHost = el("div");
    var countNote = el("div", { class: "form-hint" });
    var search = el("input", {
      class: "input", type: "search", placeholder: "Search subject or hash prefix…",
      "aria-label": "Search commits"
    });
    var selectAll = el("button", { class: "btn btn-sm", type: "button", text: "Select all matching" });
    var footClear = el("button", { class: "btn btn-quiet", type: "button", text: "Clear filter" });
    var footApply = el("button", { class: "btn btn-accent", type: "button", text: "Apply" });
    var lastTotal = 0;

    function selCount() {
      return Object.keys(sel).filter(function (k) { return sel[k]; }).length;
    }
    function updateFoot() {
      var n = selCount();
      footApply.textContent = n ? "Apply " + fmtInt(n) + (n === 1 ? " commit" : " commits") : "Apply";
      footApply.disabled = n === 0;
      countNote.textContent = n
        ? fmtInt(n) + (n === 1 ? " commit selected" : " commits selected")
        : (lastTotal ? "No commits selected" : "");
    }

    function listQS(limit) {
      var qs = ["limit=" + limit, "offset=0"];
      if (search.value.trim()) qs.push("query=" + encodeURIComponent(search.value.trim()));
      var f = filterQS(true);
      return repoBase() + "/commits?" + qs.join("&") + (f ? "&" + f : "");
    }

    function loadList() {
      clear(listHost);
      listHost.appendChild(el("div", { class: "empty-inline", text: "Loading…" }));
      api("GET", listQS(200)).then(function (data) {
        lastTotal = data.total;
        clear(listHost);
        if (!data.commits.length) {
          listHost.appendChild(el("div", { class: "empty-inline", text: "No commits match." }));
          updateFoot();
          return;
        }
        var list = el("div", { class: "pick-list" });
        data.commits.forEach(function (row) {
          var cb = el("input", { type: "checkbox", "aria-label": "Select " + shortHash(row.hash) });
          cb.checked = !!sel[row.hash];
          cb.addEventListener("change", function () {
            if (cb.checked) sel[row.hash] = true;
            else delete sel[row.hash];
            updateFoot();
          });
          list.appendChild(el("label", { class: "pick-row" }, [
            cb,
            el("span", { class: "pick-main" }, [
              el("span", { class: "hash-chip", text: shortHash(row.hash) }),
              document.createTextNode("  " + (row.subject || "(no subject)"))
            ]),
            el("span", { class: "pick-meta", text: fmtDay(row.ts) + " · " + row.author })
          ]));
        });
        listHost.appendChild(list);
        if (data.total > data.commits.length) {
          listHost.appendChild(el("div", { class: "form-hint", style: "margin-top:8px" },
            "Showing the most recent " + fmtInt(data.commits.length) + " of " + fmtInt(data.total) + " commits. " +
            "Use \"Select all matching\" to pin the whole set (max 900)."));
        }
        updateFoot();
      }).catch(function (err) {
        clear(listHost);
        listHost.appendChild(el("div", { class: "empty-inline", text: err.message }));
        updateFoot();
      });
    }

    search.addEventListener("input", debounce(loadList, 320));
    selectAll.addEventListener("click", function () {
      api("GET", listQS(900)).then(function (data) {
        data.commits.forEach(function (row) { sel[row.hash] = true; });
        if (data.total > 900) toast("Commit filters support up to 900 hashes; the most recent 900 were selected");
        loadList();
      }).catch(function (err) { toast(err.message, "error"); });
    });
    footClear.addEventListener("click", function () {
      state.filters.hashes = null;
      closeLayer();
      toast("Commit-set filter cleared");
      applyFilters();
    });
    footApply.addEventListener("click", function () {
      var hashes = Object.keys(sel).filter(function (k) { return sel[k]; });
      state.filters.hashes = hashes;
      closeLayer();
      toast(fmtInt(hashes.length) + (hashes.length === 1 ? " commit pinned to the filter" : " commits pinned to the filter"));
      applyFilters();
    });

    d.body.appendChild(el("div", { class: "toolbar" }, [search, selectAll]));
    d.body.appendChild(countNote);
    d.body.appendChild(listHost);
    d.foot.appendChild(footClear);
    d.foot.appendChild(footApply);
    updateFoot();
    openLayer(d.node, { focus: "input" });
    loadList();
  }

  /* ------------------------------------------------------------ startup */
  function init() {
    readPalette();
    var hashTab = (location.hash || "").replace("#", "");
    TABS.forEach(function (t) { if (t.id === hashTab) state.tab = hashTab; });
    renderTabs();
    renderRail();
    renderScope();

    $("#btnAddRepo").addEventListener("click", function (ev) {
      ev.stopPropagation();
      if (state.openLayer) { closeLayer(); return; }
      openAddPopover();
    });
    $("#btnLoadSample").addEventListener("click", function (ev) {
      ev.stopPropagation();
      loadSample();
    });
    $("#railToggle").addEventListener("click", function () {
      var rail = $("#filterRail");
      var open = rail.classList.toggle("open");
      $("#railToggle").setAttribute("aria-expanded", open ? "true" : "false");
    });

    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && state.openLayer) closeLayer();
    });
    document.addEventListener("click", function (ev) {
      var layer = state.openLayer;
      if (!layer) return;
      if (layer.node && layer.node.contains(ev.target)) return;
      closeLayer();
    });
    window.addEventListener("hashchange", function () {
      var t = (location.hash || "").replace("#", "");
      if (t !== state.tab) {
        TABS.forEach(function (x) {
          if (x.id === t) {
            state.tab = t;
            renderTabs();
            loadTab();
          }
        });
      }
    });
    window.addEventListener("resize", debounce(function () {
      Object.keys(state.charts).forEach(function (k) {
        if (state.charts[k]) state.charts[k].resize();
      });
    }, 150));

    refreshRepos().catch(function (err) {
      state.booting = false;
      toast(err.message, "error");
      renderError(err, function () { init(); });
    });
    loadTab();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
