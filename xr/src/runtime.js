// Runs an app on a host. The host is the only part that knows the glasses: it shows a frame, speaks a sentence
// and reports what the wearer did. Everything else (state, focus, paging, what is said and what is held back) is
// here, the same on every pair of glasses.
//
// A host:   { profile, show(frame), speak(text), on(listener), capture?() }
// What a host reports:  { type: "tap" | "double" | "long" | "swipe-forward" | "swipe-back" }
//                       { type: "voice", text }
// How that is read:     tap or swipe forward: next.  swipe back: previous.  double tap: choose.  long press: back.
//                       voice "next", "previous", "select", "back", "yes", "no"; any other words go to the app.

import { frame, rows } from "./layout.js";
import { script } from "./speech.js";

const INTENTS = { tap: "next", "swipe-forward": "next", "swipe-back": "prev", double: "select", long: "back" };
const WORDS = { next: "next", down: "next", previous: "prev", up: "prev", select: "select", choose: "select", ok: "select", okay: "select",
  back: "back", yes: "yes", yeah: "yes", yep: "yes", no: "no", nope: "no" };

export function runApp(app, host, options = {}) {
  const profile = host.profile;
  let state = typeof structuredClone === "function" ? structuredClone(app.state) : JSON.parse(JSON.stringify(app.state));
  let screenId = null, focus = 0, page = 0, list = [], lastFrame = null, lastScript = "", lastFocused = "", stopped = false, drawing = false, again = false;
  const run = { app, profile, withheld: { display: 0, speech: 0 }, errors: [] };

  const ctx = {
    get state() { return state; },
    device: profile,
    set(patch) { state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) }; render(); },
    say(text) { if (profile.speaker && !stopped) host.speak(String(text)); },
    capture() { return profile.camera && host.capture ? host.capture() : Promise.reject(new Error("These glasses have no camera.")); },
  };

  function render() {
    if (stopped) return;
    if (drawing) { again = true; return; }                     // a handler changed state while a screen was being built
    drawing = true;
    try {
      const screen = app.view(state, ctx);
      list = rows(screen);
      const id = screen.props.id || screen.props.title || "";
      if (id !== screenId) { screenId = id; focus = 0; page = 0; }
      const choosable = list.filter((r) => r.choose).length;
      focus = Math.max(0, Math.min(focus, Math.max(choosable - 1, 0)));
      lastFrame = frame(screen, list, profile, { focus, page, options });
      if (lastFrame) { page = lastFrame.page; run.withheld.display = lastFrame.withheld; host.show(lastFrame); }
      const spoken = script(screen, list, profile, { focus, options });
      run.withheld.speech = spoken.withheld;
      const rest = spoken.script.filter((line) => line !== spoken.focused).join(" ");
      if (rest !== lastScript) { if (spoken.script.length) host.speak(spoken.script.join(" ")); }   // a new screen: all of it
      else if (spoken.focused && spoken.focused !== lastFocused) host.speak(spoken.focused);          // moved: just that choice
      lastScript = rest; lastFocused = spoken.focused;
    } finally { drawing = false; }
    if (again) { again = false; render(); }
  }

  function act(fn) {
    try { const r = fn(); if (r && typeof r.catch === "function") r.catch(fail); } catch (e) { fail(e); }
  }
  function fail(e) { run.errors.push(e); if (options.onError) options.onError(e); else throw e; }

  function intent(name) {
    const choosable = list.filter((r) => r.choose);
    // A screen opens on its first page so it is read from the top. Next pages forward until the choices are in
    // view, then moves between them; the page follows the choice.
    const ahead = lastFrame && choosable.length && lastFrame.focusPage > page;
    if (name === "next") {
      if (ahead || (!choosable.length && lastFrame)) page = Math.min(page + 1, lastFrame.pages - 1);
      else if (choosable.length) { focus = (focus + 1) % choosable.length; page = "focus"; }
      return render();
    }
    if (name === "prev") {
      if (choosable.length && focus > 0 && !ahead) { focus--; page = "focus"; }
      else if (lastFrame) page = Math.max(page - 1, 0);
      return render();
    }
    if (name === "select") {
      if (ahead) { page++; return render(); }                  // the choice is not in view yet: show it first
      return choosable[focus] && act(() => choosable[focus].choose(ctx));
    }
    if (name === "yes" || name === "no") { const o = list.find((r) => r.answer === name); return o && act(() => o.choose(ctx)); }
    if (name === "back") return app.onBack && act(() => app.onBack(ctx));
  }

  function input(event) {
    if (stopped || !event) return;
    if (event.type === "voice") {
      const text = String(event.text || "").trim();
      const word = WORDS[text.toLowerCase().replace(/[.!?,]/g, "")];
      if (word) return intent(word);
      return text && app.onVoice && act(() => app.onVoice(text, ctx));
    }
    if (INTENTS[event.type]) intent(INTENTS[event.type]);
  }

  host.on(input);
  Object.defineProperties(run, { state: { get: () => state }, frame: { get: () => lastFrame }, rows: { get: () => list } });
  Object.assign(run, {
    input, intent, set: ctx.set,
    notify(event) { if (!stopped && app.onEvent) act(() => app.onEvent(event, ctx)); },
    stop() { stopped = true; },
  });
  render();
  return run;
}
