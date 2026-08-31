"""Date/weather aware selection built on the admitted CLIP index."""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from embedding_backend import CHINESE_CLIP_ID, EmbeddingError
from display_policy import DEFAULT_PHOTOFRAME_POLICY, effective_cron_slot, validate_policy


class WeatherClient:
    def fetch(self, config: dict) -> dict:
        if not config.get("enabled") or config.get("provider") == "disabled":
            return {"status": "disabled"}
        query = urllib.parse.urlencode({
            "latitude": config["latitude"],
            "longitude": config["longitude"],
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "timezone": "auto",
        })
        url = "https://api.open-meteo.com/v1/forecast?" + query
        request = urllib.request.Request(url, headers={"User-Agent": "ascend-smart-album/1.0"})
        with urllib.request.urlopen(request, timeout=float(config.get("timeout_seconds", 5))) as response:
            value = json.loads(response.read().decode("utf-8"))
        current = value.get("current") or {}
        return {
            "status": "ok",
            "temperature": current.get("temperature_2m"),
            "weather_code": current.get("weather_code"),
            "wind_speed": current.get("wind_speed_10m"),
            "updated_at": time.time(),
        }


def _weather_label(value: dict) -> str:
    code = value.get("weather_code")
    if code is None:
        return "未知天气"
    code = int(code)
    if code == 0:
        return "晴天"
    if code in {1, 2, 3}:
        return "多云"
    if code in {45, 48}:
        return "雾天"
    if code in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
        return "雨天"
    if code in {71, 73, 75, 77, 85, 86}:
        return "雪天"
    return "雷雨天气"


class SmartSelector:
    def __init__(self, index, config_store, weather_client=None):
        self.index = index
        self.config_store = config_store
        self.weather_client = weather_client or WeatherClient()
        self.weather = {"status": "unknown"}
        self.weather_error = None
        self.revision = 1
        self.current = None
        self.history: list[int] = []
        # Semantic ranking is expensive on the 310B because it includes one
        # text-encoder execution followed by a FAISS lookup.  Keep the
        # in-memory ranked candidates produced by ``_choose`` so a manual
        # touchscreen gesture consumes an NPU-derived result instead of
        # running the same query for every tap.  This is a volatile planning
        # queue only; no image or embedding cache is written to disk.
        self._semantic_queue: list[dict] = []
        self._semantic_context = None
        self._semantic_queue_source = None
        self._last_semantic_inference = {
            "model_id": CHINESE_CLIP_ID,
            "status": "not_run",
            "duration_ms": None,
            "result_count": 0,
            "at": None,
            "error": None,
        }
        # Local touchscreen requests, the periodic scheduler, and remote
        # device pulls all share ``current``/``revision`` state.  Use one
        # re-entrant lock so a persisted restore or a selection cannot be
        # observed half-way through another operation.  Keep the old
        # ``_device_lock`` name as an alias for callers that use it in tests.
        self._state_lock = threading.RLock()
        self._device_lock = self._state_lock
        # Weather I/O can take several seconds on a constrained LAN. Keep it
        # serialized, but do not hold the selection lock while waiting on the
        # network so a touchscreen next/previous request remains responsive.
        self._weather_lock = threading.Lock()

    @property
    def state_lock(self):
        """Lock guarding in-memory selection and weather state.

        Application-level display controls occasionally need to update the
        selected row directly after validating it against the index.  Expose
        the same lock without requiring those callers to know the private
        implementation name.
        """

        return self._state_lock

    def restore_local(self):
        with self._state_lock:
            return self._restore_local()

    def _restore_local(self):
        """Restore the persisted touchscreen selection without network I/O.

        The local display has its own durable ``display_state`` row, just like
        a PhotoFrame device.  Loading it before the scheduler starts keeps a
        process restart observationally stable: health/status endpoints see
        the same photo immediately and the first scheduled tick does not pick
        a new image merely because the weather snapshot is still cold.
        """
        getter = getattr(self.index, "get_display_state", None)
        if not callable(getter):
            return None
        try:
            saved = self._row_dict(getter("local"))
        except Exception:
            return None
        if not isinstance(saved, dict) or saved.get("photo_id") is None:
            return None
        try:
            photo_id = int(saved["photo_id"])
        except (TypeError, ValueError):
            return None
        current = self._photo(photo_id)
        if current is None:
            # Keep the durable row intact so an externally unavailable photo
            # can be diagnosed or become available again later.
            return None
        try:
            selection_revision = int(saved.get("selection_revision", 1))
        except (TypeError, ValueError):
            selection_revision = 1
        try:
            policy_revision = int(saved.get("policy_revision", 1))
        except (TypeError, ValueError):
            policy_revision = 1
        history_ids = []
        history_getter = getattr(self.index, "display_history_ids", None)
        if callable(history_getter):
            try:
                window = int(self.config_store.get()["display"].get("repeat_window", 12))
                # ``display_history_ids`` returns newest first while the
                # in-memory selector appends in chronological order.
                history_ids = [int(value) for value in history_getter("local", max(0, window))]
            except (KeyError, TypeError, ValueError, OSError):
                history_ids = []
        self.history = list(reversed(history_ids)) if history_ids else []
        self.revision = max(int(self.revision), selection_revision)
        self.current = dict(
            current,
            selection_revision=selection_revision,
            policy_revision=policy_revision,
            slot_key=saved.get("slot_key"),
            weather=_weather_label(self.weather),
        )
        # A restored process has no durable semantic queue.  Any volatile
        # candidates left by an embedding rebuild must be recomputed against
        # the restored photo and metadata context.
        self._clear_semantic_queue()
        return self.current

    def refresh_weather(self):
        config = self.config_store.get()["weather"]
        with self._weather_lock:
            try:
                value = self.weather_client.fetch(config)
            except Exception as exc:
                with self._state_lock:
                    self.weather_error = str(exc)
                    return self.weather
            with self._state_lock:
                self.weather = value
                self.weather_error = None
                # Weather is part of the semantic prompt and score.  A fresh
                # snapshot therefore invalidates the old ranked candidates,
                # while the next gesture still uses this cached value and
                # never performs a network request itself.
                self._clear_semantic_queue()
                self.revision += 1
                # ``current`` belongs only to the local touchscreen/E6
                # selection. Persist the revision which includes this weather
                # update so a restart cannot temporarily roll an unchanged
                # photo back to an older ETag/revision. Device selections have
                # their own state rows and include weather separately in their
                # render ETags, so they are intentionally not rewritten here.
                if isinstance(self.current, dict) and self.current.get("id") is not None:
                    self.current = dict(
                        self.current,
                        selection_revision=self.revision,
                        weather=_weather_label(self.weather),
                    )
                    saver = getattr(self.index, "save_display_state", None)
                    if callable(saver):
                        try:
                            saver(
                                "local",
                                int(self.current["id"]),
                                self.current.get("slot_key"),
                                int(self.current.get("policy_revision", 1)),
                                self.revision,
                            )
                        except (OSError, TypeError, ValueError):
                            # A temporary persistence error must not make the
                            # weather service unusable. The next selection or
                            # successful refresh will retry the durable write.
                            pass
                return self.weather

    def _refresh_weather(self):
        """Backward-compatible internal entry point."""
        return self.refresh_weather()

    def _now(self):
        try:
            return datetime.now(ZoneInfo(self.config_store.get()["timezone"]))
        except Exception:
            return datetime.now()

    def _date_score(self, row: dict, now: datetime) -> float:
        value = row.get("capture_time") or row.get("upload_time")
        if not value:
            return 0.0
        try:
            stamp = datetime.fromtimestamp(float(value), now.tzinfo) if isinstance(value, (int, float)) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return 0.0
        score = 1.0 if stamp.month == now.month else 0.0
        if abs(stamp.hour - now.hour) <= 3:
            score += 0.25
        return min(score, 1.0)

    def _fallback(self, rows):
        return max(rows, key=lambda row: (row.get("capture_time") or "", row.get("upload_time") or "")) if rows else None

    def choose(self, profile="jpeg"):
        with self._state_lock:
            return self._choose(profile)

    @staticmethod
    def _photo_signature(rows):
        """Return a compact identity for invalidating stale rankings."""

        values = []
        for value in rows:
            row = SmartSelector._row_dict(value)
            if not isinstance(row, dict) or row.get("id") is None:
                continue
            try:
                photo_id = int(row["id"])
            except (TypeError, ValueError):
                continue
            # Metadata changes (tags/time) affect date/weather scoring even
            # when the photo ID remains stable, so include those fields too.
            values.append(
                (
                    photo_id,
                    str(row.get("sha256") or ""),
                    str(row.get("filename") or ""),
                    str(row.get("capture_time") or ""),
                    str(row.get("upload_time") or ""),
                    str(row.get("tags") or ""),
                )
            )
        return tuple(sorted(values))

    def _selection_context(self, rows, config, now, label):
        """Build the invalidation key for the volatile semantic queue."""

        weather = self.weather or {}
        weather_key = tuple(
            (key, str(weather.get(key)))
            for key in ("status", "weather_code", "temperature", "wind_speed")
        )
        return (
            int(config.get("revision", 1)),
            now.date().isoformat(),
            int(now.hour),
            str(label),
            weather_key,
            self._photo_signature(rows),
        )

    def _clear_semantic_queue(self):
        """Discard candidates whose context no longer matches the library."""

        self._semantic_queue = []
        self._semantic_context = None
        self._semantic_queue_source = None

    def invalidate_preselection(self):
        """Invalidate the volatile NPU candidate plan safely.

        Uploads, metadata edits, configuration updates, and weather refreshes
        can call this hook without touching the durable display state.  The
        next scheduler prefetch or manual gesture will rebuild the plan.
        """

        with self._state_lock:
            self._clear_semantic_queue()

    def prepare_next(self, profile="jpeg"):
        """Precompute the next semantic candidates without changing ``current``.

        This method is intended for the application's serialized scheduler or
        single-worker executor: it performs one admitted Chinese-CLIP/FAISS query for a new context and
        stores only row metadata and scores in memory.  Calling it repeatedly
        for the same context is idempotent, including when the resulting queue
        is empty (for example, a one-photo library).

        Returns the number of usable queued candidates.
        """

        del profile  # The semantic ranking is shared by JPEG and E6 displays.
        with self._state_lock:
            rows = [
                self._row_dict(value)
                for value in self.index.list_photos(limit=None)
            ]
            rows = [
                row
                for row in rows
                if isinstance(row, dict) and row.get("id") is not None
            ]
            if not rows:
                self._clear_semantic_queue()
                return 0
            config = self.config_store.get()
            now = self._now()
            label = _weather_label(self.weather)
            context = self._selection_context(rows, config, now, label)
            if context == self._semantic_context:
                return len(self._semantic_queue)
            scored, _prompt, context = self._rank_candidates(rows, config, now, label)
            current_id = None
            if isinstance(self.current, dict) and self.current.get("id") is not None:
                try:
                    current_id = int(self.current["id"])
                except (TypeError, ValueError):
                    current_id = None
            self._semantic_queue = [
                {"row": dict(row), "score": float(score)}
                for score, row in scored
                if current_id is None or int(row["id"]) != current_id
            ]
            self._semantic_context = context
            # A successful text encoder call is not enough to claim semantic
            # selection: an empty FAISS space means there are no image
            # embeddings for the score to rank.  Keep that state explicit so a
            # newly restored board cannot present a metadata order as NPU
            # output.
            semantic_ok = (
                self._last_semantic_inference["status"] == "ok"
                and int(self._last_semantic_inference.get("result_count") or 0) > 0
            )
            self._semantic_queue_source = "npu_semantic" if semantic_ok else "metadata_fallback"
            return len(self._semantic_queue)

    def _rank_candidates(self, rows, config, now, label):
        """Rank rows once using the admitted Chinese-CLIP text space.

        The caller holds ``_state_lock``.  Keeping this helper separate lets
        ``_choose`` and the manual-next fallback share exactly the same NPU
        scoring contract, while the resulting list can be consumed cheaply.
        """

        prompt = f"适合{now.month}月{now.hour}点、{label}的家庭相册照片"
        semantic = {}
        started = time.perf_counter()
        inference_status = "ok"
        inference_error = None
        try:
            for result in self.index.search_text(
                prompt, CHINESE_CLIP_ID, min(100, max(1, len(rows)))
            ):
                semantic[int(result.photo_id)] = float(result.score)
        except Exception as exc:
            # Preserve the existing metadata-only fallback when an admitted
            # index is temporarily empty.  Production API admission still
            # rejects unavailable NPU models; this path only keeps navigation
            # deterministic while an index is being rebuilt.
            semantic = {}
            inference_status = "error"
            inference_error = str(exc)[:300]
        self._last_semantic_inference = {
            "model_id": CHINESE_CLIP_ID,
            "status": inference_status,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "result_count": len(semantic),
            "at": time.time(),
            "error": inference_error,
        }
        weights = config["selection"]
        total = sum(
            float(weights[key])
            for key in ("semantic_weight", "date_weight", "weather_weight")
        )
        scored = []
        for row in rows:
            row = self._row_dict(row)
            photo_id = int(row["id"])
            tags = str(row.get("tags") or "")
            weather_score = 1.0 if label in tags else 0.0
            score = (
                float(weights["semantic_weight"]) * semantic.get(photo_id, 0.0)
                + float(weights["date_weight"]) * self._date_score(row, now)
                + float(weights["weather_weight"]) * weather_score
            ) / total
            scored.append((score, row))
        scored.sort(
            key=lambda item: (
                item[0],
                item[1].get("upload_time") or "",
                item[1].get("filename") or "",
            ),
            reverse=True,
        )
        return scored, prompt, self._selection_context(rows, config, now, label)

    def _choose(self, profile="jpeg"):
        all_rows = [self._row_dict(row) for row in self.index.list_photos(limit=None)]
        rows = all_rows
        if self.history:
            filtered = [row for row in rows if int(row["id"]) not in self.history]
            if filtered:
                rows = filtered
        if not rows:
            self.current = None
            self._clear_semantic_queue()
            return None
        config = self.config_store.get()
        now = self._now()
        label = _weather_label(self.weather)
        # Rank the complete available library once.  Selection still honors
        # the repeat window by choosing the first candidate outside history;
        # the remaining NPU-ranked rows become the manual navigation queue.
        scored, prompt, context = self._rank_candidates(all_rows, config, now, label)
        history_ids = {int(value) for value in self.history}
        eligible = [item for item in scored if int(item[1]["id"]) not in history_ids]
        selected_score, selected = (eligible or scored)[0]
        self._semantic_queue = [
            {"row": dict(row), "score": float(score)}
            for score, row in scored
            if int(row["id"]) != int(selected["id"])
        ]
        self._semantic_context = context
        semantic_ok = self._last_semantic_inference["status"] == "ok"
        self._semantic_queue_source = "npu_semantic" if semantic_ok else "metadata_fallback"
        self.current = dict(
            selected,
            selection_revision=self.revision,
            weather=label,
            prompt=prompt,
            selection_source=self._semantic_queue_source,
            semantic_score=float(selected_score),
        )
        self.history.append(int(selected["id"]))
        window = int(config["display"]["repeat_window"])
        self.history = self.history[-window:] if window else []
        return self.current

    def next_local_photo(self, profile="jpeg"):
        """Advance through an NPU-ranked queue without weather I/O.

        Automatic selection fills ``_semantic_queue`` with one Chinese-CLIP
        query and its FAISS scores.  A manual gesture consumes that queue in
        constant time.  If it was invalidated by a metadata/weather/config
        change or exhausted, one synchronous NPU ranking replenishes it; the
        weather snapshot is always the already-cached ``self.weather`` value.
        """

        with self._state_lock:
            if self.current is None:
                self._restore_local()
            rows = [
                self._row_dict(value)
                for value in self.index.list_photos(limit=None)
            ]
            rows = [
                row
                for row in rows
                if isinstance(row, dict) and row.get("id") is not None
            ]
            if not rows:
                self.current = None
                self._clear_semantic_queue()
                return None
            current_id = None
            if isinstance(self.current, dict) and self.current.get("id") is not None:
                try:
                    current_id = int(self.current["id"])
                except (TypeError, ValueError):
                    current_id = None

            config = self.config_store.get()
            now = self._now()
            label = _weather_label(self.weather)
            context = self._selection_context(rows, config, now, label)
            if context != self._semantic_context:
                self._clear_semantic_queue()

            selected = None
            selected_score = None
            selection_source = (
                "npu_semantic_prefetch"
                if self._semantic_queue_source == "npu_semantic"
                else "metadata_prefetch"
            )
            while self._semantic_queue:
                candidate = self._semantic_queue.pop(0)
                row = self._row_dict(candidate.get("row"))
                if not isinstance(row, dict) or row.get("id") is None:
                    continue
                try:
                    photo_id = int(row["id"])
                except (TypeError, ValueError):
                    continue
                # Re-read the row so an unavailable/deleted photo cannot be
                # selected from a stale in-memory queue after an upload or
                # lifecycle operation.
                current_row = self._photo(photo_id, rows)
                if current_row is None or photo_id == current_id:
                    continue
                selected = current_row
                selected_score = candidate.get("score")
                break

            if selected is None and len(rows) > 1:
                # Queue empty: perform exactly one cached-weather semantic
                # query, then consume its first candidate other than current.
                scored, prompt, context = self._rank_candidates(rows, config, now, label)
                self._semantic_context = context
                semantic_ok = (
                    self._last_semantic_inference["status"] == "ok"
                    and int(self._last_semantic_inference.get("result_count") or 0) > 0
                )
                self._semantic_queue_source = "npu_semantic" if semantic_ok else "metadata_fallback"
                for score, row in scored:
                    if current_id is not None and int(row["id"]) == current_id:
                        continue
                    selected = row
                    selected_score = score
                    semantic_ok = (
                        self._last_semantic_inference["status"] == "ok"
                        and int(self._last_semantic_inference.get("result_count") or 0) > 0
                    )
                    selection_source = "npu_semantic_sync" if semantic_ok else "metadata_sync"
                    break
                if selected is not None:
                    self._semantic_queue = [
                        {"row": dict(row), "score": float(score)}
                        for score, row in scored
                        if int(row["id"]) != int(selected["id"])
                    ]
            else:
                prompt = "手动顺序切换（语义队列无可用候选）"

            if selected is None:
                # A one-photo library, or a queue whose rows all disappeared,
                # still has a deterministic navigation result.  This branch
                # is deliberately metadata-only and never contacts weather.
                def order(row):
                    try:
                        photo_id = int(row.get("id"))
                    except (TypeError, ValueError):
                        photo_id = 0
                    return (str(row.get("filename") or "").casefold(), photo_id)

                rows.sort(key=order)
                selected_index = -1
                if current_id is not None:
                    for index, row in enumerate(rows):
                        if int(row["id"]) == current_id:
                            selected_index = index
                            break
                selected = rows[(selected_index + 1) % len(rows)]
                selected_score = None
                selection_source = "metadata_order_fallback"
                prompt = "手动顺序切换（无可用语义候选）"
            self.revision += 1
            # Keep the public prompt used by the touchscreen concise and
            # backward-compatible.  ``selection_source`` and
            # ``semantic_score`` carry the NPU provenance without exposing a
            # long internal context sentence in the UI.
            self.current = dict(
                selected,
                selection_revision=self.revision,
                weather=label,
                prompt="手动顺序切换",
                selection_source=selection_source,
            )
            if selected_score is not None:
                self.current["semantic_score"] = float(selected_score)
            self.history.append(int(selected["id"]))
            try:
                window = int(config["display"].get("repeat_window", 12))
            except (KeyError, TypeError, ValueError):
                window = 12
            self.history = self.history[-window:] if window else []
            return self.current

    def current_photo(self, profile="jpeg"):
        with self._state_lock:
            if self.current is None:
                # Pick up a selection written by a previous process before
                # doing any weather request or semantic ranking.
                self._restore_local()
            needs_weather = self.current is None and self.weather.get("status") == "unknown"
            if self.current is not None:
                return self.current
        # Local display selection keeps the historical first-use behavior;
        # PhotoFrame URL Rotation uses current_for_device below and never
        # performs this network refresh on a pull. Fetch outside the state
        # lock so a slow weather provider cannot block touch navigation.
        if needs_weather:
            self.refresh_weather()
        with self._state_lock:
            if self.current is None:
                return self._choose(profile)
            return self.current

    def status(self):
        with self._state_lock:
            if self.current is None:
                self._restore_local()
            return {
                "selection_revision": self.revision,
                "current": self.current,
                "weather": self.weather,
                "weather_error": self.weather_error,
                "history_size": len(self.history),
                "semantic_preselection": {
                    "queue_size": len(self._semantic_queue),
                    "ready": self._semantic_context is not None,
                    "source": self._semantic_queue_source,
                    "last_inference": dict(self._last_semantic_inference),
                },
            }

    @staticmethod
    def _row_dict(row):
        """Normalize sqlite rows and test doubles to ordinary dictionaries."""
        if row is None:
            return None
        if isinstance(row, dict):
            return dict(row)
        keys = getattr(row, "keys", None)
        if callable(keys):
            return {key: row[key] for key in keys()}
        try:
            return dict(row)
        except (TypeError, ValueError):
            return row

    def _photo(self, photo_id: int, rows=None):
        getter = getattr(self.index, "get_photo", None)
        if callable(getter):
            return self._row_dict(getter(int(photo_id)))
        for row in rows or []:
            if int(row.get("id")) == int(photo_id):
                return self._row_dict(row)
        return None

    @staticmethod
    def _next_revision(saved, current: int) -> int:
        """Keep selection revisions monotonic across process restarts."""
        if not saved:
            return int(current)
        try:
            return max(int(current), int(saved.get("selection_revision", 0)) + 1)
        except (TypeError, ValueError):
            return int(current)

    def _select_playlist(self, key: str, policy: dict, saved, slot: Optional[str]):
        """Select the next configured photo without falling back to the library.

        ``list_photos`` already excludes unavailable/deleted records.  Keeping
        the configured ID order here means a stale playlist is safely skipped
        while a playlist containing no usable IDs returns ``None``.
        """
        rows = [self._row_dict(row) for row in self.index.list_photos(limit=None)]
        by_id = {int(row["id"]): row for row in rows if row and row.get("id") is not None}
        valid_ids = [photo_id for photo_id in policy["playlist_photo_ids"] if photo_id in by_id]
        next_revision = self._next_revision(saved, self.revision)
        if not valid_ids:
            if hasattr(self.index, "save_display_state"):
                self.index.save_display_state(key, None, slot, int(policy["policy_revision"]), next_revision)
            return None

        saved_id = None
        if saved and saved.get("photo_id") is not None:
            try:
                candidate = int(saved["photo_id"])
            except (TypeError, ValueError):
                candidate = None
            if candidate in valid_ids:
                saved_id = candidate
        if saved_id is None:
            selected_id = valid_ids[0]
        else:
            selected_id = valid_ids[(valid_ids.index(saved_id) + 1) % len(valid_ids)]
        selected = by_id[selected_id]
        if hasattr(self.index, "save_display_state"):
            self.index.save_display_state(key, selected_id, slot, int(policy["policy_revision"]), next_revision)
            self.index.record_display_history(key, selected_id, int(policy.get("repeat_window", 12)))
        return dict(
            selected,
            selection_revision=next_revision,
            policy_revision=int(policy["policy_revision"]),
            slot_key=slot,
            weather=_weather_label(self.weather),
            playlist_index=valid_ids.index(selected_id),
        )

    def _select_for_key(self, key: str, policy: dict, force: bool = False):
        policy = validate_policy(policy)
        try:
            now = self._now()
            slot = effective_cron_slot(now, policy["rotation_cron"]) if policy["auto_rotate"] else None
        except Exception:
            slot = None
        saved = self._row_dict(self.index.get_display_state(key)) if hasattr(self.index, "get_display_state") else None
        if saved and not force and saved.get("photo_id") is not None and int(saved.get("policy_revision", 1)) == int(policy["policy_revision"]):
            current = self._photo(int(saved["photo_id"]))
            in_playlist = (
                current is not None
                and (policy["selection_mode"] != "playlist" or int(current["id"]) in policy["playlist_photo_ids"])
            )
            if current is not None and in_playlist and (not policy["auto_rotate"] or saved.get("slot_key") == slot or slot is None):
                return dict(current, selection_revision=int(saved.get("selection_revision", 1)), policy_revision=int(saved.get("policy_revision", 1)), slot_key=saved.get("slot_key"), weather=_weather_label(self.weather))

        # Playlist mode is intentionally isolated from semantic smart
        # selection.  An empty or entirely stale playlist is an explicit
        # configuration error for the caller, represented by ``None``.
        if policy["selection_mode"] == "playlist":
            return self._select_playlist(key, policy, saved, slot)

        rows = [self._row_dict(row) for row in self.index.list_photos(limit=None)]
        history = self.index.display_history_ids(key, int(policy.get("repeat_window", 12))) if hasattr(self.index, "display_history_ids") else []
        if history:
            filtered = [row for row in rows if int(row["id"]) not in history]
            if filtered:
                rows = filtered
        if not rows:
            if hasattr(self.index, "save_display_state"):
                self.index.save_display_state(key, None, slot, int(policy["policy_revision"]), self.revision)
            return None
        now = self._now()
        label = _weather_label(self.weather)
        prompt = f"适合{now.month}月{now.hour}点、{label}的家庭相册照片"
        semantic = {}
        try:
            for result in self.index.search_text(prompt, CHINESE_CLIP_ID, min(100, max(1, len(rows)))):
                semantic[int(result.photo_id)] = float(result.score)
        except Exception:
            pass
        weights = self.config_store.get()["selection"]
        total = sum(float(weights[name]) for name in ("semantic_weight", "date_weight", "weather_weight"))
        scored = []
        for row in rows:
            tags = str(row.get("tags") or "")
            weather_score = 1.0 if label in tags else 0.0
            score = (float(weights["semantic_weight"]) * semantic.get(int(row["id"]), 0.0) + float(weights["date_weight"]) * self._date_score(row, now) + float(weights["weather_weight"]) * weather_score) / total
            scored.append((score, row))
        selected = max(scored, key=lambda item: (item[0], item[1].get("upload_time") or ""))[1]
        next_revision = self._next_revision(saved, self.revision)
        if hasattr(self.index, "save_display_state"):
            self.index.save_display_state(key, int(selected["id"]), slot, int(policy["policy_revision"]), next_revision)
            self.index.record_display_history(key, int(selected["id"]), int(policy.get("repeat_window", 12)))
        return dict(selected, selection_revision=next_revision, policy_revision=policy["policy_revision"], slot_key=slot, weather=label, prompt=prompt)

    def current_for_device(self, device_id: str, policy: Optional[dict] = None, force: bool = False):
        """Return a restart-stable selection for one PhotoFrame device."""
        with self._device_lock:
            # URL Rotation consumes the cached weather snapshot. The scheduler
            # or an explicit display refresh owns network weather requests, so
            # retries and rapid pulls never perform network I/O.
            return self._select_for_key(str(device_id), policy or DEFAULT_PHOTOFRAME_POLICY, force=force)
