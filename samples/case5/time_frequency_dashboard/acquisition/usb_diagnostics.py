"""Non-invasive USB diagnostics for the Hantek 6022BE acquisition path.

The command only enumerates USB devices through PyUSB. It never opens an
interface, uploads firmware, claims an endpoint, or starts waveform capture.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from typing import Any, Iterable, List, Optional, Tuple


EXPECTED_USB_IDS: Tuple[Tuple[int, int, str], ...] = (
    (0x04B4, 0x6022, "Hantek 6022BE before volatile firmware"),
    (0x04B5, 0x6022, "Hantek 6022BE alternate vendor ID"),
    (0x1D50, 0x608E, "sigrok fx2lafw firmware state; safe for the sigrok bridge"),
)


@dataclass(frozen=True)
class UsbDeviceRecord:
    vendor_id: int
    product_id: int
    bus: Optional[int]
    address: Optional[int]
    manufacturer: str
    product: str
    expected_state: str
    device_node: Optional[str]
    node_readable: Optional[bool]
    node_writable: Optional[bool]

    def as_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["vid_pid"] = f"{self.vendor_id:04x}:{self.product_id:04x}"
        return value


@dataclass(frozen=True)
class UsbDiagnosticReport:
    pyusb_available: bool
    backend_available: Optional[bool]
    devices: Tuple[UsbDeviceRecord, ...]
    errors: Tuple[str, ...]

    @property
    def exit_code(self) -> int:
        if not self.pyusb_available:
            return 2
        if self.backend_available is False:
            return 3
        if self.devices and all(item.node_writable is False for item in self.devices):
            return 4
        return 0 if self.devices else 1

    def as_json(self) -> dict[str, Any]:
        return {
            "pyusb_available": self.pyusb_available,
            "backend_available": self.backend_available,
            "devices": [item.as_json() for item in self.devices],
            "errors": list(self.errors),
            "exit_code": self.exit_code,
        }


def _expected_state(vendor_id: int, product_id: int) -> str:
    for expected_vendor, expected_product, state in EXPECTED_USB_IDS:
        if (vendor_id, product_id) == (expected_vendor, expected_product):
            return state
    return "unclassified USB device"


def _get_string(usb_util: Any, device: Any, attribute: str) -> str:
    index = getattr(device, attribute, 0) or 0
    if not index:
        return "<not reported>"
    try:
        return str(usb_util.get_string(device, index) or "<empty>")
    except Exception as exc:
        return f"<unavailable: {type(exc).__name__}>"


def _device_node_access(
    bus: Optional[int], address: Optional[int]
) -> Tuple[Optional[str], Optional[bool], Optional[bool]]:
    if bus is None or address is None:
        return None, None, None
    node = f"/dev/bus/usb/{int(bus):03d}/{int(address):03d}"
    return node, os.access(node, os.R_OK), os.access(node, os.W_OK)


def summarize_devices(devices: Iterable[Any], usb_util: Any) -> Tuple[UsbDeviceRecord, ...]:
    """Convert PyUSB device objects to records without opening them."""
    records: List[UsbDeviceRecord] = []
    expected_pairs = {(vendor, product) for vendor, product, _ in EXPECTED_USB_IDS}
    for device in devices:
        vendor_id = int(getattr(device, "idVendor", -1))
        product_id = int(getattr(device, "idProduct", -1))
        if (vendor_id, product_id) not in expected_pairs:
            continue
        bus = getattr(device, "bus", None)
        address = getattr(device, "address", None)
        device_node, node_readable, node_writable = _device_node_access(bus, address)
        records.append(
            UsbDeviceRecord(
                vendor_id=vendor_id,
                product_id=product_id,
                bus=bus,
                address=address,
                manufacturer=_get_string(usb_util, device, "iManufacturer"),
                product=_get_string(usb_util, device, "iProduct"),
                expected_state=_expected_state(vendor_id, product_id),
                device_node=device_node,
                node_readable=node_readable,
                node_writable=node_writable,
            )
        )
    return tuple(records)


def run_diagnostics() -> UsbDiagnosticReport:
    """Run a read-only PyUSB enumeration and classify common failures."""
    try:
        import usb.core  # type: ignore
        import usb.util  # type: ignore
    except ImportError as exc:
        return UsbDiagnosticReport(
            pyusb_available=False,
            backend_available=None,
            devices=(),
            errors=(
                "PyUSB is not installed in the active Python environment. "
                "Run: python -m pip install -r requirements-board.txt",
                f"Import detail: {type(exc).__name__}: {exc}",
            ),
        )

    try:
        devices = list(usb.core.find(find_all=True) or [])
    except usb.core.NoBackendError as exc:
        return UsbDiagnosticReport(
            pyusb_available=True,
            backend_available=False,
            devices=(),
            errors=(
                "PyUSB is installed but no libusb backend is available. "
                "Check the system libusb runtime and restart the shell.",
                f"Backend detail: {type(exc).__name__}: {exc}",
            ),
        )
    except usb.core.USBError as exc:
        return UsbDiagnosticReport(
            pyusb_available=True,
            backend_available=True,
            devices=(),
            errors=(
                "USB enumeration failed. Check device permissions and whether "
                "PulseView/sigrok is using the scope.",
                f"USB detail: {type(exc).__name__}: {exc}",
            ),
        )
    except Exception as exc:
        return UsbDiagnosticReport(
            pyusb_available=True,
            backend_available=None,
            devices=(),
            errors=(f"Unexpected USB enumeration error: {type(exc).__name__}: {exc}",),
        )

    records = summarize_devices(devices, usb.util)
    errors: Tuple[str, ...] = ()
    if not records:
        errors = (
            "No Hantek 6022BE USB ID was found. Close PulseView/sigrok, "
            "unplug/replug the scope, and run this diagnostic again.",
            "Expected IDs: 04b4:6022 (before firmware), 04b5:6022 (alternate), "
            "1d50:608e (fx2lafw state).",
        )
    elif all(item.node_writable is False for item in records):
        nodes = ", ".join(item.device_node or "<unknown>" for item in records)
        errors = (
            f"USB device found but the current user cannot write its device node: {nodes}",
            "Install the documented 04b5:6022 udev rule, reload udev, and unplug/replug the scope.",
        )
    return UsbDiagnosticReport(True, True, records, errors)


def print_report(report: UsbDiagnosticReport, as_json_output: bool = False) -> None:
    if as_json_output:
        print(json.dumps(report.as_json(), indent=2, ensure_ascii=True))
        return
    print("Case 5 USB diagnostic (read-only; no capture started)")
    print(f"PyUSB: {'available' if report.pyusb_available else 'missing'}")
    if report.backend_available is not None:
        print(f"libusb backend: {'available' if report.backend_available else 'missing'}")
    for record in report.devices:
        print(
            "FOUND {vid:04x}:{pid:04x} bus={bus} address={address} "
            "manufacturer={manufacturer} product={product}".format(
                vid=record.vendor_id,
                pid=record.product_id,
                bus=record.bus if record.bus is not None else "?",
                address=record.address if record.address is not None else "?",
                manufacturer=record.manufacturer,
                product=record.product,
            )
        )
        print(f"  state: {record.expected_state}")
        if record.device_node is not None:
            print(
                f"  node: {record.device_node} "
                f"readable={record.node_readable} writable={record.node_writable}"
            )
    for error in report.errors:
        print(f"ERROR: {error}")
    print(f"Exit code: {report.exit_code}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_diagnostics()
    print_report(report, as_json_output=args.json_output)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
