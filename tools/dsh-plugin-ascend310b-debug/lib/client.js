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
		/** Discovery route registered by the host half. */
		const DISCOVER_URL = "/ascend310b-debug/discover";
		/** Serial network-probe route registered by the host half. */
		const SERIAL_URL = "/ascend310b-debug/serial-probe";
		/** Fixed peripheral operation routes registered by the host half. */
		const CAMERA_REFRESH_URL = "/ascend310b-debug/camera-refresh";
		const CAMERA_PREVIEW_URL = "/ascend310b-debug/camera-preview";
		const AUDIO_REFRESH_URL = "/ascend310b-debug/audio-refresh";
		const SYSTEM_REFRESH_URL = "/ascend310b-debug/system-refresh";
		const CAMERA_CAPTURE_URL = "/ascend310b-debug/camera-capture";
		const MICROPHONE_RECORD_URL = "/ascend310b-debug/microphone-record";
		const SPEAKER_TEST_URL = "/ascend310b-debug/speaker-test";
		const PLAY_RECORDING_URL = "/ascend310b-debug/play-recording";
		/** Explicit configured-target binding route registered by the host half. */
		const BIND_URL = "/ascend310b-debug/bind";
		/** Transport-level SSH reachability route registered by the host half. */
		const LIVENESS_URL = "/ascend310b-debug/liveness";

		/** Class-name prefix keeping this plugin's styles out of other panels. */
		const CSS = `
.ad-root{height:100%;overflow:auto;padding:18px 22px 40px;box-sizing:border-box;color:var(--dsw-alias-label-primary);font-size:13px;line-height:20px;max-width:1320px;margin:0 auto}
.ad-head{display:grid;grid-template-columns:minmax(220px,.65fr) minmax(0,2fr) auto;align-items:start;gap:14px 20px;padding-bottom:12px;border-bottom:.5px solid var(--dsw-alias-border-l1)}
.ad-title{font-size:16px;font-weight:600;margin:0}
.ad-sub{color:var(--dsw-alias-label-tertiary);font-size:12px}
.ad-actions{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.ad-current-group{display:flex;flex-direction:column;gap:7px;min-width:0}
.ad-current-line{display:flex;align-items:center;gap:8px;min-width:0;flex-wrap:wrap}
.ad-current-actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap;min-height:30px}
.ad-current-actions .ad-button{min-width:104px;min-height:30px;box-sizing:border-box}
.ad-discovery-group{display:flex;flex-direction:column;align-items:flex-end;gap:4px;margin-left:0}
.ad-discovery-actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap;min-height:30px}
.ad-discovery-actions .ad-button{min-width:104px;min-height:30px;box-sizing:border-box}
.ad-action-label{color:var(--dsw-alias-label-tertiary);font-size:11px;line-height:14px;white-space:nowrap}
.ad-button{border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-button-elevated-fill);color:var(--dsw-alias-label-primary);border-radius:6px;padding:4px 12px;font:inherit;cursor:pointer;white-space:nowrap}
.ad-refresh-button{min-width:146px;justify-content:center}
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
.ad-empty-state{display:flex;flex-direction:column;align-items:flex-start;gap:8px;min-height:180px;justify-content:center;padding:24px;border:.5px dashed var(--dsw-alias-border-l2);border-radius:8px;background:var(--dsw-alias-bg-l1,#0000)}
.ad-empty-state strong{font-size:15px;color:var(--dsw-alias-label-primary)}
.ad-empty-state p{margin:0;color:var(--dsw-alias-label-tertiary);font-size:12px;max-width:620px}
.ad-operation-status{min-height:22px;margin-top:7px;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--dsw-alias-label-tertiary)}
.ad-inline-status{height:20px;margin-top:6px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:11px;line-height:20px;color:var(--dsw-alias-label-tertiary)}
.ad-global-status{height:22px;margin:0 0 8px;font-size:11px;line-height:22px;color:var(--dsw-alias-label-tertiary)}
.ad-global-status>summary{cursor:pointer;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;list-style-position:inside}
.ad-global-status[open]{height:auto;max-height:96px;overflow:auto}
.ad-global-detail{white-space:normal;line-height:1.5;padding:4px 0 6px 16px}
.ad-global-status.ad-error{color:var(--dsw-alias-state-error-primary,#d92d20);font-weight:500}
.ad-system-notice{height:18px;margin:0 0 7px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:11px;line-height:18px;color:var(--dsw-alias-label-tertiary)}
.ad-system-hint{height:20px;margin-bottom:7px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:11px;line-height:20px;color:var(--dsw-alias-label-tertiary)}
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
.ad-tab{border:0;border-bottom:2px solid transparent;background:transparent;color:var(--dsw-alias-label-tertiary);padding:6px 12px 7px;font:inherit;font-weight:600;cursor:pointer}
.ad-tab:hover{color:var(--dsw-alias-label-primary)}
.ad-tab.ad-active{color:var(--dsw-alias-state-business-primary);border-bottom-color:var(--dsw-alias-state-business-primary)}
.ad-overview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:14px}
.ad-asset-head{display:flex;justify-content:space-between;gap:12px;align-items:baseline;flex-wrap:wrap}
.ad-asset-mac{font-family:var(--ds-font-family-code);font-size:11px;color:var(--dsw-alias-label-secondary);word-break:break-all}
.ad-detail{display:flex;flex-direction:column;gap:10px}
.ad-detail-title{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ad-bind{display:flex;align-items:center;gap:7px;flex-wrap:wrap;padding:9px 10px;border:.5px solid var(--dsw-alias-border-l1);border-radius:7px}
.ad-change{color:var(--dsw-alias-state-warning-primary,#c98a00);font-size:12px}
.ad-stack{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.ad-stack span{border:.5px solid var(--dsw-alias-border-l1);border-radius:999px;padding:1px 8px;color:var(--dsw-alias-label-tertiary);font-size:11px}
.ad-toolbar-note{margin-top:10px}
.ad-workbench{display:grid;grid-template-columns:248px minmax(0,1fr);gap:20px;margin-top:14px;align-items:start}
.ad-rail{border-right:.5px solid var(--dsw-alias-border-l1);padding-right:14px;min-width:0}
.ad-rail-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:9px}
.ad-rail-head h3{font-size:13px;font-weight:600;margin:0}
.ad-board-list{display:flex;flex-direction:column;gap:6px}
.ad-history-toggle{align-self:flex-start;padding:3px 9px;font-size:11px;color:var(--dsw-alias-label-secondary)}
.ad-rail-heading{font-size:11px;font-weight:600;color:var(--dsw-alias-label-tertiary);text-transform:uppercase;letter-spacing:0;padding:2px 1px}
.ad-board-item{display:block;width:100%;text-align:left;border:.5px solid var(--dsw-alias-border-l1);border-radius:7px;background:transparent;color:var(--dsw-alias-label-primary);padding:8px 9px;font:inherit;cursor:pointer}
.ad-board-item:hover{background:var(--dsw-alias-interactive-bg-hover)}
.ad-board-item.ad-selected{border-color:var(--dsw-alias-state-business-primary);background:var(--dsw-alias-interactive-bg-hover)}
.ad-board-title{display:flex;align-items:center;gap:6px;font-weight:600;font-size:12px;min-width:0}
.ad-board-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ad-board-flag{flex:none;border:.5px solid var(--dsw-alias-state-warning-primary,#c98a00);border-radius:999px;padding:0 5px;color:var(--dsw-alias-state-warning-primary,#c98a00);font-size:9px;font-weight:500;line-height:15px}
.ad-board-dot{width:7px;height:7px;border-radius:50%;background:var(--dsw-alias-label-tertiary);flex:none}
.ad-board-dot.ad-online{background:var(--dsw-alias-state-success-primary,#2f9e44)}
.ad-board-dot.ad-offline{background:var(--dsw-alias-state-danger-primary,#d64545)}
.ad-board-dot.ad-changed{background:var(--dsw-alias-state-warning-primary,#c98a00)}
.ad-board-meta{display:-webkit-box;color:var(--dsw-alias-label-tertiary);font-size:11px;line-height:15px;margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:normal;-webkit-box-orient:vertical;-webkit-line-clamp:2}
.ad-board-mac{font-family:var(--ds-font-family-code);font-size:10px;color:var(--dsw-alias-label-tertiary);margin-top:2px;overflow:hidden;text-overflow:ellipsis}
.ad-main{min-width:0}
.ad-empty-rail{border:.5px dashed var(--dsw-alias-border-l2);border-radius:7px;padding:10px;color:var(--dsw-alias-label-tertiary);font-size:12px}
.ad-heading{display:flex;flex-direction:column;gap:2px;min-width:0}
.ad-heading .ad-sub{margin:0}
.ad-current{display:flex;align-items:center;gap:7px;min-width:0;margin-left:2px;padding:4px 9px;border:.5px solid var(--dsw-alias-border-l1);border-radius:6px;background:var(--dsw-alias-bg-l1,#0000)}
.ad-current strong{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ad-current code{font-family:var(--ds-font-family-code);font-size:10px;color:var(--dsw-alias-label-tertiary);white-space:nowrap}
.ad-state-badge{border:.5px solid var(--dsw-alias-border-l2);border-radius:999px;padding:1px 7px;color:var(--dsw-alias-label-secondary);font-size:10px;white-space:nowrap}
.ad-toolbar{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:12px}
.ad-overview-grid{grid-template-columns:repeat(auto-fit,minmax(142px,1fr));gap:8px;margin-top:12px}
.ad-metric{position:relative;overflow:hidden}
.ad-metric .ad-metric-bar{height:4px;background:var(--dsw-alias-border-l1);border-radius:2px;margin-top:8px;overflow:hidden}
.ad-metric .ad-metric-bar span{display:block;height:100%;background:var(--dsw-alias-state-business-primary);border-radius:2px}
.ad-peripheral-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:9px}
.ad-peripheral-tester{border:.5px solid var(--dsw-alias-border-l1);border-radius:8px;padding:11px;background:var(--dsw-alias-bg-l1,#0000)}
.ad-peripheral-toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
.ad-test-title{font-size:12px;font-weight:600;color:var(--dsw-alias-label-primary)}
.ad-mode-tabs{display:flex;gap:2px;padding:2px;border:.5px solid var(--dsw-alias-border-l1);border-radius:7px;background:var(--dsw-alias-bg-l2,#0000)}
.ad-mode-tab{border:0;border-radius:5px;background:transparent;color:var(--dsw-alias-label-secondary);padding:4px 10px;font:inherit;font-weight:600;cursor:pointer;white-space:nowrap}
.ad-mode-tab.ad-active{background:var(--dsw-alias-button-elevated-fill);color:var(--dsw-alias-label-primary)}
.ad-mode-tab.ad-locked:not(.ad-active){color:var(--dsw-alias-state-warning-primary,#c98a00)}
.ad-mode-tab:disabled{opacity:.5;cursor:default}
.ad-test-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:8px;margin-top:10px;align-items:end}
.ad-field{display:flex;flex-direction:column;gap:4px;min-width:0}
.ad-field label{font-size:11px;color:var(--dsw-alias-label-tertiary)}
.ad-field .ad-select,.ad-field input{width:100%;box-sizing:border-box}
.ad-test-actions{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:10px;min-height:30px}
.ad-test-actions .ad-button{min-width:140px;min-height:30px;box-sizing:border-box}
.ad-test-note{margin-top:7px;color:var(--dsw-alias-label-tertiary);font-size:11px}
.ad-peripheral-result{height:280px;overflow:auto;margin-top:11px;border-top:.5px solid var(--dsw-alias-border-l1);padding-top:10px}
.ad-audio-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.ad-audio-pane,.ad-audio-playback{min-width:0;border:.5px solid var(--dsw-alias-border-l1);border-radius:8px;padding:11px;background:var(--dsw-alias-bg-l1,#0000)}
.ad-audio-pane-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.ad-audio-pane-head h4{margin:0;font-size:13px}
.ad-audio-pane .ad-test-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
.ad-audio-result{height:178px;overflow:auto;margin-top:10px;padding-top:8px;border-top:.5px solid var(--dsw-alias-border-l1)}
.ad-audio-playback{margin-top:10px}
.ad-audio-playback-route{margin:0 0 8px;font-size:11px;color:var(--dsw-alias-label-tertiary);overflow-wrap:anywhere}
.ad-result-head{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.ad-result-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:6px;margin-top:8px}
.ad-format-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:8px}
.ad-format-card{border:.5px solid var(--dsw-alias-border-l1);border-radius:7px;padding:9px 10px;min-width:0}
.ad-format-head{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.ad-format-sizes{display:flex;flex-direction:column;gap:5px;margin-top:8px}
.ad-format-size{display:flex;justify-content:space-between;gap:10px;border-top:.5px solid var(--dsw-alias-border-l1);padding-top:5px;font-size:11px}
.ad-format-size code{font-family:var(--ds-font-family-code);font-size:10px;color:var(--dsw-alias-label-tertiary)}
.ad-result-metric{border:.5px solid var(--dsw-alias-border-l1);border-radius:6px;padding:6px 8px;min-width:0}
.ad-result-metric span{display:block;color:var(--dsw-alias-label-tertiary);font-size:10px}
.ad-result-metric strong{display:block;font-size:12px;margin-top:2px;overflow-wrap:anywhere}
.ad-confirm{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:9px}
.ad-camera-preview{height:240px;margin-top:10px;padding:8px;box-sizing:border-box;display:flex;align-items:center;justify-content:center;overflow:hidden;border:.5px solid var(--dsw-alias-border-l1);border-radius:7px;background:var(--dsw-alias-bg-l2,#1112)}
.ad-camera-preview a{display:flex;width:100%;height:100%;align-items:center;justify-content:center}
.ad-camera-preview img{display:block;max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain;image-rendering:auto}
.ad-recording-playback{height:150px;overflow:auto;margin-top:10px;padding:10px;border:.5px solid var(--dsw-alias-border-l1);border-radius:7px;background:var(--dsw-alias-bg-l1,#0000)}
.ad-playback-button{min-width:190px}
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
@media(max-width:720px){.ad-root{padding:14px 14px 32px}.ad-head{grid-template-columns:1fr;align-items:flex-start}.ad-current-group{order:2;width:100%;margin-left:0}.ad-current-line{width:100%}.ad-current{width:100%;flex:1}.ad-current-actions{width:100%}.ad-current-actions .ad-button{flex:1}.ad-discovery-group{order:3;width:100%;margin-left:0;align-items:stretch}.ad-discovery-actions .ad-button{flex:1}.ad-actions .ad-select{flex:1;min-width:180px}.ad-toolbar .ad-button{flex:1}.ad-table{display:block;overflow-x:auto;white-space:nowrap}.ad-table td,.ad-table th{white-space:normal;min-width:90px}.ad-workbench{grid-template-columns:1fr;gap:12px}.ad-rail{position:static;max-height:none;border-right:0;border-bottom:.5px solid var(--dsw-alias-border-l1);padding:0 0 10px}.ad-board-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));max-height:220px;overflow:auto}.ad-overview-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.ad-peripheral-grid{grid-template-columns:1fr}.ad-audio-grid{grid-template-columns:1fr}.ad-audio-pane .ad-test-grid{grid-template-columns:1fr}}
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

		/** Make cached and live panel data visibly different. */
		function dataState(payload, kind) {
			if (kind === "loading" && payload) return "正在读取已保存信息";
			if (payload?.transport?.ok === false) return "离线";
			if (payload?.probedAt) return `已保存结果 · ${stamp(payload.probedAt)}`;
			return "未探测";
		}

		/** Format NPU-reported MB values as GB for the UI, retaining one decimal. */
		function memoryGb(value) {
			const number = Number(value);
			return Number.isFinite(number) && number >= 0 ? `${(number / 1024).toFixed(1)} GB` : "未采集";
		}

		/** Fixed hardware capacity shown in the device name, never live usage. */
		function memoryNameSuffix(memoryTotalMb) {
			const value = Number(memoryTotalMb);
			return Number.isFinite(value) && value > 0 ? ` · ${memoryGb(value)}` : "";
		}

		/** Canonical display name shared by the rail, asset table and header. */
		function boardName(board = {}) {
			const model = typeof board.model === "string" ? board.model.replace(/\s+/g, " ").trim() : "";
			const soc = String(board.soc ?? "");
			const tier = String(board.tier ?? "");
			let name;
			if (/310B4/i.test(soc) || /\b8T\b/i.test(tier) || /\b8T\b/i.test(model)) name = "Orange Pi AI Pro 8T";
			else if (/310B1/i.test(soc) || /\b20T\b/i.test(tier) || /\b20T\b/i.test(model)) name = "Orange Pi AI Pro 20T";
			else name = model || board.hostname || board.id || "未命名开发板";
			return `${name}${memoryNameSuffix(board.memoryTotalMb ?? board.system?.memoryTotalMb)}`;
		}

		/** Canonical name for a configured target before inventory is available. */
		function targetName(target = {}) {
			const label = String(target.label ?? "").replace(/\s+/g, " ").trim();
			let name;
			if (/8T|310B4/i.test(label)) name = "Orange Pi AI Pro 8T";
			else if (/20T|310B1/i.test(label)) name = "Orange Pi AI Pro 20T";
			else {
				const known = boardName({ mac: target.mac, hostname: target.hostname, id: target.id });
				name = known !== target.hostname && known !== target.id && known !== "未命名开发板"
					? known
					: label || target.host || target.id || "未命名开发板";
			}
			return `${name}${memoryNameSuffix(target.memoryTotalMb ?? target.system?.memoryTotalMb)}`;
		}

		/** Compact hardware identifier used beside a canonical name. */
		function macSuffix(mac) {
			return typeof mac === "string" && mac.length >= 5 ? ` · ${mac.slice(-5)}` : "";
		}

		function boardMacs(board) {
			return [board?.mac, ...(Array.isArray(board?.macs) ? board.macs : [])]
				.filter((mac, index, all) => typeof mac === "string" && mac !== "" && all.indexOf(mac) === index);
		}

		function boardHasMac(board, mac) {
			return typeof mac === "string" && boardMacs(board).includes(mac);
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

		/** Parse per-node v4l2-ctl --info capabilities. */
		function videoDetailItems(payload) {
			const details = new Map();
			let current = null;
			let inDeviceCaps = false;
			const finish = () => {
				if (current !== null) details.set(current.id, current);
			};
			for (const line of factLines(payload, "videoDetails")) {
				const device = /^DEVICE=(\/dev\/video\d+)$/i.exec(line);
				if (device !== null) {
					finish();
					current = {
						id: device[1],
						driver: "",
						cardType: "",
						busInfo: "",
						videoCapture: false,
						videoCaptureMultiplanar: false,
						metadataCapture: false
					};
					inDeviceCaps = false;
					continue;
				}
				if (current === null) continue;
				const field = (pattern) => pattern.exec(line)?.[1]?.trim() ?? "";
				if (current.driver === "" && /^Driver name\s*:/i.test(line)) current.driver = field(/^Driver name\s*:\s*(.+)$/i);
				if (current.cardType === "" && /^Card type\s*:/i.test(line)) current.cardType = field(/^Card type\s*:\s*(.+)$/i);
				if (current.busInfo === "" && /^Bus info\s*:/i.test(line)) current.busInfo = field(/^Bus info\s*:\s*(.+)$/i);
				if (/^Device Caps\s*:/i.test(line)) {
					inDeviceCaps = true;
					continue;
				}
				if (inDeviceCaps && /^(?:Media Driver Info|Interface Info):/i.test(line)) {
					inDeviceCaps = false;
					continue;
				}
				if (!inDeviceCaps) continue;
				if (/^Video Capture Multiplanar$/i.test(line)) current.videoCaptureMultiplanar = true;
				if (/^Video Capture$/i.test(line)) current.videoCapture = true;
				if (/^Metadata Capture$/i.test(line)) current.metadataCapture = true;
			}
			finish();
			return details;
		}

		/** Parse v4l2-ctl --list-formats-ext into per-device formats and sizes. */
		function parseVideoFormatItems(payload) {
			const devices = new Map();
			let currentDevice = null;
			let currentFormat = null;
			let currentSize = null;
			for (const line of factLines(payload, "videoFormats")) {
				const deviceMatch = /^DEVICE=(\/dev\/video\d+)$/i.exec(line);
				if (deviceMatch !== null) {
					currentDevice = { id: deviceMatch[1], formats: [] };
					devices.set(currentDevice.id, currentDevice);
					currentFormat = null;
					currentSize = null;
					continue;
				}
				if (currentDevice === null) continue;
				const formatMatch = /^\[(\d+)\]:\s*'([^']+)'\s*(?:\((.*)\))?$/.exec(line);
				if (formatMatch !== null) {
					currentFormat = {
						index: Number(formatMatch[1]),
						fourcc: formatMatch[2],
						description: formatMatch[3] ?? "",
						sizes: []
					};
					currentDevice.formats.push(currentFormat);
					currentSize = null;
					continue;
				}
				const discreteSize = /^Size:\s*Discrete\s+(\d+)x(\d+)$/i.exec(line);
				if (discreteSize !== null && currentFormat !== null) {
					currentSize = {
						value: `${discreteSize[1]}x${discreteSize[2]}`,
						label: `${discreteSize[1]} × ${discreteSize[2]}`,
						width: Number(discreteSize[1]),
						height: Number(discreteSize[2]),
						stepwise: false,
						fps: []
					};
					if (!currentFormat.sizes.some((item) => item.value === currentSize.value)) currentFormat.sizes.push(currentSize);
					continue;
				}
				const stepwiseSize = /^Size:\s*Stepwise\s+(\d+)x(\d+)\s*-\s*(\d+)x(\d+)/i.exec(line);
				if (stepwiseSize !== null && currentFormat !== null) {
					currentSize = {
						value: `${stepwiseSize[1]}x${stepwiseSize[2]}`,
						label: `${stepwiseSize[1]} × ${stepwiseSize[2]} – ${stepwiseSize[3]} × ${stepwiseSize[4]}（连续范围）`,
						width: Number(stepwiseSize[1]),
						height: Number(stepwiseSize[2]),
						maxWidth: Number(stepwiseSize[3]),
						maxHeight: Number(stepwiseSize[4]),
						stepwise: true,
						fps: []
					};
					currentFormat.sizes.push(currentSize);
					continue;
				}
				const discreteInterval = /^Interval:\s*Discrete\s+([\d.]+)s\s*\(([\d.]+)\s*fps\)/i.exec(line);
				if (discreteInterval !== null && currentSize !== null) {
					const fps = Number(discreteInterval[2]);
					if (!currentSize.fps.includes(fps)) currentSize.fps.push(fps);
					continue;
				}
				const stepwiseInterval = /^Interval:\s*Stepwise\s+([\d.]+)s\s*\(([\d.]+)\s*fps\)\s*-\s*([\d.]+)s\s*\(([\d.]+)\s*fps\)/i.exec(line);
				if (stepwiseInterval !== null && currentSize !== null) {
					for (const fps of [Number(stepwiseInterval[2]), Number(stepwiseInterval[4])]) {
						if (!currentSize.fps.includes(fps)) currentSize.fps.push(fps);
					}
				}
			}
			return devices;
		}

		/** Extract V4L2 nodes while retaining device names and capture capability. */
		function cameraItems(payload) {
			const lines = factLines(payload, "videoDevices");
			const details = videoDetailItems(payload);
			const items = [];
			let groupName = "";
			for (const line of lines) {
				const match = /^(\/dev\/(?:video|media)\d+)$/i.exec(line);
				if (match !== null) {
					const info = details.get(match[1]);
					const name = info?.cardType || groupName || "V4L2 设备";
					const role = info?.videoCaptureMultiplanar
						? "视频采集（多平面）"
						: info?.videoCapture
							? "视频采集"
							: info?.metadataCapture
								? "仅元数据，不能取帧"
								: match[1].startsWith("/dev/video")
									? "采集能力未确认"
									: "媒体控制节点";
					items.push({
						id: match[1],
						name,
						label: name,
						selectorLabel: `${name} · ${match[1]} · ${role}`,
						detail: `${match[1]} · ${role}`,
						role,
						disabled: info?.metadataCapture === true && info?.videoCapture !== true && info?.videoCaptureMultiplanar !== true
					});
					continue;
				}
				// v4l2-ctl --list-devices prints one "Device: Interface (...):"
				// heading followed by indented /dev nodes. Keep that heading so the
				// selector identifies the physical device, not only videoN.
				if (/:$/.test(line) && !/^crw/i.test(line)) groupName = line.replace(/:$/, "");
			}
			return items;
		}

		/** Parse ALSA/PulseAudio enumeration lines into named devices. */
		function audioItems(payload, key) {
			const lines = factLines(payload, key);
			if (key === "audioSinks" || key === "audioSources") {
				return lines.flatMap((line) => {
					if (/^(?:not-found|NOT_FOUND)\b/i.test(line.trim())) return [];
					const fields = line.split(/\s+/);
					const name = fields.length > 1 && /^\d+$/.test(fields[0]) ? fields[1] : fields[0];
					if (key === "audioSources" && name.endsWith(".monitor")) return [];
					return name ? [{ id: name, label: name, detail: line }] : [];
				});
			}
			return lines.flatMap((line) => {
				const match = /^(?:card|device)\s+[^:]+:\s*(.+)$/i.exec(line);
				return match === null ? [] : [{ id: line, label: match[1], detail: line }];
			});
		}

		function bluetoothItems(payload) {
			return factLines(payload, "bluetoothDevices").flatMap((line) => {
				const match = /^Device\s+([0-9A-F:]{17})\s+(.+)$/i.exec(line.trim());
				return match === null ? [] : [{ id: match[1], label: match[2], detail: match[1] }];
			});
		}

		function pulseCardItems(payload) {
			return factLines(payload, "pulseCards").flatMap((line) => {
				if (/^(?:not-found|NOT_FOUND)\b/i.test(line.trim())) return [];
				const fields = line.trim().split(/\s+/);
				const name = fields.length > 1 && /^\d+$/.test(fields[0]) ? fields[1] : fields[0];
				return name ? [{ id: name, label: name, detail: line }] : [];
			});
		}

		/** Stable peripheral summary used by both overview and device views. */
		function peripheralInventory(payload) {
			const usb = usbItems(payload);
			const cameras = cameraItems(payload);
			const playback = audioItems(payload, "audioPlayback");
			const capture = audioItems(payload, "audioCapture");
			const sinks = audioItems(payload, "audioSinks");
			const sources = audioItems(payload, "audioSources");
			const cards = pulseCardItems(payload);
			const bluetooth = bluetoothItems(payload);
			return {
				usb,
				cameras,
				playback,
				capture,
				sinks,
				sources,
				groups: [
					{ id: "camera", label: "摄像头 / V4L2", items: cameras, raw: factValue(payload, "videoDevices"), source: "v4l2-ctl" },
					{ id: "playback", label: "音频播放", items: playback, raw: factValue(payload, "audioPlayback"), source: "aplay -l" },
					{ id: "capture", label: "音频采集", items: capture, raw: factValue(payload, "audioCapture"), source: "arecord -l" },
					{ id: "audio-sink", label: "音频 Sink", items: sinks, raw: factValue(payload, "audioSinks"), source: "pactl" },
					{ id: "audio-source", label: "音频 Source", items: sources, raw: factValue(payload, "audioSources"), source: "pactl（排除 monitor）" },
					{ id: "pulse-card", label: "Pulse Card / Profile", items: cards, raw: factValue(payload, "pulseCardDetails"), source: "pactl list cards" },
					{ id: "bluetooth", label: "蓝牙配对设备", items: bluetooth, raw: factValue(payload, "bluetoothDevices"), source: "bluetoothctl（只读）" },
					{ id: "bluetooth-info", label: "蓝牙连接详情", items: [], raw: factValue(payload, "bluetoothInfo"), source: "bluetoothctl info（只读）" },
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

		function parseAlsaPcmLine(line) {
			const match = /^card\s+(\d+):\s+(\S+)\s+\[(.*?)\],\s+device\s+(\d+):\s+(.+?)\s+\[(.*?)\]$/i.exec(String(line).trim());
			return match === null
				? null
				: {
						card: Number(match[1]),
						cardId: match[2],
						cardName: match[3],
						device: Number(match[4]),
						deviceId: match[5],
						deviceName: match[6]
					};
		}

		/** Seed selectors from the existing read-only environment probe. */
		function peripheralInventoryFromPayload(payload) {
			const videoNodes = cameraItems(payload)
				.filter((item) => /^\/dev\/video\d+$/i.test(item.id))
				.map((item) => ({
					id: item.id,
					name: item.name,
					label: item.label,
					selectorLabel: item.selectorLabel,
					role: item.role,
					disabled: item.disabled === true
				}));
			const capture = factLines(payload, "audioCapture").map(parseAlsaPcmLine).filter(Boolean);
			const playback = factLines(payload, "audioPlayback").map(parseAlsaPcmLine).filter(Boolean);
			const pulseSinks = audioItems(payload, "audioSinks").map((entry, index) => ({
				index,
				name: entry.id,
				state: ""
			}));
			const pulseSources = audioItems(payload, "audioSources").map((entry, index) => ({
				index,
				name: entry.id,
				state: "",
				isMonitor: false
			}));
			const pulseCards = pulseCardItems(payload).map((entry, index) => ({ index, name: entry.id }));
			const bluetoothDevices = bluetoothItems(payload).map((entry) => ({
				mac: entry.id,
				name: entry.label
			}));
			return {
				videoNodes,
				videoFormats: parseVideoFormatItems(payload),
				capture,
				playback,
				pulseSinks,
				pulseSources,
				pulseCards,
				bluetoothDevices,
				bluetoothController: factValue(payload, "bluetoothController"),
				bluetoothInfo: factValue(payload, "bluetoothInfo"),
				bluetoothConnected: (String(factValue(payload, "bluetoothInfo") ?? "").match(/Connected:\s+yes/gi) ?? []).length,
				bluetoothCollected: factValue(payload, "bluetoothDevices") !== null && String(factValue(payload, "bluetoothDevices")) !== "not-found",
				pulseCardsCollected: factValue(payload, "pulseCards") !== null && String(factValue(payload, "pulseCards")) !== "not-found"
			};
		}

		/** Convert the fixed peripheral inventory into safe selector options. */
		function peripheralChoices(inventory) {
			const data = inventory ?? {};
			const cameras = (data.videoNodes ?? [])
				.flatMap((entry) => {
					const text = typeof entry === "string" ? entry : String(entry?.id ?? entry?.label ?? "");
					const match = /(\/dev\/video\d+)/.exec(text);
					if (match === null) return [];
					const label = typeof entry === "object"
						? entry.selectorLabel ?? entry.label ?? text
						: text;
					return [{ value: match[1], label, disabled: entry?.disabled === true }];
				})
				.filter((item, index, all) => all.findIndex((candidate) => candidate.value === item.value) === index);
			const alsa = (items) => (items ?? []).map((item) => {
				const identity = `${item.cardId ?? ""} ${item.deviceId ?? ""}`.toLowerCase();
				const external = /(usb|uac|camera|ugreen|webcam)/u.test(identity) && !/(ascend310b|platform-sound|built-in)/u.test(identity);
				return {
					value: `plughw:${item.card},${item.device}`,
					label: `card ${item.card}: ${item.cardId} · device ${item.device}: ${item.deviceId}${external ? " · USB/外接" : " · 板载"}`,
					external
				};
			});
			const pulse = (items) => (items ?? []).map((item) => ({
				value: item.name,
				label: `${item.name}${item.isMonitor ? " · 输出监视源" : ""}`,
				monitor: item.isMonitor === true
			}));
			const alsaCapture = alsa(data.capture)
				.map((item, index) => ({ ...item, index }))
				.sort((left, right) => Number(right.external) - Number(left.external) || left.index - right.index)
				.map(({ index, ...item }) => item);
			const alsaPlayback = alsa(data.playback);
			const pulseSources = pulse(data.pulseSources).filter((item) => item.monitor !== true);
			const pulseSinks = pulse(data.pulseSinks);
			return {
				cameras: cameras.length > 0 ? cameras : [{ value: "/dev/video0", label: "/dev/video0 · 默认候选" }],
				alsaCapture,
				alsaPlayback,
				pulseSources,
				pulseSinks,
				audioSources: [
					...alsaCapture.map((item) => ({ ...item, value: `alsa:${item.value}`, backend: "alsa", device: item.value, label: `ALSA · ${item.label}` })),
					{ value: "pulse:@DEFAULT_SOURCE@", backend: "pulse", device: "@DEFAULT_SOURCE@", label: "Pulse · 默认输入（可用于已连接的蓝牙耳机）" },
					...pulseSources.map((item) => ({ ...item, value: `pulse:${item.value}`, backend: "pulse", device: item.value, label: `Pulse · ${item.label}` }))
				],
				audioSinks: [
					...alsaPlayback.map((item) => ({ ...item, value: `alsa:${item.value}`, backend: "alsa", device: item.value, label: `ALSA · ${item.label}` })),
					{ value: "pulse:@DEFAULT_SINK@", backend: "pulse", device: "@DEFAULT_SINK@", label: "Pulse · 默认输出（可用于已连接的蓝牙设备）" },
					...pulseSinks.map((item) => ({ ...item, value: `pulse:${item.value}`, backend: "pulse", device: item.value, label: `Pulse · ${item.label}` }))
				]
			};
		}

		function ResultMetric(props) {
			return h(
				"div",
				{ className: "ad-result-metric" },
				h("span", null, props.label),
				h("strong", null, props.value === null || props.value === undefined || props.value === "" ? "未采集" : String(props.value))
			);
		}

		/** Camera, microphone and speaker acquisition controls for one board. */
		function PeripheralTester(props) {
			const mode = props.mode;
			const modes = props.modes ?? [
				["camera", "摄像头"],
				["microphone", "麦克风"],
				["speaker", "扬声器"]
			];
			const form = props.form?.[mode] ?? {};
			const phase = props.state?.phase ?? "idle";
			const running = phase === "running";
			const refreshing = props.refreshing === true;
			const refreshElapsedSec = props.refreshElapsedSec ?? 0;
			const choices = peripheralChoices(props.inventory);
			const options = mode === "camera"
				? choices.cameras
				: mode === "microphone"
					? choices.audioSources
					: choices.audioSinks;
			const selectedValue = mode === "camera" ? form.device : mode === "microphone" ? form.sourceKey : form.sinkKey;
			const deviceOptions = options.some((option) => option.value === selectedValue)
				? options
				: selectedValue
					? [{ value: selectedValue, label: selectedValue }, ...options]
					: options;
			const reportedFormats = props.inventory?.videoFormats?.get?.(form.device)?.formats ?? [];
			const formatOptions = reportedFormats.length > 0
				? reportedFormats.map((format) => ({
						value: format.fourcc,
						label: `${format.fourcc}${format.description ? ` · ${format.description}` : ""}`
					}))
				: [
						{ value: "MJPG", label: "MJPG（尚未从设备采集格式列表）" },
						{ value: "YUYV", label: "YUYV（尚未从设备采集格式列表）" }
					];
			const selectedFormatValue = formatOptions.some((item) => item.value === form.pixelFormat)
				? form.pixelFormat
				: formatOptions[0]?.value ?? form.pixelFormat;
			const selectedFormat = reportedFormats.find((format) => format.fourcc === selectedFormatValue) ?? null;
			const reportedResolutions = selectedFormat?.sizes ?? [];
			const resolutions = reportedResolutions.length > 0
				? reportedResolutions.map((size) => ({
						value: size.value,
						label: size.label,
						width: size.width,
						height: size.height,
						fps: size.fps
					}))
				: [
						{ value: "320x240", label: "320 × 240", width: 320, height: 240, fps: [] },
						{ value: "640x480", label: "640 × 480", width: 640, height: 480, fps: [] },
						{ value: "1280x720", label: "1280 × 720", width: 1280, height: 720, fps: [] },
						{ value: "1920x1080", label: "1920 × 1080", width: 1920, height: 1080, fps: [] }
					];
			const currentResolutionValue = `${form.width}x${form.height}`;
			const selectedResolution = resolutions.some((item) => item.value === currentResolutionValue)
				? currentResolutionValue
				: resolutions[0]?.value ?? currentResolutionValue;
			const selectedSize = resolutions.find((item) => item.value === selectedResolution) ?? resolutions[0] ?? null;
			const fpsOptions = selectedSize?.fps?.length > 0
				? selectedSize.fps.map((fps) => ({ value: String(fps), label: `${fps} fps` }))
				: [{ value: String(form.fps ?? 30), label: `${form.fps ?? 30} fps（默认）` }];
			const selectedFps = fpsOptions.some((item) => item.value === String(form.fps))
				? String(form.fps)
				: fpsOptions[0]?.value ?? "30";
			react.useEffect(() => {
				if (mode !== "camera") return;
				if (formatOptions.length > 0 && form.pixelFormat !== selectedFormatValue) {
					props.onField("pixelFormat", selectedFormatValue);
				}
				if (selectedSize !== null && (form.width !== selectedSize.width || form.height !== selectedSize.height)) {
					props.onResolution(selectedSize.width, selectedSize.height);
				}
				if (String(form.fps ?? "") !== selectedFps) props.onField("fps", selectedFps);
			}, [form.fps, form.height, form.pixelFormat, form.width, mode, selectedFormatValue, selectedFps, selectedSize]);
			const field = (id, label, control) =>
				h(
					"div",
					{ className: "ad-field" },
					h("label", { htmlFor: `ad-peripheral-${mode}-${id}` }, label),
					control
				);
			const select = (id, value, onChange, items, disabled = false) =>
				h(
					"select",
					{
						id: `ad-peripheral-${mode}-${id}`,
						className: "ad-select",
						value,
						disabled: running || disabled,
						onChange: (event) => onChange(event.target.value)
					},
					items.map((item) => h(
						"option",
						{ key: item.value, value: item.value, disabled: item.monitor === true || item.disabled === true },
						item.label ?? item.value
					))
				);
			const number = (id, value, min, max, onChange) =>
				h("input", {
					id: `ad-peripheral-${mode}-${id}`,
					className: "ad-select",
					type: "number",
					min,
					max,
					value,
					disabled: running,
					onChange: (event) => onChange(event.target.value)
				});
			const online = props.targetOnline;
			const result = props.state?.action === mode ? props.state?.result : null;
			const data = result?.data ?? {};
			const cameraPreview = mode === "camera" && result?.ok === true && typeof result.output === "string" && result.output !== ""
				? `${CAMERA_PREVIEW_URL}?target=${encodeURIComponent(props.targetId ?? "")}&file=${encodeURIComponent(result.output)}&v=${encodeURIComponent(result.data?.sha256 ?? "")}`
				: null;
			const resultText = result?.text ?? "";
			const playback = props.recordingPlayback ?? { phase: "idle", result: null, message: "" };
			const playbackData = playback.result?.data ?? {};
			const inventory = props.inventory ?? {};
			const bluetoothDevices = inventory.bluetoothDevices ?? [];
			const bluetoothController = String(inventory.bluetoothController ?? "").trim();
			const bluetoothControllerAvailable = /Controller\s+[0-9A-F:]{17}/i.test(bluetoothController);
			const pulseCards = inventory.pulseCards ?? [];
			const bluetoothSummary = inventory.bluetoothCollected === false
				? "蓝牙状态未采集"
				: `蓝牙：${bluetoothControllerAvailable ? "控制器已枚举" : "控制器未枚举"} · ${bluetoothDevices.length} 个已配对 · ${inventory.bluetoothConnected ?? 0} 个已连接`;
			const pulseSummary = inventory.pulseCardsCollected === false
				? "Pulse card 未采集"
				: `${pulseCards.length} 个 Pulse card`;
			const resultSummary = mode === "camera"
				? [
						["采集后端", data.captureBackend],
						["实际分辨率", data.actualWidth && data.actualHeight ? `${data.actualWidth}×${data.actualHeight}` : null],
						["实际 FourCC", data.fourcc],
						["实际帧率", data.actualFps ? `${data.actualFps} fps` : null],
						["读取帧数", data.frameCount],
						["文件字节", data.bytes],
						["SHA-256", data.sha256]
					]
				: mode === "microphone"
					? [
							["检测到信号", data.signalDetected === true ? "是" : data.signalDetected === false ? "否" : null],
							["RMS", data.rms],
							["峰值", data.peak],
							["全零比例", data.zeroRatio],
							["削波比例", data.clippingRatio],
							["请求录音时长", data.requestedDurationSec !== undefined ? `${data.requestedDurationSec} s` : form.durationSec ? `${form.durationSec} s` : null],
							["WAV 实际时长", data.durationSec !== undefined ? `${data.durationSec} s` : null],
							["采集阶段耗时", data.captureElapsedSec !== undefined ? `${data.captureElapsedSec} s` : null],
							["操作总耗时", result?.durationMs !== undefined ? `${(result.durationMs / 1000).toFixed(2)} s` : null],
							["WAV 参数", data.sampleRate ? `${data.sampleRate} Hz / ${data.channels} ch` : null],
							["SHA-256", data.sha256]
						]
					: [
							["设备", data.device],
							["测试音", data.frequency ? `${data.frequency} Hz / ${data.durationSec} s` : null],
							["输出路由", data.outputRoute],
							["人耳确认", data.heard === null ? "待确认" : data.heard === true ? "已听到" : "未听到"],
							["文件字节", data.bytes],
							["SHA-256", data.sha256],
							["证据", data.evidence]
						];
			return h(
				"div",
				{ className: "ad-peripheral-tester" },
				h(
					"div",
					{ className: "ad-peripheral-toolbar" },
					modes.length > 1
						? h(
								"div",
								{ className: "ad-mode-tabs", role: "tablist", "aria-label": "外设测试类型" },
								modes.map(([id, label, locked]) =>
									h(
										"button",
										{
											key: id,
											type: "button",
											role: "tab",
											"aria-selected": mode === id,
											"aria-disabled": locked === true,
											disabled: running,
											title: locked === true ? "可以先查看麦克风设备；录音前需完成扬声器测试" : "",
											className: `ad-mode-tab${mode === id ? " ad-active" : ""}${locked === true ? " ad-locked" : ""}`,
											onClick: () => props.onMode(id)
										},
										label
									)
								)
							)
						: h("span", { className: "ad-test-title" }, modes[0]?.[1] ?? "外设测试"),
					h(
						"button",
						{
							className: "ad-button ad-refresh-button",
							type: "button",
							disabled: running || refreshing || props.refreshDisabled === true,
							onClick: props.onRefresh,
							title: "设备插拔或配置变化后，手动更新一次设备枚举"
						},
						refreshing ? `更新中 ${refreshElapsedSec}s…` : props.refreshLabel ?? "更新设备信息"
					)
				),
				h(
					"div",
					{ className: props.refreshFailed ? "ad-inline-status ad-error" : "ad-inline-status", role: "status", title: props.refreshMessage ?? "" },
					props.refreshMessage ?? ""
				),
				h(
					"div",
					{ className: "ad-test-grid" },
					mode === "camera"
						? [
								field("device", "V4L2 摄像头", select("device", form.device, (value) => props.onField("device", value), deviceOptions)),
								field(
									"pixel",
									"像素格式",
									select(
										"pixel",
										selectedFormatValue,
										(value) => props.onField("pixelFormat", value),
										formatOptions
									)
								),
								field(
									"resolution",
									"分辨率",
									select(
										"resolution",
										selectedResolution,
										(value) => {
											const resolution = resolutions.find((item) => item.value === value);
											if (resolution) props.onResolution(resolution.width, resolution.height);
										},
										resolutions
									)
								),
								field(
									"fps",
									"帧率",
									select("fps", selectedFps, (value) => props.onField("fps", value), fpsOptions)
								),
								field(
									"frames",
									"采集帧数",
									h("input", {
										id: `ad-peripheral-${mode}-frames`,
										className: "ad-select",
										type: "number",
										value: 1,
										readOnly: true,
										"aria-readonly": true
									})
								)
							]
						: mode === "microphone"
							? [
									field("device", "输入 Source", select("device", selectedValue, (value) => props.onField("sourceKey", value), deviceOptions)),
									field("rate", "采样率", number("rate", form.sampleRate, 8000, 48000, (value) => props.onField("sampleRate", value))),
									field("channels", "声道数", number("channels", form.channels, 1, 2, (value) => props.onField("channels", value))),
									field("duration", "录音时长（秒）", number("duration", form.durationSec, 1, 10, (value) => props.onField("durationSec", value)))
								]
							: [
									field("device", "输出 Sink", select("device", selectedValue, (value) => props.onField("sinkKey", value), deviceOptions)),
									field("frequency", "频率（Hz）", number("frequency", form.frequency, 100, 2000, (value) => props.onField("frequency", value))),
									field("duration", "播放时长（秒）", number("duration", form.durationSec, 0.25, 5, (value) => props.onField("durationSec", value)))
								]
				),
				h(
					"div",
					{ className: "ad-test-actions" },
					h(
						"button",
						{
							className: "ad-button ad-primary",
							type: "button",
							disabled: running || online === false,
							onClick: () => props.onRun(mode),
							title: online === false ? "当前开发板 SSH 不可达" : "执行一次真实外设测试"
						},
						phase === "running"
							? mode === "microphone" ? `录音 ${form.durationSec} 秒中…` : "测试中…"
							: mode === "camera" ? "采集一帧" : mode === "microphone" ? `录制 ${form.durationSec} 秒` : "播放测试音"
					),
					h("span", { className: "ad-test-note" }, online === true ? "开发板 SSH 在线" : online === false ? "开发板 SSH 离线，测试不可用" : "在线状态尚未检测")
				),
				h(
					"div",
					{ className: "ad-test-note" },
					mode === "microphone"
						? "麦克风可独立录音并分析电平；录音完成后可直接回放。回放无声时再检查扬声器。"
						: mode === "speaker"
							? "扬声器可单独测试，也可用于麦克风录音回放。"
							: "采集将请求一帧并写入板端 /tmp/ascend310b-debug/peripherals，不会触发 NPU 推理。"
				),
				h(
					"div",
					{ className: "ad-test-note" },
					`${bluetoothSummary} · ${pulseSummary} · Pulse source/sink 名称可手动填写`
				),
				h(
					"div",
					{ className: "ad-peripheral-result", role: "status", "aria-live": "polite" },
					phase === "failed"
						? h("div", { className: "ad-error", role: "alert" }, props.state?.message ?? "外设操作失败。")
						: phase === "running" && props.state?.action === mode
							? h("span", { className: "ad-note" }, mode === "microphone"
									? `正在录音约 ${form.durationSec} 秒，请对着麦克风说话；完成后还会进行传输和电平分析。`
									: "正在执行板端外设测试…")
							: result
									? h(
											"div",
											null,
											h(
												"div",
												{ className: "ad-result-head" },
												h("strong", null, result.ok ? "本次测试已返回证据" : "本次测试失败"),
												h("span", { className: result.evidence === "observed-pass" ? "ad-status-ok" : "ad-note" }, `${result.evidence ?? "untested"} · exit ${result.exitCode}`)
											),
											h("div", { className: "ad-result-grid" }, resultSummary.map(([label, value]) => h(ResultMetric, { key: label, label, value }))),
											resultText ? h("pre", { className: "ad-pre" }, resultText) : null,
											mode === "speaker" && result.executed
												? h(
														"div",
														{ className: "ad-confirm", role: "group", "aria-label": "扬声器人工确认" },
														h("span", { className: "ad-note" }, "是否实际听到测试音？"),
														h("button", { className: "ad-button", type: "button", onClick: () => props.onConfirm(true) }, "已听到"),
														h("button", { className: "ad-button", type: "button", onClick: () => props.onConfirm(false) }, "未听到"),
														props.confirmed === true
															? h("span", { className: "ad-status-ok" }, "人工确认通过")
															: props.confirmed === false
																? h("span", { className: "ad-error" }, "人工确认未通过")
																: null
													)
												: null,
											h(
												"details",
												{ className: "ad-peripheral-raw" },
												h("summary", null, "命令与原始输出"),
												h("pre", null, [result.command, result.output ? `输出：${result.output}` : "", result.stderr].filter(Boolean).join("\n"))
											)
										)
									: h("span", { className: "ad-note" }, "选择设备与参数后执行一次真实测试。上方枚举本身不代表采集或播放通过。")
				),
				mode === "camera"
					? h(
							"div",
							{ className: "ad-camera-preview" },
							cameraPreview
								? h(
										"a",
										{ href: cameraPreview, target: "_blank", rel: "noopener noreferrer", title: "在新窗口打开原始 JPEG" },
										h("img", { src: cameraPreview, alt: "摄像头本次采集画面", loading: "eager", decoding: "async" })
									)
								: h("span", { className: "ad-note" }, "采集成功后会在这里显示本次 JPEG 画面。")
						)
					: null,
				mode === "microphone"
					? h(
							"div",
							{ className: "ad-recording-playback" },
							h("strong", null, "录音回放闭环"),
							result?.ok === true
								? h(
								"div",
								{ className: "ad-confirm" },
								h("button", {
									className: "ad-button ad-primary ad-playback-button",
									type: "button",
									disabled: running || playback.phase === "running" || typeof result.output !== "string" || result.output === "",
									onClick: () => props.onPlayRecording(result.output)
								}, playback.phase === "running" ? "正在回放…" : "通过扬声器回放这段录音"),
								h("span", { className: "ad-note" }, "回放使用当前扬声器设备")
							)
								: h("div", { className: "ad-note" }, "本次麦克风录音成功后，可在这里立即通过扬声器回放。"),
							result?.ok === true && props.confirmed !== true
								? h("div", { className: "ad-note" }, "扬声器尚未单独确认；如果回放无声，请继续测试扬声器。")
								: null,
							result?.ok === true && playback.phase === "failed"
								? h("div", { className: "ad-error", role: "alert" }, playback.message ?? "录音回放失败。")
								: result?.ok === true && playback.phase === "done" && playback.result
									? h(
											"div",
											null,
											h(
												"div",
												{ className: "ad-result-grid" },
												h(ResultMetric, { label: "录音文件", value: playbackData.sourceRecording }),
												h(ResultMetric, { label: "输出设备", value: playbackData.device }),
												h(ResultMetric, { label: "输出路由", value: playbackData.outputRoute }),
												h(ResultMetric, { label: "WAV 时长", value: playbackData.durationSec ? `${playbackData.durationSec} s` : null }),
												h(ResultMetric, { label: "SHA-256", value: playbackData.sha256 })
											),
											h(
												"div",
												{ className: "ad-confirm", role: "group", "aria-label": "录音回放人工确认" },
												h("span", { className: "ad-note" }, "是否从扬声器听到了刚才的录音？"),
												h("button", { className: "ad-button", type: "button", onClick: () => props.onPlaybackConfirm(true) }, "听到了录音"),
												h("button", { className: "ad-button", type: "button", onClick: () => props.onPlaybackConfirm(false) }, "没听到录音"),
												props.playbackConfirmed === true
													? h("span", { className: "ad-status-ok" }, "录音回放确认通过")
													: props.playbackConfirmed === false
														? h("span", { className: "ad-error" }, "录音回放确认未通过")
														: null
											)
										)
									: null
						)
					: null
			);
		}

		/** One audio workbench: microphone input and speaker output stay visible together. */
		function AudioTester(props) {
			const choices = peripheralChoices(props.inventory);
			const microphone = props.forms?.microphone ?? {};
			const speaker = props.forms?.speaker ?? {};
			const state = props.state ?? { phase: "idle", action: null, result: null, message: "" };
			const running = state.phase === "running";
			const online = props.targetOnline;
			const appendSelected = (items, value) => items.some((item) => item.value === value)
				? items
				: value ? [{ value, label: value }, ...items] : items;
			const sourceOptions = appendSelected(choices.audioSources, microphone.sourceKey);
			const sinkOptions = appendSelected(choices.audioSinks, speaker.sinkKey);
			const currentSpeaker = sinkOptions.find((item) => item.value === speaker.sinkKey);
			const microphoneResult = state.action === "microphone" ? state.result : null;
			const latestMicrophone = microphoneResult ?? props.lastMicrophone ?? null;
			const speakerResult = state.action === "speaker" ? state.result : null;
			const microphoneData = latestMicrophone?.data ?? {};
			const speakerData = speakerResult?.data ?? {};
			const playback = props.recordingPlayback ?? { phase: "idle", result: null, message: "" };
			const playbackData = playback.result?.data ?? {};
			const field = (mode, id, label, control) => h(
				"div",
				{ className: "ad-field" },
				h("label", { htmlFor: `ad-audio-${mode}-${id}` }, label),
				control
			);
			const select = (mode, id, value, onChange, items) => h(
				"select",
				{
					id: `ad-audio-${mode}-${id}`,
					className: "ad-select",
					value,
					disabled: running,
					onChange: (event) => onChange(event.target.value)
				},
				items.map((item) => h(
					"option",
					{ key: item.value, value: item.value, disabled: item.monitor === true || item.disabled === true },
					item.label ?? item.value
				))
			);
			const number = (mode, id, value, min, max, onChange) => h("input", {
				id: `ad-audio-${mode}-${id}`,
				className: "ad-select",
				type: "number",
				min,
				max,
				value,
				disabled: running,
				onChange: (event) => onChange(event.target.value)
			});
			const microphoneStatus = state.action === "microphone" && state.phase === "failed"
				? h("div", { className: "ad-error", role: "alert" }, state.message ?? "麦克风录音失败。")
				: state.action === "microphone" && running
					? h("span", { className: "ad-note" }, `正在录音约 ${microphone.durationSec} 秒，请说话。`)
					: latestMicrophone?.ok === true
						? h(
								"div",
								null,
								h("div", { className: "ad-result-head" }, h("strong", null, "最近一次录音证据"), h("span", { className: microphoneData.signalDetected === true ? "ad-status-ok" : "ad-note" }, microphoneData.signalDetected === true ? "检测到信号" : "未检测到可信信号")),
								h(
									"div",
									{ className: "ad-result-grid" },
									h(ResultMetric, { label: "设备", value: microphoneData.device }),
									h(ResultMetric, { label: "请求 WAV", value: microphoneData.requestedDurationSec !== undefined ? `${microphoneData.requestedDurationSec} s` : null }),
									h(ResultMetric, { label: "实际 WAV", value: microphoneData.durationSec !== undefined ? `${microphoneData.durationSec} s` : null }),
									h(ResultMetric, { label: "WAV 参数", value: microphoneData.sampleRate ? `${microphoneData.sampleRate} Hz / ${microphoneData.channels} ch` : null }),
									h(ResultMetric, { label: "RMS", value: microphoneData.rms }),
									h(ResultMetric, { label: "峰值", value: microphoneData.peak }),
									h(ResultMetric, { label: "采集耗时", value: microphoneData.captureElapsedSec !== undefined ? `${microphoneData.captureElapsedSec} s` : null }),
									h(ResultMetric, { label: "操作总耗时", value: latestMicrophone.durationMs !== undefined ? `${(latestMicrophone.durationMs / 1000).toFixed(2)} s` : null })
								)
							)
						: h("span", { className: "ad-note" }, "尚未录音；上方枚举不代表麦克风能成功采集。");
			const speakerStatus = state.action === "speaker" && state.phase === "failed"
				? h("div", { className: "ad-error", role: "alert" }, state.message ?? "扬声器播放失败。")
				: state.action === "speaker" && running
					? h("span", { className: "ad-note" }, "正在播放测试音，请确认是否实际听到。")
					: speakerResult?.ok === true
						? h(
								"div",
								null,
								h("div", { className: "ad-result-head" }, h("strong", null, "最近一次播放证据"), h("span", { className: speakerData.heard === null ? "ad-note" : "ad-status-ok" }, "等待人工听音确认")),
								h(
									"div",
									{ className: "ad-result-grid" },
									h(ResultMetric, { label: "输出设备", value: speakerData.device }),
									h(ResultMetric, { label: "输出路由", value: speakerData.outputRoute }),
									h(ResultMetric, { label: "测试音", value: speakerData.frequency ? `${speakerData.frequency} Hz / ${speakerData.durationSec} s` : null })
								),
								h(
									"div",
									{ className: "ad-confirm", role: "group", "aria-label": "扬声器人工确认" },
									h("span", { className: "ad-note" }, "是否实际听到测试音？"),
									h("button", { className: "ad-button", type: "button", onClick: () => props.onConfirm(true) }, "已听到"),
									h("button", { className: "ad-button", type: "button", onClick: () => props.onConfirm(false) }, "未听到"),
									props.confirmed === true ? h("span", { className: "ad-status-ok" }, "人工确认通过") : props.confirmed === false ? h("span", { className: "ad-error" }, "人工确认未通过") : null
								)
							)
						: h("span", { className: "ad-note" }, "尚未播放测试音；命令成功与真实听音必须分开记录。");
			return h(
				"div",
				{ className: "ad-audio-tester" },
				h(
					"div",
					{ className: "ad-peripheral-toolbar" },
					h("span", { className: "ad-test-title" }, "麦克风与扬声器"),
					h(
						"button",
						{
							className: "ad-button ad-refresh-button",
							type: "button",
							disabled: running || props.refreshing || props.refreshDisabled,
							onClick: props.onRefresh
						},
						props.refreshing ? `更新中 ${props.refreshElapsedSec ?? 0}s…` : "更新音频接口信息"
					)
				),
				h("div", { className: props.refreshFailed ? "ad-inline-status ad-error" : "ad-inline-status", role: "status", title: props.refreshMessage ?? "" }, props.refreshMessage ?? ""),
				h(
					"div",
					{ className: "ad-audio-grid" },
					h(
						"section",
						{ className: "ad-audio-pane" },
						h("div", { className: "ad-audio-pane-head" }, h("h4", null, "麦克风输入"), h("span", { className: "ad-note" }, "录音与电平分析")),
						h(
							"div",
							{ className: "ad-test-grid" },
							field("microphone", "source", "输入 Source", select("microphone", "source", microphone.sourceKey, (value) => props.onField("microphone", "sourceKey", value), sourceOptions)),
							field("microphone", "rate", "采样率", number("microphone", "rate", microphone.sampleRate, 8000, 48000, (value) => props.onField("microphone", "sampleRate", value))),
							field("microphone", "channels", "声道数", number("microphone", "channels", microphone.channels, 1, 2, (value) => props.onField("microphone", "channels", value))),
							field("microphone", "duration", "录音时长（秒）", number("microphone", "duration", microphone.durationSec, 1, 10, (value) => props.onField("microphone", "durationSec", value)))
						),
						h(
							"div",
							{ className: "ad-test-actions" },
							h(
								"button",
								{
									className: "ad-button ad-primary ad-refresh-button",
									type: "button",
									disabled: running || online === false,
									onClick: () => props.onRun("microphone")
								},
								state.action === "microphone" && running ? `录音 ${microphone.durationSec} 秒中…` : `录制 ${microphone.durationSec} 秒`
							),
							h("span", { className: "ad-test-note" }, online === false ? "SSH 离线" : "麦克风可独立录音")
						),
						h("div", { className: "ad-audio-result", role: "status", "aria-live": "polite" }, microphoneStatus)
					),
					h(
						"section",
						{ className: "ad-audio-pane" },
						h("div", { className: "ad-audio-pane-head" }, h("h4", null, "扬声器输出"), h("span", { className: "ad-note" }, "测试音与人工听音")),
						h(
							"div",
							{ className: "ad-test-grid" },
							field("speaker", "sink", "输出 Sink", select("speaker", "sink", speaker.sinkKey, (value) => props.onField("speaker", "sinkKey", value), sinkOptions)),
							field("speaker", "frequency", "频率（Hz）", number("speaker", "frequency", speaker.frequency, 100, 2000, (value) => props.onField("speaker", "frequency", value))),
							field("speaker", "duration", "播放时长（秒）", number("speaker", "duration", speaker.durationSec, 0.25, 5, (value) => props.onField("speaker", "durationSec", value)))
						),
						h(
							"div",
							{ className: "ad-test-actions" },
							h(
								"button",
								{
									className: "ad-button ad-primary ad-refresh-button",
									type: "button",
									disabled: running || online === false,
									onClick: () => props.onRun("speaker")
								},
								state.action === "speaker" && running ? "播放中…" : "播放测试音"
							),
							h("span", { className: "ad-test-note" }, online === false ? "SSH 离线" : "输出设备同时用于录音回放")
						),
						h("div", { className: "ad-audio-result", role: "status", "aria-live": "polite" }, speakerStatus)
					)
				),
				h(
					"section",
					{ className: "ad-audio-playback" },
					h("div", { className: "ad-audio-pane-head" }, h("h4", null, "录音回放"), h("span", { className: "ad-note" }, "使用右侧当前扬声器")),
					h("div", { className: "ad-audio-playback-route" }, `输出设备：${currentSpeaker?.label ?? speaker.sinkKey ?? "未选择"}`),
					latestMicrophone?.ok === true
						? h(
								"div",
								{ className: "ad-confirm" },
								h("button", {
									className: "ad-button ad-primary ad-playback-button",
									type: "button",
									disabled: running || playback.phase === "running" || typeof latestMicrophone.output !== "string" || latestMicrophone.output === "",
									onClick: () => props.onPlayRecording(latestMicrophone.output)
								}, playback.phase === "running" ? "正在回放…" : "回放最近一次录音"),
								h("span", { className: "ad-note" }, "切换右侧输出 Sink 后可直接再次回放")
							)
						: h("div", { className: "ad-note" }, "先完成一次麦克风录音，回放按钮会在这里启用。"),
					playback.phase === "failed"
						? h("div", { className: "ad-error", role: "alert" }, playback.message ?? "录音回放失败。")
						: playback.phase === "done" && playback.result
							? h(
									"div",
									null,
									h(
										"div",
										{ className: "ad-result-grid" },
										h(ResultMetric, { label: "录音文件", value: playbackData.sourceRecording }),
										h(ResultMetric, { label: "输出设备", value: playbackData.device }),
										h(ResultMetric, { label: "输出路由", value: playbackData.outputRoute }),
										h(ResultMetric, { label: "WAV 时长", value: playbackData.durationSec ? `${playbackData.durationSec} s` : null })
									),
									h(
										"div",
										{ className: "ad-confirm", role: "group", "aria-label": "录音回放人工确认" },
										h("span", { className: "ad-note" }, "是否从扬声器听到了刚才的录音？"),
										h("button", { className: "ad-button", type: "button", onClick: () => props.onPlaybackConfirm(true) }, "听到了录音"),
										h("button", { className: "ad-button", type: "button", onClick: () => props.onPlaybackConfirm(false) }, "没听到录音"),
										props.playbackConfirmed === true ? h("span", { className: "ad-status-ok" }, "录音回放确认通过") : props.playbackConfirmed === false ? h("span", { className: "ad-error" }, "录音回放确认未通过") : null
									)
								)
							: null
				)
			);
		}

		/** Full v4l2-ctl --list-formats-ext capability report for one camera node. */
		function CameraFormats(props) {
			const formats = props.inventory?.videoFormats?.get?.(props.device)?.formats ?? [];
			if (formats.length === 0) {
				return h("div", { className: "ad-empty" }, "尚无该节点的格式列表；设备插拔后点击「更新设备信息」从 v4l2-ctl 重新采集。");
			}
			return h(
				"div",
				{ className: "ad-format-grid" },
				formats.map((format) => h(
					"div",
					{ className: "ad-format-card", key: format.fourcc },
					h("div", { className: "ad-format-head" }, h("strong", null, format.fourcc), h("span", { className: "ad-note" }, format.description || "无格式描述")),
					h(
						"div",
						{ className: "ad-format-sizes" },
						(format.sizes ?? []).length > 0
							? format.sizes.map((size) => h(
									"div",
									{ className: "ad-format-size", key: size.value },
									h("span", null, size.label),
									h("code", null, size.fps?.length > 0 ? `${size.fps.join(" / ")} fps` : "帧率范围未报告")
								))
							: h("span", { className: "ad-note" }, "该格式没有报告离散或连续尺寸")
					)
				))
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
			if (keys.length === 0) return h("div", { className: "ad-empty" }, "尚无设备信息：设备插拔或配置变化后点击「更新设备信息」。");
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
				["audioSources", "音频 Source", "pactl list short sources"],
				["pulseCards", "Pulse Card", "pactl list short cards"],
				["pulseCardDetails", "Pulse Card Profile", "pactl list cards"],
				["bluetoothController", "蓝牙控制器", "bluetoothctl show"],
				["bluetoothDevices", "蓝牙配对设备", "bluetoothctl devices"],
				["bluetoothInfo", "蓝牙连接详情", "bluetoothctl info"],
				["usbDevices", "USB 设备", "lsusb"],
				["videoDevices", "摄像头 / V4L2", "v4l2-ctl --list-devices + /dev/video*"],
				["videoDetails", "摄像头能力", "v4l2-ctl --info"],
				["videoFormats", "摄像头格式与分辨率", "v4l2-ctl --list-formats-ext"]
			];
			const hasAny = fields.some(([key]) => factValue(props, key) !== null);
			if (!hasAny) return h("div", { className: "ad-empty" }, "尚无外设信息：插拔设备后点击「更新设备信息」。");
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
							h("td", { className: "ad-note" }, isRecent(target.lastSeen) ? "15 分钟内扫描在线" : target.lastSeen ? `历史记录（不等于在线） · ${stamp(target.lastSeen)}` : "未探测"),
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
				["camera", "摄像头"],
				["audio", "音频接口"],
				["assets", "开发板记录"],
				["debug", "调试"],
				["raw", "原始输出"]
			];
			return h(
				"div",
				{ className: "ad-tabs", role: "tablist", "aria-label": "昇腾310B调试面板" },
				tabs.map(([id, label]) =>
					h(
						"button",
						{
							key: id,
							type: "button",
							role: "tab",
							id: `ad-tab-${id}`,
							"aria-selected": props.active === id,
							"aria-controls": `ad-panel-${id}`,
							tabIndex: props.active === id ? 0 : -1,
							ref: (node) => {
								if (props.tabRefs?.current) props.tabRefs.current[id] = node;
							},
							className: props.active === id ? "ad-tab ad-active" : "ad-tab",
							onClick: () => props.onChange(id),
							onKeyDown: (event) => {
								const index = tabs.findIndex(([tabId]) => tabId === id);
								let nextIndex = null;
								if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
								if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
								if (event.key === "Home") nextIndex = 0;
								if (event.key === "End") nextIndex = tabs.length - 1;
								if (nextIndex === null) return;
								event.preventDefault();
								const nextId = tabs[nextIndex][0];
								props.onChange(nextId);
								setTimeout(() => props.tabRefs?.current?.[nextId]?.focus?.(), 0);
							}
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
					"尚无已登记开发板。点击「局域网扫描」扫描当前网段，或点击「串口发现」从 USB 串口读取开发板。"
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
								boardMacs(board).length > 1
									? h("div", { className: "ad-note" }, `另有 ${boardMacs(board).length - 1} 个网口别名`)
									: null,
								h("div", { className: "ad-note" }, `${board.soc ?? "SoC 未采集"} · ${board.tier ?? "档位未采集"}`)
							),
							h(
								"td",
								null,
								h("div", { className: "ad-mono" }, `CANN ${board.cann ?? "未采集"}`),
								h("div", { className: "ad-mono" }, `驱动 ${board.driver ?? "未采集"}`),
								h("div", { className: "ad-mono" }, `内存 ${memoryGb(board.memoryUsedMb)} / ${memoryGb(board.memoryTotalMb)}`)
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
								h("div", null, isRecent(board.lastSeen) ? "15 分钟内扫描在线" : board.lastSeen ? "历史记录（不等于在线）" : "未探测")
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

		/** Detailed record for the selected board; the rail owns board navigation. */
		function BoardDetail(props) {
			const board = props.board;
			if (!board) {
				return h("div", { className: "ad-empty" }, "请从左侧“在线与当前选择”列表选择一块开发板。历史板可先展开历史记录。");
			}
			const target = props.target ?? null;
			const aliases = boardMacs(board);
			const latest = board.latestChange ?? null;
			const liveState = props.liveness?.byTarget?.[target?.id ?? board.configuredId ?? board.id];
			const statusText = liveState === true
				? "SSH 在线"
				: liveState === false
					? "SSH 离线"
					: isRecent(board.lastSeen)
						? "最近发现，待检测"
						: "历史记录";
			return h(
				"div",
				{ className: "ad-detail" },
				h("div", { className: "ad-detail-title" }, h("strong", null, boardName(board)), h("span", { className: "ad-state-badge" }, statusText)),
				h(
					"table",
					{ className: "ad-table" },
					h(
						"tbody",
						null,
						h("tr", null, h("th", null, "逻辑 ID"), h("td", { className: "ad-mono" }, board.id)),
						h("tr", null, h("th", null, "硬件"), h("td", null, `${board.soc ?? "SoC 未采集"} · ${board.tier ?? "档位未采集"} · ${memoryGb(board.memoryTotalMb)}`)),
						h("tr", null, h("th", null, "网络"), h("td", null, `${board.lastIp ?? "IP 未采集"} · ${board.hostname ?? "主机名未采集"}`)),
						h("tr", null, h("th", null, "MAC 别名"), h("td", { className: "ad-mono" }, aliases.join(", ") || "未采集")),
						h("tr", null, h("th", null, "配置目标"), h("td", null, target ? `${target.id} · ${target.user ?? "HwHiAiUser"}@${target.host}` : "未关联配置")),
						h("tr", null, h("th", null, "最近发现"), h("td", null, board.lastSeen ? stamp(board.lastSeen) : "未探测")),
						h("tr", null, h("th", null, "串口"), h("td", null, serialState(board))),
						h("tr", null, h("th", null, "软件"), h("td", null, `CANN ${board.cann ?? "未采集"} · 驱动 ${board.driver ?? "未采集"}`)),
						h(
							"tr",
							null,
							h("th", null, "变化"),
							h("td", null, latest
								? `${board.changesCount} 次 · ${(latest.fields ?? []).map((field) => `${field.name} ${field.from ?? "未记录"} → ${field.to ?? "未记录"}`).join("；")}`
								: "暂无系统变化")
						)
					)
				),
				target && target.isConfigured !== true && (props.configuredTargets ?? []).length > 0
					? h(
							"div",
							{ className: "ad-bind" },
							h("span", { className: "ad-note" }, "将这块发现板绑定到已有配置（只应在确认是同一块物理板时操作）"),
							h(
								"select",
								{
									className: "ad-select",
									"aria-label": "绑定配置目标",
									value: props.bindingConfig ?? "",
									onChange: (event) => props.onBindingConfig(event.target.value)
								},
								h("option", { value: "" }, "选择已有配置"),
								...(props.configuredTargets ?? []).map((candidate) =>
									h("option", { key: candidate.id, value: candidate.id }, `${candidate.id} · ${candidate.label ?? candidate.host}`)
								)
							),
							h(
								"button",
								{
									className: "ad-button ad-primary",
									type: "button",
									disabled: !props.bindingConfig || props.binding?.kind === "running",
									onClick: props.onBind
								},
								props.binding?.kind === "running" ? "绑定中…" : "绑定到配置"
							),
							props.binding?.kind === "failed" ? h("span", { className: "ad-error" }, props.binding.message) : null,
							props.binding?.kind === "done" ? h("span", { className: "ad-note" }, "绑定已保存，下次加载会使用该配置。") : null
						)
					: null
			);
		}


		/** Left navigation: one row per physical board; history is collapsed by default. */
		function BoardRail(props) {
			const [showHistory, setShowHistory] = react.useState(false);
			const boards = props.boards ?? [];
			const targets = props.targets ?? [];
			const liveness = props.liveness?.byTarget ?? {};
			const checking = props.liveness?.kind === "checking";
			const matchedTarget = (board) =>
				targets.find(
					(target) =>
						target.id === board.configuredId ||
						target.discoveredId === board.id ||
						boardMacs(board).includes(target.mac)
				);
			const boardRows = boards.map((board) => {
				const target = matchedTarget(board);
				const recent = isRecent(board.lastSeen);
				const liveState = liveness[target?.id ?? board.id];
				const online = liveState === true;
				const offline = liveState === false;
				const status = checking
					? online
						? "上次检测 SSH 可达（正在重新检查）"
						: offline
							? "上次检测 SSH 不可达（正在重新检查）"
							: "正在检查 SSH 连通性"
					: online
					? "本次检测 SSH 可达"
					: offline
						? "本次检测 SSH 不可达"
						: recent
							? "最近 15 分钟发现（尚未做本次连通性检测）"
							: board.lastSeen
								? "历史记录（不等于在线）"
								: "未探测";
				return {
					key: `board:${board.id}`,
					targetId: target?.id ?? board.id,
					title: `${boardName(board)}${macSuffix(board.mac)}`,
					secondary: target ? targetName(target) : "",
					mac: board.mac,
					aliases: boardMacs(board).join(" "),
					detail: `${board.lastIp ?? "无 IP"} · ${board.tier ?? "档位未知"} · ${memoryGb(board.memoryUsedMb)} / ${memoryGb(board.memoryTotalMb)} · ${boardMacs(board).length} 个网口 MAC · ${status} · ${serialState(board)}`,
					seenAt: board.lastSeen ?? "",
					changed: board.changesCount > 0,
					online,
					offline
				};
			});
			const representedTargets = new Set(boardRows.map((row) => row.targetId));
			const targetRows = targets
				.filter((target) => !representedTargets.has(target.id))
				.filter((target) => !boards.some((board) => {
					const aliases = boardMacs(board);
					const targetAliases = [target.mac, ...(Array.isArray(target.macs) ? target.macs : [])].filter(Boolean);
					return targetAliases.some((mac) => aliases.includes(mac)) || target.id === board.configuredId || target.id === board.discoveredId;
				}))
				.map((target) => {
					const liveState = liveness[target.id];
					const online = liveState === true;
					const offline = liveState === false;
					return {
						key: `target:${target.id}`,
						targetId: target.id,
						title: `${targetName(target)}${macSuffix(target.mac)}`,
						mac: target.mac,
						aliases: (Array.isArray(target.macs) ? target.macs : [target.mac]).filter(Boolean).join(" "),
						detail: `${target.host} · ${checking ? online ? "上次检测可达（正在重新检查）" : offline ? "上次检测不可达（正在重新检查）" : "正在检查 SSH 连通性" : online ? "本次检测 SSH 可达" : offline ? "本次检测 SSH 不可达" : isRecent(target.lastSeen) ? "最近 15 分钟发现（尚未做本次连通性检测）" : "未建档或未探测"}`,
						seenAt: target.lastSeen ?? "",
						changed: false,
						online,
						offline
					};
				});
			const rows = [...boardRows, ...targetRows];
			if (rows.length === 0) return h("div", { className: "ad-empty-rail" }, "没有已登记开发板。");
			const sorted = [...rows].sort((left, right) => (Date.parse(right.seenAt) || 0) - (Date.parse(left.seenAt) || 0));
			// Keep rail membership independent of a liveness result. Otherwise a
			// single online check adds/removes cards and pushes mobile content down.
			const selectedRow = sorted.find((row) => row.targetId === props.selected);
			const primaryRows = [...(selectedRow ? [selectedRow] : []), ...sorted.filter((row) => row !== selectedRow)].slice(0, 6);
			const historyRows = sorted.filter((row) => !primaryRows.includes(row));
			const needle = String(props.query ?? "").trim().toLowerCase();
			const matches = (row) => [row.title, row.mac, row.aliases, row.detail].filter(Boolean).join(" ").toLowerCase().includes(needle);
			const visible = needle === ""
				? showHistory ? sorted : primaryRows
				: sorted.filter(matches);
			if (visible.length === 0) return h("div", { className: "ad-empty-rail" }, "没有匹配的开发板。");
			return h(
				"div",
				{ className: "ad-board-list" },
				h("div", { className: "ad-rail-heading" }, needle === "" ? "常用与当前选择" : "搜索结果"),
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
							// Liveness wins over historical change state. A board that was
							// just found must be green even when older probes recorded changes.
							h("i", { className: `ad-board-dot${row.online ? " ad-online" : row.offline ? " ad-offline" : row.changed ? " ad-changed" : ""}` }),
							h("span", { className: "ad-board-name" }, row.title),
							row.changed ? h("span", { className: "ad-board-flag" }, "已变更") : null
						),
						h("span", { className: "ad-board-meta" }, row.detail),
						row.secondary && row.secondary !== row.title ? h("span", { className: "ad-board-meta" }, `配置：${row.secondary}`) : null,
						h("span", { className: "ad-board-mac" }, row.mac ? `MAC ${row.mac}` : "MAC 未采集")
					)
				),
				needle === "" && historyRows.length > 0
					? h(
							"button",
							{
								className: "ad-button ad-history-toggle",
								type: "button",
								onClick: () => setShowHistory((value) => !value)
							},
							showHistory ? "收起历史记录" : `显示历史记录（${historyRows.length}）`
						)
					: null
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
			const [tab, setTab] = react.useState("overview");
			const [boardQuery, setBoardQuery] = react.useState("");
			const [dependencyCase, setDependencyCase] = react.useState("case1");
			const [liveness, setLiveness] = react.useState({ kind: "idle", byTarget: {}, checkedAt: null });
			const [peripheralMode, setPeripheralMode] = react.useState("camera");
			// Camera and audio pages set their own default mode when opened.
			const [peripheralForm, setPeripheralForm] = react.useState({
				camera: { device: "/dev/video0", width: 640, height: 480, fps: 30, pixelFormat: "MJPG" },
				microphone: { sourceKey: "alsa:plughw:0,0", backend: "alsa", device: "plughw:0,0", durationSec: 3, sampleRate: 16000, channels: 1 },
				speaker: { sinkKey: "alsa:plughw:0,0", backend: "alsa", device: "plughw:0,0", durationSec: 1, sampleRate: 48000, channels: 1, frequency: 440 }
			});
			const [peripheral, setPeripheral] = react.useState({ phase: "idle", action: null, inventory: null, result: null, message: "" });
			const [lastMicrophoneResult, setLastMicrophoneResult] = react.useState(null);
			const [speakerConfirmed, setSpeakerConfirmed] = react.useState(null);
			const [recordingPlayback, setRecordingPlayback] = react.useState({ phase: "idle", result: null, message: "" });
			const [playbackConfirmed, setPlaybackConfirmed] = react.useState(null);
			const [cameraRefresh, setCameraRefresh] = react.useState({ phase: "idle", elapsedSec: 0, message: "" });
			const [audioRefresh, setAudioRefresh] = react.useState({ phase: "idle", elapsedSec: 0, message: "" });
			const [systemRefresh, setSystemRefresh] = react.useState({ phase: "idle", elapsedSec: 0, message: "" });
			const selectedRef = react.useRef("");
			const viewGeneration = react.useRef(0);
			const livenessGeneration = react.useRef(0);
			const mountedRef = react.useRef(true);
			const tabRefs = react.useRef({});
			const loadController = react.useRef(null);
			const discoveryController = react.useRef(null);
			const serialController = react.useRef(null);
			const livenessController = react.useRef(null);
			const peripheralController = react.useRef(null);
			const peripheralGeneration = react.useRef(0);
			const playbackController = react.useRef(null);
			const playbackGeneration = react.useRef(0);
			const cameraRefreshController = react.useRef(null);
			const cameraRefreshGeneration = react.useRef(0);
			const audioRefreshController = react.useRef(null);
			const audioRefreshGeneration = react.useRef(0);
			const systemRefreshController = react.useRef(null);
			const systemRefreshGeneration = react.useRef(0);

			const setSelectedTarget = react.useCallback((targetId) => {
				const next = targetId ?? "";
				if (selectedRef.current !== next) {
					peripheralGeneration.current += 1;
					playbackGeneration.current += 1;
					cameraRefreshGeneration.current += 1;
					audioRefreshGeneration.current += 1;
					systemRefreshGeneration.current += 1;
					peripheralController.current?.abort();
					playbackController.current?.abort();
					cameraRefreshController.current?.abort();
					audioRefreshController.current?.abort();
					systemRefreshController.current?.abort();
					peripheralController.current = null;
					playbackController.current = null;
					cameraRefreshController.current = null;
					audioRefreshController.current = null;
					systemRefreshController.current = null;
					setPeripheral({ phase: "idle", action: null, inventory: null, result: null, message: "" });
					setLastMicrophoneResult(null);
					setPeripheralMode("microphone");
					setSpeakerConfirmed(null);
					setRecordingPlayback({ phase: "idle", result: null, message: "" });
					setPlaybackConfirmed(null);
					setCameraRefresh({ phase: "idle", elapsedSec: 0, message: "" });
					setAudioRefresh({ phase: "idle", elapsedSec: 0, message: "" });
					setSystemRefresh({ phase: "idle", elapsedSec: 0, message: "" });
				}
				selectedRef.current = next;
				setSelected(next);
			}, []);
			/**
			 * Check transport reachability without running a full environment probe.
			 * This is deliberately independent from viewGeneration so report loading
			 * cannot cancel the online indicator checks.
			 */
			const checkLiveness = react.useCallback(async (targets = null) => {
				const generation = livenessGeneration.current + 1;
				livenessGeneration.current = generation;
				livenessController.current?.abort();
				const controller = new AbortController();
				livenessController.current = controller;
				setLiveness((previous) => ({ ...previous, kind: "checking", message: "" }));
				try {
					const response = await fetch(LIVENESS_URL, {
						method: "POST",
						headers: { "content-type": "application/json", accept: "application/json" },
						signal: controller.signal,
						body: JSON.stringify(Array.isArray(targets) && targets.length > 0 ? { targets } : {})
					});
					const payload = await response.json();
					if (!response.ok || payload.ok !== true) throw new Error(payload.error ?? `HTTP ${response.status}`);
					if (!mountedRef.current || livenessGeneration.current !== generation) return null;
					const byTarget = Object.fromEntries(
						(payload.targets ?? []).map((entry) => [entry.id, entry.online === true])
					);
					setLiveness({
						kind: "done",
						byTarget,
						checkedAt: payload.checkedAt ?? new Date().toISOString(),
						message: ""
					});
					return payload;
				} catch (error) {
					if (error?.name === "AbortError" || !mountedRef.current || livenessGeneration.current !== generation) return null;
					setLiveness({
						kind: "failed",
						byTarget: {},
						checkedAt: new Date().toISOString(),
						message: String(error?.message ?? error)
					});
					return null;
				}
			}, []);
			const applyInventoryDefaults = react.useCallback((inventory) => {
				const choices = peripheralChoices(inventory);
				setPeripheralForm((previous) => {
					const cameraDevice = choices.cameras.some((item) => item.value === previous.camera.device && item.disabled !== true)
						? previous.camera.device
						: choices.cameras.find((item) => item.disabled !== true)?.value ?? "/dev/video0";
					const microphoneChoice = choices.audioSources.find((item) => item.value === previous.microphone.sourceKey)
						?? choices.audioSources[0]
						?? { value: "alsa:plughw:0,0", backend: "alsa", device: "plughw:0,0" };
					const speakerChoice = choices.audioSinks.find((item) => item.value === previous.speaker.sinkKey)
						?? choices.audioSinks[0]
						?? { value: "alsa:plughw:0,0", backend: "alsa", device: "plughw:0,0" };
					return {
						...previous,
						camera: { ...previous.camera, device: cameraDevice },
						microphone: {
							...previous.microphone,
							sourceKey: microphoneChoice.value,
							backend: microphoneChoice.backend,
							device: microphoneChoice.device
						},
						speaker: {
							...previous.speaker,
							sinkKey: speakerChoice.value,
							backend: speakerChoice.backend,
							device: speakerChoice.device
						}
					};
				});
			}, []);
			const postPeripheral = react.useCallback(async (payload, { phase, action, url }) => {
				const target = selectedRef.current;
				if (target === "") {
					setPeripheral((previous) => ({ ...previous, phase: "failed", action, message: "尚未选择开发板。" }));
					return null;
				}
				const generation = peripheralGeneration.current + 1;
				peripheralGeneration.current = generation;
				peripheralController.current?.abort();
				const controller = new AbortController();
				peripheralController.current = controller;
				setPeripheral((previous) => ({ ...previous, phase, action, message: "" }));
				try {
					const response = await fetch(url, {
						method: "POST",
						headers: { "content-type": "application/json", accept: "application/json" },
						signal: controller.signal,
						body: JSON.stringify({ ...payload, target })
					});
					const body = await response.json();
					const result = body.result ?? null;
					if (result === null && (!response.ok || body.ok !== true)) {
						throw new Error(body.error ?? `HTTP ${response.status}`);
					}
					if (!mountedRef.current || peripheralGeneration.current !== generation || selectedRef.current !== target) return null;
					if (result === null) throw new Error('外设接口响应缺少 result 证据。');
					setPeripheral((previous) => ({
						...previous,
						phase: "done",
						action,
						result,
						message: result.ok === true ? "" : result.text ?? "外设测试失败。"
					}));
					if (action === "microphone" && result.ok === true) setLastMicrophoneResult(result);
					return result;
				} catch (error) {
					if (error?.name === "AbortError" || !mountedRef.current || peripheralGeneration.current !== generation) return null;
					setPeripheral((previous) => ({
						...previous,
						phase: "failed",
						action,
						result: null,
						message: String(error?.message ?? error)
					}));
					return null;
				}
			}, []);
			const runPeripheralTest = react.useCallback((mode) => {
				const url = mode === "camera"
					? CAMERA_CAPTURE_URL
					: mode === "microphone"
						? MICROPHONE_RECORD_URL
						: SPEAKER_TEST_URL;
				if (mode === "speaker") setSpeakerConfirmed(null);
				if (mode === "microphone") {
					setRecordingPlayback({ phase: "idle", result: null, message: "" });
					setPlaybackConfirmed(null);
				}
				return postPeripheral(
					{ allowStateChange: true, ...peripheralForm[mode] },
					{ phase: "running", action: mode, url }
				);
			}, [peripheralForm, postPeripheral]);
			const runRecordingPlayback = react.useCallback(async (recording) => {
				const target = selectedRef.current;
				if (target === "" || typeof recording !== "string" || recording === "") return null;
				const generation = playbackGeneration.current + 1;
				playbackGeneration.current = generation;
				playbackController.current?.abort();
				const controller = new AbortController();
				playbackController.current = controller;
				setPlaybackConfirmed(null);
				setRecordingPlayback({ phase: "running", result: null, message: "" });
				try {
					const response = await fetch(PLAY_RECORDING_URL, {
						method: "POST",
						headers: { "content-type": "application/json", accept: "application/json" },
						signal: controller.signal,
						body: JSON.stringify({
							target,
							backend: peripheralForm.speaker.backend,
							device: peripheralForm.speaker.device,
							recording,
							allowStateChange: true
						})
					});
					const body = await response.json();
					if (!response.ok || body.ok !== true || body.result === null || body.result === undefined) {
						throw new Error(body.error ?? body.result?.text ?? `HTTP ${response.status}`);
					}
					if (!mountedRef.current || playbackGeneration.current !== generation || selectedRef.current !== target) return null;
					setRecordingPlayback({
						phase: "done",
						result: body.result,
						message: body.result.ok === true ? "" : body.result.text ?? "录音回放失败。"
					});
					return body.result;
				} catch (error) {
					if (error?.name === "AbortError" || !mountedRef.current || playbackGeneration.current !== generation) return null;
					setRecordingPlayback({ phase: "failed", result: null, message: String(error?.message ?? error) });
					return null;
				}
			}, [peripheralForm.speaker]);
			const updatePeripheralField = react.useCallback((mode, field, value) => {
				if (mode === "speaker") {
					setSpeakerConfirmed(null);
					setPlaybackConfirmed(null);
					setRecordingPlayback({ phase: "idle", result: null, message: "" });
				}
				setPeripheralForm((previous) => {
					const next = { ...previous[mode], [field]: value };
					if (field === "sourceKey" || field === "sinkKey") {
						const separator = String(value).indexOf(":");
						if (separator > 0) {
							next.backend = String(value).slice(0, separator);
							next.device = String(value).slice(separator + 1);
						}
					}
					return { ...previous, [mode]: next };
				});
			}, []);
			const updateCameraResolution = react.useCallback((width, height) => {
				setPeripheralForm((previous) => ({
					...previous,
					camera: { ...previous.camera, width, height }
				}));
			}, []);
			const selectPeripheralMode = react.useCallback((mode) => {
				setPeripheralMode(mode);
			}, []);
			const abortAll = react.useCallback((except = null) => {
				for (const [name, ref] of [["load", loadController], ["discovery", discoveryController], ["serial", serialController]]) {
					if (name !== except) ref.current?.abort();
				}
			}, []);
			const isCurrent = (generation, controller) => mountedRef.current && viewGeneration.current === generation && (!controller || controller.signal?.aborted !== true);
			const load = react.useCallback((fresh, requestedTarget) => {
				const generation = ++viewGeneration.current;
				abortAll();
				const controller = new AbortController();
				loadController.current = controller;
				const query = [];
				if (fresh) query.push("fresh=1");
				if (typeof requestedTarget === "string" && requestedTarget !== "") query.push(`target=${encodeURIComponent(requestedTarget)}`);
				const url = query.length === 0 ? REPORT_URL : `${REPORT_URL}?${query.join("&")}`;
				setState((previous) => (previous.payload ? { ...previous, kind: "loading", switching: true } : { kind: "loading" }));
				return fetch(url, { headers: { accept: "application/json" }, signal: controller.signal })
					.then(async (response) => {
						if (!response.ok) throw new Error(`HTTP ${response.status}`);
						const payload = await response.json();
						if (payload?.ok !== true) throw new Error(payload?.error ?? `HTTP ${response.status}（响应缺少 ok=true）`);
						return payload;
					})
					.then((payload) => {
						if (!isCurrent(generation, controller)) return;
						setState({ kind: "ready", payload });
						if (payload.target?.id) setSelectedTarget(payload.target.id);
					})
					.catch((error) => {
						if (error?.name === "AbortError" || !isCurrent(generation, controller)) return;
						setState((previous) => previous.payload
							? { kind: "ready", payload: previous.payload, staleError: String(error?.message ?? error), switching: false }
							: { kind: "failed", message: String(error?.message ?? error) });
					})
					.finally(() => {
						if (loadController.current === controller) loadController.current = null;
					});
			}, [abortAll, setSelectedTarget]);

			react.useEffect(() => {
			mountedRef.current = true;
			void load(false);
			void checkLiveness();
			return () => {
				mountedRef.current = false;
				viewGeneration.current += 1;
				livenessGeneration.current += 1;
				peripheralGeneration.current += 1;
				playbackGeneration.current += 1;
				cameraRefreshGeneration.current += 1;
				audioRefreshGeneration.current += 1;
				systemRefreshGeneration.current += 1;
				abortAll();
				livenessController.current?.abort();
				peripheralController.current?.abort();
				playbackController.current?.abort();
				cameraRefreshController.current?.abort();
				audioRefreshController.current?.abort();
				systemRefreshController.current?.abort();
			};
		}, [abortAll, checkLiveness, load]);

			react.useEffect(() => {
				if ((tab !== "camera" && tab !== "audio") || selected === "" || !state.payload) return;
				applyInventoryDefaults(peripheralInventoryFromPayload(state.payload));
			}, [applyInventoryDefaults, selected, state.payload, tab]);

			react.useEffect(() => {
				if (tab === "camera" && peripheralMode !== "camera") setPeripheralMode("camera");
				if (tab === "audio" && peripheralMode === "camera") setPeripheralMode("microphone");
			}, [peripheralMode, tab]);

			react.useEffect(() => {
				const anyRunning = cameraRefresh.phase === "running"
					|| audioRefresh.phase === "running"
					|| systemRefresh.phase === "running";
				if (!anyRunning) return undefined;
				const advance = (previous) => previous.phase === "running"
					? { ...previous, elapsedSec: previous.elapsedSec + 1 }
					: previous;
				const timer = setInterval(() => {
					setCameraRefresh(advance);
					setAudioRefresh(advance);
					setSystemRefresh(advance);
				}, 1000);
				return () => clearInterval(timer);
			}, [audioRefresh.phase, cameraRefresh.phase, systemRefresh.phase]);

			const refreshCategory = react.useCallback(async (category) => {
				const specs = {
					camera: { url: CAMERA_REFRESH_URL, label: "摄像头", generation: cameraRefreshGeneration, controller: cameraRefreshController, setter: setCameraRefresh, timeoutMs: 30000 },
					audio: { url: AUDIO_REFRESH_URL, label: "音频接口", generation: audioRefreshGeneration, controller: audioRefreshController, setter: setAudioRefresh, timeoutMs: 30000 },
					system: { url: SYSTEM_REFRESH_URL, label: "系统信息", generation: systemRefreshGeneration, controller: systemRefreshController, setter: setSystemRefresh, timeoutMs: 45000 }
				};
				const spec = specs[category];
				const target = selectedRef.current;
				if (target === "") {
					spec.setter({ phase: "failed", elapsedSec: 0, message: "尚未选择开发板。" });
					return null;
				}
				if (loadController.current !== null) {
					spec.setter({ phase: "failed", elapsedSec: 0, message: "正在切换开发板，请等待当前记录加载完成。" });
					return null;
				}
				const generation = spec.generation.current + 1;
				spec.generation.current = generation;
				spec.controller.current?.abort();
				const controller = new AbortController();
				spec.controller.current = controller;
				spec.setter({ phase: "running", elapsedSec: 0, message: "" });
				try {
					const response = await fetch(spec.url, {
						method: "POST",
						headers: { "content-type": "application/json", accept: "application/json" },
						signal: controller.signal,
						body: JSON.stringify({ target, timeoutMs: spec.timeoutMs })
					});
					const payload = await response.json();
					if (!response.ok || payload.ok !== true) throw new Error(payload.error ?? `HTTP ${response.status}`);
					if (!mountedRef.current || spec.generation.current !== generation || selectedRef.current !== target) return null;
					setState((previous) => previous.payload
						? previous.payload.target?.id && previous.payload.target.id !== target
							? previous
							: {
								...previous,
								payload: {
									...previous.payload,
									...(category === "system" ? {
										probedAt: payload.probedAt,
										source: payload.source,
										board: payload.board,
										pairing: payload.pairing,
										scope: payload.scope,
										...(payload.missingCore !== undefined ? { missingCore: payload.missingCore } : {}),
										transport: payload.transport
									} : {}),
									facts: { ...(previous.payload.facts ?? {}), ...(payload.facts ?? {}) },
									categoryUpdatedAt: {
										...(previous.payload.categoryUpdatedAt ?? {}),
										[category]: payload.updatedAt
									},
									...(category === "camera" ? { cameraUpdatedAt: payload.updatedAt } : {})
								}
							}
						: previous);
					const elapsed = Number(payload.durationMs) > 0 ? Math.round(Number(payload.durationMs) / 1000) : 0;
					spec.setter({ phase: "done", elapsedSec: elapsed, message: `${spec.label}信息已更新，用时 ${elapsed} 秒。` });
					return payload;
				} catch (error) {
					if (error?.name === "AbortError" || !mountedRef.current || spec.generation.current !== generation) return null;
					spec.setter((previous) => ({ ...previous, phase: "failed", message: String(error?.message ?? error) }));
					return null;
				}
			}, []);
			const refreshCamera = react.useCallback(() => refreshCategory("camera"), [refreshCategory]);
			const refreshAudio = react.useCallback(() => refreshCategory("audio"), [refreshCategory]);
			const refreshSystem = react.useCallback(() => refreshCategory("system"), [refreshCategory]);

			const selectBoard = react.useCallback((targetId) => {
				if (targetId === "") return;
				setBindingConfig("");
				setBinding({ kind: "idle" });
				// Keep the current board identity and content paired until the
				// requested saved report arrives. load() commits both together.
				void load(true, targetId);
			}, [load]);

			const [discovery, setDiscovery] = react.useState({ kind: "idle" });
			const runDiscovery = react.useCallback(async () => {
				const generation = viewGeneration.current;
				abortAll("load");
				const controller = new AbortController();
				discoveryController.current = controller;
				setDiscovery({ kind: "running" });
				try {
					const response = await fetch(DISCOVER_URL, {
						method: "POST",
						headers: { "content-type": "application/json", accept: "application/json" },
						signal: controller.signal,
						body: "{}"
					});
					const payload = await response.json();
					if (!response.ok || payload.ok !== true) throw new Error(payload.error ?? `HTTP ${response.status}`);
					if (!isCurrent(generation, controller)) return null;
					setDiscovery({ kind: "done", payload });
					void checkLiveness();
					const found = Array.isArray(payload.found) ? payload.found : [];
					const nextTarget = selectedRef.current || found[0]?.id || "";
					if (nextTarget !== "") await load(true, nextTarget);
					return payload;
				} catch (error) {
					if (error?.name === "AbortError" || !isCurrent(generation, controller)) {
						setDiscovery((previous) => previous.kind === "running" ? { kind: "cancelled" } : previous);
						return null;
					}
					setDiscovery({ kind: "failed", message: String(error?.message ?? error) });
					return null;
				} finally {
					if (discoveryController.current === controller) discoveryController.current = null;
				}
			}, [abortAll, checkLiveness, load]);

			const cancelDiscovery = react.useCallback(() => {
				viewGeneration.current += 1;
				discoveryController.current?.abort();
				setDiscovery((previous) => previous.kind === "running" ? { kind: "cancelled" } : previous);
			}, []);

			const [serial, setSerial] = react.useState({ kind: "idle" });
			const [bindingConfig, setBindingConfig] = react.useState("");
			const [binding, setBinding] = react.useState({ kind: "idle" });
			const runSerial = react.useCallback(async () => {
				const generation = viewGeneration.current;
				abortAll("load");
				const controller = new AbortController();
				serialController.current = controller;
				setSerial({ kind: "running" });
				try {
					const response = await fetch(SERIAL_URL, {
						method: "POST",
						headers: { "content-type": "application/json", accept: "application/json" },
						signal: controller.signal,
						body: "{}"
					});
					const payload = await response.json();
					if (!response.ok || payload.ok !== true) throw new Error(payload.error ?? `HTTP ${response.status}`);
					if (!isCurrent(generation, controller)) return null;
					setSerial({ kind: "done", payload });
					void checkLiveness();
					await load(true, selectedRef.current);
					return payload;
				} catch (error) {
					if (error?.name === "AbortError" || !isCurrent(generation, controller)) {
						setSerial((previous) => previous.kind === "running" ? { kind: "cancelled" } : previous);
						return null;
					}
					setSerial({ kind: "failed", message: String(error?.message ?? error) });
					return null;
				} finally {
					if (serialController.current === controller) serialController.current = null;
				}
			}, [abortAll, checkLiveness, load]);

			const cancelSerial = react.useCallback(() => {
				viewGeneration.current += 1;
				serialController.current?.abort();
				setSerial((previous) => previous.kind === "running" ? { kind: "cancelled" } : previous);
			}, []);

			const runBind = react.useCallback(async () => {
				const discoveredTarget = selectedRef.current;
				if (discoveredTarget === "" || bindingConfig === "") return null;
				setBinding({ kind: "running" });
				try {
					const response = await fetch(BIND_URL, {
						method: "POST",
						headers: { "content-type": "application/json", accept: "application/json" },
						body: JSON.stringify({ configuredTarget: bindingConfig, discoveredTarget })
					});
					const payload = await response.json();
					if (!response.ok || payload.ok !== true) throw new Error(payload.error ?? `HTTP ${response.status}`);
					setBinding({ kind: "done", payload });
					void checkLiveness();
					await load(true, bindingConfig);
					return payload;
				} catch (error) {
					setBinding({ kind: "failed", message: String(error?.message ?? error) });
					return null;
				}
			}, [bindingConfig, checkLiveness, load]);

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
						{ className: "ad-current-group" },
						h("span", { className: "ad-action-label" }, "当前开发板"),
						h(
							"div",
							{ className: "ad-current-line" },
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
												boardMacs(board).includes(current.mac) ||
												current.discoveredId === board.id ||
												current.id === board.configuredId
										);
										const liveBoard = payload.board
											? {
													...current,
													...payload.board,
													model: factValue(payload, "boardModel") ?? current.label
												}
											: null;
										return currentBoard ? boardName(currentBoard) : liveBoard ? boardName(liveBoard) : targetName(current);
									})()
								),
								h(
									"code",
									null,
									(() => {
										const current = payload?.targets?.find((target) => target.id === selected);
										return current ? `${current.user ?? "HwHiAiUser"}@${current.host ?? "未配置地址"}` : "";
									})()
								),
								h("span", { className: "ad-state-badge" }, dataState(payload, state.kind)),
								(() => {
									const onlineNow = liveness.byTarget?.[selected];
									const text = liveness.kind === "checking"
										? "检查连接中"
										: onlineNow === true
											? "SSH 在线"
											: onlineNow === false
												? "SSH 离线"
												: "在线未检测";
									return h(
										"span",
										{
											className: onlineNow === true ? "ad-status-ok" : "ad-note",
											title: liveness.checkedAt ? `最近检测：${stamp(liveness.checkedAt)}` : "尚未完成在线检测"
										},
										text
									);
								})()
							),
							h(
								"div",
								{ className: "ad-current-actions" },
								h(
									"button",
									{
										className: "ad-button ad-primary",
										type: "button",
										disabled: liveness.kind === "checking",
										title: "只检查开发板 SSH 连接，不读取任何设备信息",
										onClick: () => checkLiveness()
									},
									liveness.kind === "checking" ? "检查中…" : "检查在线"
								)
							)
						)
					),
					h(
						"div",
						{ className: "ad-discovery-group" },
						h("span", { className: "ad-action-label" }, "发现开发板"),
						h(
							"div",
							{ className: "ad-discovery-actions" },
							serial.kind === "running"
								? h("button", { className: "ad-button", type: "button", onClick: cancelSerial }, "取消串口发现")
								: h(
										"button",
										{
											className: "ad-button",
											type: "button",
											disabled: busy,
											title: "通过串口登录，读取板卡网络/MAC，并自动尝试 SSH 探测后写入设备列表",
											onClick: runSerial
										},
										"串口发现"
									),
							discovery.kind === "running"
								? h("button", { className: "ad-button", type: "button", onClick: cancelDiscovery }, "取消扫描")
								: h(
										"button",
										{
											className: "ad-button",
											type: "button",
											disabled: busy,
											title: "默认扫描当前活动物理网卡；配置 discovery.cidrs 时才固定覆盖范围，按 MAC 更新开发板记录和 target 列表",
											onClick: runDiscovery
										},
										"局域网扫描"
									)
						)
					)
				);

			if (state.kind === "idle") {
				return h("div", { className: "ad-root" }, head(state.payload, false), h("div", { className: "ad-note", role: "status", "aria-live": "polite" }, state.message ?? "等待操作。"));
			}
			if (state.kind === "loading" && !state.payload) {
				return h("div", { className: "ad-root" }, head(state.payload, true), h("div", { className: "ad-empty", role: "status", "aria-live": "polite" }, "正在读取插件配置…"));
			}
			if (state.kind === "failed" && !state.payload) {
				return h(
					"div",
					{ className: "ad-root" },
					head(state.payload, false),
					h("div", { className: "ad-error", role: "alert", "aria-live": "assertive" }, `读取失败：${state.message}`),
					state.payload?.targets ? h("div", { className: "ad-section" }, h("h3", null, "开发板"), h(Targets, { targets: state.payload.targets })) : null
				);
			}

			const payload = state.payload;
			const hasProbe = payload.probedAt !== null && payload.probedAt !== undefined
				? true
				: payload.board !== null && payload.board !== undefined
					? true
					: payload.transport !== null && payload.transport !== undefined;
			const board = payload.board ?? {};
			const pairing = payload.pairing ?? {};
			const facts = payload.facts ?? {};
			const scope = payload.scope ?? null;
			const peripherals = peripheralInventory(payload);
			const categoryUpdated = (...categories) => {
				const stamps = categories
					.map((category) => payload.categoryUpdatedAt?.[category])
					.filter((value) => typeof value === "string" && value !== "")
					.sort();
				return stamps.length > 0 ? stamp(stamps[stamps.length - 1]) : "更新时间未记录";
			};
			const memoryUsed = board.memoryUsedMb ?? facts.memoryUsedMb?.value;
			const memoryTotal = board.memoryTotalMb ?? facts.memoryTotalMb?.value;
			const memoryValue =
				memoryUsed !== null && memoryUsed !== undefined && Number.isFinite(Number(memoryUsed))
					? memoryTotal !== null && memoryTotal !== undefined && Number.isFinite(Number(memoryTotal))
						? `${memoryGb(memoryUsed)} / ${memoryGb(memoryTotal)}`
						: `${memoryGb(memoryUsed)}（总量未采集）`
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

			const systemNotice = scope !== null && scope.applicable === false
				? { error: true, text: scope.message }
				: (payload.missingCore ?? []).length > 0
					? { error: false, text: `未采集：${payload.missingCore.join("、")}` }
					: scope !== null && (scope.applicable === true || payload.probedAt)
						? { error: false, text: scope.message }
						: { error: false, text: "" };
			const overview = h(
				"div",
				{ className: "ad-overview-page" },
				h(
					"div",
					{ className: "ad-asset-head" },
					h("h3", null, "系统信息"),
					h(
						"button",
						{
							className: "ad-button ad-refresh-button",
							type: "button",
							disabled: systemRefresh.phase === "running" || state.kind === "loading",
							onClick: refreshSystem
						},
						systemRefresh.phase === "running" ? `更新中 ${systemRefresh.elapsedSec}s…` : "更新系统信息"
					)
				),
				h("div", { className: "ad-inline-status", role: "status", title: systemRefresh.message ?? "" }, systemRefresh.message ?? ""),
				h(
					"div",
					{
						className: systemNotice.error ? "ad-system-notice ad-error" : "ad-system-notice",
						title: systemNotice.text,
						role: "status"
					},
					systemNotice.text
				),
				h(
					"div",
					null,
					!hasProbe
						? h("div", { className: "ad-system-hint" }, "尚未获取系统信息。点击右上方“更新系统信息”只读取硬件、OS、NPU、CANN、驱动和 ATC。")
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
						h(Card, { label: "摄像头", value: peripherals.cameras.length > 0 ? `${peripherals.cameras.length} 个节点` : null, source: `V4L2 · ${categoryUpdated("camera")}` }),
						h(Card, { label: "音频设备", value: peripherals.playback.length + peripherals.capture.length + peripherals.sinks.length > 0 ? `${peripherals.playback.length + peripherals.capture.length + peripherals.sinks.length} 项` : null, source: `ALSA/Pulse · ${categoryUpdated("audio")}` }),
						h(Card, { label: "USB 外设", value: peripherals.usb.length > 0 ? `${peripherals.usb.length} 个` : null, source: `USB · ${categoryUpdated("camera", "audio")}` })
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
				)
			);

			const assets = (() => {
				const selectedTarget = (payload.targets ?? []).find((target) => target.id === selected);
				const selectedBoard = (payload.boards ?? []).find(
					(board) =>
						selectedTarget !== undefined &&
						(board.id === selectedTarget.discoveredId ||
							board.configuredId === selectedTarget.id ||
							boardMacs(board).includes(selectedTarget.mac))
				);
				return h(
					"div",
					{ className: "ad-section" },
					h(
						"div",
						{ className: "ad-asset-head" },
						h("h3", null, "当前开发板记录"),
						h("span", { className: "ad-note" }, "左侧列表负责选择开发板；这里只显示当前板的身份、硬件和变化，不重复列出全部历史板")
					),
					selectedBoard
						? h(BoardDetail, {
								board: selectedBoard,
								target: selectedTarget,
								liveness,
								configuredTargets: (payload.targets ?? []).filter((candidate) => candidate.isConfigured === true),
								bindingConfig,
								onBindingConfig: setBindingConfig,
								onBind: runBind,
								binding
							})
						: h(Targets, { targets: payload.targets })
				);
			})();

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
						"aria-label": "按症状调试查询",
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
							"aria-label": "案例系统程序",
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
				h(
					"div",
					{ className: "ad-note" },
					`仓库：${payload.repoRoot ?? "未记录"} · 来源：${payload.source ?? "未探测"}${payload.probedAt ? ` · ${stamp(payload.probedAt)}` : ""}`
				),
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

			const groupsById = new Map(peripherals.groups.map((group) => [group.id, group]));
			const groupGrid = (ids) =>
				h(
					"div",
					{ className: "ad-peripheral-grid" },
					ids.map((id) => groupsById.get(id)).filter(Boolean).map((group) => h(PeripheralGroup, { key: group.id, group }))
				);
			const peripheralInventoryState = peripheralInventoryFromPayload(payload);
			const cameraPage = h(
				"div",
				null,
				h(
					"div",
					{ className: "ad-section" },
					h(
						"div",
						{ className: "ad-asset-head" },
						h("h3", null, "摄像头采集测试"),
						h("span", { className: "ad-note" }, `先选择 V4L2 节点并读取一帧 · 摄像头数据：${categoryUpdated("camera")}`)
					),
					h(PeripheralTester, {
						mode: "camera",
						modes: [["camera", "摄像头"]],
						targetId: selected,
						onMode: selectPeripheralMode,
						form: peripheralForm,
						state: peripheral,
						inventory: peripheralInventoryState,
						refreshing: cameraRefresh.phase === "running",
						refreshDisabled: state.kind === "loading",
						refreshLabel: "更新摄像头信息",
						refreshElapsedSec: cameraRefresh.elapsedSec,
						refreshMessage: cameraRefresh.message,
						refreshFailed: cameraRefresh.phase === "failed",
						onField: (field, value) => updatePeripheralField("camera", field, value),
						onResolution: updateCameraResolution,
						onRefresh: refreshCamera,
						onRun: runPeripheralTest,
						onConfirm: setSpeakerConfirmed,
						confirmed: speakerConfirmed,
						targetOnline: liveness.byTarget?.[selected]
					})
				),
				h(
					"div",
					{ className: "ad-section" },
					h("div", { className: "ad-asset-head" }, h("h3", null, "当前节点支持的全部格式"), h("span", { className: "ad-note" }, "直接来自 v4l2-ctl --list-formats-ext")),
					h(CameraFormats, { inventory: peripheralInventoryState, device: peripheralForm.camera.device })
				),
				h(
					"div",
					{ className: "ad-section" },
					h("div", { className: "ad-asset-head" }, h("h3", null, "摄像头与 USB 枚举"), h("span", { className: "ad-note" }, "设备列表不代表已经成功读帧")),
					groupGrid(["camera", "usb"])
				)
			);
			const audioPage = h(
				"div",
				null,
				h(
					"div",
					{ className: "ad-section" },
					h(
						"div",
						{ className: "ad-asset-head" },
						h("h3", null, "音频接口检测"),
						h("span", { className: "ad-note" }, `麦克风看录音电平，扬声器看人工听音 · 音频数据：${categoryUpdated("audio")}`)
					),
					h(AudioTester, {
						forms: peripheralForm,
						state: peripheral,
						inventory: peripheralInventoryState,
						lastMicrophone: lastMicrophoneResult,
						refreshing: audioRefresh.phase === "running",
						refreshDisabled: state.kind === "loading",
						refreshElapsedSec: audioRefresh.elapsedSec,
						refreshMessage: audioRefresh.message,
						refreshFailed: audioRefresh.phase === "failed",
						onField: (mode, field, value) => updatePeripheralField(mode, field, value),
						onRefresh: refreshAudio,
						onRun: runPeripheralTest,
						onConfirm: setSpeakerConfirmed,
						confirmed: speakerConfirmed,
						recordingPlayback,
						playbackConfirmed,
						onPlayRecording: runRecordingPlayback,
						onPlaybackConfirm: setPlaybackConfirmed,
						targetOnline: liveness.byTarget?.[selected]
					})
				),
				h(
					"div",
					{ className: "ad-section" },
					h("div", { className: "ad-asset-head" }, h("h3", null, "音频接口枚举"), h("span", { className: "ad-note" }, "ALSA、PulseAudio/PipeWire、蓝牙连接详情")),
					groupGrid(["playback", "capture", "audio-source", "audio-sink", "pulse-card", "bluetooth", "bluetooth-info"]),
					h("div", { className: "ad-note", style: { marginTop: "10px" } }, "设备能被列出不等于已经验证可读写；请分别执行麦克风录音和扬声器播放。")
				)
			);

			const globalStatusItems = [];
			if (state.staleError) globalStatusItems.push({ error: true, text: `当前显示的是最近一次成功快照：${state.staleError}` });
			if (liveness.kind === "failed") globalStatusItems.push({ error: true, text: `在线检查失败：${liveness.message}` });
			if (serial.kind === "failed") globalStatusItems.push({ error: true, text: `串口探测失败：${serial.message}` });
			if (serial.kind === "cancelled") globalStatusItems.push({ error: false, text: "串口发现已取消。" });
			if (serial.kind === "done") globalStatusItems.push({ error: false, text: `串口：${serial.payload.port} · ${serial.payload.hostname ?? "主机名未采集"} · ${serial.payload.ipAddress ?? "IP 未采集"} · ${serial.payload.macAddress ?? "MAC 未采集"} · 已写入 ${(serial.payload.found ?? []).length} 块` });
			if (discovery.kind === "failed") globalStatusItems.push({ error: true, text: `发现失败：${discovery.message}` });
			if (discovery.kind === "cancelled") globalStatusItems.push({ error: false, text: "局域网扫描已取消。" });
			if (discovery.kind === "done") globalStatusItems.push({ error: false, text: `发现完成：${discovery.payload.found?.length ?? 0} 块开发板；扫描 ${(discovery.payload.cidrs ?? []).join("、") || "未记录网段"}；SSH 开放 ${discovery.payload.open ?? 0} 个；记录文件 ${discovery.payload.inventoryPath ?? ""}` });
			if (payload.transport && payload.transport.ok !== true) globalStatusItems.push({ error: true, text: `探测未完成：${payload.transport.reason}` });
			const globalStatus = {
				error: globalStatusItems.some((item) => item.error),
				text: globalStatusItems.map((item) => item.text).join(" · ")
			};
			return h(
				"div",
				{ className: "ad-root" },
				head(payload, state.kind === "loading"),
				globalStatus.text === ""
					? h("div", { className: "ad-global-status", role: "status", "aria-live": "polite" })
					: h(
							"details",
							{ className: globalStatus.error ? "ad-global-status ad-error" : "ad-global-status", role: "alert" },
							h("summary", null, globalStatus.text),
							h("div", { className: "ad-global-detail" }, globalStatusItems.map((item, index) => h("div", { key: index }, item.text)))
						),
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
							liveness,
							selected,
							query: boardQuery,
							onSelect: selectBoard
						})
					),
					h(
						"main",
						{ className: "ad-main" },
						h(TabBar, { active: tab, onChange: setTab }),
						h(
							"div",
							{
								className: "ad-toolbar-note",
								role: "tabpanel",
								id: `ad-panel-${tab}`,
								"aria-labelledby": `ad-tab-${tab}`,
								tabIndex: 0
							},
							tab === "overview" ? overview : tab === "camera" ? cameraPage : tab === "audio" ? audioPage : tab === "assets" ? assets : tab === "debug" ? debug : raw
						)
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
