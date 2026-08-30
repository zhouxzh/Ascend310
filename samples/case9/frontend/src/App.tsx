import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  Check,
  CircleHelp,
  Cpu,
  Ear,
  Eraser,
  LoaderCircle,
  MessageCircle,
  Mic,
  Mic2,
  MonitorSpeaker,
  Radio,
  RefreshCw,
  Send,
  Server,
  ShieldAlert,
  Sparkles,
  Volume2,
  Wifi,
  WifiOff,
  XCircle,
} from "lucide-react";
import {
  decodeServerEvent,
  eventText,
  normalizeState,
  websocketUrl,
  type ServerEvent,
  type UiState,
  type WireMessage,
} from "./protocol";

type ConnectionState = "connecting" | "connected" | "offline";
type Role = "user" | "assistant";

interface ChatMessage {
  id: string;
  role: Role;
  text: string;
  createdAt: number;
  streaming?: boolean;
}

interface SocketApi {
  connection: ConnectionState;
  send: (message: WireMessage) => boolean;
  reconnect: () => void;
}

const STATE_LABELS: Record<UiState, string> = {
  ready: "就绪",
  recording: "录音中",
  transcribing: "识别中",
  generating: "生成中",
  playing: "播放中",
  error: "错误",
};

const STATE_DESCRIPTIONS: Record<UiState, string> = {
  ready: "麦克风和扬声器可用",
  recording: "开发板正在采集麦克风",
  transcribing: "正在转换为中文文本",
  generating: "本地模型正在生成回复",
  playing: "回复正在 USB 喇叭播放",
  error: "请查看会话中的错误信息",
};

const MAX_VISIBLE_MESSAGES = 40;

function messageId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function useLocalSocket(onEvent: (event: ServerEvent) => void): SocketApi {
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const socketRef = useRef<WebSocket>();
  const retryTimerRef = useRef<number>();
  const retryCountRef = useRef(0);
  const closedRef = useRef(false);
  const onEventRef = useRef(onEvent);
  const url = useMemo(() => websocketUrl(), []);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  const connect = useCallback(() => {
    if (closedRef.current) return;
    const current = socketRef.current;
    if (current && (current.readyState === WebSocket.OPEN || current.readyState === WebSocket.CONNECTING)) return;
    setConnection("connecting");
    const socket = new WebSocket(url);
    socketRef.current = socket;
    socket.addEventListener("open", () => {
      if (socketRef.current !== socket) {
        socket.close(1000, "superseded connection");
        return;
      }
      retryCountRef.current = 0;
      setConnection("connected");
      socket.send(JSON.stringify({ type: "hello" } satisfies WireMessage));
    });
    socket.addEventListener("message", (message) => {
      const event = decodeServerEvent(typeof message.data === "string" ? message.data : "");
      if (event) onEventRef.current(event);
    });
    socket.addEventListener("error", () => {
      if (socketRef.current === socket) setConnection("offline");
    });
    socket.addEventListener("close", () => {
      // React StrictMode and manual reconnects can briefly overlap sockets.
      // Only the active socket is allowed to change visible connection state.
      if (socketRef.current !== socket) return;
      socketRef.current = undefined;
      setConnection("offline");
      if (closedRef.current) return;
      const delay = Math.min(8000, 500 * 2 ** retryCountRef.current);
      retryCountRef.current += 1;
      retryTimerRef.current = window.setTimeout(connect, delay);
    });
  }, [url]);

  useEffect(() => {
    closedRef.current = false;
    connect();
    return () => {
      closedRef.current = true;
      if (retryTimerRef.current !== undefined) window.clearTimeout(retryTimerRef.current);
      socketRef.current?.close(1000, "page closed");
      socketRef.current = undefined;
    };
  }, [connect]);

  const send = useCallback((message: WireMessage): boolean => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    try {
      socket.send(JSON.stringify(message));
      return true;
    } catch {
      // The socket can close between the readyState check and send().
      // Callers can then surface the same recoverable offline state.
      return false;
    }
  }, []);

  const reconnect = useCallback(() => {
    retryCountRef.current = 0;
    if (retryTimerRef.current !== undefined) window.clearTimeout(retryTimerRef.current);
    socketRef.current?.close();
    socketRef.current = undefined;
    connect();
  }, [connect]);

  return { connection, send, reconnect };
}

function App() {
  const [uiState, setUiState] = useState<UiState>("ready");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [lastError, setLastError] = useState("");
  const [pttActive, setPttActive] = useState(false);
  const [audioMeta, setAudioMeta] = useState({ microphone: "C922 Pro Stream Webcam", speaker: "USB 喇叭" });
  const [lastActivity, setLastActivity] = useState<number>();
  const streamIdRef = useRef<string>();
  const pttRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const appendMessage = useCallback((message: ChatMessage) => {
    setMessages((current) => [...current, message].slice(-MAX_VISIBLE_MESSAGES));
  }, []);

  const finishAssistant = useCallback((text?: string) => {
    setMessages((current) => {
      const streamId = streamIdRef.current;
      if (streamId) {
        return current.map((item) => item.id === streamId ? { ...item, text: text || item.text, streaming: false } : item);
      }
      if (!text) return current;
      const completed: ChatMessage = { id: messageId("assistant"), role: "assistant", text, createdAt: Date.now() };
      return [...current, completed].slice(-MAX_VISIBLE_MESSAGES);
    });
    streamIdRef.current = undefined;
  }, []);

  const abortAssistantStream = useCallback(() => {
    const streamId = streamIdRef.current;
    if (streamId) {
      setMessages((current) => current.map((item) => (
        item.id === streamId ? { ...item, streaming: false } : item
      )));
    }
    streamIdRef.current = undefined;
  }, []);

  const handleEvent = useCallback((event: ServerEvent) => {
    setLastActivity(Date.now());
    const eventType = typeof event.type === "string" ? event.type.toLowerCase() : "";
    const state = normalizeState(event.state ?? event.status ?? eventType);
    if (state) {
      setUiState(state);
      if (state === "error") {
        abortAssistantStream();
        setLastError(eventText(event) || "本地服务返回错误。");
      }
    }
    if (eventType === "ready" || eventType === "hello") {
      setUiState("ready");
      setLastError("");
      if (typeof event.microphone === "string" || typeof event.speaker === "string") {
        setAudioMeta((current) => ({
          microphone: typeof event.microphone === "string" ? event.microphone : current.microphone,
          speaker: typeof event.speaker === "string" ? event.speaker : current.speaker,
        }));
      }
    }
    if (eventType === "transcript" || eventType === "user_transcript" || typeof event.transcript === "string") {
      const text = eventText(event).trim();
      if (text) {
        setMessages((current) => {
          const latest = current[current.length - 1];
          if (latest?.role === "user" && latest.text === text) return current;
          const transcript: ChatMessage = { id: messageId("user"), role: "user", text, createdAt: Date.now() };
          return [...current, transcript].slice(-MAX_VISIBLE_MESSAGES);
        });
      }
    }
    if (eventType === "delta" || eventType === "token" || typeof event.delta === "string" || typeof event.content === "string") {
      const text = (typeof event.delta === "string" ? event.delta : event.content || event.text || "");
      if (text) {
        setUiState("generating");
        setMessages((current) => {
          let streamId = streamIdRef.current;
          if (!streamId) {
            streamId = messageId("assistant");
            streamIdRef.current = streamId;
            const partial: ChatMessage = { id: streamId, role: "assistant", text, createdAt: Date.now(), streaming: true };
            return [...current, partial].slice(-MAX_VISIBLE_MESSAGES);
          }
          return current.map((item) => item.id === streamId ? { ...item, text: item.text + text, streaming: true } : item);
        });
      }
    }
    if (eventType === "done" || eventType === "complete" || eventType === "response_complete") {
      finishAssistant(typeof event.text === "string" ? event.text : undefined);
      if (!state || state === "ready") setUiState("ready");
    }
    if (eventType === "error" || event.error) {
      const text = eventText(event) || "本地服务返回错误。";
      // A failed generation must not let the next response append to the
      // previous partial assistant bubble.
      abortAssistantStream();
      setLastError(text);
      setUiState("error");
      setPttActive(false);
      pttRef.current = false;
    }
    if (eventType === "clear_ack" || eventType === "cleared") {
      setMessages([]);
      streamIdRef.current = undefined;
      setUiState("ready");
    }
  }, [abortAssistantStream, finishAssistant]);

  const { connection, send, reconnect } = useLocalSocket(handleEvent);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (connection !== "connected" && pttRef.current) {
      // The server releases capture on WebSocket disconnect.  Keep the local
      // control honest rather than leaving it visually in a recording state.
      pttRef.current = false;
      setPttActive(false);
    }
    if (connection !== "connected" && streamIdRef.current) {
      abortAssistantStream();
      setUiState("error");
      setLastError("本地服务连接已断开，当前回复未完成。");
    }
  }, [abortAssistantStream, connection]);

  const sendText = useCallback(() => {
    const text = draft.trim();
    if (!text || uiState === "recording" || uiState === "transcribing") return;
    if (!send({ type: "text", text })) {
      setLastError("尚未连接到本地服务，请稍后重试。");
      setUiState("error");
      return;
    }
    appendMessage({ id: messageId("user"), role: "user", text, createdAt: Date.now() });
    setDraft("");
    setLastError("");
    setUiState("generating");
  }, [appendMessage, draft, send, uiState]);

  const startPtt = useCallback(() => {
    if (pttRef.current || connection !== "connected" || uiState !== "ready") return;
    if (!send({ type: "ptt_start" })) {
      setLastError("尚未连接到本地服务，无法开始录音。");
      setUiState("error");
      return;
    }
    pttRef.current = true;
    setPttActive(true);
    setLastError("");
    setUiState("recording");
  }, [connection, send, uiState]);

  const stopPtt = useCallback(() => {
    if (!pttRef.current) return;
    pttRef.current = false;
    setPttActive(false);
    if (send({ type: "ptt_stop" })) {
      setUiState("transcribing");
    } else {
      setLastError("录音结束消息未发送，连接可能已断开。");
      setUiState("error");
    }
  }, [send]);

  const clearConversation = useCallback(() => {
    if (!send({ type: "clear" })) {
      setLastError("尚未连接到本地服务，无法清空会话。");
      setUiState("error");
      return;
    }
    // Wait for the server's `cleared` acknowledgement so the UI does not
    // claim a successful reset while a bounded in-memory session is busy.
    setLastError("");
  }, [send]);

  const hostLabel = window.location.hostname || "开发板";
  const stateTone = uiState === "error" ? "danger" : uiState === "ready" ? "success" : "active";

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true"><MessageCircle /></div>
          <div>
            <p className="brand-kicker">CASE9 / LOCAL VOICE LAB</p>
            <h1>本地中文聊天</h1>
          </div>
        </div>
        <div className="topbar-actions">
          <div className="host-chip"><Cpu aria-hidden="true" /><span>Ascend 310B · {hostLabel}</span></div>
          <ConnectionPill state={connection} />
          <button type="button" className="icon-button" title="重新连接本地服务" aria-label="重新连接本地服务" onClick={reconnect}><RefreshCw aria-hidden="true" /></button>
        </div>
      </header>

      <main className="workspace">
        <section className="conversation-column">
          <div className="lan-warning" role="alert">
            <div className="warning-icon" aria-hidden="true"><ShieldAlert /></div>
            <div>
              <strong>未鉴权 LAN 实验模式</strong>
              <p>同一局域网内的设备可以控制开发板麦克风和 USB 喇叭。仅在可信实验网络中使用。</p>
            </div>
            <span className="warning-tag">LAN ONLY</span>
          </div>

          <div className="conversation-header">
            <div>
              <p className="eyebrow">OFFLINE SESSION · MEMORY ONLY</p>
              <h2>与本地助手对话</h2>
              <p className="subheading">文本和语音都由开发板上的服务处理，不保存录音文件。</p>
            </div>
            <button type="button" className="quiet-button" onClick={clearConversation} title="清空当前会话" disabled={connection !== "connected" || (uiState !== "ready" && uiState !== "error")}>
              <Eraser aria-hidden="true" />
              <span>清空会话</span>
            </button>
          </div>

          <div className="message-list" ref={scrollRef} aria-live="polite" aria-label="聊天记录">
            {messages.length === 0 ? (
              <div className="empty-chat">
                <div className="empty-chat-icon" aria-hidden="true"><Bot /></div>
                <strong>等待你的第一句话</strong>
                <p>输入文字，或按住下方麦克风开始一次本地语音对话。</p>
              </div>
            ) : messages.map((message) => <MessageBubble key={message.id} message={message} />)}
          </div>

          <Composer
            draft={draft}
            setDraft={setDraft}
            onSend={sendText}
            onStartPtt={startPtt}
            onStopPtt={stopPtt}
            pttActive={pttActive}
            disabled={connection !== "connected" || uiState !== "ready"}
          />
        </section>

        <aside className="control-column" aria-label="运行状态">
          <StatusPanel uiState={uiState} connection={connection} lastError={lastError} onReconnect={reconnect} lastActivity={lastActivity} />
          <PipelinePanel uiState={uiState} />
          <AudioPanel meta={audioMeta} />
          <ServerPanel connection={connection} />
        </aside>
      </main>

      <footer className="footer-bar">
        <span><Radio aria-hidden="true" />本地服务端口 7862</span>
        <span>语音链路：ASR → LLM → TTS</span>
        <span className="footer-note"><Check aria-hidden="true" />浏览器不访问麦克风</span>
      </footer>
    </div>
  );
}

function ConnectionPill({ state }: { state: ConnectionState }) {
  const Icon = state === "connected" ? Wifi : state === "connecting" ? LoaderCircle : WifiOff;
  const label = state === "connected" ? "服务已连接" : state === "connecting" ? "正在连接" : "服务离线";
  return <span className={`connection-pill connection-pill--${state}`} title={label} aria-label={label}><Icon aria-hidden="true" className={state === "connecting" ? "spin" : undefined} /><span>{label}</span></span>;
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <article className={`message-row message-row--${message.role}`}>
      <div className="message-avatar" aria-hidden="true">{isUser ? <MessageCircle /> : <Bot />}</div>
      <div className="message-body">
        <div className="message-meta"><strong>{isUser ? "你" : "本地助手"}</strong><time>{new Date(message.createdAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</time>{message.streaming ? <span className="streaming-mark">生成中</span> : null}</div>
        <div className="message-bubble">{message.text || <span className="typing-dots"><i /><i /><i /></span>}{message.streaming ? <span className="cursor" aria-hidden="true" /> : null}</div>
      </div>
    </article>
  );
}

function Composer({ draft, setDraft, onSend, onStartPtt, onStopPtt, pttActive, disabled }: { draft: string; setDraft: (value: string) => void; onSend: () => void; onStartPtt: () => void; onStopPtt: () => void; pttActive: boolean; disabled: boolean }) {
  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  };
  const handlePointerDown = (event: React.PointerEvent<HTMLButtonElement>) => {
    if (disabled) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    onStartPtt();
  };
  const handlePointerUp = (event: React.PointerEvent<HTMLButtonElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    onStopPtt();
  };
  const handleKeyStart = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if ((event.key === " " || event.key === "Enter") && !event.repeat) {
      event.preventDefault();
      onStartPtt();
    }
  };
  const handleKeyStop = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      onStopPtt();
    }
  };
  return (
    <div className="composer">
      <div className={`ptt-wrap ${pttActive ? "is-active" : ""}`}>
        <button
          type="button"
          className="ptt-button"
          disabled={disabled && !pttActive}
          aria-label={pttActive ? "松开结束录音" : "按住说话"}
          title={pttActive ? "松开结束录音" : "按住说话"}
          onPointerDown={handlePointerDown}
          onPointerUp={handlePointerUp}
          onPointerCancel={onStopPtt}
          onKeyDown={handleKeyStart}
          onKeyUp={handleKeyStop}
          onContextMenu={(event) => event.preventDefault()}
        >
          {pttActive ? <Mic2 aria-hidden="true" /> : <Mic aria-hidden="true" />}
          <span>{pttActive ? "松开结束" : "按住说话"}</span>
        </button>
      </div>
      <div className="text-composer">
        <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={handleKeyDown} disabled={disabled} rows={2} placeholder={disabled ? "等待本地服务连接…" : "输入中文消息…"} aria-label="输入消息" />
        <button type="button" className="send-button" title="发送消息" aria-label="发送消息" disabled={!draft.trim() || disabled} onClick={onSend}><Send aria-hidden="true" /></button>
      </div>
    </div>
  );
}

function StatusPanel({ uiState, connection, lastError, onReconnect, lastActivity }: { uiState: UiState; connection: ConnectionState; lastError: string; onReconnect: () => void; lastActivity?: number }) {
  return (
    <section className="side-panel status-panel">
      <div className="panel-heading"><div><p className="eyebrow">RUNTIME STATE</p><h3>当前状态</h3></div><span className={`state-dot state-dot--${uiState}`} /></div>
      <div className={`state-card state-card--${uiState}`}><StateIcon state={uiState} /><div><strong>{STATE_LABELS[uiState]}</strong><span>{STATE_DESCRIPTIONS[uiState]}</span></div></div>
      {lastError ? <div className="error-note"><XCircle aria-hidden="true" /><span>{lastError}</span></div> : null}
      <div className="status-details"><div><span>WebSocket</span><strong>{connection === "connected" ? "在线" : connection === "connecting" ? "连接中" : "离线"}</strong></div><div><span>最近活动</span><strong>{lastActivity ? new Date(lastActivity).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : "--"}</strong></div></div>
      {connection !== "connected" ? <button type="button" className="panel-action" onClick={onReconnect}><RefreshCw aria-hidden="true" />重新连接</button> : null}
    </section>
  );
}

function StateIcon({ state }: { state: UiState }) {
  const Icon = state === "recording" ? Mic2 : state === "playing" ? Volume2 : state === "generating" ? Sparkles : state === "transcribing" ? Ear : state === "error" ? XCircle : Check;
  return <Icon aria-hidden="true" className={state === "generating" || state === "transcribing" ? "pulse" : undefined} />;
}

function PipelinePanel({ uiState }: { uiState: UiState }) {
  const steps: Array<{ key: UiState; label: string; detail: string; icon: typeof Mic }> = [
    { key: "recording", label: "麦克风", detail: "C922 / 16 kHz", icon: Mic },
    { key: "transcribing", label: "中文识别", detail: "sherpa-onnx", icon: Ear },
    { key: "generating", label: "本地生成", detail: "TinyLlama ACL/OM", icon: Sparkles },
    { key: "playing", label: "语音播放", detail: "USB speaker", icon: Volume2 },
  ];
  const activeIndex = uiState === "ready" || uiState === "error" ? -1 : steps.findIndex((item) => item.key === uiState);
  return <section className="side-panel pipeline-panel"><div className="panel-heading"><div><p className="eyebrow">VOICE PIPELINE</p><h3>处理链路</h3></div><Server aria-hidden="true" /></div><div className="pipeline-list">{steps.map((step, index) => { const Icon = step.icon; const active = index === activeIndex; const complete = activeIndex > index; return <div className={`pipeline-step ${active ? "is-active" : ""} ${complete ? "is-complete" : ""}`} key={step.key}><div className="pipeline-icon"><Icon aria-hidden="true" /></div><div><strong>{step.label}</strong><span>{step.detail}</span></div><span className="pipeline-mark">{complete ? <Check aria-hidden="true" /> : active ? <LoaderCircle aria-hidden="true" className="spin" /> : ""}</span></div>; })}</div></section>;
}

function AudioPanel({ meta }: { meta: { microphone: string; speaker: string } }) {
  return <section className="side-panel audio-panel"><div className="panel-heading"><div><p className="eyebrow">AUDIO ROUTING</p><h3>板端音频设备</h3></div><MonitorSpeaker aria-hidden="true" /></div><div className="device-row"><div className="device-icon"><Mic aria-hidden="true" /></div><div><span>输入</span><strong title={meta.microphone}>{meta.microphone}</strong></div><span className="device-status">已配置</span></div><div className="device-row"><div className="device-icon"><Volume2 aria-hidden="true" /></div><div><span>输出</span><strong title={meta.speaker}>{meta.speaker}</strong></div><span className="device-status">已配置</span></div><p className="panel-caption">音频由开发板 PulseAudio 管理，浏览器不采集音频。</p></section>;
}

function ServerPanel({ connection }: { connection: ConnectionState }) {
  return <section className="side-panel server-panel"><div className="panel-heading"><div><p className="eyebrow">LOCAL SERVICES</p><h3>本地服务</h3></div><Cpu aria-hidden="true" /></div><div className="server-row"><span className="server-dot is-online" /><div><strong>聊天控制台</strong><span>0.0.0.0:7862</span></div><b>运行中</b></div><div className="server-row"><span className={`server-dot ${connection === "connected" ? "is-online" : "is-idle"}`} /><div><strong>语音会话</strong><span>/api/ws</span></div><b>{connection === "connected" ? "已连接" : "等待"}</b></div><div className="server-row"><span className="server-dot is-online" /><div><strong>LLM 网关</strong><span>127.0.0.1:7861</span></div><b>内部</b></div></section>;
}

export default App;
