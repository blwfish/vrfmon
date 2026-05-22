"""Classify a captured frame as the VRF "feed down" placard or a live feed."""
from enum import Enum

import imagehash
from PIL import Image

from .capture import CaptureStatus


class FeedState(Enum):
    UP = "up"
    DOWN_PLACARD = "down_placard"
    DOWN_OFFLINE = "down_offline"
    ERROR = "error"
    INACCESSIBLE = "inaccessible"  # members-only / gated: state unknowable


def load_reference_hash(path):
    return imagehash.phash(Image.open(path))


def frame_distance(frame_path, reference_hash):
    # int() : imagehash returns a numpy int64, which is not JSON-serializable.
    return int(reference_hash - imagehash.phash(Image.open(frame_path)))


def decide_placard(distance, threshold):
    """Single source of truth for the placard threshold test.

    The comparison is <= : a distance exactly equal to the threshold counts
    as the placard.
    """
    return FeedState.DOWN_PLACARD if distance <= threshold else FeedState.UP


def classify(capture_result, reference_hash, threshold):
    """Map a CaptureResult to a (FeedState, phash_distance) pair."""
    if capture_result.status is CaptureStatus.MEMBERS_ONLY:
        return FeedState.INACCESSIBLE, None
    if capture_result.status is CaptureStatus.RESOLVE_FAILED:
        return FeedState.DOWN_OFFLINE, None
    if capture_result.status is CaptureStatus.GRAB_FAILED:
        return FeedState.ERROR, None
    distance = frame_distance(capture_result.frame_path, reference_hash)
    return decide_placard(distance, threshold), distance
