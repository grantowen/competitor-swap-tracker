# Competitor swap tracker

Logs when competitor channels change a video's title or thumbnail after
publishing. The version they drop and the version they keep, on the same
video, is the result of their own test. Those pairs are the data for testing
a title scorer.

## How it runs

- GitHub Actions, every 2 hours (`.github/workflows/track.yml`). No computer
  needs to be on. Run it by hand from the Actions tab with "Run workflow".
- `tracker.py`, standard library only, no API keys. It uses YouTube's public
  long-form uploads feed, oEmbed for the live title, and the thumbnail's ETag,
  which changes when the thumbnail is replaced.
- Tracks every long-form video for its first 30 days.

## Who gets tracked

Only channels doing packaging excellently, by numbers (TubeAI, 2026-10-19):
average views of 250K or more per video, average views at least 8% of
subscribers, 22% or more of videos are outliers, still uploading, and in our
lanes (interview podcast, communication, self-development, business). The
`own` lane is Jefferson's channel, logged for our own swaps, not as a model.

## Files

| File | What it holds |
| --- | --- |
| `channels.json` | Channels tracked. Edit to add or remove. `lane: own` marks our channels. |
| `data/snapshots.csv` | Every check: title and thumbnail version per video per run |
| `data/swaps.csv` | Every detected change: old, new, when, hours after publish |
| `data/thumbs/` | Each thumbnail version seen, `<video_id>_<version>.jpg` |
| `data/state.json` | Last known version per video (working state) |

## Limits

- Creators often re-upload the identical thumbnail, which changes its version
  stamp. The tracker compares the images and logs those as
  `thumbnail_resaved`, not as swaps (3 of the first 6 were this).

- A change can be a test result or an editorial fix (typo, guest name). Read
  swaps before scoring them.
- During a YouTube Test & Compare run, viewers see different versions; this
  tracker sees whatever YouTube serves it, so the winner shows up when the
  test ends.

## Other sources in the database

| Source | File | What it is |
| --- | --- | --- |
| Live tracker | `data/swaps.csv` | Swaps caught as they happen (cleanest) |
| Internet Archive | `data/archive/shard*/pairs.csv` | Past swaps recovered from saved pages and thumbnails; exact swap time unknown |
| Creator Hooks | `data/external/creator_hooks_title_ab.csv` | Jake Thomas's 103 controlled title A/B tests (2022, 15 small niche channels), with CTR for each title |
