#!/usr/bin/env python3
"""Competitor title and thumbnail swap tracker.

Every run: read each channel's long-form uploads feed, and for every video
published in the last TRACK_DAYS, record its current title and thumbnail
version. When either changes, log the old and new version as a swap.

A swap is a competitor's own test result: the version they dropped next to
the version they kept, on the same video. Those pairs are the data a title
scorer can be tested against.

Standard library only. Sources:
  list + title: YouTube Data API v3 (uploads playlist + videos.list) when the
                YOUTUBE_API_KEY env var is set; about 100 quota units a run
  fallback:     youtube.com/feeds/videos.xml + youtube.com/oembed (no key)
  thumbnail:    i.ytimg.com/vi/<id>/hqdefault.jpg  (ETag changes when it is replaced)
Total views per video are kept by views.py, not here.
"""
import csv, json, os, re, sys, time, html
import urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
THUMBS = os.path.join(DATA, "thumbs")
STATE = os.path.join(DATA, "state.json")
SNAPSHOTS = os.path.join(DATA, "snapshots.csv")
SWAPS = os.path.join(DATA, "swaps.csv")
TRACK_DAYS = 30
SHORT_MAX_SECONDS = 180  # Shorts run up to 3 minutes; anything this short is skipped
UA = {"User-Agent": "Mozilla/5.0 (competitor-swap-tracker)"}
API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
API = "https://www.googleapis.com/youtube/v3/"


def get(url, method="GET", final_404=True, tries=3):
    req = urllib.request.Request(url, headers=UA, method=method)
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, dict(r.headers), (r.read() if method == "GET" else b"")
        except urllib.error.HTTPError as e:
            # YouTube's feeds return 404/500 for minutes at a time when they
            # throttle a server, so a feed 404 is retried rather than final
            if e.code in (404, 410) and final_404:
                return e.code, dict(e.headers or {}), b""
        except Exception:
            pass
        time.sleep(3 * (attempt + 1))
    return None, {}, b""


def feed(channel_id):
    # YouTube's documented channel feed (last 15 uploads, Shorts included and
    # filtered out below). It returns 404/500 intermittently, so it is retried.
    # The undocumented long-form-only playlist feed is a fallback only.
    status, _, body = get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}", final_404=False, tries=2)
    if status != 200:
        status, _, body = get(f"https://www.youtube.com/feeds/videos.xml?playlist_id=UULF{channel_id[2:]}", tries=1)
        if status != 200:
            return None
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", body.decode("utf-8", "replace"), re.S):
        if "/shorts/" in e:
            continue
        vid = re.search(r"<yt:videoId>(.*?)<", e).group(1)
        pub = re.search(r"<published>(.*?)<", e).group(1)
        title = html.unescape(re.search(r"<title>(.*?)</title>", e, re.S).group(1)).strip()
        out.append((vid, pub, title))
    return out


def api(path, **params):
    params["key"] = API_KEY
    query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    status, _, body = get(f"{API}{path}?{query}", final_404=True, tries=3)
    if status != 200:
        return None
    return json.loads(body)


def duration_seconds(iso):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def api_videos(ids):
    """Title, publish time and length for up to any number of ids, 1 quota unit per 50."""
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 50):
        r = api("videos", part="snippet,contentDetails", id=",".join(ids[i:i + 50]), maxResults=50)
        if r is None:
            return None
        for v in r.get("items", []):
            out[v["id"]] = (v["snippet"]["publishedAt"], v["snippet"]["title"].strip(),
                            duration_seconds(v["contentDetails"].get("duration")))
    return out


def api_recent(channel_id, cutoff):
    """Ids uploaded since cutoff, from the channel's uploads playlist (newest first)."""
    ids, token = [], None
    for _ in range(4):
        params = dict(part="contentDetails", playlistId="UU" + channel_id[2:], maxResults=50)
        if token:
            params["pageToken"] = token
        r = api("playlistItems", **params)
        if r is None:
            return None
        older = False
        for it in r.get("items", []):
            pub = it["contentDetails"].get("videoPublishedAt")
            if pub and datetime.fromisoformat(pub.replace("Z", "+00:00")) < cutoff:
                older = True
                break
            ids.append(it["contentDetails"]["videoId"])
        token = r.get("nextPageToken")
        if older or not token:
            break
    return ids


def api_entries(channel_id, known, cutoff):
    """(id, published, title) for long-form videos in the window, or None if the API failed."""
    recent = api_recent(channel_id, cutoff)
    if recent is None:
        return None
    details = api_videos(dict.fromkeys(recent + known))
    if details is None:
        return None
    return [(vid, pub, title) for vid, (pub, title, secs) in details.items()
            if secs is None or secs > SHORT_MAX_SECONDS]


def oembed_title(vid):
    status, _, body = get(f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json")
    if status != 200:
        return None
    try:
        return json.loads(body)["title"].strip()
    except Exception:
        return None


def thumb_version(vid):
    status, headers, body = get(f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
    if status != 200:
        return None, None
    etag = (headers.get("ETag") or headers.get("etag") or "").strip('"')
    return etag or None, body


SAME_IMAGE = 2.0  # mean pixel difference (0-255) below which two thumbnails are the same picture


def image_diff(vid, old, new):
    """Mean grayscale difference between two saved thumbnail versions.

    Creators often re-upload the identical image, which changes the ETag.
    Returns a large number when the comparison cannot be made, so the change
    is still logged as a swap rather than silently dropped.
    """
    try:
        from PIL import Image, ImageChops, ImageStat
        a = Image.open(os.path.join(THUMBS, f"{vid}_{old}.jpg")).convert("L").resize((64, 48))
        b = Image.open(os.path.join(THUMBS, f"{vid}_{new}.jpg")).convert("L").resize((64, 48))
        return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]
    except Exception:
        return 255.0


def append(path, header, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


def main():
    os.makedirs(THUMBS, exist_ok=True)
    channels = json.load(open(os.path.join(ROOT, "channels.json")))
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff = now - timedelta(days=TRACK_DAYS)
    seen = swaps = failed = 0

    # the API answers with the current title directly, so oEmbed is only needed on the feed path
    feeds, from_api = {}, set()
    if API_KEY:
        for ch in channels:
            known = [v for v, s in state.items() if s.get("channel") == ch["name"]]
            entries = api_entries(ch["channel_id"], known, cutoff)
            if entries is not None:
                feeds[ch["channel_id"]] = entries
                from_api.add(ch["channel_id"])
        if len(from_api) < len(channels):
            print(f"API failed for {len(channels) - len(from_api)} channels, using the feed for them", file=sys.stderr)

    # feed fallback; if YouTube is throttling, wait and retry the misses
    for attempt in range(3):
        pending = [ch for ch in channels if ch["channel_id"] not in feeds]
        if not pending:
            break
        if attempt:
            print(f"retrying {len(pending)} feeds in 60s", file=sys.stderr)
            time.sleep(60)
        for ch in pending:
            entries = feed(ch["channel_id"])
            if entries is not None:
                feeds[ch["channel_id"]] = entries

    for ch in channels:
        entries = feeds.get(ch["channel_id"])
        if entries is None:
            failed += 1
            print(f"feed failed: {ch['name']} (still checking its known videos)", file=sys.stderr)
            entries = []
        # the feed only lists the last 15 uploads, so a busy channel's video can
        # drop off within days; keep checking every known video for the full window
        listed = {e[0] for e in entries}
        entries = entries + [(v, s["published"], s.get("title", "")) for v, s in state.items()
                             if s.get("channel") == ch["name"] and v not in listed]
        for vid, pub, feed_title in entries:
            published = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            if published < cutoff:
                continue
            if ch["channel_id"] in from_api and vid in listed:
                title = feed_title
            else:
                title = oembed_title(vid) or feed_title
            etag, image = thumb_version(vid)
            hours = round((now - published).total_seconds() / 3600, 1)
            # the thumbnail ETag is the Unix time the current thumbnail was set
            set_after = round((int(etag) - published.timestamp()) / 3600, 1) if etag and etag.isdigit() else ""
            seen += 1
            append(SNAPSHOTS,
                   ["captured_utc", "channel", "lane", "video_id", "published_utc", "hours_since_publish", "title", "feed_title", "thumb_version", "thumb_set_hours_after_publish"],
                   [stamp, ch["name"], ch["lane"], vid, pub, hours, title, feed_title, etag or "", set_after])

            prev = state.get(vid)
            if etag and image and not os.path.exists(os.path.join(THUMBS, f"{vid}_{etag}.jpg")):
                open(os.path.join(THUMBS, f"{vid}_{etag}.jpg"), "wb").write(image)
            if prev:
                for kind, old, new in (("title", prev["title"], title), ("thumbnail", prev["thumb_version"], etag)):
                    if new and old and new != old:
                        if kind == "thumbnail" and image_diff(vid, old, new) < SAME_IMAGE:
                            kind = "thumbnail_resaved"  # same picture re-uploaded, not a swap
                        else:
                            swaps += 1
                        append(SWAPS,
                               ["detected_utc", "channel", "lane", "video_id", "kind", "old", "new", "old_first_seen_utc", "hours_since_publish"],
                               [stamp, ch["name"], ch["lane"], vid, kind, old, new, prev["title_since" if kind == "title" else "thumbnail_since"], hours])
            entry = prev or {"channel": ch["name"], "published": pub}
            if not prev or title != prev["title"]:
                entry["title"], entry["title_since"] = title, stamp
            if etag and (not prev or etag != prev.get("thumb_version")):
                entry["thumb_version"], entry["thumbnail_since"] = etag, stamp
            entry.setdefault("thumb_version", etag)
            entry.setdefault("thumbnail_since", stamp)
            entry["last_seen"] = stamp
            state[vid] = entry

    # forget videos older than the window so state stays small
    state = {v: s for v, s in state.items()
             if datetime.fromisoformat(s["published"].replace("Z", "+00:00")) >= cutoff - timedelta(days=2)}
    json.dump(state, open(STATE, "w"), indent=1, ensure_ascii=False, sort_keys=True)
    print(f"{stamp}: {seen} videos checked across {len(channels) - failed}/{len(channels)} channels, {swaps} swaps logged")
    if failed == len(channels) and seen == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
