"""Tests for vrfmon/chatlog.py — yt-dlp live_chat action parsing and storage."""
import json

import pytest

from vrfmon import chatlog
from vrfmon.chatlog import insert_message, open_db, parse_action, prune_older_than


CAMERA = "Ashland, VA (PTZ)"
VIDEO_ID = "_eArnSLGhSo"
INGEST_EPOCH = 1_700_000_000


# --- addChatItemAction: liveChatTextMessageRenderer ---

def _text_action(**overrides):
    action = {
        "clickTrackingParams": "abc",
        "addChatItemAction": {
            "item": {
                "liveChatTextMessageRenderer": {
                    "message": {"runs": [{"text": "hello from Ashland"}]},
                    "authorName": {"simpleText": "@BennettTheP42Guy"},
                    "authorExternalChannelId": "UCHTfAvwHyJb6LzPDmG86SNQ",
                    "id": "ChwKGkNQbUJuNS1ZdnBZREZUZmZQd1FkcEswd2ZB",
                    "timestampUsec": "1787743695259621",
                }
            }
        },
    }
    action["addChatItemAction"]["item"]["liveChatTextMessageRenderer"].update(overrides)
    return action


def test_text_message_extracted_fields():
    row = parse_action(_text_action(), CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["action_type"] == "text"
    assert row["message"] == "hello from Ashland"
    assert row["author_name"] == "@BennettTheP42Guy"
    assert row["author_channel_id"] == "UCHTfAvwHyJb6LzPDmG86SNQ"
    assert row["message_id"] == "ChwKGkNQbUJuNS1ZdnBZREZUZmZQd1FkcEswd2ZB"
    assert row["camera"] == CAMERA
    assert row["video_id"] == VIDEO_ID
    assert row["author_badges"] is None
    assert row["amount"] is None
    assert json.loads(row["raw"])["addChatItemAction"]["item"]["liveChatTextMessageRenderer"]["message"] == \
        {"runs": [{"text": "hello from Ashland"}]}


def test_timestamp_usec_exact_multiple_of_a_second():
    row = parse_action(_text_action(timestampUsec="2000000"), CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["ts_epoch"] == 2


def test_timestamp_usec_truncates_toward_zero_on_remainder():
    row = parse_action(_text_action(timestampUsec="2999999"), CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["ts_epoch"] == 2


def test_timestamp_usec_missing_falls_back_to_ingest_time():
    action = _text_action()
    del action["addChatItemAction"]["item"]["liveChatTextMessageRenderer"]["timestampUsec"]
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["ts_epoch"] == INGEST_EPOCH


def test_timestamp_usec_malformed_falls_back_to_ingest_time():
    row = parse_action(_text_action(timestampUsec="not-a-number"), CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["ts_epoch"] == INGEST_EPOCH


def test_multiple_text_runs_concatenated_in_order():
    action = _text_action(message={"runs": [{"text": "part one "}, {"text": "part two"}]})
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["message"] == "part one part two"


def test_emoji_run_uses_first_shortcut():
    action = _text_action(message={"runs": [
        {"text": "nice "},
        {"emoji": {"emojiId": "abc:123", "shortcuts": [":train:", ":choo_choo:"]}},
    ]})
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["message"] == "nice :train:"


def test_emoji_run_without_shortcuts_falls_back_to_emoji_id():
    action = _text_action(message={"runs": [{"emoji": {"emojiId": "custom:1234", "shortcuts": []}}]})
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["message"] == "custom:1234"


def test_author_badges_joined_as_json_list():
    action = _text_action(authorBadges=[
        {"liveChatAuthorBadgeRenderer": {"tooltip": "Member (2 months)"}},
        {"liveChatAuthorBadgeRenderer": {"tooltip": "Moderator"}},
    ])
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert json.loads(row["author_badges"]) == ["Member (2 months)", "Moderator"]


def test_author_badge_without_tooltip_is_dropped_not_crashed():
    action = _text_action(authorBadges=[{"liveChatAuthorBadgeRenderer": {"icon": {"iconType": "MODERATOR"}}}])
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["author_badges"] is None


# --- addChatItemAction: other renderer types ---

def test_viewer_engagement_message_extracted():
    action = {
        "addChatItemAction": {
            "item": {
                "liveChatViewerEngagementMessageRenderer": {
                    "message": {"runs": [{"text": "Welcome to live chat!"}]},
                    "timestampUsec": "1700000001000000",
                }
            }
        }
    }
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["action_type"] == "engagement"
    assert row["message"] == "Welcome to live chat!"


def test_membership_renderer_joins_header_texts():
    action = {
        "addChatItemAction": {
            "item": {
                "liveChatMembershipItemRenderer": {
                    "headerPrimaryText": {"runs": [{"text": "Welcome new member!"}]},
                    "headerSubtext": {"simpleText": "@someone joined"},
                    "authorName": {"simpleText": "@someone"},
                    "timestampUsec": "1700000002000000",
                }
            }
        }
    }
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["action_type"] == "membership"
    assert row["message"] == "Welcome new member! / @someone joined"


def test_membership_renderer_missing_subtext_omits_separator():
    action = {
        "addChatItemAction": {
            "item": {
                "liveChatMembershipItemRenderer": {
                    "headerPrimaryText": {"simpleText": "Welcome new member!"},
                    "timestampUsec": "1700000002000000",
                }
            }
        }
    }
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["message"] == "Welcome new member!"


def test_paid_message_extracts_amount_and_text():
    action = {
        "addChatItemAction": {
            "item": {
                "liveChatPaidMessageRenderer": {
                    "message": {"runs": [{"text": "thanks for the stream"}]},
                    "purchaseAmountText": {"simpleText": "$5.00"},
                    "authorName": {"simpleText": "@bigfan"},
                    "timestampUsec": "1700000003000000",
                }
            }
        }
    }
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["action_type"] == "paid_message"
    assert row["amount"] == "$5.00"
    assert row["message"] == "thanks for the stream"


def test_paid_sticker_has_amount_but_no_message():
    action = {
        "addChatItemAction": {
            "item": {
                "liveChatPaidStickerRenderer": {
                    "purchaseAmountText": {"simpleText": "$2.00"},
                    "authorName": {"simpleText": "@bigfan"},
                    "timestampUsec": "1700000004000000",
                }
            }
        }
    }
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["action_type"] == "paid_sticker"
    assert row["amount"] == "$2.00"
    assert row["message"] is None


def test_placeholder_renderer_has_no_content():
    action = {
        "addChatItemAction": {
            "item": {"liveChatPlaceholderItemRenderer": {"timestampUsec": "1700000005000000"}}
        }
    }
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["action_type"] == "placeholder"
    assert row["message"] is None


def test_unrecognized_renderer_type_is_raw_only_not_dropped():
    action = {
        "addChatItemAction": {
            "item": {"liveChatSomeNewRendererType": {"timestampUsec": "1700000006000000", "foo": "bar"}}
        }
    }
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["action_type"] == "other:liveChatSomeNewRendererType"
    assert row["message"] is None
    assert "foo" in row["raw"]


# --- top-level actions besides addChatItemAction ---

def test_remove_chat_item_action():
    action = {"removeChatItemAction": {"targetItemId": "abc123"}}
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["action_type"] == "delete_item"
    assert row["message_id"] == "abc123"
    assert row["ts_epoch"] == INGEST_EPOCH  # no timestamp on this action; uses ingest time


def test_mark_chat_item_as_deleted_action():
    action = {"markChatItemAsDeletedAction": {"targetItemId": "def456"}}
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["action_type"] == "delete_item"
    assert row["message_id"] == "def456"


def test_mark_chat_items_by_author_as_deleted_action():
    action = {"markChatItemsByAuthorAsDeletedAction": {"externalChannelId": "UCabc"}}
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["action_type"] == "delete_author"
    assert row["author_channel_id"] == "UCabc"


def test_unrecognized_top_level_action_is_raw_only_not_dropped():
    action = {"replaceChatItemAction": {"targetItemId": "xyz", "replacementItem": {}}}
    row = parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH)
    assert row["action_type"] == "other:replaceChatItemAction"
    assert "replacementItem" in row["raw"]


# --- ambiguous / degenerate inputs ---

def test_action_with_only_click_tracking_params_is_skipped():
    assert parse_action({"clickTrackingParams": "abc"}, CAMERA, VIDEO_ID, INGEST_EPOCH) is None


def test_empty_action_is_skipped():
    assert parse_action({}, CAMERA, VIDEO_ID, INGEST_EPOCH) is None


def test_add_chat_item_action_with_empty_item_is_skipped():
    action = {"addChatItemAction": {"item": {}}}
    assert parse_action(action, CAMERA, VIDEO_ID, INGEST_EPOCH) is None


def test_non_dict_input_is_skipped():
    assert parse_action(None, CAMERA, VIDEO_ID, INGEST_EPOCH) is None
    assert parse_action("not a dict", CAMERA, VIDEO_ID, INGEST_EPOCH) is None


# --- storage: insert + prune ---

@pytest.fixture
def db(tmp_path):
    conn = open_db(str(tmp_path / "chat.db"))
    yield conn
    conn.close()


def _insert_at(db, ts_epoch, action_type="text"):
    action = {"removeChatItemAction": {"targetItemId": "x"}}  # no timestamp of its own
    row = parse_action(action, CAMERA, VIDEO_ID, ts_epoch)
    row["action_type"] = action_type
    insert_message(db, row)
    db.commit()
    return row


def test_insert_and_read_back(db):
    _insert_at(db, 1000)
    rows = db.execute("SELECT * FROM messages").fetchall()
    assert len(rows) == 1
    assert rows[0]["camera"] == CAMERA


def test_prune_deletes_strictly_older_than_cutoff(db):
    _insert_at(db, 1000)  # older than cutoff -> deleted
    _insert_at(db, 2000)  # exactly at cutoff -> kept
    _insert_at(db, 3000)  # newer than cutoff -> kept

    deleted = prune_older_than(db, cutoff_epoch=2000)

    assert deleted == 1
    remaining = sorted(r["ts_epoch"] for r in db.execute("SELECT ts_epoch FROM messages").fetchall())
    assert remaining == [2000, 3000]


def test_prune_with_no_matching_rows_deletes_nothing(db):
    _insert_at(db, 5000)
    deleted = prune_older_than(db, cutoff_epoch=1000)
    assert deleted == 0
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
