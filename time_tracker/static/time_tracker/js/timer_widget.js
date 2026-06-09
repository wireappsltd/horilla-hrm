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
