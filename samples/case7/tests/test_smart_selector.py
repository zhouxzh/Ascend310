import tempfile
import unittest
import sqlite3
from datetime import datetime
from pathlib import Path

from server_config import ConfigStore
from smart_selector import SmartSelector


class Result:
    def __init__(self, photo_id, score):
        self.photo_id = photo_id
        self.score = score


class FakeIndex:
    def __init__(self, semantic_results=None):
        self.search_calls = 0
        self.semantic_results = semantic_results
        self.rows = [
            {"id": 1, "filename": "old.jpg", "capture_time": "2022-01-01T10:00:00", "upload_time": 1.0, "tags": "晴天", "filepath": "/tmp/old.jpg", "sha256": "a"},
            {"id": 2, "filename": "best.jpg", "capture_time": datetime.now().replace(microsecond=0).isoformat(), "upload_time": 2.0, "tags": "晴天 旅行", "filepath": "/tmp/best.jpg", "sha256": "b"},
        ]

    def list_photos(self, limit=None):
        return list(self.rows)

    def search_text(self, query, model_id, k):
        self.search_calls += 1
        return [Result(2, 0.95), Result(1, 0.10)] if self.semantic_results is None else self.semantic_results


class PlaylistIndex:
    def __init__(self, rows=None):
        self.rows = rows or [
            {"id": 10, "filename": "10.jpg", "upload_time": 10.0, "capture_time": None, "tags": "", "filepath": "/tmp/10.jpg"},
            {"id": 11, "filename": "11.jpg", "upload_time": 11.0, "capture_time": None, "tags": "", "filepath": "/tmp/11.jpg"},
            {"id": 12, "filename": "12.jpg", "upload_time": 12.0, "capture_time": None, "tags": "", "filepath": "/tmp/12.jpg"},
        ]
        self.saved = {}
        self.history = []

    def list_photos(self, limit=None):
        return list(self.rows)

    def get_photo(self, photo_id):
        return next((row for row in self.rows if int(row["id"]) == int(photo_id)), None)

    def get_display_state(self, device_id):
        return self.saved.get(str(device_id))

    def save_display_state(self, device_id, photo_id, slot_key, policy_revision, selection_revision):
        self.saved[str(device_id)] = {
            "device_id": str(device_id),
            "photo_id": photo_id,
            "slot_key": slot_key,
            "policy_revision": policy_revision,
            "selection_revision": selection_revision,
        }

    def display_history_ids(self, device_id, limit=12):
        return list(reversed(self.history[-int(limit):])) if int(limit) else []

    def record_display_history(self, device_id, photo_id, keep=12):
        self.history.append(int(photo_id))
        if int(keep) >= 0:
            self.history = self.history[-int(keep):] if int(keep) else []


class SqliteStateIndex(PlaylistIndex):
    """Return a real sqlite3.Row for the persisted display state."""

    def __init__(self):
        super().__init__()
        self._connection = sqlite3.connect(":memory:")
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            "CREATE TABLE display_state (photo_id INTEGER, slot_key TEXT, "
            "policy_revision INTEGER, selection_revision INTEGER)"
        )
        self._connection.execute(
            "INSERT INTO display_state VALUES (?, ?, ?, ?)",
            (11, "manual", 3, 17),
        )
        self._connection.commit()

    def get_display_state(self, device_id):
        return self._connection.execute("SELECT * FROM display_state").fetchone()


class Weather:
    def __init__(self):
        self.calls = 0

    def fetch(self, config):
        self.calls += 1
        return {"status": "ok", "weather_code": 0}


class SmartSelectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.selector = SmartSelector(FakeIndex(), ConfigStore(Path(self.temp.name) / "config.json"), Weather())

    def tearDown(self):
        self.temp.cleanup()

    def test_semantic_date_weather_selection_is_shared_by_profiles(self):
        current = self.selector.current_photo("e6")
        self.assertEqual(current["id"], 2)
        self.assertEqual(self.selector.current_photo("jpeg")["id"], 2)
        self.assertEqual(current["weather"], "晴天")

    def test_manual_next_consumes_precomputed_semantic_inference_without_weather(self):
        index = FakeIndex()
        weather = Weather()
        selector = SmartSelector(index, ConfigStore(Path(self.temp.name) / "manual-config.json"), weather)
        selector.current_photo("jpeg")
        searches = index.search_calls
        weather_calls = weather.calls

        next_photo = selector.next_local_photo("jpeg")

        self.assertEqual(next_photo["id"], 1)
        self.assertEqual(index.search_calls, searches)
        self.assertEqual(weather.calls, weather_calls)
        self.assertEqual(next_photo["prompt"], "手动顺序切换")
        self.assertEqual(next_photo["selection_source"], "npu_semantic_prefetch")

    def test_manual_next_consumes_npu_ranked_order(self):
        index = FakeIndex(
            semantic_results=[Result(2, 0.99), Result(1, 0.60)]
        )
        weather = Weather()
        selector = SmartSelector(
            index,
            ConfigStore(Path(self.temp.name) / "ranked-config.json"),
            weather,
        )

        first = selector.current_photo("jpeg")
        searches = index.search_calls
        second = selector.next_local_photo("jpeg")

        self.assertEqual(first["id"], 2)
        self.assertEqual(second["id"], 1)
        self.assertEqual(index.search_calls, searches)
        self.assertEqual(second["selection_source"], "npu_semantic_prefetch")
        self.assertGreaterEqual(second["semantic_score"], 0.0)

    def test_manual_next_refills_empty_queue_with_cached_weather_npu_query(self):
        index = FakeIndex()
        weather = Weather()
        selector = SmartSelector(
            index,
            ConfigStore(Path(self.temp.name) / "sync-config.json"),
            weather,
        )
        # Simulate a restored/current row without calling current_photo(), so
        # the semantic queue is empty and next must execute one NPU query.
        selector.current = dict(index.rows[0])
        selector.weather = {"status": "ok", "weather_code": 0}
        searches = index.search_calls
        weather_calls = weather.calls

        selected = selector.next_local_photo("jpeg")

        self.assertEqual(selected["id"], 2)
        self.assertEqual(index.search_calls, searches + 1)
        self.assertEqual(weather.calls, weather_calls)
        self.assertEqual(selected["selection_source"], "npu_semantic_sync")

    def test_failed_semantic_query_is_reported_as_metadata_sync(self):
        class FailingIndex(FakeIndex):
            def search_text(self, query, model_id, k):
                self.search_calls += 1
                raise RuntimeError("fixture NPU failure")

        index = FailingIndex()
        selector = SmartSelector(
            index,
            ConfigStore(Path(self.temp.name) / "failed-query-config.json"),
            Weather(),
        )
        selector.current = dict(index.rows[0])
        selector.weather = {"status": "ok", "weather_code": 0}

        selected = selector.next_local_photo()

        self.assertEqual(selected["selection_source"], "metadata_sync")
        self.assertEqual(selector.status()["semantic_preselection"]["source"], "metadata_fallback")

    def test_empty_semantic_index_is_not_reported_as_npu_selection(self):
        selector = SmartSelector(
            FakeIndex(semantic_results=[]),
            ConfigStore(Path(self.temp.name) / "empty-query-config.json"),
            Weather(),
        )
        selector.current = dict(selector.index.rows[0])
        selector.weather = {"status": "ok", "weather_code": 0}

        selected = selector.next_local_photo()

        self.assertEqual(selected["selection_source"], "metadata_sync")
        telemetry = selector.status()["semantic_preselection"]
        self.assertEqual(telemetry["last_inference"]["status"], "ok")
        self.assertEqual(telemetry["last_inference"]["result_count"], 0)
        self.assertEqual(telemetry["source"], "metadata_fallback")

    def test_prepare_next_does_not_change_current_or_repeat_same_context(self):
        index = FakeIndex()
        weather = Weather()
        selector = SmartSelector(
            index,
            ConfigStore(Path(self.temp.name) / "prepare-config.json"),
            weather,
        )
        selector.current = dict(index.rows[0])
        before = dict(selector.current)

        queued = selector.prepare_next("jpeg")
        searches = index.search_calls
        queued_again = selector.prepare_next("e6")

        self.assertGreaterEqual(queued, 1)
        self.assertEqual(queued_again, queued)
        self.assertEqual(index.search_calls, searches)
        self.assertEqual(selector.current, before)
        self.assertEqual(weather.calls, 0)

    def test_invalidate_preselection_allows_a_new_npu_plan(self):
        index = FakeIndex()
        selector = SmartSelector(
            index,
            ConfigStore(Path(self.temp.name) / "invalidate-config.json"),
            Weather(),
        )
        selector.current = dict(index.rows[0])

        selector.prepare_next()
        self.assertEqual(index.search_calls, 1)
        selector.invalidate_preselection()
        selector.prepare_next()
        self.assertEqual(index.search_calls, 2)

    def test_local_selection_restores_without_weather_request(self):
        index = PlaylistIndex()
        index.save_display_state("local", 11, "manual", 3, 17)
        index.record_display_history("local", 10)
        index.record_display_history("local", 11)
        weather = Weather()
        selector = SmartSelector(index, ConfigStore(Path(self.temp.name) / "local-config.json"), weather)

        restored = selector.restore_local()

        self.assertIsNotNone(restored)
        self.assertEqual(restored["id"], 11)
        self.assertEqual(restored["selection_revision"], 17)
        self.assertEqual(restored["policy_revision"], 3)
        self.assertEqual(selector.revision, 17)
        self.assertEqual(selector.status()["current"]["id"], 11)
        self.assertEqual(weather.calls, 0)
        # A second read (the path used by the API after startup) is stable and
        # does not alter the history or contact Open-Meteo.
        self.assertEqual(selector.current_photo()["id"], 11)
        self.assertEqual(selector.history, [10, 11])
        self.assertEqual(weather.calls, 0)

    def test_local_selection_accepts_sqlite_row_state(self):
        index = SqliteStateIndex()
        weather = Weather()
        selector = SmartSelector(index, ConfigStore(Path(self.temp.name) / "sqlite-config.json"), weather)

        restored = selector.restore_local()

        self.assertEqual(restored["id"], 11)
        self.assertEqual(restored["selection_revision"], 17)
        self.assertEqual(restored["policy_revision"], 3)
        self.assertEqual(weather.calls, 0)
        self.assertEqual(selector.current_photo()["id"], 11)
        self.assertEqual(weather.calls, 0)

    def test_weather_refresh_persists_local_selection_revision(self):
        index = PlaylistIndex()
        index.save_display_state("local", 11, "manual", 3, 17)
        weather = Weather()
        selector = SmartSelector(index, ConfigStore(Path(self.temp.name) / "weather-persist-config.json"), weather)
        selector.restore_local()

        selector.refresh_weather()

        saved = index.get_display_state("local")
        self.assertEqual(saved["photo_id"], 11)
        self.assertEqual(saved["selection_revision"], 18)
        self.assertEqual(selector.current["selection_revision"], 18)
        self.assertEqual(selector.current["weather"], "晴天")
        restarted = SmartSelector(index, ConfigStore(Path(self.temp.name) / "weather-restart-config.json"), Weather())
        self.assertEqual(restarted.restore_local()["selection_revision"], 18)

    def test_playlist_is_stable_within_slot_and_advances_on_next_slot(self):
        index = PlaylistIndex()
        weather = Weather()
        selector = SmartSelector(index, ConfigStore(Path(self.temp.name) / "playlist-config.json"), weather)
        selector._now = lambda: datetime(2026, 8, 23, 10, 0)
        policy = {
            "selection_mode": "playlist",
            "playlist_photo_ids": [11, 12, 10],
            "rotation_cron": ["*/5 * *"],
            "policy_revision": 4,
        }
        first = selector.current_for_device("e1002", policy)
        self.assertEqual(first["id"], 11)
        retry = selector.current_for_device("e1002", policy)
        self.assertEqual(retry["id"], 11)
        self.assertEqual(retry["selection_revision"], first["selection_revision"])

        selector._now = lambda: datetime(2026, 8, 23, 10, 5)
        second = selector.current_for_device("e1002", policy)
        self.assertEqual(second["id"], 12)
        self.assertNotEqual(second["selection_revision"], first["selection_revision"])

        # A request arriving just after the scheduled minute still belongs to
        # that effective slot; Wi-Fi wake-up latency must not pin the frame to
        # the previous photo until the following five-minute boundary.
        selector._now = lambda: datetime(2026, 8, 23, 10, 6)
        delayed = selector.current_for_device("e1002", policy)
        self.assertEqual(delayed["id"], 12)

        selector._now = lambda: datetime(2026, 8, 23, 10, 10)
        third = selector.current_for_device("e1002", policy)
        self.assertEqual(third["id"], 10)

        forced = selector.current_for_device("e1002", policy, force=True)
        self.assertEqual(forced["id"], 11)
        # A new selector instance reads the persisted state and does not pick
        # a random image after a restart.
        restarted = SmartSelector(index, ConfigStore(Path(self.temp.name) / "restart-config.json"), Weather())
        restarted._now = lambda: datetime(2026, 8, 23, 10, 10)
        self.assertEqual(restarted.current_for_device("e1002", policy)["id"], 11)
        self.assertEqual(weather.calls, 0)

    def test_device_selection_only_consumes_cached_weather(self):
        index = PlaylistIndex()
        weather = Weather()
        selector = SmartSelector(index, ConfigStore(Path(self.temp.name) / "playlist-weather.json"), weather)
        selector._now = lambda: datetime(2026, 8, 23, 10, 0)
        policy = {"selection_mode": "playlist", "playlist_photo_ids": [10], "rotation_cron": ["*/5 * *"]}
        selector.current_for_device("e1002", policy)
        selector.current_for_device("e1002", policy)
        self.assertEqual(weather.calls, 0)
        selector.refresh_weather()
        self.assertEqual(weather.calls, 1)

    def test_playlist_skips_unavailable_ids_and_does_not_fallback(self):
        index = PlaylistIndex([index_row for index_row in PlaylistIndex().rows if index_row["id"] != 12])
        selector = SmartSelector(index, ConfigStore(Path(self.temp.name) / "playlist-config-2.json"), Weather())
        selector._now = lambda: datetime(2026, 8, 23, 10, 0)
        policy = {"selection_mode": "playlist", "playlist_photo_ids": [12, 11, 10], "rotation_cron": ["*/5 * *"]}
        self.assertEqual(selector.current_for_device("e1002", policy)["id"], 11)
        selector._now = lambda: datetime(2026, 8, 23, 10, 5)
        self.assertEqual(selector.current_for_device("e1002", policy)["id"], 10)

        empty = {"selection_mode": "playlist", "playlist_photo_ids": [999], "rotation_cron": ["*/5 * *"]}
        self.assertIsNone(selector.current_for_device("empty", empty))
        self.assertIsNone(index.saved["empty"]["photo_id"])


if __name__ == "__main__":
    unittest.main()
