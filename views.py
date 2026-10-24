#!/usr/bin/env python3
"""Total views for every video we know about, refreshed daily.

Outlier multipliers need a video's views next to its channel's normal views,
so this keeps one current row per video: every id in the backfill lists plus
every video the live tracker has seen. Long-form and Shorts are both kept,
with duration, so analysis can filter.

YouTube Data API v3 videos.list, 1 quota unit per 50 videos (about 80 units
a day today). Needs YOUTUBE_API_KEY.
"""
import csv, glob, json, os, sys
from datetime import datetime, timezone

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


def main():
    if not tracker.API_KEY:
        sys.exit("YOUTUBE_API_KEY is not set")
    names = {c["channel_id"]: c["name"] for c in json.load(open(os.path.join(ROOT, "channels.json")))}
    views = json.load(open(VIEWS)) if os.path.exists(VIEWS) else {}
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = sorted(set(known_ids()) | set(views))
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
