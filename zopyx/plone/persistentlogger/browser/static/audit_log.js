(function () {
  "use strict";

  function init() {
    var app = document.getElementById("persistentlogger-app");
    if (!app) return;
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
    var gridApi = null;

    function text(value) { return value === null || value === undefined ? "" : String(value); }
    function details(value) {
      var output;
      try { output = JSON.stringify(value); } catch (_) { output = "[unavailable]"; }
      if (output === undefined) output = "";
      return output.length > 420 ? output.slice(0, 420) + "…" : output;
    }
    function prettyDetails(value) {
      var output;
      try { output = JSON.stringify(value, null, 2); } catch (_) { output = "[unavailable]"; }
      return output === undefined ? "" : output;
    }
    function localTime(value) {
      if (!value) return "unknown time";
      var date = new Date(value);
      if (Number.isNaN(date.getTime())) return text(value);
      try {
        return new Intl.DateTimeFormat(document.documentElement.lang || undefined, {
          dateStyle: "medium",
          timeStyle: "medium",
          timeZone: timezone
        }).format(date);
      } catch (_) {
        return date.toLocaleString();
      }
    }
    function openDetails(row) {
      detailsEvent.textContent = text(row.event_type) || "Unclassified event";
      detailsActor.textContent = "Actor · " + (text(row.actor) || "system");
      detailsCreated.textContent = "Recorded · " + localTime(row.created_at);
      detailsContent.textContent = prettyDetails(row.details);
      if (typeof detailsDialog.showModal === "function") {
        detailsDialog.showModal();
      } else {
        detailsDialog.setAttribute("open", "open");
      }
      detailsClose.focus();
    }
    function copyDetails() {
      var value = detailsContent.textContent;
      if (!navigator.clipboard || !navigator.clipboard.writeText) {
        detailsCopy.textContent = "Select JSON to copy";
        return;
      }
      navigator.clipboard.writeText(value).then(function () {
        detailsCopy.textContent = "Copied";
        window.setTimeout(function () { detailsCopy.textContent = "Copy JSON"; }, 1400);
      }, function () {
        detailsCopy.textContent = "Copy unavailable";
      });
    }
    function closeDetails() {
      if (typeof detailsDialog.close === "function") {
        detailsDialog.close();
      } else {
        detailsDialog.removeAttribute("open");
      }
    }
    function visible(element, value) {
      if (value) element.removeAttribute("hidden");
      else element.setAttribute("hidden", "hidden");
    }
    function setState(message, isError) {
      status.textContent = message;
      visible(error, isError);
      if (isError) error.textContent = message;
    }
    function render(rows) {
      visible(empty, rows.length === 0);
      if (gridApi) gridApi.setGridOption("rowData", rows);
    }
    function load() {
      setState("Loading events…", false);
      fetch(endpoint, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (response) { if (!response.ok) throw new Error("The audit stream could not be loaded."); return response.json(); })
        .then(function (payload) { var rows = Array.isArray(payload.rows) ? payload.rows : []; render(rows); setState(rows.length + " event" + (rows.length === 1 ? "" : "s") + " · newest first", false); })
        .catch(function (reason) { render([]); setState(reason.message || "The audit stream could not be loaded.", true); });
    }
    function exportEvents(format) {
      var params = new URLSearchParams({ format: format });
      if (search.value) params.set("quick", search.value);
      window.location.href = exportEndpoint + "?" + params.toString();
    }
    var columnDefs = [
      { field: "created_at", headerName: "Recorded", minWidth: 210, sort: "desc", valueFormatter: function (params) { return localTime(params.value); } },
      { field: "severity", headerName: "Level", width: 110 },
      { field: "event_type", headerName: "Event", minWidth: 200, flex: 1 },
      { field: "actor", headerName: "Actor", minWidth: 150 },
      { field: "comment", headerName: "Summary", minWidth: 260, flex: 1 },
      { field: "details", headerName: "Details", minWidth: 150, cellRenderer: function (params) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "persistentlogger-details-button";
        button.setAttribute("aria-label", "View details");
        button.title = "View details";
        button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M2 12s3.6-5.2 10-5.2S22 12 22 12s-3.6 5.2-10 5.2S2 12 2 12Zm10 2.7a2.7 2.7 0 1 0 0-5.4 2.7 2.7 0 0 0 0 5.4Z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        button.addEventListener("click", function () { openDetails(params.data); });
        return button;
      } }
    ];
    if (window.agGrid && typeof window.agGrid.createGrid === "function") {
      try {
        gridApi = window.agGrid.createGrid(gridHost, { columnDefs: columnDefs, rowData: [], animateRows: false, pagination: true, paginationPageSize: 25, defaultColDef: { sortable: true, resizable: true, filter: true, wrapText: false }, getRowId: function (params) { return text(params.data.event_id); } });
      } catch (reason) {
        setState("The audit table could not start: " + (reason.message || "unknown grid error"), true);
        return;
      }
      search.addEventListener("input", function () { if (gridApi) gridApi.setGridOption("quickFilterText", search.value); });
      document.getElementById("persistentlogger-refresh").addEventListener("click", load);
      exportJson.addEventListener("click", function () { exportEvents("json"); });
      exportCsv.addEventListener("click", function () { exportEvents("csv"); });
      detailsClose.addEventListener("click", closeDetails);
      detailsFooterClose.addEventListener("click", closeDetails);
      detailsCopy.addEventListener("click", copyDetails);
      detailsDialog.addEventListener("click", function (event) { if (event.target === detailsDialog) closeDetails(); });
      load();
    } else {
      setState("The audit table could not start because its table library is unavailable.", true);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}());
