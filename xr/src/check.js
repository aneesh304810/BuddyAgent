// Run an app on every kind of glasses without owning any, and say where something would be lost.
// Each finding: { profile, level: "error" | "warn" | "info", text }. An error means the app does not work there.

import { PROFILES, canChoose, canOutput } from "./profiles.js";
import { memoryHost } from "./hosts/core.js";
import { runApp } from "./runtime.js";

export function check(app, { profiles = Object.keys(PROFILES), states = [], options = {} } = {}) {
  const findings = [];
  for (const id of profiles) {
    const p = PROFILES[id];
    const add = (level, text) => { if (!findings.some((f) => f.profile === id && f.text === text)) findings.push({ profile: id, level, text }); };
    if (!canOutput(p)) { add("error", "No display and no speaker: the app cannot tell the wearer anything."); continue; }
    for (const need of app.needs || []) if (!p[need]) add("error", `The app needs a ${need}, which these glasses do not have.`);
    for (const state of [null, ...states]) {
      const host = memoryHost(p);
      let run;
      try { run = runApp(state ? { ...app, state: { ...app.state, ...state } } : app, host, options); }
      catch (e) { add("error", `The app failed to start: ${e.message}`); continue; }
      const rows = run.rows;
      if (rows.some((r) => r.choose) && !canChoose(p)) add("error", "There is something to choose but no way to choose it (no microphone and no tap).");
      if (rows.some((r) => r.kind === "image" && r.noAlt) && !(p.display && p.display.images))
        add("warn", "An Image has no alt text: on these glasses the wearer gets nothing in its place.");
      if (run.withheld.display) add("info", `${run.withheld.display} private ${run.withheld.display === 1 ? "line is" : "lines are"} not shown here; the wearer is told to open their phone.`);
      if (run.withheld.speech) add("info", `${run.withheld.speech} private ${run.withheld.speech === 1 ? "line is" : "lines are"} not spoken here; the wearer is told to check their phone.`);
      if (run.frame && run.frame.pages > 1) add("info", `One screen takes ${run.frame.pages} pages on this display.`);
      run.stop();
    }
  }
  return findings;
}

export const works = (findings, profile) => !findings.some((f) => f.profile === profile && f.level === "error");
