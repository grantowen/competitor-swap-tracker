#!/usr/bin/env python3
"""Historical swap backfill from the Internet Archive (Wayback Machine).

For each video in a list, read the Archive's saved copies of its YouTube page
and of its thumbnail images. Every title or thumbnail that differs from an
earlier copy becomes a dropped/kept pair, labeled source=wayback.

Polite by design: one request at a time per shard, a pause between requests,
long back-off when the Archive asks us to slow down, logged out, no keys.
Resumable: videos already in the output are skipped, and the run stops itself
before the job time limit so finished work is always committed.

Usage: python backfill.py <list1.txt,list2.txt> <shard> <shards> [minutes]
Each list line: <channel_id> <video_id> <video_id> ...
"""
import csv, gzip, html, json, os, re, sys, time
import urllib.request, urllib.error, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "archive")
PAUSE = 1.0          # seconds between Archive requests
EARLY_TITLES = 1     # readable early copies wanted (titles change early)
MAX_PAGE_READS = 9   # cap on saved pages fetched per video (many are bot walls)
UA = {"User-Agent": "competitor-swap-tracker/1.0 (research; contact via GitHub grantowen)"}
START = time.time()
STATS = {"requests": 0, "ok": 0, "codes": {}, "errors": {}, "wait_s": 0.0, "request_s": 0.0}


def get(url, binary=False, tries=5):
    for attempt in range(tries):
        time.sleep(PAUSE)
        STATS["wait_s"] += PAUSE
        STATS["requests"] += 1
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                STATS["request_s"] += time.time() - t0
                STATS["ok"] += 1
                while body[:2] == b"\x1f\x8b":  # the Archive sometimes serves pages still gzipped
                    body = gzip.decompress(body)
                return body if binary else body.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            STATS["request_s"] += time.time() - t0
            STATS["codes"][e.code] = STATS["codes"].get(e.code, 0) + 1
            if e.code in (404, 403):
                return None
            wait = 60 if e.code in (429, 503) else 10 * (attempt + 1)
        except Exception as e:
            STATS["request_s"] += time.time() - t0
            k = type(e).__name__
            STATS["errors"][k] = STATS["errors"].get(k, 0) + 1
            wait = 10 * (attempt + 1)
        STATS["wait_s"] += wait
        time.sleep(wait)
    return None


def cdx(url, extra=""):
    q = f"https://web.archive.org/cdx/search/cdx?url={urllib.parse.quote(url, safe='')}&output=json&filter=statuscode:200{extra}"
    body = get(q)
    if not body:
        return []
    try:
        rows = json.loads(body)
    except Exception:
        return []
    return [dict(zip(rows[0], r)) for r in rows[1:]] if rows else []


def page_title(text):
    if not text:
        return None
    m = re.search(r'"videoDetails":\{"videoId":"[^"]+","title":"((?:[^"\\]|\\.)*)"', text)
    if m:
        try:
            return json.loads('"' + m.group(1) + '"').strip() or None
        except Exception:
            pass
    # pages behind YouTube's bot notice still carry the title in the player overlay
    for pat in (r'"playerOverlayVideoDetailsRenderer":\{"title":\{"simpleText":"((?:[^"\\]|\\.)*)"',
                r'"videoPrimaryInfoRenderer":\{"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"'):
        m = re.search(pat, text)
        if m:
            try:
                t = json.loads('"' + m.group(1) + '"').strip()
                if t:
                    return t
            except Exception:
                pass
    for pat in (r'<meta name="title" content="([^"]+)"', r'<meta property="og:title" content="([^"]+)"'):
        m = re.search(pat, text)
        if m and m.group(1).strip():
            return html.unescape(m.group(1)).strip()
    return None  # bot wall or empty shell page


def live_title(vid):
    body = get(f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json", tries=2)
    try:
        return json.loads(body)["title"].strip()
    except Exception:
        return None


def image_diff(a_path, b_path):
    try:
        from PIL import Image, ImageChops, ImageStat
        a = Image.open(a_path).convert("L").resize((64, 48))
        b = Image.open(b_path).convert("L").resize((64, 48))
        return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]
    except Exception:
        return None


def append(path, header, rows):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)


def process(channel_id, vid, outdir, thumbdir):
    titles = []  # (timestamp, title, source)
    snaps = cdx(f"youtube.com/watch?v={vid}", "&collapse=timestamp:8&fl=timestamp")
    stamps = [s["timestamp"] for s in snaps]
    reads = 0
    for ts in stamps:  # walk forward from the earliest copy until enough readable ones
        if reads >= MAX_PAGE_READS - 1 or sum(1 for t in titles) >= EARLY_TITLES:
            break
        reads += 1
        t = page_title(get(f"https://web.archive.org/web/{ts}id_/https://www.youtube.com/watch?v={vid}"))
        if t:
            titles.append((ts, t, "wayback"))
    for ts in reversed(stamps):  # and one readable late copy
        if reads >= MAX_PAGE_READS or (titles and ts <= titles[-1][0]):
            break
        reads += 1
        t = page_title(get(f"https://web.archive.org/web/{ts}id_/https://www.youtube.com/watch?v={vid}"))
        if t:
            titles.append((ts, t, "wayback"))
            break
    now = live_title(vid)
    if now:
        titles.append((time.strftime("%Y%m%d%H%M%S", time.gmtime()), now, "live"))

    thumbs = []  # (stem, timestamp, path): earliest and latest distinct image of one size
    versions = {}
    for s in cdx(f"i.ytimg.com/vi/{vid}/", "&matchType=prefix&collapse=digest&fl=timestamp,original,digest"):
        stem = re.sub(r"\?.*$", "", s["original"]).rsplit("/", 1)[-1].split(".")[0]
        versions.setdefault(stem, []).append(s)
    stem = next((k for k in ("hqdefault", "hq720", "maxresdefault", "sddefault", "mqdefault") if len(versions.get(k, [])) >= 2), None)
    if stem:
        vs = sorted(versions[stem], key=lambda s: s["timestamp"])
        for s in (vs[0], vs[-1]):
            img = get(f"https://web.archive.org/web/{s['timestamp']}id_/{s['original']}", binary=True)
            if img and len(img) > 2000:
                path = os.path.join(thumbdir, f"{vid}_{stem}_{s['timestamp']}.jpg")
                open(path, "wb").write(img)
                thumbs.append((stem, s["timestamp"], path))

    append(os.path.join(outdir, "titles.csv"), ["channel_id", "video_id", "seen", "title", "source"],
           [(channel_id, vid, ts, t, src) for ts, t, src in titles])
    pairs = []
    seq = [x for i, x in enumerate(titles) if i == 0 or x[1] != titles[i - 1][1]]
    for a, b in zip(seq, seq[1:]):
        pairs.append((channel_id, vid, "title", a[1], b[1], a[0], b[0], "wayback" if b[2] == "wayback" else "wayback+live"))
    for stem in dict.fromkeys(t[0] for t in thumbs):
        group = sorted((t for t in thumbs if t[0] == stem), key=lambda t: t[1])
        for a, b in zip(group, group[1:]):
            d = image_diff(a[2], b[2])
            if d is not None and d >= 2.0:
                pairs.append((channel_id, vid, "thumbnail", os.path.basename(a[2]), os.path.basename(b[2]), a[1], b[1], f"wayback:{round(d, 1)}"))
    append(os.path.join(outdir, "pairs.csv"),
           ["channel_id", "video_id", "kind", "old", "new", "old_seen", "new_seen", "source"], pairs)
    append(os.path.join(outdir, "done.csv"), ["video_id", "page_snapshots", "titles_read", "thumb_versions"],
           [(vid, len(stamps), sum(1 for t in titles if t[2] == "wayback"), len(thumbs))])
    return len(pairs)


def main():
    lists, shard, shards = sys.argv[1].split(","), int(sys.argv[2]), int(sys.argv[3])
    budget = float(sys.argv[4]) * 60 if len(sys.argv) > 4 else 160 * 60
    outdir = os.path.join(OUT, f"shard{shard}")
    thumbdir = os.path.join(OUT, "thumbs")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(thumbdir, exist_ok=True)
    # skip anything any earlier run finished, whatever shard it ran in
    done = set()
    for d in os.listdir(OUT):
        f = os.path.join(OUT, d, "done.csv")
        if d.startswith("shard") and os.path.exists(f):
            done |= {r["video_id"] for r in csv.DictReader(open(f))}
    every = []
    for listfile in lists:
        for line in open(listfile).read().splitlines():
            if line.strip():
                channel_id, *vids = line.split()
                every += [(channel_id, v) for v in vids]
    # interleave by video, not by channel, so every shard gets a mix and all finish together
    work = [w for i, w in enumerate(every) if i % shards == shard and w[1] not in done]
    print(f"shard {shard}/{shards}: {len(work)} videos to do, {len(done)} already done overall")
    found = 0
    for n, (channel_id, vid) in enumerate(work, 1):
        if time.time() - START > budget:
            print(f"time budget reached after {n - 1} videos; rerun to continue")
            break
        t0, r0, w0 = time.time(), STATS["requests"], STATS["wait_s"]
        found += process(channel_id, vid, outdir, thumbdir)
        print(f"{n}/{len(work)} {vid}: {time.time() - t0:.0f}s, {STATS['requests'] - r0} requests, "
              f"{STATS['wait_s'] - w0:.0f}s waiting | totals ok={STATS['ok']} http={STATS['codes']} "
              f"errors={STATS['errors']} request_time={STATS['request_s']:.0f}s wait_time={STATS['wait_s']:.0f}s", flush=True)
    print(f"shard {shard} finished: {found} pairs")


if __name__ == "__main__":
    main()
