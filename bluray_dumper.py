#!/usr/bin/env python3
import sys, os, re, struct, subprocess, time, shutil, signal, logging, traceback, hashlib, sqlite3, json, ast, glob, faulthandler, threading, platform, random, random
from pathlib import Path
from datetime import timedelta
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QProgressBar,
                             QFileDialog, QMessageBox, QFrame, QTextEdit,
                             QDialog, QListWidget, QLineEdit, QCheckBox,
                             QRadioButton, QGroupBox, QFormLayout,
                             QDialogButtonBox, QInputDialog, QComboBox,
                             QSystemTrayIcon, QMenu, QTableWidget, QHeaderView,
                             QAbstractItemView, QSplitter, QTableWidgetItem,
                             QStyle)
from PyQt6.QtCore import QTimer, Qt, QThread, pyqtSignal, QSettings, QPointF, QRectF
from PyQt6.QtGui import QTextCursor, QIcon, QAction, QPainter, QColor, QPen, QFont, QBrush, QRadialGradient, QConicalGradient, QPainterPath

AACS_DIR = Path.home() / '.config' / 'aacs'
BDPLUS_DIR = Path.home() / '.config' / 'bdplus'
DEFAULT_DEVICE = '/dev/sr0'
LOG_FILE = Path.home() / 'bluray_dumper.log'
CATALOG_DB = Path.home() / '.config' / 'bluray_dumper' / 'catalog.db'
REQUIRED_TOOLS = ['bluraybackup', 'genisoimage', 'blockdev']
TOOL_INSTALL = {
    'bluraybackup': '   pacman -S bluraybackup    # Arch\n'
                    '   apt install bluraybackup   # Debian/Ubuntu\n'
                    '   dnf install bluraybackup   # Fedora',
    'genisoimage':  '   pacman -S cdrtools         # Arch\n'
                    '   apt install genisoimage    # Debian/Ubuntu\n'
                    '   dnf install genisoimage    # Fedora',
    'blockdev':     '   pacman -S util-linux       # Arch\n'
                    '   apt install util-linux     # Debian/Ubuntu',
    'mkudffs':      '   pacman -S udftools          # Arch\n'
                    '   apt install udftools        # Debian/Ubuntu',
}
TOOL_PACKAGES = {
    'pacman': {
        'bluraybackup': 'bluraybackup',
        'genisoimage': 'cdrtools',
        'blockdev': 'util-linux',
        'mkudffs': 'udftools',
        'HandBrakeCLI': 'handbrake-cli',
        'ffmpeg': 'ffmpeg',
        'blkid': 'util-linux',
        'isoinfo': 'cdrtools',
        'git': 'git',
        'dvdauthor': 'dvdauthor',
    },
    'apt': {
        'bluraybackup': 'bluraybackup',
        'genisoimage': 'genisoimage',
        'blockdev': 'util-linux',
        'mkudffs': 'udftools',
        'HandBrakeCLI': 'handbrake-cli',
        'ffmpeg': 'ffmpeg',
        'blkid': 'util-linux',
        'isoinfo': 'genisoimage',
        'git': 'git',
        'dvdauthor': 'dvdauthor',
    },
    'dnf': {
        'bluraybackup': 'bluraybackup',
        'genisoimage': 'genisoimage',
        'blockdev': 'util-linux',
        'mkudffs': 'udftools',
        'HandBrakeCLI': 'HandBrakeCLI',
        'ffmpeg': 'ffmpeg',
        'blkid': 'util-linux',
        'isoinfo': 'genisoimage',
        'git': 'git',
        'dvdauthor': 'dvdauthor',
    },
}

TARGET_SIZES = {
    'dvd5': 4_700_000_000,
    'dvd9': 8_500_000_000,
    'bd25': 25_000_000_000,
    'bd50': 50_000_000_000,
}
TARGET_LABELS = {
    '': 'None (no compression)',
    'dvd5': 'DVD-5 (4.7 GB)',
    'dvd9': 'DVD-9 (8.5 GB)',
    'bd25': 'BD-25 (25 GB)',
    'bd50': 'BD-50 (50 GB)',
    'custom': 'Custom size',
}

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ])
log = logging.getLogger(__name__)


def tool_available(name):
    return shutil.which(name) is not None


def check_required_tools():
    missing = [t for t in REQUIRED_TOOLS if not tool_available(t)]
    if missing:
        log.error('Missing required tools: %s', ', '.join(missing))
        return missing
    log.debug('All required tools found')
    return []


def detect_pm():
    for pm in ['pacman', 'apt', 'dnf']:
        if shutil.which(pm):
            return pm
    return None


def install_missing_tools(tools, parent=None, pm=None):
    if pm is None:
        pm = detect_pm()
    if pm is None:
        return False
    if not shutil.which('pkexec'):
        log.error('pkexec not found, cannot auto-install')
        return False

    pkgs = [TOOL_PACKAGES.get(pm, {}).get(t, t) for t in tools]
    pkgs = [p for p in pkgs if p]
    if not pkgs:
        return False

    pm_install = {
        'pacman': ['-S', '--noconfirm'],
        'apt': ['install', '-y'],
        'dnf': ['install', '-y'],
    }.get(pm, [])

    if parent:
        msg = QMessageBox(parent)
        msg.setWindowTitle('Install Missing Tools')
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setText(f'The following tools are required:\n{", ".join(tools)}')
        msg.setInformativeText(
            f'Install packages with {pm}?\n\n'
            f'  sudo {pm} {" ".join(pm_install)} {" ".join(pkgs)}')
        btn_install = msg.addButton('Install', QMessageBox.ButtonRole.ActionRole)
        msg.addButton('Skip', QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() != btn_install:
            return False

    cmd = ['pkexec', pm] + pm_install + pkgs
    log.info('Installing: %s', ' '.join(cmd))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            log.info('Installation successful')
            return True
        log.error('Installation failed (code %d): %s', r.returncode, r.stderr[-300:])
    except subprocess.TimeoutExpired:
        log.error('Installation timed out')
    except Exception as e:
        log.error('Install exception: %s', e)
    return False


def ensure_tool(name, parent=None, pm=None):
    if tool_available(name):
        return True
    log.warning('Tool missing: %s', name)
    if pm is None:
        pm = detect_pm()
    if pm and shutil.which('pkexec') and parent:
        msg = QMessageBox(parent)
        msg.setWindowTitle('Missing Tool')
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setText(f'{name} is required for this operation.')
        btn_install = msg.addButton('Install', QMessageBox.ButtonRole.ActionRole)
        msg.addButton('Cancel', QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() == btn_install:
            if install_missing_tools([name], parent=parent, pm=pm):
                return tool_available(name)
    return False


def readable_device(device):
    return os.access(device, os.R_OK)


def get_disc_label(device):
    if not readable_device(device):
        log.warning('Device %s not readable', device)
        return None

    try:
        r = subprocess.run(['blkid', '-o', 'value', '-s', 'LABEL', device],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            label = r.stdout.strip()
            log.debug('blkid label: %s', label)
            return label
    except FileNotFoundError:
        log.debug('blkid not found, skipping')
    except subprocess.TimeoutExpired:
        log.warning('blkid timed out')
    except Exception as e:
        log.warning('blkid failed: %s', e)

    try:
        r = subprocess.run(['isoinfo', '-d', '-i', device],
                           capture_output=True, text=True, timeout=5)
        for line in r.stdout.split('\n'):
            if 'Volume id' in line or 'Volume Id' in line:
                v = line.split(':', 1)[-1].strip()
                if v:
                    log.debug('isoinfo label: %s', v)
                    return v
    except FileNotFoundError:
        log.debug('isoinfo not found, skipping')
    except subprocess.TimeoutExpired:
        log.warning('isoinfo timed out')
    except Exception as e:
        log.warning('isoinfo failed: %s', e)

    try:
        with open(device, 'rb') as f:
            for sector in (256, 16):
                try:
                    f.seek(sector * 2048)
                    avdp = f.read(512)
                    if len(avdp) < 512:
                        continue
                    tag_id = struct.unpack('<H', avdp[0:2])[0]
                    if tag_id != 2:
                        continue
                    main_vds_lbn = struct.unpack('<I', avdp[16:20])[0]
                    main_vds_len = struct.unpack('<I', avdp[20:24])[0]
                    if main_vds_len == 0 or main_vds_len > 65536:
                        continue
                    f.seek(main_vds_lbn * 2048)
                    vds = f.read(min(main_vds_len, 32768))
                    off = 0
                    while off + 2048 <= len(vds):
                        tid = struct.unpack('<H', vds[off:off+2])[0]
                        if tid == 1:
                            raw = vds[off+24:off+56]
                            if raw[0:1] == b'\x08':
                                vol = raw[1:].decode('ascii', errors='ignore').strip()
                            else:
                                vol = raw.decode('ascii', errors='ignore').strip()
                            vol = vol.strip(' \t\n\r\x00')
                            if vol:
                                log.debug('UDF raw label: %s', vol)
                                return vol
                        elif tid in (0, 0xFFFF):
                            break
                        off += 2048
                except (OSError, struct.error) as e:
                    log.debug('sector %d read failed: %s', sector, e)
                    continue
    except PermissionError:
        log.error('Permission denied reading %s directly', device)
    except FileNotFoundError:
        log.error('Device %s not found', device)
    except Exception as e:
        log.warning('Raw UDF read failed: %s', e)

    log.info('Could not determine disc label')
    return None


def get_disc_size(device):
    try:
        r = subprocess.run(['blockdev', '--getsize64', device],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            size = int(r.stdout.strip())
            log.debug('Disc size: %d bytes (%.2f GB)', size, size / (1024**3))
            return size
    except subprocess.TimeoutExpired:
        log.warning('blockdev timed out')
    except Exception as e:
        log.warning('blockdev failed: %s', e)
    return 0


def is_medium_present(device):
    if not os.path.exists(device):
        log.warning('Device %s does not exist', device)
        return False
    try:
        r = subprocess.run(['blockdev', '--getsize64', device],
                           capture_output=True, text=True, timeout=5)
        present = r.returncode == 0 and r.stdout.strip().isdigit() and int(r.stdout.strip()) > 0
        log.debug('Medium present on %s: %s', device, present)
        return present
    except subprocess.TimeoutExpired:
        log.warning('blockdev timed out checking medium')
    except Exception as e:
        log.warning('Failed to check medium: %s', e)
    return False


def dir_size(path):
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += dir_size(entry.path)
            except PermissionError:
                log.warning('Permission denied: %s', entry.path)
                continue
            except OSError as e:
                log.warning('Error scanning %s: %s', entry.path, e)
                continue
    except PermissionError:
        log.error('Permission denied scanning %s', path)
    except FileNotFoundError:
        log.error('Path not found: %s', path)
    except Exception as e:
        log.error('Unexpected error scanning %s: %s', path, e)
    return total


def detect_drives():
    drives = sorted(Path('/dev').glob('sr*'))
    return [str(d) for d in drives] if drives else [DEFAULT_DEVICE]


def get_mounts(device):
    try:
        r = subprocess.run(['findmnt', '-n', '-o', 'TARGET', device],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return [m.strip() for m in r.stdout.strip().split('\n') if m.strip()]
    except Exception:
        pass
    return []


def create_avchd_structure(m2ts_path, output_dir):
    bdmv = output_dir / 'BDMV'
    (bdmv / 'PLAYLIST').mkdir(parents=True, exist_ok=True)
    (bdmv / 'CLIPINF').mkdir(parents=True, exist_ok=True)
    (bdmv / 'STREAM').mkdir(parents=True, exist_ok=True)

    cert_dir = output_dir / 'CERTIFICATE'
    cert_dir.mkdir(parents=True, exist_ok=True)
    id_data = bytearray(6144)
    id_data[0:12] = b'CERTIFICATE\x00\x00\x00'
    id_bytes = bytes(id_data)
    (cert_dir / 'id.bdmv').write_bytes(id_bytes)
    log.debug('Wrote CERTIFICATE/id.bdmv (%d bytes)', len(id_bytes))

    stream_file = bdmv / 'STREAM' / '00000.m2ts'
    shutil.copy2(m2ts_path, stream_file)
    file_size = stream_file.stat().st_size

    duration_sec = _get_m2ts_duration(m2ts_path)
    _write_index_bdmv(bdmv / 'index.bdmv')
    _write_movieobject_bdmv(bdmv / 'MovieObject.bdmv')
    _write_playlist(bdmv / 'PLAYLIST' / '00000.mpls', duration_sec)
    _write_clipinfo(bdmv / 'CLIPINF' / '00000.clpi', file_size, duration_sec)
    log.info('AVCHD structure created at %s', bdmv)


def _write_minimal_dvd_ifo(video_ts_dir):
    for name in ('VIDEO_TS.IFO', 'VIDEO_TS.BUP',
                 'VTS_01_0.IFO', 'VTS_01_0.BUP'):
        p = video_ts_dir / name
        if not p.exists():
            data = bytearray(2048)
            if 'IFO' in name:
                data[0:12] = b'DVDVIDEOMG\x00\x00'
            p.write_bytes(bytes(data))
    log.info('Minimal IFO placeholders written to %s', video_ts_dir)


def _get_m2ts_duration(m2ts_path):
    try:
        r = subprocess.run(['ffprobe', '-v', 'error',
                           '-show_entries', 'format=duration',
                           '-of', 'default=noprint_wrappers=1:nokey=1',
                           str(m2ts_path)],
                          capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
    except Exception as e:
        log.warning('ffprobe duration failed: %s', e)
    return 0.0


def _write_index_bdmv(path):
    offset = 0x20
    data = bytearray()
    data += b'INDX'
    data += struct.pack('>HH', 0x0100, 0x2400)
    data += struct.pack('>II', offset, 0)
    data += b'\x00' * 16
    while len(data) < offset:
        data += b'\x00'
    data += struct.pack('>I', 1)
    data += struct.pack('>H', 1)
    data += b'00000'
    data += struct.pack('>BB', 1, 0)
    data += struct.pack('>II', 0xFFFFFFFF, 1)
    data += struct.pack('>II', 0xFFFFFFFF, 0)
    path.write_bytes(bytes(data))
    log.debug('Wrote index.bdmv (%d bytes)', len(data))


def _write_movieobject_bdmv(path):
    data = bytearray()
    data += b'MOBJ'
    data += struct.pack('>HH', 0x0100, 0x2400)
    data += struct.pack('>II', 0x0010, 0)
    data += b'\x00' * 8
    data += struct.pack('>H', 2)
    data += struct.pack('>H', 0x0100)
    data += struct.pack('>IIII', 0xFFFFFFFF, 0xFFFFFFFF, 0, 1)
    path.write_bytes(bytes(data))
    log.debug('Wrote MovieObject.bdmv (%d bytes)', len(data))


def _write_playlist(path, duration_sec, has_audio=True):
    total_ticks = int(duration_sec * 45000)
    stn_buf = bytearray()
    stn_buf += struct.pack('>H', 1 if has_audio else 0)
    stn_buf += b'\x00' * 2
    stn_buf += struct.pack('>B', 1)
    stn_buf += struct.pack('>B', 0x1B)
    stn_buf += b'\x00' * 4
    stn_buf += struct.pack('>I', 0)
    stn_buf += struct.pack('>I', 0xFFFFFFFF)
    if has_audio:
        stn_buf += struct.pack('>B', 1)
        stn_buf += struct.pack('>B', 0x80)
        stn_buf += b'\x00' * 4
    stn_buf += struct.pack('>B', 0x80 if has_audio else 0)
    stn_buf += struct.pack('>B', 0x00)
    stn_buf += b'\x00' * 3
    stn_buf += struct.pack('>B', 1)
    stn_buf += b'\x00' * 3
    stn_buf += struct.pack('>B', 1)
    stn_buf += b'\x00' * 3
    stn_length = len(stn_buf)

    data = bytearray()
    data += b'MPLS'
    data += struct.pack('>HH', 0x0100, 0x2400)
    data += struct.pack('>II', 0x003A, 0)
    data += b'\x00' * 16
    while len(data) < 0x3A:
        data += b'\x00'
    data += struct.pack('>H', 1)
    data += struct.pack('>H', 0)
    data += struct.pack('>B', 1)
    data += b'\x00' * 3
    data += struct.pack('>II', 0, total_ticks)
    data += struct.pack('>II', 0, 0)
    data += struct.pack('>H', 1)
    data += struct.pack('>H', 0xFFFF)
    data += b'00000'
    data += struct.pack('>B', 0x60)
    data += struct.pack('>B', 0)
    data += b'\x00' * 2
    data += b'\x00' * 2
    data += struct.pack('>H', stn_length)
    data += stn_buf
    path.write_bytes(bytes(data))
    log.debug('Wrote playlist (%d bytes, %.1f sec)', len(data), duration_sec)


def _write_clipinfo(path, file_size, duration_sec, has_audio=True):
    total_ticks = int(duration_sec * 45000)
    data = bytearray()
    data += b'CLPI'
    data += struct.pack('>HH', 0x0100, 0x2400)
    data += struct.pack('>II', 0x0038, 0)
    data += b'\x00' * 16
    while len(data) < 0x38:
        data += b'\x00'
    data += b'00000'
    data += struct.pack('>BB', 1, 0)
    data += struct.pack('>IH', 0, 0)
    data += struct.pack('>II', 0, total_ticks)
    data += struct.pack('>II', 0, total_ticks)
    data += struct.pack('>Q', file_size)
    data += struct.pack('>II', 0, total_ticks)
    data += struct.pack('>II', 0, total_ticks)
    data += struct.pack('>II', 0, total_ticks)
    data += struct.pack('>II', 0, 1)
    data += struct.pack('>H', 1)
    data += struct.pack('>H', 1 if has_audio else 0)
    data += struct.pack('>B', 1)
    data += struct.pack('>B', 0x1B)
    data += struct.pack('>H', 0x1011)
    data += struct.pack('>H', 0)
    data += struct.pack('>I', 0)
    data += struct.pack('>I', 0xFFFFFFFF)
    if has_audio:
        data += struct.pack('>B', 1)
        data += struct.pack('>B', 0x80)
        data += struct.pack('>H', 0x1100)
        data += struct.pack('>H', 0)
        data += struct.pack('>B', 0x80)
        data += struct.pack('>B', 0x00)
        data += struct.pack('>B', 0x00)
        data += struct.pack('>B', 0x00)
    path.write_bytes(bytes(data))
    log.debug('Wrote clipinfo (%d bytes, size=%d)', len(data), file_size)


def sanitize_filename(name):
    safe = ''.join(c if c.isalnum() or c in ' _-.' else '_' for c in name)
    return safe.strip() or 'Untitled_Bluray'


def _write_crash_dump(context, exc_info=None):
    try:
        crash_dump = Path.home() / 'bluray_dumper_crash.log'
        with open(crash_dump, 'a') as f:
            f.write(f'=== Crash at {time.ctime()} [{context}] ===\n')
            if exc_info:
                traceback.print_exception(*exc_info, file=f)
            f.write('\n')
        log.critical('Crash dump appended to %s [%s]', crash_dump, context)
    except Exception as e:
        log.error('Failed to write crash dump: %s', e)


def disk_free(path):
    try:
        st = os.statvfs(path)
        return st.f_frsize * st.f_bavail
    except Exception as e:
        log.warning('Could not check free space on %s: %s', path, e)
        return 0


def _find_vaapi_device():
    try:
        cards = sorted(glob.glob('/dev/dri/renderD*'))
        for card in cards:
            try:
                r = subprocess.run(
                    ['vainfo', '--display', 'drm', '--device', card],
                    capture_output=True, text=True, timeout=5)
                if 'Driver version' in r.stdout and 'Supported' in r.stdout:
                    log.debug('VAAPI device found: %s', card)
                    return card
            except Exception:
                continue
    except Exception as e:
        log.warning('VAAPI device detection failed: %s', e)
    fallback = '/dev/dri/renderD128'
    log.debug('VAAPI device detection failed, using fallback: %s', fallback)
    return fallback


def _probe_duration(path):
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'csv=p=0', str(path)],
            capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except Exception as e:
        log.warning('ffprobe duration failed: %s', e)
        return 0


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Settings')
        self.resize(460, 380)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        drives = detect_drives()
        self.device_combo = QComboBox()
        self.device_combo.setEditable(True)
        self.device_combo.addItems(drives)
        form.addRow('Device:', self.device_combo)

        self.auto_eject_cb = QCheckBox('Eject disc after completion')
        form.addRow(self.auto_eject_cb)

        self.auto_delete_cb = QCheckBox('Delete dump folder after ISO verified')
        form.addRow(self.auto_delete_cb)

        layout.addLayout(form)

        compress_group = QGroupBox('Compression')
        compress_layout = QVBoxLayout(compress_group)
        compress_form = QFormLayout()
        self.compress_target_combo = QComboBox()
        for key, label in TARGET_LABELS.items():
            self.compress_target_combo.addItem(label, key)
        self.compress_target_combo.currentIndexChanged.connect(self._on_target_changed)
        compress_form.addRow('Target size:', self.compress_target_combo)
        self.custom_size_edit = QLineEdit()
        self.custom_size_edit.setPlaceholderText('Size in GB (e.g. 10)')
        self.custom_size_edit.setEnabled(False)
        compress_form.addRow('Custom GB:', self.custom_size_edit)
        compress_layout.addLayout(compress_form)
        note = QLabel('Requires HandBrakeCLI. Encodes the main movie to fit the target.')
        note.setStyleSheet('color: #888; font-size: 11px;')
        note.setWordWrap(True)
        compress_layout.addWidget(note)
        layout.addWidget(compress_group)

        credit = QLabel('Copyright drdoom69gaming')
        credit.setStyleSheet('color: #888; font-size: 10px;')
        credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(credit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_settings)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.load_settings()

    def _on_target_changed(self, idx):
        self.custom_size_edit.setEnabled(
            self.compress_target_combo.itemData(idx) == 'custom')

    def load_settings(self):
        s = QSettings('BluRayDumper', 'dumper')
        saved = s.value('device', DEFAULT_DEVICE)
        idx = self.device_combo.findText(saved)
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)
        else:
            self.device_combo.setCurrentText(saved)
        self.auto_eject_cb.setChecked(s.value('auto_eject', 'false') == 'true')
        self.auto_delete_cb.setChecked(s.value('auto_delete', 'false') == 'true')
        target = s.value('compress_target', '')
        tidx = self.compress_target_combo.findData(target)
        if tidx >= 0:
            self.compress_target_combo.setCurrentIndex(tidx)
        self.custom_size_edit.setText(s.value('compress_custom_gb', ''))

    def save_settings(self):
        s = QSettings('BluRayDumper', 'dumper')
        s.setValue('device', self.device_combo.currentText())
        s.setValue('auto_eject', 'true' if self.auto_eject_cb.isChecked() else 'false')
        s.setValue('auto_delete', 'true' if self.auto_delete_cb.isChecked() else 'false')
        s.setValue('compress_target', self.compress_target_combo.currentData())
        s.setValue('compress_custom_gb', self.custom_size_edit.text())
        self.accept()


class LogViewer(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Blu-ray Dumper Log')
        self.resize(700, 500)
        layout = QVBoxLayout(self)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setStyleSheet('font-family: monospace; font-size: 11px;')
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        refresh_btn = QPushButton('Refresh')
        refresh_btn.clicked.connect(self.load_log)
        btn_layout.addWidget(refresh_btn)

        clear_btn = QPushButton('Clear Log')
        clear_btn.clicked.connect(self.clear_log)
        btn_layout.addWidget(clear_btn)

        btn_layout.addStretch()
        close_btn = QPushButton('Close')
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self.load_log()

    def load_log(self):
        try:
            content = LOG_FILE.read_text(errors='replace')
            self.text_edit.setPlainText(content)
            self.text_edit.moveCursor(QTextCursor.MoveOperation.End)
        except Exception as e:
            self.text_edit.setPlainText(f'Could not load log: {e}')

    def clear_log(self):
        reply = QMessageBox.question(
            self, 'Clear Log', 'Clear the log file?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                LOG_FILE.write_text('')
                self.text_edit.clear()
            except Exception as e:
                QMessageBox.critical(self, 'Error', f'Could not clear log: {e}')


class DiscCatalog:
    def __init__(self):
        self.db_path = CATALOG_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS dumps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    device TEXT,
                    disc_size INTEGER,
                    dump_size INTEGER,
                    iso_size INTEGER,
                    sha256 TEXT,
                    compress_status TEXT,
                    compress_size INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )''')
        except Exception as e:
            log.error('Catalog DB init failed: %s', e)

    def insert_dump(self, label, device, disc_size, dump_size, iso_size,
                    sha256, compress_status='', compress_size=0):
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute('''INSERT INTO dumps
                    (label, device, disc_size, dump_size, iso_size, sha256,
                     compress_status, compress_size)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                    (label, device, disc_size, dump_size, iso_size, sha256,
                     compress_status, compress_size))
            log.info('Catalog entry added: %s', label)
        except Exception as e:
            log.error('Catalog insert failed: %s', e)

    def list_dumps(self, query=''):
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                if query:
                    cur = conn.execute(
                        '''SELECT * FROM dumps
                           WHERE label LIKE ? OR sha256 LIKE ?
                           ORDER BY created_at DESC''',
                        (f'%{query}%', f'%{query}%'))
                else:
                    cur = conn.execute(
                        'SELECT * FROM dumps ORDER BY created_at DESC')
                rows = cur.fetchall()
                return [dict(zip([d[0] for d in cur.description], r)) for r in rows]
        except Exception as e:
            log.error('Catalog list failed: %s', e)
            return []


class CatalogDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Disc Catalog')
        self.resize(800, 500)
        self.catalog = DiscCatalog()
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('Search by label or SHA256...')
        self.search_input.textChanged.connect(self.refresh_table)
        search_row.addWidget(self.search_input)
        layout.addLayout(search_row)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ['ID', 'Label', 'Date', 'Disc Size', 'Dump Size', 'ISO Size',
             'SHA256', 'Compress'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton('Close')
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        self.refresh_table()

    def refresh_table(self):
        dumps = self.catalog.list_dumps(self.search_input.text().strip())
        self.table.setRowCount(len(dumps))
        for i, d in enumerate(dumps):
            self.table.setItem(i, 0, QTableWidgetItem(str(d.get('id', ''))))
            self.table.setItem(i, 1, QTableWidgetItem(d.get('label', '')))
            self.table.setItem(i, 2, QTableWidgetItem(
                d.get('created_at', '')[:19] if d.get('created_at') else ''))
            disc_gb = d.get('disc_size', 0) / (1024**3) if d.get('disc_size') else 0
            self.table.setItem(i, 3, QTableWidgetItem(f'{disc_gb:.2f} GB'))
            dump_gb = d.get('dump_size', 0) / (1024**3) if d.get('dump_size') else 0
            self.table.setItem(i, 4, QTableWidgetItem(f'{dump_gb:.2f} GB'))
            iso_gb = d.get('iso_size', 0) / (1024**3) if d.get('iso_size') else 0
            self.table.setItem(i, 5, QTableWidgetItem(f'{iso_gb:.2f} GB'))
            sha = d.get('sha256', '') or ''
            self.table.setItem(i, 6, QTableWidgetItem(sha[:16] + '...' if len(sha) > 16 else sha))
            self.table.setItem(i, 7, QTableWidgetItem(d.get('compress_status', '')))
        self.table.resizeColumnsToContents()


class DumpWorker(QThread):
    finished = pyqtSignal(int, str)
    output_line = pyqtSignal(str)

    def __init__(self, device, cwd, keyfile):
        super().__init__()
        self.device = device
        self.cwd = cwd
        self.keyfile = keyfile
        self._process = None

    def run(self):
        cmd = ['bluraybackup']
        if self.device:
            cmd += ['-d', self.device]
        if self.keyfile:
            cmd += ['-k', self.keyfile]

        log.info('Starting dump (cwd=%s): %s', self.cwd, ' '.join(cmd))
        try:
            self._process = subprocess.Popen(
                cmd, cwd=self.cwd, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                errors='replace')
            for line in iter(self._process.stdout.readline, ''):
                line = line.rstrip('\n\r')
                if line and '\ufffd' not in line:
                    log.debug('[bluraybackup] %s', line)
                    self.output_line.emit(line)
            self._process.stdout.close()
            self._process.wait()
            log.info('bluraybackup exited with code %d', self._process.returncode)
            self.finished.emit(self._process.returncode, '')
        except FileNotFoundError:
            log.critical('bluraybackup not found in PATH')
            self.finished.emit(-1, 'bluraybackup not found. Is it installed?')
        except PermissionError:
            log.critical('Permission denied running bluraybackup')
            self.finished.emit(-1, 'Permission denied. Run without sudo.')
        except Exception as e:
            log.error('Dump worker exception: %s', traceback.format_exc())
            _write_crash_dump('DumpWorker', sys.exc_info())
            self.finished.emit(-1, str(e))

    def stop(self):
        if self._process and self._process.poll() is None:
            log.warning('Terminating dump process')
            self._process.terminate()
            try:
                self._process.wait(3)
            except subprocess.TimeoutExpired:
                pass
            if self._process.poll() is None:
                log.warning('Killing dump process')
                self._process.kill()


class ISOWorker(QThread):
    finished = pyqtSignal(int, str)
    output_line = pyqtSignal(str)

    def __init__(self, source_dir, iso_path):
        super().__init__()
        self.source_dir = source_dir
        self.iso_path = iso_path
        self._process = None

    def run(self):
        cmd = ['genisoimage', '-iso-level', '4', '-UDF', '-o', self.iso_path, self.source_dir]
        log.info('Starting ISO creation: %s', ' '.join(cmd))

        try:
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
            for line in iter(self._process.stdout.readline, ''):
                line = line.rstrip('\n\r')
                if line:
                    log.debug('[genisoimage] %s', line)
                    self.output_line.emit(line)
            self._process.stdout.close()
            self._process.wait()
            log.info('genisoimage exited with code %d', self._process.returncode)
            self.finished.emit(self._process.returncode, '')
        except FileNotFoundError:
            log.critical('genisoimage not found in PATH')
            self.finished.emit(-1, 'genisoimage not found. Install cdrtools.')
        except PermissionError:
            log.critical('Permission denied running genisoimage')
            self.finished.emit(-1, 'Permission denied.')
        except Exception as e:
            log.error('ISO worker exception: %s', traceback.format_exc())
            _write_crash_dump('ISOWorker', sys.exc_info())
            self.finished.emit(-1, str(e))

    def stop(self):
        if self._process and self._process.poll() is None:
            log.warning('Terminating genisoimage')
            self._process.terminate()
            try:
                self._process.wait(3)
            except subprocess.TimeoutExpired:
                pass
            if self._process.poll() is None:
                log.warning('Killing genisoimage')
                self._process.kill()


class CompressWorker(QThread):
    finished = pyqtSignal(int, str)
    output_line = pyqtSignal(str)
    progress_updated = pyqtSignal(int, str)

    def __init__(self, source_dir, output_path, target_bytes=0):
        super().__init__()
        self.source_dir = source_dir
        self.output_path = output_path
        self.target_bytes = target_bytes
        self._process = None
        self.encoder_name = ''
        self._total_dur = 0

    def run(self):
        stream_dir = Path(self.source_dir) / 'BDMV' / 'STREAM'
        if not stream_dir.is_dir():
            self.finished.emit(-1, 'No BDMV/STREAM directory found')
            return
        m2ts_files = list(stream_dir.glob('*.m2ts'))
        if not m2ts_files:
            self.finished.emit(-1, 'No M2TS files found')
            return
        main_movie = max(m2ts_files, key=lambda f: f.stat().st_size)

        self._total_dur = self._get_duration(main_movie)
        gpu_encoder, _ = self._detect_gpu_encoder()

        if gpu_encoder:
            log.info('GPU encoder detected (%s), using ffmpeg with hardware acceleration', gpu_encoder)
            self.encoder_name = f'ffmpeg {gpu_encoder}'
            self._ffmpeg_encode(main_movie)
            return
        elif tool_available('HandBrakeCLI'):
            self.encoder_name = 'HandBrakeCLI'
            cmd = self._build_handbrake_cmd(main_movie)
            log.info('Starting compression (HandBrakeCLI): %s', ' '.join(cmd))
            try:
                self._process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1)
                for line in iter(self._process.stdout.readline, ''):
                    line = line.rstrip('\n\r')
                    if line:
                        log.debug('[HandBrakeCLI] %s', line)
                        self.output_line.emit(line)
                        m = re.search(r'(\d+\.?\d*)\s*%\s*\(', line)
                        if m:
                            pct = int(float(m.group(1)))
                            self.progress_updated.emit(pct, '')
                self._process.stdout.close()
                self._process.wait()
                log.info('HandBrakeCLI exited with code %d', self._process.returncode)
                if self._process.returncode == 0:
                    try:
                        out_size = Path(self.output_path).stat().st_size
                        if out_size >= 10 * 1024 * 1024:
                            self.finished.emit(0, '')
                            return
                        log.warning('HandBrakeCLI returned 0 but output is only %d bytes, treating as failure', out_size)
                    except OSError:
                        log.warning('HandBrakeCLI returned 0 but output file missing, treating as failure')
                else:
                    log.warning('HandBrakeCLI failed with code %d', self._process.returncode)
            except FileNotFoundError:
                log.warning('HandBrakeCLI not found despite tool_available check, falling back to ffmpeg')
            except Exception as e:
                log.error('HandBrakeCLI failed: %s', traceback.format_exc())
                QMessageBox.critical(
                    None, 'HandBrakeCLI Error',
                    f'HandBrakeCLI failed:\n{e}\n\nFalling back to ffmpeg.')
                QApplication.processEvents()

        if tool_available('ffmpeg'):
            if not self.encoder_name:
                self.encoder_name = 'ffmpeg'
            self._ffmpeg_encode(main_movie)
        else:
            self.finished.emit(-1, 'Neither HandBrakeCLI nor ffmpeg is available for compression.')

    def _build_handbrake_cmd(self, main_movie):
        cmd = ['HandBrakeCLI', '-i', str(main_movie), '-o', self.output_path,
               '--format', 'av_mkv', '--encoder', 'x264',
               '--encoder-preset', 'slower',
               '--encoder-profile', 'high', '--encoder-level', '4.0',
               '--cfr',
               '--aencoder', 'ac3', '--ab', '448k',
               '--audio-lang-list', 'eng']

        if self.target_bytes > 0:
            duration = self._get_duration(main_movie)
            if duration > 0:
                total_kbps = int(self.target_bytes * 8 / duration / 1000)
                video_kbps = max(100, int((total_kbps - 448) * 0.95))
                cmd += ['--vb', str(video_kbps)]
                log.info('HandBrakeCLI compression target: %d bytes, video bitrate %d kbps',
                         self.target_bytes, video_kbps)
            else:
                log.warning('Could not determine duration, falling back to CRF 22')
                cmd += ['--quality', '22']
        else:
            cmd += ['--quality', '22']
        return cmd

    def _get_duration(self, main_movie):
        return _probe_duration(main_movie)

    @staticmethod
    def _find_english_audio_idx(main_movie):
        try:
            r = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries',
                 'stream=index,codec_type:stream_tags=language',
                 '-of', 'csv=p=0', str(main_movie)],
                capture_output=True, text=True, timeout=30)
            audio_count = 0
            for line in r.stdout.strip().split('\n'):
                line = line.strip()
                if not line:
                    continue
                cols = line.split(',')
                if len(cols) >= 2 and cols[1] == 'audio':
                    lang = cols[2].strip().lower() if len(cols) > 2 and cols[2].strip() else ''
                    if lang == 'eng':
                        return audio_count
                    audio_count += 1
        except Exception as e:
            log.warning('ffprobe audio language detection failed: %s', e)
        return 0

    def _ffmpeg_encode(self, main_movie):
        gpu_encoder, gpu_opts = self._detect_gpu_encoder()
        if gpu_encoder:
            log.info('Using GPU encoder: %s', gpu_encoder)

        cmd = ['ffmpeg', '-i', str(main_movie)]
        if gpu_encoder:
            cmd += gpu_opts
        else:
            cmd += ['-c:v', 'libx264', '-preset', 'slower',
                    '-profile:v', 'high', '-level', '4.0']

        audio_idx = self._find_english_audio_idx(main_movie)
        cmd += ['-pix_fmt', 'yuv420p',
                '-c:a', 'ac3', '-b:a', '448k',
                '-map', '0:v:0', '-map', f'0:a:{audio_idx}',
                '-y', str(self.output_path)]

        if self.target_bytes > 0:
            duration = self._get_duration(main_movie)
            if duration > 0:
                total_kbps = int(self.target_bytes * 8 / duration / 1000)
                video_kbps = max(100, int((total_kbps - 448) * 0.95))
                gpu_rate_opts = ['-b:v', f'{video_kbps}k']
                if gpu_encoder == 'h264_vaapi':
                    gpu_rate_opts = ['-b:v', f'{video_kbps}k', '-maxrate', f'{int(video_kbps*1.2)}k']
                cmd += gpu_rate_opts
                log.info('ffmpeg compression target: %d bytes, video bitrate %d kbps, encoder=%s',
                         self.target_bytes, video_kbps, gpu_encoder or 'libx264')
            else:
                gpu_qual_opts = ['-crf', '22'] if not gpu_encoder else ['-qp', '22']
                cmd += gpu_qual_opts
        else:
            gpu_qual_opts = ['-crf', '22'] if not gpu_encoder else ['-qp', '22']
            cmd += gpu_qual_opts

        log.info('Starting compression (ffmpeg): %s', ' '.join(cmd))
        self._run_subprocess(cmd, 'ffmpeg')

    @staticmethod
    def _detect_gpu_encoder():
        try:
            r = subprocess.run(['ffmpeg', '-encoders'], capture_output=True, text=True, timeout=10)
            encoders = r.stdout
        except Exception:
            return None, []

        # Prefer VAAPI on AMD (radeonsi/Mesa), then AMF, then NVENC, then QSV
        if 'h264_vaapi' in encoders:
            vaapi_dev = _find_vaapi_device()
            log.info('GPU encoder detected: h264_vaapi (device=%s)', vaapi_dev)
            return 'h264_vaapi', ['-vaapi_device', vaapi_dev,
                                  '-vf', 'format=nv12,hwupload',
                                  '-c:v', 'h264_vaapi']
        if 'h264_amf' in encoders:
            log.info('GPU encoder detected: h264_amf')
            return 'h264_amf', ['-c:v', 'h264_amf', '-usage', 'transcoding']
        if 'h264_nvenc' in encoders:
            log.info('GPU encoder detected: h264_nvenc')
            return 'h264_nvenc', ['-c:v', 'h264_nvenc', '-preset', 'p4']
        if 'h264_qsv' in encoders:
            log.info('GPU encoder detected: h264_qsv')
            return 'h264_qsv', ['-c:v', 'h264_qsv']
        log.info('No GPU encoder found, using CPU (libx264)')
        return None, []

    def _run_subprocess(self, cmd, label):
        try:
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
            for line in iter(self._process.stdout.readline, ''):
                line = line.rstrip('\n\r')
                if line:
                    log.debug('[%s] %s', label, line)
                    self.output_line.emit(line)
                    m = re.search(r'time=(\d+):(\d+):(\d+)\.(\d+)', line)
                    if m and self._total_dur > 0:
                        cur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                        pct = int(cur * 100 / self._total_dur)
                        sm = re.search(r'speed=([\d.]+)x', line)
                        spd = sm.group(1) + 'x' if sm else ''
                        self.progress_updated.emit(min(pct, 99), spd)
            self._process.stdout.close()
            self._process.wait()
            log.info('%s exited with code %d', label, self._process.returncode)
            self.finished.emit(self._process.returncode, '')
        except FileNotFoundError:
            self.finished.emit(-1, f'{label} not found in PATH.')
        except Exception as e:
            log.error('%s worker exception: %s', label, traceback.format_exc())
            _write_crash_dump(f'CompressWorker/{label}', sys.exc_info())
            self.finished.emit(-1, str(e))

    def stop(self):
        if self._process and self._process.poll() is None:
            log.warning('Terminating compression process')
            self._process.terminate()
            try:
                self._process.wait(3)
            except subprocess.TimeoutExpired:
                pass
            if self._process.poll() is None:
                log.warning('Killing compression process')
                self._process.kill()


class RemuxWorker(QThread):
    finished = pyqtSignal(int, str)
    output_line = pyqtSignal(str)
    progress_updated = pyqtSignal(int, str)

    def __init__(self, source_dir, output_path):
        super().__init__()
        self.source_dir = source_dir
        self.output_path = output_path
        self._process = None
        self._total_dur = 0

    def run(self):
        stream_dir = Path(self.source_dir) / 'BDMV' / 'STREAM'
        if not stream_dir.is_dir():
            self.finished.emit(-1, 'No BDMV/STREAM directory found')
            return
        m2ts_files = list(stream_dir.glob('*.m2ts'))
        if not m2ts_files:
            self.finished.emit(-1, 'No M2TS files found')
            return
        main_movie = max(m2ts_files, key=lambda f: f.stat().st_size)
        self._total_dur = _probe_duration(main_movie)

        cmd = ['ffmpeg', '-i', str(main_movie),
               '-map', '0', '-c', 'copy',
               '-y', str(self.output_path)]
        log.info('Remuxing main movie: %s', ' '.join(cmd))
        try:
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
            for line in iter(self._process.stdout.readline, ''):
                line = line.rstrip('\n\r')
                if line:
                    log.debug('[ffmpeg remux] %s', line)
                    self.output_line.emit(line)
                    m = re.search(r'time=(\d+):(\d+):(\d+)\.(\d+)', line)
                    if m and self._total_dur > 0:
                        cur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                        pct = int(cur * 100 / self._total_dur)
                        sm = re.search(r'speed=([\d.]+)x', line)
                        spd = sm.group(1) + 'x' if sm else ''
                        self.progress_updated.emit(min(pct, 99), spd)
            self._process.stdout.close()
            self._process.wait()
            log.info('ffmpeg remux exited with code %d', self._process.returncode)
            self.finished.emit(self._process.returncode, '')
        except FileNotFoundError:
            self.finished.emit(-1, 'ffmpeg not found. Install ffmpeg.')
        except Exception as e:
            log.error('Remux worker exception: %s', traceback.format_exc())
            _write_crash_dump('RemuxWorker', sys.exc_info())
            self.finished.emit(-1, str(e))

    def stop(self):
        if self._process and self._process.poll() is None:
            log.warning('Terminating ffmpeg remux')
            self._process.terminate()
            try:
                self._process.wait(3)
            except subprocess.TimeoutExpired:
                pass
            if self._process.poll() is None:
                log.warning('Killing ffmpeg remux')
                self._process.kill()


class ExtractWorker(QThread):
    finished = pyqtSignal(int, str)
    output_line = pyqtSignal(str)

    def __init__(self, source_dir, stream_specs, output_dir):
        super().__init__()
        self.source_dir = source_dir
        self.stream_specs = stream_specs
        self.output_dir = output_dir
        self._process = None

    def run(self):
        stream_dir = Path(self.source_dir) / 'BDMV' / 'STREAM'
        if not stream_dir.is_dir():
            self.finished.emit(-1, 'No BDMV/STREAM directory found')
            return
        m2ts_files = list(stream_dir.glob('*.m2ts'))
        if not m2ts_files:
            self.finished.emit(-1, 'No M2TS files found')
            return
        main_movie = max(m2ts_files, key=lambda f: f.stat().st_size)

        for spec in self.stream_specs:
            kind = spec['kind']
            index = spec['index']
            lang = spec.get('lang', 'unknown')
            ext = spec.get('ext', 'm4a')
            out_name = f'{kind}_{index}_{lang}.{ext}'
            out_path = Path(self.output_dir) / out_name
            cmd = ['ffmpeg', '-i', str(main_movie),
                   '-map', f'0:{index}',
                   '-c', 'copy',
                   '-y', str(out_path)]
            log.info('Extracting stream %d: %s', index, ' '.join(cmd))
            try:
                self._process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1)
                for line in iter(self._process.stdout.readline, ''):
                    line = line.rstrip('\n\r')
                    if line:
                        self.output_line.emit(line)
                self._process.stdout.close()
                self._process.wait()
                log.info('Extraction of stream %d finished with code %d',
                         index, self._process.returncode)
            except FileNotFoundError:
                self.finished.emit(-1, 'ffmpeg not found. Install ffmpeg.')
                return
            except Exception as e:
                log.error('Extract worker exception: %s', traceback.format_exc())
                _write_crash_dump('ExtractWorker', sys.exc_info())
                self.finished.emit(-1, str(e))
                return
        self.finished.emit(0, '')

    def stop(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(3)
            except subprocess.TimeoutExpired:
                pass
            if self._process.poll() is None:
                self._process.kill()


class BurnWorker(QThread):
    finished = pyqtSignal(bool, object)

    def __init__(self, parent, iso_path, device):
        super().__init__(parent)
        self.iso_path = iso_path
        self.device = device
        self._process = None

    def run(self):
        try:
            cmd = ['pkexec', 'wodim', '-v', '-dao',
                   f'dev={self.device}', 'speed=4',
                   str(self.iso_path)]
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True)
            for line in self._process.stdout:
                log.info('[wodim] %s', line.rstrip())
            self._process.wait()
            if self._process.returncode == 0:
                self.finished.emit(True, self.iso_path)
            else:
                log.error('wodim failed with code %d', self._process.returncode)
                self.finished.emit(False, self.iso_path)
        except Exception as e:
            log.error('Burn worker exception: %s', traceback.format_exc())
            _write_crash_dump('BurnWorker', sys.exc_info())
            self.finished.emit(False, self.iso_path)

    def stop(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(3)
            except subprocess.TimeoutExpired:
                pass
            if self._process.poll() is None:
                self._process.kill()


def list_streams(m2ts_path):
    try:
        r = subprocess.run(['ffprobe', '-v', 'quiet',
                           '-print_format', 'json',
                           '-show_streams', str(m2ts_path)],
                          capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return []
        data = json.loads(r.stdout)
        streams = []
        for s in data.get('streams', []):
            idx = s.get('index', 0)
            codec_type = s.get('codec_type', '')
            codec_name = s.get('codec_name', '')
            lang = s.get('tags', {}).get('language', 'und')
            if codec_type == 'audio':
                ext = 'm4a'
            elif codec_type == 'subtitle':
                ext = 'sup'
            else:
                ext = 'bin'
            streams.append({
                'index': idx,
                'kind': codec_type,
                'codec': codec_name,
                'lang': lang,
                'ext': ext
            })
        return streams
    except Exception as e:
        log.warning('ffprobe failed: %s', e)
        return []


class StreamSelectDialog(QDialog):
    def __init__(self, streams, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Select Streams to Extract')
        self.resize(500, 400)
        self.selected = []
        layout = QVBoxLayout(self)

        label = QLabel('Select audio/subtitle streams to extract:')
        layout.addWidget(label)

        self.checkboxes = []
        for s in streams:
            kind = s['kind']
            codec = s['codec']
            lang = s['lang']
            idx = s['index']
            text = f'[{kind}] #{idx} {codec} ({lang})'
            cb = QCheckBox(text)
            cb.setChecked(True)
            cb.stream_info = s
            self.checkboxes.append(cb)
            layout.addWidget(cb)

        btn_layout = QHBoxLayout()
        select_all = QPushButton('Select All')
        select_all.clicked.connect(lambda: [c.setChecked(True) for c in self.checkboxes])
        btn_layout.addWidget(select_all)
        deselect_all = QPushButton('Deselect All')
        deselect_all.clicked.connect(lambda: [c.setChecked(False) for c in self.checkboxes])
        btn_layout.addWidget(deselect_all)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        self.selected = [cb.stream_info for cb in self.checkboxes if cb.isChecked()]
        if not self.selected:
            QMessageBox.warning(self, 'No Streams', 'Select at least one stream.')
            return
        super().accept()


class BatchCompressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Batch Compression Queue')
        self.resize(500, 400)
        layout = QVBoxLayout(self)

        self.folder_list = QListWidget()
        self.folder_list.setAlternatingRowColors(True)
        layout.addWidget(self.folder_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton('Add Dump Folder')
        add_btn.clicked.connect(self.add_folder)
        btn_row.addWidget(add_btn)
        remove_btn = QPushButton('Remove Selected')
        remove_btn.clicked.connect(self.remove_folder)
        btn_row.addWidget(remove_btn)
        layout.addLayout(btn_row)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel('Target size:'))
        self.target_combo = QComboBox()
        for key, label in TARGET_LABELS.items():
            self.target_combo.addItem(label, key)
        target_row.addWidget(self.target_combo)
        layout.addLayout(target_row)

        self.start_btn = QPushButton('Start Batch Compression')
        self.start_btn.setStyleSheet(
            'QPushButton { background-color: #4a90d9; color: white; font-weight: bold; '
            'padding: 8px; border-radius: 4px; }'
            'QPushButton:disabled { background-color: #aaa; }')
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.accept)
        layout.addWidget(self.start_btn)

        self.folders = []

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, 'Select Dump Folder')
        if d:
            self.folders.append(d)
            self.folder_list.addItem(d)
            self.start_btn.setEnabled(True)

    def remove_folder(self):
        row = self.folder_list.currentRow()
        if row >= 0:
            self.folders.pop(row)
            self.folder_list.takeItem(row)
            self.start_btn.setEnabled(len(self.folders) > 0)

    def get_target_bytes(self):
        key = self.target_combo.currentData()
        if key == 'custom':
            gb, ok = QInputDialog.getDouble(self, 'Custom Size',
                                            'Target size in GB:', 10, 1, 100, 1)
            return int(gb * 1_000_000_000) if ok else 0
        return TARGET_SIZES.get(key, 0)


class IsoBrowserDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Browse & Restore from ISO')
        self.resize(700, 500)
        layout = QVBoxLayout(self)

        iso_row = QHBoxLayout()
        self.iso_path_edit = QLineEdit()
        self.iso_path_edit.setPlaceholderText('Select an ISO file...')
        iso_row.addWidget(self.iso_path_edit)
        browse_btn = QPushButton('Browse')
        browse_btn.clicked.connect(self.browse_iso)
        iso_row.addWidget(browse_btn)
        load_btn = QPushButton('Load File List')
        load_btn.clicked.connect(self.load_file_list)
        iso_row.addWidget(load_btn)
        layout.addLayout(iso_row)

        self.file_table = QTableWidget()
        self.file_table.setColumnCount(2)
        self.file_table.setHorizontalHeaderLabels(['Path', 'Size'])
        self.file_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_table.setAlternatingRowColors(True)
        self.file_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.file_table)

        extract_row = QHBoxLayout()
        self.extract_dest_edit = QLineEdit()
        self.extract_dest_edit.setPlaceholderText('Extract to...')
        extract_row.addWidget(self.extract_dest_edit)
        dest_btn = QPushButton('Choose')
        dest_btn.clicked.connect(self.choose_dest)
        extract_row.addWidget(dest_btn)
        extract_btn = QPushButton('Extract Selected')
        extract_btn.setStyleSheet(
            'QPushButton { background-color: #4a90d9; color: white; font-weight: bold; '
            'padding: 6px; border-radius: 4px; }')
        extract_btn.clicked.connect(self.extract_files)
        extract_row.addWidget(extract_btn)
        burn_btn = QPushButton('Burn ISO')
        burn_btn.setStyleSheet(
            'QPushButton { background-color: #d9804a; color: white; font-weight: bold; '
            'padding: 6px; border-radius: 4px; }')
        burn_btn.clicked.connect(self.burn_iso)
        extract_row.addWidget(burn_btn)
        layout.addLayout(extract_row)

        self.iso_path = None
        self.file_list = []

    def browse_iso(self):
        p, _ = QFileDialog.getOpenFileName(
            self, 'Select ISO File', str(Path.home()), 'ISO Files (*.iso)')
        if p:
            self.iso_path_edit.setText(p)
            self.iso_path = p
            self.load_file_list()

    def load_file_list(self):
        iso = self.iso_path_edit.text().strip()
        if not iso or not Path(iso).is_file():
            QMessageBox.warning(self, 'Error', 'Select a valid ISO file.')
            return
        self.iso_path = iso
        try:
            r = subprocess.run(['isoinfo', '-f', '-i', iso],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                QMessageBox.warning(self, 'Error', f'isoinfo failed: {r.stderr}')
                return
            lines = [l.strip() for l in r.stdout.split('\n') if l.strip()]
            self.file_list = lines
            self.file_table.setRowCount(len(lines))
            for i, path in enumerate(lines):
                self.file_table.setItem(i, 0, QTableWidgetItem(path))
                self.file_table.setItem(i, 1, QTableWidgetItem(''))
            self.file_table.resizeColumnsToContents()
            log.info('Loaded %d files from ISO', len(lines))
        except FileNotFoundError:
            QMessageBox.critical(self, 'Missing Tool', 'isoinfo not found.')
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Failed to list ISO: {e}')

    def choose_dest(self):
        d = QFileDialog.getExistingDirectory(self, 'Extract To',
                                              str(Path.home()))
        if d:
            self.extract_dest_edit.setText(d)

    def extract_files(self):
        iso = self.iso_path
        dest = self.extract_dest_edit.text().strip()
        if not iso or not Path(iso).is_file():
            QMessageBox.warning(self, 'Error', 'No ISO loaded.')
            return
        if not dest:
            QMessageBox.warning(self, 'Error', 'Select extraction destination.')
            return
        selected = [self.file_list[i.row()]
                    for i in self.file_table.selectionModel().selectedRows()]
        if not selected:
            QMessageBox.warning(self, 'Error', 'Select files to extract.')
            return
        dest_path = Path(dest)
        dest_path.mkdir(parents=True, exist_ok=True)
        for fpath in selected:
            out_file = dest_path / fpath.lstrip('/')
            out_file.parent.mkdir(parents=True, exist_ok=True)
            log.info('Extracting %s from ISO', fpath)
            self._extract_single(iso, fpath, str(out_file))
        QMessageBox.information(self, 'Done',
                                f'Extracted {len(selected)} file(s) to:\n{dest}')

    def _extract_single(self, iso, iso_path, out_path):
        try:
            with open(out_path, 'wb') as out_f:
                r = subprocess.run(
                    ['isoinfo', '-x', iso_path, '-i', iso],
                    capture_output=True, timeout=300)
                if r.returncode == 0:
                    out_f.write(r.stdout)
                    log.debug('Extracted %s (%d bytes)', iso_path, len(r.stdout))
                else:
                    log.warning('Failed to extract %s: %s', iso_path, r.stderr)
        except Exception as e:
            log.error('Extraction failed for %s: %s', iso_path, e)

    def burn_iso(self):
        iso = self.iso_path
        if not iso or not Path(iso).is_file():
            QMessageBox.warning(self, 'Error', 'Select a valid ISO file first.')
            return
        self.accept()
        parent = self.parent()
        if parent and hasattr(parent, '_burn_iso_dialog'):
            parent._burn_iso_dialog(Path(iso))


class ProfileManager:
    def __init__(self):
        self.settings = QSettings('BluRayDumper', 'dumper')

    def list_profiles(self):
        self.settings.beginGroup('profiles')
        profiles = self.settings.childGroups()
        self.settings.endGroup()
        return profiles

    def save_profile(self, name, config):
        self.settings.beginGroup(f'profiles/{name}')
        for k, v in config.items():
            self.settings.setValue(k, v)
        self.settings.endGroup()

    def load_profile(self, name):
        config = {}
        self.settings.beginGroup(f'profiles/{name}')
        for k in self.settings.childKeys():
            config[k] = self.settings.value(k)
        self.settings.endGroup()
        return config

    def delete_profile(self, name):
        self.settings.beginGroup(f'profiles/{name}')
        self.settings.remove('')
        self.settings.endGroup()


class DiscSpeedWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(90, 90)
        self._speed_mb_s = 0.0
        self._angle = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start(40)
        self._mode = 'idle'
        self._binary_chars = []
        self._binary_timer = QTimer(self)
        self._binary_timer.timeout.connect(self._add_binary_char)
        self._binary_timer.setInterval(100)
        self._binary_cols = 14

    def set_speed(self, mb_s):
        self._speed_mb_s = mb_s

    def set_mode(self, mode):
        self._mode = mode
        if mode == 'writing':
            self._binary_chars = []
            self._binary_timer.start()
        else:
            self._binary_timer.stop()
            self._binary_chars = []
        self.update()

    def _add_binary_char(self):
        self._binary_chars.append(random.choice('01'))
        max_rows = 7
        max_chars = max_rows * self._binary_cols
        if len(self._binary_chars) > max_chars:
            self._binary_chars = self._binary_chars[-max_chars:]
        self.update()

    def _tick(self):
        s = abs(self._speed_mb_s)
        if self._mode == 'reading' and s > 0.1:
            increment = max(0.3, min(s * 0.8, 15.0))
            self._angle = (self._angle + increment) % 360
        elif self._mode == 'reading' or self._mode == 'idle':
            self._angle = self._angle * 0.95
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        outer = min(w, h) / 2 - 4

        g = QRadialGradient(cx - outer * 0.2, cy - outer * 0.2, outer * 1.2)
        g.setColorAt(0, QColor(80, 80, 85))
        g.setColorAt(0.7, QColor(45, 45, 50))
        g.setColorAt(1, QColor(25, 25, 30))
        p.setPen(QPen(QColor(160, 160, 160), 1))
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(cx, cy), outer, outer)

        if self._mode == 'writing':
            p.save()
            clip = QPainterPath()
            clip.addEllipse(QPointF(cx, cy), outer - 2, outer - 2)
            p.setClipPath(clip)
            p.setFont(QFont('monospace', 6))
            p.setPen(QColor(0, 210, 0))
            cw, ch = 6, 10
            start_x = int(cx - (self._binary_cols * cw / 2))
            start_y = int(cy - 3 * ch)
            for i, ch_ in enumerate(self._binary_chars):
                row = i // self._binary_cols
                col = i % self._binary_cols
                p.drawText(start_x + col * cw, start_y + row * ch, ch_)
            p.restore()
            txt = f'{self._speed_mb_s:.1f} MB/s' if self._speed_mb_s > 0 else 'Writing...'
            p.setPen(QColor(180, 200, 220))
            p.setFont(QFont('sans-serif', 7))
            tr = QRectF(0, h - 14, w, 14)
            p.drawText(tr, Qt.AlignmentFlag.AlignCenter, txt)
        elif self._mode == 'reading':
            p.save()
            p.translate(cx, cy)
            p.rotate(self._angle)
            cg = QConicalGradient(QPointF(0, 0), 0)
            cg.setColorAt(0.0, QColor(255, 255, 255, 50))
            cg.setColorAt(0.125, QColor(100, 200, 255, 40))
            cg.setColorAt(0.25, QColor(200, 100, 255, 30))
            cg.setColorAt(0.375, QColor(255, 200, 100, 40))
            cg.setColorAt(0.5, QColor(100, 255, 150, 45))
            cg.setColorAt(0.625, QColor(255, 100, 100, 35))
            cg.setColorAt(0.75, QColor(150, 150, 255, 40))
            cg.setColorAt(0.875, QColor(255, 255, 100, 30))
            cg.setColorAt(1.0, QColor(255, 255, 255, 50))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(cg))
            p.drawEllipse(QPointF(0, 0), outer - 2, outer - 2)
            p.restore()
            if self._speed_mb_s > 0:
                txt = f'{self._speed_mb_s:.1f} MB/s'
                p.setPen(QColor(180, 200, 220))
                p.setFont(QFont('sans-serif', 7))
                tr = QRectF(0, h - 14, w, 14)
                p.drawText(tr, Qt.AlignmentFlag.AlignCenter, txt)

        inner = outer * 0.18
        p.setPen(QPen(QColor(120, 120, 120), 1))
        p.setBrush(QBrush(QColor(35, 35, 40)))
        p.drawEllipse(QPointF(cx, cy), inner, inner)
        p.setBrush(QBrush(QColor(240, 240, 240)))
        p.drawEllipse(QPointF(cx, cy), inner * 0.35, inner * 0.35)


class BluRayDumperWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Blu-ray Dumper')
        self.resize(720, 750)

        self.disc_label = None
        self.disc_size = 0
        self.dest_dir = None
        self.iso_path = None
        self.dump_worker = None
        self.iso_worker = None
        self.compress_worker = None
        self.dump_start_time = None
        self.elapsed = 0
        self._dump_path = None
        self._working = False
        self._cancelled = False
        self.progress_check_interval = 2
        self.queue = []
        self.device_path = DEFAULT_DEVICE
        self.auto_eject = False
        self.auto_delete = False
        self.compress_target = ''
        self.compress_custom_gb = ''
        self._last_sha256 = ''
        self.direct_to_mkv = False
        self.extract_worker = None
        self.batch_compress_worker = None
        self.remux_worker = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)

        self.setAcceptDrops(True)

        self.tray_icon = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = QSystemTrayIcon(self)
            self.tray_icon.setIcon(self.style().standardIcon(
                QStyle.StandardPixmap.SP_DriveDVDIcon))
            tray_menu = QMenu()
            show_action = QAction('Show', self)
            show_action.triggered.connect(self.show_and_raise)
            tray_menu.addAction(show_action)
            quit_action = QAction('Quit', self)
            quit_action.triggered.connect(QApplication.quit)
            tray_menu.addAction(quit_action)
            self.tray_icon.setContextMenu(tray_menu)
            self.tray_icon.activated.connect(self.on_tray_activated)
            self.tray_icon.show()
        else:
            log.debug('System tray not available')

        menubar = self.menuBar()
        opt_menu = menubar.addMenu('READ')
        disc_act = QAction('Disclaimer', self)
        disc_act.triggered.connect(self._show_disclaimer)
        opt_menu.addAction(disc_act)

        self.status_label = QLabel('Starting...')
        self.status_label.setStyleSheet('font-size: 14px; font-weight: bold;')

        disc_frame = QFrame()
        disc_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        dfl = QVBoxLayout(disc_frame)
        self.disc_info = QLabel('Checking system...')
        self.disc_info.setStyleSheet('color: #666;')
        dfl.addWidget(self.disc_info)

        top_row = QHBoxLayout()
        top_left = QVBoxLayout()
        top_left.setSpacing(4)
        top_left.addWidget(self.status_label)
        top_left.addWidget(disc_frame)
        top_row.addLayout(top_left)
        top_row.addStretch()
        self._disc_widget = DiscSpeedWidget()
        self._disc_widget.setToolTip('Disc read speed')
        top_row.addWidget(self._disc_widget, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top_row)

        btn_row1 = QHBoxLayout()
        self.refresh_btn = QPushButton('Refresh Disc Detection')
        self.refresh_btn.clicked.connect(self.check_disc)
        btn_row1.addWidget(self.refresh_btn)
        self.settings_btn = QPushButton('Settings')
        self.settings_btn.clicked.connect(self.open_settings)
        btn_row1.addWidget(self.settings_btn)
        self.catalog_btn = QPushButton('Disc Catalog')
        self.catalog_btn.clicked.connect(self.open_catalog)
        btn_row1.addWidget(self.catalog_btn)
        self.profile_load_combo = QComboBox()
        self.profile_load_combo.setPlaceholderText('Config Profiles...')
        self.profile_load_combo.currentIndexChanged.connect(self.on_profile_selected)
        btn_row1.addWidget(self.profile_load_combo)
        self.profile_save_btn = QPushButton('Save Profile')
        self.profile_save_btn.clicked.connect(self.save_current_profile)
        btn_row1.addWidget(self.profile_save_btn)
        layout.addLayout(btn_row1)

        self.dest_label = QLabel('Destination: not selected')
        self.dest_label.setWordWrap(True)
        layout.addWidget(self.dest_label)

        self.direct_mkv_cb = QCheckBox('Direct-to-MKV (skip ISO, compress immediately)')
        self.direct_mkv_cb.setChecked(self.direct_to_mkv)
        self.direct_mkv_cb.toggled.connect(self.on_direct_mkv_toggled)
        layout.addWidget(self.direct_mkv_cb)

        dest_btn_layout = QHBoxLayout()
        self.select_dest_btn = QPushButton('Select Destination Folder')
        self.select_dest_btn.clicked.connect(self.select_destination)
        dest_btn_layout.addWidget(self.select_dest_btn)
        self.open_folder_btn = QPushButton('Open Destination')
        self.open_folder_btn.clicked.connect(self.open_destination)
        self.open_folder_btn.setEnabled(False)
        dest_btn_layout.addWidget(self.open_folder_btn)
        layout.addLayout(dest_btn_layout)

        feature_row = QHBoxLayout()
        self.batch_compress_btn = QPushButton('Batch Compression')
        self.batch_compress_btn.clicked.connect(self.open_batch_compress)
        feature_row.addWidget(self.batch_compress_btn)
        self.extract_btn = QPushButton('Extract Audio/Subtitles')
        self.extract_btn.clicked.connect(self.open_extraction)
        feature_row.addWidget(self.extract_btn)
        self.restore_btn = QPushButton('Restore from ISO')
        self.restore_btn.clicked.connect(self.open_restore)
        feature_row.addWidget(self.restore_btn)
        self.clear_btn = QPushButton('Clear & Reset')
        self.clear_btn.setStyleSheet('color: #d9a04a;')
        self.clear_btn.clicked.connect(self.clear_state)
        feature_row.addWidget(self.clear_btn)
        feature_row.addStretch()
        self.exit_btn = QPushButton('Exit')
        self.exit_btn.setStyleSheet('color: #d94a4a;')
        self.exit_btn.clicked.connect(self._exit_app)
        feature_row.addWidget(self.exit_btn)
        layout.addLayout(feature_row)

        self.dump_btn = QPushButton('Dump This Disc')
        self.dump_btn.setEnabled(False)
        self.dump_btn.setStyleSheet(
            'QPushButton { background-color: #4a90d9; color: white; font-size: 16px; '
            'font-weight: bold; padding: 10px; border-radius: 6px; }'
            'QPushButton:hover { background-color: #357abd; }'
            'QPushButton:disabled { background-color: #aaa; }')
        self.dump_btn.clicked.connect(self.start_dump)
        layout.addWidget(self.dump_btn)

        btn_row = QHBoxLayout()
        self.cancel_btn = QPushButton('Cancel')
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setStyleSheet(
            'QPushButton { background-color: #d94a4a; color: white; font-size: 14px; '
            'font-weight: bold; padding: 8px; border-radius: 6px; }'
            'QPushButton:hover { background-color: #bd3535; }')
        self.cancel_btn.clicked.connect(self.cancel_operation)
        btn_row.addStretch()
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        timer_speed_row = QHBoxLayout()
        self.timer_label = QLabel()
        self.timer_label.setVisible(False)
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timer_label.setStyleSheet('font-size: 18px; font-family: monospace;')
        timer_speed_row.addWidget(self.timer_label)
        self.speed_label = QLabel()
        self.speed_label.setVisible(False)
        self.speed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.speed_label.setStyleSheet('font-size: 14px; color: #555;')
        timer_speed_row.addWidget(self.speed_label)
        layout.addLayout(timer_speed_row)

        self.log_output = QLabel()
        self.log_output.setWordWrap(True)
        self.log_output.setStyleSheet(
            'color: #555; font-style: italic; padding: 4px; '
            'background-color: #f5f5f5; border-radius: 3px;')
        self.log_output.setMaximumHeight(60)
        self.log_output.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.log_output)

        log_btn_layout = QHBoxLayout()
        self.view_log_btn = QPushButton('View Log')
        self.view_log_btn.clicked.connect(self.open_log)
        log_btn_layout.addWidget(self.view_log_btn)
        log_btn_layout.addStretch()
        layout.addLayout(log_btn_layout)

        queue_group = QGroupBox('Batch Queue')
        queue_layout = QVBoxLayout(queue_group)
        self.queue_list = QListWidget()
        self.queue_list.setAlternatingRowColors(True)
        queue_layout.addWidget(self.queue_list)
        q_btn_row = QHBoxLayout()
        self.add_queue_btn = QPushButton('Add Current Disc to Queue')
        self.add_queue_btn.clicked.connect(self.add_to_queue)
        q_btn_row.addWidget(self.add_queue_btn)
        self.remove_queue_btn = QPushButton('Remove Selected')
        self.remove_queue_btn.clicked.connect(self.remove_from_queue)
        q_btn_row.addWidget(self.remove_queue_btn)
        self.start_queue_btn = QPushButton('Start Queue')
        self.start_queue_btn.setEnabled(False)
        self.start_queue_btn.setStyleSheet(
            'QPushButton { background-color: #4a90d9; color: white; font-weight: bold; '
            'padding: 6px; border-radius: 4px; }'
            'QPushButton:disabled { background-color: #aaa; color: #ddd; }')
        self.start_queue_btn.clicked.connect(self.process_queue)
        q_btn_row.addWidget(self.start_queue_btn)
        queue_layout.addLayout(q_btn_row)
        layout.addWidget(queue_group)

        bottom_frame = QFrame()
        bottom_frame.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)
        bottom_frame.setStyleSheet('QFrame { background: #f0f0f0; border: 1px solid #ccc; }')
        bottom_layout = QVBoxLayout(bottom_frame)
        bottom_layout.setContentsMargins(6, 1, 6, 1)
        bottom_layout.setSpacing(0)
        self._bottom_quote = QLabel(self._pick_random_quote())
        self._bottom_quote.setStyleSheet('color: #888; font-style: italic; font-size: 11px;')
        bottom_layout.addWidget(self._bottom_quote)
        layout.addWidget(bottom_frame)

        self.progress_timer = QTimer()
        self.progress_timer.timeout.connect(self.update_progress)

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_timer_display)

        settings = QSettings('BluRayDumper', 'dumper')
        saved = settings.value('destination', '')
        if saved and Path(saved).is_dir():
            self.dest_dir = Path(saved)
            self.dest_label.setText(f'Destination: {self.dest_dir}')
            log.debug('Restored destination: %s', self.dest_dir)
        self.device_path = settings.value('device', DEFAULT_DEVICE)
        self.auto_eject = settings.value('auto_eject', 'false') == 'true'
        self.auto_delete = settings.value('auto_delete', 'false') == 'true'
        self.compress_target = settings.value('compress_target', '')
        self.compress_custom_gb = settings.value('compress_custom_gb', '')
        self.direct_to_mkv = settings.value('direct_to_mkv', 'false') == 'true'
        self.direct_mkv_cb.setChecked(self.direct_to_mkv)
        self.catalog = DiscCatalog()
        self.profile_mgr = ProfileManager()
        self._restore_queue()

        missing = check_required_tools()
        if missing:
            self.status_label.setText('Setup incomplete')
            self.refresh_btn.setEnabled(False)
            self.select_dest_btn.setEnabled(False)
            log.error('Missing required tools: %s', ', '.join(missing))

            pm = detect_pm()
            if pm and shutil.which('pkexec'):
                msg = QMessageBox(self)
                msg.setWindowTitle('Install Missing Tools')
                msg.setIcon(QMessageBox.Icon.Question)
                msg.setText('Required tools are missing.')
                msg.setInformativeText(
                    f'Missing: {", ".join(missing)}\n\n'
                    'Install automatically?')
                btn_install = msg.addButton('Install', QMessageBox.ButtonRole.ActionRole)
                btn_manual = msg.addButton('Show Instructions', QMessageBox.ButtonRole.ActionRole)
                msg.addButton('Skip', QMessageBox.ButtonRole.RejectRole)
                msg.exec()

                if msg.clickedButton() == btn_install:
                    if install_missing_tools(missing, parent=self, pm=pm):
                        self.status_label.setText('Tools installed')
                        self.disc_info.setText('Tools installed. Refreshing...')
                        QApplication.processEvents()
                        if not check_required_tools():
                            self.refresh_btn.setEnabled(True)
                            self.select_dest_btn.setEnabled(True)
                            self.check_disc()
                            return
                        else:
                            QMessageBox.warning(self, 'Installation Issue',
                                'Some tools may still be missing.\n'
                                'Please check the log and install manually.')
                elif msg.clickedButton() == btn_manual:
                    install_guide = '\n'.join(
                        f'{t}:\n{TOOL_INSTALL.get(t, t)}' for t in missing)
                    QMessageBox.information(self, 'Manual Install',
                        f'Install missing tools:\n\n{install_guide}')
                    self.disc_info.setText(
                        f'Missing tools: {", ".join(missing)}\n\n'
                        'Install with your package manager:\n\n'
                        f'{install_guide}')
                    return

            self.disc_info.setText(
                f'Missing tools: {", ".join(missing)}\n\n'
                'Install with your package manager:\n\n'
                + '\n'.join(f'{t}:\n{TOOL_INSTALL.get(t, t)}' for t in missing))
        else:
            self.check_disc()

    def open_log(self):
        viewer = LogViewer(self)
        viewer.exec()

    def check_disc(self):
        self.status_label.setText('Detecting disc...')
        self.disc_info.setText('Checking drive...')
        self.dump_btn.setEnabled(False)
        QApplication.processEvents()
        log.debug('Checking for disc in %s', self.device_path)

        if not os.path.exists(self.device_path):
            self.status_label.setText('Drive not found')
            self.disc_info.setText(f'Device {self.device_path} does not exist.')
            self.disc_label = None
            self.disc_size = 0
            log.warning('Device %s not found', self.device_path)
            return

        if not readable_device(self.device_path):
            self.status_label.setText('Permission denied')
            self.disc_info.setText(
                f'Cannot read {self.device_path}.\n'
                'Add yourself to the optical group:\n'
                '  sudo usermod -a -G optical $USER\n'
                'Then log out and back in.')
            self.disc_label = None
            self.disc_size = 0
            log.warning('Cannot read %s (permission denied)', self.device_path)
            return

        if not is_medium_present(self.device_path):
            self.status_label.setText('No disc detected')
            self.disc_info.setText('Insert a Blu-ray disc into the drive.')
            self.disc_label = None
            self.disc_size = 0
            return

        self.disc_size = get_disc_size(self.device_path)
        label = get_disc_label(self.device_path)
        self.disc_label = label or 'Unknown Blu-ray'

        if self.disc_label == 'Unknown Blu-ray' and self.disc_size > 0:
            for attempt in range(3):
                self.status_label.setText(f'Retrying label detection ({attempt + 1}/3)...')
                self.disc_info.setText(
                    f'Reading disc label... (attempt {attempt + 1}/3)')
                QApplication.processEvents()
                time.sleep(2)
                label = get_disc_label(self.device_path)
                if label:
                    self.disc_label = label
                    break
                log.debug('Label retry %d failed', attempt + 1)

        info = f'Disc: {self.disc_label}\nSize: {self.disc_size / (1024**3):.2f} GB'
        self.disc_info.setText(info)
        self.status_label.setText('Disc ready')
        log.info('Detected disc: %s (%.2f GB)', self.disc_label, self.disc_size / (1024**3))
        self.check_ready()

    def select_destination(self):
        d = QFileDialog.getExistingDirectory(self, 'Select Destination Directory',
                                              str(Path.home()))
        if d:
            self.dest_dir = Path(d)
            self.dest_label.setText(f'Destination: {self.dest_dir}')
            log.debug('Destination set: %s', self.dest_dir)
            settings = QSettings('BluRayDumper', 'dumper')
            settings.setValue('destination', str(self.dest_dir))
            self.check_ready()

    def check_ready(self):
        ready = (self.disc_label is not None and self.dest_dir is not None
                 and not self._working)
        self.dump_btn.setEnabled(ready)
        self.open_folder_btn.setEnabled(
            self.dest_dir is not None and self.dest_dir.exists())
        self.add_queue_btn.setEnabled(ready and self.disc_label != 'Unknown Blu-ray')
        if ready and self.disc_label:
            folder = sanitize_filename(self.disc_label)
            self.dump_btn.setText(f'Dump "{self.disc_label}" → {folder}/')

    def open_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec():
            s = QSettings('BluRayDumper', 'dumper')
            self.device_path = s.value('device', DEFAULT_DEVICE)
            self.auto_eject = s.value('auto_eject', 'false') == 'true'
            self.auto_delete = s.value('auto_delete', 'false') == 'true'
            self.compress_target = s.value('compress_target', '')
            self.compress_custom_gb = s.value('compress_custom_gb', '')
            log.info('Settings updated: device=%s, auto_eject=%s, auto_delete=%s, '
                     'compress=%s',
                     self.device_path, self.auto_eject, self.auto_delete,
                     self.compress_target)

    def open_destination(self):
        target = self._dump_path if self._dump_path and self._dump_path.exists() else self.dest_dir
        if target and target.exists():
            subprocess.run(['xdg-open', str(target)], capture_output=True)

    def notify_user(self, title, message, icon='information'):
        try:
            if self.tray_icon:
                self.tray_icon.showMessage(title, message,
                                           QSystemTrayIcon.MessageIcon.Information, 5000)
        except Exception:
            pass
        try:
            subprocess.run(['notify-send', '--icon=' + icon, title, message],
                           capture_output=True, timeout=3)
        except Exception:
            pass

    def add_to_queue(self):
        if not self.disc_label or self.disc_label == 'Unknown Blu-ray':
            return
        if not self.dest_dir:
            return
        entry = {'label': self.disc_label, 'device': self.device_path,
                 'dest': str(self.dest_dir),
                 'direct_to_mkv': self.direct_to_mkv}
        self.queue.append(entry)
        self._update_queue_list()
        self._save_queue()
        log.info('Queued: %s -> %s (MKV=%s)', self.disc_label,
                 self.dest_dir, self.direct_to_mkv)
        self.start_queue_btn.setEnabled(True)

    def remove_from_queue(self):
        row = self.queue_list.currentRow()
        if row >= 0 and row < len(self.queue):
            self.queue.pop(row)
            self._update_queue_list()
            self._save_queue()
            log.debug('Removed queue item %d', row)
        self.start_queue_btn.setEnabled(len(self.queue) > 0)

    def _update_queue_list(self):
        self.queue_list.clear()
        for entry in self.queue:
            mkv_flag = ' [MKV]' if entry.get('direct_to_mkv') else ''
            self.queue_list.addItem(
                f'{entry["label"]}{mkv_flag} → {entry["dest"]}')

    def _save_queue(self):
        s = QSettings('BluRayDumper', 'dumper')
        s.setValue('batch_queue', str(self.queue))

    def _restore_queue(self):
        s = QSettings('BluRayDumper', 'dumper')
        raw = s.value('batch_queue', '')
        if raw:
            try:
                restored = ast.literal_eval(raw)
                if isinstance(restored, list):
                    self.queue = restored
                    self._update_queue_list()
                    self.start_queue_btn.setEnabled(len(self.queue) > 0)
                    log.info('Restored %d queue items', len(self.queue))
            except Exception as e:
                log.warning('Failed to restore queue: %s', e)

    def process_queue(self):
        if not self.queue:
            return
        if self._working:
            log.warning('Already working, cannot start queue')
            return
        entry = self.queue.pop(0)
        self._update_queue_list()
        self._save_queue()
        self.disc_label = entry['label']
        self.device_path = entry.get('device', DEFAULT_DEVICE)
        self.dest_dir = Path(entry['dest'])
        self.direct_to_mkv = entry.get('direct_to_mkv', False)
        self.direct_mkv_cb.setChecked(self.direct_to_mkv)
        self.dest_label.setText(f'Destination: {self.dest_dir}')
        self.disc_info.setText(f'Disc: {self.disc_label}')
        self.status_label.setText(f'Processing: {self.disc_label}')
        log.info('Queue processing: %s -> %s (MKV=%s)', self.disc_label,
                 self.dest_dir, self.direct_to_mkv)
        self.start_dump()

    def queue_next(self):
        if self.queue:
            reply = QMessageBox.question(
                self, 'Queue',
                f'Queue has {len(self.queue)} more disc(s).\n\n'
                'Insert the next disc and press OK to continue.',
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            if reply == QMessageBox.StandardButton.Ok:
                self.check_disc()
                self.process_queue()
            else:
                self.queue.clear()
                self.queue_list.clear()
                self._save_queue()
                log.info('Queue cancelled by user')

    def generate_sha256(self, iso_path):
        sha_path = iso_path.with_name(iso_path.name + '.sha256')
        log.info('Generating SHA256 for %s', iso_path.name)
        try:
            h = hashlib.sha256()
            with open(iso_path, 'rb') as f:
                for chunk in iter(lambda: f.read(65536), b''):
                    h.update(chunk)
            content = f'{h.hexdigest()}  {iso_path.name}\n'
            sha_path.write_text(content)
            log.info('SHA256 saved to %s: %s', sha_path.name, h.hexdigest())
            return sha_path
        except Exception as e:
            log.error('SHA256 generation failed: %s', e)
            return None

    def export_session_log(self, dest_dir, disc_label):
        try:
            log_text = LOG_FILE.read_text(errors='replace')
            lines = log_text.strip().split('\n')
            relevant = [l for l in lines if disc_label in l or 'bluraybackup' in l
                        or 'genisoimage' in l or 'ISO' in l or 'Dump' in l or 'SHA256' in l]
            if not relevant:
                relevant = lines[-50:]
            out_path = dest_dir / f'{sanitize_filename(disc_label)}_session.log'
            out_path.write_text('\n'.join(relevant[-200:]) + '\n')
            log.info('Session log exported: %s', out_path)
            return out_path
        except Exception as e:
            log.warning('Session log export failed: %s', e)
            return None

    def mount_iso_verify(self, iso_path):
        try:
            r = subprocess.run(['isoinfo', '-d', '-i', str(iso_path)],
                               capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                log.warning('isoinfo failed on ISO: %s', r.stderr.strip())
                return False
            info_summary = ' | '.join(r.stdout.split('\n')[:8])
            log.info('ISO volume info: %s', info_summary)

            r2 = subprocess.run(['isoinfo', '-f', '-i', str(iso_path)],
                                capture_output=True, text=True, timeout=60)
            if r2.returncode == 0:
                bdmv_files = [l for l in r2.stdout.split('\n') if '/BDMV/' in l]
                if bdmv_files:
                    log.info('ISO spot-check: found %d BDMV entries (showing first 10)',
                             len(bdmv_files))
                    for f in bdmv_files[:10]:
                        log.debug('  %s', f)
            log.info('ISO verification successful (isoinfo)')
            return True
        except FileNotFoundError:
            log.debug('isoinfo not found, skipping ISO mount verify')
            return False
        except Exception as e:
            log.warning('ISO verify failed: %s', e)
            return False

    def start_dump(self):
        if self._working:
            log.warning('start_dump called while already working')
            return
        if not self.dest_dir or not self.disc_label:
            return

        if not ensure_tool('bluraybackup', parent=self):
            QMessageBox.critical(self, 'Missing Tool',
                                 'bluraybackup is not installed.\n\n'
                                 f'Install:\n{TOOL_INSTALL["bluraybackup"]}')
            return

        if not os.access(self.device_path, os.R_OK):
            QMessageBox.critical(self, 'Permission Denied',
                                 f'Cannot read {self.device_path}.')
            return

        if not is_medium_present(self.device_path):
            QMessageBox.critical(self, 'No Disc',
                                 'No disc detected in the drive.')
            return

        mounts = get_mounts(self.device_path)
        if mounts:
            reply = QMessageBox.warning(
                self, 'Disc Mounted',
                f'The disc is mounted at:\n{chr(10).join(mounts)}\n\n'
                'Dumping a mounted disc may cause errors.\n'
                'Continue anyway?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

        label, ok = QInputDialog.getText(self, 'Rename Disc',
                                          'Disc label:',
                                          text=self.disc_label)
        if not ok or not label.strip():
            return
        self.disc_label = label.strip()

        folder_name = sanitize_filename(self.disc_label)
        dump_path = self.dest_dir / folder_name

        free = disk_free(self.dest_dir)
        if free > 0 and self.disc_size > 0 and free < self.disc_size:
            reply = QMessageBox.warning(
                self, 'Low Disk Space',
                f'Not enough free space on destination.\n'
                f'Need: {self.disc_size / (1024**3):.2f} GB\n'
                f'Free: {free / (1024**3):.2f} GB\n\n'
                'Continue anyway?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

        if dump_path.exists():
            reply = QMessageBox.question(
                self, 'Folder Exists',
                f'The folder "{folder_name}" already exists.\n'
                'Overwrite its contents?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            log.warning('Removing existing dump folder: %s', dump_path)
            try:
                shutil.rmtree(dump_path)
            except PermissionError as e:
                QMessageBox.critical(self, 'Permission Error',
                                     f'Cannot remove existing folder: {e}')
                return
            except OSError as e:
                QMessageBox.critical(self, 'Error',
                                     f'Cannot remove existing folder: {e}')
                return

        try:
            dump_path.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            QMessageBox.critical(self, 'Permission Denied',
                                 f'Cannot create destination folder:\n{e}')
            return
        except OSError as e:
            QMessageBox.critical(self, 'Error',
                                 f'Cannot create destination folder:\n{e}')
            return

        keyfile = AACS_DIR / 'KEYDB.cfg'
        if not keyfile.is_file():
            log.warning('No KEYDB.cfg found at %s', keyfile)
            keyfile = None
        else:
            log.debug('Using keyfile: %s', keyfile)

        self._dump_path = dump_path
        self._disc_widget.set_mode('reading')
        self._enter_working_state(status_text='Dumping...', log_text='Dumping disc...',
                                  start_progress_timer=True)

        self.dump_worker = DumpWorker(
            self.device_path, str(dump_path),
            str(keyfile) if keyfile else None)
        self.dump_worker.finished.connect(self.on_dump_finished)
        self.dump_worker.output_line.connect(self.on_dump_output)
        self.dump_worker.start()
        log.info('Dump started: %s -> %s', self.device_path, dump_path)

    def on_dump_output(self, line):
        self.log_output.setText(line[-80:])

    def _on_encode_progress(self, pct, speed_text):
        self.progress_bar.setValue(pct)
        label = f'{pct}%'
        if self.elapsed > 0 and pct > 0:
            eta_s = int((self.elapsed * 100 / pct) - self.elapsed)
            label += f'  |  ETA {str(timedelta(seconds=eta_s))}'
        if speed_text:
            label += f'  |  {speed_text}'
            m = re.search(r'([\d.]+)', speed_text)
            if m:
                self._disc_widget.set_speed(float(m.group(1)))
        self.speed_label.setText(label)

    def update_progress(self):
        if not self._dump_path or not self._dump_path.exists():
            return
        if self.disc_size == 0:
            return
        try:
            current = dir_size(self._dump_path)
            pct = min(int(current * 100 / self.disc_size), 99)
            self.progress_bar.setValue(pct)

            if self.elapsed > 0 and current > 0:
                elapsed_h = self.elapsed / 3600
                speed_gb_h = (current / (1024**3)) / elapsed_h
                speed_mb_s = (current / (1024**2)) / self.elapsed
                self._disc_widget.set_speed(speed_mb_s)
                remaining = self.disc_size - current
                eta_s = remaining / (current / self.elapsed) if current > 0 else 0
                eta_str = str(timedelta(seconds=int(eta_s))) if eta_s > 0 else ''
                suffix = f'  |  ETA {eta_str}' if eta_str else ''
                self.speed_label.setText(
                    f'{speed_gb_h:.1f} GB/h  |  {speed_mb_s:.1f} MB/s{suffix}')

            log.debug('Progress: %d%% (%d / %d bytes)', pct, current, self.disc_size)
        except Exception as e:
            log.warning('Progress update failed: %s', e)

    def update_timer_display(self):
        self.elapsed += 1
        self.timer_label.setText(str(timedelta(seconds=self.elapsed)))

    def on_dump_finished(self, retcode, error):
        if self._cancelled:
            return
        log.info('Dump finished with code %d', retcode)
        self.timer.stop()
        self.progress_timer.stop()
        self.speed_label.setVisible(False)
        self._disc_widget.set_mode('idle')
        self._disc_widget.set_speed(0)

        if retcode == 0:
            self.notify_user('Blu-ray Dumper', 'Disc dump completed successfully')

        try:
            actual = dir_size(self._dump_path)
        except Exception as e:
            actual = 0
            log.error('Failed to read dump size: %s', e)

        coverage = (actual / self.disc_size * 100) if self.disc_size > 0 else 0

        if retcode != 0:
            log.warning('bluraybackup exit code %d, coverage %.1f%%', retcode, coverage)
            if coverage >= 80 and actual > 0:
                self.progress_bar.setValue(min(int(coverage), 100))
                self.status_label.setText('Dump complete (with warnings)')
                self.log_output.setText(
                    'bluraybackup reported minor errors but most data was copied.')
                self._working = False
                self.reset_buttons()
                if self.direct_to_mkv:
                    if self._handle_direct_mkv():
                        return
                msg = (
                    'Disc dump completed with minor warnings.\n\n'
                    f'Dump size: {actual / (1024**3):.2f} GB ({coverage:.0f}% of disc)\n'
                    'The data is largely intact.\n\n'
                    'Would you like to turn your BD-Dump into an ISO file?')
                reply = QMessageBox.question(
                    self, 'Dump Complete (Warnings)', msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    self.create_iso()
                else:
                    if self.auto_eject:
                        self.eject_disc()
                    elif QMessageBox.question(self, 'Eject Disc',
                        'Eject the disc?',
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                        ) == QMessageBox.StandardButton.Yes:
                        self.eject_disc()
                    self.queue_next()
                return

            self.progress_bar.setValue(0)
            self.status_label.setText('Dump failed')
            msg = error or f'bluraybackup exited with code {retcode}'
            self.log_output.setText(f'Error: {msg}')
            self._working = False
            self.reset_buttons()
            QMessageBox.critical(self, 'Dump Failed',
                                 f'Disc dump failed.\n{msg}')
            log.error('Dump failed: %s', msg)
            self.queue_next()
            return

        self.progress_bar.setValue(100)
        self.status_label.setText('Dump complete')
        self.log_output.setText('Disc dumped successfully.')
        self._working = False
        self.reset_buttons()

        if self.direct_to_mkv:
            if self._handle_direct_mkv():
                return

        msg = ('Disc dump completed successfully.\n\n'
               f'Dump size: {actual / (1024**3):.2f} GB\n\n'
               'Would you like to turn your BD-Dump into an ISO file?')

        reply = QMessageBox.question(
            self, 'Dump Complete', msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            self.create_iso()
        else:
            QMessageBox.information(
                self, 'Done',
                f'The disc dump has been saved to:\n{self._dump_path}')
            if self.auto_eject:
                self.eject_disc()
            elif QMessageBox.question(self, 'Eject Disc',
                'Eject the disc?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                ) == QMessageBox.StandardButton.Yes:
                self.eject_disc()
            self.queue_next()

    def create_iso(self):
        if self._working:
            log.warning('create_iso called while already working')
            return
        if not self._dump_path or not self._dump_path.exists():
            QMessageBox.critical(self, 'Error',
                                 'Dump folder not found. Cannot create ISO.')
            return

        if not ensure_tool('genisoimage', parent=self):
            QMessageBox.critical(self, 'Missing Tool',
                                 'genisoimage is not installed.\n\n'
                                 f'Install:\n{TOOL_INSTALL["genisoimage"]}')
            return

        iso_name = self._dump_path.name + '.iso'
        self.iso_path = self._dump_path.parent / iso_name

        free = disk_free(self._dump_path.parent)
        dump_sz = dir_size(self._dump_path)
        if free > 0 and dump_sz > 0 and free < dump_sz:
            reply = QMessageBox.warning(
                self, 'Low Disk Space',
                f'Not enough free space for ISO.\n'
                f'Estimated ISO size: {dump_sz / (1024**3):.2f} GB\n'
                f'Free: {free / (1024**3):.2f} GB\n\n'
                'Continue anyway?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

        if self.iso_path.exists():
            reply = QMessageBox.question(
                self, 'ISO Exists',
                f'{iso_name} already exists. Overwrite?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                self.iso_path.unlink()
            except OSError as e:
                QMessageBox.critical(self, 'Error',
                                     f'Cannot remove existing ISO: {e}')
                return

        self._disc_widget.set_mode('writing')
        self._enter_working_state(indeterminate=True, status_text='Creating ISO...',
                                  log_text='Creating ISO with UDF filesystem...',
                                  speed_text='Creating ISO...')

        self.iso_worker = ISOWorker(str(self._dump_path), str(self.iso_path))
        self.iso_worker.finished.connect(self.on_iso_finished)
        self.iso_worker.output_line.connect(self.on_iso_output)
        self.iso_worker.start()
        log.info('ISO creation started: %s', self.iso_path)

    def on_iso_output(self, line):
        self.log_output.setText(line[-80:])

    def on_iso_finished(self, retcode, error):
        if self._cancelled:
            return
        log.info('ISO creation finished with code %d', retcode)
        self.timer.stop()
        self.speed_label.setVisible(False)
        self._disc_widget.set_mode('idle')
        self._disc_widget.set_speed(0)
        self.progress_bar.setRange(0, 100)
        self._working = False
        self.reset_buttons()

        if retcode != 0:
            self.progress_bar.setValue(0)
            self._iso_failed(error or f'genisoimage exited with code {retcode}')
            return

        if not self.iso_path or not self.iso_path.exists():
            self._iso_failed('ISO file was not created.')
            return

        self.progress_bar.setValue(100)
        self.status_label.setText('Verifying ISO...')

        sizes = self._read_iso_sizes()
        if sizes is None:
            return
        iso_size, dump_size = sizes

        self.log_output.setText('Verifying ISO vs dump sizes...')
        QApplication.processEvents()

        tolerance = max(1024 * 1024, int(dump_size * 0.01))
        size_diff = abs(iso_size - dump_size)
        match = size_diff <= tolerance
        log.info('ISO verification: ISO %d bytes, Dump %d bytes, diff %d, tolerance %d, match=%s',
                 iso_size, dump_size, size_diff, tolerance, match)

        if match:
            self._post_iso_success(iso_size, dump_size)
        else:
            self._post_iso_mismatch(iso_size, dump_size)

    def _iso_failed(self, msg):
        self.status_label.setText('ISO creation failed')
        self.log_output.setText(f'ISO failed: {msg}')
        QMessageBox.critical(self, 'ISO Failed', f'ISO creation failed.\n{msg}')
        log.error('ISO failed: %s', msg)
        self.queue_next()

    def _read_iso_sizes(self):
        try:
            return self.iso_path.stat().st_size, dir_size(self._dump_path)
        except Exception as e:
            self.status_label.setText('Verification error')
            self.log_output.setText(f'Cannot read sizes: {e}')
            QMessageBox.critical(self, 'Error', f'Cannot verify ISO:\n{e}')
            log.error('Verification error: %s', traceback.format_exc())
            return None

    def _post_iso_success(self, iso_size, dump_size):
        self.notify_user('Blu-ray Dumper', 'ISO verified successfully')

        self.status_label.setText('Generating SHA256...')
        QApplication.processEvents()
        sha_path = self.generate_sha256(self.iso_path)
        self._last_sha256 = ''
        if sha_path:
            try:
                self._last_sha256 = sha_path.read_text().strip().split()[0]
            except Exception:
                pass

        self.status_label.setText('Exporting session log...')
        QApplication.processEvents()
        self.export_session_log(self.iso_path.parent,
                                self._dump_path.name if self._dump_path else 'bluray')

        self.status_label.setText('Cataloging...')
        QApplication.processEvents()
        self.insert_catalog_entry()

        self.status_label.setText('Spot-checking ISO contents...')
        QApplication.processEvents()
        self.mount_iso_verify(self.iso_path)

        self.status_label.setText('ISO verified')

        sha_note = f'\nSHA256: {sha_path.name}' if sha_path else ''

        compress_target = self.compress_target
        compress_bytes = 0
        if compress_target == 'custom':
            try:
                compress_bytes = int(float(self.compress_custom_gb) * 1_000_000_000)
            except (ValueError, TypeError):
                compress_target = ''
        elif compress_target in TARGET_SIZES:
            compress_bytes = TARGET_SIZES[compress_target]

        if compress_target and compress_bytes > 0 and tool_available('HandBrakeCLI'):
            label = TARGET_LABELS.get(compress_target, f'{compress_bytes / 1e9:.1f} GB')
            reply = QMessageBox.question(
                self, 'Compress ISO',
                f'ISO verified ({iso_size / (1024**3):.2f} GB).\n\n'
                f'Compress main movie to fit {label}?\n\n'
                f'This will create an MKV alongside the ISO.\n'
                f'Requires HandBrakeCLI.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self._pending_auto_delete = self.auto_delete
                self._pending_auto_eject = self.auto_eject
                self.start_compress(compress_bytes)
                return

        self._pending_auto_delete = False
        self._pending_auto_eject = False

        if self.auto_delete:
            try:
                shutil.rmtree(self._dump_path)
                self.log_output.setText('Original dump folder deleted (auto).')
                log.info('Dump folder auto-deleted: %s', self._dump_path)
            except (PermissionError, OSError) as e:
                log.error('Cannot auto-delete dump folder: %s', e)
                QMessageBox.warning(self, 'Auto-Delete Warning',
                                    f'Could not delete dump folder: {e}')

        if self.auto_eject:
            self.eject_disc()

        if not compress_target or not tool_available('HandBrakeCLI'):
            QMessageBox.information(self, 'ISO Complete',
                                    f'ISO saved as:\n{self.iso_path}{sha_note}')
        self.queue_next()

    def _post_iso_mismatch(self, iso_size, dump_size):
        self.status_label.setText('Size mismatch')
        self.log_output.setText('ISO size does not match dump size.')
        QMessageBox.warning(
            self, 'Size Mismatch',
            f'ISO size ({iso_size / (1024**3):.2f} GB) does not match\n'
            f'dump size ({dump_size / (1024**3):.2f} GB).\n'
            'The original dump folder has been preserved.')
        if self.auto_eject:
            self.eject_disc()
        elif QMessageBox.question(self, 'Eject Disc',
            'Eject the disc?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes:
            self.eject_disc()
        self.queue_next()

    def reset_buttons(self):
        self.dump_btn.setEnabled(not self._working and self.disc_label is not None
                                 and self.dest_dir is not None)
        self.refresh_btn.setEnabled(not self._working)
        self.select_dest_btn.setEnabled(not self._working)
        self.cancel_btn.setVisible(self._working)
        self.cancel_btn.setEnabled(self._working)
        self.batch_compress_btn.setEnabled(not self._working)
        self.extract_btn.setEnabled(not self._working and self._dump_path is not None)
        self.restore_btn.setEnabled(not self._working)

    def clear_state(self):
        if self._working:
            QMessageBox.warning(self, 'Busy', 'Cannot reset while working.')
            return
        self.disc_label = None
        self.disc_size = 0
        self._dump_path = None
        self.iso_path = None
        self._last_sha256 = ''
        self.status_label.setText('Ready')
        self.disc_info.setText('No disc data. Insert a disc or select a destination.')
        self.log_output.setText('')
        self.dest_label.setText('Destination: not selected')
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        self.timer_label.setVisible(False)
        self.speed_label.setVisible(False)
        self.dump_btn.setText('Dump This Disc')
        self.dump_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(False)
        log.info('State cleared by user')

    def _show_disclaimer(self):
        QMessageBox.information(self, 'Disclaimer',
            'Do not use this software to make illegal copies of copyrighted '
            'material. This software is intended only for creating backup '
            'copies of media that you legally own and are authorized to '
            'duplicate. Users are responsible for complying with all '
            'applicable copyright laws and regulations in their jurisdiction.\n\n'
            'Warning\n\n'
            'This software is provided as is, without any decryption methods, '
            'decryption capabilities, keys, or access credentials of any kind. '
            'We do not provide, distribute, assist with obtaining, or offer '
            'support for finding such methods or keys. Our obligation is '
            'limited to providing this front end software, and the '
            'functionality it offers should be considered sufficient for its '
            'intended purpose.')

    def _enter_working_state(self, show_progress=True, show_timer=True,
                              show_speed=True, indeterminate=False,
                              status_text='Working...', log_text='',
                              speed_text='', start_progress_timer=False):
        self._cancelled = False
        self._working = True
        self.dump_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.select_dest_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(True)

        self.progress_bar.setVisible(show_progress)
        if show_progress:
            self.progress_bar.setRange(0, 0 if indeterminate else 100)
            self.progress_bar.setValue(0)

        self.timer_label.setVisible(show_timer)
        if show_timer:
            self.elapsed = 0
            self.timer_label.setText('00:00:00')
            self.timer.start(1000)

        self.speed_label.setVisible(show_speed)
        if show_speed:
            self.speed_label.setText(speed_text)

        if log_text:
            self.log_output.setText(log_text)
        if status_text:
            self.status_label.setText(status_text)

        if start_progress_timer:
            self.progress_timer.start(self.progress_check_interval * 1000)

        QApplication.processEvents()

    def _handle_direct_mkv(self):
        compress_bytes = self._get_compress_bytes()
        if compress_bytes > 0:
            hb_avail = tool_available('HandBrakeCLI')
            ff_avail = tool_available('ffmpeg')
            if not hb_avail and not ff_avail:
                QMessageBox.warning(self, 'No Encoder',
                    'Compression target is set but neither HandBrakeCLI nor ffmpeg is available.\n'
                    'Skipping compression.')
                return False
            label = TARGET_LABELS.get(self.compress_target,
                                      f'{compress_bytes / 1e9:.1f} GB')
            reply = QMessageBox.question(
                self, 'Compress to MKV?',
                f'Direct-to-MKV is enabled.\n\n'
                f'Compress main movie to fit {label}?\n'
                f'This will skip ISO creation and create an MKV directly.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.start_compress(compress_bytes)
                return True
            return False

        if tool_available('ffmpeg'):
            reply = QMessageBox.question(
                self, 'Direct-to-MKV',
                'Direct-to-MKV is enabled (no compression target).\n\n'
                'Create MKV from main movie?\n'
                'Streams will be copied directly (no re-encode).\n'
                'ISO creation will be skipped.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.start_remux()
                return True
        return False

    def _get_compress_bytes(self):
        target = self.compress_target
        if target == 'custom':
            try:
                return int(float(self.compress_custom_gb) * 1_000_000_000)
            except (ValueError, TypeError):
                return 0
        return TARGET_SIZES.get(target, 0)

    def start_compress(self, target_bytes):
        if self._working:
            log.warning('start_compress called while already working')
            return
        if not self._dump_path or not self._dump_path.exists():
            QMessageBox.critical(self, 'Error', 'Dump folder not found.')
            return

        out_name = sanitize_filename(self._dump_path.name) + '_compressed.mkv'
        out_path = self._dump_path.parent / out_name

        self._disc_widget.set_mode('writing')
        self._enter_working_state(indeterminate=False, status_text='Compressing...',
                                  log_text='Compressing main movie...',
                                  show_speed=True)

        self.compress_worker = CompressWorker(
            str(self._dump_path), str(out_path), target_bytes)
        self.compress_worker.finished.connect(self.on_compress_finished)
        self.compress_worker.output_line.connect(self.on_dump_output)
        self.compress_worker.progress_updated.connect(self._on_encode_progress)
        self.compress_worker.start()
        log.info('Compression started: %s -> %s', self._dump_path, out_path)

    def start_remux(self):
        if self._working:
            log.warning('start_remux called while already working')
            return
        if not self._dump_path or not self._dump_path.exists():
            QMessageBox.critical(self, 'Error', 'Dump folder not found.')
            return
        out_name = sanitize_filename(self._dump_path.name) + '.mkv'
        out_path = self._dump_path.parent / out_name
        self._disc_widget.set_mode('writing')
        self._enter_working_state(indeterminate=False, status_text='Remuxing to MKV...',
                                  log_text='Remuxing main movie (direct copy, no re-encode)...',
                                  show_speed=True)
        self.remux_worker = RemuxWorker(str(self._dump_path), str(out_path))
        self.remux_worker.finished.connect(self.on_remux_finished)
        self.remux_worker.output_line.connect(self.on_dump_output)
        self.remux_worker.progress_updated.connect(self._on_encode_progress)
        self.remux_worker.start()
        log.info('Remux started: %s -> %s', self._dump_path, out_path)

    def eject_disc(self):
        try:
            subprocess.run(['eject', self.device_path], capture_output=True, timeout=5)
            log.info('Disc ejected')
        except FileNotFoundError:
            log.debug('eject command not found, skipping')
        except Exception as e:
            log.warning('Failed to eject disc: %s', e)

    def cancel_operation(self):
        log.warning('Cancel requested by user')
        self._cancelled = True
        self.timer.stop()
        self.progress_timer.stop()
        self.cancel_btn.setEnabled(False)

        if self.dump_worker and self.dump_worker.isRunning():
            self.dump_worker.stop()
            self.dump_worker.wait(5000)
            if self._dump_path and self._dump_path.exists():
                shutil.rmtree(self._dump_path, ignore_errors=True)
                log.info('Partial dump removed: %s', self._dump_path)

        if self.iso_worker and self.iso_worker.isRunning():
            self.iso_worker.stop()
            self.iso_worker.wait(5000)
            if self.iso_path and self.iso_path.exists():
                self.iso_path.unlink(missing_ok=True)
                log.info('Partial ISO removed: %s', self.iso_path)

        if self.compress_worker and self.compress_worker.isRunning():
            self.compress_worker.stop()
            self.compress_worker.wait(5000)

        if self.extract_worker and self.extract_worker.isRunning():
            self.extract_worker.stop()
            self.extract_worker.wait(5000)

        if self.remux_worker and self.remux_worker.isRunning():
            self.remux_worker.stop()
            self.remux_worker.wait(5000)

        if hasattr(self, '_batch_compress_queue'):
            self._batch_compress_queue = []

        self._working = False
        self.reset_buttons()
        self.status_label.setText('Cancelled')
        self.log_output.setText('Operation cancelled.')
        self.progress_bar.setVisible(False)
        self.timer_label.setVisible(False)
        QMessageBox.information(self, 'Cancelled', 'Operation cancelled.')

    def _exit_app(self):
        if self._working:
            QMessageBox.information(
                self, 'Cannot Exit',
                'Please click Cancel first to stop the current operation, '
                'then click Exit.')
            return
        self._force_quit = True
        QMessageBox.information(self, 'Goodbye', 'Thank you, come again!')
        self.close()

    def closeEvent(self, event):
        if getattr(self, '_force_quit', False):
            QApplication.quit()
            return
        if self.tray_icon and self.tray_icon.isVisible():
            self.hide()
            self.tray_icon.showMessage('Blu-ray Dumper',
                                        'Application minimized to tray.',
                                       QSystemTrayIcon.MessageIcon.Information, 2000)
            event.ignore()
            return
        if self._working:
            reply = QMessageBox.question(
                self, 'Operation in Progress',
                'An operation is in progress. Quit anyway?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            log.warning('Application closing with operation in progress')
        if self.dump_worker and self.dump_worker.isRunning():
            self.dump_worker.stop()
            self.dump_worker.wait(5000)
        if self.iso_worker and self.iso_worker.isRunning():
            self.iso_worker.stop()
            self.iso_worker.wait(5000)
        if self.compress_worker and self.compress_worker.isRunning():
            self.compress_worker.stop()
            self.compress_worker.wait(5000)
        if self.extract_worker and self.extract_worker.isRunning():
            self.extract_worker.stop()
            self.extract_worker.wait(5000)

        if self.remux_worker and self.remux_worker.isRunning():
            self.remux_worker.stop()
            self.remux_worker.wait(5000)
        event.accept()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                p = Path(url.toLocalFile())
                if p.is_dir():
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_dir():
                self.dest_dir = p
                self.dest_label.setText(f'Destination: {self.dest_dir}')
                log.debug('Destination set via drop: %s', self.dest_dir)
                settings = QSettings('BluRayDumper', 'dumper')
                settings.setValue('destination', str(self.dest_dir))
                self.check_ready()
                break


    def on_direct_mkv_toggled(self, checked):
        self.direct_to_mkv = checked
        settings = QSettings('BluRayDumper', 'dumper')
        settings.setValue('direct_to_mkv', 'true' if checked else 'false')
        log.info('Direct-to-MKV: %s', 'enabled' if checked else 'disabled')

    def show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_and_raise()

    def open_catalog(self):
        dialog = CatalogDialog(self)
        dialog.exec()

    def on_profile_selected(self, idx):
        if idx < 0:
            return
        name = self.profile_load_combo.itemText(idx)
        if not name:
            return
        config = self.profile_mgr.load_profile(name)
        if config:
            self.device_path = config.get('device', DEFAULT_DEVICE)
            s = QSettings('BluRayDumper', 'dumper')
            s.setValue('device', self.device_path)
            self.auto_eject = config.get('auto_eject', 'false') == 'true'
            self.auto_delete = config.get('auto_delete', 'false') == 'true'
            self.compress_target = config.get('compress_target', '')
            self.compress_custom_gb = config.get('compress_custom_gb', '')
            s.setValue('auto_eject', 'true' if self.auto_eject else 'false')
            s.setValue('auto_delete', 'true' if self.auto_delete else 'false')
            s.setValue('compress_target', self.compress_target)
            s.setValue('compress_custom_gb', self.compress_custom_gb)
            dest = config.get('destination', '')
            if dest and Path(dest).is_dir():
                self.dest_dir = Path(dest)
                self.dest_label.setText(f'Destination: {self.dest_dir}')
                s.setValue('destination', dest)
            self.direct_to_mkv = config.get('direct_to_mkv', 'false') == 'true'
            self.direct_mkv_cb.blockSignals(True)
            self.direct_mkv_cb.setChecked(self.direct_to_mkv)
            self.direct_mkv_cb.blockSignals(False)
            s.setValue('direct_to_mkv', 'true' if self.direct_to_mkv else 'false')
            log.info('Profile loaded: %s', name)
            QMessageBox.information(self, 'Profile Loaded',
                                    f'Configuration profile "{name}" loaded.')
            self.check_disc()

    def refresh_profile_list(self):
        self.profile_load_combo.blockSignals(True)
        current = self.profile_load_combo.currentText()
        self.profile_load_combo.clear()
        self.profile_load_combo.addItem('')
        for name in self.profile_mgr.list_profiles():
            self.profile_load_combo.addItem(name)
        idx = self.profile_load_combo.findText(current)
        if idx >= 0:
            self.profile_load_combo.setCurrentIndex(idx)
        self.profile_load_combo.blockSignals(False)

    def save_current_profile(self):
        name, ok = QInputDialog.getText(self, 'Save Profile',
                                         'Profile name:',
                                         text='')
        if not ok or not name.strip():
            return
        name = name.strip()
        config = {
            'device': self.device_path,
            'destination': str(self.dest_dir) if self.dest_dir else '',
            'auto_eject': 'true' if self.auto_eject else 'false',
            'auto_delete': 'true' if self.auto_delete else 'false',
            'compress_target': self.compress_target,
            'compress_custom_gb': self.compress_custom_gb,
            'direct_to_mkv': 'true' if self.direct_to_mkv else 'false',
        }
        self.profile_mgr.save_profile(name, config)
        self.refresh_profile_list()
        log.info('Profile saved: %s', name)
        QMessageBox.information(self, 'Profile Saved',
                                f'Configuration profile "{name}" saved.')

    def open_batch_compress(self):
        if self._working:
            QMessageBox.warning(self, 'Busy', 'An operation is in progress.')
            return
        dialog = BatchCompressDialog(self)
        if dialog.exec():
            folders = dialog.folders
            target_bytes = dialog.get_target_bytes()
            if not folders or target_bytes <= 0:
                return
            if not ensure_tool('HandBrakeCLI', parent=self):
                QMessageBox.critical(self, 'Missing Tool',
                                     'HandBrakeCLI is not installed.')
                return
            self._batch_compress_queue = folders
            self._batch_compress_target = target_bytes
            self._working = True
            self.reset_buttons()
            self.status_label.setText('Starting batch compression...')
            QApplication.processEvents()
            self._process_batch_compress()

    def _process_batch_compress(self):
        if not self._batch_compress_queue:
            self._working = False
            self.reset_buttons()
            QMessageBox.information(self, 'Batch Complete',
                                    'All folders compressed.')
            return
        folder = Path(self._batch_compress_queue.pop(0))
        if not folder.is_dir() or not (folder / 'BDMV' / 'STREAM').is_dir():
            log.warning('Skipping invalid folder: %s', folder)
            self._process_batch_compress()
            return
        self._dump_path = folder
        self.start_compress(self._batch_compress_target)

    @staticmethod
    def _is_dvd_target(target):
        return target in ('dvd5', 'dvd9')

    def _finish_after_compress(self):
        if getattr(self, '_pending_auto_delete', False) and self._dump_path:
            try:
                shutil.rmtree(self._dump_path)
                self.log_output.setText('Original dump folder deleted (auto).')
                log.info('Dump folder auto-deleted: %s', self._dump_path)
            except (PermissionError, OSError) as e:
                log.error('Cannot auto-delete dump folder: %s', e)
        if getattr(self, '_pending_auto_eject', False):
            self.eject_disc()
        self._pending_auto_delete = False
        self._pending_auto_eject = False
        self._disc_widget.set_mode('idle')
        self._disc_widget.set_speed(0)

    def on_compress_finished(self, retcode, error):
        if self._cancelled:
            return
        log.info('Compression finished with code %d', retcode)
        self.timer.stop()
        self._working = False
        self.reset_buttons()

        handled = False

        if retcode != 0:
            self.status_label.setText('Compression failed')
            ename = getattr(self.compress_worker, 'encoder_name', 'encoder')
            msg = error or f'{ename} exited with code {retcode}'
            self.log_output.setText(f'Compression failed: {msg}')
            QMessageBox.critical(self, 'Compression Failed', msg)
            log.error('Compression failed: %s', msg)
        else:
            mkv_path = Path(self.compress_worker.output_path)

            if not mkv_path.is_file() or mkv_path.stat().st_size < 10 * 1024 * 1024:
                log.error('Compression reported success but no valid output at %s', mkv_path)
                self.status_label.setText('Compression failed')
                self.log_output.setText(
                    'Encoder reported success but created no output.\n'
                    'The size target may not be supported by this version.')
                QMessageBox.critical(self, 'Compression Failed',
                    'Compression exited with code 0 but did not produce a valid MKV.\n'
                    'Check that your version supports the requested options.\n'
                    'See log for details.')
                return

            self.status_label.setText('Compression complete')
            self.log_output.setText('Compressed MKV created.')

            if tool_available('ffmpeg') and self._is_dvd_target(self.compress_target):
                handled = self._offer_dvd_output(mkv_path)
            else:
                QMessageBox.information(
                    self, 'Compression Done',
                    f'Compressed MKV saved as:\n{mkv_path}')

        self._finish_after_compress()

        self.progress_bar.setVisible(False)
        self.timer_label.setVisible(False)

        if not handled:
            if hasattr(self, '_batch_compress_queue'):
                self._process_batch_compress()
            else:
                self.queue_next()

    def on_remux_finished(self, retcode, error):
        if self._cancelled:
            return
        log.info('Remux finished with code %d', retcode)
        self.timer.stop()
        self._working = False
        self.reset_buttons()
        self.progress_bar.setVisible(False)
        self.timer_label.setVisible(False)

        if retcode != 0:
            self.status_label.setText('Remux failed')
            msg = error or f'ffmpeg exited with code {retcode}'
            self.log_output.setText(f'Remux failed: {msg}')
            QMessageBox.critical(self, 'Remux Failed',
                f'MKV remuxing failed.\n{msg}')
            log.error('Remux failed: %s', msg)
        else:
            mkv_path = Path(self.remux_worker.output_path)
            if not mkv_path.is_file() or mkv_path.stat().st_size < 1024 * 1024:
                self.status_label.setText('Remux failed')
                self.log_output.setText('Output MKV is empty or missing.')
                QMessageBox.critical(self, 'Remux Failed',
                    'ffmpeg reported success but output is missing or too small.')
                log.error('Remux produced no valid output at %s', mkv_path)
            else:
                self.status_label.setText('Remux complete')
                self.log_output.setText(f'MKV created: {mkv_path.name}')
                QMessageBox.information(self, 'Remux Complete',
                    f'MKV saved as:\n{mkv_path}')
                log.info('Remux successful: %s (%.2f GB)',
                         mkv_path, mkv_path.stat().st_size / (1024**3))

        self._finish_after_compress()
        self.queue_next()

    def _offer_dvd_output(self, mkv_path):
        dlg = QDialog(self)
        dlg.setWindowTitle('DVD Output Format')
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(
            f'Compressed video ready at:\n{mkv_path.name}\n\n'
            'Choose output format:'))
        self._dvd_mkv_rb = QRadioButton('MKV — keep as-is')
        self._dvd_avchd_rb = QRadioButton('AVCHD ISO — HD on DVD (plays on PS3/PS4/Blu-ray players)')
        self._dvd_vob_rb = QRadioButton('DVD-SDVIDEO ISO — standard definition DVD (plays on standard DVD players)')
        self._dvd_mkv_rb.setChecked(True)
        layout.addWidget(self._dvd_mkv_rb)
        layout.addWidget(self._dvd_avchd_rb)
        layout.addWidget(self._dvd_vob_rb)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(btns)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            QMessageBox.information(
                self, 'Compression Done',
                f'Compressed MKV saved as:\n{mkv_path}')
            return False
        iso_ok = False
        if self._dvd_vob_rb.isChecked():
            iso_ok = self._create_dvd_video_iso(mkv_path)
        elif self._dvd_avchd_rb.isChecked():
            iso_ok = self._create_avchd_iso(mkv_path)
        else:
            QMessageBox.information(
                self, 'Compression Done',
                f'Compressed MKV saved as:\n{mkv_path}')
            return False
        if iso_ok:
            self._cleanup_after_burn(mkv_path)
            return True
        return False

    def _cleanup_after_burn(self, mkv_path):
        dlg = QDialog(self)
        dlg.setWindowTitle('Clean Up')
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(
            'DVD/AVCHD creation complete.\n\n'
            'Temporary working directory can be deleted.'))
        cb_del = QCheckBox('Delete temporary files')
        cb_del.setChecked(True)
        layout.addWidget(cb_del)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        layout.addWidget(btns)
        btns.accepted.connect(dlg.accept)
        dlg.exec()
        deleted = False
        if cb_del.isChecked():
            d = Path(self._current_dvd_workdir) if hasattr(self, '_current_dvd_workdir') else None
            if d and d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                log.info('Deleted temp workdir: %s', d)
                deleted = True
        msg = 'DVD/AVCHD output created from:\n' + str(mkv_path)
        if deleted:
            msg += '\n\nTemporary files deleted.'
        QMessageBox.information(self, 'Complete', msg)

    @staticmethod
    def _run_subprocess_streaming(cmd, timeout=3600, label='process'):
        result = [None]
        exc = [None]
        def _run():
            try:
                result[0] = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout)
            except Exception as e:
                exc[0] = e
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        while thread.is_alive():
            thread.join(0.05)
            QApplication.processEvents()
        if exc[0] is not None:
            raise exc[0]
        r = result[0]
        if r.returncode != 0:
            log.error('%s failed (code=%d): stdout=%s, stderr=%s',
                      label, r.returncode,
                      r.stdout[-500:] if r.stdout else '',
                      r.stderr[-500:] if r.stderr else '')
        else:
            log.debug('%s succeeded (code=0): stdout=%s, stderr=%s',
                      label,
                      r.stdout[-200:] if r.stdout else '',
                      r.stderr[-200:] if r.stderr else '')
        return r.returncode, r.stdout, r.stderr

    def _create_avchd_iso(self, mkv_path):
        self._disc_widget.set_mode('writing')
        self.status_label.setText('Creating AVCHD DVD...')
        self.log_output.setText('Remuxing with ffmpeg...')
        QApplication.processEvents()

        mkv_size = Path(mkv_path).stat().st_size
        if mkv_size < 10 * 1024 * 1024:
            QMessageBox.critical(self, 'AVCHD Error',
                                 f'Compressed MKV is only {mkv_size / (1024*1024):.1f} MB.\n'
                                 'Something went wrong during compression.')
            return

        name_stem = Path(mkv_path).stem.replace('_compressed', '')
        avchd_dir = Path(mkv_path).parent / f'avchd_{name_stem}'
        iso_path = avchd_dir.parent / f'{name_stem}_avchd.iso'

        if avchd_dir.exists():
            shutil.rmtree(avchd_dir)

        log.info('AVCHD: remuxing MKV to M2TS')
        self.log_output.setText('Remuxing to M2TS...')
        QApplication.processEvents()
        m2ts_path = avchd_dir / 'temp.m2ts'
        avchd_dir.mkdir(parents=True, exist_ok=True)

        rc, out, err = self._run_subprocess_streaming(
            ['ffmpeg', '-i', str(mkv_path), '-c:v', 'copy', '-c:a', 'ac3', '-b:a', '448k',
             '-f', 'mpegts', '-mpegts_m2ts_mode', '1', '-y', str(m2ts_path)],
            label='ffmpeg_m2ts')
        if rc != 0:
            err_msg = (err[-500:] if err else 'unknown error').strip()
            self.status_label.setText('AVCHD remux failed')
            self.log_output.setText(err_msg)
            QMessageBox.critical(self, 'AVCHD Error',
                f'ffmpeg remux to M2TS failed (code {rc}):\n{err_msg}')
            shutil.rmtree(avchd_dir, ignore_errors=True)
            return

        if m2ts_path.stat().st_size < 10 * 1024 * 1024:
            QMessageBox.critical(self, 'AVCHD Error',
                f'M2TS is only {m2ts_path.stat().st_size / (1024*1024):.1f} MB.\n'
                'The video stream may be corrupt.\n'
                f'Check {LOG_FILE} for ffprobe/ffmpeg output during remux.')
            shutil.rmtree(avchd_dir, ignore_errors=True)
            return

        self.log_output.setText('Building AVCHD structure...')
        QApplication.processEvents()
        try:
            create_avchd_structure(m2ts_path, avchd_dir)
        except Exception as e:
            self.status_label.setText('AVCHD structure failed')
            err_msg = traceback.format_exc()
            log.error('AVCHD structure creation failed:\n%s', err_msg)
            self.log_output.setText(str(e))
            QMessageBox.critical(self, 'AVCHD Error',
                f'AVCHD structure creation failed:\n{e}\n\n'
                f'Check {LOG_FILE} for details.')
            shutil.rmtree(avchd_dir, ignore_errors=True)
            return
        finally:
            if m2ts_path.exists():
                m2ts_path.unlink()

        cert_dir = avchd_dir / 'CERTIFICATE'
        cert_dir.mkdir(parents=True, exist_ok=True)
        id_data = bytearray(6144)
        id_data[0:12] = b'CERTIFICATE\x00\x00\x00'
        (cert_dir / 'id.bdmv').write_bytes(bytes(id_data))

        vol_id = name_stem.replace('_', ' ')[:32].upper().strip() or 'AVCHD'
        self.log_output.setText('Creating AVCHD ISO (UDF 2.50)...')
        QApplication.processEvents()

        if not ensure_tool('mkudffs', parent=self):
            QMessageBox.critical(self, 'Missing Tool',
                'mkudffs (from udftools) is required for AVCHD ISO.\n'
                f'{TOOL_INSTALL["mkudffs"]}')
            shutil.rmtree(avchd_dir, ignore_errors=True)
            return

        total_size = sum(f.stat().st_size for f in avchd_dir.rglob('*') if f.is_file()) + 10 * 1024 * 1024
        blocks = (total_size + 2047) // 2048

        rc, out, err = self._run_subprocess_streaming(
            ['mkudffs', '--media-type', 'hd', '--udfrev', '2.01',
             '--label', vol_id, '--blocksize', '2048',
             str(iso_path), str(blocks)],
            label='mkudffs')
        if rc != 0:
            err_msg = (err[-500:] if err else 'unknown error').strip()
            self.status_label.setText('AVCHD ISO failed')
            self.log_output.setText(err_msg)
            QMessageBox.critical(self, 'AVCHD Error',
                f'UDF filesystem creation failed (code {rc}):\n{err_msg}')
            shutil.rmtree(avchd_dir, ignore_errors=True)
            return

        iso_path.chmod(0o666)
        self.log_output.setText('Populating ISO with AVCHD structure...')
        QApplication.processEvents()

        import tempfile
        script_fd, script_path = tempfile.mkstemp(
            suffix='.sh', prefix='avchd_populate_')
        os.close(script_fd)
        try:
            script_lines = [
                '#!/bin/sh',
                'set -e',
                f'ISO="{iso_path}"',
                f'SRC="{avchd_dir}"',
                'MNT=$(mktemp -d /tmp/avchd_mount_XXXXXX)',
                'trap "rmdir \"$MNT\" 2>/dev/null; exit" EXIT INT TERM',
                'LOOP=$(losetup -f --show "$ISO")',
                'mount -o rw "$LOOP" "$MNT"',
                'cp -a "$SRC/BDMV" "$MNT/"',
                'cp -a "$SRC/CERTIFICATE" "$MNT/"',
                'sync',
                'umount "$MNT"',
                'losetup -d "$LOOP"',
            ]
            with open(script_path, 'w') as f:
                f.write('\n'.join(script_lines) + '\n')
            os.chmod(script_path, 0o755)

            rc2, out2, err2 = self._run_subprocess_streaming(
                ['pkexec', script_path], label='isopopulate')
            if rc2 != 0:
                err_msg = (err2[-500:] if err2 else 'populate failed').strip()
                self.status_label.setText('AVCHD ISO failed')
                self.log_output.setText(err_msg)
                QMessageBox.critical(self, 'AVCHD Error',
                    f'Failed to populate ISO (code {rc2}):\n{err_msg}')
                shutil.rmtree(avchd_dir, ignore_errors=True)
                return
        finally:
            if script_path and os.path.exists(script_path):
                os.unlink(script_path)

        shutil.rmtree(avchd_dir, ignore_errors=True)
        self._current_dvd_workdir = str(avchd_dir)
        self.status_label.setText('AVCHD DVD ISO created')
        self.log_output.setText(f'Created: {iso_path.name}')
        self._burn_iso_dialog(iso_path)
        log.info('AVCHD ISO created: %s', iso_path)
        return True

    @staticmethod
    def _detect_dvd_format(mpeg_path):
        try:
            r = subprocess.run(
                ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                 '-show_entries', 'stream=height', '-of', 'csv=p=0',
                 str(mpeg_path)],
                capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip():
                h = int(r.stdout.strip())
                return 'pal' if h > 480 else 'ntsc'
        except Exception:
            pass
        return 'ntsc'

    def _create_dvd_video_iso(self, mkv_path):
        self._disc_widget.set_mode('writing')
        self.status_label.setText('Creating DVD-Video...')
        self.log_output.setText('Encoding to DVD-compatible MPEG-2...')
        QApplication.processEvents()

        name_stem = Path(mkv_path).stem.replace('_compressed', '')
        dvd_dir = Path(mkv_path).parent / f'dvd_{name_stem}'
        dvd_dir.mkdir(parents=True, exist_ok=True)

        mpg_path = dvd_dir / 'temp.mpg'
        scaled = self._detect_dvd_format(mkv_path)
        w, h = ('720', '480') if scaled == 'ntsc' else ('720', '576')
        rc, out, err = self._run_subprocess_streaming(
            ['ffmpeg', '-i', str(mkv_path), '-c:v', 'mpeg2video',
             '-vf', f'scale={w}:{h}:force_original_aspect_ratio=decrease,'
                   f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2',
             '-b:v', '8M', '-maxrate', '9M', '-bufsize', '2M',
             '-c:a', 'ac3', '-ac', '2', '-b:a', '448k',
             '-f', 'dvd', '-y', str(mpg_path)],
            label='ffmpeg_dvd')
        if rc != 0:
            err_msg = (err[-500:] if err else 'unknown error').strip()
            self.status_label.setText('DVD encode failed')
            self.log_output.setText(err_msg)
            QMessageBox.critical(self, 'DVD Error',
                f'MPEG-2 encoding failed (code {rc}):\n{err_msg}')
            shutil.rmtree(dvd_dir, ignore_errors=True)
            return

        if tool_available('dvdauthor'):
            self.status_label.setText('Authoring DVD-Video...')
            self.log_output.setText('Running dvdauthor...')
            QApplication.processEvents()
            fmt = self._detect_dvd_format(mpg_path)
            dvd_size = '720x480' if fmt == 'ntsc' else '720x576'
            vmgm_mpg = dvd_dir / 'vmgm.mpg'
            rc2, _, _ = self._run_subprocess_streaming(
                ['ffmpeg', '-f', 'lavfi', '-i', 'color=c=black:s=' + dvd_size + ':d=0.04',
                 '-c:v', 'mpeg2video', '-frames:v', '1',
                 '-b:v', '1M', '-y', str(vmgm_mpg)],
                timeout=120, label='ffmpeg_vmgm')
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<dvdauthor dest="{dvd_dir}">\n'
                f'  <vmgm>\n'
                '    <menus>\n'
                '      <pgc>\n'
                f'        <vob file="{vmgm_mpg}"/>\n'
                '      </pgc>\n'
                '    </menus>\n'
                '  </vmgm>\n'
                '  <titleset>\n'
                '    <titles>\n'
                '      <pgc>\n'
                f'        <vob file="{mpg_path}"/>\n'
                '      </pgc>\n'
                '    </titles>\n'
                '  </titleset>\n'
                '</dvdauthor>\n'
            )
            xml_path = dvd_dir / 'dvd.xml'
            xml_path.write_text(xml)
            rc3, out3, err3 = self._run_subprocess_streaming(
                ['dvdauthor', '-x', str(xml_path)],
                timeout=300, label='dvdauthor')
            if rc3 != 0:
                err_msg = (err3[-500:] if err3 else 'unknown error').strip()
                self.status_label.setText('DVD authoring failed')
                self.log_output.setText(err_msg)
                QMessageBox.critical(self, 'DVD Error',
                    f'dvdauthor failed (code {rc3}):\n{err_msg}\n\n'
                    f'Check {LOG_FILE} for full output.')
                shutil.rmtree(dvd_dir, ignore_errors=True)
                return
            if mpg_path.exists():
                mpg_path.unlink()
            if vmgm_mpg.exists():
                vmgm_mpg.unlink()
            iso_cmd = ['genisoimage', '-dvd-video', '-o']
        else:
            self.log_output.setText('dvdauthor not found — creating VOB fallback...')
            QApplication.processEvents()
            video_ts = dvd_dir / 'VIDEO_TS'
            video_ts.mkdir(exist_ok=True)
            vob_path = video_ts / 'VTS_01_1.VOB'
            shutil.move(str(mpg_path), str(vob_path))
            _write_minimal_dvd_ifo(video_ts)
            iso_cmd = ['genisoimage', '-udf', '-o']

        iso_path = dvd_dir.parent / f'{name_stem}_dvd.iso'
        self.log_output.setText('Creating DVD ISO...')
        QApplication.processEvents()
        rc4, out4, err4 = self._run_subprocess_streaming(
            iso_cmd + [str(iso_path), str(dvd_dir)],
            timeout=3600, label='genisoimage_dvd')
        if rc4 != 0:
            err_msg = (err4[-500:] if err4 else 'unknown error').strip()
            self.status_label.setText('DVD ISO failed')
            self.log_output.setText(err_msg)
            QMessageBox.critical(self, 'DVD Error',
                f'ISO creation failed (code {rc4}):\n{err_msg}')
            shutil.rmtree(dvd_dir, ignore_errors=True)
            return

        shutil.rmtree(dvd_dir, ignore_errors=True)
        self._current_dvd_workdir = str(dvd_dir)
        self.status_label.setText('DVD-Video ISO created')
        self.log_output.setText(f'Created: {iso_path.name}')
        self._burn_iso_dialog(iso_path)
        log.info('DVD-Video ISO created: %s', iso_path)
        return True

    def _burn_iso_dialog(self, iso_path):
        msg = QMessageBox(self)
        msg.setWindowTitle('Burn DVD')
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setText(f'ISO ready:\n{iso_path.name}')
        msg.setInformativeText('How would you like to burn this disc?')
        btn_k3b = msg.addButton('Open in K3B', QMessageBox.ButtonRole.ActionRole)
        btn_wodim = msg.addButton('Burn with wodim', QMessageBox.ButtonRole.ActionRole)
        btn_skip = msg.addButton('Skip', QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_skip)
        msg.exec()

        if msg.clickedButton() == btn_k3b:
            subprocess.Popen(['k3b', '--image', str(iso_path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            QMessageBox.information(self, 'K3B Launched',
                                    'K3B has been opened with the ISO loaded.\n'
                                    'Review settings and click Burn.')
        elif msg.clickedButton() == btn_wodim:
            self._run_wodim_burn(iso_path)

    def _run_wodim_burn(self, iso_path):
        self._disc_widget.set_mode('writing')
        self._disc_widget.set_speed(0)
        self.status_label.setText('Burning DVD...')
        self.log_output.setText(f'Burning {iso_path.name} with wodim...')
        dev = QInputDialog.getText(self, 'Disc Device',
                                   'Enter DVD burner device:',
                                   text='/dev/sr0')
        if not dev[1] or not dev[0].strip():
            return
        device = dev[0].strip()
        burner = BurnWorker(self, iso_path, device)
        burner.finished.connect(
            lambda ok, path: self._on_burn_finished(ok, path))
        burner.start()

    def _on_burn_finished(self, ok, iso_path):
        self._disc_widget.set_mode('idle')
        self._disc_widget.set_speed(0)
        if ok:
            QMessageBox.information(
                self, 'Burn Complete',
                f'ISO burned successfully:\n{iso_path}\n\n'
                'Verify playback on your set-top player.')
        else:
            QMessageBox.critical(
                self, 'Burn Failed',
                f'Failed to burn:\n{iso_path}\n\n'
                f'Check the log for details.')

    def open_extraction(self):
        if self._working:
            QMessageBox.warning(self, 'Busy', 'An operation is in progress.')
            return
        if not self._dump_path or not self._dump_path.exists():
            QMessageBox.warning(self, 'No Dump',
                                'No dump folder available. Dump a disc first.')
            return
        stream_dir = self._dump_path / 'BDMV' / 'STREAM'
        if not stream_dir.is_dir():
            QMessageBox.warning(self, 'No Streams',
                                'No BDMV/STREAM directory found in dump.')
            return
        m2ts_files = list(stream_dir.glob('*.m2ts'))
        if not m2ts_files:
            QMessageBox.warning(self, 'No M2TS', 'No M2TS files found.')
            return
        main_movie = max(m2ts_files, key=lambda f: f.stat().st_size)
        if not tool_available('ffprobe') or not tool_available('ffmpeg'):
            QMessageBox.critical(self, 'Missing Tool',
                                 'ffmpeg/ffprobe not found. Install ffmpeg.')
            return
        streams = list_streams(main_movie)
        if not streams:
            QMessageBox.information(self, 'No Streams',
                                    'No audio/subtitle streams detected.')
            return
        dialog = StreamSelectDialog(streams, self)
        if dialog.exec():
            selected = dialog.selected
            extract_dir = self._dump_path.parent / (
                sanitize_filename(self._dump_path.name) + '_extracted')
            extract_dir.mkdir(parents=True, exist_ok=True)
            self._disc_widget.set_mode('writing')
            self._enter_working_state(show_progress=False, show_timer=False,
                                      show_speed=False,
                                      status_text='Extracting...',
                                      log_text='Extracting streams...')
            self.extract_worker = ExtractWorker(str(self._dump_path), selected, str(extract_dir))
            self.extract_worker.finished.connect(self.on_extract_finished)
            self.extract_worker.output_line.connect(self.on_dump_output)
            self.extract_worker.start()
            log.info('Stream extraction started to %s', extract_dir)

    def on_extract_finished(self, retcode, error):
        if self._cancelled:
            return
        self._disc_widget.set_mode('idle')
        self._disc_widget.set_speed(0)
        self._working = False
        self.reset_buttons()
        if retcode == 0:
            extract_dir = Path(self.extract_worker.output_dir) if hasattr(self.extract_worker, 'output_dir') else ''
            self.status_label.setText('Extraction complete')
            self.log_output.setText('Streams extracted successfully.')
            QMessageBox.information(self, 'Extraction Done',
                                    f'Streams extracted to:\n{extract_dir}')
        else:
            self.status_label.setText('Extraction failed')
            self.log_output.setText(f'Extraction error: {error}')
            QMessageBox.critical(self, 'Extraction Failed', error)

    def open_restore(self):
        dialog = IsoBrowserDialog(self)
        dialog.exec()

    def update_profiles_in_ui(self):
        self.refresh_profile_list()

    def insert_catalog_entry(self):
        try:
            iso_size = self.iso_path.stat().st_size if self.iso_path else 0
            dump_size = dir_size(self._dump_path) if self._dump_path else 0
            self.catalog.insert_dump(
                label=self.disc_label or 'Unknown',
                device=self.device_path,
                disc_size=self.disc_size,
                dump_size=dump_size,
                iso_size=iso_size,
                sha256=self._last_sha256 if hasattr(self, '_last_sha256') else '',
                compress_status=self.compress_target or '',
            )
        except Exception as e:
            log.warning('Catalog insert failed: %s', e)

    def _pick_random_quote(self):
        return random.choice(IMG_QUOTES)

def global_exception_handler(exc_type, exc_value, exc_tb):
    log.critical('Unhandled exception',
                 exc_info=(exc_type, exc_value, exc_tb))
    _write_crash_dump('main_thread', (exc_type, exc_value, exc_tb))
    try:
        app = QApplication.instance()
        if app:
            QMessageBox.critical(
                None, 'Unexpected Error',
                f'An unexpected error occurred:\n\n{exc_value}\n\n'
                'Check the log for details.\n'
                'A crash dump was saved to ~/bluray_dumper_crash.log')
    except Exception:
        pass


def thread_exception_handler(args):
    log.critical('Unhandled exception in thread: %s',
                 ''.join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_tb)))
    _write_crash_dump(f'thread:{args.thread.name if args.thread else "unknown"}',
                      (args.exc_type, args.exc_value, args.exc_tb))


def log_system_info():
    log.info('=== System Info ===')
    log.info('Python: %s', sys.version)
    log.info('Platform: %s', platform.platform())
    log.info('CPU: %s', platform.processor() or 'unknown')
    log.info('Memory: %s', platform.machine())
    for prog, ver_flag in [('ffmpeg', '-version'), ('HandBrakeCLI', '--version'),
                            ('bluraybackup', '--version'), ('genisoimage', '--version'),
                            ('blockdev', '--version'), ('eject', '--version'),
                            ('mkudffs', '--version'), ('wodim', '--version')]:
        try:
            r = subprocess.run([prog, ver_flag], capture_output=True, text=True, timeout=5)
            first = (r.stdout or r.stderr or '').split('\n')[0][:120]
            log.info('%s: %s', prog, first)
        except Exception:
            log.info('%s: NOT FOUND', prog)
    try:
        st = os.statvfs(str(Path.home()))
        free_gb = (st.f_frsize * st.f_bavail) / (1024**3)
        log.info('Free space on home: %.2f GB', free_gb)
    except Exception:
        pass
    log.info('===================')




IMG_QUOTES = [
    "I'm not insane, I'm just not as sane as you.",
    "Don't Panic!",
    "Only two things are infinite: the universe and human stupidity, and I'm not sure about the former.",
    "All those moments will be lost in time, like tears in rain.",
    "I'll be back.",
    "It's only a model.",
    "Bring me a shrubbery!",
    "Your money or your life!",
    "You shall not pass!",
    "Great success!",
    "I find your lack of faith disturbing.",
    "Do or do not. There is no try.",
    "To infinity and beyond!",
    "Winter is coming.",
    "Hasta la vista, baby.",
    "My precious.",
    "I see dead people.",
    "I'm too old for this ****.",
    "Go ahead, make my day.",
    "There's no place like home.",
    "Life is like a box of chocolates. You never know what you're gonna get.",
    "I feel the need—the need for speed!",
    "Here's looking at you, kid.",
    "May the Force be with you.",
    "The name's Bond, James Bond.",
    "Elementary, my dear Watson.",
    "I think, therefore I am.",
    "Cogito ergo sum.",
    "I came, I saw, I conquered.",
    "Veni, vidi, vici.",
    "I'll have what she's having.",
    "We're going to need a bigger boat.",
    "Show me the money!",
    "You had me at hello.",
    "I'm walking here!",
    "Keep your friends close, but your enemies closer.",
    "With great power comes great responsibility.",
    "Why so serious?",
    "I believe whatever doesn't kill you simply makes you... stranger.",
    "A martini. Shaken, not stirred.",
    "Bond. James Bond.",
    "To be or not to be, that is the question.",
    "The only thing we have to fear is fear itself.",
    "Ask not what your country can do for you...",
    "I have a dream.",
    "Mr. Anderson.",
    "There is no spoon.",
    "I know kung fu.",
    "Follow the white rabbit.",
    "Ignorance is bliss.",
    "Resistance is futile.",
    "Make it so.",
    "Engage!",
    "Live long and prosper.",
    "Beam me up, Scotty.",
    "Space: the final frontier.",
    "Fascinating.",
    "I'm a doctor, not a bricklayer!",
    "Damn it, Jim!",
    "It's a trap!",
    "Never tell me the odds!",
    "I've got a bad feeling about this.",
    "Use the Force, Luke.",
    "Size matters not.",
    "Judge me by my size, do you?",
    "Luminous beings are we, not this crude matter.",
    "Patience you must have, my young padawan.",
    "Always two there are, no more, no less.",
    "So this is how liberty dies... with thunderous applause.",
    "Hello there!",
    "I have the high ground!",
    "You underestimate my power!",
    "I am your father.",
    "No, I am your father.",
    "Search your feelings, you know it to be true.",
    "It's over, Anakin! I have the high ground!",
    "Another happy landing.",
    "This is where the fun begins.",
    "I've been looking forward to this.",
    "Good relations with the Wookiees, I have.",
    "Power! Unlimited power!",
    "Dew it!",
    "I am the Senate!",
    "Not yet.",
    "UNLIMITED POWER!",
    "I don't like sand.",
    "Are you threatening me, Master Jedi?",
    "I am the light.",
    "The dark side of the Force is a pathway to many abilities some consider to be unnatural.",
    "Execute Order 66.",
    "So uncivilized.",
    "I hate it when he does that.",
    "Oh, I don't think so.",
    "Nooooooooo!",
    "This is the way.",
    "The greatest teacher, failure is.",
    "Always in motion is the future.",
    "Each time you shift, it takes longer.",
    "Open the pod bay doors, HAL.",
    "I'm sorry, Dave. I'm afraid I can't do that.",
    "My god, it's full of stars!",
    "Race you to the roof!",
    "Badges? We don't need no stinking badges!",
    "The stuff that dreams are made of.",
    "You're gonna need a bigger boat.",
    "Snakes. Why'd it have to be snakes?",
    "I am serious. And don't call me Shirley.",
    "Surely you can't be serious.",
    "I speak Jive.",
    "The plane! The plane!",
    "Danger Zone!",
    "I want my MTV.",
    "Video killed the radio star.",
    "Take the red pill.",
    "Welcome to the real world.",
    "I know you are but what am I?",
    "I coulda been a contender.",
    "I'm king of the world!",
    "I'll never let go.",
    "You jump, I jump.",
    "Is it safe?",
    "I am not a number, I am a free man!",
    "Your number's up!",
    "Resistance is not futile.",
    "We are the Borg.",
    "You will be assimilated.",
    "Lower your shields and surrender your ships.",
    "It's not the years, it's the mileage.",
    "Klaatu barada nikto.",
    "D'oh!",
    "Eat my shorts!",
    "Aye caramba!",
    "Don't have a cow, man.",
    "Excellent.",
    "Unbelievable!",
    "To the Batmobile!",
    "I'm the King of the World!",
    "You can't fight in here! This is the War Room!",
    "Greed is good.",
    "You can't handle the truth!",
    "A census taker once tried to test me. I ate his liver with some fava beans and a nice chianti.",
    "Round up the usual suspects.",
    "We'll always have Paris.",
    "Fasten your seatbelts, it's going to be a bumpy night.",
    "I'm ready for my close-up.",
    "Play it again, Sam.",
    "We rob banks.",
    "I think this is the beginning of a beautiful friendship.",
    "Who's on first?",
    "I'm not a doctor, but I play one on TV.",
    "What you see is what you get!",
    "I'm mad as hell and I'm not going to take this anymore!",
    "Hello. My name is Inigo Montoya. You killed my father. Prepare to die.",
    "Inconceivable!",
    "You keep using that word. I do not think it means what you think it means.",
    "Rodents of Unusual Size? I don't think they exist.",
    "As you wish.",
    "Anybody want a peanut?",
    "Never go in against a Sicilian when death is on the line!",
    "Have fun storming the castle!",
    "I'm not a witch, I'm your wife!",
    "Cake, and grief counseling, will be available at the conclusion of the test.",
    "The cake is a lie.",
    "This was a triumph. I'm making a note here: huge success.",
    "Eat your vegetables.",
    "Clean your room.",
    "Wash behind your ears.",
    "Floss every day.",
    "Don't forget to make backups.",
    "Measure twice, cut once.",
    "Computers are fast; programmers keep it slow.",
    "I compute, therefore I am.",
    "There are 10 types of people in the world: those who understand binary and those who don't.",
    "Do not meddle in the affairs of dragons, for you are crunchy and taste good with ketchup.",
    "Time is an illusion. Lunchtime doubly so.",
    "I'd tell you a joke about UDP but you might not get it.",
    "A computer program does what you tell it to do, not what you want it to do.",
    "Programming is thinking, not typing.",
    "Windows 7: You had one job.",
    "There are only two hard problems in computer science: cache invalidation and naming things.",
    "Works on my machine.",
    "It's not a bug, it's a feature.",
    "I don't always test my code, but when I do, I do it in production.",
    "The best thing about a boolean is even if you are wrong, you are only off by a bit.",
    "To understand recursion, you must first understand recursion.",
    "An undefined problem has an infinite number of solutions.",
    "Eat, sleep, code, repeat.",
    "Talk is cheap. Show me the code.",
    "It's working exactly as designed.",
    "That's not a bug, that's a feature.",
]


def main():
    faulthandler.enable()
    threading.excepthook = thread_exception_handler
    sys.excepthook = global_exception_handler

    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setQuitOnLastWindowClosed(False)

    log.info('=== Blu-ray Dumper starting ===')
    log_system_info()

    win = BluRayDumperWindow()
    win.show()
    win.refresh_profile_list()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
