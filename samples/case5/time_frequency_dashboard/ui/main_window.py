"""Top-level Qt shell for the mutually exclusive Hantek and RTL-SDR tools."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from PySide6.QtCore import QSettings, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTabWidget

from ..controller import Case5Controller
from ..instrument_coordinator import InstrumentCoordinator, InstrumentStartToken
from ..rtl_sdr_run_report import summarize_rtl_sdr_run
from ..rtl_sdr_service import (
    RtlSdrRunConfig,
    RtlSdrService,
    discover_accepted_models,
)
from .hantek_workspace import HantekWorkspace
from .sdr_controls import SdrModelOption, SdrUiRunRequest
from .sdr_workspace import SdrWorkspace
from .theme import stylesheet


class DashboardWindow(QMainWindow):
    """Keep presentation concerns in Qt and device ownership in the coordinator."""

    model_scan_finished = Signal(int, object, object)
    qc_finished = Signal(object, object)
    start_finished = Signal(object, object, object)

    SETTINGS_KEY = "case5/last_workspace"
    HANTEK_INDEX = 0
    SDR_INDEX = 1

    def __init__(
        self,
        controller: Case5Controller,
        sigrok_bridge: Path,
        allow_simulation: bool,
        *,
        rtl_sdr_service: RtlSdrService | None = None,
        coordinator: InstrumentCoordinator | None = None,
        sdr_models_dir: Path = Path("models/generated/inference"),
        sdr_output_root: Path = Path("data/rtl_sdr_npu_inference"),
        sdr_developer_sources: bool = False,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.sigrok_bridge = Path(sigrok_bridge)
        self.allow_simulation = bool(allow_simulation)
        self.rtl_sdr_service = rtl_sdr_service or RtlSdrService()
        self.coordinator = coordinator or InstrumentCoordinator(controller, self.rtl_sdr_service)
        self.sdr_models_dir = Path(sdr_models_dir)
        self.sdr_output_root = Path(sdr_output_root)
        self.sdr_developer_sources = bool(sdr_developer_sources)
        self._closed = False
        self._model_scan_generation = 0
        self._model_scan_in_flight = False
        self._settings = QSettings("Ascend310", "Case5")
        self.model_scan_finished.connect(self._apply_model_scan)
        self.qc_finished.connect(self._show_qc_result)
        self.start_finished.connect(self._show_start_result)

        self.setWindowTitle("Case 5 - Signal Analysis Dashboard")
        self.setMinimumSize(1280, 760)
        self.setStyleSheet(stylesheet())
        self._build_ui()
        self._restore_workspace()
        # Adding the first tab emits currentChanged(0).  Restore the saved
        # workspace before observing changes so construction cannot overwrite
        # a previously selected SDR tab with the Hantek default.
        self.workspace_tabs.currentChanged.connect(self._remember_workspace)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(50)
        # Selecting a workspace must never initialize an OM, open rtl_sdr, or
        # start sigrok.  Validation happens only inside the respective Start
        # handler, after the coordinator granted ownership.
        self.refresh()

    def _build_ui(self) -> None:
        self.workspace_tabs = QTabWidget(self)
        self.workspace_tabs.setObjectName("instrumentWorkspaceTabs")
        self.workspace_tabs.setDocumentMode(True)
        self.setCentralWidget(self.workspace_tabs)

        self.hantek_workspace = HantekWorkspace(
            self.controller,
            self.sigrok_bridge,
            self.allow_simulation,
            on_start=self._start_hantek,
            on_simulation=self._start_hantek_simulation,
            on_stop=self._request_stop,
        )
        self.sdr_workspace = SdrWorkspace(
            on_start=self._start_sdr,
            on_stop=self._request_stop,
            on_qc=self._run_sdr_qc,
        )
        self.sdr_workspace.set_developer_sources(self.sdr_developer_sources)
        # Discovery only parses manifests and rehashes artifacts.  Run it on a
        # worker so a directory with many candidates never stalls the Qt event
        # loop, and still keep start-time validation inside the service.
        self.sdr_workspace.set_models(())
        self.reload_sdr_models()
        self.workspace_tabs.addTab(self.hantek_workspace, "Hantek")
        self.workspace_tabs.addTab(self.sdr_workspace, "RTL-SDR")

    def _accepted_sdr_models(self) -> tuple[SdrModelOption, ...]:
        """Adapt the service admission result to the deliberately small UI type."""
        return tuple(
            SdrModelOption(
                model_id=model.model_id,
                manifest_path=model.manifest_path,
                task=model.task,
                input_shape=model.input_shape,
                sample_rate_hz=model.sample_rate_hz,
                display_name=(
                    f"{model.model_id} | OM P95 {model.npu_p95_ms:.2f} ms | "
                    f"CPU gain {model.npu_speedup_over_cpu:.2f}x"
                ),
            )
            for model in discover_accepted_models(self.sdr_models_dir)
        )

    def _restore_workspace(self) -> None:
        value = self._settings.value(self.SETTINGS_KEY, self.HANTEK_INDEX)
        try:
            index = int(value)
        except (TypeError, ValueError):
            index = self.HANTEK_INDEX
        if index not in (self.HANTEK_INDEX, self.SDR_INDEX):
            index = self.HANTEK_INDEX
        self.workspace_tabs.setCurrentIndex(index)

    def _remember_workspace(self, index: int) -> None:
        if index in (self.HANTEK_INDEX, self.SDR_INDEX):
            self._settings.setValue(self.SETTINGS_KEY, index)

    def _start_hantek(self, settings: dict[str, float]) -> None:
        start_settings = dict(settings)
        self._launch_start(
            "Hantek 6022BE",
            self.coordinator.reserve_hantek_start,
            lambda token: self.coordinator.start_hantek_reserved(
                token,
                self.sigrok_bridge,
                start_settings,
            ),
        )

    def _start_hantek_simulation(self) -> None:
        self._launch_start(
            "Hantek simulation",
            self.coordinator.reserve_hantek_start,
            lambda token: self.coordinator.start_hantek_reserved(
                token,
                self.sigrok_bridge,
                simulation=True,
            ),
        )

    def _start_sdr(self, request: SdrUiRunRequest) -> None:
        """Translate presentation values without bypassing service revalidation."""
        try:
            if request.source != "rtl" and not self.sdr_developer_sources:
                raise ValueError(
                    "CU8 replay and synthetic SDR sources require --sdr-developer-sources"
                )
            config = RtlSdrRunConfig(
                source=request.source,
                manifest_path=request.manifest_path,
                models_dir=self.sdr_models_dir,
                input_cu8=request.input_cu8,
                sample_rate_hz=request.sample_rate_hz,
                center_frequency_hz=request.center_frequency_hz,
                device=request.device,
                gain_db=request.gain_db,
                ppm_error=request.ppm_error,
                rf_input_context=request.rf_input_context,
                duration_seconds=request.duration_seconds,
                output_dir=self.sdr_output_root,
            )
            self._launch_start(
                "RTL-SDR",
                self.coordinator.reserve_rtl_sdr_start,
                lambda token: self.coordinator.start_rtl_sdr_reserved(token, config),
            )
        except Exception as exc:
            self._show_start_error("RTL-SDR", exc)

    def _launch_start(self, title: str, reserve: Any, start: Any) -> None:
        """Reserve ownership on Qt's thread, then run expensive startup off it."""
        try:
            token: InstrumentStartToken = reserve()
        except Exception as exc:
            self._show_start_error(title, exc)
            return
        self.refresh()

        def run_start() -> None:
            try:
                start(token)
            except Exception as exc:
                self.start_finished.emit(title, exc, token)
            else:
                self.start_finished.emit(title, None, token)

        worker = threading.Thread(
            target=run_start,
            name=f"case5-{token.source}-start",
            daemon=True,
        )
        try:
            worker.start()
        except Exception as exc:
            # Reservation happened before worker creation so the UI could
            # show Stop during preflight.  Revoke it explicitly if the Python
            # worker itself cannot launch.
            self.coordinator.cancel_reserved_start(token)
            self._show_start_error(title, exc)
            self.refresh()

    def _request_stop(self) -> None:
        self.coordinator.request_stop()
        self.refresh()

    def _run_sdr_qc(self) -> None:
        snapshot = self.rtl_sdr_service.snapshot()
        coordinator_snapshot = self.coordinator.snapshot()
        if (
            snapshot.result_path is None
            or snapshot.capture_path is None
            or snapshot.state.lower() != "idle"
            or snapshot.completion_status != "completed"
            or snapshot.source != "rtl"
            or coordinator_snapshot.active_source is not None
        ):
            QMessageBox.warning(
                self,
                "RTL-SDR QC",
                "QC requires a normally completed RTL-SDR run with CU8 and JSONL artifacts, and no active instrument.",
            )
            return
        self.sdr_workspace.controls.set_qc_available(False)

        def run_qc() -> None:
            try:
                summary = summarize_rtl_sdr_run(
                    snapshot.result_path, capture_path=snapshot.capture_path
                )
            except Exception as exc:
                self.qc_finished.emit(None, str(exc))
            else:
                self.qc_finished.emit(summary, None)

        worker = threading.Thread(target=run_qc, name="case5-rtl-sdr-qc", daemon=True)
        try:
            worker.start()
        except RuntimeError as exc:
            QMessageBox.warning(self, "RTL-SDR QC", f"Could not start QC worker: {exc}")
            self.refresh()

    def _show_qc_result(self, summary: object, error: object) -> None:
        if self._closed:
            return
        if error is not None:
            QMessageBox.warning(self, "RTL-SDR QC", str(error))
            self.refresh()
            return
        if not isinstance(summary, dict):
            QMessageBox.warning(self, "RTL-SDR QC", "QC did not return a summary")
            self.refresh()
            return
        timing = summary.get("timing", {}).get("end_to_end_ms", {})
        QMessageBox.information(
            self,
            "RTL-SDR QC",
            "Read-only QC passed. "
            f"Batches: {summary.get('batches', 0)}\n"
            f"End-to-end P95: {timing.get('p95_ms', '--')} ms\n"
            f"Capture: {summary.get('capture_path', '--')}",
        )
        self.refresh()

    def _show_start_error(self, title: str, exc: Exception) -> None:
        QMessageBox.warning(self, title, str(exc))

    def _show_start_result(
        self,
        title: object,
        error: object,
        token: object,
    ) -> None:
        if self._closed:
            return
        if isinstance(token, InstrumentStartToken):
            # A start worker can return after Stop and a subsequent generation
            # have already completed.  Its stale error must not describe the
            # currently selected instrument as failed.
            if self.coordinator.snapshot().generation != token.generation:
                self.refresh()
                return
        if error is not None:
            self._show_start_error(str(title), error)
        self.refresh()

    def refresh(self) -> None:
        coordinator_snapshot = self.coordinator.snapshot()
        active_source = coordinator_snapshot.active_source
        hantek_available = active_source in (None, "hantek")
        sdr_available = active_source in (None, "rtl_sdr")
        reason = (
            ""
            if active_source is None
            else f"{active_source} is {coordinator_snapshot.state.lower()}; stop it first"
        )
        self.hantek_workspace.set_available(hantek_available, reason)
        self.sdr_workspace.set_available(sdr_available, reason)
        self.hantek_workspace.render(
            self.controller.snapshot(),
            coordinator_state=coordinator_snapshot.state,
            coordinator_active=active_source == "hantek",
        )
        sdr_snapshot = self.rtl_sdr_service.snapshot()
        sdr_frame = None
        if self.workspace_tabs.currentIndex() == self.SDR_INDEX:
            sdr_frame = self.rtl_sdr_service.latest_frame()
        rendered_sdr_frame = self.sdr_workspace.render(
            sdr_snapshot,
            sdr_frame,
            coordinator_state=coordinator_snapshot.state,
            coordinator_active=active_source == "rtl_sdr",
            coordinator_message=coordinator_snapshot.message,
        )
        if rendered_sdr_frame and sdr_frame is not None:
            acknowledge = getattr(self.rtl_sdr_service, "acknowledge_display_frame", None)
            if callable(acknowledge):
                acknowledge(sdr_frame.generation, sdr_frame.sequence)

    def reload_sdr_models(self) -> None:
        """Refresh the reviewed-model list without opening an OM or RTL-SDR."""
        if self._closed or self._model_scan_in_flight:
            return
        self._model_scan_in_flight = True
        self._model_scan_generation += 1
        generation = self._model_scan_generation
        current = self.sdr_workspace.controls.selected_model
        selected_model_id = None if current is None else current.model_id

        def scan() -> None:
            try:
                models = self._accepted_sdr_models()
            except Exception:  # discovery excludes individual bad manifests
                models = ()
            self.model_scan_finished.emit(generation, models, selected_model_id)

        worker = threading.Thread(target=scan, name="case5-sdr-model-scan", daemon=True)
        try:
            worker.start()
        except RuntimeError as exc:
            if generation == self._model_scan_generation:
                self._model_scan_in_flight = False
                self.sdr_workspace.controls.model_info.setText(
                    f"模型扫描无法启动：{exc}"
                )

    def _apply_model_scan(
        self,
        generation: int,
        models: tuple[SdrModelOption, ...],
        selected_model_id: str | None,
    ) -> None:
        if self._closed or generation != self._model_scan_generation:
            return
        self._model_scan_in_flight = False
        self.sdr_workspace.set_models(models, selected_model_id)

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._closed:
            self._closed = True
            self.timer.stop()
            self.coordinator.close()
        event.accept()


def run_dashboard(
    controller: Case5Controller,
    sigrok_bridge: Path,
    allow_simulation: bool,
    *,
    rtl_sdr_service: RtlSdrService | None = None,
    coordinator: InstrumentCoordinator | None = None,
    sdr_models_dir: Path = Path("models/generated/inference"),
    sdr_output_root: Path = Path("data/rtl_sdr_npu_inference"),
    sdr_developer_sources: bool = False,
) -> int:
    application = QApplication.instance() or QApplication([])
    window = DashboardWindow(
        controller,
        sigrok_bridge,
        allow_simulation,
        rtl_sdr_service=rtl_sdr_service,
        coordinator=coordinator,
        sdr_models_dir=sdr_models_dir,
        sdr_output_root=sdr_output_root,
        sdr_developer_sources=sdr_developer_sources,
    )
    window.showMaximized()
    return application.exec()
