export type UiState =
  | "ready"
  | "recording"
  | "transcribing"
  | "generating"
  | "playing"
  | "error";

export type WireMessageType =
  | "hello"
  | "text"
  | "ptt_start"
  | "ptt_stop"
  | "clear";

export interface WireMessage {
  type: WireMessageType;
  text?: string;
}

export interface ServerEvent {
  type?: string;
  state?: string;
  status?: string;
  text?: string;
  content?: string;
  delta?: string;
  transcript?: string;
  message?: string;
  error?: string;
  [key: string]: unknown;
}

export function websocketUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/ws`;
}

export function normalizeState(value: unknown): UiState | undefined {
  if (typeof value !== "string") return undefined;
  const state = value.toLowerCase().replace(/[\s-]+/g, "_");
  if (["ready", "idle", "connected", "complete", "done"].includes(state)) return "ready";
  if (["recording", "listening", "capture", "capturing"].includes(state)) return "recording";
  if (["transcribing", "transcribe", "asr", "recognizing", "recognition"].includes(state)) return "transcribing";
  if (["generating", "generate", "llm", "thinking", "streaming"].includes(state)) return "generating";
  if (["playing", "playback", "speaking", "tts", "synthesizing"].includes(state)) return "playing";
  if (["error", "failed", "failure"].includes(state)) return "error";
  return undefined;
}

export function decodeServerEvent(raw: unknown): ServerEvent | undefined {
  if (typeof raw !== "string") return undefined;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return undefined;
    const event = parsed as Record<string, unknown>;
    const payload = event.payload;
    if (payload && typeof payload === "object" && !Array.isArray(payload)) {
      return { ...(payload as Record<string, unknown>), ...event } as ServerEvent;
    }
    const data = event.data;
    if (data && typeof data === "object" && !Array.isArray(data)) {
      return { ...(data as Record<string, unknown>), ...event } as ServerEvent;
    }
    return event as ServerEvent;
  } catch {
    return undefined;
  }
}

export function eventText(event: ServerEvent): string {
  for (const value of [event.text, event.transcript, event.delta, event.content, event.message, event.error]) {
    if (typeof value === "string") return value;
  }
  return "";
}
