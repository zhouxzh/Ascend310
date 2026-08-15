from types import SimpleNamespace

from time_frequency_dashboard.acquisition import usb_diagnostics
from time_frequency_dashboard.acquisition.usb_diagnostics import (
    UsbDiagnosticReport,
    UsbDeviceRecord,
    summarize_devices,
)


class FakeUsbUtil:
    @staticmethod
    def get_string(device, index):
        return {1: "Cypress", 2: "Hantek 6022BE"}[index]


def test_summarize_only_includes_expected_hantek_ids(monkeypatch):
    # Keep this unit test independent from a real Hantek that happens to use
    # the synthetic bus/address pair on a development board.
    monkeypatch.setattr(usb_diagnostics.os, "access", lambda *_args: False)
    devices = [
        SimpleNamespace(
            idVendor=0x04B4,
            idProduct=0x6022,
            bus=7,
            address=5,
            iManufacturer=1,
            iProduct=2,
        ),
        SimpleNamespace(
            idVendor=0x1234,
            idProduct=0x5678,
            bus=1,
            address=2,
            iManufacturer=1,
            iProduct=2,
        ),
    ]

    records = summarize_devices(devices, FakeUsbUtil())

    assert records == (
        UsbDeviceRecord(
            vendor_id=0x04B4,
            product_id=0x6022,
            bus=7,
            address=5,
            manufacturer="Cypress",
            product="Hantek 6022BE",
            expected_state="Hantek 6022BE before volatile firmware",
            device_node="/dev/bus/usb/007/005",
            node_readable=False,
            node_writable=False,
        ),
    )


def test_missing_device_report_is_actionable():
    report = UsbDiagnosticReport(
        pyusb_available=True,
        backend_available=True,
        devices=(),
        errors=("No Hantek 6022BE USB ID was found.",),
    )

    assert report.exit_code == 1
    assert report.as_json()["devices"] == []
    assert "No Hantek" in report.as_json()["errors"][0]


def test_permission_denied_report_has_distinct_exit_code():
    device = UsbDeviceRecord(
        vendor_id=0x04B5,
        product_id=0x6022,
        bus=7,
        address=7,
        manufacturer="<unavailable: ValueError>",
        product="<unavailable: ValueError>",
        expected_state="Hantek 6022BE alternate vendor ID",
        device_node="/dev/bus/usb/007/007",
        node_readable=True,
        node_writable=False,
    )
    report = UsbDiagnosticReport(True, True, (device,), ("permission denied",))

    assert report.exit_code == 4
