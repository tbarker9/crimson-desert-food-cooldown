#!/usr/bin/env python3
"""cdmods - apply the Crimson Desert mods in this folder directly, no mod manager needed.

Pure Python 3 standard library (curses for the TUI), so it runs on any common Linux distro.

How it works
  * The game's archives are never edited. Modified data tables go into one extra archive
    group folder ("cdmods") next to the game's own 0000..0035 folders, and that group is
    registered in meta/0.papgt, the game's archive index.
  * meta/0.papgt is therefore the only original game file that changes. Before the first
    change it is backed up to ~/.local/share/cd-mods/<game version>/ . A backup only counts
    for the exact game version it was taken from.
  * Every apply starts from the originals: restore meta/0.papgt, remove the cdmods folder,
    then rebuild from the untouched game tables and write the new selection.
  * If the game files are not original (Steam update, another mod manager, manual edits),
    it refuses and tells you to run Steam "Verify integrity of game files".

Usage
  ./cdmods.py                 interactive TUI
  ./cdmods.py status          show game version, backup and what is applied
  ./cdmods.py list            list the available mods
  ./cdmods.py apply FILE...   apply the given mod files (replaces the current selection)
  ./cdmods.py restore         put the game back to original
  Options: --game DIR (or CD_GAME_DIR) to point at the game folder.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

APP = 'cd-mods'
APP_ID = 3321460
GROUP = 'cdmods'                      # our archive group folder / papgt entry
GROUP_FLAGS = 0x003FFF00              # same flags DMM uses for overlay groups
PAMT_CONST = bytes.fromhex('32020e6100000000')
TABLE_DIR = 'gamedata/binarystaticinfo__/bin'
SOURCE_GROUP = '0008'                 # vanilla group holding the data tables
HASH_SEED = 0x000C5EDE
MOD_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'), APP)


class ModError(Exception):
    """A problem the user needs to act on; the message says what to do."""


# ---------------------------------------------------------------------------------------------
# Low-level formats: Bob Jenkins lookup3 hashlittle (archive checksums) and LZ4 block decoding
# ---------------------------------------------------------------------------------------------

def _rot(v, k):
    return ((v << k) | (v >> (32 - k))) & 0xFFFFFFFF


def hashlittle(data, initval=HASH_SEED):
    M = 0xFFFFFFFF
    length = len(data)
    a = b = c = (0xDEADBEEF + length + initval) & M
    blocks = (length - 1) // 12 if length else 0      # the main loop runs while more than 12 bytes remain
    if blocks:
        for x, y, z in struct.iter_unpack('<3I', memoryview(data)[:blocks * 12]):
            a = (a + x) & M; b = (b + y) & M; c = (c + z) & M
            a = (a - c) & M; a ^= ((c << 4) | (c >> 28)) & M; c = (c + b) & M
            b = (b - a) & M; b ^= ((a << 6) | (a >> 26)) & M; a = (a + c) & M
            c = (c - b) & M; c ^= ((b << 8) | (b >> 24)) & M; b = (b + a) & M
            a = (a - c) & M; a ^= ((c << 16) | (c >> 16)) & M; c = (c + b) & M
            b = (b - a) & M; b ^= ((a << 19) | (a >> 13)) & M; a = (a + c) & M
            c = (c - b) & M; c ^= ((b << 4) | (b >> 28)) & M; b = (b + a) & M
    rest = length - blocks * 12
    if rest == 0:
        return c
    tail = bytes(data[blocks * 12:]) + b'\x00' * 12
    if rest >= 9:
        c = (c + (int.from_bytes(tail[8:12], 'little') & (M >> (8 * (12 - rest))))) & M
    if rest >= 8:
        b = (b + int.from_bytes(tail[4:8], 'little')) & M
    elif rest >= 5:
        b = (b + (int.from_bytes(tail[4:8], 'little') & (M >> (8 * (8 - rest))))) & M
    if rest >= 4:
        a = (a + int.from_bytes(tail[0:4], 'little')) & M
    else:
        a = (a + (int.from_bytes(tail[0:4], 'little') & (M >> (8 * (4 - rest))))) & M
    c ^= b; c = (c - _rot(b, 14)) & M
    a ^= c; a = (a - _rot(c, 11)) & M
    b ^= a; b = (b - _rot(a, 25)) & M
    c ^= b; c = (c - _rot(b, 16)) & M
    a ^= c; a = (a - _rot(c, 4)) & M
    b ^= a; b = (b - _rot(a, 14)) & M
    c ^= b; c = (c - _rot(b, 24)) & M
    return c


def lz4_block_decompress(src, size):
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        tok = src[i]; i += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                x = src[i]; i += 1; lit += x
                if x != 255:
                    break
        out += src[i:i + lit]; i += lit
        if i >= n or len(out) >= size:
            break
        off = src[i] | (src[i + 1] << 8); i += 2
        ml = tok & 15
        if ml == 15:
            while True:
                x = src[i]; i += 1; ml += x
                if x != 255:
                    break
        ml += 4
        start = len(out) - off
        if off >= ml:
            out += out[start:start + ml]
        else:                                   # overlapping copy: repeat the pattern
            pat = out[start:]
            reps, rem = divmod(ml, off)
            out += pat * reps + pat[:rem]
    if len(out) != size:
        raise ModError(f'LZ4 data decoded to {len(out)} bytes, expected {size}')
    return bytes(out)


# ---------------------------------------------------------------------------------------------
# Archive index files: meta/0.papgt (group list) and <group>/0.pamt (file list)
# ---------------------------------------------------------------------------------------------

class Papgt:
    """meta/0.papgt: header, one entry per archive group, then the group names."""

    def __init__(self, data):
        if len(data) < 16:
            raise ModError('meta/0.papgt is too small to be valid')
        self.magic, self.checksum, info = struct.unpack_from('<III', data, 0)
        self.info_hi = info & ~0xFF
        count = info & 0xFF
        self.valid_checksum = hashlittle(data[12:]) == self.checksum
        raw = [struct.unpack_from('<III', data, 12 + 12 * i) for i in range(count)]
        at = 12 + 12 * count
        size = struct.unpack_from('<I', data, at)[0]
        names = data[at + 4:at + 4 + size]
        self.groups = []                          # [name, flags, pamt checksum]
        for flags, name_off, chk in raw:
            name = names[name_off:names.index(b'\x00', name_off)].decode('ascii')
            self.groups.append([name, flags, chk])

    def names(self):
        return [g[0] for g in self.groups]

    def build(self):
        names, entries = bytearray(), bytearray()
        for name, flags, chk in self.groups:
            entries += struct.pack('<III', flags, len(names), chk)
            names += name.encode('ascii') + b'\x00'
        body = bytes(entries + struct.pack('<I', len(names)) + names)
        info = struct.pack('<I', self.info_hi | len(self.groups))
        return struct.pack('<II', self.magic, hashlittle(body)) + info + body


def parse_pamt(data):
    """Return {'path': (paz_index, offset, comp_size, orig_size, flags)} for every file."""
    o = 4
    npaz = struct.unpack_from('<I', data, o)[0]; o += 12
    for i in range(npaz):
        o += 8 + (4 if i < npaz - 1 else 0)

    def tree(o):
        size = struct.unpack_from('<I', data, o)[0]; o += 4
        start, nodes = o, {}
        while o < start + size:
            parent, ln = struct.unpack_from('<IB', data, o)
            nodes[o - start] = (parent, data[o + 5:o + 5 + ln].decode('utf-8', 'replace'))
            o += 5 + ln
        return nodes, o

    folders, o = tree(o)
    files_tree, o = tree(o)

    def full(nodes, ref):
        parts = []
        while ref != 0xFFFFFFFF:
            parent, name = nodes[ref]
            parts.append(name)
            ref = parent
        return ''.join(reversed(parts))

    nfold = struct.unpack_from('<I', data, o)[0]; o += 4
    frecs = [struct.unpack_from('<IIII', data, o + 16 * i) for i in range(nfold)]; o += 16 * nfold
    nfiles = struct.unpack_from('<I', data, o)[0]; o += 4
    recs = [struct.unpack_from('<IIIII', data, o + 20 * i) for i in range(nfiles)]
    out = {}
    for _h, name_off, first, count in frecs:
        folder = full(folders, name_off)
        for node, off, comp, orig, flags in recs[first:first + count]:
            out[folder + '/' + full(files_tree, node)] = (flags & 0xFF, off, comp, orig, flags)
    return out


def build_group(files):
    """Build (pamt, paz) for one folder TABLE_DIR holding {filename: bytes}, stored uncompressed."""
    paz, recs = bytearray(), []
    ftree = bytearray()
    for name in sorted(files):
        while len(paz) % 16:
            paz += b'\x00'
        node = len(ftree)
        ftree += struct.pack('<IB', 0xFFFFFFFF, len(name)) + name.encode('ascii')
        recs.append(struct.pack('<IIIII', node, len(paz), len(files[name]), len(files[name]), 0))
        paz += files[name]
    while len(paz) % 16:
        paz += b'\x00'
    # folder path tree: "gamedata" <- "/binarystaticinfo__" <- "/bin"  (same shape the game uses)
    parts = ['gamedata'] + ['/' + p for p in TABLE_DIR.split('/')[1:]]
    folders, parent, leaf = bytearray(), 0xFFFFFFFF, 0
    for p in parts:
        leaf = len(folders)
        folders += struct.pack('<IB', parent, len(p)) + p.encode('ascii')
        parent = leaf
    body = bytearray()
    body += struct.pack('<I', 1) + PAMT_CONST
    body += struct.pack('<II', hashlittle(bytes(paz)), len(paz))
    body += struct.pack('<I', len(folders)) + folders
    body += struct.pack('<I', len(ftree)) + ftree
    body += struct.pack('<I', 1) + struct.pack('<IIII', hashlittle(TABLE_DIR.encode()), leaf, 0, len(recs))
    body += struct.pack('<I', len(recs)) + b''.join(recs)
    pamt = struct.pack('<I', hashlittle(bytes(body[8:]))) + bytes(body)
    return pamt, bytes(paz)


def pamt_checksum(data):
    return struct.unpack_from('<I', data, 0)[0], hashlittle(data[12:])


# ---------------------------------------------------------------------------------------------
# Game data tables (.staticinfoheader = index, .staticinfobody = records)
# ---------------------------------------------------------------------------------------------

class Table:
    """A game table: body bytes (mutable) plus {record name: (key, offset)} from its header."""

    def __init__(self, name, header, body):
        self.name, self.header, self.body = name, header, bytearray(body)
        for base, count in ((2, struct.unpack_from('<H', header, 0)[0]),
                            (4, struct.unpack_from('<I', header, 0)[0])):
            if base + 8 * count == len(header):
                break
        else:
            raise ModError(f'{name}: unrecognised header layout')
        entries = sorted((struct.unpack_from('<I', header, base + 8 * i + 4)[0],
                          struct.unpack_from('<I', header, base + 8 * i)[0]) for i in range(count))
        self.records = {}
        self.anchors = {}                               # (entry, field group) -> offset, found before edits
        for i, (off, key) in enumerate(entries):
            end = entries[i + 1][0] if i + 1 < len(entries) else len(body)
            ln = struct.unpack_from('<I', body, off + 4)[0]
            self.records[body[off + 8:off + 8 + ln].decode('utf-8', 'replace')] = (key, off, end)

    def record(self, entry, key):
        if entry not in self.records:
            raise ModError(f'{self.name}: record "{entry}" not found (game data changed?)')
        k, off, end = self.records[entry]
        if key is not None and k != key:
            raise ModError(f'{self.name}: record "{entry}" has key {k}, mod expects {key}')
        return off, end, off + 8 + len(entry.encode('utf-8'))   # start, end, data start


def _find_cooltime(body, start, end):
    for i in range(start, end - 24):
        a, b, c = struct.unpack_from('<qqq', body, i)
        if a == b == c and 100 <= a <= 3600000 and a % 100 == 0:
            return i
    raise ModError('cooldown field not found')


def _find_apply_max_stack_cap(t, start, end, data):
    """Walk the ItemInfo fields up to apply_max_stack_cap (layout per the crimson-rs project)."""
    b = t.body
    o = data + 1 + 8                                   # is_blocked, max_stack_count

    def u(fmt):
        nonlocal o
        v = struct.unpack_from(fmt, b, o)[0]; o += struct.calcsize(fmt); return v

    def cstr():
        nonlocal o
        n = u('<I'); o += n

    def arr(fn):
        for _ in range(u('<I')):
            fn()

    def lstr():
        nonlocal o
        o += 1 + 8; cstr()

    lstr(); u('<I'); u('<I')
    arr(lambda: (u('<I'), arr(lambda: u('<B'))))
    arr(lambda: u('<I')); u('<I'); arr(lambda: u('<I')); arr(lambda: u('<I'))
    arr(lambda: (u('<I'), u('<I'), u('<B'), arr(lambda: u('<I')), u('<B')))
    u('<I'); u('<B'); u('<B'); u('<I'); u('<I')
    lstr(); lstr(); u('<I'); u('<H'); u('<I'); u('<B'); u('<I')
    arr(lambda: (u('<I'), u('<I')))
    u('<B')                                            # use_immediately
    if not start <= o < end:
        raise ModError('apply_max_stack_cap outside its record')
    return o


def _find_difficulty_list(body, start, end):
    """game_difficulty_buff_level_list: u32 levels (0,1,2,3) right before a BuffLevel_Difficulty* key.

    The key is followed by one byte that varies per character (03 for enemies, 00-03 for playable
    characters) and then ff ff ff ff."""
    for i in range(start + 16, end - 9):
        if body[i + 5:i + 9] == b'\xff\xff\xff\xff' and \
                struct.unpack_from('<I', body, i)[0] in (1000276, 1000277, 1000278) and \
                struct.unpack_from('<4I', body, i - 16) == (0, 1, 2, 3):
            return i - 16
    raise ModError('difficulty level list not found')


def _anchor(t, entry, name, find):
    """Locate a field once per record, before an edit can disturb the pattern used to find it."""
    k = (entry, name)
    if k not in t.anchors:
        t.anchors[k] = find()
    return t.anchors[k]


def field_location(t, entry, key, field):
    """Return (offset, struct format) of a named field in one record."""
    start, end, data = t.record(entry, key)
    if t.name == 'iteminfo':
        if field == 'max_stack_count':
            return data + 1, '<Q'
        if field == 'apply_max_stack_cap':
            return _anchor(t, entry, 'stackcap', lambda: _find_apply_max_stack_cap(t, start, end, data)), '<B'
        m = re.fullmatch(r'cooltime\.([abc])', field)
        if m:
            pos = _anchor(t, entry, 'cooltime', lambda: _find_cooltime(t.body, data, end))
            return pos + 8 * 'abc'.index(m.group(1)), '<q'
    elif t.name == 'itemuseinfo':
        fields = {'base.flag_d': (5, '<B'), 'base.group_lookup_a': (6, '<I'), 'base.group_lookup_b': (10, '<I')}
        if field in fields:
            rel, fmt = fields[field]
            return data + rel, fmt
    elif t.name == 'characterinfo':
        m = re.fullmatch(r'game_difficulty_buff_level_list\.([abc])', field)
        if m:
            pos = _anchor(t, entry, 'difficulty', lambda: _find_difficulty_list(t.body, data, end))
            return pos + 4 * (1 + 'abc'.index(m.group(1))), '<I'
    raise ModError(f'{t.name}: field "{field}" is not supported by cdmods')


# ---------------------------------------------------------------------------------------------
# Mods (the .field.json files in this folder)
# ---------------------------------------------------------------------------------------------

class Mod:
    def __init__(self, path):
        self.path, self.file = path, os.path.basename(path)
        with open(path) as f:
            m = json.load(f)
        if m.get('format') != 3:
            raise ModError(f'{self.file}: not a field-name (format 3) mod')
        info = m.get('modinfo', {})
        self.title = info.get('title') or self.file
        self.description = info.get('description', '')
        self.version = info.get('version', '')
        if 'targets' in m:
            pairs = [(t['file'], t['intents']) for t in m['targets']]
        else:
            pairs = [(m['target'], m['intents'])]
        self.intents = {}                             # table -> [intent]
        for target, intents in pairs:
            table = target.split('.')[0]
            for it in intents:
                if it.get('op', 'set') != 'set':
                    raise ModError(f'{self.file}: only "set" intents are supported')
            self.intents.setdefault(table, []).extend(intents)

    def touches(self):
        return {(t, i['entry'], i['field']) for t, its in self.intents.items() for i in its}


def load_mods(folder=MOD_DIR):
    mods = []
    for f in sorted(os.listdir(folder)):
        if f.endswith('.field.json'):
            mods.append(Mod(os.path.join(folder, f)))
    return mods


def mod_groups(mods):
    """Mods that set the same fields can't be combined: return [(label, [mods])], one pick per group."""
    parent = list(range(len(mods)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    owner = {}
    for i, m in enumerate(mods):
        for t in m.touches():
            if t in owner:
                parent[find(i)] = find(owner[t])
            else:
                owner[t] = i
    groups = {}
    for i, m in enumerate(mods):
        groups.setdefault(find(i), []).append(m)
    out = []
    for members in groups.values():
        members.sort(key=lambda m: _natural(m.title))
        titles = [m.title for m in members]
        label = os.path.commonprefix(titles).rstrip(' 0123456789/+,.-') if len(titles) > 1 else titles[0]
        out.append((label or titles[0], members))
    out.sort(key=lambda g: _natural(g[0]))
    return out


def _natural(s):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r'(\d+)', s)]


def check_selection(mods):
    seen = {}
    for m in mods:
        for t in m.touches():
            if t in seen:
                raise ModError(f'"{m.title}" and "{seen[t].title}" change the same data; pick only one')
            seen[t] = m


# ---------------------------------------------------------------------------------------------
# Game install, originals and state
# ---------------------------------------------------------------------------------------------

def find_game_dir(explicit=None):
    cand = explicit or os.environ.get('CD_GAME_DIR')
    if cand:
        if os.path.isfile(os.path.join(cand, 'meta', '0.papgt')):
            return os.path.abspath(cand)
        raise ModError(f'{cand} does not look like the Crimson Desert folder (no meta/0.papgt)')
    steam_roots = ['~/.local/share/Steam', '~/.steam/steam', '~/.steam/root',
                   '~/.var/app/com.valvesoftware.Steam/.local/share/Steam', '~/snap/steam/common/.local/share/Steam']
    libraries = []
    for root in steam_roots:
        vdf = os.path.expanduser(os.path.join(root, 'steamapps', 'libraryfolders.vdf'))
        if os.path.isfile(vdf):
            libraries.append(os.path.expanduser(root))
            with open(vdf, errors='replace') as f:
                libraries += re.findall(r'"path"\s+"([^"]+)"', f.read())
    for lib in dict.fromkeys(libraries):
        acf = os.path.join(lib, 'steamapps', f'appmanifest_{APP_ID}.acf')
        if os.path.isfile(acf):
            with open(acf, errors='replace') as f:
                m = re.search(r'"installdir"\s+"([^"]+)"', f.read())
            if m:
                path = os.path.join(lib, 'steamapps', 'common', m.group(1))
                if os.path.isfile(os.path.join(path, 'meta', '0.papgt')):
                    return path
    raise ModError('Could not find Crimson Desert in your Steam libraries. Use --game DIR or set CD_GAME_DIR.')


def read(path):
    with open(path, 'rb') as f:
        return f.read()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.cdmods-')
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o755 if os.path.basename(path) == '0.papgt' else 0o644)
    os.replace(tmp, path)


class Game:
    def __init__(self, game_dir=None):
        self.dir = find_game_dir(game_dir)
        self.meta = os.path.join(self.dir, 'meta')
        paver = read(os.path.join(self.meta, '0.paver'))
        a, b, c = struct.unpack_from('<HHH', paver, 0)
        self.version = f'{a}.{b:02d}.{c:02d}'
        self.version_id = paver.hex()               # exact build, includes a build hash
        self.backup_dir = os.path.join(DATA_DIR, self.version_id)
        self.state_path = os.path.join(DATA_DIR, 'state.json')

    # --- persistent state -----------------------------------------------------------------
    def backup(self):
        """Original meta/0.papgt for THIS game version, or None."""
        manifest = os.path.join(self.backup_dir, 'manifest.json')
        papgt = os.path.join(self.backup_dir, '0.papgt')
        if not (os.path.isfile(manifest) and os.path.isfile(papgt)):
            return None
        with open(manifest) as f:
            info = json.load(f)
        data = read(papgt)
        if info.get('version_id') != self.version_id or info.get('papgt_sha256') != sha256(data):
            return None
        return data

    def save_backup(self, papgt):
        os.makedirs(self.backup_dir, exist_ok=True)
        atomic_write(os.path.join(self.backup_dir, '0.papgt'), papgt)
        info = {'game_version': self.version, 'version_id': self.version_id, 'papgt_sha256': sha256(papgt),
                'game_dir': self.dir, 'created': datetime.now(timezone.utc).isoformat()}
        atomic_write(os.path.join(self.backup_dir, 'manifest.json'), json.dumps(info, indent=2).encode())

    def state(self):
        try:
            with open(self.state_path) as f:
                st = json.load(f)
        except (OSError, ValueError):
            return None
        if st.get('version_id') != self.version_id or st.get('game_dir') != self.dir:
            return None
        return st

    def save_state(self, st):
        os.makedirs(DATA_DIR, exist_ok=True)
        if st is None:
            if os.path.exists(self.state_path):
                os.remove(self.state_path)
            return
        atomic_write(self.state_path, json.dumps(st, indent=2).encode())

    # --- checks ---------------------------------------------------------------------------
    def game_running(self):
        try:
            out = subprocess.run(['pgrep', '-af', 'CrimsonDesert.exe'], capture_output=True, text=True).stdout
        except OSError:
            return False
        return any('pgrep' not in line for line in out.splitlines())

    def _verify_cache(self):
        """{pamt path: [size, mtime_ns, inode, checksum]} for index files already fully verified."""
        try:
            with open(os.path.join(DATA_DIR, 'verified.json')) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_verify_cache(self, cache):
        os.makedirs(DATA_DIR, exist_ok=True)
        atomic_write(os.path.join(DATA_DIR, 'verified.json'), json.dumps(cache).encode())

    def inspect(self, progress=None):
        """Work out what state the game files are in (never changes game files).

        Each archive index is checksummed once; afterwards it is trusted while its size, mtime and inode
        are unchanged, so a Steam update or any edit triggers a full re-check."""
        papgt_bytes = read(os.path.join(self.meta, '0.papgt'))
        papgt = Papgt(papgt_bytes)
        st = self.state()
        backup = self.backup()
        problems = []
        if not papgt.valid_checksum:
            problems.append('meta/0.papgt fails its own checksum')
        ours = GROUP in papgt.names()
        foreign = [n for n in papgt.names() if not re.fullmatch(r'\d{4}', n) and n != GROUP]
        if foreign:
            problems.append('another mod manager has mods mounted: ' + ', '.join(foreign) +
                            ' (unmount there first, or run Steam Verify)')
        groups = [(n, c) for n, _f, c in papgt.groups
                  if re.fullmatch(r'\d{4}', n) and os.path.isfile(os.path.join(self.dir, n, '0.pamt'))]
        cache, dirty = self._verify_cache(), False
        for i, (name, chk) in enumerate(groups, 1):
            path = os.path.join(self.dir, name, '0.pamt')
            st_ = os.stat(path)
            sig = [st_.st_size, st_.st_mtime_ns, st_.st_ino, chk]
            if cache.get(path) == sig:                   # verified before and unchanged since
                continue
            if progress:
                progress(f'Checking game files {i}/{len(groups)}: {name} ({st_.st_size // 1024} KB)')
            stored, actual = pamt_checksum(read(path))
            if stored != chk or actual != chk:
                problems.append(f'{name}/0.pamt does not match the archive index (modified?)')
                cache.pop(path, None)
            else:
                cache[path] = sig
            dirty = True
        if dirty:
            self._save_verify_cache(cache)
        if ours:
            if not st or st.get('papgt_sha256') != sha256(papgt_bytes):
                problems.append('a cdmods group is registered but does not match what cdmods last wrote')
        elif backup is not None and papgt_bytes != backup:
            problems.append('meta/0.papgt differs from your saved original for this game version')
        leftovers = [d for d in os.listdir(self.dir)
                     if os.path.isdir(os.path.join(self.dir, d)) and d not in papgt.names()
                     and not re.fullmatch(r'\d{4}', d) and d not in ('bin64', 'meta')]
        return {'papgt': papgt, 'papgt_bytes': papgt_bytes, 'state': st, 'backup': backup,
                'applied': ours, 'problems': problems, 'leftovers': leftovers}

    def require_clean(self, progress=None):
        if self.game_running():
            raise ModError('Crimson Desert is running. Close the game first.')
        info = self.inspect(progress)
        if info['problems']:
            fix = ('\n\nFix: close the game, then in Steam right-click Crimson Desert > Properties >'
                   ' Installed Files > "Verify integrity of game files". Then run cdmods again.')
            if info['backup'] is not None and not any('0.pamt' in p for p in info['problems']):
                fix += ('\nOr put back your saved original for this game version with: ./cdmods.py restore'
                        ' (the "Restore original" option in the menu).')
            raise ModError('Your game files are not the originals:\n  - ' + '\n  - '.join(info['problems']) + fix)
        return info

    # --- actions --------------------------------------------------------------------------
    def original_papgt(self, info):
        """The original meta/0.papgt, backing it up first if needed."""
        if info['backup'] is not None:
            return info['backup']
        if info['applied']:
            raise ModError('cdmods is applied but the original backup for this game version is missing. '
                           'Run Steam "Verify integrity of game files" to get clean files.')
        self.save_backup(info['papgt_bytes'])
        return info['papgt_bytes']

    def restore(self, progress=print):
        """Put meta/0.papgt back and remove the cdmods group.

        Works even when the files were changed by something else, as long as we hold a backup for this
        exact game version and the archives themselves (0.pamt files) are untouched."""
        if self.game_running():
            raise ModError('Crimson Desert is running. Close the game first.')
        info = self.inspect(progress)
        if info['problems'] and (info['backup'] is None or any('0.pamt' in p for p in info['problems'])):
            info = self.require_clean(progress)                  # raises with the Steam Verify instructions
        original = self.original_papgt(info)
        if read(os.path.join(self.meta, '0.papgt')) != original:
            atomic_write(os.path.join(self.meta, '0.papgt'), original)
        group_dir = os.path.join(self.dir, GROUP)
        if os.path.isdir(group_dir):
            shutil.rmtree(group_dir)
        self.save_state(None)
        progress('Game restored to original.')

    def extract_tables(self, names, progress=print):
        pamt = read(os.path.join(self.dir, SOURCE_GROUP, '0.pamt'))
        index = parse_pamt(pamt)
        out = {}
        for name in names:
            for part in ('staticinfoheader', 'staticinfobody'):
                path = f'{TABLE_DIR}/{name}.{part}'
                if path not in index:
                    raise ModError(f'{path} not found in the game archives')
                paz_i, off, comp, orig, flags = index[path]
                with open(os.path.join(self.dir, SOURCE_GROUP, f'{paz_i}.paz'), 'rb') as f:
                    f.seek(off)
                    raw = f.read(comp)
                ctype = (flags >> 16) & 0xF
                if ctype == 0 and comp == orig:
                    data = raw
                elif ctype == 2:
                    progress(f'  reading {name}.{part} ({orig // 1024} KB)')
                    data = lz4_block_decompress(raw, orig)
                else:
                    raise ModError(f'{path}: unsupported storage type {ctype:#x}')
                out[(name, part)] = data
        return {n: Table(n, out[(n, 'staticinfoheader')], out[(n, 'staticinfobody')]) for n in names}

    def apply(self, mods, progress=print):
        check_selection(mods)
        if not mods:
            return self.restore(progress)
        progress('Checking game files...')
        info = self.require_clean(progress)
        original = self.original_papgt(info)
        tables_needed = sorted({t for m in mods for t in m.intents})
        progress('Loading original game tables...')
        tables = self.extract_tables(tables_needed, progress)
        changed = 0
        for m in mods:
            progress(f'Applying {m.title}')
            for table, intents in m.intents.items():
                t = tables[table]
                for it in intents:
                    off, fmt = field_location(t, it['entry'], it.get('key'), it['field'])
                    struct.pack_into(fmt, t.body, off, it['new'])
                    changed += 1
        files = {}
        for name, t in tables.items():
            files[f'{name}.staticinfoheader'] = t.header
            files[f'{name}.staticinfobody'] = bytes(t.body)
        size = sum(len(v) for v in files.values())
        progress(f'Building the cdmods archive ({size // 2**20} MB, takes a few seconds)...')
        pamt, paz = build_group(files)
        # start from the originals every time
        papgt = Papgt(original)
        papgt.groups = [[GROUP, GROUP_FLAGS, pamt_checksum(pamt)[0]]] + \
                       [g for g in papgt.groups if g[0] != GROUP]
        new_papgt = papgt.build()
        progress('Writing to the game folder...')
        group_dir = os.path.join(self.dir, GROUP)
        if read(os.path.join(self.meta, '0.papgt')) != original:
            atomic_write(os.path.join(self.meta, '0.papgt'), original)
        if os.path.isdir(group_dir):
            shutil.rmtree(group_dir)
        os.makedirs(group_dir)
        atomic_write(os.path.join(group_dir, '0.paz'), paz)
        atomic_write(os.path.join(group_dir, '0.pamt'), pamt)
        atomic_write(os.path.join(self.meta, '0.papgt'), new_papgt)
        self.save_state({'version_id': self.version_id, 'game_version': self.version, 'game_dir': self.dir,
                         'applied': [m.file for m in mods], 'titles': [m.title for m in mods],
                         'papgt_sha256': sha256(new_papgt), 'changes': changed,
                         'applied_at': datetime.now(timezone.utc).isoformat()})
        progress(f'Done: {len(mods)} mod(s), {changed} change(s) applied.')


# ---------------------------------------------------------------------------------------------
# TUI (curses)
# ---------------------------------------------------------------------------------------------

def run_tui(game, mods):
    import curses
    import textwrap

    groups = mod_groups(mods)
    applied = set()
    # rows: ('head', label) | ('opt', group_index, mod or None)
    rows, choice = [], {}
    for gi, (label, members) in enumerate(groups):
        radio = len(members) > 1
        rows.append(('head', label + ('  — pick one' if radio else '')))
        if radio:
            rows.append(('opt', gi, None))
        for m in members:
            rows.append(('opt', gi, m))
        choice[gi] = None
    selectable = [i for i, r in enumerate(rows) if r[0] == 'opt']
    state = {'cur': selectable[0] if selectable else 0, 'top': 0, 'msg': '', 'info': None}
    BAR = curses.A_REVERSE | curses.A_BOLD          # terminal's own colours, inverted: readable in any theme

    def refresh_info(scr, lines, select=False):
        """Re-read the game state (with a progress screen if files need checking)."""
        state['info'] = game.inspect(busy(scr, lines, 'Checking game files (first run or after a game update)'))
        applied.clear()
        if state['info']['applied'] and state['info']['state']:
            applied.update(state['info']['state']['applied'])
        if select:
            for gi, (_label, members) in enumerate(groups):
                on = [m for m in members if m.file in applied]
                choice[gi] = on[0] if on else None

    def selected():
        return [m for m in choice.values() if m is not None]

    def draw(scr):
        scr.erase()
        h, w = scr.getmaxyx()
        C = curses.color_pair
        info = state['info']
        put = lambda y, x, s, a=0: scr.addnstr(y, x, s, max(0, w - x - 1), a) if 0 <= y < h else None
        put(0, 0, ' Crimson Desert mods ', BAR)
        put(0, 23, f'game {game.version}   backup: {"saved" if info["backup"] is not None else "on first apply"}', C(4))
        if info['problems']:
            put(1, 1, 'Game files are NOT original — run Steam "Verify integrity of game files"', C(3) | curses.A_BOLD)
        elif info['applied'] and info['state']:
            put(1, 1, 'Applied: ' + ', '.join(info['state']['titles']), C(2))
        else:
            put(1, 1, 'Game files are original (no mods applied)', C(2))
        list_top, list_h = 3, max(3, h - 10)
        cur = state['cur']
        if cur < state['top'] + 1:
            state['top'] = max(0, cur - 1)
        if cur >= state['top'] + list_h:
            state['top'] = cur - list_h + 1
        for n, i in enumerate(range(state['top'], min(len(rows), state['top'] + list_h))):
            r, y = rows[i], list_top + n
            if r[0] == 'head':
                put(y, 1, r[1], C(4) | curses.A_BOLD)
                continue
            _, gi, m = r
            radio = len(groups[gi][1]) > 1
            on = choice[gi] is m if radio else choice[gi] is not None
            mark = ('(•)' if on else '( )') if radio else ('[x]' if on else '[ ]')
            text = m.title if m else 'None'
            if m and m.file in applied:
                text += '   (applied)'
            attr = curses.A_REVERSE if i == cur else 0
            put(y, 3, f'{mark} {text}', attr | (C(2) if on else 0))
        # description of highlighted mod
        r = rows[cur] if rows else None
        desc_y = list_top + list_h + 1
        if r and r[0] == 'opt' and r[2] is not None:
            for k, line in enumerate(textwrap.wrap(r[2].description, max(20, w - 4))[:3]):
                put(desc_y + k, 2, line, C(5))
        put(h - 3, 1, f'Selected: {len(selected())} mod(s)', curses.A_BOLD)
        put(h - 2, 1, state['msg'], C(3) if state['msg'].startswith('!') else C(2))
        put(h - 1, 0, ' ↑↓ move   Space select   A apply   R restore original   Q quit ', BAR)
        scr.refresh()

    def ask(scr, question):
        h, w = scr.getmaxyx()
        scr.addnstr(h - 2, 1, ' ' * (w - 2), w - 2)
        scr.addnstr(h - 2, 1, question + ' [y/N] ', w - 2, curses.color_pair(4) | curses.A_BOLD)
        scr.refresh()
        return scr.getch() in (ord('y'), ord('Y'))

    def busy(scr, lines, title='Working... do not start the game'):
        h, w = scr.getmaxyx()

        def progress(msg):
            lines.append(msg)
            scr.erase()
            scr.addnstr(0, 0, f' {title} ', w - 1, BAR)
            for k, line in enumerate(lines[-(h - 2):]):
                scr.addnstr(k + 1, 1, line, w - 2)
            scr.refresh()
        return progress

    def main(scr):
        curses.curs_set(0)
        curses.use_default_colors()
        for i, (fg, bg) in enumerate([(-1, -1), (curses.COLOR_GREEN, -1),
                                      (curses.COLOR_RED, -1), (curses.COLOR_CYAN, -1), (-1, -1)], 1):
            curses.init_pair(i, fg, bg)
        refresh_info(scr, [], select=True)
        while True:
            draw(scr)
            k = scr.getch()
            if k in (ord('q'), ord('Q')):
                return 0
            if k in (curses.KEY_UP, ord('k')):
                pos = selectable.index(state['cur'])
                state['cur'] = selectable[max(0, pos - 1)]
            elif k in (curses.KEY_DOWN, ord('j')):
                pos = selectable.index(state['cur'])
                state['cur'] = selectable[min(len(selectable) - 1, pos + 1)]
            elif k in (ord(' '), 10, curses.KEY_ENTER):
                _, gi, m = rows[state['cur']]
                if len(groups[gi][1]) > 1:
                    choice[gi] = m
                else:
                    choice[gi] = None if choice[gi] else m
                state['msg'] = ''
            elif k in (ord('a'), ord('A'), ord('r'), ord('R')):
                restore = k in (ord('r'), ord('R')) or not selected()
                q = 'Restore the original game files?' if restore else f'Apply {len(selected())} mod(s)?'
                if not ask(scr, q):
                    state['msg'] = 'Cancelled.'
                    continue
                lines = []
                try:
                    if restore:
                        game.restore(busy(scr, lines))
                        for gi in choice:
                            choice[gi] = None
                    else:
                        game.apply(selected(), busy(scr, lines))
                    state['msg'] = (lines[-1] if lines else 'Done.') + '  You can start the game now.'
                except ModError as e:
                    state['msg'] = '! ' + str(e).splitlines()[0]
                    busy(scr, lines)('')
                    for line in str(e).splitlines():
                        busy(scr, lines)(line)
                    busy(scr, lines)('Press any key...')
                    scr.getch()
                refresh_info(scr, lines)

    return curses.wrapper(main)


# ---------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------

def status_lines(game):
    info = game.inspect()
    lines = [f'Game folder : {game.dir}', f'Game version: {game.version}  ({game.version_id})',
             f'Original backup: {"saved" if info["backup"] is not None else "not saved yet (taken on first apply)"}']
    if info['applied'] and info['state']:
        lines.append('Applied mods:')
        lines += [f'  - {t}' for t in info['state']['titles']]
    else:
        lines.append('Applied mods: none (game files are original)' if not info['problems'] else 'Applied mods: unknown')
    if info['problems']:
        lines.append('PROBLEMS:')
        lines += [f'  - {p}' for p in info['problems']]
    if info['leftovers']:
        lines.append('Note: unregistered folders in the game dir (harmless, from other tools): ' + ', '.join(info['leftovers']))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description='Apply the Crimson Desert mods in this folder, no mod manager needed.')
    ap.add_argument('--game', help='Crimson Desert install folder (default: find it through Steam)')
    ap.add_argument('command', nargs='?', choices=['tui', 'status', 'list', 'apply', 'restore'], default='tui')
    ap.add_argument('files', nargs='*', help='mod files for "apply"')
    args = ap.parse_args(argv)
    try:
        if args.command == 'list':
            for label, members in mod_groups(load_mods()):
                print(label + (' (pick one)' if len(members) > 1 else ''))
                for m in members:
                    print(f'   {m.file:60s} {m.title}')
            return 0
        game = Game(args.game)
        if args.command == 'status':
            print('\n'.join(status_lines(game)))
        elif args.command == 'restore':
            game.restore()
        elif args.command == 'apply':
            by_name = {m.file: m for m in load_mods()}
            chosen = []
            for f in args.files:
                key = os.path.basename(f)
                if key not in by_name:
                    raise ModError(f'unknown mod file: {f} (see "cdmods.py list")')
                chosen.append(by_name[key])
            game.apply(chosen)
        else:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ModError('the TUI needs a terminal; use "status", "list", "apply" or "restore" instead')
            return run_tui(game, load_mods())
        return 0
    except ModError as e:
        print(f'cdmods: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
