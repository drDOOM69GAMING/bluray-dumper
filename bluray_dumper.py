#!/usr/bin/env python3
import sys, os, struct, subprocess, time, shutil, signal, logging, traceback, hashlib, sqlite3
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
from PyQt6.QtCore import QTimer, Qt, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QTextCursor, QIcon, QAction

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
    stn_length = 0
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


def disk_free(path):
    try:
        st = os.statvfs(path)
        return st.f_frsize * st.f_bavail
    except Exception as e:
        log.warning('Could not check free space on %s: %s', path, e)
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

    def __init__(self, source_dir, output_path, target_bytes=0):
        super().__init__()
        self.source_dir = source_dir
        self.output_path = output_path
        self.target_bytes = target_bytes
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

        cmd = ['HandBrakeCLI', '-i', str(main_movie), '-o', self.output_path,
               '--format', 'av_mkv', '--encoder', 'x264',
               '--encoder-profile', 'high', '--encoder-level', '4.0',
               '--cfr',
               '-x', 'preset=slower',
               '--subtitle', 'none',
               '--native-language', 'eng',
               '--aencoder', 'ac3', '--ab', '448k',
               '--audio-lang-list', 'eng', '--first-audio']

        if self.target_bytes > 0:
            target_mb = max(1, int(self.target_bytes * 0.92 / (1024 * 1024)))
            cmd += ['--size', str(target_mb)]
            log.info('Compression targeting %d MB (%d bytes)', target_mb, self.target_bytes)
        else:
            cmd += ['--quality', '22']
        log.info('Starting compression: %s', ' '.join(cmd))
        try:
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
            for line in iter(self._process.stdout.readline, ''):
                line = line.rstrip('\n\r')
                if line:
                    log.debug('[HandBrakeCLI] %s', line)
                    self.output_line.emit(line)
            self._process.stdout.close()
            self._process.wait()
            log.info('HandBrakeCLI exited with code %d', self._process.returncode)
            self.finished.emit(self._process.returncode, '')
        except FileNotFoundError:
            self.finished.emit(-1, 'HandBrakeCLI not found. Install from handbrake.fr')
        except Exception as e:
            log.error('Compress worker exception: %s', traceback.format_exc())
            self.finished.emit(-1, str(e))

    def stop(self):
        if self._process and self._process.poll() is None:
            log.warning('Terminating HandBrakeCLI')
            self._process.terminate()
            try:
                self._process.wait(3)
            except subprocess.TimeoutExpired:
                pass
            if self._process.poll() is None:
                log.warning('Killing HandBrakeCLI')
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


def list_streams(m2ts_path):
    try:
        r = subprocess.run(['ffprobe', '-v', 'quiet',
                           '-print_format', 'json',
                           '-show_streams', str(m2ts_path)],
                          capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return []
        import json
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


class BluRayDumperWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Blu-ray Dumper')
        self.resize(720, 700)

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

        self.status_label = QLabel('Starting...')
        self.status_label.setStyleSheet('font-size: 14px; font-weight: bold;')
        layout.addWidget(self.status_label)

        disc_frame = QFrame()
        disc_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        dfl = QVBoxLayout(disc_frame)
        self.disc_info = QLabel('Checking system...')
        self.disc_info.setStyleSheet('color: #666;')
        dfl.addWidget(self.disc_info)
        layout.addWidget(disc_frame)

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
            install_guide = '\n'.join(
                f'{t}:\n{TOOL_INSTALL.get(t, t)}' for t in missing)
            self.disc_info.setText(
                f'Missing tools: {", ".join(missing)}\n\n'
                'Install with your package manager:\n\n'
                f'{install_guide}')
            self.refresh_btn.setEnabled(False)
            self.select_dest_btn.setEnabled(False)
            log.error('Missing required tools:\n%s', install_guide)
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
                import ast
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

        if not tool_available('bluraybackup'):
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
                if self.direct_to_mkv and tool_available('HandBrakeCLI'):
                    compress_target = self.compress_target
                    compress_bytes = 0
                    if compress_target == 'custom':
                        try:
                            compress_bytes = int(float(self.compress_custom_gb) * 1_000_000_000)
                        except (ValueError, TypeError):
                            compress_target = ''
                    elif compress_target in TARGET_SIZES:
                        compress_bytes = TARGET_SIZES[compress_target]
                    if compress_bytes > 0:
                        label = TARGET_LABELS.get(compress_target, f'{compress_bytes / 1e9:.1f} GB')
                        reply = QMessageBox.question(
                            self, 'Compress to MKV?',
                            f'Direct-to-MKV is enabled.\n\n'
                            f'Compress main movie to fit {label}?\n'
                            f'This will skip ISO creation and create an MKV directly.',
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                        if reply == QMessageBox.StandardButton.Yes:
                            self.start_compress(compress_bytes)
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

        if self.direct_to_mkv and tool_available('HandBrakeCLI'):
            compress_target = self.compress_target
            compress_bytes = 0
            if compress_target == 'custom':
                try:
                    compress_bytes = int(float(self.compress_custom_gb) * 1_000_000_000)
                except (ValueError, TypeError):
                    compress_target = ''
            elif compress_target in TARGET_SIZES:
                compress_bytes = TARGET_SIZES[compress_target]
            if compress_bytes > 0:
                label = TARGET_LABELS.get(compress_target, f'{compress_bytes / 1e9:.1f} GB')
                reply = QMessageBox.question(
                    self, 'Compress to MKV?',
                    f'Direct-to-MKV is enabled.\n\n'
                    f'Compress main movie to fit {label}?\n'
                    f'This will skip ISO creation and create an MKV directly.',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    self.start_compress(compress_bytes)
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

        if not tool_available('genisoimage'):
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

    def start_compress(self, target_bytes):
        if self._working:
            log.warning('start_compress called while already working')
            return
        if not self._dump_path or not self._dump_path.exists():
            QMessageBox.critical(self, 'Error', 'Dump folder not found.')
            return

        out_name = sanitize_filename(self._dump_path.name) + '_compressed.mkv'
        out_path = self._dump_path.parent / out_name

        self._enter_working_state(indeterminate=True, status_text='Compressing...',
                                  log_text='Compressing main movie...',
                                  show_speed=False)

        self.compress_worker = CompressWorker(
            str(self._dump_path), str(out_path), target_bytes)
        self.compress_worker.finished.connect(self.on_compress_finished)
        self.compress_worker.output_line.connect(self.on_dump_output)
        self.compress_worker.start()
        log.info('Compression started: %s -> %s', self._dump_path, out_path)

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
            if not tool_available('HandBrakeCLI'):
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

    def on_compress_finished(self, retcode, error):
        if self._cancelled:
            return
        log.info('Compression finished with code %d', retcode)
        self.timer.stop()
        self._working = False
        self.reset_buttons()

        if retcode != 0:
            self.status_label.setText('Compression failed')
            msg = error or f'HandBrakeCLI exited with code {retcode}'
            self.log_output.setText(f'Compression failed: {msg}')
            QMessageBox.critical(self, 'Compression Failed', msg)
            log.error('Compression failed: %s', msg)
        else:
            self.status_label.setText('Compression complete')
            mkv_path = Path(self.compress_worker.output_path)
            self.log_output.setText('Compressed MKV created.')

            if tool_available('ffmpeg') and self._is_dvd_target(self.compress_target):
                self._offer_dvd_output(mkv_path)
            else:
                QMessageBox.information(
                    self, 'Compression Done',
                    f'Compressed MKV saved as:\n{mkv_path}')

        self._finish_after_compress()

        self.progress_bar.setVisible(False)
        self.timer_label.setVisible(False)

        if hasattr(self, '_batch_compress_queue'):
            self._process_batch_compress()
        else:
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
            return
        if self._dvd_vob_rb.isChecked():
            self._create_dvd_video_iso(mkv_path)
        elif self._dvd_avchd_rb.isChecked():
            self._create_avchd_iso(mkv_path)
        else:
            QMessageBox.information(
                self, 'Compression Done',
                f'Compressed MKV saved as:\n{mkv_path}')

    @staticmethod
    def _bluray_muxer_available():
        try:
            r = subprocess.run(['ffmpeg', '-muxers'], capture_output=True, text=True, timeout=10)
            return 'bluray' in r.stdout
        except Exception:
            return False

    def _create_avchd_iso(self, mkv_path):
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

        if self._bluray_muxer_available():
            try:
                r = subprocess.run(
                    ['ffmpeg', '-i', str(mkv_path), '-c:v', 'copy', '-c:a', 'ac3', '-b:a', '448k',
                     '-f', 'bluray', '-muxrate', '30000000', str(avchd_dir)],
                    capture_output=True, text=True, timeout=3600)
                if r.returncode != 0:
                    raise RuntimeError(f'ffmpeg bluray muxer failed: {r.stderr.strip()[-300:]}')
            except FileNotFoundError:
                QMessageBox.critical(self, 'Missing Tool', 'ffmpeg not found.')
                return
            except Exception as e:
                self.status_label.setText('AVCHD mux failed')
                self.log_output.setText(str(e))
                QMessageBox.critical(self, 'AVCHD Error', f'ffmpeg bluray muxer failed:\n{e}')
                shutil.rmtree(avchd_dir, ignore_errors=True)
                return

            bdmv = avchd_dir / 'BDMV'
            if not bdmv.is_dir():
                QMessageBox.critical(self, 'AVCHD Error',
                                     'ffmpeg did not create the BDMV directory structure.\n'
                                     'The bluray muxer may not be supported in this ffmpeg build.')
                shutil.rmtree(avchd_dir, ignore_errors=True)
                return
        else:
            self.log_output.setText('ffmpeg bluray muxer not available, using fallback...')
            QApplication.processEvents()
            m2ts_path = avchd_dir / 'temp.m2ts'
            avchd_dir.mkdir(parents=True, exist_ok=True)
            try:
                r = subprocess.run(
                    ['ffmpeg', '-i', str(mkv_path), '-c:v', 'copy', '-c:a', 'ac3', '-b:a', '448k',
                     '-f', 'mpegts', '-mpegts_m2ts_mode', '1', '-y', str(m2ts_path)],
                    capture_output=True, text=True, timeout=3600)
                if r.returncode != 0:
                    raise RuntimeError(f'ffmpeg remux failed: {r.stderr.strip()[-200:]}')
            except FileNotFoundError:
                QMessageBox.critical(self, 'Missing Tool', 'ffmpeg not found.')
                shutil.rmtree(avchd_dir, ignore_errors=True)
                return
            except Exception as e:
                self.status_label.setText('AVCHD remux failed')
                self.log_output.setText(str(e))
                QMessageBox.critical(self, 'AVCHD Error', f'Remuxing failed:\n{e}')
                shutil.rmtree(avchd_dir, ignore_errors=True)
                return

            if m2ts_path.stat().st_size < 10 * 1024 * 1024:
                QMessageBox.critical(self, 'AVCHD Error',
                                     f'M2TS is only {m2ts_path.stat().st_size / (1024*1024):.1f} MB.\n'
                                     'The video stream may be corrupt.')
                shutil.rmtree(avchd_dir, ignore_errors=True)
                return

            self.log_output.setText('Building AVCHD structure (fallback)...')
            QApplication.processEvents()
            try:
                create_avchd_structure(m2ts_path, avchd_dir)
            except Exception as e:
                self.status_label.setText('AVCHD structure failed')
                self.log_output.setText(str(e))
                QMessageBox.critical(self, 'AVCHD Error', f'Structure creation failed:\n{e}')
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
        self.log_output.setText('Creating AVCHD ISO...')
        QApplication.processEvents()
        try:
            r = subprocess.run(
                ['genisoimage', '-udf', '-udf-revision', '0x0200',
                 '-V', vol_id, '-volset', vol_id,
                 '-o', str(iso_path), str(avchd_dir)],
                capture_output=True, text=True, timeout=3600)
            if r.returncode != 0:
                raise RuntimeError(f'ISO creation failed: {r.stderr.strip()[-200:]}')
        except FileNotFoundError:
            QMessageBox.critical(self, 'Missing Tool', 'genisoimage not found.')
            shutil.rmtree(avchd_dir, ignore_errors=True)
            return
        except Exception as e:
            self.status_label.setText('AVCHD ISO failed')
            self.log_output.setText(str(e))
            QMessageBox.critical(self, 'AVCHD Error', f'ISO creation failed:\n{e}')
            shutil.rmtree(avchd_dir, ignore_errors=True)
            return

        shutil.rmtree(avchd_dir, ignore_errors=True)
        self.status_label.setText('AVCHD DVD ISO created')
        self.log_output.setText(f'Created: {iso_path.name}')
        QMessageBox.information(
            self, 'AVCHD Complete',
            f'AVCHD DVD ISO created:\n{iso_path}\n\n'
            f'Burn to a DVD-R/RW disc with:\n'
            f'  sudo wodim -v -dao dev=/dev/sr0 {iso_path.name}')
        log.info('AVCHD ISO created: %s', iso_path)

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
        self.status_label.setText('Creating DVD-Video...')
        self.log_output.setText('Encoding to DVD-compatible MPEG-2...')
        QApplication.processEvents()

        name_stem = Path(mkv_path).stem.replace('_compressed', '')
        dvd_dir = Path(mkv_path).parent / f'dvd_{name_stem}'
        dvd_dir.mkdir(parents=True, exist_ok=True)

        mpg_path = dvd_dir / 'temp.mpg'
        try:
            scaled = self._detect_dvd_format(mkv_path)
            w, h = ('720', '480') if scaled == 'ntsc' else ('720', '576')
            r = subprocess.run(
                ['ffmpeg', '-i', str(mkv_path), '-c:v', 'mpeg2video',
                 '-vf', f'scale={w}:{h}:force_original_aspect_ratio=decrease,'
                       f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2',
                 '-b:v', '8M', '-maxrate', '9M', '-bufsize', '2M',
                 '-c:a', 'ac3', '-ac', '2', '-b:a', '448k',
                 '-f', 'dvd', '-y', str(mpg_path)],
                capture_output=True, text=True, timeout=3600)
            if r.returncode != 0:
                raise RuntimeError(f'ffmpeg DVD encode failed: {r.stderr.strip()[-200:]}')
        except FileNotFoundError:
            QMessageBox.critical(self, 'Missing Tool', 'ffmpeg not found.')
            shutil.rmtree(dvd_dir, ignore_errors=True)
            return
        except Exception as e:
            self.status_label.setText('DVD encode failed')
            self.log_output.setText(str(e))
            QMessageBox.critical(self, 'DVD Error', f'MPEG-2 encoding failed:\n{e}')
            shutil.rmtree(dvd_dir, ignore_errors=True)
            return

        if tool_available('dvdauthor'):
            self.status_label.setText('Authoring DVD-Video...')
            self.log_output.setText('Running dvdauthor...')
            QApplication.processEvents()
            fmt = self._detect_dvd_format(mpg_path)
            dvd_size = '720x480' if fmt == 'ntsc' else '720x576'
            vmgm_mpg = dvd_dir / 'vmgm.mpg'
            r = subprocess.run(
                ['ffmpeg', '-f', 'lavfi', '-i', 'color=c=black:s=' + dvd_size + ':d=0.04',
                 '-c:v', 'mpeg2video', '-frames:v', '1',
                 '-b:v', '1M', '-y', str(vmgm_mpg)],
                capture_output=True, text=True, timeout=120)
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
            try:
                r = subprocess.run(
                    ['dvdauthor', '-x', str(xml_path)],
                    capture_output=True, text=True, timeout=300)
                if r.returncode != 0:
                    raise RuntimeError(f'dvdauthor failed: {r.stderr.strip()[-400:]}')
            except Exception as e:
                self.status_label.setText('DVD authoring failed')
                self.log_output.setText(str(e))
                QMessageBox.critical(self, 'DVD Error', f'dvdauthor failed:\n{e}')
                shutil.rmtree(dvd_dir, ignore_errors=True)
                return
            finally:
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
        try:
            r = subprocess.run(
                iso_cmd + [str(iso_path), str(dvd_dir)],
                capture_output=True, text=True, timeout=3600)
            if r.returncode != 0:
                raise RuntimeError(f'ISO creation failed: {r.stderr.strip()[-200:]}')
        except FileNotFoundError:
            QMessageBox.critical(self, 'Missing Tool', 'genisoimage not found.')
            shutil.rmtree(dvd_dir, ignore_errors=True)
            return
        except Exception as e:
            self.status_label.setText('DVD ISO failed')
            self.log_output.setText(str(e))
            QMessageBox.critical(self, 'DVD Error', f'ISO creation failed:\n{e}')
            shutil.rmtree(dvd_dir, ignore_errors=True)
            return

        shutil.rmtree(dvd_dir, ignore_errors=True)
        self.status_label.setText('DVD-Video ISO created')
        self.log_output.setText(f'Created: {iso_path.name}')
        QMessageBox.information(
            self, 'DVD-Video Complete',
            f'DVD-Video ISO created:\n{iso_path}\n\n'
            f'Burn to a DVD-R/RW disc with:\n'
            f'  sudo wodim -v -dao dev=/dev/sr0 {iso_path.name}')
        log.info('DVD-Video ISO created: %s', iso_path)

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


def global_exception_handler(exc_type, exc_value, exc_tb):
    log.critical('Unhandled exception',
                 exc_info=(exc_type, exc_value, exc_tb))
    try:
        app = QApplication.instance()
        if app:
            QMessageBox.critical(
                None, 'Unexpected Error',
                f'An unexpected error occurred:\n\n{exc_value}\n\n'
                'Check the log for details.\n'
                'The application will continue running.')
    except Exception:
        pass


def main():
    sys.excepthook = global_exception_handler
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setQuitOnLastWindowClosed(False)
    log.info('Blu-ray Dumper starting')
    win = BluRayDumperWindow()
    win.show()
    win.refresh_profile_list()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
