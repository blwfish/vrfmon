"""SQLite store for YouTube live chat messages, plus the yt-dlp action parser.

yt-dlp's ``live_chat`` subtitle format emits one JSON object per line, each
wrapping a list of actions from YouTube's innertube live chat API
(``replayChatItemAction.actions[]``). One action is one of:

  - ``addChatItemAction``    - a new item to render. ``item`` holds exactly one
    renderer key; the renderer type determines what the item is:
        liveChatTextMessageRenderer               -> extracted (action_type "text")
        liveChatViewerEngagementMessageRenderer    -> extracted ("engagement")
        liveChatMembershipItemRenderer             -> extracted ("membership")
        liveChatPaidMessageRenderer  (Super Chat)  -> extracted ("paid_message")
        liveChatPaidStickerRenderer  (Super Sticker)-> extracted ("paid_sticker",
                                                        no message text - it's a sticker)
        liveChatModeChangeMessageRenderer          -> extracted ("mode_change")
        liveChatPlaceholderItemRenderer            -> extracted ("placeholder",
                                                        no content - a slot reserved
                                                        for a message pending a spam check)
        anything else                              -> raw-only, action_type
                                                        "other:<rendererKey>", logged once
  - ``removeChatItemAction``                  -> extracted ("delete_item"): a prior
                                                  message_id was removed
  - ``markChatItemAsDeletedAction``           -> extracted ("delete_item"), same shape
  - ``markChatItemsByAuthorAsDeletedAction``  -> extracted ("delete_author"): a ban
  - anything else (e.g. replaceChatItemAction)-> raw-only, action_type "other:<key>"

In every case the action's full JSON is kept in the ``raw`` column, so nothing
is ever silently lost even when a renderer/action type isn't specifically
extracted into its own columns.

Dropped, not stored anywhere (judged to carry no transcript value):
  - ``clickTrackingParams`` on every action - internal UI telemetry
  - image/thumbnail URLs (authorPhoto, badge icons, sticker/emoji images)
  - ``videoOffsetTimeMsec`` / ``isLive`` on the outer wrapper - replay-sync
    fields, meaningless for a live-only wall-clock transcript
  - link targets inside message runs (``navigationEndpoint``) - the run's
    visible text is kept, only the click-through URL is dropped
"""
import datetime
import json
import sqlite3


_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id                INTEGER PRIMARY KEY,
    ts                TEXT    NOT NULL,
    ts_epoch          INTEGER NOT NULL,
    camera            TEXT    NOT NULL,
    video_id          TEXT,
    action_type       TEXT    NOT NULL,
    message_id        TEXT,
    author_name       TEXT,
    author_channel_id TEXT,
    author_badges     TEXT,
    message           TEXT,
    amount            TEXT,
    raw               TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_camera_ts ON messages(camera, ts_epoch);
CREATE INDEX IF NOT EXISTS messages_ts_epoch  ON messages(ts_epoch);
"""

_RENDERER_ACTION_TYPES = {
    "liveChatTextMessageRenderer": "text",
    "liveChatViewerEngagementMessageRenderer": "engagement",
    "liveChatMembershipItemRenderer": "membership",
    "liveChatPaidMessageRenderer": "paid_message",
    "liveChatPaidStickerRenderer": "paid_sticker",
    "liveChatModeChangeMessageRenderer": "mode_change",
    "liveChatPlaceholderItemRenderer": "placeholder",
}

_TOP_ACTION_TYPES = {
    "removeChatItemAction": "delete_item",
    "markChatItemAsDeletedAction": "delete_item",
    "markChatItemsByAuthorAsDeletedAction": "delete_author",
}

_warned_unknown = set()


def _warn_once(kind, key):
    tag = (kind, key)
    if tag not in _warned_unknown:
        _warned_unknown.add(tag)
        print(f"[chatlog] unrecognized {kind}: {key!r} (stored raw-only)")


def open_db(path):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def insert_message(conn, row):
    conn.execute(
        "INSERT INTO messages(ts, ts_epoch, camera, video_id, action_type,"
        " message_id, author_name, author_channel_id, author_badges, message,"
        " amount, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (row["ts"], row["ts_epoch"], row["camera"], row["video_id"],
         row["action_type"], row["message_id"], row["author_name"],
         row["author_channel_id"], row["author_badges"], row["message"],
         row["amount"], row["raw"]),
    )


def prune_older_than(conn, cutoff_epoch):
    """Delete messages older than cutoff_epoch (unix seconds). Returns rows deleted."""
    cur = conn.execute("DELETE FROM messages WHERE ts_epoch < ?", (cutoff_epoch,))
    conn.commit()
    return cur.rowcount


def _run_text(run):
    if "text" in run:
        return run["text"]
    if "emoji" in run:
        emoji = run["emoji"] or {}
        shortcuts = emoji.get("shortcuts") or []
        return shortcuts[0] if shortcuts else emoji.get("emojiId", "")
    return ""


def _parse_runs(field):
    """Flatten a YouTube {"runs": [...]} / {"simpleText": ...} field to plain text."""
    if not isinstance(field, dict):
        return None
    runs = field.get("runs")
    if runs is not None:
        return "".join(_run_text(r) for r in runs)
    simple = field.get("simpleText")
    return simple if simple is not None else None


def _badge_titles(badges):
    titles = []
    for badge in badges or []:
        for renderer in badge.values():
            tooltip = (renderer or {}).get("tooltip")
            if tooltip:
                titles.append(tooltip)
    return titles


def _ts_from_usec(usec, fallback_epoch):
    if usec is None:
        return fallback_epoch
    try:
        return int(int(usec) / 1_000_000)
    except (TypeError, ValueError):
        return fallback_epoch


def _iso_utc(epoch):
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).isoformat(timespec="seconds")


def _base_row(camera, video_id, action_type, ts_epoch, raw, **fields):
    row = {
        "ts": _iso_utc(ts_epoch),
        "ts_epoch": ts_epoch,
        "camera": camera,
        "video_id": video_id,
        "action_type": action_type,
        "message_id": None,
        "author_name": None,
        "author_channel_id": None,
        "author_badges": None,
        "message": None,
        "amount": None,
        "raw": raw,
    }
    row.update(fields)
    return row


def _row_from_renderer(renderer_key, renderer, camera, video_id, raw, ingest_epoch):
    action_type = _RENDERER_ACTION_TYPES.get(renderer_key)
    if action_type is None:
        _warn_once("renderer type", renderer_key)
        action_type = f"other:{renderer_key}"

    ts_epoch = _ts_from_usec(renderer.get("timestampUsec"), ingest_epoch)
    author_name = (renderer.get("authorName") or {}).get("simpleText")
    badges = _badge_titles(renderer.get("authorBadges"))

    message = None
    amount = None
    if action_type == "text":
        message = _parse_runs(renderer.get("message"))
    elif action_type == "engagement":
        message = _parse_runs(renderer.get("message"))
    elif action_type == "membership":
        primary = _parse_runs(renderer.get("headerPrimaryText"))
        secondary = _parse_runs(renderer.get("headerSubtext"))
        message = " / ".join(p for p in (primary, secondary) if p) or None
    elif action_type == "paid_message":
        message = _parse_runs(renderer.get("message"))
        amount = (renderer.get("purchaseAmountText") or {}).get("simpleText")
    elif action_type == "paid_sticker":
        amount = (renderer.get("purchaseAmountText") or {}).get("simpleText")
    elif action_type == "mode_change":
        message = _parse_runs(renderer.get("text"))
    # placeholder / other:* -> no content fields, raw column has everything

    return _base_row(
        camera, video_id, action_type, ts_epoch, raw,
        message_id=renderer.get("id"),
        author_name=author_name,
        author_channel_id=renderer.get("authorExternalChannelId"),
        author_badges=json.dumps(badges) if badges else None,
        message=message,
        amount=amount,
    )


def parse_action(action, camera, video_id, ingest_epoch):
    """Turn one yt-dlp live_chat action dict into a `messages` row, or None to skip.

    ingest_epoch is the unix-seconds wall-clock time the line was read; it's
    used as the timestamp only when the action itself carries none (e.g.
    deletions), or as a fallback if timestampUsec is missing/malformed.
    """
    if not isinstance(action, dict):
        return None
    keys = [k for k in action if k != "clickTrackingParams"]
    if not keys:
        return None
    kind = keys[0]
    raw = json.dumps(action, separators=(",", ":"))

    if kind == "addChatItemAction":
        item = (action[kind] or {}).get("item") or {}
        item_keys = list(item.keys())
        if not item_keys:
            return None
        renderer_key = item_keys[0]
        renderer = item[renderer_key] or {}
        return _row_from_renderer(renderer_key, renderer, camera, video_id, raw, ingest_epoch)

    if kind in _TOP_ACTION_TYPES:
        body = action[kind] or {}
        return _base_row(
            camera, video_id, _TOP_ACTION_TYPES[kind], ingest_epoch, raw,
            message_id=body.get("targetItemId"),
            author_channel_id=body.get("externalChannelId"),
        )

    _warn_once("action type", kind)
    return _base_row(camera, video_id, f"other:{kind}", ingest_epoch, raw)
