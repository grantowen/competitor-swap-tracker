#!/usr/bin/env python3
"""Total views for every video we know about, refreshed daily.

Outlier multipliers need a video's views next to its channel's normal views,
so this keeps one current row per video: every upload of the last 2 years
from each channel's uploads playlist, plus every id in the backfill lists and
every video the live tracker has seen. Long-form and Shorts are both kept,
with duration, so analysis can filter.

YouTube Data API v3 playlistItems.list + videos.list, 1 quota unit per 50
videos each (a few hundred units a day). Needs YOUTUBE_API_KEY.
"""
import csv, glob, json, os, sys
from datetime import datetime, timezone, timedelta

import tracker

ROOT = tracker.ROOT
VIEWS = os.path.join(ROOT, "data", "views.json")


def known_ids():
    ids = {}
    for path in glob.glob(os.path.join(ROOT, "backfill", "videos_batch*.txt")):
        for line in open(path):
            parts = line.split()
            for vid in parts[1:]:
                ids.setdefault(vid, None)
    snaps = os.path.join(ROOT, "data", "snapshots.csv")
    if os.path.exists(snaps):
        for r in csv.DictReader(open(snaps, encoding="utf-8", errors="ignore")):
            ids.setdefault(r["video_id"], None)
    return list(ids)


def all_uploads(channel_id, since):
    """Every upload since a date from the channel's uploads playlist, 1 quota unit per 50."""
    # the playlist is roughly newest first but not strictly (premieres, re-publishes),
    # so keep paging until two whole pages fall before the window
    ids, token, old_pages = [], None, 0
    while True:
        params = dict(part="contentDetails", playlistId="UU" + channel_id[2:], maxResults=50)
        if token:
            params["pageToken"] = token
        r = tracker.api("playlistItems", **params)
        if r is None:
            return ids
        in_window = 0
        for it in r.get("items", []):
            pub = it["contentDetails"].get("videoPublishedAt")
            if pub and datetime.fromisoformat(pub.replace("Z", "+00:00")) < since:
                continue
            ids.append(it["contentDetails"]["videoId"])
            in_window += 1
        old_pages = old_pages + 1 if in_window == 0 else 0
        token = r.get("nextPageToken")
        if not token or old_pages >= 2:
            return ids


def main():
    if not tracker.API_KEY:
        sys.exit("YOUTUBE_API_KEY is not set")
    channels = json.load(open(os.path.join(ROOT, "channels.json")))
    names = {c["channel_id"]: c["name"] for c in channels}
    views = json.load(open(VIEWS)) if os.path.exists(VIEWS) else {}
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # the backfill lists came from third-party tools and miss uploads; the uploads
    # playlist is the complete record, so every video of the last 2 years is kept
    since = datetime.now(timezone.utc) - timedelta(days=2 * 365 + 30)
    listed = [v for c in channels for v in all_uploads(c["channel_id"], since)]
    ids = sorted(set(known_ids()) | set(views) | set(listed))
    got = missing = 0
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        r = tracker.api("videos", part="snippet,statistics,contentDetails", id=",".join(chunk), maxResults=50)
        if r is None:
            sys.exit(f"API call failed at {i}/{len(ids)}; {got} refreshed so far, nothing written")
        returned = set()
        for v in r.get("items", []):
            s, st = v["snippet"], v.get("statistics", {})
            returned.add(v["id"])
            views[v["id"]] = {
                "channel": names.get(s["channelId"], s.get("channelTitle", "")),
                "channel_id": s["channelId"],
                "published": s["publishedAt"],
                "title": s["title"].strip(),
                "duration_s": tracker.duration_seconds(v["contentDetails"].get("duration")),
                "views": int(st["viewCount"]) if "viewCount" in st else None,
                "likes": int(st["likeCount"]) if "likeCount" in st else None,
                "comments": int(st["commentCount"]) if "commentCount" in st else None,
                "captured_utc": stamp,
            }
            got += 1
        # deleted or private videos stop coming back; keep the last row and mark it
        for vid in set(chunk) - returned:
            if vid in views:
                views[vid]["gone_since"] = views[vid].get("gone_since") or stamp
            missing += 1
    json.dump(views, open(VIEWS, "w"), indent=1, ensure_ascii=False, sort_keys=True)
    print(f"{stamp}: {got} videos refreshed, {missing} not returned (deleted or private)")


if __name__ == "__main__":
    main()
