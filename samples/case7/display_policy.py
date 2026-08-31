"""PhotoFrame schedule validation and on-demand JPEG rendering."""

from __future__ import annotations

import io
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, ImageOps


class DisplayPolicyError(ValueError):
    pass


# ``auto`` is the safe default: normalize camera EXIF orientation and apply
# the explicit panel mounting rotation, while preserving the photo's intended
# portrait/landscape direction. ``match_display`` is opt-in because rotating
# every portrait photo to fill a landscape panel can make people appear
# sideways.
ORIENTATION_MODES = frozenset({"auto", "match_display"})
DISPLAY_ORIENTATIONS = frozenset({"landscape", "portrait", "square"})


def hint_jpeg_decode(image: Image.Image, target_size) -> None:
    """Give JPEG decoders a bounded intermediate-size hint when supported.

    ``Image.draft`` changes only how the current source is decoded; it does
    not write a thumbnail or any other persistent image artifact.  Formats
    without a draft path (including some MPO/PNG handlers) simply continue
    through the normal decoder.
    """

    try:
        width, height = int(target_size[0]), int(target_size[1])
        if width <= 0 or height <= 0:
            return
        draft = getattr(image, "draft", None)
        if callable(draft):
            draft("RGB", (max(1, width * 2), max(1, height * 2)))
    except (AttributeError, IndexError, TypeError, ValueError, OSError):
        return


def orientation_for_size(size) -> str:
    """Return the aspect orientation for a ``(width, height)`` pair."""

    try:
        width, height = int(size[0]), int(size[1])
    except (IndexError, TypeError, ValueError) as exc:
        raise DisplayPolicyError("display size must contain integer width and height") from exc
    if width <= 0 or height <= 0:
        raise DisplayPolicyError("display size must be positive")
    if width == height:
        return "square"
    return "landscape" if width > height else "portrait"


def validate_orientation_mode(value) -> str:
    mode = str(value or "auto").strip().lower()
    if mode not in ORIENTATION_MODES:
        raise DisplayPolicyError("orientation_mode must be auto or match_display")
    return mode


def normalize_display_orientation(value, size) -> str:
    """Validate an optional orientation hint, falling back to dimensions."""

    if value in (None, "", "auto"):
        return orientation_for_size(size)
    normalized = str(value).strip().lower()
    if normalized not in DISPLAY_ORIENTATIONS - {"square"}:
        raise DisplayPolicyError("display orientation must be landscape or portrait")
    return normalized


def orient_image(
    image: Image.Image,
    target_size,
    *,
    mode: str = "auto",
    rotation: int = 0,
    target_orientation: Optional[str] = None,
) -> Image.Image:
    """Normalize EXIF and orient one image for a display request.

    ``rotation`` describes the physical mounting angle (clockwise degrees).
    In ``match_display`` mode an additional quarter-turn is applied only when
    the source and target are opposite non-square orientations.  Matching is
    evaluated after the explicit rotation so this mode guarantees that the
    final encoded image has the requested display orientation. The returned
    image is detached from the caller's image object.
    """

    mode = validate_orientation_mode(mode)
    try:
        rotation = int(rotation)
    except (TypeError, ValueError) as exc:
        raise DisplayPolicyError("rotation must be 0, 90, 180, or 270") from exc
    if rotation not in {0, 90, 180, 270}:
        raise DisplayPolicyError("rotation must be 0, 90, 180, or 270")
    # ``square`` is an internal result of inferring a target from equal
    # dimensions.  Public device/query negotiation deliberately accepts only
    # landscape or portrait, but the renderer must still be able to process a
    # square touchscreen viewport returned by that inference.
    target = (
        "square"
        if str(target_orientation or "").strip().lower() == "square"
        else normalize_display_orientation(target_orientation, target_size)
    )
    # exif_transpose handles camera orientation tags 1-8 before any display
    # transformation. Converting to RGB also drops stale EXIF orientation data.
    result = ImageOps.exif_transpose(image).convert("RGB")
    if rotation:
        result = result.rotate(-rotation, expand=True)
    if mode == "match_display":
        source_orientation = orientation_for_size(result.size)
        if (
            target != "square"
            and source_orientation != "square"
            and source_orientation != target
        ):
            # Direction is deterministic; a physically reversed panel can use
            # rotation=270 in its device policy.
            result = result.rotate(-90, expand=True)
    return result


def _field(value: str, minimum: int, maximum: int) -> set[int]:
    if value == "*":
        return set(range(minimum, maximum + 1))
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            raise DisplayPolicyError("empty cron item")
        step = 1
        if "/" in item:
            item, step_text = item.split("/", 1)
            try:
                step = int(step_text)
            except ValueError as exc:
                raise DisplayPolicyError("cron step must be an integer") from exc
            if step <= 0:
                raise DisplayPolicyError("cron step must be positive")
        if item == "*":
            start, end = minimum, maximum
        elif "-" in item:
            parts = item.split("-", 1)
            try:
                start, end = int(parts[0]), int(parts[1])
            except ValueError as exc:
                raise DisplayPolicyError("cron range must be numeric") from exc
        else:
            try:
                start = end = int(item)
            except ValueError as exc:
                raise DisplayPolicyError("cron item must be numeric") from exc
        if start < minimum or end > maximum or start > end:
            raise DisplayPolicyError("cron value is outside its field range")
        result.update(range(start, end + 1, step))
    return result


def parse_cron(expression: str) -> tuple[set[int], set[int], set[int]]:
    parts = str(expression).split()
    if len(parts) != 3:
        raise DisplayPolicyError("rotation cron must have minute hour day-of-week fields")
    return _field(parts[0], 0, 59), _field(parts[1], 0, 23), _field(parts[2], 0, 7)


def validate_cron_rules(rules: Iterable[str]) -> list[str]:
    values = [str(rule).strip() for rule in rules]
    if not values or len(values) > 16:
        raise DisplayPolicyError("rotation_cron must contain 1 to 16 rules")
    for value in values:
        parse_cron(value)
    return values


def cron_slot(now: datetime, rules: Iterable[str]) -> Optional[str]:
    # Python Monday=0 while cron Sunday is 0/7.
    day = (now.weekday() + 1) % 7
    for rule in validate_cron_rules(rules):
        minutes, hours, days = parse_cron(rule)
        if now.minute in minutes and now.hour in hours and (day in days or (day == 0 and 7 in days)):
            return f"{now:%Y-%m-%d-%H-%M}:{rule}"
    return None


def effective_cron_slot(now: datetime, rules: Iterable[str], lookback_minutes: int = 7 * 24 * 60) -> Optional[str]:
    """Return the most recent matching minute at or before ``now``.

    PhotoFrame requests can arrive a little after the scheduled minute because
    of Wi-Fi wake-up or HTTP retry latency.  Keeping the exact ``cron_slot``
    helper unchanged preserves its validation contract, while this wrapper
    makes a delayed request belong to the latest effective rotation slot.
    """
    current = now.replace(second=0, microsecond=0)
    for offset in range(max(0, int(lookback_minutes)) + 1):
        candidate = current - timedelta(minutes=offset)
        value = cron_slot(candidate, rules)
        if value is not None:
            return value
    return None


# The e-paper endpoints intentionally use a slow default schedule.  A device
# may opt into the ten-minute cadence with ``rotation_cron=["*/10 * *"]``;
# local touchscreen rotation is governed by server_config.display instead.
DEFAULT_EINK_ROTATION_CRON = ["*/30 * *"]


DEFAULT_PHOTOFRAME_POLICY = {
    "auto_rotate": True,
    "rotation_cron": list(DEFAULT_EINK_ROTATION_CRON),
    # Smart selection remains the backwards-compatible default.  A playlist
    # is deliberately represented by photo IDs rather than file paths so a
    # device policy cannot escape the managed photo store.
    "selection_mode": "smart",
    "playlist_photo_ids": [],
    "repeat_window": 12,
    "crop_mode": "cover",
    "overlay_date": True,
    "overlay_weather": True,
    "jpeg_quality": 82,
    "max_bytes": 2 * 1024 * 1024,
    "orientation_mode": "auto",
    "rotation": 0,
    "width": 800,
    "height": 480,
    "policy_revision": 1,
}


def validate_policy(patch: Optional[dict] = None, base: Optional[dict] = None) -> dict:
    value = dict(DEFAULT_PHOTOFRAME_POLICY)
    value.update(base or {})
    value.update(patch or {})
    value["rotation_cron"] = validate_cron_rules(value["rotation_cron"])
    if not isinstance(value["selection_mode"], str) or value["selection_mode"] not in {"smart", "playlist"}:
        raise DisplayPolicyError("selection_mode must be smart or playlist")
    playlist = value["playlist_photo_ids"]
    if not isinstance(playlist, list):
        raise DisplayPolicyError("playlist_photo_ids must be a list")
    if len(playlist) > 1000:
        raise DisplayPolicyError("playlist_photo_ids must contain at most 1000 IDs")
    normalized_playlist = []
    seen = set()
    for photo_id in playlist:
        # bool is an int subclass, but is never a valid photo ID.
        if isinstance(photo_id, bool) or not isinstance(photo_id, int) or photo_id <= 0:
            raise DisplayPolicyError("playlist_photo_ids must contain positive integers")
        if photo_id in seen:
            raise DisplayPolicyError("playlist_photo_ids must not contain duplicate IDs")
        seen.add(photo_id)
        normalized_playlist.append(int(photo_id))
    value["playlist_photo_ids"] = normalized_playlist
    try:
        if isinstance(value["repeat_window"], bool):
            raise ValueError
        value["repeat_window"] = int(value["repeat_window"])
    except (TypeError, ValueError) as exc:
        raise DisplayPolicyError("repeat_window must be an integer") from exc
    if not 0 <= value["repeat_window"] <= 1000:
        raise DisplayPolicyError("repeat_window is outside its range")
    if value["crop_mode"] not in {"cover", "fit"}:
        raise DisplayPolicyError("crop_mode must be cover or fit")
    value["orientation_mode"] = validate_orientation_mode(value.get("orientation_mode", "auto"))
    for key in ("overlay_date", "overlay_weather", "auto_rotate"):
        if not isinstance(value[key], bool):
            raise DisplayPolicyError(f"{key} must be boolean")
    for key, low, high in (("jpeg_quality", 1, 100), ("max_bytes", 4096, 25 * 1024 * 1024), ("width", 1, 4096), ("height", 1, 4096)):
        try:
            value[key] = int(value[key])
        except (TypeError, ValueError) as exc:
            raise DisplayPolicyError(f"{key} must be an integer") from exc
        if not low <= value[key] <= high:
            raise DisplayPolicyError(f"{key} is outside its range")
    if int(value["rotation"]) not in {0, 90, 180, 270}:
        raise DisplayPolicyError("rotation must be 0, 90, 180, or 270")
    value["rotation"] = int(value["rotation"])
    value["policy_revision"] = int(value.get("policy_revision", 1))
    return value


class PhotoRenderer:
    """Serialize PIL work so a retry cannot multiply peak memory use."""

    def __init__(self):
        self._lock = threading.Lock()

    @staticmethod
    def _font(size: int):
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
        for candidate in candidates:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size)
        return ImageFont.load_default()

    def render(
        self,
        path: Path,
        policy: dict,
        timezone: str,
        weather_label: str = "",
        target_orientation: Optional[str] = None,
    ) -> tuple[bytes, int, int]:
        policy = validate_policy(policy)
        with self._lock, Image.open(path) as source:
            width, height = policy["width"], policy["height"]
            hint_jpeg_decode(source, (width, height))
            # ``width``/``height`` describe the encoded display frame.  The
            # mounting rotation changes the pixels inside that frame; it does
            # not swap the frame contract.  Swapping the target dimensions
            # here would apply a second quarter-turn in match_display mode.
            image = orient_image(
                source,
                (width, height),
                mode=policy["orientation_mode"],
                rotation=policy["rotation"],
                target_orientation=target_orientation,
            )
            if policy["crop_mode"] == "cover":
                scale = max(width / image.width, height / image.height)
                resized = image.resize((max(width, round(image.width * scale)), max(height, round(image.height * scale))), Image.Resampling.LANCZOS)
                left, top = (resized.width - width) // 2, (resized.height - height) // 2
                image = resized.crop((left, top, left + width, top + height))
            else:
                image.thumbnail((width, height), Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", (width, height), "white")
                canvas.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
                image = canvas
            lines = []
            if policy["overlay_date"]:
                try:
                    stamp = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                lines.append(stamp)
            if policy["overlay_weather"] and weather_label:
                lines.append(weather_label)
            if lines:
                draw = ImageDraw.Draw(image, "RGBA")
                font = self._font(max(14, width // 36))
                text = "  |  ".join(lines)
                try:
                    box = draw.textbbox((0, 0), text, font=font)
                except UnicodeEncodeError:
                    # A minimal board/controller image may have no CJK font.
                    # Keep the photo request usable rather than failing the
                    # whole render; full fonts retain the original labels.
                    text = text.encode("ascii", "replace").decode("ascii")
                    box = draw.textbbox((0, 0), text, font=font)
                padding = max(8, width // 100)
                draw.rounded_rectangle((padding, height - (box[3] - box[1]) - padding * 3, min(width - padding, box[2] + padding * 3), height - padding), radius=6, fill=(0, 0, 0, 145))
                draw.text((padding * 2, height - (box[3] - box[1]) - padding * 2), text, fill=(255, 255, 255, 235), font=font)
            quality = policy["jpeg_quality"]
            for _ in range(7):
                for candidate in range(quality, 13, -10):
                    output = io.BytesIO()
                    image.save(output, "JPEG", quality=candidate, optimize=False)
                    body = output.getvalue()
                    if len(body) <= policy["max_bytes"]:
                        return body, image.width, image.height
                image = image.resize((max(1, round(image.width * .8)), max(1, round(image.height * .8))), Image.Resampling.LANCZOS)
            raise DisplayPolicyError("rendered PhotoFrame image exceeds max_bytes")
