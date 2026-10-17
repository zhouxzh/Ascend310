window.__ModuleLoader__.load({
	id: "dsh-plugin-ascend310b-writing",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });

		const react = require("react");
		const h = react.createElement;

		/** The id shared by the sidebar entry and the main panel it opens. */
		const PANEL_ID = "ascend310b-writing";
		/** Exact route the host half registers for this panel. */
		const REPORT_URL = "/ascend310b-writing/report.json";

		/** Class-name prefix keeping this plugin's styles out of every other panel. */
		const CSS = `
.tw-root{height:100%;overflow:auto;padding:20px 24px 40px;box-sizing:border-box;color:var(--dsw-alias-label-primary);font-size:13px;line-height:20px}
.tw-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:4px}
.tw-title{font-size:16px;font-weight:600;margin:0}
.tw-sub{color:var(--dsw-alias-label-tertiary);font-size:12px}
.tw-actions{margin-left:auto;display:flex;gap:8px}
.tw-button{border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-button-elevated-fill);color:var(--dsw-alias-label-primary);border-radius:6px;padding:4px 12px;font:inherit;cursor:pointer}
.tw-button:hover{background:var(--dsw-alias-interactive-bg-hover)}
.tw-button:disabled{opacity:.5;cursor:default}
.tw-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:16px 0}
.tw-card{border:.5px solid var(--dsw-alias-border-l1);border-radius:8px;padding:10px 12px}
.tw-card b{display:block;font-size:20px;font-weight:600;line-height:26px}
.tw-card span{color:var(--dsw-alias-label-tertiary);font-size:12px}
.tw-section{margin-top:20px}
.tw-section h3{font-size:13px;font-weight:600;margin:0 0 8px}
.tw-table{width:100%;border-collapse:collapse;font-size:12px}
.tw-table th{text-align:left;color:var(--dsw-alias-label-tertiary);font-weight:500;border-bottom:.5px solid var(--dsw-alias-border-l1);padding:4px 8px}
.tw-table td{border-bottom:.5px solid var(--dsw-alias-border-l1);padding:4px 8px;vertical-align:top}
.tw-table td.tw-num{text-align:right;font-variant-numeric:tabular-nums}
.tw-bar{height:4px;border-radius:2px;background:var(--dsw-alias-state-business-primary);min-width:2px}
.tw-bar-track{width:88px;height:4px;border-radius:2px;background:var(--dsw-alias-border-l1);display:inline-block;vertical-align:middle}
.tw-file{font-family:var(--ds-font-family-code);color:var(--dsw-alias-label-secondary)}
.tw-sev{font-weight:600;font-family:var(--ds-font-family-code)}
.tw-sev-error{color:var(--dsw-alias-state-error-primary)}
.tw-sev-warning{color:var(--dsw-alias-state-warning-primary,#c98a00)}
.tw-sev-info{color:var(--dsw-alias-label-tertiary)}
.tw-empty{color:var(--dsw-alias-label-tertiary);padding:12px 0}
.tw-error{color:var(--dsw-alias-state-error-primary);white-space:pre-wrap}
`;

		/** Install this plugin's stylesheet once, at materialization. */
		function installStyles() {
			if (document.querySelector('style[data-plugin="dsh-plugin-ascend310b-writing"]') !== null) return;
			const tag = document.createElement("style");
			tag.dataset.plugin = "dsh-plugin-ascend310b-writing";
			tag.textContent = CSS;
			document.head.appendChild(tag);
		}

		/** Format an integer with thousands separators. */
		function num(value) {
			return Number(value ?? 0).toLocaleString("zh-CN");
		}

		/** Human-readable generation time in the browser's own time zone. */
		function stamp(iso) {
			if (typeof iso !== "string") return "";
			const date = new Date(iso);
			return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN");
		}

		/** One headline number. */
		function Card(props) {
			return h("div", { className: "tw-card" }, h("b", null, props.value), h("span", null, props.label));
		}

		/** The per-part progress table plus its proportional bars. */
		function ProgressTable(props) {
			const parts = props.parts ?? [];
			const widest = parts.reduce((max, part) => Math.max(max, part.cjk ?? 0), 0);
			if (parts.length === 0) return h("div", { className: "tw-empty" }, "暂无正文文件。");
			return h(
				"table",
				{ className: "tw-table" },
				h(
					"thead",
					null,
					h(
						"tr",
						null,
						h("th", null, "部分"),
						h("th", null, "占比"),
						h("th", { className: "tw-num" }, "篇数"),
						h("th", { className: "tw-num" }, "中文字"),
						h("th", { className: "tw-num" }, "图"),
						h("th", { className: "tw-num" }, "代码块"),
						h("th", { className: "tw-num" }, "阅读量")
					)
				),
				h(
					"tbody",
					null,
					parts.map((part) =>
						h(
							"tr",
							{ key: part.key },
							h("td", null, part.label),
							h(
								"td",
								null,
								h(
									"span",
									{ className: "tw-bar-track" },
									h("span", {
										className: "tw-bar",
										style: { width: widest === 0 ? "0" : `${Math.max(2, Math.round((part.cjk / widest) * 100))}%`, display: "block" }
									})
								)
							),
							h("td", { className: "tw-num" }, num(part.files?.length)),
							h("td", { className: "tw-num" }, num(part.cjk)),
							h("td", { className: "tw-num" }, num((part.files ?? []).reduce((sum, file) => sum + (file.figures ?? 0), 0))),
							h("td", { className: "tw-num" }, num((part.files ?? []).reduce((sum, file) => sum + (file.codeBlocks ?? 0), 0))),
							h("td", { className: "tw-num" }, `${num((part.files ?? []).reduce((sum, file) => sum + (file.readingMinutes ?? 0), 0))} 分钟`)
						)
					)
				)
			);
		}

		/** The finding list, most severe first. */
		function FindingList(props) {
			const findings = props.findings ?? [];
			if (findings.length === 0) return h("div", { className: "tw-empty" }, "没有发现问题。");
			return h(
				"table",
				{ className: "tw-table" },
				h(
					"thead",
					null,
					h(
						"tr",
						null,
						h("th", null, "级别"),
						h("th", null, "位置"),
						h("th", null, "规则"),
						h("th", null, "说明")
					)
				),
				h(
					"tbody",
					null,
					findings.map((finding, index) =>
						h(
							"tr",
							{ key: `${finding.file}:${finding.line}:${finding.rule}:${index}` },
							h("td", { className: `tw-sev tw-sev-${finding.severity}` }, finding.severity === "error" ? "错误" : finding.severity === "warning" ? "警告" : "提示"),
							h("td", { className: "tw-file" }, `${finding.file}:${finding.line}`),
							h("td", { className: "tw-file" }, finding.rule),
							h("td", null, finding.message)
						)
					)
				)
			);
		}

		/**
		 * The Writing panel: totals, per-part progress, and the audit findings.
		 * @returns the panel element.
		 */
		function WritingPanel() {
			const [state, setState] = react.useState({ kind: "loading" });
			const [onlyProblems, setOnlyProblems] = react.useState(false);

			const load = react.useCallback((fresh) => {
				setState((previous) => (previous.kind === "ready" ? previous : { kind: "loading" }));
				fetch(fresh ? `${REPORT_URL}?fresh=1` : REPORT_URL, { headers: { accept: "application/json" } })
					.then(async (response) => {
						if (!response.ok) throw new Error(`HTTP ${response.status}`);
						return response.json();
					})
					.then((payload) => setState({ kind: "ready", payload }))
					.catch((error) => setState({ kind: "failed", message: String(error?.message ?? error) }));
			}, []);

			react.useEffect(() => {
				load(false);
			}, [load]);

			const head = h(
				"div",
				{ className: "tw-head" },
				h("h2", { className: "tw-title" }, "写作"),
				h("span", { className: "tw-sub" }, "教程写作守门员：命名规范、锚点、链接与进度"),
				h(
					"div",
					{ className: "tw-actions" },
					h(
						"button",
						{ className: "tw-button", type: "button", onClick: () => setOnlyProblems((value) => !value) },
						onlyProblems ? "显示全部" : "只看问题"
					),
					h("button", { className: "tw-button", type: "button", disabled: state.kind === "loading", onClick: () => load(true) }, state.kind === "loading" ? "读取中…" : "刷新")
				)
			);

			if (state.kind === "loading") return h("div", { className: "tw-root" }, head, h("div", { className: "tw-empty" }, "正在读取仓库…"));
			if (state.kind === "failed") {
				return h(
					"div",
					{ className: "tw-root" },
					head,
					h("div", { className: "tw-error" }, `读取失败：${state.message}`)
				);
			}

			const { audit, progress, root, generatedAt } = state.payload;
			const findings = onlyProblems ? (audit.findings ?? []).filter((finding) => finding.severity !== "info") : (audit.findings ?? []);
			const totals = progress.totals ?? {};

			return h(
				"div",
				{ className: "tw-root" },
				head,
				h("div", { className: "tw-sub" }, `${root} · ${stamp(generatedAt)}`),
				h(
					"div",
					{ className: "tw-cards" },
					h(Card, { label: "正文文件", value: num(totals.files) }),
					h(Card, { label: "中文字", value: num(totals.cjk) }),
					h(Card, { label: "图", value: num(totals.figures) }),
					h(Card, { label: "代码块", value: num(totals.codeBlocks) }),
					h(Card, { label: "错误", value: num(audit.summary?.errors) }),
					h(Card, { label: "警告", value: num(audit.summary?.warnings) })
				),
				h("div", { className: "tw-section" }, h("h3", null, "各分部进度"), h(ProgressTable, { parts: progress.parts })),
				h(
					"div",
					{ className: "tw-section" },
					h("h3", null, `审计发现（${findings.length}${audit.summary?.truncated ? "，已截断" : ""}）`),
					h(FindingList, { findings })
				),
				(progress.missingFromSidebar ?? []).length > 0
					? h(
							"div",
							{ className: "tw-section" },
							h("h3", null, "未加入 sidebar.ts"),
							h("div", { className: "tw-file" }, (progress.missingFromSidebar ?? []).join("、"))
						)
					: null
			);
		}

		/** The sidebar glyph for this panel. */
		function WritingPanelIcon(props) {
			const size = props?.size ?? 16;
			return h(
				"svg",
				{ width: size, height: size, viewBox: "0 0 16 16", fill: "none", xmlns: "http://www.w3.org/2000/svg" },
				h("path", {
					d: "M3 1.75h6.5L13 5.25v9H3z",
					stroke: "currentColor",
					"stroke-width": "1.2",
					"stroke-linejoin": "round"
				}),
				h("path", { d: "M9.25 1.75v3.5h3.5", stroke: "currentColor", "stroke-width": "1.2", "stroke-linejoin": "round" }),
				h("path", { d: "M5.25 8.5h5.5M5.25 11h3.5", stroke: "currentColor", "stroke-width": "1.2", "stroke-linecap": "round" })
			);
		}

		/** Services the panel registrations need from the browser plugin context. */
		const inject = ["slots"];

		/**
		 * Contribute the Writing entry to the sidebar and the panel it opens.
		 * @param ctx - the browser plugin context.
		 */
		function apply(ctx) {
			installStyles();
			ctx.slots.inject("main", function* () {
				yield ctx.slots.register({ name: "main", key: PANEL_ID }, WritingPanel);
			});
			ctx.slots.inject("sidebar.panellist", () =>
				ctx.slots.register(
					{
						name: "sidebar.panellist",
						id: PANEL_ID,
						order: 20,
						label: "昇腾310B写作"
					},
					WritingPanelIcon
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
