// Not run: compiled by `tsc --noEmit` to prove an app can be written in TypeScript against the framework's types.
import { Ask, CLASSES, List, Screen, Text, check, defineApp, memoryHost, runApp } from "../src/index.js";

interface State { gate: string; going: boolean }

const app = defineApp<State>({
  name: "Gate change",
  state: { gate: "B14", going: false },
  view: (s) => Screen({ title: "Gate change" },
    Text(`Flight UA 212 now boards at ${s.gate}`),
    List(["Walk: 9 min", "Boards: 18:40"]),
    !s.going && Ask<State>("Navigate there?", { yes: (c) => c.set({ going: true }) })),
  onVoice: (text, ctx) => ctx.set({ gate: text }),
});

const host = memoryHost(CLASSES.text);
const run = runApp(app, host);
const gate: string = run.state.gate;
const lines: string[] = host.last ? host.last.lines.map((l) => l.text) : [];
export const ok = check(app).length >= 0 && gate.length > 0 && lines.length >= 0;
