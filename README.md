# bluray-dumper

PyQt6 GUI for dumping Blu-ray discs, creating UDF ISOs, compressing/remuxing to MKV, authoring AVCHD/DVD-Video ISOs, and burning.

## Requirements

**Runtime:**
- Python 3 + PyQt6
- `bluraybackup` — disc dump
- `genisoimage` — standard ISO creation (DVD-Video)
- `blockdev` (util-linux) — disc size detection
- `mkudffs` (udftools) — AVCHD ISO (UDF 2.50)
- `wodim` (cdrtools) — CLI disc burning
- `pkexec` (polkit) — privilege escalation (auto-install, loop mount, burn)

**Optional:**
- `HandBrakeCLI` — MKV compression (CPU encode)
- `ffmpeg` / `ffprobe` — AVCHD/DVD-Video authoring, GPU encode, audio/subtitle extraction
- `dvdauthor` — DVD-Video authoring
- `k3b` — GUI burning with ISO pre-loaded
- `eject` — eject disc after completion
- `notify-send` (libnotify) — desktop notifications

**GPU encode (auto-detected):**
- `h264_vaapi` — AMD (Mesa) / Intel GPUs
- `h264_amf` — AMD proprietary
- `h264_nvenc` — NVIDIA
- `h264_qsv` — Intel QuickSync

## Install

```bash
pip install PyQt6
```

Required tools are auto-installed via `pkexec` on first use. Manual install:

```bash
# Arch
sudo pacman -S bluraybackup cdrtools util-linux udftools handbrake-cli ffmpeg dvdauthor k3b eject libnotify polkit

# Debian/Ubuntu
sudo apt install bluraybackup genisoimage util-linux udftools handbrake-cli ffmpeg dvdauthor k3b eject libnotify-bin policykit-1

# Fedora
sudo dnf install bluraybackup genisoimage util-linux udftools handbrake-cli ffmpeg dvdauthor k3b eject libnotify polkit
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
5. Optionally compress the main movie
6. For DVD-5/9 targets, choose: MKV, AVCHD ISO, or DVD-Video ISO
7. Optionally burn the resulting ISO with K3B or wodim

## Features

- **Disc dump** via bluraybackup with live progress, speed (GB/h, MB/s), and ETA
- **UDF ISO** creation with genisoimage and SHA256 checksum verification
- **Compression** with HandBrakeCLI or ffmpeg GPU (VAAPI/AMF/NVENC/QSV) — auto-detected
- **ffmpeg fallback** — if HandBrakeCLI exits 0 with no output, ffmpeg is used automatically
- **Direct-to-MKV (remux)** — `ffmpeg -c copy`, no re-encode, no ISO
- **AVCHD ISO** — remux MKV → M2TS → BDMV structure → mkudffs UDF 2.50 ISO
- **DVD-Video ISO** — ffmpeg MPEG-2 encode → dvdauthor → genisoimage UDF ISO
- **GPU encode** — auto-detects h264_vaapi, h264_amf, h264_nvenc, h264_qsv in priority order
- **Burn ISO** — after creation or from ISO browser: "Open in K3B" or "Burn with wodim" (pkexec)
- **ISO browser & restore** — list and extract files from ISOs; re-burn any ISO
- **Auto-install** — missing tools installed via `pkexec <pm> -S <pkgs>` with confirmation dialog
- **Batch compression queue** — process multiple existing dumps
- **Audio/subtitle extraction** via ffprobe/ffmpeg stream selection dialog
- **Disc catalog** — SQLite database tracking all dumps (label, sizes, SHA256, compression)
- **Config profiles** — save/load named profiles with device, destination, compression settings
- **Batch queue** — sequential disc processing with per-entry MKV flag
- **Multiple drive support** — auto-detects `/dev/sr*` devices
- **System tray** — minimize to tray with show/quit menu
- **Desktop notifications** via notify-send and QSystemTrayIcon
- **Settings** — persistent device, destination, auto-eject, auto-delete, compression target
- **Clear & Reset** — one-click clear log and reset UI
- **Crash protection** — `faulthandler`, thread/sys excepthooks, crash dump to `~/bluray_dumper_crash.log`

## Workflow

```
Insert disc → Dump → ISO → SHA256 → verify → compress/remux → AVCHD/DVD ISO → burn
```

Auto-delete dump folder and auto-eject disc can be toggled in Settings. Remux (no compression) skips ISO entirely and creates an MKV directly.

## Logs

All operations logged to `~/bluray_dumper.log`. Crash dumps written to `~/bluray_dumper_crash.log`. Session logs exported alongside ISO.

## Player Compatibility

- **AVCHD ISO** (mkudffs, UDF 2.50): works on PS3, PS4, most standalone Blu-ray players
- **DVD-Video ISO**: universal DVD player compatibility
