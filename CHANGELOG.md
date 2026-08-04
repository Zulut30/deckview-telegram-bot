# Deckview Changelog

## Unreleased

- Fixed Telegram download buttons expiring with the two-hour temporary render
  directory: new buttons resolve immutable cached artifacts, while existing
  buttons can recover the photo already stored by Telegram.
- Added versioned, immutable 720px WebP preview derivatives for API deck
  renders while preserving the original JPEG for downloads and lightboxes.
- Backfilled previews on warm API cache hits without adding decode work to the
  Telegram render path.
- Added atomic concurrent publication, corrupt-preview recovery, path
  validation, cache telemetry fields, long-lived nginx caching, and regression
  coverage for preview generation and API responses.
