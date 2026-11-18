// Types for Buddy XR, for apps written in TypeScript.

export type Privacy = "public" | "personal" | "health";
export interface BlockProps { privacy?: Privacy }
export interface Block { type: string; props: Record<string, unknown>; children: Block[] }

export interface Display { kind: "text" | "color"; lines: number; cols: number; images: boolean; linesMeasured?: boolean }
export interface Profile {
  id: string; name: string; about: string;
  display: Display | null; speaker: boolean; mic: boolean; camera: boolean;
  input: Array<"tap" | "double" | "long" | "swipe">;
  route: "direct" | "maker-app";
  class?: string; tested?: boolean; source?: string;
}

export interface Ctx<S> {
  readonly state: S;
  readonly device: Profile;
  set(patch: Partial<S> | ((state: S) => Partial<S>)): void;
  say(text: string): void;
  capture(): Promise<unknown>;
}

export interface ListItem { label: string; detail?: string; value?: string; privacy?: Privacy; [key: string]: unknown }

export function Screen(props: BlockProps & { id?: string; title?: string }, ...children: Array<Block | Block[] | false | null | undefined | "">): Block;
export function Text(text: string, props?: BlockProps & { say?: boolean }): Block;
export function Stat(label: string, value: string, props?: BlockProps): Block;
export function List<S = any>(items: Array<string | ListItem>, props?: BlockProps & { onSelect?: (item: ListItem, index: number, ctx: Ctx<S>) => void | Promise<void> }): Block;
export function Progress(label: string, value: number, props?: BlockProps): Block;
export function Ask<S = any>(question: string, props?: BlockProps & { yes?: (ctx: Ctx<S>) => void | Promise<void>; no?: (ctx: Ctx<S>) => void | Promise<void>; yesLabel?: string; noLabel?: string }): Block;
export function Alert(text: string, props?: BlockProps & { level?: "info" | "warn" }): Block;
export function Image(src: string, props?: BlockProps & { alt?: string }): Block;

export interface App<S = any> {
  name: string;
  state?: S;
  needs?: Array<"mic" | "camera" | "speaker">;
  view(state: S, ctx: Ctx<S>): Block;
  onVoice?(text: string, ctx: Ctx<S>): void | Promise<void>;
  onEvent?(event: any, ctx: Ctx<S>): void | Promise<void>;
  onBack?(ctx: Ctx<S>): void | Promise<void>;
}
export function defineApp<S>(app: App<S>): Required<Pick<App<S>, "name" | "state" | "needs" | "view">> & App<S>;

export interface Line { text: string; kind: string; focused: boolean; level?: "info" | "warn"; value?: number }
export interface Frame { id: string; title: string; lines: Line[]; page: number; pages: number; focusPage: number; image: { src: string; alt: string } | null; withheld: number }

export type InputEvent = { type: "tap" | "double" | "long" | "swipe-forward" | "swipe-back" } | { type: "voice"; text: string };
export interface Host { profile: Profile; show(frame: Frame): void; speak(text: string): void; on(listener: (event: InputEvent) => void): void; capture?(): Promise<unknown> }
export interface RunOptions { speakPersonal?: boolean; onError?: (error: Error) => void }
export interface Run<S = any> {
  readonly state: S; readonly frame: Frame | null; readonly profile: Profile;
  withheld: { display: number; speech: number }; errors: Error[];
  input(event: InputEvent): void; notify(event: unknown): void; set: Ctx<S>["set"]; stop(): void;
}
export function runApp<S>(app: App<S>, host: Host, options?: RunOptions): Run<S>;

export const CLASSES: Record<"audio" | "text" | "color", Profile>;
export const FAMILIES: Record<string, Profile>;
export const PROFILES: Record<string, Profile>;
export function profile(id: string): Profile;
export function canOutput(p: Profile): boolean;
export function canChoose(p: Profile): boolean;

export const LEVELS: Privacy[];
export const WITHHELD: { display: string; speech: string };
export function allowed(level: Privacy, channel: "display" | "speech", profile: Profile, options?: RunOptions): boolean;

export function wrap(text: string, cols: number): string[];
export interface MemoryHost extends Host { frames: Frame[]; spoken: string[]; input(event: InputEvent): void; voice(text: string): void; readonly last: Frame | null; readonly text: string }
export function memoryHost(profile: Profile): MemoryHost;
export function bridgeHost(profile: Profile, post: (message: unknown) => void): Host & { receive(message: unknown): void };

export interface Finding { profile: string; level: "error" | "warn" | "info"; text: string }
export function check(app: App, options?: { profiles?: string[]; states?: object[]; options?: RunOptions }): Finding[];
export function works(findings: Finding[], profile: string): boolean;
