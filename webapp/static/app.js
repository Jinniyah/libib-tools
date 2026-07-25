// webapp/static/app.js
//
// The entire client-side script for the app — no framework, no build step.
// Wires the run-a-scraper page: submits the options form to start a job,
// opens an EventSource to stream its log, reacts to named "status" and
// "done" SSE events (see webapp/app.py's _job_event_stream), and renders
// download links once the job finishes.

function initScrapePage() {
  const form = document.getElementById("scrape-form");
  if (!form) return;

  const provider = form.dataset.provider;
  const startBtn = document.getElementById("start-btn");
  const jobPanel = document.getElementById("job-panel");
  const statusBadge = document.getElementById("job-status");
  const loginWait = document.getElementById("login-wait");
  const continueBtn = document.getElementById("continue-btn");
  const cancelBtn = document.getElementById("cancel-btn");
  const logPanel = document.getElementById("log-panel");
  const resultBox = document.getElementById("job-result");
  const resultSummary = document.getElementById("job-result-summary");
  const downloadsBox = document.getElementById("job-downloads");

  const TERMINAL_STATUSES = ["completed", "failed", "cancelled"];

  let currentJobId = null;

  function setStatus(status) {
    statusBadge.textContent = status;
    statusBadge.className = "badge badge-status-" + status;
    loginWait.hidden = status !== "waiting_for_login";
    cancelBtn.hidden = TERMINAL_STATUSES.includes(status);
  }

  function appendLog(line) {
    logPanel.textContent += line + "\n";
    logPanel.scrollTop = logPanel.scrollHeight;
  }

  function jobUrl(suffix) {
    return "/scrape/" + provider + "/jobs/" + currentJobId + suffix;
  }

  async function loadResult() {
    const response = await fetch(jobUrl(""));
    const detail = await response.json();

    resultBox.hidden = false;
    if (detail.error) {
      resultSummary.textContent = "Failed: " + detail.error;
    } else if (detail.downloads.length === 0) {
      resultSummary.textContent =
        "Job " + detail.status + ". No files were written (dry run, or no books found).";
    } else if (detail.output_dir) {
      resultSummary.textContent =
        "Job " + detail.status + ". Files saved to: " + detail.output_dir;
    } else {
      resultSummary.textContent = "Job " + detail.status + ".";
    }

    downloadsBox.innerHTML = "";
    detail.downloads.forEach((d) => {
      const link = document.createElement("a");
      link.href = d.url;
      link.className = "btn btn-secondary";
      link.textContent = "Download " + d.filename;
      link.style.marginRight = "0.5rem";
      downloadsBox.appendChild(link);
    });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    startBtn.disabled = true;

    const formData = new FormData(form);
    const payload = {
      pages: formData.get("pages") ? Number(formData.get("pages")) : null,
      dry_run: formData.get("dry_run") === "on",
      output_dir: formData.get("output_dir") || ".",
      no_enrich: formData.get("no_enrich") === "on",
    };

    let response;
    try {
      response = await fetch("/scrape/" + provider + "/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (err) {
      appendLog("Error starting job: " + err);
      startBtn.disabled = false;
      return;
    }

    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      appendLog("Error starting job: " + (body.detail || response.statusText));
      startBtn.disabled = false;
      return;
    }

    const data = await response.json();
    currentJobId = data.job_id;
    jobPanel.hidden = false;
    cancelBtn.disabled = false;
    continueBtn.disabled = false;
    setStatus(data.status);

    const source = new EventSource(jobUrl("/events"));
    source.onmessage = (e) => appendLog(e.data);
    source.addEventListener("status", (e) => setStatus(e.data));
    source.addEventListener("done", (e) => {
      setStatus(e.data);
      source.close();
      startBtn.disabled = false;
      loadResult();
    });
    source.onerror = () => {
      appendLog("[connection to server lost]");
    };
  });

  continueBtn.addEventListener("click", async () => {
    continueBtn.disabled = true;
    // Hide immediately rather than waiting for the status SSE event to come
    // back — a successful Continue always moves the job off
    // waiting_for_login, and waiting for that round trip (backend poll +
    // SSE push) left the popup visible and re-clickable for up to ~1s,
    // which read as "my click didn't register."
    loginWait.hidden = true;

    const response = await fetch(jobUrl("/continue"), { method: "POST" });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      appendLog("Error continuing job: " + (body.detail || response.statusText));
      loginWait.hidden = false;
      continueBtn.disabled = false;
    }
  });

  cancelBtn.addEventListener("click", async () => {
    cancelBtn.disabled = true;
    await fetch(jobUrl("/cancel"), { method: "POST" });
  });
}

// webapp/static/app.js (continued)
//
// Reconciliation: initReconcilePage() runs a reconcile job (same SSE/job
// pattern as initScrapePage(), minus the login-wait step — reconcile has no
// manual login). initReconcileReviewPage() is the interactive checkbook
// review: pick a gap book, search/rank candidate Libib entries the
// automated matcher missed, confirm a match or confirm it's genuinely new,
// finalize into a reviewed gap CSV + tag-suggestions report.

function initReconcilePage() {
  const form = document.getElementById("reconcile-form");
  if (!form) return;

  const startBtn = document.getElementById("start-btn");
  const jobPanel = document.getElementById("job-panel");
  const statusBadge = document.getElementById("job-status");
  const cancelBtn = document.getElementById("cancel-btn");
  const logPanel = document.getElementById("log-panel");
  const resultBox = document.getElementById("job-result");
  const resultSummary = document.getElementById("job-result-summary");
  const reviewLink = document.getElementById("job-review-link");
  const downloadsBox = document.getElementById("job-downloads");

  const TERMINAL_STATUSES = ["completed", "failed", "cancelled"];

  let currentJobId = null;

  function setStatus(status) {
    statusBadge.textContent = status;
    statusBadge.className = "badge badge-status-" + status;
    cancelBtn.hidden = TERMINAL_STATUSES.includes(status);
  }

  function appendLog(line) {
    logPanel.textContent += line + "\n";
    logPanel.scrollTop = logPanel.scrollHeight;
  }

  function jobUrl(suffix) {
    return "/reconcile/jobs/" + currentJobId + suffix;
  }

  async function loadResult() {
    const response = await fetch(jobUrl(""));
    const detail = await response.json();

    resultBox.hidden = false;
    if (detail.error) {
      resultSummary.textContent = "Failed: " + detail.error;
    } else if (detail.downloads.length === 0) {
      resultSummary.textContent = "Job " + detail.status + ". No files were written.";
    } else if (detail.output_dir) {
      resultSummary.textContent =
        "Job " + detail.status + ". Files saved to: " + detail.output_dir;
    } else {
      resultSummary.textContent = "Job " + detail.status + ".";
    }

    reviewLink.innerHTML = "";
    if (detail.review_snapshot_path) {
      const link = document.createElement("a");
      link.href =
        "/reconcile/review?snapshot=" + encodeURIComponent(detail.review_snapshot_path);
      link.className = "btn";
      link.textContent = "Start reviewing";
      reviewLink.appendChild(link);
    }

    downloadsBox.innerHTML = "";
    detail.downloads.forEach((d) => {
      const link = document.createElement("a");
      link.href = d.url;
      link.className = "btn btn-secondary";
      link.textContent = "Download " + d.filename;
      link.style.marginRight = "0.5rem";
      downloadsBox.appendChild(link);
    });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    startBtn.disabled = true;

    const formData = new FormData(form);
    const payload = {
      libib_path: formData.get("libib_path"),
      chirp: formData.get("chirp") || null,
      kindle: formData.get("kindle") || null,
      kobo: formData.get("kobo") || null,
      nook: formData.get("nook") || null,
      output_dir: formData.get("output_dir") || ".",
      no_enrich: formData.get("no_enrich") === "on",
    };

    let response;
    try {
      response = await fetch("/reconcile/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (err) {
      appendLog("Error starting job: " + err);
      startBtn.disabled = false;
      return;
    }

    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      appendLog("Error starting job: " + (body.detail || response.statusText));
      startBtn.disabled = false;
      return;
    }

    const data = await response.json();
    currentJobId = data.job_id;
    jobPanel.hidden = false;
    cancelBtn.disabled = false;
    setStatus(data.status);

    const source = new EventSource(jobUrl("/events"));
    source.onmessage = (e) => appendLog(e.data);
    source.addEventListener("status", (e) => setStatus(e.data));
    source.addEventListener("done", (e) => {
      setStatus(e.data);
      source.close();
      startBtn.disabled = false;
      loadResult();
    });
    source.onerror = () => {
      appendLog("[connection to server lost]");
    };
  });

  cancelBtn.addEventListener("click", async () => {
    cancelBtn.disabled = true;
    await fetch(jobUrl("/cancel"), { method: "POST" });
  });
}

function initReconcileReviewPage() {
  const app = document.getElementById("review-app");
  if (!app) return;

  const snapshot = app.dataset.snapshot;
  const progressEl = document.getElementById("review-progress");
  const saveStatusEl = document.getElementById("save-status");
  const gapFilterInput = document.getElementById("gap-filter");
  const gapListEl = document.getElementById("gap-list");
  const candidateEmpty = document.getElementById("candidate-empty");
  const candidateDetail = document.getElementById("candidate-detail");
  const gapTitleEl = document.getElementById("candidate-gap-title");
  const gapAuthorEl = document.getElementById("candidate-gap-author");
  const candidateSearchInput = document.getElementById("candidate-search");
  const candidateSearchHint = document.getElementById("candidate-search-hint");
  const candidateListEl = document.getElementById("candidate-list");
  const confirmNewBtn = document.getElementById("confirm-new-btn");
  const enrichBtn = document.getElementById("enrich-btn");
  const metaDescription = document.getElementById("meta-description");
  const metaPublisher = document.getElementById("meta-publisher");
  const metaPublishDate = document.getElementById("meta-publish-date");
  const metaLength = document.getElementById("meta-length");
  const metaSeriesName = document.getElementById("meta-series-name");
  const metaSeriesPosition = document.getElementById("meta-series-position");
  const saveMetadataBtn = document.getElementById("save-metadata-btn");
  const finalizeBtn = document.getElementById("finalize-btn");
  const finalizeResult = document.getElementById("finalize-result");

  let gaps = [];
  let selectedGapKey = null;
  let searchDebounceTimer = null;

  function apiUrl(path, params) {
    const url = new URL(path, window.location.origin);
    url.searchParams.set("snapshot", snapshot);
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v) url.searchParams.set(k, v);
    });
    return url.toString();
  }

  function currentGap() {
    return gaps.find((g) => g.key === selectedGapKey) || null;
  }

  function decisionBadge(decision) {
    const status = decision ? decision.status : "undecided";
    const span = document.createElement("span");
    span.className = "badge badge-status-" + status;
    span.textContent = status.replace("_", " ");
    return span;
  }

  function updateProgress() {
    const decided = gaps.filter((g) => g.decision).length;
    progressEl.textContent = decided + " / " + gaps.length + " reviewed";
  }

  function renderGapList() {
    const filter = gapFilterInput.value.trim().toLowerCase();
    gapListEl.innerHTML = "";

    gaps
      .filter(
        (g) =>
          !filter ||
          g.title.toLowerCase().includes(filter) ||
          g.author.toLowerCase().includes(filter)
      )
      .forEach((g) => {
        const li = document.createElement("li");
        li.className = "review-row";
        if (g.key === selectedGapKey) li.classList.add("selected");

        const left = document.createElement("span");
        const text = document.createElement("span");
        text.textContent = g.title + (g.author ? " — " + g.author : "");
        left.appendChild(text);
        if (!g.has_enrichment) {
          const noMeta = document.createElement("span");
          noMeta.className = "field-hint";
          noMeta.style.marginLeft = "0.4rem";
          noMeta.textContent = "(no metadata)";
          left.appendChild(noMeta);
        }
        li.appendChild(left);
        li.appendChild(decisionBadge(g.decision));

        li.addEventListener("click", () => selectGap(g.key));
        gapListEl.appendChild(li);
      });

    updateProgress();
  }

  async function loadGaps() {
    const response = await fetch(apiUrl("/api/reconcile/review/gaps"));
    const data = await response.json();
    gaps = data.gaps;
    renderGapList();
    if (selectedGapKey) renderCandidateHeader();
  }

  function populateMetadataForm(gap) {
    const e = gap.enrichment || {};
    metaDescription.value = e.description || "";
    metaPublisher.value = e.publisher || "";
    metaPublishDate.value = e.publish_date || "";
    metaLength.value = e.length_of || "";
    metaSeriesName.value = e.series_name || "";
    metaSeriesPosition.value = e.series_position != null ? e.series_position : "";
  }

  function renderCandidateHeader() {
    const gap = currentGap();
    if (!gap) return;
    gapTitleEl.textContent = gap.title;
    gapAuthorEl.textContent = gap.author ? "by " + gap.author : "Author unknown";
    populateMetadataForm(gap);

    const decided = gap.decision && gap.decision.status === "confirmed_new";
    confirmNewBtn.textContent = decided
      ? "✓ Confirmed new — click to undo"
      : "None of these — confirm as genuinely new";
  }

  function renderCandidateList(candidates, mode, total) {
    const gap = currentGap();
    const confirmedLibibKey =
      gap && gap.decision && gap.decision.status === "confirmed_match"
        ? gap.decision.libib_key
        : null;

    candidateListEl.innerHTML = "";

    if (mode === "search") {
      candidateSearchHint.hidden = total <= candidates.length;
      if (!candidateSearchHint.hidden) {
        candidateSearchHint.textContent =
          total - candidates.length + " more — refine your search";
      }
    } else {
      candidateSearchHint.hidden = true;
    }

    candidates.forEach((item) => {
      const candidate = mode === "search" ? item : item.candidate;
      const li = document.createElement("li");
      li.className = "candidate-row";

      const info = document.createElement("div");
      let label = candidate.title + (candidate.creators ? " — " + candidate.creators : "");
      if (mode === "ranked") {
        label += " (" + Math.round(item.score * 100) + "% match" +
          (item.author_overlap ? ", author overlaps" : "") + ")";
      }
      info.textContent = label;
      li.appendChild(info);

      const isConfirmed = candidate.key === confirmedLibibKey;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-secondary";
      btn.textContent = isConfirmed ? "✓ Confirmed — click to undo" : "Confirm match";
      if (isConfirmed) li.classList.add("selected");
      btn.addEventListener("click", () =>
        saveDecision(isConfirmed ? "undecided" : "confirmed_match", candidate.key)
      );
      li.appendChild(btn);

      candidateListEl.appendChild(li);
    });
  }

  async function loadCandidates(query) {
    if (!selectedGapKey) return;
    const response = await fetch(
      apiUrl("/api/reconcile/review/candidates", { gap_key: selectedGapKey, q: query })
    );
    const data = await response.json();
    renderCandidateList(data.candidates, data.mode, data.total);
  }

  function selectGap(key) {
    selectedGapKey = key;
    candidateSearchInput.value = "";
    candidateEmpty.hidden = true;
    candidateDetail.hidden = false;
    renderGapList();
    renderCandidateHeader();
    loadCandidates(null);
  }

  let saveStatusTimer = null;

  function flashSaved(text) {
    saveStatusEl.textContent = text;
    clearTimeout(saveStatusTimer);
    saveStatusTimer = setTimeout(() => {
      saveStatusEl.textContent = "";
    }, 2500);
  }

  async function saveDecision(status, libibKey) {
    const response = await fetch(apiUrl("/api/reconcile/review/decisions"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gap_key: selectedGapKey,
        status: status,
        libib_key: libibKey || null,
      }),
    });
    flashSaved(response.ok ? "Saved ✓" : "Error saving — try again");
    await loadGaps();
    renderCandidateHeader();
    loadCandidates(candidateSearchInput.value.trim() || null);
  }

  gapFilterInput.addEventListener("input", renderGapList);

  candidateSearchInput.addEventListener("input", () => {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => {
      loadCandidates(candidateSearchInput.value.trim() || null);
    }, 200);
  });

  confirmNewBtn.addEventListener("click", () => {
    const gap = currentGap();
    const alreadyConfirmedNew = gap && gap.decision && gap.decision.status === "confirmed_new";
    saveDecision(alreadyConfirmedNew ? "undecided" : "confirmed_new", null);
  });

  enrichBtn.addEventListener("click", async () => {
    if (!selectedGapKey) return;
    enrichBtn.disabled = true;
    enrichBtn.textContent = "Looking…";

    try {
      const response = await fetch(apiUrl("/api/reconcile/review/enrich"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ gap_key: selectedGapKey }),
      });
      if (response.ok) {
        const data = await response.json();
        const gap = currentGap();
        if (gap) {
          gap.enrichment = data.gap.enrichment;
          gap.has_enrichment = data.has_enrichment;
        }
        populateMetadataForm(data.gap);
        flashSaved("Metadata updated ✓");
        renderGapList();
      } else {
        const body = await response.json().catch(() => ({}));
        flashSaved("Error fetching metadata: " + (body.detail || response.statusText));
      }
    } finally {
      enrichBtn.disabled = false;
      enrichBtn.textContent = "Try to find more info";
    }
  });

  saveMetadataBtn.addEventListener("click", async () => {
    if (!selectedGapKey) return;
    saveMetadataBtn.disabled = true;

    const payload = {
      gap_key: selectedGapKey,
      description: metaDescription.value,
      publisher: metaPublisher.value,
      publish_date: metaPublishDate.value,
      length_of: metaLength.value,
      series_name: metaSeriesName.value,
      series_position: metaSeriesPosition.value ? Number(metaSeriesPosition.value) : null,
    };

    try {
      const response = await fetch(apiUrl("/api/reconcile/review/manual-enrichment"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (response.ok) {
        const data = await response.json();
        const gap = currentGap();
        if (gap) {
          gap.enrichment = data.gap.enrichment;
          gap.has_enrichment = data.has_enrichment;
        }
        flashSaved("Saved ✓");
        renderGapList();
      } else {
        const body = await response.json().catch(() => ({}));
        flashSaved("Error saving metadata: " + (body.detail || response.statusText));
      }
    } finally {
      saveMetadataBtn.disabled = false;
    }
  });

  finalizeBtn.addEventListener("click", async () => {
    finalizeBtn.disabled = true;
    const response = await fetch(apiUrl("/reconcile/review/finalize"), { method: "POST" });
    const data = await response.json();
    finalizeBtn.disabled = false;

    finalizeResult.hidden = false;
    finalizeResult.innerHTML = "";

    const gapLink = document.createElement("a");
    gapLink.href = data.gap_csv.url;
    gapLink.className = "btn btn-secondary";
    gapLink.textContent = "Download " + data.gap_csv.filename;
    gapLink.style.marginRight = "0.5rem";
    finalizeResult.appendChild(gapLink);

    if (data.tag_report) {
      const tagLink = document.createElement("a");
      tagLink.href = data.tag_report.url;
      tagLink.className = "btn btn-secondary";
      tagLink.textContent = "Download " + data.tag_report.filename;
      finalizeResult.appendChild(tagLink);
    }
  });

  loadGaps();
}
