window.__ModuleLoader__.load({
	id: "dsh-plugin-ascend310b-debug",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });

		const react = require("react");
		const h = react.createElement;

		/** The id shared by the sidebar entry and the main panel it opens. */
		const PANEL_ID = "ascend310b-debug";
		/** Visible name. It names the chip family so the target is unmistakable. */
		const PANEL_TITLE = "昇腾310B调试";
		/** Read-only report route registered by the host half. */
		const REPORT_URL = "/ascend310b-debug/report.json";
		/** Probe route registered by the host half. */
		const PROBE_URL = "/ascend310b-debug/probe";
		/** Discovery route registered by the host half. */
		const DISCOVER_URL = "/ascend310b-debug/discover";
		/** Serial network-probe route registered by the host half. */
		const SERIAL_URL = "/ascend310b-debug/serial-probe";
		/** Delay between automatic read-only probes while live monitoring is on. */
		const LIVE_INTERVAL_MS = 60000;

		/** Class-name prefix keeping this plugin's styles out of other panels. */
		const CSS = `
.ad-root{height:100%;overflow:auto;padding:18px 22px 40px;box-sizing:border-box;color:var(--dsw-alias-label-primary);font-size:13px;line-height:20px;max-width:1320px;margin:0 auto}
.ad-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.ad-title{font-size:16px;font-weight:600;margin:0}
.ad-sub{color:var(--dsw-alias-label-tertiary);font-size:12px}
.ad-actions{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.ad-button{border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-button-elevated-fill);color:var(--dsw-alias-label-primary);border-radius:6px;padding:4px 12px;font:inherit;cursor:pointer;white-space:nowrap}
.ad-button:hover{background:var(--dsw-alias-interactive-bg-hover)}
.ad-button:disabled{opacity:.5;cursor:default}
.ad-button.ad-primary{border-color:var(--dsw-alias-state-business-primary);color:var(--dsw-alias-state-business-primary)}
.ad-select{border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-button-elevated-fill);color:var(--dsw-alias-label-primary);border-radius:6px;padding:4px 8px;font:inherit}
.ad-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:8px;margin:14px 0}
.ad-card{border:.5px solid var(--dsw-alias-border-l1);border-radius:8px;padding:9px 11px;min-width:0}
.ad-card b{display:block;font-size:16px;font-weight:600;line-height:22px;overflow-wrap:anywhere}
.ad-card span{display:block;color:var(--dsw-alias-label-tertiary);font-size:12px}
.ad-card i{display:block;color:var(--dsw-alias-label-tertiary);font-size:11px;font-style:normal;margin-top:2px;overflow-wrap:anywhere}
.ad-section{margin-top:20px}
.ad-section h3{font-size:13px;font-weight:600;margin:0 0 8px}
.ad-dependency{margin-top:12px;padding:10px 11px;border:.5px solid var(--dsw-alias-border-l1);border-radius:7px;min-width:0}
.ad-install-hint{margin-top:10px;color:var(--dsw-alias-label-secondary);font-size:12px}
.ad-table{width:100%;border-collapse:collapse;font-size:12px}
.ad-table th{text-align:left;color:var(--dsw-alias-label-tertiary);font-weight:500;border-bottom:.5px solid var(--dsw-alias-border-l1);padding:4px 8px}
.ad-table td{border-bottom:.5px solid var(--dsw-alias-border-l1);padding:4px 8px;vertical-align:top;overflow-wrap:anywhere}
.ad-mono{font-family:var(--ds-font-family-code);color:var(--dsw-alias-label-secondary);font-size:12px}
.ad-verdict{display:inline-block;border-radius:999px;padding:1px 9px;font-size:12px;border:.5px solid var(--dsw-alias-border-l2)}
.ad-verdict-ok{color:var(--dsw-alias-state-success-primary,#2f9e44);border-color:var(--dsw-alias-state-success-primary,#2f9e44)}
.ad-verdict-warn{color:var(--dsw-alias-state-warning-primary,#c98a00);border-color:var(--dsw-alias-state-warning-primary,#c98a00)}
.ad-verdict-info{color:var(--dsw-alias-state-business-primary)}
.ad-note{color:var(--dsw-alias-label-tertiary);font-size:12px}
.ad-empty{color:var(--dsw-alias-label-tertiary);padding:10px 0}
.ad-error{color:var(--dsw-alias-state-error-primary);white-space:pre-wrap}
.ad-list{margin:0;padding-left:18px}
.ad-list li{margin:2px 0}
.ad-pill{display:inline-block;border:.5px solid var(--dsw-alias-border-l1);border-radius:999px;padding:0 8px;margin:0 6px 6px 0;font-size:12px;cursor:pointer;background:transparent;color:var(--dsw-alias-label-primary)}
.ad-pill:hover{background:var(--dsw-alias-interactive-bg-hover)}
.ad-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}
.ad-pre{margin:6px 0 0;padding:8px 10px;border:.5px solid var(--dsw-alias-border-l1);border-radius:6px;background:var(--dsw-alias-bg-l1,#0000);font-family:var(--ds-font-family-code);font-size:11px;line-height:16px;max-height:220px;overflow:auto;white-space:pre-wrap}
.ad-device-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
.ad-device-row{border:.5px solid var(--dsw-alias-border-l1);border-radius:7px;padding:9px 11px;min-width:0}
.ad-device-name{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-weight:600}
.ad-device-name .ad-note{font-family:var(--ds-font-family-code);font-weight:400}
.ad-device-value{margin:6px 0 0;max-height:180px;overflow:auto;white-space:pre-wrap;word-break:break-word;font-family:var(--ds-font-family-code);font-size:11px;line-height:16px;color:var(--dsw-alias-label-secondary)}
.ad-banner{border:.5px solid var(--dsw-alias-state-warning-primary,#c98a00);border-radius:8px;padding:8px 11px;margin-top:12px;font-size:12px;color:var(--dsw-alias-label-secondary)}
.ad-banner.ad-scope-block{border-color:var(--dsw-alias-state-error-primary,#d92d20);color:var(--dsw-alias-state-error-primary,#d92d20);font-weight:500}
.ad-scope-ok{margin-top:10px;font-size:12px;color:var(--dsw-alias-label-tertiary)}
.ad-tabs{display:flex;gap:2px;border-bottom:.5px solid var(--dsw-alias-border-l1);margin:14px 0 0}
.ad-tab{border:0;border-bottom:2px solid transparent;background:transparent;color:var(--dsw-alias-label-tertiary);padding:6px 12px 7px;font:inherit;cursor:pointer}
.ad-tab:hover{color:var(--dsw-alias-label-primary)}
.ad-tab.ad-active{color:var(--dsw-alias-state-business-primary);border-bottom-color:var(--dsw-alias-state-business-primary);font-weight:600}
.ad-overview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:14px}
.ad-asset-head{display:flex;justify-content:space-between;gap:12px;align-items:baseline;flex-wrap:wrap}
.ad-asset-mac{font-family:var(--ds-font-family-code);font-size:11px;color:var(--dsw-alias-label-secondary);word-break:break-all}
.ad-change{color:var(--dsw-alias-state-warning-primary,#c98a00);font-size:12px}
.ad-stack{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.ad-stack span{border:.5px solid var(--dsw-alias-border-l1);border-radius:999px;padding:1px 8px;color:var(--dsw-alias-label-tertiary);font-size:11px}
.ad-toolbar-note{margin-top:10px}
.ad-workbench{display:grid;grid-template-columns:248px minmax(0,1fr);gap:20px;margin-top:14px;align-items:start}
.ad-rail{border-right:.5px solid var(--dsw-alias-border-l1);padding-right:14px;min-width:0}
.ad-rail-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:9px}
.ad-rail-head h3{font-size:13px;font-weight:600;margin:0}
.ad-board-list{display:flex;flex-direction:column;gap:6px}
.ad-board-item{display:block;width:100%;text-align:left;border:.5px solid var(--dsw-alias-border-l1);border-radius:7px;background:transparent;color:var(--dsw-alias-label-primary);padding:8px 9px;font:inherit;cursor:pointer}
.ad-board-item:hover{background:var(--dsw-alias-interactive-bg-hover)}
.ad-board-item.ad-selected{border-color:var(--dsw-alias-state-business-primary);background:var(--dsw-alias-interactive-bg-hover)}
.ad-board-title{display:flex;align-items:center;gap:6px;font-weight:600;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ad-board-dot{width:7px;height:7px;border-radius:50%;background:var(--dsw-alias-label-tertiary);flex:none}
.ad-board-dot.ad-online{background:var(--dsw-alias-state-success-primary,#2f9e44)}
.ad-board-dot.ad-changed{background:var(--dsw-alias-state-warning-primary,#c98a00)}
.ad-board-meta{display:block;color:var(--dsw-alias-label-tertiary);font-size:11px;line-height:16px;margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ad-board-mac{font-family:var(--ds-font-family-code);font-size:10px;color:var(--dsw-alias-label-tertiary);margin-top:2px;overflow:hidden;text-overflow:ellipsis}
.ad-main{min-width:0}
.ad-empty-rail{border:.5px dashed var(--dsw-alias-border-l2);border-radius:7px;padding:10px;color:var(--dsw-alias-label-tertiary);font-size:12px}
.ad-heading{display:flex;flex-direction:column;gap:2px;min-width:0}
.ad-heading .ad-sub{margin:0}
.ad-current{display:flex;align-items:center;gap:7px;min-width:0;margin-left:2px;padding:4px 9px;border:.5px solid var(--dsw-alias-border-l1);border-radius:6px;background:var(--dsw-alias-bg-l1,#0000)}
.ad-current strong{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ad-current code{font-family:var(--ds-font-family-code);font-size:10px;color:var(--dsw-alias-label-tertiary);white-space:nowrap}
.ad-toolbar{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:12px}
.ad-overview-grid{grid-template-columns:repeat(auto-fit,minmax(142px,1fr));gap:8px;margin-top:12px}
.ad-metric{position:relative;overflow:hidden}
.ad-metric .ad-metric-bar{height:4px;background:var(--dsw-alias-border-l1);border-radius:2px;margin-top:8px;overflow:hidden}
.ad-metric .ad-metric-bar span{display:block;height:100%;background:var(--dsw-alias-state-business-primary);border-radius:2px}
.ad-peripheral-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:9px}
.ad-peripheral-card{border:.5px solid var(--dsw-alias-border-l1);border-radius:7px;padding:10px;min-width:0;background:var(--dsw-alias-bg-l1,#0000)}
.ad-peripheral-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
.ad-peripheral-head strong{font-size:12px}
.ad-status-ok{color:var(--dsw-alias-state-success-primary,#2f9e44);font-size:11px;white-space:nowrap}
.ad-peripheral-items{display:flex;flex-direction:column;gap:5px;margin-top:9px}
.ad-peripheral-item{display:flex;justify-content:space-between;gap:10px;align-items:baseline;min-width:0;border-top:.5px solid var(--dsw-alias-border-l1);padding-top:5px}
.ad-peripheral-item span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ad-peripheral-item code{font-family:var(--ds-font-family-code);font-size:10px;color:var(--dsw-alias-label-tertiary);white-space:nowrap}
.ad-peripheral-raw{margin-top:9px;color:var(--dsw-alias-label-tertiary);font-size:11px}
.ad-peripheral-raw summary{cursor:pointer}
.ad-peripheral-raw pre{max-height:110px;overflow:auto;white-space:pre-wrap;font-size:10px;margin:6px 0 0;font-family:var(--ds-font-family-code)}
.ad-raw-details summary{cursor:pointer;font-weight:600;font-size:12px;margin-bottom:10px}
.ad-device-list{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.ad-workbench{grid-template-columns:minmax(220px,268px) minmax(0,1fr);gap:18px}
.ad-rail{position:sticky;top:12px;max-height:calc(100vh - 36px);overflow:auto}
.ad-board-item{min-height:62px}
.ad-board-title{font-size:11px}
.ad-section{padding-top:13px;border-top:.5px solid var(--dsw-alias-border-l1)}
.ad-section:first-child{border-top:0;padding-top:0}
.ad-tabs{overflow-x:auto;white-space:nowrap;scrollbar-width:thin}
.ad-tab{flex:none}
@media(max-width:720px){.ad-root{padding:14px 14px 32px}.ad-head{align-items:flex-start}.ad-actions{width:100%;margin-left:0}.ad-current{order:3;width:100%;margin-left:0}.ad-actions .ad-select{flex:1;min-width:180px}.ad-toolbar .ad-button{flex:1}.ad-table{display:block;overflow-x:auto;white-space:nowrap}.ad-table td,.ad-table th{white-space:normal;min-width:90px}.ad-workbench{grid-template-columns:1fr;gap:12px}.ad-rail{position:static;max-height:none;border-right:0;border-bottom:.5px solid var(--dsw-alias-border-l1);padding:0 0 10px}.ad-board-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}.ad-overview-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.ad-peripheral-grid{grid-template-columns:1fr}}
`;

		/** Install this plugin's stylesheet once. */
		function installStyles() {
			if (document.querySelector('style[data-plugin="dsh-plugin-ascend310b-debug"]') !== null) return;
			const tag = document.createElement("style");
			tag.dataset.plugin = "dsh-plugin-ascend310b-debug";
			tag.textContent = CSS;
			document.head.appendChild(tag);
		}

		/** Wait until the panel is wide enough for two columns. */
		function stamp(iso) {
			if (typeof iso !== "string" || iso === "") return "";
			const date = new Date(iso);
			return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN");
		}

		/** A board is live only after a recent probe, not merely because it is configured. */
		function isRecent(iso, minutes = 15) {
			if (typeof iso !== "string" || iso === "") return false;
			const age = Date.now() - new Date(iso).getTime();
			return Number.isFinite(age) && age >= 0 && age <= minutes * 60 * 1000;
		}

		/** Human-readable serial state for a board. */
		function serialState(board) {
			const serial = board?.serial;
			if (!serial || typeof serial !== "object") return "串口未发现";
			const port = serial.port ? ` · ${serial.port}` : "";
			const when = serial.lastSeen ? ` · ${stamp(serial.lastSeen)}` : "";
			return serial.status === "reachable" ? `串口在线${port}${when}` : `串口${port}${when}`;
		}

		/** Canonical display name shared by the rail, asset table and header. */
		function boardName(board = {}) {
			const model = typeof board.model === "string" ? board.model.replace(/\s+/g, " ").trim() : "";
			const soc = String(board.soc ?? "");
			const tier = String(board.tier ?? "");
			if (/310B4/i.test(soc) || /\b8T\b/i.test(tier) || /\b8T\b/i.test(model)) return "Orange Pi AI Pro 8T";
			if (/310B1/i.test(soc) || /\b20T\b/i.test(tier) || /\b20T\b/i.test(model)) return "Orange Pi AI Pro 20T";
			return model || board.hostname || board.id || "未命名开发板";
		}

		/** Canonical name for a configured target before inventory is available. */
		function targetName(target = {}) {
			const label = String(target.label ?? "").replace(/\s+/g, " ").trim();
			if (/8T|310B4/i.test(label)) return "Orange Pi AI Pro 8T";
			if (/20T|310B1/i.test(label)) return "Orange Pi AI Pro 20T";
			const known = boardName({ mac: target.mac, hostname: target.hostname, id: target.id });
			if (known !== target.hostname && known !== target.id && known !== "未命名开发板") return known;
			return label || target.host || target.id || "未命名开发板";
		}

		/** Compact hardware identifier used beside a canonical name. */
		function macSuffix(mac) {
			return typeof mac === "string" && mac.length >= 5 ? ` · ${mac.slice(-5)}` : "";
		}

		/** Readable text from a fact, preserving a missing value as null. */
		function factValue(payload, key) {
			const fact = payload?.facts?.[key];
			if (fact === undefined || fact === null) return null;
			const value = fact.value;
			if (value === null || value === undefined || value === "") return null;
			if (typeof value === "boolean") return value ? "是" : "否";
			return String(value);
		}

		/** Split raw device enumeration output into useful non-empty lines. */
		function factLines(payload, key) {
			const value = factValue(payload, key);
			if (value === null) return [];
			return value
				.split(/\r?\n/)
				.map((line) => line.trim())
				.filter((line) => line !== "" && !/^(?:UNKNOWN|NOT_FOUND|not-found)$/i.test(line));
		}

		/** Parse lsusb output into stable rows. */
		function usbItems(payload) {
			return factLines(payload, "usbDevices").flatMap((line) => {
				const match = /^Bus\s+(\d+)\s+Device\s+(\d+):\s+ID\s+([0-9a-f]{4}:[0-9a-f]{4})\s*(.*)$/i.exec(line);
				return match === null
					? []
					: [{ id: match[3], label: match[4] || "USB 设备", detail: `Bus ${match[1]} · Device ${match[2]}` }];
			});
		}

		/** Extract V4L2 nodes and the preceding friendly label. */
		function cameraItems(payload) {
			const lines = factLines(payload, "videoDevices");
			const items = [];
			for (let index = 0; index < lines.length; index += 1) {
				const match = /^(\/dev\/(?:video|media)\d+)\s*$/i.exec(lines[index]);
				if (match === null) continue;
				items.push({
					id: match[1],
					label: lines[index - 1] && lines[index - 1] !== match[1] ? lines[index - 1] : "V4L2 设备",
					detail: match[1]
				});
			}
			return items;
		}

		/** Parse ALSA/PulseAudio enumeration lines into named devices. */
		function audioItems(payload, key) {
			const lines = factLines(payload, key);
			if (key === "audioSinks") {
				return lines.map((line) => ({ id: line, label: line, detail: line }));
			}
			return lines.flatMap((line) => {
				const match = /^(?:card|device)\s+[^:]+:\s*(.+)$/i.exec(line);
				return match === null ? [] : [{ id: line, label: match[1], detail: line }];
			});
		}

		/** Stable peripheral summary used by both overview and device views. */
		function peripheralInventory(payload) {
			const usb = usbItems(payload);
			const cameras = cameraItems(payload);
			const playback = audioItems(payload, "audioPlayback");
			const capture = audioItems(payload, "audioCapture");
			const sinks = audioItems(payload, "audioSinks");
			return {
				usb,
				cameras,
				playback,
				capture,
				sinks,
				groups: [
					{ id: "camera", label: "摄像头 / V4L2", items: cameras, raw: factValue(payload, "videoDevices"), source: "v4l2-ctl" },
					{ id: "playback", label: "音频播放", items: playback, raw: factValue(payload, "audioPlayback"), source: "aplay -l" },
					{ id: "capture", label: "音频采集", items: capture, raw: factValue(payload, "audioCapture"), source: "arecord -l" },
					{ id: "audio-sink", label: "音频 Sink", items: sinks, raw: factValue(payload, "audioSinks"), source: "pactl" },
					{ id: "usb", label: "USB 外设", items: usb, raw: factValue(payload, "usbDevices"), source: "lsusb" }
				]
			};
		}

		/** One peripheral group with a compact count and an expandable raw list. */
		function PeripheralGroup(props) {
			const group = props.group;
			const status = group.raw === null || group.raw === undefined
				? "未采集"
				: group.items.length > 0
					? `${group.items.length} 个已发现`
					: "未发现";
			return h(
				"div",
				{ className: "ad-peripheral-card" },
				h(
					"div",
					{ className: "ad-peripheral-head" },
					h("strong", null, group.label),
					h("span", { className: group.items.length > 0 ? "ad-status-ok" : "ad-note" }, status)
				),
				group.items.length > 0
					? h(
							"div",
							{ className: "ad-peripheral-items" },
							group.items.slice(0, 8).map((item) =>
								h(
									"div",
									{ className: "ad-peripheral-item", key: `${group.id}:${item.id}` },
									h("span", null, item.label),
									h("code", null, item.detail)
								)
							),
							group.items.length > 8 ? h("span", { className: "ad-note" }, `另有 ${group.items.length - 8} 个`) : null
						)
					: h("div", { className: "ad-note" }, group.raw === null || group.raw === undefined ? "该类别尚未采集。" : "已采集，但当前没有枚举到设备。"),
				h("details", { className: "ad-peripheral-raw" }, h("summary", null, `原始输出 · ${group.source}`), h("pre", null, group.raw || group.items.map((item) => item.detail).join("\n") || "无输出"))
			);
		}

		/** One headline card, with the evidence line underneath. */
		function Card(props) {
			return h(
				"div",
				{ className: "ad-card", title: props.title ?? "" },
				h("span", null, props.label),
				h("b", null, props.value === null || props.value === undefined || props.value === "" ? "未采集" : props.value),
				props.source ? h("i", null, props.source) : null
			);
		}

		/** Verdict badge, coloured by how strong the evidence is. */
		function Verdict(props) {
			const verdict = props.verdict ?? "incomplete";
			const tone =
				verdict === "observed-match"
					? "ad-verdict-ok"
					: verdict === "documented-pairing"
						? "ad-verdict-info"
						: verdict === "documented-unpaired" || verdict === "undocumented-cann"
							? "ad-verdict-warn"
							: "ad-verdict-info";
			return h("span", { className: `ad-verdict ${tone}` }, `${verdict} · ${props.evidence ?? "untested"}`);
		}

		/** The fact table rendered from whatever the board actually reported. */
		function FactTable(props) {
			const facts = props.facts ?? {};
			const keys = Object.keys(facts);
			if (keys.length === 0) return h("div", { className: "ad-empty" }, "尚未探测：点击右上角「运行探测」读取板端信息。");
			return h(
				"table",
				{ className: "ad-table" },
				h("thead", null, h("tr", null, h("th", null, "字段"), h("th", null, "板端实际值"), h("th", null, "来源节"))),
				h(
					"tbody",
					null,
					keys.map((key) =>
						h(
							"tr",
							{ key },
							h("td", { className: "ad-mono" }, key),
							h("td", null, factValue(props, key)),
							h("td", { className: "ad-mono" }, facts[key].source ?? "")
						)
					)
				)
			);
		}

		/** Sound, USB and camera inventory, kept separate from version facts. */
		function DeviceFacts(props) {
			const fields = [
				["soundCards", "声卡", "/proc/asound/cards"],
				["audioPlayback", "播放设备", "aplay -l"],
				["audioCapture", "录音设备", "arecord -l"],
				["audioSinks", "音频 Sink", "pactl list short sinks"],
				["usbDevices", "USB 设备", "lsusb"],
				["videoDevices", "摄像头 / V4L2", "v4l2-ctl --list-devices + /dev/video*"]
			];
			const hasAny = fields.some(([key]) => factValue(props, key) !== null);
			if (!hasAny) return h("div", { className: "ad-empty" }, "尚未探测：点击右上角「运行探测」读取外设信息。");
			return h(
				"div",
				{ className: "ad-device-list" },
				fields.map(([key, label, source]) => {
					const value = factValue(props, key);
					return h(
						"div",
						{ className: "ad-device-row", key },
						h("div", { className: "ad-device-name" }, label, h("span", { className: "ad-note" }, source)),
						h("pre", { className: "ad-device-value" }, value ?? "未发现/未安装")
					);
				})
			);
		}

		/** Equipment section: the two configured boards plus the active one. */
		function Targets(props) {
			const targets = props.targets ?? [];
			if (targets.length === 0) {
				return h(
					"div",
					{ className: "ad-empty" },
					"未配置开发板。请在 profile 的 cordis.patch.yml 里为 dsh-plugin-ascend310b-debug 设置 config.targets。"
				);
			}
			return h(
				"table",
				{ className: "ad-table" },
				h("thead", null, h("tr", null, h("th", null, "目标"), h("th", null, "地址"), h("th", null, "传输"), h("th", null, "最近状态"), h("th", null, "说明"))),
				h(
					"tbody",
					null,
					targets.map((target) =>
						h(
							"tr",
							{ key: target.id },
							h("td", null, `${targetName(target)}${target.isDefault ? "（默认）" : ""}`),
							h("td", { className: "ad-mono" }, `${target.user ? `${target.user}@` : ""}${target.host}`),
							h("td", null, target.transport),
							h("td", { className: "ad-note" }, isRecent(target.lastSeen) ? "近期在线" : target.lastSeen ? `历史记录 · ${stamp(target.lastSeen)}` : "未探测"),
							h("td", { className: "ad-note" }, target.note ?? "")
						)
					)
				)
			);
		}

		/** Per-case apt/Python/device requirements; display only, never auto-install. */
		function CaseRequirements(props) {
			const cases = props.cases ?? [];
			const current = cases.find((item) => item.id === props.selected) ?? cases[0] ?? null;
			if (current === null) return h("div", { className: "ad-empty" }, "尚无案例依赖记录。");
			const packages = current.apt ?? [];
			const installable = packages.filter((pkg) => pkg.level !== "optional").map((pkg) => pkg.name);
			const aptCommand = `ascend_apt_install target=<target> packages=[${installable.map((name) => JSON.stringify(name)).join(", ")}] update=true dryRun=true allowStateChange=true`;
			return h(
				"div",
				{ className: "ad-dependency" },
				h("div", { className: "ad-asset-head" }, h("h3", null, `${current.id} · ${current.title}`), h("span", { className: "ad-note" }, current.device ?? "")),
				h("div", { className: "ad-note" }, `来源：${current.source ?? "未记录"}${(current.groups ?? []).length > 0 ? ` · 用户组：${current.groups.join("、")}` : ""}`),
				packages.length > 0
					? h(
							"table",
							{ className: "ad-table" },
							h("thead", null, h("tr", null, h("th", null, "系统程序"), h("th", null, "用途"), h("th", null, "级别"), h("th", null, "来源"))),
							h(
								"tbody",
								null,
								packages.map((pkg) =>
									h(
										"tr",
										{ key: pkg.name },
										h("td", { className: "ad-mono" }, pkg.name),
										h("td", null, pkg.purpose),
										h("td", null, pkg.level),
										h("td", { className: "ad-note" }, pkg.source)
									)
								)
							)
						)
					: h("div", { className: "ad-empty" }, "该案例没有已记录的 apt 系统程序依赖。"),
				(current.python ?? []).length > 0
					? h("div", { className: "ad-note" }, `Python/运行时依赖：${current.python.join("；")}`)
					: null,
				installable.length > 0
					? h("div", { className: "ad-install-hint" }, "安装提示（先在对话中 dry-run，不会由面板自动提权）：", h("pre", { className: "ad-pre" }, aptCommand))
					: null
			);
		}

		/** Navigation for the operational panel. */
		function TabBar(props) {
			const tabs = [
				["overview", "概览"],
				["devices", "外设"],
				["assets", "开发板资产"],
				["debug", "调试"],
				["raw", "原始输出"]
			];
			return h(
				"div",
				{ className: "ad-tabs", role: "tablist" },
				tabs.map(([id, label]) =>
					h(
						"button",
						{
							key: id,
							type: "button",
							role: "tab",
							"aria-selected": props.active === id,
							className: props.active === id ? "ad-tab ad-active" : "ad-tab",
							onClick: () => props.onChange(id)
						},
						label
					)
				)
			);
		}

		/** MAC-keyed inventory view; one row is one physical board. */
		function BoardTable(props) {
			const boards = props.boards ?? [];
			if (boards.length === 0) {
				return h(
					"div",
					{ className: "ad-empty" },
					"尚无资产表记录。点击「局域网扫描」扫描私有网段，或点击「串口发现」从 USB 串口读取开发板。"
				);
			}
			return h(
				"table",
				{ className: "ad-table" },
				h(
					"thead",
					null,
					h("tr", null, h("th", null, "硬件"), h("th", null, "系统 / 内存"), h("th", null, "网络"), h("th", null, "串口"), h("th", null, "最近发现"), h("th", null, "变更"))
				),
				h(
					"tbody",
					null,
					boards.map((board) =>
						h(
							"tr",
							{ key: board.mac ?? board.id },
							h(
								"td",
								null,
								h("div", null, boardName(board)),
								(board.model && board.model !== boardName(board)) ? h("div", { className: "ad-note" }, board.model) : null,
								h("div", { className: "ad-asset-mac" }, board.mac ?? "MAC 未采集"),
								h("div", { className: "ad-note" }, `${board.soc ?? "SoC 未采集"} · ${board.tier ?? "档位未采集"}`)
							),
							h(
								"td",
								null,
								h("div", { className: "ad-mono" }, `CANN ${board.cann ?? "未采集"}`),
								h("div", { className: "ad-mono" }, `驱动 ${board.driver ?? "未采集"}`),
								h("div", { className: "ad-mono" }, `内存 ${board.memoryUsedMb ?? "?"} / ${board.memoryTotalMb ?? "?"} MB`)
							),
							h(
								"td",
								null,
								h("div", { className: "ad-mono" }, board.lastIp ?? "IP 未采集"),
								h("div", { className: "ad-note" }, board.hostname ?? "主机名未采集")
							),
							h(
								"td",
								{ className: "ad-note" },
								h("div", null, serialState(board)),
								h("div", null, board.serial?.user ?? "HwHiAiUser")
							),
							h(
								"td",
								{ className: "ad-note" },
								h("div", null, board.lastSeen ? stamp(board.lastSeen) : "未探测"),
								h("div", null, isRecent(board.lastSeen) ? "近期在线" : board.lastSeen ? "历史记录" : "未探测")
							),
							h(
								"td",
								null,
								board.changesCount > 0
									? h(
											"span",
											{
												className: "ad-change",
												title: (board.latestChange?.fields ?? []).map((field) => `${field.name}: ${field.from ?? "未记录"} -> ${field.to ?? "未记录"}`).join("\n")
											},
											`${board.changesCount} 次 · ${(board.latestChange?.fields ?? []).map((field) => field.name).join("、") || "系统变化"}`
										)
									: h("span", { className: "ad-note" }, board.configuredId ? "已关联配置" : "已发现")
							)
						)
					)
				)
			);
		}

		/** Left navigation: one row per physical board, keyed by MAC. */
		function BoardRail(props) {
			const boards = props.boards ?? [];
			const targets = props.targets ?? [];
			const matchedTarget = (board) =>
				targets.find(
					(target) =>
						target.id === board.configuredId ||
						target.discoveredId === board.id ||
						(board.mac !== null && target.mac === board.mac)
				);
			const rows = boards.length > 0
				? boards.map((board) => {
						const target = matchedTarget(board);
						const recent = isRecent(board.lastSeen);
						return {
							key: board.id,
							targetId: target?.id ?? board.id,
							title: `${boardName(board)}${macSuffix(board.mac)}`,
							secondary: target ? targetName(target) : "",
							mac: board.mac,
							detail: `${board.lastIp ?? "无 IP"} · ${board.tier ?? "档位未知"} · ${board.memoryUsedMb ?? "?"} / ${board.memoryTotalMb ?? "?"} MB · ${recent ? "网络近期在线" : board.lastSeen ? "网络历史记录" : "未探测"} · ${serialState(board)}`,
							changed: board.changesCount > 0,
							online: recent
						};
					})
				: targets.map((target) => ({
						key: target.id,
						targetId: target.id,
						title: `${targetName(target)}${macSuffix(target.mac)}`,
						mac: target.mac,
						detail: `${target.host} · ${isRecent(target.lastSeen) ? "近期在线" : "未建档或未探测"}`,
						changed: false,
						online: isRecent(target.lastSeen)
					}));
			if (rows.length === 0) return h("div", { className: "ad-empty-rail" }, "没有已登记开发板。");
			const needle = String(props.query ?? "").trim().toLowerCase();
			const visible = needle === ""
				? rows
				: rows.filter((row) => [row.title, row.mac, row.detail].filter(Boolean).join(" ").toLowerCase().includes(needle));
			if (visible.length === 0) return h("div", { className: "ad-empty-rail" }, "没有匹配的开发板。");
			return h(
				"div",
				{ className: "ad-board-list" },
				visible.map((row) =>
					h(
						"button",
						{
							key: row.key,
							type: "button",
							className: `ad-board-item${props.selected === row.targetId ? " ad-selected" : ""}`,
							title: `${row.title}\n${row.detail}`,
							onClick: () => props.onSelect(row.targetId)
						},
						h(
							"span",
							{ className: "ad-board-title" },
							h("i", { className: `ad-board-dot${row.changed ? " ad-changed" : row.online ? " ad-online" : ""}` }),
							row.title
						),
						h("span", { className: "ad-board-meta" }, row.detail),
						row.secondary && row.secondary !== row.title ? h("span", { className: "ad-board-meta" }, `配置：${row.secondary}`) : null,
						h("span", { className: "ad-board-mac" }, row.mac ? `MAC ${row.mac}` : "MAC 未采集")
					)
				)
			);
		}

		/** The debugging knowledge router: click a symptom to fill the query. */
		function Knowledge(props) {
			const entries = props.knowledge ?? [];
			if (entries.length === 0) return h("div", { className: "ad-empty" }, "没有可用的调试知识条目。");
			return h(
				"div",
				null,
				h(
					"div",
					null,
					entries.map((entry) =>
						h(
							"button",
							{
								key: entry.id,
								type: "button",
								className: "ad-pill",
								title: `${entry.area} · 阶段 ${entry.stage}`,
								onClick: () => props.onPick(entry.symptoms?.[0] ?? entry.title)
							},
							entry.title
						)
					)
				),
				h(
					"div",
					{ className: "ad-note" },
					"点击任一条目把对应症状填入下面的查询框；每条计划都带停止条件，失败按层记录，不写成整机不支持。"
				)
			);
		}

		/** Samples matching the current query. */
		function Samples(props) {
			const samples = props.samples ?? [];
			if (samples.length === 0) return h("div", { className: "ad-empty" }, "没有匹配的样例程序。");
			return h(
				"table",
				{ className: "ad-table" },
				h(
					"thead",
					null,
					h("tr", null, h("th", null, "样例"), h("th", null, "调试原型"), h("th", null, "运行入口"), h("th", null, "依赖"))
				),
				h(
					"tbody",
					null,
					samples.map((sample) =>
						h(
							"tr",
							{ key: sample.id },
							h("td", null, sample.title, h("div", { className: "ad-mono" }, sample.readme ?? "")),
							h("td", null, sample.archetype ?? "未分类"),
							h("td", { className: "ad-mono" }, sample.entry ?? "见 README"),
							h("td", { className: "ad-note" }, (sample.capabilities ?? []).join("、"))
						)
					)
				)
			);
		}

		/**
		 * The Ascend 310B debugging panel.
		 *
		 * It only ever displays what a probe returned. Before the first probe the
		 * version cards read 未采集, because a hardcoded board version in a panel
		 * would be exactly the kind of invention this plugin exists to prevent.
		 */
		function AscendPanel() {
			const [state, setState] = react.useState({ kind: "loading" });
			const [selected, setSelected] = react.useState("");
			const [query, setQuery] = react.useState("");
			const [live, setLive] = react.useState(false);
			const [tab, setTab] = react.useState("overview");
			const [boardQuery, setBoardQuery] = react.useState("");
			const [dependencyCase, setDependencyCase] = react.useState("case1");

			const load = react.useCallback((fresh) => {
				const url = fresh ? `${REPORT_URL}?fresh=1` : REPORT_URL;
				return fetch(url, { headers: { accept: "application/json" } })
					.then(async (response) => {
						if (!response.ok) throw new Error(`HTTP ${response.status}`);
						const payload = await response.json();
						if (payload?.ok !== true) throw new Error(payload?.error ?? `HTTP ${response.status}（响应缺少 ok=true）`);
						return payload;
					})
					.then((payload) => {
						setState({ kind: "ready", payload });
						setSelected((current) => current || payload.target?.id || payload.targets?.[0]?.id || "");
					})
					.catch((error) => setState({ kind: "failed", message: String(error?.message ?? error) }));
			}, []);

			react.useEffect(() => {
				load(false);
			}, [load]);

			const runProbe = react.useCallback(async (requestedTarget) => {
				const targetId = typeof requestedTarget === "string" && requestedTarget !== "" ? requestedTarget : selected;
				setState((previous) => ({ kind: previous.kind === "ready" ? "probing" : "loading", payload: previous.payload }));
				try {
					const response = await fetch(PROBE_URL, {
						method: "POST",
						headers: { "content-type": "application/json", accept: "application/json" },
						body: JSON.stringify(targetId === "" ? {} : { target: targetId })
					});
					const payload = await response.json();
					if (!response.ok) throw new Error(payload?.error ?? `HTTP ${response.status}`);
					if (payload?.ok === false && payload?.error && payload?.target === undefined && payload?.scope === undefined) {
						throw new Error(payload.error);
					}
					setState({ kind: "ready", payload });
					return payload;
				} catch (error) {
					setState((previous) => ({
						kind: "failed",
						message: String(error?.message ?? error),
						payload: previous.payload
					}));
					return null;
				}
			}, [selected]);

			const selectBoard = react.useCallback((targetId) => {
				setSelected(targetId);
				if (targetId === "") return;
				// While live monitoring is on, changing the target restarts the
				// polling effect; do not fire a second immediate probe.
				if (live && targetId !== selected) return;
				void runProbe(targetId);
			}, [live, runProbe, selected]);

			react.useEffect(() => {
				if (!live) return undefined;
				let cancelled = false;
				let timer = null;
				const tick = async () => {
					if (cancelled) return;
					await runProbe();
					if (!cancelled) timer = setTimeout(tick, LIVE_INTERVAL_MS);
				};
				tick();
				return () => {
					cancelled = true;
					if (timer !== null) clearTimeout(timer);
				};
			}, [live, runProbe]);

			const [discovery, setDiscovery] = react.useState({ kind: "idle" });
			const runDiscovery = react.useCallback(async () => {
				setDiscovery({ kind: "running" });
				try {
					const response = await fetch(DISCOVER_URL, {
						method: "POST",
						headers: { "content-type": "application/json", accept: "application/json" },
						body: "{}"
					});
					const payload = await response.json();
					if (!response.ok || payload.ok !== true) throw new Error(payload.error ?? `HTTP ${response.status}`);
					setDiscovery({ kind: "done", payload });
					await load(true);
					return payload;
				} catch (error) {
					setDiscovery({ kind: "failed", message: String(error?.message ?? error) });
					return null;
				}
			}, [load]);

			const [serial, setSerial] = react.useState({ kind: "idle" });
			const runSerial = react.useCallback(async () => {
				setSerial({ kind: "running" });
				try {
					const response = await fetch(SERIAL_URL, {
						method: "POST",
						headers: { "content-type": "application/json", accept: "application/json" },
						body: "{}"
					});
					const payload = await response.json();
					if (!response.ok || payload.ok !== true) throw new Error(payload.error ?? `HTTP ${response.status}`);
					setSerial({ kind: "done", payload });
					await load(true);
					return payload;
				} catch (error) {
					setSerial({ kind: "failed", message: String(error?.message ?? error) });
					return null;
				}
			}, [load]);

			const head = (payload, busy) =>
				h(
					"div",
					{ className: "ad-head" },
					h(
						"div",
						{ className: "ad-heading" },
						h("h2", { className: "ad-title" }, PANEL_TITLE),
						h("span", { className: "ad-sub" }, "310B1 / 310B4 板卡、CANN、驱动与外设诊断")
					),
					h(
						"div",
						{ className: "ad-current" },
						h(
							"strong",
							null,
							(() => {
								const current = payload?.targets?.find((target) => target.id === selected);
								if (!current) return "未选择开发板";
								const currentBoard = (payload.boards ?? []).find(
									(board) =>
										(board.mac !== null && current.mac === board.mac) ||
										current.discoveredId === board.id ||
										current.id === board.configuredId
								);
								const name = currentBoard ? boardName(currentBoard) : targetName(current);
								const mac = currentBoard?.mac ?? current.mac;
								return `${name}${macSuffix(mac)}`;
							})()
						),
						h(
							"code",
							null,
							(() => {
								const current = payload?.targets?.find((target) => target.id === selected);
								return current ? `${current.user ?? "HwHiAiUser"}@${current.host ?? "未配置地址"}` : "";
							})()
						)
					),
					h(
						"div",
						{ className: "ad-actions" },
						h(
							"button",
							{
								className: "ad-button",
								type: "button",
								disabled: serial.kind === "running",
								title: "通过串口登录，读取板卡网络/MAC，并自动尝试 SSH 探测后写入设备列表",
								onClick: runSerial
							},
							serial.kind === "running" ? "串口发现中…" : "串口发现"
						),
						h(
							"button",
							{
								className: "ad-button",
								type: "button",
								disabled: discovery.kind === "running",
								title: "扫描配置中的私有 CIDR 与本机活动网段，按 MAC 更新资产表和 target 列表",
								onClick: runDiscovery
							},
							discovery.kind === "running" ? "扫描中…" : "局域网扫描"
						),
						h(
							"button",
							{
								className: live ? "ad-button ad-primary" : "ad-button",
								type: "button",
								title: "每 60 秒执行一次只读探测；这是轮询快照，不是板端流式指标",
								onClick: () => setLive((value) => !value)
							},
							live ? "停止实时" : "实时监控"
						),
						h(
							"button",
							{ className: "ad-button ad-primary", type: "button", disabled: busy, onClick: () => runProbe() },
							busy ? "探测中…" : "运行探测"
						),
						h(
							"button",
							{ className: "ad-button", type: "button", disabled: busy, onClick: () => load(true) },
							"刷新"
						)
					)
				);

			if (state.kind === "loading") {
				return h("div", { className: "ad-root" }, head(state.payload, true), h("div", { className: "ad-empty" }, "正在读取插件配置…"));
			}
			if (state.kind === "failed") {
				return h(
					"div",
					{ className: "ad-root" },
					head(state.payload, false),
					h("div", { className: "ad-error" }, `读取失败：${state.message}`),
					state.payload?.targets ? h("div", { className: "ad-section" }, h("h3", null, "开发板"), h(Targets, { targets: state.payload.targets })) : null
				);
			}

			const payload = state.payload;
			const probing = state.kind === "probing";
			const board = payload.board ?? {};
			const pairing = payload.pairing ?? {};
			const facts = payload.facts ?? {};
			const scope = payload.scope ?? null;
			const peripherals = peripheralInventory(payload);
			const memoryUsed = board.memoryUsedMb ?? facts.memoryUsedMb?.value;
			const memoryTotal = board.memoryTotalMb ?? facts.memoryTotalMb?.value;
			const memoryValue =
				memoryUsed !== null && memoryUsed !== undefined && Number.isFinite(Number(memoryUsed))
					? memoryTotal !== null && memoryTotal !== undefined && Number.isFinite(Number(memoryTotal))
						? `${Number(memoryUsed)} / ${Number(memoryTotal)} MB`
						: `${Number(memoryUsed)} MB（总量未采集）`
					: null;
			const terms = query
				.trim()
				.toLowerCase()
				.split(/[\s,，、/]+/)
				.filter((term) => term.length >= 2);
			const matchedSamples = (payload.samples ?? []).filter((sample) => {
				if (terms.length === 0) return true;
				const haystack = [
					sample.id,
					sample.title,
					sample.archetype,
					(sample.capabilities ?? []).join(" "),
					(sample.keywords ?? []).join(" "),
					(sample.searchTerms ?? []).join(" ")
				]
					.join(" ")
					.toLowerCase();
				return terms.some((term) => haystack.includes(term));
			});

			const overview = h(
				"div",
				null,
				scope !== null && scope.applicable === false
					? h("div", { className: "ad-banner ad-scope-block" }, scope.message)
					: null,
				scope !== null && scope.applicable === true
					? h("div", { className: "ad-scope-ok" }, scope.message)
					: null,
				scope !== null && scope.applicable === "unknown" && payload.probedAt
					? h("div", { className: "ad-banner" }, scope.message)
					: null,
				(payload.missingCore ?? []).length > 0
					? h("div", { className: "ad-banner" }, `未采集的关键项：${payload.missingCore.join("、")}。这些值保持为未采集，不用推测值代替。`)
					: null,
				h(
					"div",
					{ className: "ad-overview-grid" },
					h(Card, { label: "SoC / 档位", value: board.soc ? `${board.soc} / ${board.tier ?? "未知"}` : null, source: board.chip ? `npu-smi: ${board.chip}` : "未采集" }),
					h(Card, { label: "健康 / 温度", value: board.health ? `${board.health} · ${board.temperatureC ?? "—"} C` : null, source: "Alarm 只作为诊断背景" }),
					h(Card, {
						label: "NPU 内存",
						value: memoryValue,
						source: memoryValue ? "npu-smi memory used / total" : "未采集 memory-used / memory-total"
					}),
					h(Card, { label: "CANN", value: factValue(payload, "cannVersion"), source: facts.cannVersion?.source ?? "未采集" }),
					h(Card, { label: "驱动", value: factValue(payload, "driverVersion"), source: facts.driverVersion?.source ?? "未采集" }),
					h(Card, { label: "PyACL", value: factValue(payload, "aclImport"), source: "import acl" }),
					h(Card, { label: "摄像头", value: peripherals.cameras.length > 0 ? `${peripherals.cameras.length} 个节点` : null, source: "V4L2 / video" }),
					h(Card, { label: "音频设备", value: peripherals.playback.length + peripherals.capture.length + peripherals.sinks.length > 0 ? `${peripherals.playback.length + peripherals.capture.length + peripherals.sinks.length} 项` : null, source: "aplay / arecord / pactl" }),
					h(Card, { label: "USB 外设", value: peripherals.usb.length > 0 ? `${peripherals.usb.length} 个` : null, source: "lsusb" })
				),
				h(
					"div",
					{ className: "ad-section" },
					h("div", { className: "ad-asset-head" }, h("h3", null, "版本配套"), h(Verdict, { verdict: pairing.verdict, evidence: pairing.evidence })),
					h("ul", { className: "ad-list" }, (pairing.reasons ?? []).map((reason, index) => h("li", { key: index }, reason))),
					(pairing.nextActions ?? []).length > 0
						? h("div", { className: "ad-note" }, `下一步：${(pairing.nextActions ?? []).join(" ")}`)
						: null
				)
			);

			const assets = h(
				"div",
				null,
				h(
					"div",
					{ className: "ad-section" },
					h(
						"div",
						{ className: "ad-asset-head" },
						h("h3", null, "开发板资产"),
						h("span", { className: "ad-note" }, "MAC 是硬件唯一标识；IP/主机名只是当前网络信息")
					),
					h(BoardTable, { boards: payload.boards ?? [] }),
					(payload.boards ?? []).length === 0 ? h(Targets, { targets: payload.targets }) : null
				)
			);

			const debug = h(
				"div",
				null,
				h(
					"div",
					{ className: "ad-section" },
					h("h3", null, "按症状调试"),
					h("div", { className: "ad-note" }, "输入报错或选择一个已收录症状；计划按层给出停止条件。"),
					h("input", {
						className: "ad-select",
						style: { width: "100%", boxSizing: "border-box", marginTop: "8px" },
						value: query,
						placeholder: "例如：import acl 失败 / venc create channel / exit 139",
						onChange: (event) => setQuery(event.target.value)
					}),
					h("div", { style: { marginTop: "10px" } }, h(Knowledge, { knowledge: payload.knowledge, onPick: setQuery }))
				),
				h(
					"div",
					{ className: "ad-section" },
					h("div", { className: "ad-asset-head" }, h("h3", null, "案例系统程序"), h("span", { className: "ad-note" }, "只显示文档记录的 apt/Python/设备依赖")),
					h("div", { className: "ad-note" }, "需要安装系统程序时，先在对话中使用 ascend_apt_install dry-run；面板不会自动提权。"),
					h(
						"select",
						{
							className: "ad-select",
							style: { width: "100%", boxSizing: "border-box", marginTop: "8px" },
							value: dependencyCase,
							onChange: (event) => setDependencyCase(event.target.value)
						},
						(payload.caseDependencies ?? []).map((item) => h("option", { key: item.id, value: item.id }, `${item.id} · ${item.title}`))
					),
					h(CaseRequirements, { cases: payload.caseDependencies ?? [], selected: dependencyCase })
				),
				h(
					"div",
					{ className: "ad-section" },
					h("div", { className: "ad-asset-head" }, h("h3", null, "可参考样例"), h("span", { className: "ad-note" }, `${matchedSamples.length} 个匹配`)),
					h(Samples, { samples: matchedSamples.slice(0, 12) })
				)
			);

			const raw = h(
				"div",
				null,
				h("div", { className: "ad-section" }, h("h3", null, "板端环境事实"), h(FactTable, { facts, ...payload })),
				payload.rawExcerpt
					? h("div", { className: "ad-section" }, h("h3", null, "最近一次探测输出"), h("pre", { className: "ad-pre" }, payload.rawExcerpt))
					: null,
				h(
					"div",
					{ className: "ad-section" },
					h("h3", null, "已记录的版本组合"),
					h(
						"table",
						{ className: "ad-table" },
						h("thead", null, h("tr", null, h("th", null, "板卡"), h("th", null, "SoC / 档位"), h("th", null, "CANN"), h("th", null, "驱动"), h("th", null, "证据"))),
						h(
							"tbody",
							null,
							(payload.observedStacks ?? []).map((stack) =>
								h(
									"tr",
									{ key: stack.id },
									h("td", null, stack.board),
									h("td", null, `${stack.soc} / ${stack.tier}`),
									h("td", { className: "ad-mono" }, stack.cann),
									h("td", { className: "ad-mono" }, stack.driver),
									h("td", null, stack.status)
								)
							)
						)
					)
				)
			);

			const devices = h(
				"div",
				null,
				h(
					"div",
					{ className: "ad-section" },
					h("div", { className: "ad-asset-head" }, h("h3", null, "已搭配外设"), h("span", { className: "ad-note" }, "按板端 lsusb / V4L2 / ALSA 枚举")),
					h("div", { className: "ad-peripheral-grid" }, peripherals.groups.map((group) => h(PeripheralGroup, { key: group.id, group }))),
					h("div", { className: "ad-note", style: { marginTop: "10px" } }, "设备能被列出不等于已经验证可读写；摄像头仍需实际读帧，音频仍需实际播放/录音。")
				),
				h(
					"details",
					{ className: "ad-section ad-raw-details" },
					h("summary", null, "原始设备枚举输出"),
					h(DeviceFacts, { facts, ...payload })
				)
			);

			return h(
				"div",
				{ className: "ad-root" },
				head(payload, probing),
				h(
					"div",
					{ className: "ad-sub" },
					`${payload.repoRoot ?? ""} · ${payload.source ?? "not-probed"}${payload.probedAt ? ` · ${stamp(payload.probedAt)}` : ""}`
				),
				serial.kind === "failed"
					? h("div", { className: "ad-error" }, `串口探测失败：${serial.message}`)
					: serial.kind === "done"
						? h(
								"div",
								{ className: "ad-note" },
								`串口：${serial.payload.port} · ${serial.payload.hostname ?? "主机名未采集"} · ${serial.payload.ipAddress ?? "IP 未采集"} · ${serial.payload.macAddress ?? "MAC 未采集"} · 已写入 ${(serial.payload.found ?? []).length} 块`
							)
						: null,
				discovery.kind === "failed"
					? h("div", { className: "ad-error" }, `发现失败：${discovery.message}`)
					: discovery.kind === "done"
						? h("div", { className: "ad-note" }, `发现完成：${discovery.payload.found?.length ?? 0} 块开发板；资产表 ${discovery.payload.inventoryPath ?? ""}`)
						: null,
				payload.transport && payload.transport.ok !== true
					? h("div", { className: "ad-banner" }, `探测未完成：${payload.transport.reason}`)
					: null,
				h(
					"div",
					{ className: "ad-workbench" },
					h(
						"aside",
						{ className: "ad-rail" },
						h(
							"div",
							{ className: "ad-rail-head" },
							h("h3", null, "开发板"),
							h("span", { className: "ad-note" }, `${(payload.boards ?? []).length || (payload.targets ?? []).length} 台`)
						),
						h("input", {
							className: "ad-select",
							style: { width: "100%", boxSizing: "border-box", marginBottom: "9px" },
							value: boardQuery,
							placeholder: "搜索主机名 / MAC / IP",
							onChange: (event) => setBoardQuery(event.target.value)
						}),
						h(BoardRail, {
							boards: payload.boards ?? [],
							targets: payload.targets ?? [],
							selected,
							query: boardQuery,
							onSelect: selectBoard
						})
					),
					h(
						"main",
						{ className: "ad-main" },
						h(TabBar, { active: tab, onChange: setTab }),
						h("div", { className: "ad-toolbar-note" }, tab === "overview" ? overview : tab === "devices" ? devices : tab === "assets" ? assets : tab === "debug" ? debug : raw)
					)
				)
			);
		}

		/** The sidebar glyph for this panel. */
		function AscendPanelIcon(props) {
			const size = props?.size ?? 16;
			return h(
				"svg",
				{ width: size, height: size, viewBox: "0 0 16 16", fill: "none", xmlns: "http://www.w3.org/2000/svg" },
				h("rect", { x: "1.75", y: "3.25", width: "12.5", height: "9.5", rx: "1.2", stroke: "currentColor", "stroke-width": "1.2" }),
				h("path", { d: "M4.25 6.25h3M4.25 8.75h5.5M4.25 11h2.5", stroke: "currentColor", "stroke-width": "1.2", "stroke-linecap": "round" }),
				h("path", { d: "M10.5 10.5l1.5 1.5 2-2.5", stroke: "currentColor", "stroke-width": "1.2", "stroke-linecap": "round", "stroke-linejoin": "round" })
			);
		}

		/** Services the panel registrations need from the browser plugin context. */
		const inject = ["slots"];

		/**
		 * Contribute the 310B debugging entry to the sidebar and the panel it opens.
		 * @param ctx - the browser plugin context.
		 */
		function apply(ctx) {
			installStyles();
			ctx.slots.inject("main", function* () {
				yield ctx.slots.register({ name: "main", key: PANEL_ID }, AscendPanel);
			});
			ctx.slots.inject("sidebar.panellist", () =>
				ctx.slots.register(
					{
						name: "sidebar.panellist",
						id: PANEL_ID,
						order: 22,
						label: PANEL_TITLE
					},
					AscendPanelIcon
				)
			);
		}

		exports.PANEL_ID = PANEL_ID;
		exports.apply = apply;
		exports.inject = inject;
		return module.exports;
	}
});

//# sourceMappingURL=client.js.map
