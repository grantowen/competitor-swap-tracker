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
- Tracks every long-form video for its first 10 days.

## Files

| File | What it holds |
| --- | --- |
| `channels.json` | Channels tracked. Edit to add or remove. `lane: own` marks our channels. |
| `data/snapshots.csv` | Every check: title and thumbnail version per video per run |
| `data/swaps.csv` | Every detected change: old, new, when, hours after publish |
| `data/thumbs/` | Each thumbnail version seen, `<video_id>_<version>.jpg` |
| `data/state.json` | Last known version per video (working state) |

## Limits

- A change can be a test result or an editorial fix (typo, guest name). Read
  swaps before scoring them.
- During a YouTube Test & Compare run, viewers see different versions; this
  tracker sees whatever YouTube serves it, so the winner shows up when the
  test ends.
