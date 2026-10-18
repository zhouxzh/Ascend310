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
		/** Delay between automatic read-only probes while live monitoring is on. */
		const LIVE_INTERVAL_MS = 60000;

		/** Class-name prefix keeping this plugin's styles out of other panels. */
		const CSS = `
.ad-root{height:100%;overflow:auto;padding:18px 22px 40px;box-sizing:border-box;color:var(--dsw-alias-label-primary);font-size:13px;line-height:20px}
.ad-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
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
@media(max-width:720px){.ad-root{padding:14px 14px 32px}.ad-actions{width:100%;margin-left:0}.ad-actions .ad-select{flex:1;min-width:180px}.ad-table{display:block;overflow-x:auto;white-space:nowrap}.ad-table td,.ad-table th{white-space:normal;min-width:90px}.ad-workbench{grid-template-columns:1fr;gap:12px}.ad-rail{border-right:0;border-bottom:.5px solid var(--dsw-alias-border-l1);padding:0 0 10px}.ad-board-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}}
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

		/** Human label for a fact, falling back to the raw key. */
		function factValue(payload, key) {
			const fact = payload?.facts?.[key];
			if (fact === undefined || fact === null) return null;
			const value = fact.value;
			if (value === null || value === undefined || value === "") return null;
			if (typeof value === "boolean") return value ? "是" : "否";
			return String(value);
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
				h("thead", null, h("tr", null, h("th", null, "目标"), h("th", null, "地址"), h("th", null, "传输"), h("th", null, "说明"))),
				h(
					"tbody",
					null,
					targets.map((target) =>
						h(
							"tr",
							{ key: target.id },
							h("td", null, target.isDefault ? `${target.label}（默认）` : target.label),
							h("td", { className: "ad-mono" }, `${target.user ? `${target.user}@` : ""}${target.host}`),
							h("td", null, target.transport),
							h("td", { className: "ad-note" }, target.note ?? "")
						)
					)
				)
			);
		}

		/** Navigation for the operational panel. */
		function TabBar(props) {
			const tabs = [
				["overview", "概览"],
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
					"尚无资产表记录。点击「发现开发板」扫描私有网段，或先对已配置 target 运行一次探测。"
				);
			}
			return h(
				"table",
				{ className: "ad-table" },
				h(
					"thead",
					null,
					h("tr", null, h("th", null, "硬件"), h("th", null, "系统"), h("th", null, "网络"), h("th", null, "最近发现"), h("th", null, "变更"))
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
								h("div", null, board.model ?? board.soc ?? board.id),
								h("div", { className: "ad-asset-mac" }, board.mac ?? "MAC 未采集"),
								h("div", { className: "ad-note" }, `${board.soc ?? "SoC 未采集"} · ${board.tier ?? "档位未采集"}`)
							),
							h(
								"td",
								null,
								h("div", { className: "ad-mono" }, `CANN ${board.cann ?? "未采集"}`),
								h("div", { className: "ad-mono" }, `驱动 ${board.driver ?? "未采集"}`)
							),
							h(
								"td",
								null,
								h("div", { className: "ad-mono" }, board.lastIp ?? "IP 未采集"),
								h("div", { className: "ad-note" }, board.hostname ?? "主机名未采集")
							),
							h("td", { className: "ad-note" }, board.lastSeen ? stamp(board.lastSeen) : "未探测"),
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
						return {
							key: board.id,
							targetId: target?.id ?? board.id,
							title: board.model ?? target?.label ?? board.id,
							mac: board.mac,
							detail: `${board.lastIp ?? "无 IP"} · ${board.tier ?? "档位未知"}`,
							changed: board.changesCount > 0,
							configured: Boolean(board.configuredId || target)
						};
					})
				: targets.map((target) => ({
						key: target.id,
						targetId: target.id,
						title: target.label,
						mac: target.mac,
						detail: `${target.host} · 未建档`,
						changed: false,
						configured: true
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
							onClick: () => props.onSelect(row.targetId)
						},
						h(
							"span",
							{ className: "ad-board-title" },
							h("i", { className: `ad-board-dot${row.changed ? " ad-changed" : row.configured ? " ad-online" : ""}` }),
							row.title
						),
						h("span", { className: "ad-board-meta" }, row.detail),
						h("span", { className: "ad-board-mac" }, row.mac ?? "MAC 未采集")
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

			const load = react.useCallback((fresh) => {
				const url = fresh ? `${REPORT_URL}?fresh=1` : REPORT_URL;
				return fetch(url, { headers: { accept: "application/json" } })
					.then(async (response) => {
						if (!response.ok) throw new Error(`HTTP ${response.status}`);
						return response.json();
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
					if (!response.ok || payload.ok !== true) throw new Error(payload.error ?? `HTTP ${response.status}`);
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

			const head = (payload, busy) =>
				h(
					"div",
					{ className: "ad-head" },
					h("h2", { className: "ad-title" }, PANEL_TITLE),
					h("span", { className: "ad-sub" }, "昇腾 310B1 / 310B4 板卡、驱动、CANN 版本探测与分层调试"),
					h(
						"span",
						{ className: "ad-mono" },
						(() => {
							const current = payload?.targets?.find((target) => target.id === selected);
							return current ? `${current.label} · ${current.mac ?? "MAC 未采集"}` : "未选择开发板";
						})()
					),
					h(
						"div",
						{ className: "ad-actions" },
						h(
							"button",
							{
								className: "ad-button",
								type: "button",
								disabled: discovery.kind === "running",
								title: "扫描配置中的私有 CIDR，按 MAC 更新资产表和 target 列表",
								onClick: runDiscovery
							},
							discovery.kind === "running" ? "发现中…" : "发现开发板"
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
					h(Card, { label: "CANN", value: factValue(payload, "cannVersion"), source: facts.cannVersion?.source ?? "未采集" }),
					h(Card, { label: "驱动", value: factValue(payload, "driverVersion"), source: facts.driverVersion?.source ?? "未采集" }),
					h(Card, { label: "PyACL", value: factValue(payload, "aclImport"), source: "import acl" })
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

			return h(
				"div",
				{ className: "ad-root" },
				head(payload, probing),
				h(
					"div",
					{ className: "ad-sub" },
					`${payload.repoRoot ?? ""} · ${payload.source ?? "not-probed"}${payload.probedAt ? ` · ${stamp(payload.probedAt)}` : ""}`
				),
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
						h("div", { className: "ad-toolbar-note" }, tab === "overview" ? overview : tab === "assets" ? assets : tab === "debug" ? debug : raw)
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
