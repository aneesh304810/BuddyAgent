// Picks a server page's look before it draws. The app passes ?theme=light or ?theme=dark (older apps sent
// "default" for dark); the choice is kept on this device for pages opened without it. With no choice the page
// follows the phone or computer's own light or dark setting. tokens.css and theme.css do the rest.
(function () {
  var asked = new URLSearchParams(location.search).get("theme"), look = null;
  if (asked) look = asked === "light" ? "light" : "dark";
  try {
    if (look) localStorage.setItem("buddyTheme", look);
    else look = localStorage.getItem("buddyTheme");
  } catch (e) { /* no storage in this view: use the asked or system look */ }
  if (look === "default") look = "dark";
  if (look !== "light" && look !== "dark") {
    look = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.dataset.theme = look;
})();
