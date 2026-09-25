(function () {
  "use strict";
  function init() {
    var root = document.getElementById("persistentlogger-controlpanel");
    if (!root || !window.Survey) return;
    var survey = new Survey.Model(JSON.parse(root.dataset.survey));
    survey.onComplete.add(function (sender) {
      fetch(root.dataset.saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(sender.data)
      }).then(function (response) {
        if (!response.ok) throw new Error("Unable to save settings");
        window.location.assign(root.dataset.redirectUrl);
      }).catch(function () {
        sender.showCompletedPage = false;
        sender.completedHtml = "<div class='message error'>Settings could not be saved.</div>";
        sender.render();
      });
    });
    survey.render(document.getElementById("persistentlogger-survey"));
  }
  document.addEventListener("DOMContentLoaded", init);
}());
