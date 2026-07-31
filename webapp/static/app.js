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

      const orphanLink = document.createElement("a");
      orphanLink.href =
        "/reconcile/review/orphans?snapshot=" +
        encodeURIComponent(detail.review_snapshot_path);
      orphanLink.className = "btn btn-secondary";
      orphanLink.textContent = "Review orphans";
      orphanLink.style.marginLeft = "0.5rem";
      reviewLink.appendChild(orphanLink);
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
      wait_for_rate_limits: formData.get("wait_for_rate_limits") === "on",
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

  const resumeDirInput = document.getElementById("resume-dir");
  const findReviewsBtn = document.getElementById("find-reviews-btn");
  const resumeStatusEl = document.getElementById("resume-status");
  const resumeListEl = document.getElementById("resume-list");
  const outputDirInput = document.getElementById("output_dir");

  findReviewsBtn.addEventListener("click", async () => {
    const dir = resumeDirInput.value.trim() || outputDirInput.value.trim() || ".";
    resumeListEl.innerHTML = "";
    resumeStatusEl.textContent = "Looking…";

    let data;
    try {
      const response = await fetch(
        "/api/reconcile/review/snapshots?dir=" + encodeURIComponent(dir)
      );
      data = await response.json();
    } catch (err) {
      resumeStatusEl.textContent = "Error checking that folder: " + err;
      return;
    }

    if (data.snapshots.length === 0) {
      resumeStatusEl.textContent = "No existing reviews found in that folder.";
      return;
    }

    resumeStatusEl.textContent = "";
    data.snapshots.forEach((s) => {
      const li = document.createElement("li");
      li.className = "review-row";

      const info = document.createElement("span");
      info.textContent =
        s.generated_at + " — " + s.decided_count + " / " + s.gap_count + " reviewed";
      li.appendChild(info);

      const link = document.createElement("a");
      link.href = "/reconcile/review?snapshot=" + encodeURIComponent(s.path);
      link.className = "btn btn-secondary";
      link.textContent = "Resume";
      li.appendChild(link);

      const orphanLink = document.createElement("a");
      orphanLink.href =
        "/reconcile/review/orphans?snapshot=" + encodeURIComponent(s.path);
      orphanLink.className = "btn btn-secondary";
      orphanLink.textContent = "Review orphans";
      orphanLink.style.marginLeft = "0.5rem";
      li.appendChild(orphanLink);

      resumeListEl.appendChild(li);
    });
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
  const skipBtn = document.getElementById("skip-btn");
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

    const skipped = gap.decision && gap.decision.status === "skipped";
    skipBtn.textContent = skipped
      ? "✓ Skipped — click to undo"
      : "Skip — not needed in Libib (loan, short story, etc.)";
  }

  function missingTagFor(gap, candidate) {
    // Checks the candidate's already-resolved `providers` list, not its raw
    // `tags` — providers is computed server-side through the same
    // tag-synonym mapping (e.g. Barnes & Noble's own "bn" tag counts as
    // "nook") used for real matching, so this can't wrongly claim a tag is
    // missing just because it's spelled differently than expected.
    return candidate.providers.includes(gap.provider) ? null : gap.provider;
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
      const missingTag = missingTagFor(gap, candidate);

      const info = document.createElement("div");
      let label = candidate.title + (candidate.creators ? " — " + candidate.creators : "");
      if (mode === "ranked") {
        label += " (" + Math.round(item.score * 100) + "% match" +
          (item.author_overlap ? ", author overlaps" : "") + ")";
      }
      info.appendChild(document.createTextNode(label));
      if (missingTag) {
        const tagNote = document.createElement("div");
        tagNote.className = "field-hint";
        tagNote.textContent = "Needs the \"" + missingTag + "\" tag in Libib";
        info.appendChild(tagNote);
      }
      li.appendChild(info);

      const isConfirmed = candidate.key === confirmedLibibKey;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-secondary";
      btn.textContent = isConfirmed
        ? "✓ Confirmed — click to undo"
        : missingTag
        ? 'Confirm match (add "' + missingTag + '" tag)'
        : "Confirm match";
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

  skipBtn.addEventListener("click", () => {
    const gap = currentGap();
    const alreadySkipped = gap && gap.decision && gap.decision.status === "skipped";
    saveDecision(alreadySkipped ? "undecided" : "skipped", null);
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

// Orphan review: the mirror image of initReconcileReviewPage() above — for
// each Libib entry no scrape matched, search/pick the Libib entry it
// duplicates, flag it as no longer owned, or mark it reviewed with no
// action needed, then finalize into a plain-text report of Libib edits to
// make by hand.
function initOrphanReviewPage() {
  const app = document.getElementById("orphan-review-app");
  if (!app) return;

  const snapshot = app.dataset.snapshot;
  const progressEl = document.getElementById("orphan-progress");
  const saveStatusEl = document.getElementById("orphan-save-status");
  const orphanFilterInput = document.getElementById("orphan-filter");
  const orphanListEl = document.getElementById("orphan-list");
  const detailEmpty = document.getElementById("orphan-detail-empty");
  const detail = document.getElementById("orphan-detail");
  const orphanTitleEl = document.getElementById("orphan-title");
  const orphanAuthorEl = document.getElementById("orphan-author");
  const orphanProvidersEl = document.getElementById("orphan-providers");
  const resolvedNoteEl = document.getElementById("orphan-resolved-note");
  const duplicateSearchInput = document.getElementById("duplicate-search");
  const duplicateSearchHint = document.getElementById("duplicate-search-hint");
  const duplicateListEl = document.getElementById("duplicate-list");
  const needsArchiveBtn = document.getElementById("needs-archive-btn");
  const keepBtn = document.getElementById("keep-btn");
  const finalizeBtn = document.getElementById("orphan-finalize-btn");
  const finalizeResult = document.getElementById("orphan-finalize-result");

  let orphans = [];
  let selectedOrphanKey = null;
  let searchDebounceTimer = null;

  function apiUrl(path, params) {
    const url = new URL(path, window.location.origin);
    url.searchParams.set("snapshot", snapshot);
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v) url.searchParams.set(k, v);
    });
    return url.toString();
  }

  function currentOrphan() {
    return orphans.find((o) => o.key === selectedOrphanKey) || null;
  }

  function decisionBadge(orphan) {
    const status = orphan.resolved_via_gap_review
      ? "resolved"
      : orphan.decision
      ? orphan.decision.status
      : "undecided";
    const span = document.createElement("span");
    span.className = "badge badge-status-" + status;
    span.textContent = status.replace("_", " ");
    return span;
  }

  function updateProgress() {
    const decided = orphans.filter(
      (o) => o.decision || o.resolved_via_gap_review
    ).length;
    progressEl.textContent = decided + " / " + orphans.length + " reviewed";
  }

  function renderOrphanList() {
    const filter = orphanFilterInput.value.trim().toLowerCase();
    orphanListEl.innerHTML = "";

    orphans
      .filter(
        (o) =>
          !filter ||
          o.title.toLowerCase().includes(filter) ||
          o.creators.toLowerCase().includes(filter)
      )
      .forEach((o) => {
        const li = document.createElement("li");
        li.className = "review-row";
        if (o.key === selectedOrphanKey) li.classList.add("selected");

        const text = document.createElement("span");
        text.textContent = o.title + (o.creators ? " — " + o.creators : "");
        li.appendChild(text);
        li.appendChild(decisionBadge(o));

        li.addEventListener("click", () => selectOrphan(o.key));
        orphanListEl.appendChild(li);
      });

    updateProgress();
  }

  async function loadOrphans() {
    const response = await fetch(apiUrl("/api/reconcile/review/orphans"));
    const data = await response.json();
    orphans = data.orphans;
    renderOrphanList();
    if (selectedOrphanKey) renderDetailHeader();
  }

  function renderDetailHeader() {
    const orphan = currentOrphan();
    if (!orphan) return;
    orphanTitleEl.textContent = orphan.title;
    orphanAuthorEl.textContent = orphan.creators
      ? "by " + orphan.creators
      : "Author unknown";
    orphanProvidersEl.textContent = orphan.providers.length
      ? "Tagged: " + orphan.providers.join(", ")
      : "";

    resolvedNoteEl.hidden = !orphan.resolved_via_gap_review;

    const isArchive = orphan.decision && orphan.decision.status === "needs_archive";
    needsArchiveBtn.textContent = isArchive
      ? "✓ Needs archived — click to undo"
      : "Needs archived — no longer owned";

    const isKeep = orphan.decision && orphan.decision.status === "keep";
    keepBtn.textContent = isKeep
      ? "✓ Kept — click to undo"
      : "Keep — reviewed, no action needed";
  }

  function renderDuplicateList(candidates, mode, total) {
    const orphan = currentOrphan();
    const confirmedLibibKey =
      orphan && orphan.decision && orphan.decision.status === "duplicate"
        ? orphan.decision.libib_key
        : null;

    duplicateListEl.innerHTML = "";

    if (mode === "search") {
      duplicateSearchHint.hidden = total <= candidates.length;
      if (!duplicateSearchHint.hidden) {
        duplicateSearchHint.textContent =
          total - candidates.length + " more — refine your search";
      }
    } else {
      duplicateSearchHint.hidden = true;
    }

    candidates.forEach((item) => {
      const candidate = mode === "search" ? item : item.candidate;
      const li = document.createElement("li");
      li.className = "candidate-row";

      const info = document.createElement("div");
      let label =
        candidate.title + (candidate.creators ? " — " + candidate.creators : "");
      if (mode === "ranked") {
        label +=
          " (" +
          Math.round(item.score * 100) +
          "% match" +
          (item.author_overlap ? ", author overlaps" : "") +
          ")";
      }
      info.appendChild(document.createTextNode(label));
      li.appendChild(info);

      const isConfirmed = candidate.key === confirmedLibibKey;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-secondary";
      btn.textContent = isConfirmed
        ? "✓ Marked as duplicate — click to undo"
        : "Mark as duplicate";
      if (isConfirmed) li.classList.add("selected");
      btn.addEventListener("click", () =>
        saveDecision(isConfirmed ? "undecided" : "duplicate", candidate.key)
      );
      li.appendChild(btn);

      duplicateListEl.appendChild(li);
    });
  }

  async function loadDuplicates(query) {
    if (!selectedOrphanKey) return;
    const response = await fetch(
      apiUrl("/api/reconcile/review/orphan-candidates", {
        orphan_key: selectedOrphanKey,
        q: query,
      })
    );
    const data = await response.json();
    renderDuplicateList(data.candidates, data.mode, data.total);
  }

  function selectOrphan(key) {
    selectedOrphanKey = key;
    duplicateSearchInput.value = "";
    detailEmpty.hidden = true;
    detail.hidden = false;
    renderOrphanList();
    renderDetailHeader();
    loadDuplicates(null);
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
    const response = await fetch(apiUrl("/api/reconcile/review/orphan-decisions"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        orphan_key: selectedOrphanKey,
        status: status,
        libib_key: libibKey || null,
      }),
    });
    flashSaved(response.ok ? "Saved ✓" : "Error saving — try again");
    await loadOrphans();
    renderDetailHeader();
    loadDuplicates(duplicateSearchInput.value.trim() || null);
  }

  orphanFilterInput.addEventListener("input", renderOrphanList);

  duplicateSearchInput.addEventListener("input", () => {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => {
      loadDuplicates(duplicateSearchInput.value.trim() || null);
    }, 200);
  });

  needsArchiveBtn.addEventListener("click", () => {
    const orphan = currentOrphan();
    const already = orphan && orphan.decision && orphan.decision.status === "needs_archive";
    saveDecision(already ? "undecided" : "needs_archive", null);
  });

  keepBtn.addEventListener("click", () => {
    const orphan = currentOrphan();
    const already = orphan && orphan.decision && orphan.decision.status === "keep";
    saveDecision(already ? "undecided" : "keep", null);
  });

  finalizeBtn.addEventListener("click", async () => {
    finalizeBtn.disabled = true;
    const response = await fetch(apiUrl("/reconcile/review/finalize-orphans"), {
      method: "POST",
    });
    const data = await response.json();
    finalizeBtn.disabled = false;

    finalizeResult.hidden = false;
    finalizeResult.innerHTML = "";

    if (data.report) {
      const link = document.createElement("a");
      link.href = data.report.url;
      link.className = "btn btn-secondary";
      link.textContent = "Download " + data.report.filename;
      finalizeResult.appendChild(link);
    } else {
      finalizeResult.textContent =
        "No duplicates or archive flags recorded yet — nothing to report.";
    }
  });

  loadOrphans();
}
