// What may be shown or spoken, by how private it is. The framework applies this to every block before anything
// reaches a host: an app cannot switch it off, only mark its content.
//
//   public     shown and spoken anywhere.
//   personal   shown in the lens (only the wearer sees it). Spoken only if the wearer chose that
//              (options.speakPersonal), because an open-ear speaker is heard by the people nearby.
//   health     shown only on glasses reached directly from the phone (profile.route === "direct"), so it does
//              not pass through a maker's app or service. Never spoken.
//
// What is held back is replaced with a fixed line, so the wearer knows there is something and where to read it.

export const LEVELS = ["public", "personal", "health"];
export const WITHHELD = { display: "Private. Open your phone.", speech: "You have a private update. Check your phone." };

export function allowed(level, channel, profile, options = {}) {
  if (!LEVELS.includes(level)) throw new Error(`Unknown privacy level "${level}". Use one of: ${LEVELS.join(", ")}`);
  if (level === "public") return true;
  if (channel === "display") return level === "personal" || profile.route === "direct";
  return level === "personal" && options.speakPersonal === true;   // speech
}
