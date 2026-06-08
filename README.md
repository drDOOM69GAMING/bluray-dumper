<img width="1224" height="1238" alt="Screenshot_20260608_033316" src="https://github.com/user-attachments/assets/6df5e5e0-0838-4ba4-96ff-74e4b99eab6c" />

# bluray-dumper

PyQt6 GUI for dumping Blu-ray discs, creating UDF ISOs, and compressing to MKV/AVCHD/DVD-Video.

## Requirements

**Required:**
- `bluraybackup` — disc dump
- `genisoimage` — ISO creation
- `blockdev` (util-linux) — disc size detection
- Python 3 + PyQt6

**Optional:**
- `HandBrakeCLI` — MKV compression
- `ffmpeg` / `ffprobe` — AVCHD/DVD-Video authoring, audio/subtitle extraction
- `dvdauthor` — DVD-Video ISO authoring (fallback: VOB + UDF ISO)
- `eject` — eject disc after completion
- `notify-send` (libnotify) — desktop notifications

## Install

```bash
pip install PyQt6
```

Install required tools via your package manager:

```bash
# Arch
sudo pacman -S bluraybackup cdrtools util-linux handbrake-cli ffmpeg dvdauthor eject libnotify

# Debian/Ubuntu
sudo apt install bluraybackup genisoimage util-linux handbrake-cli ffmpeg dvdauthor eject libnotify-bin

# Fedora
sudo dnf install bluraybackup genisoimage util-linux handbrake-cli ffmpeg dvdauthor eject libnotify
```

Place AACS keys at `~/.config/aacs/KEYDB.cfg` and BD+ data at `~/.config/bdplus/`.

## Usage

```bash
./bluray_dumper.py
```

1. Insert a Blu-ray disc
2. Select destination folder
3. Click **Dump This Disc**
4. After dump, optionally create a UDF ISO
5. Optionally compress the main movie with HandBrakeCLI
6. For DVD-5/9 targets, choose: MKV, AVCHD ISO, or DVD-Video ISO

## Features

- **Disc dump** via bluraybackup with live progress, speed (GB/h, MB/s), and ETA
- **UDF ISO** creation with genisoimage (`-iso-level 4 -UDF`) and size verification
- **SHA256** checksum generation on verified ISOs
- **Compression** with HandBrakeCLI (H.264, quality 22, CFR, x264 preset slower)
- **Direct-to-MKV** mode — skip ISO, compress straight from dump
- **AVCHD ISO** — remux MKV to M2TS, build BDMV structure, create UDF ISO
- **DVD-Video ISO** — encode to MPEG-2, author with dvdauthor, create DVD-Video ISO
- **Batch compression** queue — process multiple existing dumps
- **Audio/subtitle extraction** via ffprobe/ffmpeg stream selection dialog
- **ISO browser & restore** — list and extract files from ISOs
- **Disc catalog** — SQLite database tracking all dumps (label, sizes, SHA256, compression)
- **Config profiles** — save/load named profiles with device, destination, compression settings
- **Batch queue** — sequential disc processing with per-entry MKV flag
- **Multiple drive support** — auto-detects `/dev/sr*` devices
- **System tray** — minimize to tray with show/quit menu
- **Desktop notifications** via notify-send and QSystemTrayIcon
- **Settings** — persistent device, destination, auto-eject, auto-delete, compression target

## Workflow

```
Insert disc → Dump → ISO → SHA256 → verify → (optionally) compress to MKV/AVCHD/DVD
```

Auto-delete dump folder and auto-eject disc can be toggled in Settings.

## Logs

All operations logged to `~/bluray_dumper.log`. Session logs exported alongside ISO.
