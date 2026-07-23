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
