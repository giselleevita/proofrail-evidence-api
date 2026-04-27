(function () {
  "use strict";

  var STORAGE_BASE = "proofrail_console_base_url";
  var STORAGE_KEY = "proofrail_console_api_key";

  function $(id) {
    return document.getElementById(id);
  }

  function normalizeBase(url) {
    if (!url) return "";
    return String(url).replace(/\/+$/, "");
  }

  function apiFetch(path, options) {
    var base = normalizeBase($("baseUrl").value);
    var key = $("apiKey").value.trim();
    if (!base) throw new Error("Set API base URL");
    if (!key) throw new Error("Set API key");
    var headers = Object.assign({}, (options && options.headers) || {});
    headers["x-api-key"] = key;
    if (!headers["content-type"] && options && options.body) {
      headers["content-type"] = "application/json";
    }
    var url = base + path;
    return fetch(url, Object.assign({}, options, { headers: headers }));
  }

  function idempotencyKey() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "idem-" + String(Date.now()) + "-" + String(Math.random()).slice(2);
  }

  function showErr(el, msg) {
    if (!el) return;
    if (msg) {
      el.textContent = msg;
      el.hidden = false;
    } else {
      el.textContent = "";
      el.hidden = true;
    }
  }

  var casesCursor = null;
  var currentCaseId = null;
  var currentDetail = null;

  function setView(name) {
    var nav = $("mainNav");
    var cases = $("viewCases");
    var detail = $("viewDetail");
    if (name === "detail") {
      cases.hidden = true;
      detail.hidden = false;
      nav.querySelector('[data-view="cases"]').classList.remove("active");
      nav.querySelector('[data-view="detail"]').classList.add("active");
      $("tabDetail").disabled = false;
    } else {
      detail.hidden = true;
      cases.hidden = false;
      nav.querySelector('[data-view="detail"]').classList.remove("active");
      nav.querySelector('[data-view="cases"]').classList.add("active");
    }
  }

  function loadSettings() {
    $("baseUrl").value =
      sessionStorage.getItem(STORAGE_BASE) || window.location.origin || "";
    $("apiKey").value = sessionStorage.getItem(STORAGE_KEY) || "";
  }

  function saveSettings() {
    sessionStorage.setItem(STORAGE_BASE, normalizeBase($("baseUrl").value));
    sessionStorage.setItem(STORAGE_KEY, $("apiKey").value.trim());
  }

  function renderCases(rows) {
    var tbody = $("casesBody");
    tbody.innerHTML = "";
    rows.forEach(function (c) {
      var tr = document.createElement("tr");
      tr.dataset.caseId = c.case_id;
      tr.innerHTML =
        "<td><code>" +
        escapeHtml(c.case_id) +
        "</code></td>" +
        "<td>" +
        escapeHtml(c.status) +
        "</td>" +
        "<td>" +
        escapeHtml(c.decision || "—") +
        "</td>" +
        "<td>" +
        escapeHtml(c.subject_name || "—") +
        "</td>" +
        "<td>" +
        escapeHtml(c.updated_at || "") +
        "</td>";
      tr.addEventListener("click", function () {
        openCase(c.case_id);
      });
      tbody.appendChild(tr);
    });
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function fetchCases(append) {
    showErr($("casesError"), "");
    var params = new URLSearchParams();
    var st = $("filterStatus").value;
    if (st) params.set("status", st);
    var asg = $("filterAssignee").value.trim();
    if (asg) params.set("assignee", asg);
    params.set("limit", "50");
    if (append && casesCursor) params.set("cursor", casesCursor);
    if (!append) casesCursor = null;

    var qs = params.toString();
    var path = "/v2/cases" + (qs ? "?" + qs : "");

    return apiFetch(path, { method: "GET" }).then(function (r) {
      if (!r.ok) return r.text().then(function (t) {
        throw new Error(r.status + " " + (t || r.statusText));
      });
      var next = r.headers.get("x-next-cursor");
      casesCursor = next || null;
      $("btnLoadMore").hidden = !casesCursor;
      $("casesCursorHint").hidden = !casesCursor;
      $("casesCursorHint").textContent = casesCursor
        ? "More pages available (x-next-cursor)."
        : "";
      return r.json();
    });
  }

  function loadCases(reset) {
    fetchCases(!reset)
      .then(function (data) {
        if (reset) renderCases(data);
        else {
          var tbody = $("casesBody");
          data.forEach(function (c) {
            var rows = tbody.querySelectorAll("tr");
            /* dedupe by case_id */
            var dup = false;
            for (var j = 0; j < rows.length; j++) {
              if (rows[j].dataset.caseId === c.case_id) {
                dup = true;
                break;
              }
            }
            if (!dup) {
              var tr = document.createElement("tr");
              tr.dataset.caseId = c.case_id;
              tr.innerHTML =
                "<td><code>" +
                escapeHtml(c.case_id) +
                "</code></td>" +
                "<td>" +
                escapeHtml(c.status) +
                "</td>" +
                "<td>" +
                escapeHtml(c.decision || "—") +
                "</td>" +
                "<td>" +
                escapeHtml(c.subject_name || "—") +
                "</td>" +
                "<td>" +
                escapeHtml(c.updated_at || "") +
                "</td>";
              tr.addEventListener("click", function () {
                openCase(c.case_id);
              });
              tbody.appendChild(tr);
            }
          });
        }
      })
      .catch(function (e) {
        showErr($("casesError"), e.message || String(e));
      });
  }

  function openCase(caseId) {
    currentCaseId = caseId;
    showErr($("detailError"), "");
    showErr($("decisionError"), "");
    showErr($("decisionMessage"), "");
    showErr($("eventError"), "");
    showErr($("eventMessage"), "");
    $("detailTitle").textContent = "Case " + caseId;
    setView("detail");
    apiFetch("/v2/cases/" + encodeURIComponent(caseId), { method: "GET" })
      .then(function (r) {
        if (!r.ok) return r.text().then(function (t) {
          throw new Error(r.status + " " + (t || r.statusText));
        });
        return r.json();
      })
      .then(function (body) {
        currentDetail = body;
        var c = body.case;
        $("detailSummary").innerHTML =
          "<dl>" +
          "<dt>Status</dt><dd>" +
          escapeHtml(c.status) +
          "</dd>" +
          "<dt>Screening</dt><dd><code>" +
          escapeHtml(c.screening_id) +
          "</code></dd>" +
          "<dt>Evidence pack</dt><dd><code>" +
          escapeHtml(c.evidence_pack_id) +
          "</code></dd>" +
          "<dt>Assignee</dt><dd>" +
          escapeHtml(c.assignee || "—") +
          "</dd>" +
          "<dt>Automated decision</dt><dd>" +
          escapeHtml(c.decision || "—") +
          "</dd>" +
          "</dl>";
        var ul = $("eventsList");
        ul.innerHTML = "";
        (body.events || []).forEach(function (ev) {
          var li = document.createElement("li");
          li.innerHTML =
            "<strong>" +
            escapeHtml(ev.ts) +
            "</strong> · " +
            escapeHtml(ev.event_type) +
            " · " +
            escapeHtml(ev.actor) +
            (ev.note ? "<br/>" + escapeHtml(ev.note) : "");
          ul.appendChild(li);
        });
      })
      .catch(function (e) {
        showErr($("detailError"), e.message || String(e));
      });
  }

  function downloadEvidenceJson() {
    if (!currentDetail || !currentDetail.case) return;
    var packId = currentDetail.case.evidence_pack_id;
    var path =
      "/v2/evidence-packs/" +
      encodeURIComponent(packId) +
      "/export?format=json";
    apiFetch(path, { method: "GET" })
      .then(function (r) {
        if (!r.ok) return r.text().then(function (t) {
          throw new Error(r.status + " " + (t || r.statusText));
        });
        return r.blob();
      })
      .then(function (blob) {
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "evidence-pack-" + packId + ".json";
        a.click();
        URL.revokeObjectURL(a.href);
      })
      .catch(function (e) {
        showErr($("detailError"), e.message || String(e));
      });
  }

  function submitDecision() {
    if (!currentDetail || !currentDetail.case) return;
    var screeningId = currentDetail.case.screening_id;
    var outcome = $("decisionOutcome").value;
    var note = $("decisionNote").value.trim() || null;
    var body = { outcome: outcome, note: note };
    showErr($("decisionError"), "");
    $("decisionMessage").textContent = "";
    $("decisionMessage").hidden = true;
    apiFetch("/v2/screenings/" + encodeURIComponent(screeningId) + "/decision", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "Idempotency-Key": idempotencyKey(),
      },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        if (!r.ok) return r.text().then(function (t) {
          throw new Error(r.status + " " + (t || r.statusText));
        });
        return r.json();
      })
      .then(function () {
        $("decisionMessage").textContent = "Decision recorded.";
        $("decisionMessage").hidden = false;
        openCase(currentCaseId);
      })
      .catch(function (e) {
        showErr($("decisionError"), e.message || String(e));
      });
  }

  function addComment() {
    if (!currentCaseId) return;
    var note = $("eventNote").value.trim();
    if (!note) {
      showErr($("eventError"), "Enter a note.");
      return;
    }
    showErr($("eventError"), "");
    $("eventMessage").textContent = "";
    $("eventMessage").hidden = true;
    apiFetch("/v2/cases/" + encodeURIComponent(currentCaseId) + "/events", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "Idempotency-Key": idempotencyKey(),
      },
      body: JSON.stringify({ event_type: "comment", note: note }),
    })
      .then(function (r) {
        if (!r.ok) return r.text().then(function (t) {
          throw new Error(r.status + " " + (t || r.statusText));
        });
        return r.json();
      })
      .then(function () {
        $("eventMessage").textContent = "Event added.";
        $("eventMessage").hidden = false;
        $("eventNote").value = "";
        openCase(currentCaseId);
      })
      .catch(function (e) {
        showErr($("eventError"), e.message || String(e));
      });
  }

  function wire() {
    $("btnSave").addEventListener("click", function () {
      showErr($("settingsError"), "");
      try {
        saveSettings();
        $("mainNav").hidden = false;
        $("viewCases").hidden = false;
        loadCases(true);
      } catch (e) {
        showErr($("settingsError"), e.message || String(e));
      }
    });

    $("btnClearKey").addEventListener("click", function () {
      $("apiKey").value = "";
      sessionStorage.removeItem(STORAGE_KEY);
    });

    $("btnRefreshCases").addEventListener("click", function () {
      loadCases(true);
    });

    $("btnLoadMore").addEventListener("click", function () {
      loadCases(false);
    });

    $("btnBackCases").addEventListener("click", function () {
      setView("cases");
    });

    $("btnExportJson").addEventListener("click", downloadEvidenceJson);
    $("btnSubmitDecision").addEventListener("click", submitDecision);
    $("btnAddComment").addEventListener("click", addComment);

    document.querySelectorAll(".tabs [data-view]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (btn.dataset.view === "detail" && !currentCaseId) return;
        setView(btn.dataset.view);
      });
    });
  }

  loadSettings();
  wire();

  if (sessionStorage.getItem(STORAGE_KEY)) {
    $("mainNav").hidden = false;
    $("viewCases").hidden = false;
    loadCases(true);
  }
})();
