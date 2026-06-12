/**
 * timer_widget.js  —  Vanilla JS for the Time Tracker.
 *
 * No Alpine.js dependency (Horilla's bundle does not ship an Alpine runtime).
 * Handles:
 *   - Live ticking counters (navbar widget + tracker-page bar)
 *   - Clockify-style bar: project picker, tags picker, billable toggle
 *   - Re-initialisation after every HTMX swap
 */
(function () {
  "use strict";

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function formatHMS(totalSeconds) {
    if (totalSeconds < 0) totalSeconds = 0;
    var h = Math.floor(totalSeconds / 3600);
    var m = Math.floor((totalSeconds % 3600) / 60);
    var s = totalSeconds % 60;
    return pad(h) + ":" + pad(m) + ":" + pad(s);
  }

  function escapeHtml(str) {
    var d = document.createElement("div");
    d.textContent = str == null ? "" : String(str);
    return d.innerHTML;
  }

  /* ---------------------------------------------------------------------
   * Live ticking counters: any element with [data-tt-counter] and a
   * data-started-at ISO timestamp ticks every second.
   * ------------------------------------------------------------------- */
  function initCounters(root) {
    var els = (root || document).querySelectorAll("[data-tt-counter]");
    els.forEach(function (el) {
      if (el._ttTimer) return; // already ticking
      var startedAt = el.getAttribute("data-started-at");
      if (!startedAt) return;
      var start = new Date(startedAt).getTime();
      if (isNaN(start)) return;
      var render = function () {
        var elapsed = Math.floor((Date.now() - start) / 1000);
        el.textContent = formatHMS(elapsed);
      };
      render();
      el._ttTimer = setInterval(render, 1000);
    });
  }

  /* ---------------------------------------------------------------------
   * Clockify-style tracker bar interactions (stopped state only).
   * ------------------------------------------------------------------- */
  function initTrackerBar() {
    var bar = document.getElementById("tt-tracker-bar");
    if (!bar || bar.dataset.ttInit === "1") return;
    bar.dataset.ttInit = "1";

    // ---- Project picker ----
    var pBtn = bar.querySelector("#tt-project-btn");
    var pDrop = bar.querySelector("#tt-project-dropdown");
    var pHidden = bar.querySelector("#tt-project-id");
    var pLabel = bar.querySelector("#tt-project-label");
    var pSearch = bar.querySelector("#tt-project-search");

    if (pBtn && pDrop) {
      pBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        closeAllDropdowns(bar, pDrop);
        pDrop.classList.toggle("tt-open");
        if (pSearch) {
          pSearch.value = "";
          filterProjects("");
          pSearch.focus();
        }
      });

      bar.querySelectorAll("[data-tt-project]").forEach(function (item) {
        item.addEventListener("click", function () {
          var id = item.getAttribute("data-id") || "";
          var title = item.getAttribute("data-title") || "";
          var color = item.getAttribute("data-color") || "#6366f1";
          if (pHidden) pHidden.value = id;
          if (pLabel) {
            if (id) {
              pLabel.className = "tt-bar__project-sel";
              pLabel.innerHTML =
                '<span class="tt-dot" style="background:' +
                escapeHtml(color) +
                '"></span> ' +
                escapeHtml(title);
            } else {
              pLabel.className = "tt-bar__project-add";
              pLabel.innerHTML =
                '<ion-icon name="add-circle-outline"></ion-icon> Project';
            }
          }
          pDrop.classList.remove("tt-open");
        });
      });
    }

    function filterProjects(q) {
      q = (q || "").toLowerCase().trim();
      bar.querySelectorAll("[data-tt-project]").forEach(function (item) {
        if (!item.getAttribute("data-id")) return; // keep "No project"
        var title = (item.getAttribute("data-title") || "").toLowerCase();
        item.style.display = title.indexOf(q) !== -1 ? "" : "none";
      });
    }
    if (pSearch) {
      pSearch.addEventListener("input", function () {
        filterProjects(pSearch.value);
      });
      pSearch.addEventListener("click", function (e) {
        e.stopPropagation();
      });
    }

    // ---- Tags picker ----
    var tBtn = bar.querySelector("#tt-tags-btn");
    var tDrop = bar.querySelector("#tt-tags-dropdown");
    var tHidden = bar.querySelector("#tt-tags-hidden");
    var tCount = bar.querySelector("#tt-tags-count");

    if (tBtn && tDrop) {
      tBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        closeAllDropdowns(bar, tDrop);
        tDrop.classList.toggle("tt-open");
      });
      tDrop.addEventListener("click", function (e) {
        e.stopPropagation();
      });

      var syncTags = function () {
        var checked = bar.querySelectorAll("[data-tt-tag]:checked");
        if (tHidden) {
          tHidden.innerHTML = "";
          checked.forEach(function (cb) {
            var inp = document.createElement("input");
            inp.type = "hidden";
            inp.name = "tag_ids";
            inp.value = cb.value;
            tHidden.appendChild(inp);
          });
        }
        if (tCount) {
          if (checked.length) {
            tCount.textContent = checked.length;
            tCount.style.display = "";
            tBtn.classList.add("tt-bar__icon--active");
          } else {
            tCount.style.display = "none";
            tBtn.classList.remove("tt-bar__icon--active");
          }
        }
      };
      bar.querySelectorAll("[data-tt-tag]").forEach(function (cb) {
        cb.addEventListener("change", syncTags);
      });
      syncTags();
    }

    // ---- Billable toggle ----
    var bBtn = bar.querySelector("#tt-billable-btn");
    var bHidden = bar.querySelector("#tt-billable");
    if (bBtn && bHidden) {
      bBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        var active = bHidden.value === "on";
        bHidden.value = active ? "" : "on";
        bBtn.classList.toggle("tt-bar__icon--active", !active);
      });
    }

    // ---- Close dropdowns on outside click (bind once) ----
    if (!document._ttOutsideBound) {
      document._ttOutsideBound = true;
      document.addEventListener("click", function (e) {
        var b = document.getElementById("tt-tracker-bar");
        if (!b) return;
        // Ignore clicks inside a picker field (button/dropdown handle themselves)
        if (e.target.closest && e.target.closest(".tt-bar__field")) return;
        b.querySelectorAll(".tt-bar__dropdown.tt-open").forEach(function (d) {
          d.classList.remove("tt-open");
        });
      });
    }
  }

  function closeAllDropdowns(bar, except) {
    bar.querySelectorAll(".tt-bar__dropdown.tt-open").forEach(function (d) {
      if (d !== except) d.classList.remove("tt-open");
    });
  }

  /* ---------------------------------------------------------------------
   * Per-row project colour dots — paint the dot next to each row's project
   * <select> from the selected option's data-color, and keep it in sync.
   * ------------------------------------------------------------------- */
  function initRowDots(root) {
    (root || document)
      .querySelectorAll("select[data-tt-projsel]")
      .forEach(function (sel) {
        if (sel._ttDot) return;
        sel._ttDot = true;
        var dot =
          sel.parentElement &&
          sel.parentElement.querySelector("[data-tt-dot]");
        var paint = function () {
          if (!dot) return;
          var o = sel.options[sel.selectedIndex];
          dot.style.background =
            (o && o.getAttribute("data-color")) || "#cbd5e1";
        };
        paint();
        sel.addEventListener("change", paint);
      });
  }

  /* ---------------------------------------------------------------------
   * Boot + re-init after HTMX swaps.
   * ------------------------------------------------------------------- */
  function initAll(root) {
    initCounters(root);
    initTrackerBar();
    initRowDots(root);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initAll(document);
    });
  } else {
    initAll(document);
  }

  // After any HTMX swap, re-scan the WHOLE document. Scanning evt.target is
  // unreliable for outerHTML swaps (the new node may not be the event target),
  // which would leave a freshly-swapped running timer counter frozen.
  document.body.addEventListener("htmx:afterSwap", function () {
    initAll(document);
  });
  document.body.addEventListener("htmx:load", function () {
    initAll(document);
  });
})();

/* ==========================================================================
 * Phase 2: Idle Detection + Break controls
 * ========================================================================== */
(function () {
  "use strict";

  var IDLE_TIMEOUT_MS = (window.TT_IDLE_TIMEOUT_MINUTES || 10) * 60 * 1000;
  var HEARTBEAT_INTERVAL_MS = 30000;
  var lastActivity = Date.now();
  var idleModalShown = false;
  var idleStart = null;
  var heartbeatTimer = null;

  function getCsrf() {
    var el = document.querySelector("[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  function timerRunning() {
    return !!document.querySelector("[data-tt-counter]");
  }

  // ---- Activity tracking ----
  function resetActivity() {
    lastActivity = Date.now();
    if (idleModalShown) return;
  }

  ["mousemove", "keydown", "click", "touchstart", "scroll"].forEach(function (ev) {
    document.addEventListener(ev, resetActivity, { passive: true });
  });

  // ---- Heartbeat ----
  function sendHeartbeat() {
    if (!timerRunning()) return;
    fetch("/time-tracker/timer/heartbeat/", {
      method: "POST",
      headers: { "X-CSRFToken": getCsrf() },
    });
  }

  function startHeartbeat() {
    if (heartbeatTimer) return;
    heartbeatTimer = setInterval(function () {
      if (!timerRunning()) { stopHeartbeat(); return; }
      var idle = Date.now() - lastActivity;
      if (idle < IDLE_TIMEOUT_MS) {
        sendHeartbeat();
      } else if (!idleModalShown) {
        idleStart = lastActivity + IDLE_TIMEOUT_MS;
        showIdleModal();
      }
    }, HEARTBEAT_INTERVAL_MS);
  }

  function stopHeartbeat() {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }

  // ---- Idle modal ----
  function showIdleModal() {
    if (idleModalShown) return;
    idleModalShown = true;

    var idleSecs = Math.round((Date.now() - idleStart) / 1000);
    var modal = document.createElement("div");
    modal.id = "tt-idle-modal";
    modal.innerHTML = [
      '<div style="position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:9999;display:flex;align-items:center;justify-content:center;">',
      '<div style="background:#fff;border-radius:14px;padding:2rem;max-width:400px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,.2);text-align:center;">',
      '<div style="font-size:2rem;margin-bottom:.5rem;">⏸️</div>',
      '<h3 style="margin:.5rem 0;font-size:1.1rem;font-weight:700;color:#1e293b;">You\'ve been idle</h3>',
      '<p style="color:#64748b;font-size:.875rem;margin:.5rem 0 1.25rem;">',
      'You\'ve been inactive for <strong id="tt-idle-dur">–</strong>. What do you want to do with this time?',
      '</p>',
      '<div style="display:flex;gap:.75rem;justify-content:center;flex-wrap:wrap;">',
      '<button id="tt-idle-keep" style="padding:.6rem 1.25rem;background:#6366f1;color:#fff;border:none;border-radius:8px;font-weight:600;cursor:pointer;font-size:.875rem;">Keep time</button>',
      '<button id="tt-idle-discard" style="padding:.6rem 1.25rem;background:#f1f5f9;color:#374151;border:none;border-radius:8px;font-weight:600;cursor:pointer;font-size:.875rem;">Discard idle time</button>',
      '<button id="tt-idle-stop" style="padding:.6rem 1.25rem;background:#fee2e2;color:#991b1b;border:none;border-radius:8px;font-weight:600;cursor:pointer;font-size:.875rem;">Stop timer</button>',
      '</div></div></div>',
    ].join("");
    document.body.appendChild(modal);

    var durEl = modal.querySelector("#tt-idle-dur");
    function updateDur() {
      var s = Math.round((Date.now() - idleStart) / 1000);
      var m = Math.floor(s / 60); var sec = s % 60;
      durEl.textContent = (m > 0 ? m + "m " : "") + sec + "s";
    }
    updateDur();
    var durTimer = setInterval(updateDur, 1000);

    function dismiss(action) {
      clearInterval(durTimer);
      modal.remove();
      idleModalShown = false;
      lastActivity = Date.now();

      if (action === "keep") {
        sendHeartbeat();
        return;
      }
      if (action === "discard") {
        // Start an idle break from idleStart, end now
        fetch("/time-tracker/breaks/start/", {
          method: "POST",
          headers: { "X-CSRFToken": getCsrf(), "Content-Type": "application/x-www-form-urlencoded" },
          body: "break_type=idle",
        }).then(function () {
          return fetch("/time-tracker/breaks/stop/", {
            method: "POST",
            headers: { "X-CSRFToken": getCsrf() },
          });
        });
        return;
      }
      if (action === "stop") {
        var stopBtn = document.querySelector("[data-tt-stop-btn], form[data-tt-stop] button[type=submit]");
        if (stopBtn) stopBtn.click();
      }
    }

    modal.querySelector("#tt-idle-keep").onclick = function () { dismiss("keep"); };
    modal.querySelector("#tt-idle-discard").onclick = function () { dismiss("discard"); };
    modal.querySelector("#tt-idle-stop").onclick = function () { dismiss("stop"); };
  }

  // ---- Break UI ----
  function initBreakControls() {
    var startBtn = document.getElementById("tt-break-start-btn");
    var stopBtn  = document.getElementById("tt-break-stop-btn");
    var breakInfo = document.getElementById("tt-break-info");

    if (startBtn) {
      startBtn.addEventListener("click", function () {
        fetch("/time-tracker/breaks/start/", {
          method: "POST",
          headers: { "X-CSRFToken": getCsrf(), "Content-Type": "application/x-www-form-urlencoded" },
          body: "break_type=manual",
        }).then(function (r) { return r.json(); }).then(function () {
          if (startBtn) startBtn.style.display = "none";
          if (stopBtn)  stopBtn.style.display  = "";
          if (breakInfo) breakInfo.style.display = "";
        });
      });
    }

    if (stopBtn) {
      stopBtn.addEventListener("click", function () {
        fetch("/time-tracker/breaks/stop/", {
          method: "POST",
          headers: { "X-CSRFToken": getCsrf() },
        }).then(function (r) { return r.json(); }).then(function (data) {
          if (startBtn) startBtn.style.display = "";
          if (stopBtn)  stopBtn.style.display  = "none";
          if (breakInfo) {
            breakInfo.textContent = "Break: " + formatHMS(data.duration_seconds || 0);
          }
        });
      });
    }
  }

  function pad2(n) { return String(n).padStart(2, "0"); }
  function formatHMS(s) {
    s = Math.max(0, s | 0);
    return pad2(s / 3600 | 0) + ":" + pad2((s % 3600) / 60 | 0) + ":" + pad2(s % 60);
  }

  // ---- Boot ----
  function boot() {
    if (timerRunning()) startHeartbeat();
    initBreakControls();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  document.body.addEventListener("htmx:afterSwap", function () {
    if (timerRunning()) startHeartbeat();
    else stopHeartbeat();
    initBreakControls();
  });
})();
