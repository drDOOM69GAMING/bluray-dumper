<img width="1410" height="1313" alt="Screenshot_20260610_110301-1" src="https://github.com/user-attachments/assets/4e92565b-5deb-471b-b36f-2d66dea01a55" />

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

- **Disc dump** via bluraybackup with live progress, speed (GB/h, MB/s), and ETA (same progress/ETA display also shown during compress and remux)
- **Disc speed widget** — upper-right: spinning disc during reads, fire-colored binary chars (`010101`) during writes (red→orange→yellow gradient)
- **Status bar quotes** — random quote in the bottom status bar
- **UDF ISO** creation with genisoimage and SHA256 checksum verification
- **Compression** with HandBrakeCLI or ffmpeg GPU (VAAPI and vendor encoders) — auto-detected
- **ffmpeg fallback** — if HandBrakeCLI exits 0 with no output, ffmpeg is used automatically (GPU or CPU)
- **English audio auto-select** — ffprobe finds first audio stream tagged `eng`
- **Direct-to-MKV (remux)** — `ffmpeg -c copy`, no re-encode, no ISO
- **AVCHD ISO** — remux MKV → M2TS → BDMV structure → mkudffs UDF 2.01 → loop mount + populate → byte-patch to UDF 2.50 (verified after population)
- **DVD-Video ISO** — ffmpeg MPEG-2 encode → dvdauthor → genisoimage UDF ISO
- **GPU encode** — auto-detects h264_vaapi, h264_amf, h264_nvenc, h264_qsv
- **Burn ISO** — "Open in K3B" or "Burn with wodim" (pkexec); auto-eject after burn
- **Post-burn cleanup dialog** — delete temporary files after ISO creation
- **ISO browser & restore** — list and extract files from ISOs; re-burn
- **Auto-install** — missing tools via pkexec
- **Batch compression queue** — process multiple dumps
- **Audio/subtitle extraction** via ffprobe/ffmpeg stream selection
- **Disc catalog** — SQLite database tracking all dumps
- **Config profiles** — save/load device, destination, compression settings
- **Batch queue** — collapsible panel with real-time live activity status showing the current operation at all times (dump, compress, remux, ISO create, verify, burn, extract, etc.); sequential disc processing with per-entry MKV flag
- **Multiple drive support** — auto-detects `/dev/sr*` devices
- **System tray** — minimize with show/quit
- **Desktop notifications** via notify-send and QSystemTrayIcon
- **Settings** — persistent device, destination, auto-eject, auto-delete, compression target
- **Clear & Reset** — one-click clear log and reset UI
- **Crash protection** — faulthandler, thread/sys excepthooks, crash dump to `~/bluray_dumper_crash.log`
- **In-app Disclaimer** — READ menu bar with legal disclaimer

## Workflow

```
Insert disc → Dump → ISO → SHA256 → verify → compress/remux → AVCHD/DVD ISO → verify → burn
```

Auto-delete dump folder and auto-eject disc can be toggled in Settings. Remux (no compression) skips ISO entirely and creates an MKV directly.

## Known Issues

- **GPU double-encode (fixed)**: earlier versions would ffmpeg-encode after GPU encode, overwriting the MKV. Now `return` prevents the second pass.
- **AVCHD on PS4/standalone Blu-ray players**: works. Uses `mkudffs --media-type hd --udfrev 2.01` (rewritable loop-mount) then patches UDF rev bytes to 2.50. DomainFlags left at 0x00 (reference discs use 0x03) — confirmed working on PS4 and standalone Blu-ray player without adjustment.
- **Verification false positives (fixed)**: `rg` without `-a` skips binary ISOs; `BDMV/index.bdmv` never matched since UDF stores bare filenames, not paths. Both fixed.

## Logs

All operations logged to `~/bluray_dumper.log`. Crash dumps written to `~/bluray_dumper_crash.log`. Session logs exported alongside ISO.

## Player Compatibility

- **DVD-Video ISO** (dvdauthor + genisoimage, SD MPEG-2): tested & works on standard DVD players, PS4 see's the disc but refused to play it.
- **AVCHD ISO** (mkudffs UDF 2.01 → byte-patched to 2.50, BDMV structure, HD video): tested and works on standalone Blu-ray players (confirmed on 3D-capable player). Works on VLC, so far it works with everything but consoles.
- **MKV**: universal software playback (no disc needed)

For widest disc player compatibility, choose DVD-Video over AVCHD.

<img width="1401" height="1315" alt="Screenshot_20260610_105732-1" src="https://github.com/user-attachments/assets/339c96de-1cc1-40f3-a773-d8b3f623c89d" />
