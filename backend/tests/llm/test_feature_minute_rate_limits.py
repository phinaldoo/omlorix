import asyncio
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.models import (  # noqa: E402
    RATE_LIMIT_ADMISSION_COMPLETED,
    RATE_LIMIT_ADMISSION_FAILED,
    RATE_LIMIT_QUOTA_UNIT_MINUTES,
    RATE_LIMIT_SCOPE_CHAT,
    RATE_LIMIT_TARGET_TYPE_DICTATION,
    RATE_LIMIT_TARGET_TYPE_REALTIME,
    DurationRateLimitAdmissionContext,
    RateLimit,
    RateLimitDurationAdmission,
    RateLimitUsageWindow,
    admit_user_duration_rate_limit,
    finalize_duration_rate_limit_admission,
    get_rate_limit_usage_snapshot,
    renew_dictation_duration_rate_limit_lease,
    touch_duration_rate_limit_admission,
)
from app.llm.audio_duration import measure_audio_duration_seconds  # noqa: E402
from app.llm.schemas import CreateRateLimitRequest  # noqa: E402


class FeatureMinuteRateLimitTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        RateLimit.__table__.create(bind=engine)
        RateLimitUsageWindow.__table__.create(bind=engine)
        RateLimitDurationAdmission.__table__.create(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _create_limit(self, db, target_type: str, minutes: int = 2) -> RateLimit:
        policy = RateLimit(
            name=f"{target_type} quota",
            target_type=target_type,
            model_ids=[],
            tool_keys=[],
            user_ids=["user-1"],
            group_ids=[],
            scope=RATE_LIMIT_SCOPE_CHAT,
            period="day",
            timezone="UTC",
            quota_unit=RATE_LIMIT_QUOTA_UNIT_MINUTES,
            quota_value=minutes,
            max_requests=1,
            is_active=True,
        )
        db.add(policy)
        db.commit()
        db.refresh(policy)
        return policy

    def test_schema_requires_minutes_and_no_catalog_targets(self):
        validated = CreateRateLimitRequest.model_validate(
            {
                "name": "Dictation quota",
                "target_type": "dictation",
                "user_ids": ["user-1"],
                "period": "day",
                "quota_unit": "minutes",
                "quota_value": 10,
            }
        )
        self.assertEqual(validated.quota_unit.value, "minutes")

        with self.assertRaises(ValueError):
            CreateRateLimitRequest.model_validate(
                {
                    "name": "Invalid dictation quota",
                    "target_type": "dictation",
                    "model_ids": ["model-1"],
                    "user_ids": ["user-1"],
                    "period": "day",
                    "quota_unit": "minutes",
                    "quota_value": 10,
                }
            )

    def test_browser_reported_duration_requires_explicit_non_quota_fallback(self):
        with patch("app.llm.audio_duration._measure_wav_duration", return_value=None), patch(
            "app.llm.audio_duration._measure_with_ffprobe",
            return_value=None,
        ):
            self.assertIsNone(
                measure_audio_duration_seconds(
                    b"unmeasurable",
                    filename="recording.webm",
                    reported_duration_seconds=12.5,
                )
            )
            self.assertEqual(
                measure_audio_duration_seconds(
                    b"unmeasurable",
                    filename="recording.webm",
                    reported_duration_seconds=12.5,
                    allow_reported_duration=True,
                ),
                12.5,
            )

    def test_ffprobe_disconnects_stdin_without_unsupported_cli_option(self):
        """Browser WebM probing must use options supported by ffprobe."""
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"streams": [], "format": {"duration": "12.5"}}',
            stderr="",
        )
        with patch(
            "app.llm.audio_duration._measure_wav_duration",
            return_value=None,
        ), patch(
            "app.llm.audio_duration.shutil.which",
            return_value="/usr/bin/ffprobe",
        ), patch(
            "app.llm.audio_duration.subprocess.run",
            return_value=completed,
        ) as run_mock:
            measured = measure_audio_duration_seconds(
                b"browser-webm",
                filename="recording.webm",
            )

        self.assertEqual(measured, 12.5)
        command = run_mock.call_args.args[0]
        self.assertNotIn("-nostdin", command)
        self.assertEqual(run_mock.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_ffmpeg_uses_packet_timestamps_when_container_duration_is_absent(self):
        """Metadata-less MediaRecorder output remains measurable for quotas."""
        ffprobe_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"streams": [{"codec_type": "audio"}], "format": {}}',
            stderr="",
        )
        ffmpeg_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="out_time_us=12421000\nprogress=end\n",
            stderr="",
        )
        with patch(
            "app.llm.audio_duration._measure_wav_duration",
            return_value=None,
        ), patch(
            "app.llm.audio_duration.shutil.which",
            side_effect=["/usr/bin/ffprobe", "/usr/bin/ffmpeg"],
        ), patch(
            "app.llm.audio_duration.subprocess.run",
            side_effect=[ffprobe_result, ffmpeg_result],
        ) as run_mock:
            measured = measure_audio_duration_seconds(
                b"metadata-less-browser-webm",
                filename="recording.webm",
            )

        self.assertEqual(measured, 12.421)
        self.assertEqual(run_mock.call_count, 2)
        fallback_command = run_mock.call_args.args[0]
        self.assertIn("-c", fallback_command)
        self.assertIn("copy", fallback_command)

    def test_production_image_includes_ffprobe_for_browser_recordings(self):
        """The standard image must measure the WebM/MP4 formats emitted by browsers."""
        dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile"

        self.assertIn("ffmpeg", dockerfile.read_text(encoding="utf-8"))

    def test_dictation_accounts_precise_seconds_and_blocks_overspend(self):
        db = self.Session()
        policy = self._create_limit(db, RATE_LIMIT_TARGET_TYPE_DICTATION, minutes=1)

        admission = admit_user_duration_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            target_type=RATE_LIMIT_TARGET_TYPE_DICTATION,
            requested_seconds=35,
        )
        self.assertIsInstance(admission, DurationRateLimitAdmissionContext)
        finalize_duration_rate_limit_admission(db, admission.admission_id, consumed_seconds=35)

        snapshot = get_rate_limit_usage_snapshot(db, policy, "user-1")
        self.assertEqual(snapshot["current_usage_seconds"], 35)
        self.assertEqual(snapshot["remaining_usage_seconds"], 25)

        blocked = admit_user_duration_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            target_type=RATE_LIMIT_TARGET_TYPE_DICTATION,
            requested_seconds=26,
        )
        self.assertIsInstance(blocked, dict)
        self.assertEqual(blocked["code"], "user_dictation_rate_limited")

    def test_realtime_reserves_remaining_budget_and_releases_unused_time(self):
        db = self.Session()
        policy = self._create_limit(db, RATE_LIMIT_TARGET_TYPE_REALTIME, minutes=2)

        admission = admit_user_duration_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            target_type=RATE_LIMIT_TARGET_TYPE_REALTIME,
        )
        self.assertIsInstance(admission, DurationRateLimitAdmissionContext)
        self.assertEqual(admission.reserved_seconds, 120)

        concurrent = admit_user_duration_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            target_type=RATE_LIMIT_TARGET_TYPE_REALTIME,
        )
        self.assertIsInstance(concurrent, dict)

        finalize_duration_rate_limit_admission(db, admission.admission_id, consumed_seconds=30)
        next_admission = admit_user_duration_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            target_type=RATE_LIMIT_TARGET_TYPE_REALTIME,
        )
        self.assertIsInstance(next_admission, DurationRateLimitAdmissionContext)
        self.assertEqual(next_admission.reserved_seconds, 90)

        snapshot = get_rate_limit_usage_snapshot(db, policy, "user-1")
        self.assertEqual(snapshot["current_usage_seconds"], 30)

    def test_active_live_dictation_reservation_is_not_reported_as_consumed(self):
        """A parallel live socket must not make unused minutes look spent."""
        db = self.Session()
        self._create_limit(db, RATE_LIMIT_TARGET_TYPE_DICTATION, minutes=10)

        active = admit_user_duration_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            target_type=RATE_LIMIT_TARGET_TYPE_DICTATION,
        )
        self.assertIsInstance(active, DurationRateLimitAdmissionContext)
        self.assertEqual(active.reserved_seconds, 600)

        blocked = admit_user_duration_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            target_type=RATE_LIMIT_TARGET_TYPE_DICTATION,
        )

        self.assertIsInstance(blocked, dict)
        self.assertEqual(blocked["code"], "user_dictation_rate_limited")
        self.assertEqual(blocked["reason"], "active_reservation")
        self.assertEqual(blocked["current_usage_seconds"], 0)
        self.assertEqual(blocked["remaining_usage_seconds"], 600)
        self.assertEqual(blocked["reserved_usage_seconds"], 600)
        self.assertEqual(blocked["available_usage_seconds"], 0)

    def test_finalization_is_idempotent_and_capped_to_the_reservation(self):
        db = self.Session()
        policy = self._create_limit(db, RATE_LIMIT_TARGET_TYPE_DICTATION, minutes=2)
        admission = admit_user_duration_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            target_type=RATE_LIMIT_TARGET_TYPE_DICTATION,
            requested_seconds=40,
        )
        self.assertIsInstance(admission, DurationRateLimitAdmissionContext)
        competing_db = self.Session()
        # Preload the same open row in a second identity map. This reproduces
        # the stale read held by two concurrent stop requests before either
        # request acquires the per-window serialization lock.
        competing_admission = competing_db.get(
            RateLimitDurationAdmission,
            admission.admission_id,
        )
        self.assertEqual(competing_admission.status, "open")

        # A delayed or duplicated stop must never charge more than the atomic
        # reservation and must never increment the usage window twice.
        finalize_duration_rate_limit_admission(
            db,
            admission.admission_id,
            consumed_seconds=4_000,
        )
        finalize_duration_rate_limit_admission(
            competing_db,
            admission.admission_id,
            consumed_seconds=20,
        )

        db.expire_all()
        snapshot = get_rate_limit_usage_snapshot(db, policy, "user-1")
        stored = db.get(RateLimitDurationAdmission, admission.admission_id)
        self.assertEqual(snapshot["current_usage_seconds"], 40)
        self.assertEqual(stored.consumed_seconds, 40)
        self.assertEqual(stored.status, RATE_LIMIT_ADMISSION_COMPLETED)

    def test_stale_dictation_reservation_is_released_without_usage(self):
        db = self.Session()
        self._create_limit(db, RATE_LIMIT_TARGET_TYPE_DICTATION, minutes=2)
        stale = admit_user_duration_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            target_type=RATE_LIMIT_TARGET_TYPE_DICTATION,
            requested_seconds=90,
        )
        self.assertIsInstance(stale, DurationRateLimitAdmissionContext)
        stale_record = db.get(RateLimitDurationAdmission, stale.admission_id)
        # Live dictation heartbeats every 20 seconds. A socket lost for longer
        # than the 90-second lease is safe to reclaim without charging usage.
        stale_record.admitted_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        stale_record.last_activity_at = stale_record.admitted_at
        db.commit()

        replacement = admit_user_duration_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            target_type=RATE_LIMIT_TARGET_TYPE_DICTATION,
            requested_seconds=90,
        )

        db.refresh(stale_record)
        self.assertIsInstance(replacement, DurationRateLimitAdmissionContext)
        self.assertEqual(stale_record.status, RATE_LIMIT_ADMISSION_FAILED)
        self.assertEqual(stale_record.consumed_seconds, 0)

    def test_stale_realtime_reservation_charges_at_most_reserved_time(self):
        db = self.Session()
        policy = self._create_limit(db, RATE_LIMIT_TARGET_TYPE_REALTIME, minutes=2)
        stale = admit_user_duration_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            target_type=RATE_LIMIT_TARGET_TYPE_REALTIME,
            requested_seconds=60,
        )
        self.assertIsInstance(stale, DurationRateLimitAdmissionContext)
        stale_record = db.get(RateLimitDurationAdmission, stale.admission_id)
        stale_record.admitted_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        stale_record.last_activity_at = datetime.now(timezone.utc) - timedelta(minutes=4)
        db.commit()

        replacement = admit_user_duration_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            target_type=RATE_LIMIT_TARGET_TYPE_REALTIME,
        )

        db.refresh(stale_record)
        snapshot = get_rate_limit_usage_snapshot(db, policy, "user-1")
        self.assertIsInstance(replacement, DurationRateLimitAdmissionContext)
        self.assertEqual(replacement.reserved_seconds, 60)
        self.assertEqual(stale_record.consumed_seconds, 60)
        self.assertEqual(snapshot["current_usage_seconds"], 60)

    def test_realtime_heartbeat_renews_the_open_reservation_lease(self):
        db = self.Session()
        self._create_limit(db, RATE_LIMIT_TARGET_TYPE_REALTIME, minutes=2)
        admission = admit_user_duration_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            target_type=RATE_LIMIT_TARGET_TYPE_REALTIME,
            requested_seconds=60,
        )
        self.assertIsInstance(admission, DurationRateLimitAdmissionContext)
        record = db.get(RateLimitDurationAdmission, admission.admission_id)
        record.last_activity_at = datetime.now(timezone.utc) - timedelta(minutes=4)
        db.commit()

        self.assertTrue(touch_duration_rate_limit_admission(db, admission.admission_id))
        db.refresh(record)
        self.assertGreater(
            record.last_activity_at.replace(tzinfo=timezone.utc),
            datetime.now(timezone.utc) - timedelta(seconds=10),
        )

        # The renewed reservation remains active and therefore still protects
        # its portion of the quota from a concurrent call.
        blocked = admit_user_duration_rate_limit(
            db,
            user_id="user-1",
            group_id=None,
            target_type=RATE_LIMIT_TARGET_TYPE_REALTIME,
            requested_seconds=61,
        )
        self.assertIsInstance(blocked, dict)

    def test_dictation_lease_renewer_runs_until_processing_stops(self):
        """Long provider work must heartbeat repeatedly until cancellation."""
        renewed_admission_ids = []

        async def run_renewer():
            renewal_task = asyncio.create_task(
                renew_dictation_duration_rate_limit_lease(
                    "dictation-admission-1",
                    interval_seconds=0.1,
                )
            )
            await asyncio.sleep(0.25)
            renewal_task.cancel()
            await asyncio.gather(renewal_task, return_exceptions=True)

        with patch(
            "app.llm.models._touch_duration_rate_limit_admission_with_new_session",
            side_effect=lambda admission_id: (
                renewed_admission_ids.append(admission_id) or True
            ),
        ):
            asyncio.run(run_renewer())

        self.assertGreaterEqual(len(renewed_admission_ids), 2)
        self.assertEqual(
            set(renewed_admission_ids),
            {"dictation-admission-1"},
        )


if __name__ == "__main__":
    unittest.main()
