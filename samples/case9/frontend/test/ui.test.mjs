import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const [app, protocol, styles, packageJson] = await Promise.all([
  readFile(join(root, "src", "App.tsx"), "utf8"),
  readFile(join(root, "src", "protocol.ts"), "utf8"),
  readFile(join(root, "src", "styles.css"), "utf8"),
  readFile(join(root, "package.json"), "utf8"),
]);

test("local chat exposes the planned websocket commands and events", () => {
  for (const item of ["hello", "text", "ptt_start", "ptt_stop", "clear", "ready", "recording", "transcribing", "generating", "playing", "error"]) {
    assert.match(app + protocol, new RegExp(item));
  }
  assert.match(app, /\/api\/ws/);
  assert.match(app, /WebSocket/);
});

test("voice controls stay server-side and never request browser microphone access", () => {
  assert.match(app, /按住说话/);
  assert.match(app, /onPointerDown/);
  assert.match(app, /onPointerUp/);
  assert.match(app, /uiState !== "ready"/);
  assert.doesNotMatch(app, /getUserMedia|MediaRecorder|mediaDevices/);
  assert.match(app, /浏览器不访问麦克风/);
});

test("LAN warning, memory-only session, and board pipeline are visible", () => {
  assert.match(app, /未鉴权 LAN 实验模式/);
  assert.match(app, /不保存录音文件/);
  assert.match(app, /TinyLlama ACL\/OM/);
  assert.match(app, /abortAssistantStream/);
  assert.match(app, /清空会话/);
  assert.match(app, /0\.0\.0\.0:7862/);
});

test("responsive touch layout and accessible command labels are encoded", () => {
  assert.match(styles, /@media \(max-width: 760px\)/);
  assert.match(styles, /--touch: 52px/);
  assert.match(app, /aria-label=\{pttActive \? "松开结束录音" : "按住说话"\}/);
  assert.match(app, /title="重新连接本地服务"/);
  const pkg = JSON.parse(packageJson);
  assert.equal(pkg.scripts.test, "node --test test/*.test.mjs");
  assert.ok(pkg.dependencies["lucide-react"]);
});
