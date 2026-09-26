(function () {
  "use strict";

  function text(value) { return value === null || value === undefined ? "" : String(value); }

  function prettyDetails(value) {
    var output;
    try { output = JSON.stringify(value, null, 2); } catch (_) { output = "[unavailable]"; }
    return output === undefined ? "" : output;
  }

  function localTime(value, timezone) {
    if (!value) return "unknown time";
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return text(value);
    try {
      return new Intl.DateTimeFormat(document.documentElement.lang || undefined, { dateStyle: "medium", timeStyle: "medium", timeZone: timezone }).format(date);
    } catch (_) { return date.toLocaleString(); }
  }

  function auditFilterModel(filters) {
    var model = {};
    (filters || []).forEach(function (filter) {
      if (!filter || !filter.field || filter.value === undefined || filter.value === null || filter.value === "") return;
      var operators = { equal: "equals", notEqual: "notEqual", starts: "startsWith", ends: "endsWith", empty: "blank", notEmpty: "notBlank", in: "set" };
      model[filter.field] = { filterType: "text", type: operators[filter.type] || "contains", filter: filter.value };
    });
    return model;
  }

  function auditSortModel(sorters) {
    return (sorters || []).map(function (sorter) {
      return { colId: sorter.field, sort: sorter.dir === "asc" ? "asc" : "desc" };
    });
  }

  function init() {
    var app = document.getElementById("persistentlogger-app");
    if (!app || typeof window.Tabulator === "undefined") return;
    var gridHost = document.getElementById("persistentlogger-grid");
    var status = document.getElementById("persistentlogger-status");
    var empty = document.getElementById("persistentlogger-empty");
    var error = document.getElementById("persistentlogger-error");
    var search = document.getElementById("persistentlogger-quick");
    var detailsDialog = document.getElementById("persistentlogger-details-dialog");
    var detailsContent = document.getElementById("persistentlogger-details-content");
    var detailsClose = document.getElementById("persistentlogger-details-close");
    var detailsFooterClose = document.getElementById("persistentlogger-details-footer-close");
    var detailsCopy = document.getElementById("persistentlogger-details-copy");
    var detailsEvent = document.getElementById("persistentlogger-details-event");
    var detailsActor = document.getElementById("persistentlogger-details-actor");
    var detailsCreated = document.getElementById("persistentlogger-details-created");
    var exportJson = document.getElementById("persistentlogger-export-json");
    var exportCsv = document.getElementById("persistentlogger-export-csv");
    var endpoint = app.getAttribute("data-endpoint");
    var exportEndpoint = app.getAttribute("data-export-endpoint");
    var timezone = app.getAttribute("data-timezone") || "UTC";
    var lastTotal = 0;
    var searchTimer = null;

    function visible(element, value) { if (value) element.removeAttribute("hidden"); else element.setAttribute("hidden", "hidden"); }
    function setState(message, isError) { status.textContent = message; visible(error, isError); if (isError) error.textContent = message; }
    function updateEmptyState() {
      empty.textContent = search.value.trim() ? "No matching data found" : "No audit events match this stream.";
      visible(empty, lastTotal === 0);
    }
    function requestUrl(params) {
      var page = Number(params.page) || 1;
      var size = Number(params.size) || 25;
      var request = {
        startRow: (page - 1) * size,
        endRow: page * size,
        quick: search.value.trim(),
        sortModel: auditSortModel(params.sorters),
        filterModel: auditFilterModel(params.filters)
      };
      var query = new URLSearchParams({
        startRow: String(request.startRow),
        endRow: String(request.endRow),
        sortModel: JSON.stringify(request.sortModel),
        filterModel: JSON.stringify(request.filterModel)
      });
      if (request.quick) query.set("quick", request.quick);
      return endpoint + "?" + query.toString();
    }
    function openDetails(row) {
      detailsEvent.textContent = text(row.event_type) || "Unclassified event";
      detailsActor.textContent = "Actor · " + (text(row.actor) || "system");
      detailsCreated.textContent = "Recorded · " + localTime(row.created_at, timezone);
      detailsContent.textContent = prettyDetails(row.details);
      if (typeof detailsDialog.showModal === "function") detailsDialog.showModal(); else detailsDialog.setAttribute("open", "open");
      detailsClose.focus();
    }
    function closeDetails() { if (typeof detailsDialog.close === "function") detailsDialog.close(); else detailsDialog.removeAttribute("open"); }
    function copyDetails() {
      var value = detailsContent.textContent;
      if (!navigator.clipboard || !navigator.clipboard.writeText) { detailsCopy.textContent = "Select JSON to copy"; return; }
      navigator.clipboard.writeText(value).then(function () {
        detailsCopy.textContent = "Copied";
        window.setTimeout(function () { detailsCopy.textContent = "Copy JSON"; }, 1400);
      }, function () { detailsCopy.textContent = "Copy unavailable"; });
    }
    function exportEvents(format) {
      var params = new URLSearchParams({ format: format });
      if (search.value) params.set("quick", search.value);
      window.location.href = exportEndpoint + "?" + params.toString();
    }

    var columns = [
      { title: "Recorded", field: "created_at", width: 195, sorter: "datetime", headerFilter: "input", formatter: function (cell) { return localTime(cell.getValue(), timezone); } },
      { title: "Level", field: "severity", width: 76, sorter: "string", headerFilter: "input" },
      { title: "Event", field: "event_type", width: 175, sorter: "string", headerFilter: "input" },
      { title: "Actor", field: "actor", width: 140, sorter: "string", headerFilter: "input" },
      { title: "Summary", field: "comment", minWidth: 240, widthGrow: 2, sorter: "string", headerFilter: "input" },
      { title: "Details", field: "details", width: 70, hozAlign: "center", headerSort: false, formatter: function (cell) {
        var row = cell.getRow().getData();
        return '<button type="button" class="persistentlogger-details-button" data-event-id="' + text(row.event_id) + '" aria-label="View details" title="View details"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M2 12s3.6-5.2 10-5.2S22 12 22 12s-3.6 5.2-10 5.2S2 12 2 12Zm10 2.7a2.7 2.7 0 1 0 0-5.4 2.7 2.7 0 0 0 0 5.4Z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>';
      } }
    ];

    var table;
    try {
      table = new window.Tabulator(gridHost, {
        index: "event_id",
        layout: "fitColumns",
        height: "336px",
        rowHeight: 36,
        placeholder: "",
        movableColumns: false,
        pagination: true,
        paginationMode: "remote",
        paginationSize: 25,
        paginationSizeSelector: [10, 25, 50, 100],
        paginationCounter: "rows",
        sortMode: "remote",
        filterMode: "remote",
        ajaxURL: endpoint,
        ajaxURLGenerator: function (url, _config, params) { return requestUrl(params); },
        ajaxConfig: { method: "GET", credentials: "same-origin", headers: { Accept: "application/json" } },
        ajaxResponse: function (_url, params, response) {
          var rows = Array.isArray(response.rows) ? response.rows : [];
          lastTotal = Number.isInteger(response.total) ? response.total : rows.length;
          setState(lastTotal + " event" + (lastTotal === 1 ? "" : "s") + " · newest first", false);
          updateEmptyState();
          return { last_page: Math.max(1, Math.ceil(lastTotal / (Number(params.size) || 25))), data: rows };
        },
        ajaxError: function (_xhr, _textStatus, errorThrown) {
          visible(empty, false);
          setState(errorThrown && errorThrown.message ? errorThrown.message : "The audit stream could not be loaded.", true);
        },
        dataLoading: function () { setState("Loading events…", false); },
        columns: columns
      });
    } catch (reason) {
      setState("The audit table could not start: " + (reason.message || "unknown grid error"), true);
      return;
    }

    search.addEventListener("input", function () {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(function () { table.setData(); }, 250);
    });
    gridHost.addEventListener("click", function (event) {
      var button = event.target.closest("[data-event-id]");
      if (!button) return;
      var row = table.getRow(button.getAttribute("data-event-id"));
      if (row) openDetails(row.getData());
    });
    document.getElementById("persistentlogger-refresh").addEventListener("click", function () { table.setData(); });
    exportJson.addEventListener("click", function () { exportEvents("json"); });
    exportCsv.addEventListener("click", function () { exportEvents("csv"); });
    detailsClose.addEventListener("click", closeDetails);
    detailsFooterClose.addEventListener("click", closeDetails);
    detailsCopy.addEventListener("click", copyDetails);
    detailsDialog.addEventListener("click", function (event) { if (event.target === detailsDialog) closeDetails(); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
}());
