# Local FFmpeg (optional)

Venma Video looks for `ffmpeg` and `ffprobe` in this order:

1. `VENMA_FFMPEG` / `VENMA_FFPROBE` environment variables
2. `ffmpeg/` or `ffmpeg/bin/` in the repository root (this folder)
3. Your system `PATH`

Binaries placed here are git-ignored and will never be committed.
