"""Waveshare 7.3-inch E (Spectra 6) e-Paper output for the smart album.

The image preparation path is deliberately hardware independent.  It can be
used on the workstation to generate and inspect the exact 800x480, six-color
frame that will be sent to the panel.  The board path uses ``python-periphery``
for Linux SPI/GPIO, so it does not assume Raspberry Pi BCM numbering.

The command/register values in :class:`EpaperDisplay` follow Waveshare's
``epd7in3e`` reference driver. The reference driver is MIT licensed; this
module keeps the transport and board pin mapping separate from that protocol.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from PIL import Image

from display_policy import orient_image

from config import (
    EPAPER_BACKEND,
    EPAPER_BUSY_LINE,
    EPAPER_BUSY_TIMEOUT_S,
    EPAPER_DC_LINE,
    EPAPER_FRAME_PATH,
    EPAPER_GPIOCHIP,
    EPAPER_HEIGHT,
    EPAPER_PREVIEW_PATH,
    EPAPER_PWR_LINE,
    EPAPER_RST_LINE,
    EPAPER_SPI_DEVICE,
    EPAPER_SPI_HZ,
    EPAPER_WIDTH,
)


ImageSource = Union[str, os.PathLike, Image.Image]

# The E panel uses six visible colors in a sparse 4-bit code space. Index 4 is
# the official reserved black entry and is retained so the remaining hardware
# values line up exactly with Waveshare's epd7in3e reference driver.
EPAPER_PALETTE = (
    (0, 0, 0),        # black, 0x0
    (255, 255, 255),  # white, 0x1
    (255, 255, 0),    # yellow, 0x2
    (255, 0, 0),      # red, 0x3
    (0, 0, 0),        # reserved black, 0x4
    (0, 0, 255),      # blue, 0x5
    (0, 255, 0),      # green, 0x6
)


class EpaperError(RuntimeError):
    """Raised for invalid frames or unavailable e-Paper hardware."""


@dataclass(frozen=True)
class EpaperConfig:
    """Board transport settings.

    ``dc_line`` and the other GPIO values are Linux gpiochip line offsets,
    not physical header pin numbers.  Resolve them from the AIpro pin table or
    device-tree overlay on the target board before enabling the hardware
    backend.
    """

    backend: str = EPAPER_BACKEND
    spi_device: str = EPAPER_SPI_DEVICE
    gpiochip: str = EPAPER_GPIOCHIP
    dc_line: Optional[int] = None
    rst_line: Optional[int] = None
    busy_line: Optional[int] = None
    pwr_line: Optional[int] = None
    spi_hz: int = EPAPER_SPI_HZ
    busy_timeout_s: float = EPAPER_BUSY_TIMEOUT_S

    @classmethod
    def from_environment(cls) -> "EpaperConfig":
        def line(name: str, value: Optional[str]) -> Optional[int]:
            if value in (None, ""):
                return None
            try:
                return int(value)
            except ValueError as exc:
                raise EpaperError(f"{name} must be an integer line offset") from exc

        return cls(
            backend=os.environ.get("SMART_ALBUM_EPAPER_BACKEND", EPAPER_BACKEND),
            spi_device=os.environ.get("SMART_ALBUM_EPAPER_SPI", EPAPER_SPI_DEVICE),
            gpiochip=os.environ.get("SMART_ALBUM_EPAPER_GPIOCHIP", EPAPER_GPIOCHIP),
            dc_line=line("SMART_ALBUM_EPAPER_DC_LINE", os.environ.get("SMART_ALBUM_EPAPER_DC_LINE", EPAPER_DC_LINE)),
            rst_line=line("SMART_ALBUM_EPAPER_RST_LINE", os.environ.get("SMART_ALBUM_EPAPER_RST_LINE", EPAPER_RST_LINE)),
            busy_line=line("SMART_ALBUM_EPAPER_BUSY_LINE", os.environ.get("SMART_ALBUM_EPAPER_BUSY_LINE", EPAPER_BUSY_LINE)),
            pwr_line=line("SMART_ALBUM_EPAPER_PWR_LINE", os.environ.get("SMART_ALBUM_EPAPER_PWR_LINE", EPAPER_PWR_LINE)),
            spi_hz=int(os.environ.get("SMART_ALBUM_EPAPER_SPI_HZ", str(EPAPER_SPI_HZ))),
            busy_timeout_s=float(os.environ.get("SMART_ALBUM_EPAPER_BUSY_TIMEOUT_S", str(EPAPER_BUSY_TIMEOUT_S))),
        )


@dataclass(frozen=True)
class EpaperFrame:
    """Prepared display frame and its quantized preview."""

    image: Image.Image
    packed: bytes


@dataclass(frozen=True)
class DisplayResult:
    """Evidence returned by a display operation."""

    backend: str
    width: int
    height: int
    frame_bytes: int
    preview_path: Optional[str]
    frame_path: Optional[str]


def _palette_image() -> Image.Image:
    palette = [channel for color in EPAPER_PALETTE for channel in color]
    palette.extend([0] * (256 * 3 - len(palette)))
    image = Image.new("P", (1, 1))
    image.putpalette(palette)
    return image


def crop_to_panel(
    image: Image.Image,
    size=(EPAPER_WIDTH, EPAPER_HEIGHT),
    *,
    orientation_mode: str = "auto",
    rotation: int = 0,
    target_orientation: Optional[str] = None,
) -> Image.Image:
    """Orient, resize while preserving aspect ratio, then center-crop."""

    image = orient_image(
        image,
        size,
        mode=orientation_mode,
        rotation=rotation,
        target_orientation=target_orientation,
    )
    target_width, target_height = size
    if image.width <= 0 or image.height <= 0:
        raise EpaperError("source image has no pixels")
    scale = max(target_width / image.width, target_height / image.height)
    resampling = getattr(Image, "Resampling", Image)
    resized = image.resize(
        (max(target_width, round(image.width * scale)), max(target_height, round(image.height * scale))),
        resampling.LANCZOS,
    )
    left = (resized.width - target_width) // 2
    top = (resized.height - target_height) // 2
    return resized.crop((left, top, left + target_width, top + target_height))


def _load_image(source: ImageSource) -> Image.Image:
    if isinstance(source, Image.Image):
        return source.copy()
    path = Path(source)
    if not path.is_file():
        raise EpaperError(f"image does not exist: {path}")
    with Image.open(path) as image:
        return image.copy()


def pack_palette_indices(indices: bytes, width=EPAPER_WIDTH, height=EPAPER_HEIGHT) -> bytes:
    """Pack two 4-bit palette indexes into each byte, high nibble first."""

    expected = width * height
    if len(indices) != expected:
        raise EpaperError(f"expected {expected} palette indexes, got {len(indices)}")
    if expected % 2:
        raise EpaperError("panel pixel count must be even for 4-bit packing")
    return bytes(
        (indices[offset] << 4) | indices[offset + 1]
        for offset in range(0, expected, 2)
    )


def prepare_frame(
    source: ImageSource,
    dither=True,
    *,
    orientation_mode: str = "auto",
    rotation: int = 0,
    target_orientation: Optional[str] = None,
) -> EpaperFrame:
    """Prepare a source image for the E6 panel."""

    image = crop_to_panel(
        _load_image(source),
        orientation_mode=orientation_mode,
        rotation=rotation,
        target_orientation=target_orientation,
    )
    dither_enum = getattr(Image, "Dither", Image)
    dither_mode = dither_enum.FLOYDSTEINBERG if dither else dither_enum.NONE
    quantized = image.quantize(palette=_palette_image(), dither=dither_mode)
    indices = quantized.tobytes()
    return EpaperFrame(image=quantized.convert("RGB"), packed=pack_palette_indices(indices))


def save_frame(frame: EpaperFrame, preview_path=EPAPER_PREVIEW_PATH, frame_path=EPAPER_FRAME_PATH):
    """Persist dry-run evidence without touching the display hardware."""

    preview = Path(preview_path) if preview_path else None
    packed = Path(frame_path) if frame_path else None
    if preview:
        preview.parent.mkdir(parents=True, exist_ok=True)
        frame.image.save(preview, format="PNG")
    if packed:
        packed.parent.mkdir(parents=True, exist_ok=True)
        packed.write_bytes(frame.packed)
    return (str(preview) if preview else None, str(packed) if packed else None)


class _PeripheryTransport:
    """Small adapter around python-periphery's SPI and GPIO objects."""

    def __init__(self, config: EpaperConfig):
        if None in (config.dc_line, config.rst_line, config.busy_line):
            raise EpaperError(
                "hardware backend requires DC, RST and BUSY gpiochip line offsets"
            )
        try:
            from periphery import GPIO, SPI
        except ImportError as exc:
            raise EpaperError(
                "hardware backend requires python-periphery; install it on the AIpro board"
            ) from exc

        self._GPIO = GPIO
        self.spi = SPI(config.spi_device, 0, config.spi_hz)
        self.dc = GPIO(config.gpiochip, config.dc_line, "out")
        self.rst = GPIO(config.gpiochip, config.rst_line, "out")
        self.busy = GPIO(config.gpiochip, config.busy_line, "in")
        self.pwr = GPIO(config.gpiochip, config.pwr_line, "out") if config.pwr_line is not None else None

    def write(self, data: bytes):
        self.spi.transfer(list(data))

    def close(self):
        for gpio in (self.pwr, self.busy, self.rst, self.dc):
            if gpio is not None:
                gpio.close()
        self.spi.close()


class EpaperDisplay:
    """Render and optionally refresh a Waveshare 7.3-inch E panel."""

    def __init__(self, config: Optional[EpaperConfig] = None):
        self.config = config or EpaperConfig.from_environment()
        self._transport = None

    def _command(self, command: int):
        self._transport.dc.write(False)
        self._transport.write(bytes([command]))

    def _data(self, data):
        self._transport.dc.write(True)
        self._transport.write(bytes(data))

    def _wait_idle(self):
        deadline = time.monotonic() + self.config.busy_timeout_s
        while not self._transport.busy.read():
            if time.monotonic() >= deadline:
                raise EpaperError("e-Paper BUSY timeout; check wiring and panel power")
            time.sleep(0.005)

    def _reset(self):
        self._transport.rst.write(True)
        time.sleep(0.020)
        self._transport.rst.write(False)
        time.sleep(0.002)
        self._transport.rst.write(True)
        time.sleep(0.020)

    def _init_panel(self):
        self._reset()
        self._wait_idle()
        time.sleep(0.030)
        for command, data in (
            (0xAA, [0x49, 0x55, 0x20, 0x08, 0x09, 0x18]),
            (0x01, [0x3F]),
            (0x00, [0x5F, 0x69]),
            (0x03, [0x00, 0x54, 0x00, 0x44]),
            (0x05, [0x40, 0x1F, 0x1F, 0x2C]),
            (0x06, [0x6F, 0x1F, 0x17, 0x49]),
            (0x08, [0x6F, 0x1F, 0x1F, 0x22]),
            (0x30, [0x03]),
            (0x50, [0x3F]),
            (0x60, [0x02, 0x00]),
            (0x61, [0x03, 0x20, 0x01, 0xE0]),
            (0x84, [0x01]),
            (0xE3, [0x2F]),
        ):
            self._command(command)
            self._data(data)
        self._command(0x04)
        self._wait_idle()

    def _refresh(self, packed: bytes):
        self._command(0x10)
        self._data(packed)
        self._command(0x04)
        self._wait_idle()
        self._command(0x12)
        self._data([0x00])
        self._wait_idle()
        self._command(0x02)
        self._data([0x00])
        self._wait_idle()

    def _sleep(self):
        self._command(0x07)
        self._data([0xA5])
        time.sleep(2.0)

    def show(
        self,
        source: ImageSource,
        preview_path=EPAPER_PREVIEW_PATH,
        frame_path=EPAPER_FRAME_PATH,
        dither=True,
        *,
        orientation_mode: str = "auto",
        rotation: int = 0,
        target_orientation: Optional[str] = None,
    ) -> DisplayResult:
        frame = prepare_frame(
            source,
            dither=dither,
            orientation_mode=orientation_mode,
            rotation=rotation,
            target_orientation=target_orientation,
        )
        saved_preview, saved_frame = save_frame(frame, preview_path, frame_path)
        backend = self.config.backend.lower().strip()
        if backend in ("dry-run", "dryrun", "preview"):
            return DisplayResult(backend="dry-run", width=EPAPER_WIDTH, height=EPAPER_HEIGHT, frame_bytes=len(frame.packed), preview_path=saved_preview, frame_path=saved_frame)
        if backend not in ("periphery", "orangepi", "waveshare"):
            raise EpaperError(f"unsupported e-Paper backend: {self.config.backend}")

        self._transport = _PeripheryTransport(self.config)
        try:
            if self._transport.pwr is not None:
                self._transport.pwr.write(True)
            self._init_panel()
            self._refresh(frame.packed)
            self._sleep()
        finally:
            self._transport.close()
            self._transport = None
        return DisplayResult(backend="periphery", width=EPAPER_WIDTH, height=EPAPER_HEIGHT, frame_bytes=len(frame.packed), preview_path=saved_preview, frame_path=saved_frame)


def show_image(source: ImageSource, **kwargs) -> DisplayResult:
    """Convenience wrapper used by the CLI and web UI callbacks."""

    return EpaperDisplay().show(source, **kwargs)
