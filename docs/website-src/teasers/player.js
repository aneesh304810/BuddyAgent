// Plays SCENES once: an intro card, each scene's caption and lines, an end card; then window.__done.
(async function () {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const $ = (id) => document.getElementById(id);
  const show = (id, on) => $(id).classList.toggle("hide", !on);
  await wait(2600); show("intro", false); await wait(500); show("demo", true);
  for (const sc of window.SCENES) {
    $("caption").textContent = sc.caption; $("feed").innerHTML = "";
    $("caption").classList.add("in");
    await wait(900);
    for (const line of sc.lines) {
      const d = document.createElement("div");
      d.className = "line " + (line[0] || "");
      d.innerHTML = line[1];
      $("feed").appendChild(d);
      requestAnimationFrame(() => d.classList.add("in"));
      await wait(line[2] || 1700);
    }
    await wait(1800); $("caption").classList.remove("in"); await wait(400);
  }
  show("demo", false); await wait(500); show("outro", true); await wait(3200);
  window.__done = true;
})();
