/**
 * JUST Assistant — Admin UI (vanilla JS)
 * APIs: /api/admin/* and /api/redis/*
 */
(function () {
  "use strict";

  var API = "/api/admin";
  var ROOT = "";

  var charts = { daily: null, source: null, topics: null };
  var analyticsCache = null;

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }
  function $all(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function getTheme() {
    return localStorage.getItem("admin-theme") || "light";
  }
  function setTheme(t) {
    localStorage.setItem("admin-theme", t);
    document.documentElement.classList.toggle("dark", t === "dark");
  }

  function api(path, opts) {
    return fetch(API + path, opts).then(function (res) {
      if (!res.ok) throw new Error(res.statusText);
      return res.json();
    });
  }
  function apiRoot(path, opts) {
    return fetch(ROOT + path, opts).then(function (res) {
      if (!res.ok) throw new Error(res.statusText);
      return res.json();
    });
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function chartDefaults() {
    var dark = document.documentElement.classList.contains("dark");
    return {
      fg: dark ? "#94a3b8" : "#64748b",
      grid: dark ? "#1f2937" : "#e2e8f0",
    };
  }
  function destroyChart(key) {
    if (charts[key]) {
      charts[key].destroy();
      charts[key] = null;
    }
  }

  var titles = {
    overview: "Overview",
    analytics: "Question analytics",
    "redis-quick": "Redis (quick)",
    "redis-detail": "Redis (detail)",
    activity: "Activity & logs",
    settings: "Settings",
  };

  function showView(name) {
    $all(".view").forEach(function (v) {
      v.classList.toggle("view--active", v.getAttribute("data-view") === name);
    });
    $all(".nav-item").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-view") === name);
    });
    $("#page-title").textContent = titles[name] || name;
    if (name === "overview") loadOverview();
    if (name === "analytics") loadAnalytics();
    if (name === "redis-quick") loadRedisQuick();
    if (name === "redis-detail") loadRedisDetail();
    if (name === "activity") loadActivity();
    if (name === "settings") loadSettings();
  }

  function loadOverview() {
    api("/overview").then(function (d) {
      var q = d.queries || {};
      var r = d.redis || {};
      var a = d.assistant || {};
      var hit = q.cacheHitRatePct;
      var hitStr = hit == null ? "—" : hit + "%";
      $("#metric-cards").innerHTML = [
        metricCard("Recorded queries (window)", q.totalRecorded != null ? q.totalRecorded : "—", "Last 30 days"),
        metricCard("Last 24 hours", q.last24h != null ? q.last24h : "—", "Queries"),
        metricCard("Last 7 days", q.last7d != null ? q.last7d : "—", "Queries"),
        metricCard("Cache answer rate", hitStr, "Redis vs live lookup"),
        metricCard("data:* keys", r.dataKeys != null ? r.dataKeys : "—", "In Redis"),
        metricCard("Embedding vectors", r.embeddingVectors != null ? r.embeddingVectors : "—", "emb:*"),
      ].join("");

      var integ = $("#integration-status");
      integ.innerHTML = [
        liCheck("Redis", r.connected),
        liCheck("OpenAI", a.openaiConfigured),
        liCheck("Embeddings", a.embeddingsConfigured),
        "<li>Similarity threshold: <code>" + escapeHtml(String(a.similarityThreshold)) + "</code></li>",
      ].join("");
    });
    api("/insights").then(function (d) {
      $("#insight-summary").textContent = d.summary || "—";
      var ul = $("#insight-list");
      var parts = [];
      (d.topQuestions || []).slice(0, 5).forEach(function (x, i) {
        parts.push("<li><strong>" + (i + 1) + ".</strong> " + escapeHtml(x.question) + " <span class='muted'>(" + x.count + ")</span></li>");
      });
      (d.anomalies || []).forEach(function (a) {
        parts.push("<li class='warn'>" + escapeHtml(a.detail) + "</li>");
      });
      ul.innerHTML = parts.length ? parts.join("") : "<li class='muted'>No data yet.</li>";
    });
  }

  function metricCard(title, value, sub) {
    return (
      '<div class="metric-card"><h4>' +
      escapeHtml(title) +
      '</h4><div class="metric-value">' +
      escapeHtml(String(value)) +
      '</div><div class="muted small">' +
      escapeHtml(sub) +
      "</div></div>"
    );
  }
  function liCheck(label, ok) {
    return (
      "<li>" +
      (ok ? '<span class="ok">✓</span> ' : '<span class="bad">✗</span> ') +
      escapeHtml(label) +
      "</li>"
    );
  }

  function loadAnalytics() {
    var range = $("#analytics-range").value;
    api("/analytics?range=" + encodeURIComponent(range)).then(function (d) {
      analyticsCache = d;
      var c = chartDefaults();

      destroyChart("daily");
      var ctxD = document.getElementById("chart-daily");
      if (ctxD && typeof Chart !== "undefined") {
        var vol = d.dailyVolume || [];
        charts.daily = new Chart(ctxD, {
          type: "line",
          data: {
            labels: vol.map(function (x) {
              return x.date;
            }),
            datasets: [
              {
                label: "Query count",
                data: vol.map(function (x) {
                  return x.count;
                }),
                borderColor: "#2563eb",
                backgroundColor: "rgba(37,99,235,0.15)",
                fill: true,
                tension: 0.25,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { labels: { color: c.fg } } },
            scales: {
              x: { ticks: { color: c.fg }, grid: { color: c.grid } },
              y: { ticks: { color: c.fg }, grid: { color: c.grid }, beginAtZero: true },
            },
          },
        });
      }

      destroyChart("source");
      var ctxS = document.getElementById("chart-source");
      var sp = d.sourceSplit || { redis: 0, live_web: 0 };
      if (ctxS && typeof Chart !== "undefined") {
        charts.source = new Chart(ctxS, {
          type: "doughnut",
          data: {
            labels: ["From Redis (cache)", "Live lookup (live_web)"],
            datasets: [
              {
                data: [sp.redis || 0, sp.live_web || 0],
                backgroundColor: ["#16a34a", "#ca8a04"],
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { labels: { color: c.fg } } },
          },
        });
      }

      destroyChart("topics");
      var ctxT = document.getElementById("chart-topics");
      var tt = (d.topTopics || []).slice(0, 12);
      if (ctxT && typeof Chart !== "undefined" && tt.length) {
        charts.topics = new Chart(ctxT, {
          type: "bar",
          data: {
            labels: tt.map(function (x) {
              return x.topic.length > 40 ? x.topic.slice(0, 40) + "…" : x.topic;
            }),
            datasets: [
              {
                label: "Count",
                data: tt.map(function (x) {
                  return x.count;
                }),
                backgroundColor: "#7c3aed",
              },
            ],
          },
          options: {
            indexAxis: "y",
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
              x: { ticks: { color: c.fg }, grid: { color: c.grid }, beginAtZero: true },
              y: { ticks: { color: c.fg }, grid: { color: c.grid } },
            },
          },
        });
      }

      var tbq = $("#table-top-q tbody");
      tbq.innerHTML = (d.topQuestions || [])
        .map(function (row) {
          return "<tr><td>" + escapeHtml(row.question) + "</td><td>" + row.count + "</td></tr>";
        })
        .join("");

      var tbt = $("#table-top-t tbody");
      tbt.innerHTML = (d.topTopics || [])
        .map(function (row) {
          return "<tr><td dir='ltr'>" + escapeHtml(row.topic) + "</td><td>" + row.count + "</td></tr>";
        })
        .join("");
    });
  }

  function downloadCsv() {
    if (!analyticsCache) return;
    var lines = [];
    lines.push("# JUST Assistant analytics export");
    lines.push("totalQueries," + (analyticsCache.totalQueries || 0));
    lines.push("queriesLast24h," + (analyticsCache.queriesLast24h || 0));
    lines.push("cacheHitRatePct," + (analyticsCache.cacheHitRate != null ? analyticsCache.cacheHitRate : ""));
    lines.push("");
    lines.push("date,daily_query_count");
    (analyticsCache.dailyVolume || []).forEach(function (x) {
      lines.push(x.date + "," + x.count);
    });
    lines.push("");
    lines.push("rank,question,count");
    (analyticsCache.topQuestions || []).forEach(function (q, i) {
      lines.push((i + 1) + ',"' + String(q.question || "").replace(/"/g, '""') + '",' + (q.count || 0));
    });
    lines.push("");
    lines.push("topic,count");
    (analyticsCache.topTopics || []).forEach(function (t) {
      lines.push('"' + String(t.topic || "").replace(/"/g, '""') + '",' + (t.count || 0));
    });
    var blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "just-assistant-analytics.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function loadRedisQuick() {
    apiRoot("/api/redis/keys")
      .then(function (keys) {
        var total = [].concat(keys.data_keys || [], keys.alias_keys || [], keys.embedding_keys || [], keys.canonical_keys || []).length;
        $("#redis-quick-stats").innerHTML =
          "<span>data keys: <strong>" +
          (keys.data_keys || []).length +
          "</strong></span> · <span>alias: <strong>" +
          (keys.alias_keys || []).length +
          "</strong></span> · <span>emb: <strong>" +
          (keys.embedding_keys || []).length +
          "</strong></span>";
        return apiRoot("/api/redis/all-data");
      })
      .then(function (list) {
        var wrap = $("#redis-quick-cards");
        wrap.innerHTML = (list || [])
          .map(function (item) {
            var key = item.canonical_key || "";
            var data = item.data || {};
            var preview = "";
            try {
              preview = JSON.stringify(data).slice(0, 280);
            } catch (e) {
              preview = String(data);
            }
            return (
              '<article class="data-card"><header class="data-card-head"><span class="data-card-title">' +
              escapeHtml(key) +
              '</span></header><div class="data-card-body"><pre class="json-tiny">' +
              escapeHtml(preview) +
              (preview.length >= 280 ? "…" : "") +
              "</pre></div></article>"
            );
          })
          .join("");
        if (!list || !list.length) wrap.innerHTML = "<p class='muted'>No cached data.</p>";
      })
      .catch(function () {
        $("#redis-quick-cards").innerHTML = "<p class='muted'>Could not reach Redis.</p>";
      });
  }

  function loadRedisDetail() {
    apiRoot("/api/redis/keys").then(function (k) {
      $("#redis-keys-dump").textContent = JSON.stringify(k, null, 2);
    });
    apiRoot("/api/redis/all-data").then(function (list) {
      var tb = $("#redis-data-table tbody");
      tb.innerHTML = (list || [])
        .map(function (item) {
          var s = "";
          try {
            s = JSON.stringify(item.data).slice(0, 200);
          } catch (e) {
            s = String(item.data);
          }
          return (
            "<tr><td dir='ltr'>" +
            escapeHtml(item.canonical_key) +
            "</td><td dir='ltr'><code>" +
            escapeHtml(s) +
            "…</code></td></tr>"
          );
        })
        .join("");
    });
    apiRoot("/api/redis/aliases").then(function (map) {
      var tb = $("#redis-alias-table tbody");
      var rows = Object.keys(map || {}).map(function (canon) {
        var aliases = map[canon];
        var n = Array.isArray(aliases) ? aliases.length : 0;
        return "<tr><td dir='ltr'>" + escapeHtml(canon) + "</td><td>" + n + "</td></tr>";
      });
      tb.innerHTML = rows.join("") || "<tr><td colspan='2'>—</td></tr>";
    });
  }

  function loadActivity() {
    api("/activity").then(function (d) {
      $("#activity-feed").innerHTML = (d.items || [])
        .map(function (it) {
          return (
            '<div class="feed-item"><div><strong>' +
            escapeHtml(it.title) +
            "</strong></div><div class='muted'>" +
            escapeHtml(it.detail) +
            '</div><time>' +
            escapeHtml(it.timestamp) +
            "</time></div>"
          );
        })
        .join("");
    });
    loadLogs();
  }

  var logTimer = null;
  function loadLogs() {
    clearTimeout(logTimer);
    logTimer = setTimeout(function () {
      var q = $("#log-search").value.trim();
      var level = $("#log-level").value;
      api("/logs?q=" + encodeURIComponent(q) + "&level=" + level + "&sort=desc").then(function (d) {
        $("#log-table tbody").innerHTML = (d.logs || [])
          .map(function (row) {
            return (
              "<tr><td>" +
              escapeHtml(row.timestamp) +
              '</td><td><span class="badge-level ' +
              escapeHtml(row.level) +
              '">' +
              escapeHtml(row.level) +
              "</span></td><td class='log-msg'>" +
              escapeHtml(row.message) +
              "</td></tr>"
            );
          })
          .join("");
      });
    }, 200);
  }

  function loadSettings() {
    api("/settings").then(function (s) {
      $("#settings-panel").innerHTML =
        '<label>Site name <input id="set-site" value="' +
        escapeHtml(s.siteName || "") +
        '" /></label>' +
        '<p class="muted">Integrations:</p><ul>' +
        (s.integrations || [])
          .map(function (i) {
            return "<li>" + escapeHtml(i.name) + ": " + (i.connected ? "Connected" : "Disconnected") + "</li>";
          })
          .join("") +
        '</ul><button type="button" class="btn btn-primary" id="btn-save-settings">Save</button>';
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    setTheme(getTheme());
    $("#btn-theme").addEventListener("click", function () {
      var next = document.documentElement.classList.contains("dark") ? "light" : "dark";
      setTheme(next);
      if ($("#view-analytics").classList.contains("view--active")) loadAnalytics();
    });

    $("#main-nav").addEventListener("click", function (e) {
      var btn = e.target.closest(".nav-item");
      if (!btn) return;
      showView(btn.getAttribute("data-view"));
      var appEl = document.querySelector(".app");
      if (appEl && window.matchMedia("(max-width: 768px)").matches) {
        appEl.classList.remove("sidebar-open");
      }
    });
    var menuBtn = $("#btn-menu");
    var appEl = document.querySelector(".app");
    var sidebarBackdrop = $("#sidebarBackdrop");
    if (menuBtn && appEl) {
      menuBtn.addEventListener("click", function () {
        appEl.classList.toggle("sidebar-open");
      });
    }
    if (sidebarBackdrop && appEl) {
      sidebarBackdrop.addEventListener("click", function () {
        appEl.classList.remove("sidebar-open");
      });
    }

    $("#analytics-range").addEventListener("change", loadAnalytics);
    $("#btn-export-csv").addEventListener("click", downloadCsv);

    $("#log-search").addEventListener("input", loadLogs);
    $("#log-level").addEventListener("change", loadLogs);

    $("#redis-tabs").addEventListener("click", function (e) {
      var tab = e.target.closest(".tab");
      if (!tab) return;
      var name = tab.getAttribute("data-tab");
      $all(".tab").forEach(function (t) {
        t.classList.toggle("active", t === tab);
      });
      $all(".tab-panel").forEach(function (p) {
        p.classList.toggle("active", p.id === "panel-" + name);
      });
    });

    document.body.addEventListener("click", function (e) {
      if (e.target.id === "btn-save-settings") {
        var site = $("#set-site");
        if (!site) return;
        api("/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ siteName: site.value }),
        }).then(function () {
          alert("Saved.");
        });
      }
      if (e.target.id === "btn-clear-redis") {
        if (!confirm("Clear all Redis assistant data?")) return;
        apiRoot("/api/redis/clear", { method: "DELETE" })
          .then(function () {
            alert("Cache cleared.");
            loadOverview();
          })
          .catch(function (err) {
            alert(err.message);
          });
      }
    });

    setInterval(function () {
      if ($("#view-activity").classList.contains("view--active")) {
        loadActivity();
      }
    }, 20000);

    showView("overview");
  });
})();
