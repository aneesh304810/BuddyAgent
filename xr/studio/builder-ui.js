// The no-code builder: someone who knows their work describes it as steps, by filling in boxes and dragging
// them into order. Nothing here is code, and nothing is run as code: the steps are plain data that the
// framework's buildApp turns into an app. The draft is kept in this browser only.

import { KINDS, advice, buildApp, problems, toCode } from "../src/index.js";
import { EMPTY, INDUSTRIES } from "./industries.js";

const DRAFT = "buddyxr-builder-draft";
const PRIVACY = [["public", "Anyone nearby may hear this"], ["personal", "Private: show it, do not read it aloud"], ["health", "About someone's health: strictest"]];
const ICONS = { info: "💬", do: "✅", ask: "❓", choose: "☰", note: "🎙️" };
const START = { info: "Something they need to know", do: "Something to do", ask: "A question with a yes or no answer?", choose: "What to choose between", note: "What should they tell you?" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const copy = (v) => JSON.parse(JSON.stringify(v));
const store = { get() { try { return JSON.parse(localStorage.getItem(DRAFT) || "null"); } catch { return null; } },
  set(v) { try { localStorage.setItem(DRAFT, JSON.stringify(v)); } catch { /* a private window: the draft is just not kept */ } } };
const fresh = (kind) => ({ kind, text: START[kind], ...(kind === "choose" ? { options: ["First choice", "Second choice"] } : {}), ...(kind === "ask" ? { ifNo: "" } : {}) });

export function mountBuilder(root, { onApp, onNothing }) {
  const kept = store.get();
  let spec = kept && kept.spec && Array.isArray(kept.spec.steps) ? kept.spec : copy(INDUSTRIES[0].spec);
  let from = kept && kept.spec ? (kept.from || "") : INDUSTRIES[0].id;
  let timer = 0;

  root.innerHTML = `
    <div class="b-field"><label for="b-industry"><b>1</b> Pick your kind of work</label>
      <select id="b-industry"><option value="">Start from nothing</option>${INDUSTRIES.map((i) => `<option value="${esc(i.id)}">${esc(i.industry)}: ${esc(i.spec.name)}</option>`).join("")}</select></div>
    <div class="b-field"><label for="b-name"><b>2</b> Name your app</label><input type="text" id="b-name" maxlength="40"></div>
    <div class="b-field"><label><b>3</b> Your steps, in order</label>
      <p class="hint" style="margin:0 0 8px">Drag a step by its handle to move it, or use the arrows. The glasses show one step at a time.</p>
      <ol id="b-steps" class="b-steps"></ol>
      <div class="b-add" id="b-add"><span>Add a step:</span>${Object.entries(KINDS).map(([k, label]) => `<button type="button" data-add="${k}">${ICONS[k]} ${esc(label)}</button>`).join("")}</div></div>
    <div class="b-field"><label for="b-finish"><b>4</b> When they finish, say</label><input type="text" id="b-finish" maxlength="200" placeholder="All steps finished."></div>
    <div id="b-problems" role="status"></div>
    <div class="bar"><button type="button" class="main" id="b-launch">Launch this app</button><button type="button" id="b-code">Show it as code</button><button type="button" id="b-clear">Start over</button></div>`;
  const $ = (sel) => root.querySelector(sel);
  const list = $("#b-steps");

  function card(s, i) {
    const options = s.kind === "choose" ? `<label class="b-sub">Choices, one on each line<textarea data-f="options" rows="3">${esc((s.options || []).join("\n"))}</textarea></label>` : "";
    const ifNo = s.kind === "ask" ? `<label class="b-sub">If the answer is no, tell them (optional)<input type="text" data-f="ifNo" maxlength="200" value="${esc(s.ifNo || "")}" placeholder="What to do about it"></label>` : "";
    return `<li class="b-step" data-i="${i}">
      <div class="b-head"><span class="b-grip" title="Drag to move" aria-hidden="true">⠿</span><span class="b-num">${i + 1}</span>
        <select data-f="kind" aria-label="What kind of step ${i + 1} is">${Object.entries(KINDS).map(([k, label]) => `<option value="${k}"${k === s.kind ? " selected" : ""}>${ICONS[k]} ${esc(label)}</option>`).join("")}</select>
        <span class="b-move"><button type="button" data-act="up" aria-label="Move step ${i + 1} up"${i === 0 ? " disabled" : ""}>↑</button><button type="button" data-act="down" aria-label="Move step ${i + 1} down"${i === spec.steps.length - 1 ? " disabled" : ""}>↓</button><button type="button" data-act="remove" aria-label="Remove step ${i + 1}">✕</button></span></div>
      <textarea data-f="text" rows="2" maxlength="300" aria-label="What step ${i + 1} says">${esc(s.text || "")}</textarea>
      ${options}${ifNo}
      <label class="b-sub">Who may hear it<select data-f="privacy">${PRIVACY.map(([v, label]) => `<option value="${v}"${(s.privacy || "public") === v ? " selected" : ""}>${esc(label)}</option>`).join("")}</select></label></li>`;
  }

  function draw() {
    $("#b-industry").value = from; $("#b-name").value = spec.name || ""; $("#b-finish").value = spec.finish || "";
    list.innerHTML = spec.steps.map(card).join("");
    changed();
  }

  function changed() {
    store.set({ spec, from });
    const wrong = problems(spec), tips = wrong.length ? [] : advice(spec);
    $("#b-problems").innerHTML = wrong.map((w) => `<p class="b-wrong">${esc(w)}</p>`).join("") + tips.map((t) => `<p class="b-tip">${esc(t)}</p>`).join("");
    $("#b-launch").disabled = $("#b-code").disabled = wrong.length > 0;
    clearTimeout(timer);
    timer = setTimeout(() => { if (wrong.length) onNothing(wrong[0]); else onApp(buildApp(copy(spec)), spec); }, 250);
  }

  function move(i, to) {
    if (to < 0 || to >= spec.steps.length || to === i) return;
    const [s] = spec.steps.splice(i, 1); spec.steps.splice(to, 0, s); draw();
  }

  list.addEventListener("input", (e) => {
    const li = e.target.closest(".b-step"), f = e.target.dataset.f; if (!li || !f) return;
    const s = spec.steps[Number(li.dataset.i)];
    if (f === "options") s.options = e.target.value.split("\n").map((o) => o.trim()).filter(Boolean);
    else if (f === "privacy") { if (e.target.value === "public") delete s.privacy; else s.privacy = e.target.value; }
    else if (f === "kind") { const text = s.text; spec.steps[Number(li.dataset.i)] = { ...fresh(e.target.value), text, ...(s.privacy ? { privacy: s.privacy } : {}) }; return draw(); }
    else s[f] = e.target.value;
    changed();
  });
  list.addEventListener("click", (e) => {
    const li = e.target.closest(".b-step"), act = e.target.dataset.act; if (!li || !act) return;
    const i = Number(li.dataset.i);
    if (act === "up") move(i, i - 1); else if (act === "down") move(i, i + 1); else { spec.steps.splice(i, 1); draw(); }
  });

  // Dragging, with pointer events so it works with a mouse, a finger or a pen: a step by its handle, or a new
  // step from the "Add a step" row. A press on an "Add a step" button that does not move is an ordinary click.
  let drag = null, justDropped = false;
  const slot = (y) => { const cards = [...list.children];
    for (let i = 0; i < cards.length; i++) { const r = cards[i].getBoundingClientRect(); if (y < r.top + r.height / 2) return i; }
    return cards.length; };
  const mark = (to) => [...list.children].forEach((li, i) => { li.classList.toggle("before", to === i); li.classList.toggle("after", to === list.children.length && i === list.children.length - 1); });
  const stop = () => { drag = null; mark(-1); list.querySelectorAll(".lifted").forEach((li) => li.classList.remove("lifted")); root.classList.remove("dragging"); };
  root.addEventListener("pointerdown", (e) => {
    if (e.button > 0) return;
    const grip = e.target.closest(".b-grip"), add = e.target.closest("[data-add]");
    if (!grip && !add) return;
    drag = grip ? { move: Number(grip.closest(".b-step").dataset.i), live: true } : { add: add.dataset.add, live: false };
    drag.x = e.clientX; drag.y = e.clientY;
    (grip || add).setPointerCapture(e.pointerId);
    if (grip) { grip.closest(".b-step").classList.add("lifted"); root.classList.add("dragging"); e.preventDefault(); }
  });
  root.addEventListener("pointermove", (e) => {
    if (!drag) return;
    if (!drag.live && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > 6) { drag.live = true; root.classList.add("dragging"); }
    if (!drag.live) return;
    drag.to = slot(e.clientY); mark(drag.to);
    if (e.clientY < 70) window.scrollBy(0, -14); else if (e.clientY > window.innerHeight - 70) window.scrollBy(0, 14);
  });
  root.addEventListener("pointerup", () => {
    if (!drag) return;
    const d = drag; stop();
    if (!d.live || d.to == null) return;
    if (d.add) { justDropped = true; if (spec.steps.length < 40) { spec.steps.splice(d.to, 0, fresh(d.add)); draw(); } }
    else move(d.move, d.to > d.move ? d.to - 1 : d.to);
  });
  root.addEventListener("pointercancel", stop);

  $("#b-add").addEventListener("click", (e) => { if (justDropped) { justDropped = false; return; }
    const kind = e.target.dataset && e.target.dataset.add; if (!kind || spec.steps.length >= 40) return;
    spec.steps.push(fresh(kind)); draw(); const last = list.lastElementChild; if (last) { last.scrollIntoView({ block: "nearest" }); last.querySelector("textarea").select(); } });
  $("#b-name").addEventListener("input", (e) => { spec.name = e.target.value; changed(); });
  $("#b-finish").addEventListener("input", (e) => { spec.finish = e.target.value; changed(); });
  $("#b-industry").addEventListener("change", (e) => { from = e.target.value; const found = INDUSTRIES.find((i) => i.id === from); spec = copy(found ? found.spec : EMPTY); draw(); });
  $("#b-clear").addEventListener("click", () => { from = ""; spec = copy(EMPTY); draw(); });

  draw();
  return { get spec() { return spec; }, code: () => toCode(spec), launch: $("#b-launch"), showCode: $("#b-code") };
}
