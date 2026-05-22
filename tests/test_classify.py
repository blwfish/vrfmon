"""Threshold tests for the placard classifier, run against real placard frames."""
import glob
import os

import imagehash
import numpy as np
import pytest
from PIL import Image

from vrfmon.capture import CaptureResult, CaptureStatus, classify_resolve_error
from vrfmon.classify import (
    FeedState, classify, decide_placard, frame_distance, load_reference_hash,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFERENCE = os.path.join(ROOT, "reference", "placard.jpg")
SAMPLES = sorted(glob.glob(os.path.join(ROOT, "reference", "placard_samples", "*.jpg")))
LIVE_SAMPLES = sorted(glob.glob(os.path.join(ROOT, "reference", "live_samples", "*.jpg")))
THRESHOLD = 10


@pytest.fixture(scope="module")
def ref_hash():
    return load_reference_hash(REFERENCE)


def test_samples_present():
    assert len(SAMPLES) == 12


def test_all_real_placard_frames_classify_as_down(ref_hash):
    # Every real placard frame must land well below the threshold.
    for path in SAMPLES:
        d = frame_distance(path, ref_hash)
        assert d <= THRESHOLD, f"{os.path.basename(path)} distance {d}"
        assert decide_placard(d, THRESHOLD) is FeedState.DOWN_PLACARD


def test_threshold_boundary():
    # decide_placard uses <= ; pin which side the boundary falls on.
    assert decide_placard(THRESHOLD - 1, THRESHOLD) is FeedState.DOWN_PLACARD
    assert decide_placard(THRESHOLD, THRESHOLD) is FeedState.DOWN_PLACARD
    assert decide_placard(THRESHOLD + 1, THRESHOLD) is FeedState.UP


def test_non_placard_images_classify_as_up(ref_hash, tmp_path):
    # Synthetic negatives must sit well above the threshold.
    cases = {
        "black": np.zeros((720, 1280, 3), np.uint8),
        "white": np.full((720, 1280, 3), 255, np.uint8),
        "noise": (np.random.default_rng(0).random((720, 1280, 3)) * 255).astype(np.uint8),
    }
    for name, arr in cases.items():
        path = tmp_path / f"{name}.jpg"
        Image.fromarray(arr).save(path)
        d = frame_distance(str(path), ref_hash)
        assert d > THRESHOLD, f"{name} distance {d} should exceed threshold"
        assert decide_placard(d, THRESHOLD) is FeedState.UP


def test_classify_maps_capture_failures(ref_hash):
    offline = classify(CaptureResult(CaptureStatus.RESOLVE_FAILED, None, "x"),
                        ref_hash, THRESHOLD)
    assert offline[0] is FeedState.DOWN_OFFLINE

    grab_fail = classify(CaptureResult(CaptureStatus.GRAB_FAILED, None, "x"),
                         ref_hash, THRESHOLD)
    assert grab_fail[0] is FeedState.ERROR

    members = classify(CaptureResult(CaptureStatus.MEMBERS_ONLY, None, "x"),
                       ref_hash, THRESHOLD)
    assert members[0] is FeedState.INACCESSIBLE

    placard = classify(CaptureResult(CaptureStatus.GOT_FRAME, SAMPLES[0], "ok"),
                       ref_hash, THRESHOLD)
    assert placard[0] is FeedState.DOWN_PLACARD


def test_classify_resolve_error_detects_members_only():
    # The members-only gate must not be mistaken for an outage.
    members = "available to this channel's members on level: VRF Express"
    assert classify_resolve_error(members) is CaptureStatus.MEMBERS_ONLY
    assert classify_resolve_error("This live event has ended") is CaptureStatus.RESOLVE_FAILED
    assert classify_resolve_error("") is CaptureStatus.RESOLVE_FAILED
    assert classify_resolve_error(None) is CaptureStatus.RESOLVE_FAILED


def test_real_live_frames_classify_as_up(ref_hash):
    # Real VRF live-camera frames must sit above the threshold (not the placard).
    assert LIVE_SAMPLES, "no live fixtures present"
    for path in LIVE_SAMPLES:
        d = frame_distance(path, ref_hash)
        assert d > THRESHOLD, f"{os.path.basename(path)} distance {d}"
        assert decide_placard(d, THRESHOLD) is FeedState.UP
