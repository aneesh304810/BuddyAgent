// Runs the Studio's app on a real pair of Brilliant Labs Halo glasses, over Bluetooth from this browser.
//
// NOT YET TRIED ON A REAL PAIR. The parts are the maker's own Bluetooth library (vendor/brilliant-ble, loaded only
// when someone asks to connect) and haloHost. It needs a browser with Web Bluetooth: Chrome on Android or on a
// computer. Nothing leaves this device: the app runs in this page and talks to the glasses directly.
//
// The glasses run their own copy of the app, separate from the one in the simulator beside it.

import { haloHost, runApp } from "../src/index.js";

const KIT = new URL("../vendor/brilliant-ble/brilliant-ble.es.js", import.meta.url).href;

/**
 * @param root     an empty element to draw the box into
 * @param makeApp  returns the app to run, or null when there is none
 * @returns        { restart() }: call it when the app has changed
 */
export function mountGlasses(root, { makeApp }) {
  const can = typeof navigator !== "undefined" && Boolean(navigator.bluetooth);
  let kit = null, ble = null, host = null, run = null, name = "", busy = false;

  root.innerHTML = `<h2>Run it on your glasses</h2>
    <div class="bar" style="margin:0"><button class="main" data-connect${can ? "" : " disabled"}>Connect my Halo</button>
      <button data-stop hidden>Disconnect</button>
      <label><input type="checkbox" data-large> Larger text</label></div>
    <p class="hint" data-status role="status"></p>
    <p class="hint">Brilliant Labs Halo, over Bluetooth from this device. Not yet tried on a real pair.</p>`;
  const $ = (name) => root.querySelector(`[data-${name}]`);
  const tell = (text, bad = false) => { $("status").textContent = text; $("status").classList.toggle("bad", bad); };
  const draw = () => { $("connect").hidden = Boolean(ble); $("stop").hidden = !ble; $("connect").disabled = !can || busy; };

  const load = () => (kit ||= import(KIT));

  async function start() {
    if (run) { run.stop(); run = null; }
    if (host) { await host.close().catch(() => {}); host = null; }
    if (!ble) return;
    const app = makeApp();
    if (!app) return tell(`Connected to ${name}. There is no app to run yet.`);
    tell(`Sending the app to ${name}…`);
    let failed = false;
    const mine = haloHost(ble, { large: $("large").checked, onProblem: (e) => { failed = true; if (host === mine) tell(`The glasses reported a problem: ${e.message}`, true); } });
    host = mine;
    try { run = runApp(app, mine, { onError: (e) => tell(`The app stopped: ${e.message}`, true) }); }
    catch (e) { return tell(`The app could not start: ${e.message}`, true); }
    await mine.settled();
    if (host === mine && !failed) tell(`Running on ${name}. On the glasses: press once for the next choice, twice to choose, and hold to go back.`);
  }

  async function connect() {
    if (busy || ble) return;
    busy = true; draw();
    tell("Choose your glasses in the list the browser shows.");
    try {
      const { BrilliantBle } = await load();
      const next = new BrilliantBle();
      name = (await next.connect()) || "your glasses";
      next.setDisconnectHandler(() => { if (ble !== next) return; ble = null; host = null; if (run) run.stop(); run = null; tell("The glasses disconnected."); draw(); });
      ble = next;
      await start();
    } catch (e) {
      ble = null;
      tell(e && e.name === "NotFoundError" ? "No glasses were chosen." : `Could not connect: ${e && e.message ? e.message : e}`, e && e.name !== "NotFoundError");
    }
    busy = false; draw();
  }

  async function stop() {
    const old = ble;
    if (run) { run.stop(); run = null; }
    if (host) { await host.close().catch(() => {}); host = null; }
    ble = null;
    if (old) await old.disconnect().catch(() => {});
    tell("Disconnected."); draw();
  }

  if (can) {
    for (const early of ["pointerenter", "focus", "touchstart"]) $("connect").addEventListener(early, () => { load().catch(() => { kit = null; }); }, { once: true, passive: true });
    $("connect").onclick = connect;
    $("stop").onclick = stop;
    $("large").onchange = () => { if (ble) start(); };
  } else tell("This browser cannot use Bluetooth. Open this page in Chrome on Android or on a computer.");
  draw();

  return { restart() { if (ble && !busy) start(); } };
}
