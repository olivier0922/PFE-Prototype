// Panel full screen and toolbar menus. Loaded automatically by Dash from assets/.
(function () {
  "use strict";

  function refreshGraphs() {
    // Plotly graphs follow their container once the layout has settled.
    window.requestAnimationFrame(function () {
      window.dispatchEvent(new Event("resize"));
    });
  }

  function setMaximized(panel, on) {
    panel.classList.toggle("is-maximized", on);
    document.body.classList.toggle("has-maximized", on);
    var button = panel.querySelector("[data-fullscreen]");
    if (button) {
      button.textContent = on ? "✕" : "⤢";
      button.title = on ? "Quitter le plein écran (Échap)" : "Plein écran (Échap pour quitter)";
    }
    refreshGraphs();
  }

  function closeMenus(except) {
    document.querySelectorAll("details.menu[open]").forEach(function (menu) {
      if (menu !== except) {
        menu.removeAttribute("open");
      }
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-fullscreen]");
    if (button) {
      var panel = button.closest(".panel");
      if (panel) {
        var current = document.querySelector(".panel.is-maximized");
        if (current && current !== panel) {
          setMaximized(current, false);
        }
        setMaximized(panel, !panel.classList.contains("is-maximized"));
      }
      return;
    }
    closeMenus(event.target.closest("details.menu"));
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }
    closeMenus(null);
    var current = document.querySelector(".panel.is-maximized");
    if (current) {
      setMaximized(current, false);
    }
  });
})();
