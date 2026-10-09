# -*- coding: utf-8 -*-
"""AstroStation 天文盒子文件浏览器 (v1.0.1)
================================================
连接树莓派天文盒子 (as / astrostation / 共享 AstroStation)。自动发现盒子地址:
  ① 上次成功的地址 -> ② 10.0.10.1 (盒子 WiFi 热点) -> ③ mDNS 搜索 raspberrypi.local (网线直连)
提供:
  - 远程浏览: 目录树(懒加载) + 文件列表(排序/类型图标/大小/时间) + 面包屑导航 + 前进/后退
  - 下载: 选中项(文件或文件夹, 递归)下载到本地任意位置
  - 批量下载: 多条 "远程目录 -> 本地目录" 映射任务, 一键后台执行
  - 后台任务队列面板: 逐任务进度条 / 取消 / 重试 / 状态汇总
  - CLI: 命令行模式 (discover / ls / stat / dl), 供自动化(脚本 / windows-mcp)直接调用
  - 连接设置: 界面「设置」按钮可改 地址 / 账号 / 密码 / 共享名 (默认 = 图谱盒子出厂值)
  - 无头自检: --selftest (发现+连接+列目录, 并写 selftest_result.txt)

用法:
  python stellavita_browser.py                 # 图形界面
  python stellavita_browser.py --selftest      # 无头自检
  python stellavita_browser.py --cli dl <远程路径> <本地路径> [--jobs 8]
依赖:  pip install impacket customtkinter

SMB 协议逻辑与旧版一致(保持不变):
  SMBConnection(host, host, timeout=10) / login / connectTree
  / listPath(share, pattern, 3) / getFile(share, path, callback)
"""
import os, json, time, threading, itertools, socket, struct, sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import customtkinter as ctk

try:
    from impacket.smbconnection import SMBConnection
except ImportError:
    SMBConnection = None

# ---------------- 配置常量 ----------------
HOST = '10.0.10.1'
USER = 'as'
PASS = 'astrostation'
SHARE = 'AstroStation'
DIR_ATTR = 0x10
def _app_base_dir():
    """数据基准目录: 打包(frozen)后=exe 所在目录; 脚本运行=脚本所在目录。
    (修复: PyInstaller onefile 下 __file__ 在临时解压目录, 配置/记忆会随退出丢失)"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

VERSION = 'v1.0.1'
CONFIG_PATH = os.path.join(_app_base_dir(), 'astrostation_config.json')
SPINNER = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
PHASE_STAT = '统计中…'
PHASE_DL = '下载中'
PARALLEL_DEFAULT = 8  # 默认并行连接数(下载面板可调)

# 连接参数(界面「设置」/ 配置文件可改; 默认 = 图谱盒子出厂值)
CREDS = {'user': USER, 'pass': PASS, 'share': SHARE}

# ---------------- 配色(暗色现代) ----------------
C = {
    'bg': '#111216', 'panel': '#1a1c21', 'panel2': '#20232a', 'panel3': '#262a33',
    'text': '#e8eaf0', 'dim': '#8d94a5', 'faint': '#5b6272',
    'accent': '#4f8cff', 'accent_h': '#3a74e0',
    'ok': '#2f9e44', 'ok_h': '#267a35',
    'warn': '#ffb454', 'err': '#ff5c6c',
    'folder': '#7db6ff', 'fits': '#ffd97a', 'file': '#d9dde6',
    'sel': '#2e4e8f', 'violet': '#7c5cd6', 'violet_h': '#6a4ac0',
}

FONT = ('Microsoft YaHei UI', 11)
FONT_SMALL = ('Microsoft YaHei UI', 10)
FONT_BOLD = ('Microsoft YaHei UI', 12, 'bold')
FONT_TITLE = ('Microsoft YaHei UI', 16, 'bold')
FONT_HEAD = ('Microsoft YaHei UI', 10, 'bold')

TYPE_MAP = {
    '.fits': ('✨', 'FITS 天文图'), '.fit': ('✨', 'FITS 天文图'), '.fts': ('✨', 'FITS 天文图'),
    '.fz': ('✨', 'FITS 压缩'),
    '.jpg': ('🖼', '图像'), '.jpeg': ('🖼', '图像'), '.png': ('🖼', '图像'), '.gif': ('🖼', '图像'),
    '.bmp': ('🖼', '图像'), '.tif': ('🖼', '图像'), '.tiff': ('🖼', '图像'),
    '.cr2': ('🖼', 'RAW 图像'), '.nef': ('🖼', 'RAW 图像'), '.arw': ('🖼', 'RAW 图像'), '.dng': ('🖼', 'RAW 图像'),
    '.mp4': ('🎞', '视频'), '.mov': ('🎞', '视频'), '.avi': ('🎞', '视频'),
    '.mkv': ('🎞', '视频'), '.mts': ('🎞', '视频'), '.m2ts': ('🎞', '视频'),
    '.mp3': ('🎵', '音频'), '.wav': ('🎵', '音频'), '.flac': ('🎵', '音频'), '.aac': ('🎵', '音频'),
    '.zip': ('🗜', '压缩包'), '.tar': ('🗜', '压缩包'), '.gz': ('🗜', '压缩包'),
    '.7z': ('🗜', '压缩包'), '.rar': ('🗜', '压缩包'),
    '.txt': ('📄', '文本'), '.md': ('📄', '文本'), '.log': ('📄', '日志'),
    '.cfg': ('📄', '配置'), '.ini': ('📄', '配置'), '.json': ('📄', '配置'),
    '.py': ('🐍', '脚本'), '.sh': ('🐍', '脚本'), '.bat': ('🐍', '脚本'),
    '.csv': ('📊', '表格'), '.xls': ('📊', '表格'), '.xlsx': ('📊', '表格'),
}
FITS_EXTS = ('.fits', '.fit', '.fts', '.fz')

# ---------------- 工具函数 ----------------
def size_str(b):
    for u in ['B', 'KB', 'MB', 'GB']:
        if b < 1024:
            return f'{b:.0f} {u}' if u == 'B' else f'{b:.1f} {u}'
        b /= 1024
    return f'{b:.1f} TB'

def fmt_time(ts):
    if not ts:
        return '—'
    t = time.localtime(ts)
    now = time.localtime()
    if (t.tm_year, t.tm_mon, t.tm_mday) == (now.tm_year, now.tm_mon, now.tm_mday):
        return f'今天 {t.tm_hour:02d}:{t.tm_min:02d}'
    return time.strftime('%Y-%m-%d %H:%M', t)

def ell(s, n):
    s = str(s)
    return s if len(s) <= n else s[:max(0, n - 1)] + '…'

def _short(e, n=160):
    s = str(e) or e.__class__.__name__
    return s if len(s) <= n else s[:n - 1] + '…'

def _rm_quiet(p):
    try:
        os.remove(p)
    except Exception:
        pass

# 远程路径处理(纯字符串, 根目录用 '' 表示)
def norm_path(p):
    p = str(p or '').strip().replace('\\', '/')
    if p in ('', '/', '.'):
        return ''
    return p.rstrip('/')

def join_path(a, b):
    a = norm_path(a)
    b = str(b).strip().replace('\\', '/').lstrip('/')
    return f'{a}/{b}' if a else b

def parent_path(p):
    p = norm_path(p)
    i = p.rfind('/')
    return p[:i] if i >= 0 else ''

def basename_path(p):
    p = norm_path(p)
    return p.split('/')[-1] if p else ''

def task_chip(task):
    s = task.state
    if s == 'pending':
        return '排队中', 'dim'
    if s == 'running':
        if task.phase == PHASE_STAT:
            return PHASE_STAT, 'warn'
        return f'{int(task.progress() * 100)}%', 'accent'
    if s == 'done':
        return '✓ 完成', 'ok'
    if s == 'error':
        return '失败', 'err'
    return '已取消', 'dim'

def task_info(task):
    if task.state == 'done':
        return f'{size_str(task.bytes_done)} · {task.files_done} 个文件'
    if task.state == 'running':
        if task.phase == PHASE_STAT:
            return '正在统计文件数量…'
        return f'{task.files_done}/{task.files_total} 个文件 · {size_str(task.bytes_done)}'
    if task.state == 'error':
        return ell(task.error, 30)
    if task.state == 'cancelled':
        return '已取消'
    return ''

# ---------------- 远程条目 ----------------
class RemoteEntry:
    __slots__ = ('name', 'is_dir', 'size', 'mtime')

    def __init__(self, name, is_dir, size, mtime):
        self.name = name
        self.is_dir = is_dir
        self.size = size
        self.mtime = mtime

    @property
    def ext(self):
        return os.path.splitext(self.name)[1].lower()

    @property
    def is_fits(self):
        return self.ext in FITS_EXTS or self.name.lower().endswith('.fits')

    @property
    def icon(self):
        return '📁' if self.is_dir else TYPE_MAP.get(self.ext, ('📄', '文件'))[0]

    @property
    def type_str(self):
        return '文件夹' if self.is_dir else TYPE_MAP.get(self.ext, ('📄', '文件'))[1]

# ---------------- 本地配置(记住窗口/下载位置) ----------------
class AppConfig:
    def __init__(self):
        self.path = CONFIG_PATH
        self.data = {}
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
        except Exception:
            self.data = {}

    def get(self, k, d=None):
        return self.data.get(k, d)

    def set(self, k, v):
        self.data[k] = v

    def save(self):
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def _load_creds(cfg):
    """从配置读取连接参数(账号/密码/共享名)到 CREDS; 界面/CLI/自检共用。"""
    for key, dflt in (('user', USER), ('pass', PASS), ('share', SHARE)):
        try:
            v = cfg.get(key, dflt)
        except Exception:
            v = dflt
        if v:
            CREDS[key] = str(v)


def apply_settings(cfg, host=None, user=None, password=None, share=None):
    """写入连接设置并落盘。host=None 不改地址; host='' 清空固定地址(纯自动发现)。"""
    if host is not None:
        cfg.set('last_host', str(host or '').strip())
    if user is not None and str(user).strip():
        cfg.set('user', str(user).strip())
    if password is not None:
        cfg.set('pass', str(password))
    if share is not None and str(share).strip():
        cfg.set('share', str(share).strip())
    cfg.save()
    _load_creds(cfg)

# ---------------- SMB 服务层(协议用法与旧版一致) ----------------
class SMBService:
    """封装 impacket SMB 连接 / 列目录 / 递归遍历 / 下载。
    注意: impacket 连接对象非线程安全, 所有 SMB 调用用锁串行化。"""

    def __init__(self, host=HOST, user=None, password=None, share=None):
        self.host = host or HOST
        self.user = user if user is not None else CREDS['user']
        self.password = password if password is not None else CREDS['pass']
        self.share = share if share is not None else CREDS['share']
        self._conn = None
        self._lock = threading.RLock()

    @property
    def connected(self):
        return self._conn is not None

    def connect(self):
        with self._lock:
            self._conn = SMBConnection(self.host, self.host, timeout=10)
            self._conn.login(self.user, self.password)
            self._conn.connectTree(self.share)

    def close(self):
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def list_dir(self, path):
        """列出目录内容(不含 . / ..), 根目录传 ''。"""
        with self._lock:
            if self._conn is None:
                raise ConnectionError('未连接盒子')
            patt = (path.rstrip('/') + '/*') if path else '*'
            fs = self._conn.listPath(self.share, patt, 3)
            out = []
            for f in fs:
                name = f.get_longname()
                if name in ('.', '..'):
                    continue
                isdir = bool(f.get_attributes() & DIR_ATTR)
                try:
                    size = f.get_filesize()
                except Exception:
                    size = 0
                try:
                    mt = f.get_mtime_epoch()
                except Exception:
                    mt = 0
                out.append(RemoteEntry(name, isdir, size, mt))
            return out

    def walk(self, path, check=None):
        """深度优先遍历 path 下所有文件, 产出 (所在远程目录, 条目)。"""
        stack = [path]
        while stack:
            cur = stack.pop()
            dirs, files = [], []
            for e in self.list_dir(cur):
                if check:
                    check()
                (dirs if e.is_dir else files).append(e)
            for e in files:
                if check:
                    check()
                yield cur, e
            for e in reversed(dirs):
                stack.append(join_path(cur, e.name))

    def count_tree(self, path, check=None):
        n = b = 0
        for _cur, e in self.walk(path, check):
            n += 1
            b += e.size
        return n, b

    def download_file(self, remote, local_path, on_bytes=None, check=None):
        with self._lock:
            if self._conn is None:
                raise ConnectionError('未连接盒子')
            if check:
                check()
            with open(local_path, 'wb') as fh:
                def cb(data):
                    if check:
                        check()
                    fh.write(data)
                    if on_bytes:
                        on_bytes(len(data))
                self._conn.getFile(self.share, remote.rstrip('/'), cb)

# ---------------- 盒子自动发现 (网线直连 / WiFi 热点) ----------------
def _parse_mdns_a(data):
    """极简 mDNS 应答解析: 返回其中的所有 IPv4 (A 记录)。"""
    res = []
    try:
        if len(data) < 12:
            return res
        qd, an, ns, ar = struct.unpack('>HHHH', data[4:12])
        off = 12

        def read_name(o):
            labels = []
            hops = 0
            while hops < 12:
                if o >= len(data):
                    break
                l = data[o]
                if l == 0:
                    o += 1
                    break
                if l & 0xC0:
                    o += 2
                    break
                labels.append(data[o + 1:o + 1 + l].decode('latin1', 'ignore'))
                o += 1 + l
                hops += 1
            return '.'.join(labels), o

        for _ in range(qd):
            _nm, off = read_name(off)
            off += 4
        for _ in range(an + ns + ar):
            _nm, off = read_name(off)
            if off + 10 > len(data):
                break
            typ, cls, _ttl, ln = struct.unpack('>HHIH', data[off:off + 10])
            off += 10
            rd = data[off:off + ln]
            off += ln
            if typ == 1 and ln == 4:
                res.append(socket.inet_ntoa(rd))
    except Exception:
        pass
    return res


def _mdns_find(timeout=1.8):
    """mDNS 搜索: 主机名(raspberrypi.local) + SMB 服务(_smb._tcp.local), 返回候选 IP 列表。"""
    found = []
    queries = []
    try:
        for name, qtype in (('raspberrypi.local', 1), ('_smb._tcp.local', 12)):
            q = struct.pack('>HHHHHH', 0, 0, 1, 0, 0, 0)
            for part in name.split('.'):
                b = part.encode()
                q += bytes([len(b)]) + b
            q += b'\x00' + struct.pack('>HH', qtype, 1)
            queries.append(q)
    except Exception:
        return found
    socks = []
    try:
        srcs = [ip for ip in socket.gethostbyname_ex(socket.gethostname())[2]
                if not ip.startswith('127.')]
    except Exception:
        srcs = []
    for src in (srcs or ['']):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if src:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(src))
                s.bind((src, 0))
            else:
                s.bind(('', 0))
            for q in queries:
                s.sendto(q, ('224.0.0.251', 5353))
            socks.append(s)
        except Exception:
            pass
    try:
        ml = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ml.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ml.bind(('', 5353))
        socks.append(ml)
    except Exception:
        pass
    end = time.time() + timeout
    while time.time() < end and len(found) < 8:
        for s in socks:
            try:
                s.settimeout(max(0.05, end - time.time()))
                data, addr = s.recvfrom(4096)
            except Exception:
                continue
            for ip in _parse_mdns_a(data):
                if ip not in found:
                    found.append(ip)
    for s in socks:
        try:
            s.close()
        except Exception:
            pass
    return found


def _quick_445(ip, timeout=0.8):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        ok = (s.connect_ex((ip, 445)) == 0)
        s.close()
        return ok
    except Exception:
        return False


def _nbns_find(timeout=1.5):
    """NetBIOS 名字广播查询(备用发现通道): 找名为 RASPBERRYPI 的节点, 返回应答 IP 列表。"""
    found = []
    try:
        raw = b'RASPBERRYPI'.ljust(15, b' ') + b'\x00'
        enc = b''
        for b in raw:
            enc += bytes([0x41 + (b >> 4), 0x41 + (b & 0x0F)])
        q = struct.pack('>HHHHHH', 0x4E42, 0x0110, 1, 0, 0, 0) + b'\x20' + enc + b'\x00'
        q += struct.pack('>HH', 0x0020, 0x0001)
    except Exception:
        return found
    socks = []
    try:
        srcs = [ip for ip in socket.gethostbyname_ex(socket.gethostname())[2]
                if not ip.startswith('127.')]
    except Exception:
        srcs = []
    for src in (srcs or ['']):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if src:
                s.bind((src, 0))
            else:
                s.bind(('', 0))
            for dst in ('169.254.255.255', '255.255.255.255'):
                try:
                    s.sendto(q, (dst, 137))
                except Exception:
                    pass
            socks.append(s)
        except Exception:
            pass
    end = time.time() + timeout
    while time.time() < end and len(found) < 4:
        for s in socks:
            try:
                s.settimeout(max(0.05, end - time.time()))
                data, addr = s.recvfrom(2048)
            except Exception:
                continue
            try:
                flags = struct.unpack('>H', data[2:4])[0]
                an = struct.unpack('>H', data[6:8])[0]
            except Exception:
                continue
            if (flags & 0x8000) and an >= 1 and addr[0] not in found:
                found.append(addr[0])
    for s in socks:
        try:
            s.close()
        except Exception:
            pass
    return found


def discover_host(cfg=None, log=None):
    """按 ①上次地址 ②10.0.10.1 ③mDNS ④NetBIOS 名字广播 顺序找盒子, 返回第一个能登录的地址。
    惰性探测: 前面的候选找到就不再做后面的广播搜索。"""
    seen = set()

    def try_ip(ip):
        if not ip or ip in seen:
            return None
        seen.add(ip)
        for attempt in (1, 2):
            if not _quick_445(ip):
                # 盒子 445 偶发抖动: 稍等重试一次 (实测遇到过自愈)
                if attempt == 1:
                    time.sleep(0.3)
                    continue
                if log:
                    log(f'{ip} 445 不通')
                return None
            try:
                svc = SMBService(host=ip)
                svc.connect()
                svc.close()
                return ip
            except Exception as e:
                if log:
                    log(f'{ip} 登录失败: {str(e)[:80]}')
                return None
        return None

    base = []
    if cfg is not None:
        try:
            base.append(str(cfg.get('last_host', '') or '').strip())
        except Exception:
            pass
    base.append('10.0.10.1')
    if log:
        log('候选: ' + (', '.join([b for b in base if b]) or '无'))
    for ip in base:
        got = try_ip(ip)
        if got:
            return got
    extra = _mdns_find()
    if extra and log:
        log('mDNS: ' + ', '.join(extra))
    for ip in extra:
        got = try_ip(ip)
        if got:
            return got
    extra = _nbns_find()
    if extra and log:
        log('NetBIOS: ' + ', '.join(extra))
    for ip in extra:
        got = try_ip(ip)
        if got:
            return got
    return None

# ---------------- 下载任务 / 队列引擎 ----------------
class TaskCancelled(Exception):
    pass

_TASK_IDS = itertools.count(1)

class DownloadTask:
    def __init__(self, remote, local, kind='file', size_hint=None, title=None):
        self.id = next(_TASK_IDS)
        self.remote = remote
        self.local = local
        self.kind = kind          # 'file' 单文件 | 'dir' 文件夹(本地建同名子目录) | 'batch' 批量(直接进本地目录)
        self.size_hint = size_hint or 0
        self.title = title or basename_path(remote) or remote
        self.state = 'pending'    # pending / running / done / error / cancelled
        self.phase = ''
        self.files_total = 0
        self.files_done = 0
        self.bytes_total = 0
        self.bytes_done = 0
        self.error = ''
        self.created = time.time()
        self.finished = 0
        self._cancelled = False
        self._last_emit = 0.0
        self._lock = threading.RLock()
        self._fails = []
        self._last_err = ''

    @property
    def terminal(self):
        return self.state in ('done', 'error', 'cancelled')

    def check(self):
        if self._cancelled:
            raise TaskCancelled()

    def add_bytes(self, n):
        with self._lock:
            self.bytes_done += n

    def progress(self):
        if self.state == 'done':
            return 1.0
        if self.state != 'running':
            return 0.0
        if self.bytes_total > 0:
            return min(1.0, self.bytes_done / self.bytes_total)
        if self.files_total > 0:
            return min(1.0, self.files_done / self.files_total)
        return 0.0


class DownloadQueue:
    """v3 下载引擎: 全局并行连接池。
    所有任务共享 PARALLEL 条 SMB 连接; 文件夹任务会拆分到多条连接上并行下载。"""

    def __init__(self, on_change=None, host_getter=None):
        self._host_getter = host_getter or (lambda: HOST)
        self.tasks = []
        self._cv = threading.Condition()
        self._stop = False
        self._on_change = on_change
        self._active = []           # 轮转: [task, [(remote, local), ...], next_index]
        self._expand_pending = []   # 待展开(统计)的任务
        self._workers = []
        self._parallel = 1
        self._expander = threading.Thread(target=self._expand_loop, daemon=True)
        self._expander.start()
        self.set_parallel(PARALLEL_DEFAULT)

    # ---- 对外接口 ----
    def submit(self, remote, local, kind='file', size_hint=None, title=None):
        task = DownloadTask(remote, local, kind, size_hint, title)
        with self._cv:
            self.tasks.append(task)
            self._expand_pending.append(task)
            self._cv.notify_all()
        self._emit(task, force=True)
        return task

    def retry(self, task):
        return self.submit(task.remote, task.local, task.kind, task.size_hint, task.title)

    def cancel(self, task):
        with self._cv:
            task._cancelled = True
            if task.state == 'pending':
                task.state = 'cancelled'
                task.finished = time.time()
            self._cv.notify_all()
        self._emit(task, force=True)

    def set_parallel(self, n):
        try:
            n = int(n)
        except Exception:
            return
        with self._cv:
            self._parallel = max(1, min(16, n))
            while len(self._workers) < self._parallel:
                th = threading.Thread(target=self._worker_loop, args=(len(self._workers),), daemon=True)
                th.start()
                self._workers.append(th)
            self._cv.notify_all()

    def parallel(self):
        return self._parallel

    def prune_finished(self):
        with self._cv:
            self.tasks = [t for t in self.tasks if not t.terminal]

    def snapshot(self):
        with self._cv:
            return list(self.tasks)

    def shutdown(self):
        with self._cv:
            self._stop = True
            self._cv.notify_all()

    # ---- 展开(统计)线程 ----
    def _expand_loop(self):
        lister = None
        lister_key = None
        while True:
            with self._cv:
                while not self._stop and not self._expand_pending:
                    self._cv.wait(0.3)
                if self._stop:
                    return
                task = None
                for t in self._expand_pending:
                    if t.state == 'pending' and not t._cancelled:
                        task = t
                        break
                if task is not None:
                    self._expand_pending.remove(task)
                else:
                    self._expand_pending = []
            if task is None:
                continue
            try:
                live = self._host_getter()
                key = (live, CREDS['user'], CREDS['pass'], CREDS['share'])
                if lister is not None and lister_key != key:
                    # 盒子地址/账号变化后, 统计连接重建
                    try:
                        lister.close()
                    except Exception:
                        pass
                    lister = None
                if lister is None:
                    lister = SMBService(host=live)
                    lister.connect()
                    lister_key = key
                self._expand_task(lister, task)
            except TaskCancelled:
                task.state = 'cancelled'
                task.finished = time.time()
                self._emit(task, force=True)
            except Exception as e:
                if lister is not None:
                    try:
                        lister.close()
                    except Exception:
                        pass
                    lister = None
                task.state = 'error'
                task.error = _short(e, 200)
                task.finished = time.time()
                self._emit(task, force=True)

    def _expand_task(self, lister, task):
        task.state = 'running'
        if task.kind == 'file':
            task.files_total = 1
            task.bytes_total = task.size_hint
            task.phase = PHASE_DL
            self._emit(task, force=True)
            self._push(task, [(task.remote, os.path.join(task.local, basename_path(task.remote)))])
            return
        task.phase = PHASE_STAT
        self._emit(task, force=True)
        files = []
        total_b = 0
        base = task.local
        if task.kind == 'dir':
            base = os.path.join(base, basename_path(task.remote))
        prefix = task.remote.rstrip('/')
        for cur, e in lister.walk(task.remote, check=task.check):
            task.check()
            rel = cur[len(prefix):].strip('/') if prefix else cur
            ldir = os.path.join(base, *rel.split('/')) if rel else base
            files.append((join_path(cur, e.name), os.path.join(ldir, e.name)))
            total_b += e.size
        task.files_total = len(files)
        task.bytes_total = total_b
        task.phase = PHASE_DL
        self._emit(task, force=True)
        self._push(task, files)

    def _push(self, task, files):
        with self._cv:
            if task._cancelled:
                task.files_done = task.files_total
                task.state = 'cancelled'
                task.finished = time.time()
                self._emit(task, force=True)
                return
            if not files:
                task.state = 'done'
                task.finished = time.time()
                self._emit(task, force=True)
                return
            self._active.append([task, files, 0])
            self._cv.notify_all()

    # ---- 连接池工作线程 ----
    def _worker_loop(self, idx):
        svc = SMBService(host=self._host_getter())
        while True:
            job = None
            with self._cv:
                while not self._stop:
                    if idx >= self._parallel or not self._active:
                        self._cv.wait(0.4)
                        continue
                    task, files, i = self._active[0]
                    if i >= len(files):
                        self._active.pop(0)
                        continue
                    self._active[0][2] = i + 1
                    job = (task, files[i][0], files[i][1])
                    break
                if self._stop:
                    try:
                        svc.close()
                    except Exception:
                        pass
                    return
            if job is None:
                continue
            self._run_file(svc, job)

    def _run_file(self, svc, job):
        task, remote, local = job
        if task._cancelled or task.terminal:
            self._count_file(task, ok=False)
            return
        last_err = ''
        for attempt in (1, 2):
            try:
                task.check()
                os.makedirs(os.path.dirname(local) or '.', exist_ok=True)
                live = self._host_getter()
                if live and svc.host != live:
                    # 修复: 盒子地址可能已重新发现(直连地址变化/热点切换), 跟随最新地址
                    try:
                        svc.close()
                    except Exception:
                        pass
                    svc.host = live
                svc.user = CREDS['user']
                svc.password = CREDS['pass']
                svc.share = CREDS['share']
                if not svc.connected:
                    svc.connect()
                svc.download_file(remote, local,
                                  on_bytes=lambda n: (task.add_bytes(n), self._emit(task)),
                                  check=task.check)
                self._count_file(task, ok=True)
                return
            except TaskCancelled:
                _rm_quiet(local)
                self._count_file(task, ok=False)
                return
            except Exception as e:
                last_err = '%s [目标 %s]' % (_short(e, 140), svc.host)
                _rm_quiet(local)
                try:
                    svc.close()
                except Exception:
                    pass
                if attempt == 2:
                    with task._lock:
                        task._fails.append(basename_path(remote))
                        task._last_err = last_err
                    self._count_file(task, ok=False)
                    return

    def _count_file(self, task, ok):
        with task._lock:
            task.files_done += 1
            done = task.files_done
        if done >= task.files_total >= 1:
            with self._cv:
                if task.state == 'running':
                    if task._cancelled:
                        task.state = 'cancelled'
                    elif task._fails:
                        task.state = 'error'
                        task.error = '%d 个文件失败: %s' % (
                            len(task._fails), ', '.join(str(x) for x in task._fails[:3]))
                        if task._last_err:
                            task.error += ' | ' + task._last_err
                    else:
                        task.state = 'done'
                    task.finished = time.time()
            self._emit(task, force=True)
        else:
            self._emit(task)

    def _emit(self, task, force=False):
        if self._on_change is None:
            return
        now = time.monotonic()
        if not force and task._last_emit and now - task._last_emit < 0.1:
            return
        task._last_emit = now
        try:
            self._on_change(task)
        except Exception:
            pass

# ---------------- 批量下载窗口 ----------------
class BatchDLWindow(ctk.CTkToplevel):
    """多条 (远程目录 -> 本地目录) 任务, 提交到后台队列后窗口可关闭。"""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self._subs = []       # [(remote, local, row_widgets)]
        self._sub_ids = []    # 每个提交对应的队列任务 id(开始前为 None)
        self._running = False
        self._local_dir = ''
        self.title('批量下载 — 任务队列')
        self.geometry('880x600')
        self.minsize(700, 460)
        self.transient(app)
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self.app.add_task_listener(self._on_task_change)
        self._build_ui()
        x = app.winfo_x() + max(0, (app.winfo_width() - 880) // 2)
        y = app.winfo_y() + max(0, (app.winfo_height() - 600) // 3)
        self.geometry(f'880x600+{x}+{y}')
        self._fade_in()
        self._st('添加任务后点击「▶ 开始批量下载」; 窗口可随时关闭, 下载在主窗口队列中继续', 'dim')

    # ---- UI ----
    def _build_ui(self):
        ctk.CTkLabel(self, text='批量下载 — 远程目录 → 本地目录',
                     font=FONT_TITLE, text_color=C['text']).pack(pady=(16, 2))
        ctk.CTkLabel(self, text='每个任务将远程目录的【全部内容】保存到所选本地目录',
                     font=FONT_SMALL, text_color=C['dim']).pack()

        addf = ctk.CTkFrame(self, fg_color=C['panel2'], corner_radius=12)
        addf.pack(fill='x', padx=16, pady=10)
        self.r_entry = ctk.CTkEntry(addf, placeholder_text='远程目录, 如 sequence/Light',
                                    width=300, font=FONT, height=32)
        self.r_entry.pack(side='left', padx=(12, 6), pady=10)
        self.r_entry.insert(0, self.app.cwd or '')
        self.r_entry.bind('<Return>', lambda e: self.add_task())
        self.lbl_local = ctk.CTkLabel(addf, text='本地: (未选择)', width=240, anchor='w',
                                      font=FONT_SMALL, text_color=C['dim'])
        self.lbl_local.pack(side='left', padx=4)
        ctk.CTkButton(addf, text='选择…', width=70, height=32, font=FONT_SMALL,
                      fg_color=C['panel3'], hover_color=C['accent_h'],
                      command=self.pick_local).pack(side='left', padx=4)
        ctk.CTkButton(addf, text='＋ 添加任务', width=110, height=32, font=FONT_SMALL,
                      fg_color=C['accent'], hover_color=C['accent_h'],
                      command=self.add_task).pack(side='left', padx=(8, 12))

        listf = ctk.CTkFrame(self, fg_color='transparent')
        listf.pack(fill='both', expand=True, padx=16)
        hdr = ctk.CTkFrame(listf, fg_color='transparent')
        hdr.pack(fill='x')
        self.lbl_count = ctk.CTkLabel(hdr, text='任务清单 (0)', font=FONT_BOLD,
                                      text_color=C['text'])
        self.lbl_count.pack(side='left')
        for txt, w, cmd in (('保存清单', 84, self.save_tasks),
                            ('载入清单', 84, self.load_tasks),
                            ('清空', 64, self.clear_subs)):
            ctk.CTkButton(hdr, text=txt, width=w, height=26, font=FONT_SMALL,
                          fg_color=C['panel2'], hover_color=C['panel3'],
                          command=cmd).pack(side='right', padx=3)
        self.list_area = ctk.CTkScrollableFrame(listf, fg_color=C['panel'], corner_radius=12)
        self.list_area.pack(fill='both', expand=True, pady=(6, 0))

        bot = ctk.CTkFrame(self, fg_color='transparent')
        bot.pack(fill='x', padx=16, pady=(10, 4))
        self.overall = ctk.CTkProgressBar(bot, height=8, corner_radius=4,
                                          progress_color=C['accent'])
        self.overall.set(0)
        self.overall.pack(side='left', fill='x', expand=True)
        self.lbl_overall = ctk.CTkLabel(bot, text='0/0', width=52, anchor='e', font=FONT_SMALL)
        self.lbl_overall.pack(side='left', padx=8)
        self.btn_start = ctk.CTkButton(bot, text='▶ 开始批量下载', width=150, height=34,
                                       font=FONT_BOLD, fg_color=C['ok'], hover_color=C['ok_h'],
                                       command=self.start)
        self.btn_start.pack(side='right')
        self.status = ctk.CTkLabel(self, text='', anchor='w', font=FONT_SMALL, text_color=C['dim'])
        self.status.pack(fill='x', padx=18, pady=(0, 12))

    def _fade_in(self):
        try:
            a = float(self.attributes('-alpha'))
        except Exception:
            a = 1.0
        if a < 1.0:
            self.attributes('-alpha', min(1.0, a + 0.12))
            self.after(16, self._fade_in)

    def _st(self, msg, key='dim'):
        self.status.configure(text=msg, text_color=C[key])

    # ---- 任务增删 ----
    def pick_local(self):
        d = filedialog.askdirectory(parent=self, title='选择本地保存文件夹',
                                    initialdir=self.app.cfg.get('last_local', ''))
        if d:
            self._local_dir = d
            self.app.cfg.set('last_local', d)
            self.app.cfg.save()
            self.lbl_local.configure(text='本地: ' + ell(d, 30), text_color=C['text'])

    def add_task(self):
        r = self.r_entry.get().strip()
        if not r:
            messagebox.showinfo('提示', '请填写远程目录', parent=self)
            return
        if not self._local_dir:
            messagebox.showinfo('提示', '请先选择本地保存位置', parent=self)
            return
        for ro, lo, _w in self._subs:
            if ro == r and lo == self._local_dir:
                self._st('该任务已存在', 'warn')
                return
        self._add_sub(r, self._local_dir)
        self.r_entry.delete(0, 'end')
        self._st(f'已添加任务: {r} → {ell(self._local_dir, 24)}', 'ok')

    def _add_sub(self, r, l):
        idx = len(self._subs)
        f = ctk.CTkFrame(self.list_area, fg_color=C['panel2'], corner_radius=10)
        f.pack(fill='x', padx=6, pady=3)
        f.grid_columnconfigure(1, weight=1)
        title = ctk.CTkLabel(f, text=f'{r}  →  {l}', font=FONT, anchor='w', text_color=C['text'])
        title.grid(row=0, column=0, columnspan=3, sticky='w', padx=(12, 4), pady=(8, 0))
        chip = ctk.CTkLabel(f, text='等待开始', width=76, anchor='w', font=FONT_SMALL,
                            text_color=C['dim'])
        chip.grid(row=1, column=0, sticky='w', padx=(12, 6), pady=(0, 8))
        bar = ctk.CTkProgressBar(f, height=6, corner_radius=3, progress_color=C['accent'])
        bar.set(0)
        bar.grid(row=1, column=1, sticky='ew', padx=4, pady=(0, 8))
        pct = ctk.CTkLabel(f, text='0%', width=44, anchor='e', font=FONT_SMALL)
        pct.grid(row=1, column=2, padx=4, pady=(0, 8))
        info = ctk.CTkLabel(f, text='', width=190, anchor='e', font=FONT_SMALL,
                            text_color=C['dim'])
        info.grid(row=1, column=3, padx=(4, 8), pady=(0, 8))
        xbtn = ctk.CTkButton(f, text='✕', width=28, height=24, corner_radius=7,
                             font=FONT_SMALL, fg_color=C['panel3'], hover_color=C['err'],
                             command=lambda i=idx: self._remove_sub(i))
        xbtn.grid(row=0, column=3, padx=(4, 8), pady=(6, 0))
        self._subs.append((r, l, {'f': f, 'chip': chip, 'bar': bar, 'pct': pct,
                                  'info': info, 'xbtn': xbtn}))
        self._sub_ids.append(None)
        self._update_count()

    def _remove_sub(self, idx):
        if self._running:
            return
        _r, _l, w = self._subs.pop(idx)
        self._sub_ids.pop(idx)
        w['f'].destroy()
        self._update_count()

    def _update_count(self):
        self.lbl_count.configure(text=f'任务清单 ({len(self._subs)})')

    def clear_subs(self):
        if self._running:
            messagebox.showinfo('提示', '下载进行中, 不能清空列表', parent=self)
            return
        for _r, _l, w in self._subs:
            w['f'].destroy()
        self._subs.clear()
        self._sub_ids.clear()
        self._update_count()
        self.lbl_overall.configure(text='0/0')
        self.overall.set(0)
        self._st('已清空任务列表', 'dim')

    # ---- 保存 / 载入清单 ----
    def save_tasks(self):
        if not self._subs:
            messagebox.showinfo('提示', '没有任务可保存', parent=self)
            return
        p = filedialog.asksaveasfilename(parent=self, title='保存任务清单',
                                         defaultextension='.json',
                                         initialfile='astrostation_batch.json')
        if not p:
            return
        data = [{'remote': r, 'local': l} for r, l, _w in self._subs]
        try:
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._st(f'已保存 {len(data)} 个任务', 'ok')
        except Exception as e:
            messagebox.showerror('保存失败', str(e), parent=self)

    def load_tasks(self):
        p = filedialog.askopenfilename(parent=self, title='载入任务清单',
                                       filetypes=[('JSON 清单', '*.json')])
        if not p:
            return
        try:
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
            n = 0
            for item in data:
                r = str(item.get('remote', '')).strip()
                l = str(item.get('local', '')).strip()
                if r and l:
                    dup = any(ro == r and lo == l for ro, lo, _w in self._subs)
                    if not dup:
                        self._add_sub(r, l)
                        n += 1
            self._st(f'已载入 {n} 个任务(重复项已跳过)', 'ok')
        except Exception as e:
            messagebox.showerror('载入失败', str(e), parent=self)

    # ---- 启动 ----
    def start(self):
        if not self._subs:
            messagebox.showinfo('提示', '请先添加任务', parent=self)
            return
        if self._running:
            return
        if not self.app.svc.connected:
            if not self.app._require_conn(True):
                return
        n = 0
        for i, (r, l, _w) in enumerate(self._subs):
            t = self.app.queue.submit(r, l, kind='batch', title=r)
            self._sub_ids[i] = t.id
            n += 1
        self._running = True
        self.btn_start.configure(state='disabled', text='后台下载中…')
        self.app._dock_expand()
        self._st(f'已提交 {n} 个任务到后台队列', 'ok')
        self._update_overall()

    def _my_tasks(self):
        ids = set(tid for tid in self._sub_ids if tid)
        return [t for t in self.app.queue.snapshot() if t.id in ids]

    def _update_overall(self):
        ts = self._my_tasks()
        if not ts:
            return
        done = sum(1 for t in ts if t.terminal)
        p = sum(t.progress() for t in ts) / len(ts)
        self.app._anim(self.overall, p)
        self.lbl_overall.configure(text=f'{done}/{len(ts)}')
        if done == len(ts):
            ok = sum(1 for t in ts if t.state == 'done')
            er = sum(1 for t in ts if t.state == 'error')
            cc = sum(1 for t in ts if t.state == 'cancelled')
            msg = f'全部完成: 成功 {ok}'
            if er:
                msg += f', 失败 {er}'
            if cc:
                msg += f', 取消 {cc}'
            self._st(msg, 'err' if er else 'ok')
            if self._running:
                self._running = False
                self.btn_start.configure(state='normal', text='▶ 开始批量下载')

    def _on_task_change(self, task):
        for i, tid in enumerate(self._sub_ids):
            if tid == task.id:
                w = self._subs[i][2]
                text, key = task_chip(task)
                w['chip'].configure(text=text, text_color=C[key])
                w['pct'].configure(text='' if task.state == 'pending'
                                   else f'{int(task.progress() * 100)}%')
                w['info'].configure(text=task_info(task))
                self.app._anim(w['bar'], task.progress())
                if task.terminal:
                    w['xbtn'].configure(state='disabled')
                break
        self._update_overall()

    def _on_close(self):
        self.app.remove_task_listener(self._on_task_change)
        self.destroy()

# ---------------- 连接设置窗口 ----------------
class SettingsWindow(ctk.CTkToplevel):
    """连接设置: 盒子地址 / 账号 / 密码 / 共享名 (默认 = 图谱盒子出厂值)。"""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title('连接设置')
        self.resizable(False, False)
        try:
            self.transient(app)
        except Exception:
            pass
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self._build_ui()
        w, h = 470, 372
        try:
            x = app.winfo_x() + max(0, (app.winfo_width() - w) // 2)
            y = app.winfo_y() + max(0, (app.winfo_height() - h) // 3)
            self.geometry(f'{w}x{h}+{x}+{y}')
        except Exception:
            self.geometry(f'{w}x{h}')

    def _row(self, box, label, value, show=None, ph=''):
        f = ctk.CTkFrame(box, fg_color='transparent')
        f.pack(fill='x', padx=14, pady=(9, 0))
        ctk.CTkLabel(f, text=label, width=76, anchor='w', font=FONT,
                     text_color=C['dim']).pack(side='left')
        e = ctk.CTkEntry(f, font=FONT, height=30, placeholder_text=ph)
        if show:
            e.configure(show=show)
        e.pack(side='left', fill='x', expand=True)
        if value:
            e.insert(0, value)
        return e

    def _build_ui(self):
        ctk.CTkLabel(self, text='连接设置', font=FONT_TITLE, text_color=C['text']).pack(pady=(16, 0))
        ctk.CTkLabel(self, text='默认 = 图谱盒子出厂设置; 地址留空 = 每次自动发现',
                     font=FONT_SMALL, text_color=C['dim']).pack(pady=(2, 4))
        box = ctk.CTkFrame(self, fg_color=C['panel2'], corner_radius=12)
        box.pack(fill='x', padx=18)
        cur_host = '' if (not self.app.host or self.app.host == HOST) else self.app.host
        self.e_host = self._row(box, '盒子地址', cur_host, ph='留空 = 自动发现 (网线直连 / 热点)')
        self.e_user = self._row(box, '账号', CREDS['user'])
        self.e_pass = self._row(box, '密码', CREDS['pass'], show='•')
        self.e_share = self._row(box, '共享名', CREDS['share'])
        ctk.CTkLabel(box, text='', height=4, font=FONT_SMALL).pack()
        ctk.CTkLabel(self, text='提示: 直连时盒子地址是随机的 169.254.x.x, 一般留空即可; 设置会保存。',
                     font=FONT_SMALL, text_color=C['faint']).pack(anchor='w', padx=24, pady=(8, 0))
        btns = ctk.CTkFrame(self, fg_color='transparent')
        btns.pack(fill='x', padx=18, pady=(12, 14), side='bottom')
        ctk.CTkButton(btns, text='恢复默认', width=92, height=32, font=FONT,
                      fg_color=C['panel3'], hover_color=C['panel2'], text_color=C['text'],
                      command=self._reset_defaults).pack(side='left')
        ctk.CTkButton(btns, text='保存', width=112, height=32, font=FONT,
                      fg_color=C['accent'], hover_color=C['accent_h'],
                      command=self._save).pack(side='right')
        ctk.CTkButton(btns, text='取消', width=80, height=32, font=FONT,
                      fg_color=C['panel3'], hover_color=C['panel2'], text_color=C['text'],
                      command=self.destroy).pack(side='right', padx=(0, 8))

    def _reset_defaults(self):
        self.e_host.delete(0, 'end')
        for e, v in ((self.e_user, USER), (self.e_pass, PASS), (self.e_share, SHARE)):
            e.delete(0, 'end')
            e.insert(0, v)

    def _save(self):
        host = self.e_host.get().strip()
        user = self.e_user.get().strip()
        pwd = self.e_pass.get()
        share = self.e_share.get().strip()
        if not user or not share:
            messagebox.showwarning('提示', '账号和共享名不能为空', parent=self)
            return
        apply_settings(self.app.cfg, host=host, user=user, password=pwd, share=share)
        self.app.host = host or HOST
        self.app.svc.close()
        self.app._dot_pulse(False)
        self.app.dot.configure(text_color=C['faint'])
        self.app.btn_conn.configure(text='重连')
        self.app._st('设置已保存, 正在重新连接…', 'dim')
        self.destroy()
        self.app.connect_async()

# ---------------- 主窗口 ----------------
class BoxerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('AstroStation 天文盒子文件浏览器')
        self.geometry('1140x740')
        self.minsize(980, 620)
        self.cfg = AppConfig()
        _load_creds(self.cfg)
        g = self.cfg.get('geometry')
        if g:
            try:
                self.geometry(g)
            except Exception:
                pass

        self.host = self.cfg.get('last_host', '') or HOST
        self._disc_logs = []
        self.svc = SMBService(host=self.host)
        self.queue = DownloadQueue(on_change=self._on_task_change, host_getter=lambda: self.host)
        try:
            self.parallel_now = int(self.cfg.get('parallel', PARALLEL_DEFAULT))
        except Exception:
            self.parallel_now = PARALLEL_DEFAULT
        self.queue.set_parallel(self.parallel_now)
        self.cwd = ''
        self._entries = []
        self._iid_entry = {}
        self._sort_key = 'name'
        self._sort_desc = False
        self._token = 0
        self._busy = False
        self._connecting = False
        self._hist = []
        self._hpos = -1
        self._dock_rows = {}
        self._dock_open = False
        self._task_listeners = []
        self._was_queue_busy = False
        self._anim_targets = {}
        self._anim_pending = set()
        self._pulse_i = 0
        self._dot_pulsing = False
        self._spin_i = 0
        self._spinner_on = False

        self._build_styles()
        self._build_ui()
        self._update_nav_state()
        self._set_busy(True, '正在搜索盒子…')
        self._dot_pulse(True)
        self.after(200, self.connect_async)
        self.protocol('WM_DELETE_WINDOW', self._on_close)

    # ---- 样式 ----
    def _build_styles(self):
        st = ttk.Style(self)
        st.theme_use('clam')
        st.configure('Boxer.Treeview', background=C['panel'], fieldbackground=C['panel'],
                     foreground=C['text'], rowheight=32, borderwidth=0, font=FONT)
        st.configure('Boxer.Treeview.Heading', background=C['panel2'], foreground=C['dim'],
                     borderwidth=0, relief='flat', font=FONT_HEAD, padding=(10, 7))
        st.map('Boxer.Treeview', background=[('selected', C['sel'])],
               foreground=[('selected', '#ffffff')])
        st.map('Boxer.Treeview.Heading', background=[('active', C['panel3'])])
        st.configure('Side.Treeview', background=C['panel2'], fieldbackground=C['panel2'],
                     foreground=C['text'], rowheight=26, borderwidth=0, font=FONT_SMALL)
        st.map('Side.Treeview', background=[('selected', C['sel'])],
               foreground=[('selected', '#ffffff')])

    def _nbtn(self, parent, text, w, cmd=None):
        return ctk.CTkButton(parent, text=text, width=w, height=30, corner_radius=8,
                             font=FONT_SMALL, fg_color=C['panel2'], hover_color=C['panel3'],
                             text_color=C['text'], command=cmd)

    # ---- 布局 ----
    def _build_ui(self):
        # 顶栏
        head = ctk.CTkFrame(self, fg_color=C['panel'], corner_radius=14)
        head.pack(fill='x', padx=14, pady=(12, 6))
        self.btn_conn = self._nbtn(head, '重连', 64, self.toggle_conn)
        self.btn_conn.pack(side='right', padx=(0, 14))
        self.btn_host = self._nbtn(head, '地址', 56, self.set_host_manual)
        self.btn_host.pack(side='right', padx=(0, 4))
        self.btn_set = self._nbtn(head, '设置', 56, self.open_settings)
        self.btn_set.pack(side='right', padx=(0, 4))
        self.dot = ctk.CTkLabel(head, text='●', font=('Segoe UI', 18), text_color=C['faint'])
        self.dot.pack(side='left', padx=(14, 6))
        ttl = ctk.CTkFrame(head, fg_color='transparent')
        ttl.pack(side='left')
        ctk.CTkLabel(ttl, text='AstroStation 天文盒子', font=FONT_TITLE,
                     text_color=C['text']).pack(anchor='w')
        self.lbl_sub = ctk.CTkLabel(ttl, text=f'共享 {CREDS["share"]} · 账号 {CREDS["user"]} · 自动发现',
                                    font=FONT_SMALL, text_color=C['dim'])
        self.lbl_sub.pack(anchor='w')
        btns = ctk.CTkFrame(head, fg_color='transparent')
        btns.pack(side='right', padx=8)
        self.btn_batch = ctk.CTkButton(btns, text='⚡ 批量下载', width=104, height=30,
                                       corner_radius=8, font=FONT_SMALL,
                                       fg_color=C['violet'], hover_color=C['violet_h'],
                                       command=self.open_batch)
        self.btn_batch.pack(side='right', padx=3)
        self.btn_dl = ctk.CTkButton(btns, text='⬇ 下载选中', width=104, height=30,
                                    corner_radius=8, font=FONT_SMALL,
                                    fg_color=C['ok'], hover_color=C['ok_h'],
                                    command=self.download_selected)
        self.btn_dl.pack(side='right', padx=3)
        self.btn_fwd = self._nbtn(btns, '▶', 36, self.go_forward)
        self.btn_fwd.pack(side='right', padx=3)
        self.btn_back = self._nbtn(btns, '◀', 36, self.go_back)
        self.btn_back.pack(side='right', padx=3)
        self.btn_up = self._nbtn(btns, '⬆ 上级', 76, self.go_up)
        self.btn_up.pack(side='right', padx=3)
        self.btn_refresh = self._nbtn(btns, '⟳ 刷新', 76, self.refresh)
        self.btn_refresh.pack(side='right', padx=3)

        # 面包屑 + 路径输入
        nav = ctk.CTkFrame(self, fg_color=C['panel'], corner_radius=14)
        nav.pack(fill='x', padx=14, pady=(0, 8))
        self.pentry = ctk.CTkEntry(nav, width=300, height=30, font=FONT_SMALL,
                                   placeholder_text='输入远程路径, 回车跳转 (如 sequence/Light)')
        self.pentry.pack(side='right', padx=(8, 10), pady=8)
        self.pentry.bind('<Return>', lambda e: self._jump_to_entry())
        self.crumb = ctk.CTkFrame(nav, fg_color='transparent')
        self.crumb.pack(side='left', fill='x', expand=True, padx=10, pady=8)

        # 主区域: 左侧目录树 + 右侧文件列表
        mid = ctk.CTkFrame(self, fg_color='transparent')
        mid.pack(fill='both', expand=True, padx=14)
        side = ctk.CTkFrame(mid, width=252, fg_color=C['panel'], corner_radius=14)
        side.pack(side='left', fill='y', padx=(0, 8))
        side.pack_propagate(False)
        hd = ctk.CTkFrame(side, fg_color='transparent')
        hd.pack(fill='x', padx=12, pady=(10, 4))
        ctk.CTkLabel(hd, text='📂 目录树', font=FONT_BOLD, text_color=C['text']).pack(side='left')
        ctk.CTkLabel(hd, text='单击进入', font=FONT_SMALL, text_color=C['faint']).pack(side='right')
        tw = ctk.CTkFrame(side, fg_color=C['panel2'], corner_radius=10)
        tw.pack(fill='both', expand=True, padx=10)
        self.side_tree = ttk.Treeview(tw, show='tree', style='Side.Treeview',
                                      selectmode='browse')
        self.side_tree.pack(side='left', fill='both', expand=True, padx=(2, 0), pady=2)
        ssb = ctk.CTkScrollbar(tw, command=self.side_tree.yview)
        self.side_tree.configure(yscrollcommand=ssb.set)
        ssb.pack(side='right', fill='y', pady=2)
        self.side_tree.insert('', 'end', iid='/', text='🏠 根目录 /')
        self.side_tree.insert('/', 'end', iid='/__loading', text='')
        self.side_tree.bind('<<TreeviewSelect>>', self._on_tree_select)
        self.side_tree.bind('<<TreeviewOpen>>', self._on_tree_open)
        self.side_tree.bind('<Return>', self._on_tree_enter)
        inf = ctk.CTkFrame(side, fg_color=C['panel2'], corner_radius=10)
        inf.pack(fill='x', padx=10, pady=10)
        ctk.CTkLabel(inf, text='连接信息', font=FONT_BOLD, text_color=C['dim']).pack(
            anchor='w', padx=12, pady=(8, 2))
        self.lbl_conninfo = ctk.CTkLabel(inf, text=f'盒子  未连接\n账号  {CREDS["user"]}\n共享  {CREDS["share"]}',
                                         font=FONT_SMALL, text_color=C['text'], justify='left')
        self.lbl_conninfo.pack(anchor='w', padx=12, pady=(0, 8))

        lw = ctk.CTkFrame(mid, fg_color=C['panel'], corner_radius=14)
        lw.pack(side='left', fill='both', expand=True)
        self.tree = ttk.Treeview(lw, columns=('name', 'size', 'type', 'mtime'),
                                 show='headings', style='Boxer.Treeview',
                                 selectmode='extended')
        heads = [('name', '名称'), ('size', '大小'), ('type', '类型'), ('mtime', '修改时间')]
        widths = {'name': 430, 'size': 110, 'type': 140, 'mtime': 150}
        for k, txt in heads:
            self.tree.heading(k, text=txt, command=lambda kk=k: self._sort_by(kk))
            self.tree.column(k, width=widths[k], anchor='w' if k in ('name', 'type') else 'e')
        lsb = ctk.CTkScrollbar(lw, command=self.tree.yview)
        self.tree.configure(yscrollcommand=lsb.set)
        self.tree.pack(side='left', fill='both', expand=True, padx=(8, 0), pady=8)
        lsb.pack(side='right', fill='y', pady=8)
        self.tree.tag_configure('dir', foreground=C['folder'])
        self.tree.tag_configure('fits', foreground=C['fits'])
        self.tree.tag_configure('file', foreground=C['file'])
        self.tree.bind('<Double-1>', self._on_double)
        self.tree.bind('<Return>', self._on_enter)
        self.tree.bind('<BackSpace>', lambda e: self.go_up())
        self.tree.bind('<F5>', lambda e: self.refresh())
        self.tree.bind('<Control-d>', lambda e: self.download_selected())
        self.tree.bind('<Control-b>', lambda e: self.open_batch())
        self.tree.bind('<Button-3>', self._on_right_click)

        # 右键菜单
        self.menu = tk.Menu(self, tearoff=0, bg=C['panel2'], fg=C['text'],
                            activebackground=C['sel'], activeforeground='#ffffff',
                            font=FONT_SMALL)
        self.menu.add_command(label='打开 / 进入', command=self._menu_enter)
        self.menu.add_command(label='下载到本地…', command=self.download_selected)
        self.menu.add_command(label='复制远程路径', command=self._menu_copy)
        self.menu.add_separator()
        self.menu.add_command(label='刷新', command=self.refresh)

        # 底部下载队列面板(可收起)
        self._dock = ctk.CTkFrame(self, fg_color=C['panel'], corner_radius=14)
        self._dock.pack(fill='x', padx=14, pady=(0, 8))
        dh = ctk.CTkFrame(self._dock, fg_color='transparent')
        dh.pack(fill='x', padx=10, pady=(6, 4))
        self._dock_toggle = ctk.CTkButton(dh, text='⬇ 下载队列  ▸', width=160, height=26,
                                          fg_color='transparent', hover_color=C['panel3'],
                                          text_color=C['text'], font=FONT_BOLD, anchor='w',
                                          command=self._toggle_dock)
        self._dock_toggle.pack(side='left')
        self._dock_count = ctk.CTkLabel(dh, text='', font=FONT_SMALL, text_color=C['dim'])
        self._dock_count.pack(side='left', padx=6)
        self.btn_clear_done = ctk.CTkButton(dh, text='清空已完成', width=90, height=26,
                                            corner_radius=7, font=FONT_SMALL,
                                            fg_color=C['panel2'], hover_color=C['panel3'],
                                            command=self._clear_done)
        self.btn_clear_done.pack(side='right', padx=6)
        self.par_menu = ctk.CTkOptionMenu(dh, values=['2', '4', '6', '8', '12'], width=76, height=26,
                                          corner_radius=7, font=FONT_SMALL,
                                          fg_color=C['panel2'], button_color=C['panel3'],
                                          button_hover_color=C['accent_h'], text_color=C['text'],
                                          command=self._on_parallel)
        self.par_menu.set(str(self.parallel_now))
        self.par_menu.pack(side='right', padx=6)
        ctk.CTkLabel(dh, text='并行连接', font=FONT_SMALL, text_color=C['dim']).pack(side='right')
        self._dock_body = ctk.CTkScrollableFrame(self._dock, fg_color='transparent', height=152)

        # 状态栏
        sbar = ctk.CTkFrame(self, fg_color=C['panel'], corner_radius=14)
        sbar.pack(fill='x', padx=14, pady=(0, 12))
        self.spin = ctk.CTkLabel(sbar, text='', font=('Consolas', 12), width=16,
                                 text_color=C['warn'])
        self.spin.pack(side='left', padx=(14, 2))
        self.status_lbl = ctk.CTkLabel(sbar, text='就绪', anchor='w', font=FONT,
                                       text_color=C['text'])
        self.status_lbl.pack(side='left', fill='x', expand=True, padx=4)
        self.overall = ctk.CTkProgressBar(sbar, width=230, height=8, corner_radius=4,
                                          progress_color=C['accent'])
        self.overall.set(0)
        self.overall.pack(side='right', padx=(6, 14))
        self.queue_lbl = ctk.CTkLabel(sbar, text='队列空闲', font=FONT_SMALL,
                                      text_color=C['dim'])
        self.queue_lbl.pack(side='right', padx=4)

    # ---- 状态显示/动效 ----
    def _st(self, msg, key='ok'):
        self.status_lbl.configure(text=msg, text_color=C[key])

    def _set_busy(self, busy, msg=None):
        self._busy = busy
        if busy:
            if not self._spinner_on:
                self._spinner_on = True
                self._spin_i = 0
                self._spinner_tick()
        else:
            self._spinner_on = False
            self.spin.configure(text='')
        if msg:
            self._st(msg, 'warn' if busy else 'ok')

    def _spinner_tick(self):
        if not self._spinner_on:
            self.spin.configure(text='')
            return
        self.spin.configure(text=SPINNER[self._spin_i % len(SPINNER)])
        self._spin_i += 1
        self.after(90, self._spinner_tick)

    def _dot_pulse(self, on):
        self._dot_pulsing = on
        if on:
            self._pulse_i = 0
            self._pulse_tick()
        else:
            self.dot.configure(text_color=C['ok'] if self.svc.connected else C['faint'])

    def _pulse_tick(self):
        if not self._dot_pulsing:
            return
        self.dot.configure(text_color=C['warn'] if self._pulse_i % 2 == 0 else '#6e5a20')
        self._pulse_i += 1
        self.after(380, self._pulse_tick)

    def _anim(self, pb, target):
        """平滑动画推进进度条。"""
        self._anim_targets[pb] = float(target)
        if pb in self._anim_pending:
            return
        self._anim_pending.add(pb)

        def tick():
            if pb not in self._anim_targets:
                self._anim_pending.discard(pb)
                return
            t = self._anim_targets[pb]
            try:
                cur = pb.get()
                exists = bool(pb.winfo_exists())
            except Exception:
                exists = False
            if not exists or abs(cur - t) < 0.003:
                try:
                    pb.set(t)
                except Exception:
                    pass
                self._anim_pending.discard(pb)
                self._anim_targets.pop(pb, None)
                return
            pb.set(cur + (t - cur) * 0.28)
            self.after(26, tick)

        tick()

    # ---- 连接 ----
    def connect_async(self):
        if self._connecting or self.svc.connected:
            return
        self._connecting = True
        self._set_busy(True, '正在搜索盒子 (网线直连 / WiFi)…')
        self._dot_pulse(True)
        self.btn_conn.configure(text='连接中…')
        self._disc_logs = []
        threading.Thread(target=self._connect, daemon=True).start()

    def _connect(self):
        try:
            ip = discover_host(self.cfg, log=lambda m: self._disc_logs.append(m))
            if not ip:
                raise RuntimeError('未发现盒子 (直连网线 / stellavita 热点 均已尝试)')
            self.host = ip
            self.svc.host = ip
            self.svc.close()
            self.svc.connect()
            self.cfg.set('last_host', ip)
            self.cfg.save()
            self.after(0, self._on_connected)
        except Exception as e:
            self.after(0, self._on_connect_fail, e)

    def _on_connected(self):
        self._connecting = False
        self._dot_pulse(False)
        self._set_busy(False)
        self.dot.configure(text_color=C['ok'])
        self.btn_conn.configure(text='断开')
        self._st(f'已连接 AstroStation ({self.host})', 'ok')
        self.lbl_sub.configure(text=f'{self.host} · 共享 {CREDS["share"]} · 账号 {CREDS["user"]}')
        self.lbl_conninfo.configure(text=f'盒子  {self.host}\n账号  {CREDS["user"]}\n共享  {CREDS["share"]}')
        self._update_nav_state()
        self.navigate('', record=False)
        self._load_tree_children('/')

    def _on_connect_fail(self, e):
        self._connecting = False
        self._dot_pulse(False)
        self.dot.configure(text_color=C['err'])
        self.btn_conn.configure(text='重连')
        self._set_busy(False)
        self._st(f'连接失败: {_short(e)}', 'err')
        logs = '\n'.join(self._disc_logs[-5:])
        messagebox.showerror('连接失败',
                             f'未找到可用的盒子连接\n{e}\n\n'
                             '请确认: ① 网线直连时盒子已开机 或 ② 已连接 stellavita 热点\n'
                             '也可点「地址」手动指定；需要的话把此消息发我排查。'
                             + (f'\n\n搜索记录:\n{logs}' if logs else ''),
                             parent=self)

    def open_settings(self):
        SettingsWindow(self)

    def set_host_manual(self):
        from tkinter import simpledialog
        ip = simpledialog.askstring('手动指定盒子地址', '输入盒子 IP (直连通常是 169.254.x.x):',
                                    initialvalue=self.host or HOST, parent=self)
        if not ip:
            return
        ip = ip.strip()
        self.host = ip
        self.svc.host = ip
        self.cfg.set('last_host', ip)
        self.cfg.save()
        self._st(f'已记录地址 {ip}', 'dim')
        if self.svc.connected:
            self.svc.close()
            self._dot_pulse(False)
            self.dot.configure(text_color=C['faint'])
            self.btn_conn.configure(text='重连')
        self.connect_async()

    def toggle_conn(self):
        if self.svc.connected:
            self.svc.close()
            self.dot.configure(text_color=C['faint'])
            self.btn_conn.configure(text='重连')
            self._st('已断开连接', 'dim')
            self.lbl_sub.configure(text=f'共享 {CREDS["share"]} · 账号 {CREDS["user"]} · 自动发现')
            self.lbl_conninfo.configure(text=f'盒子  未连接\n账号  {CREDS["user"]}\n共享  {CREDS["share"]}')
            self._update_nav_state()
            for k in self.side_tree.get_children('/'):
                self.side_tree.delete(k)
            self.side_tree.insert('/', 'end', iid='/__loading', text='')
        else:
            self.connect_async()

    def _update_nav_state(self):
        ok = self.svc.connected
        for b in (self.btn_refresh, self.btn_up, self.btn_dl, self.btn_batch):
            b.configure(state='normal' if ok else 'disabled')
        self.btn_back.configure(state='normal' if (ok and self._hpos > 0) else 'disabled')
        self.btn_fwd.configure(state='normal' if (ok and self._hpos < len(self._hist) - 1) else 'disabled')

    def _require_conn(self, interactive=True):
        if self.svc.connected:
            return True
        if interactive and not self._connecting:
            if messagebox.askyesno('未连接', '尚未连接盒子, 现在连接吗？', parent=self):
                self.connect_async()
        return False

    # ---- 浏览 ----
    def navigate(self, path, record=True, force=False):
        path = norm_path(path)
        if not self.svc.connected:
            self._set_busy(False)
            if not self._connecting:
                self._st('尚未连接盒子', 'err')
                self._require_conn(True)
            return
        if not force and self._entries and path == self.cwd:
            self._update_nav_state()
            return
        if record:
            if self._hpos < len(self._hist) - 1:
                self._hist = self._hist[:self._hpos + 1]
            self._hist.append(path)
            self._hpos = len(self._hist) - 1
            if len(self._hist) > 200:
                self._hist = self._hist[-200:]
                self._hpos = len(self._hist) - 1
        self._update_nav_state()
        tok = self._token = self._token + 1
        self._set_busy(True, '正在读取目录…')

        def job():
            try:
                entries = self.svc.list_dir(path)
                err = None
            except Exception as e:
                entries = None
                err = e
            self.after(0, self._render_dir, tok, path, entries, err)

        threading.Thread(target=job, daemon=True).start()

    def refresh(self):
        if self.svc.connected:
            self.navigate(self.cwd, record=False, force=True)

    def _render_dir(self, tok, path, entries, err):
        if tok != self._token:
            return  # 过期请求, 丢弃
        self._set_busy(False)
        if err is not None:
            self._st(f'读取目录失败: {_short(err)}', 'err')
            return
        self.cwd = path
        self._entries = entries
        total_b = self._render_rows()
        self._render_breadcrumb()
        self.pentry.delete(0, 'end')
        self.pentry.insert(0, path or '/')
        self._st(f'{len(entries)} 项 · 合计 {size_str(total_b)} · 双击进入文件夹', 'ok')
        self._sync_tree_to(path)

    def _render_rows(self):
        t = self.tree
        t.delete(*t.get_children())
        self._iid_entry.clear()
        es = list(self._entries)
        if self._sort_key == 'name':
            keyf = lambda e: e.name.lower()
        elif self._sort_key == 'size':
            keyf = lambda e: e.size
        elif self._sort_key == 'mtime':
            keyf = lambda e: e.mtime
        else:
            keyf = lambda e: e.type_str
        es.sort(key=keyf, reverse=self._sort_desc)
        es.sort(key=lambda e: e.is_dir, reverse=True)  # 稳定排序: 文件夹永远在前
        total_b = sum(e.size for e in es if not e.is_dir)
        for e in es:
            tag = 'dir' if e.is_dir else ('fits' if e.is_fits else 'file')
            iid = t.insert('', 'end',
                           values=(f'{e.icon} {e.name}',
                                   '—' if e.is_dir else size_str(e.size),
                                   e.type_str, fmt_time(e.mtime)),
                           tags=(tag,))
            self._iid_entry[iid] = e
        self._entries = es
        self._update_headings()
        return total_b

    def _update_headings(self):
        base = {'name': '名称', 'size': '大小', 'type': '类型', 'mtime': '修改时间'}
        for k, txt in base.items():
            mark = (' ▼' if self._sort_desc else ' ▲') if k == self._sort_key else ''
            self.tree.heading(k, text=txt + mark)

    def _sort_by(self, key):
        if self._sort_key == key:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key, self._sort_desc = key, False
        self._render_rows()

    def go_up(self):
        if self.svc.connected:
            self.navigate(parent_path(self.cwd))

    def go_back(self):
        if self._hpos > 0:
            self._hpos -= 1
            self.navigate(self._hist[self._hpos], record=False, force=True)

    def go_forward(self):
        if self._hpos < len(self._hist) - 1:
            self._hpos += 1
            self.navigate(self._hist[self._hpos], record=False, force=True)

    def _jump_to_entry(self):
        p = self.pentry.get().strip()
        if not p:
            p = ''
        self.navigate(p)

    def _render_breadcrumb(self):
        for w in self.crumb.winfo_children():
            w.destroy()
        segs = [s for s in self.cwd.split('/') if s]
        ctk.CTkButton(self.crumb, text='⌂', width=34, height=26, corner_radius=7,
                      fg_color='transparent', hover_color=C['panel3'],
                      text_color=C['accent'], font=FONT_BOLD,
                      command=lambda: self.navigate('')).pack(side='left', padx=1)
        if not segs:
            return
        cum = []
        cur = ''
        for s in segs:
            cur = join_path(cur, s)
            cum.append(cur)
        shown = segs if len(segs) <= 6 else segs[-6:]
        if len(segs) > 6:
            ctk.CTkLabel(self.crumb, text='›', text_color=C['faint'], font=FONT).pack(side='left', padx=2)
            skip = cum[len(segs) - 6]
            ctk.CTkButton(self.crumb, text='…', width=30, height=26, corner_radius=7,
                          fg_color='transparent', hover_color=C['panel3'],
                          text_color=C['dim'], font=FONT,
                          command=lambda p=parent_path(skip): self.navigate(p)).pack(side='left', padx=1)
        for i, s in enumerate(shown):
            idx = len(segs) - len(shown) + i
            ctk.CTkLabel(self.crumb, text='›', text_color=C['faint'], font=FONT).pack(side='left', padx=2)
            t = ell(s, 18)
            w = min(150, 26 + len(t) * 12)
            ctk.CTkButton(self.crumb, text=t, width=w, height=26, corner_radius=7,
                          fg_color='transparent', hover_color=C['panel3'],
                          text_color=C['folder'], font=FONT,
                          command=lambda p=cum[idx]: self.navigate(p)).pack(side='left', padx=1)

    # ---- 文件列表事件 ----
    def _on_double(self, ev):
        iid = self.tree.identify_row(ev.y)
        if not iid:
            return
        e = self._iid_entry.get(iid)
        if e is not None and e.is_dir:
            self.navigate(join_path(self.cwd, e.name))

    def _on_enter(self, ev):
        sel = self.tree.selection()
        if not sel:
            return
        e = self._iid_entry.get(sel[0])
        if e is not None and e.is_dir:
            self.navigate(join_path(self.cwd, e.name))

    def _on_right_click(self, ev):
        iid = self.tree.identify_row(ev.y)
        if iid:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            try:
                self.menu.tk_popup(ev.x_root, ev.y_root)
            finally:
                self.menu.grab_release()

    def _menu_enter(self):
        sel = self.tree.selection()
        if not sel:
            return
        e = self._iid_entry.get(sel[0])
        if e is not None and e.is_dir:
            self.navigate(join_path(self.cwd, e.name))

    def _menu_copy(self):
        sel = self.tree.selection()
        if not sel:
            return
        e = self._iid_entry.get(sel[0])
        if e is not None:
            self.clipboard_clear()
            self.clipboard_append(join_path(self.cwd, e.name))
            self._st('已复制远程路径', 'ok')

    # ---- 下载 ----
    def download_selected(self):
        if not self._require_conn():
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo('提示', '请先选中要下载的文件或文件夹', parent=self)
            return
        local = filedialog.askdirectory(parent=self, title='选择保存到本地哪个文件夹',
                                        initialdir=self.cfg.get('last_local', ''))
        if not local:
            return
        self.cfg.set('last_local', local)
        self.cfg.save()
        items = []
        for iid in sel:
            e = self._iid_entry.get(iid)
            if e is None:
                continue
            items.append((e, join_path(self.cwd, e.name)))
        if not items:
            return
        for e, rp in items:
            if e.is_dir:
                self.queue.submit(rp, local, kind='dir', title=e.name)
            else:
                self.queue.submit(rp, local, kind='file', size_hint=e.size, title=e.name)
        self._st(f'已加入 {len(items)} 个下载任务', 'ok')
        self._dock_expand()

    def open_batch(self):
        if not self._require_conn():
            return
        BatchDLWindow(self)

    # ---- 目录树 ----
    def _on_tree_select(self, ev):
        iid = self.side_tree.focus()
        if iid and not iid.endswith('__loading'):
            self.navigate('' if iid == '/' else iid)

    def _on_tree_enter(self, ev):
        iid = self.side_tree.focus()
        if iid and iid != '/' and not iid.endswith('__loading'):
            self.navigate(iid)

    def _on_tree_open(self, ev):
        iid = self.side_tree.focus()
        if iid and self.side_tree.exists(iid):
            self._load_tree_children(iid)

    def _load_tree_children(self, node):
        tree = self.side_tree
        rp = '' if node == '/' else node
        kids = tree.get_children(node)
        if kids and kids[0].endswith('__loading'):
            return  # 正在加载
        for k in kids:
            tree.delete(k)
        tree.insert(node, 'end', iid=node + '/__loading', text='⏳')

        def job():
            try:
                entries = self.svc.list_dir(rp)
                err = None
            except Exception as e:
                entries = None
                err = e
            self.after(0, self._tree_load_done, node, entries, err)

        threading.Thread(target=job, daemon=True).start()

    def _tree_load_done(self, node, entries, err):
        tree = self.side_tree
        if not tree.exists(node):
            return
        if tree.exists(node + '/__loading'):
            tree.delete(node + '/__loading')
        if err is not None:
            return
        for e in entries:
            if not e.is_dir:
                continue
            iid = join_path('' if node == '/' else node, e.name)
            if tree.exists(iid):
                continue
            tree.insert(node, 'end', iid=iid, text='📁 ' + e.name)
            tree.insert(iid, 'end', iid=iid + '/__loading', text='')

    def _sync_tree_to(self, path):
        """让目录树展开到当前路径(异步, 不阻塞)。"""

        def job():
            cur = ''
            chain = []
            try:
                for seg in path.split('/'):
                    if not seg:
                        continue
                    cur = join_path(cur, seg)
                    self.svc.list_dir(cur)
                    chain.append(cur)
            except Exception:
                pass
            self.after(0, self._apply_tree_sync, chain)

        threading.Thread(target=job, daemon=True).start()

    def _apply_tree_sync(self, chain):
        tree = self.side_tree
        if not tree.exists('/'):
            return
        parent = '/'
        for p in chain:
            if not tree.exists(p):
                tree.insert(parent, 'end', iid=p, text='📁 ' + basename_path(p))
                tree.insert(p, 'end', iid=p + '/__loading', text='')
            kids = tree.get_children(p)
            if not kids:
                tree.insert(p, 'end', iid=p + '/__loading', text='')
            tree.item(p, open=True)
            self._load_tree_children(p)
            parent = p
        tree.see(parent)

    # ---- 下载队列面板 ----
    def _on_task_change(self, task):
        try:
            self.after(0, lambda t=task: self._task_changed_ui(t))
        except Exception:
            pass  # 应用已关闭

    def _task_changed_ui(self, task):
        self._dock_update(task)
        for fn in list(self._task_listeners):
            try:
                fn(task)
            except Exception:
                pass
        busy = any(t.state in ('pending', 'running') for t in self.queue.snapshot())
        if busy != self._was_queue_busy:
            self._was_queue_busy = busy
            if not busy:
                done = sum(1 for t in self.queue.snapshot() if t.state == 'done')
                err = sum(1 for t in self.queue.snapshot() if t.state == 'error')
                cc = sum(1 for t in self.queue.snapshot() if t.state == 'cancelled')
                msg = f'下载队列空闲 · 成功 {done}'
                if err:
                    msg += f' · 失败 {err}'
                if cc:
                    msg += f' · 取消 {cc}'
                self._st(msg, 'err' if err else 'ok')
        self._status_overall()

    def _dock_expand(self):
        if not self._dock_open:
            self._dock_open = True
            self._dock_body.pack(fill='x', padx=10, pady=(0, 8))
            self._dock_toggle.configure(text='⬇ 下载队列  ▾')

    def _toggle_dock(self):
        if self._dock_open:
            self._dock_body.pack_forget()
            self._dock_open = False
            self._dock_toggle.configure(text='⬇ 下载队列  ▸')
        else:
            self._dock_expand()

    def _dock_update(self, task):
        row = self._dock_rows.get(task.id)
        if row is None:
            while len(self._dock_rows) >= 14:
                for k in list(self._dock_rows.keys()):
                    r = self._dock_rows[k]
                    if r['task'].terminal:
                        r['f'].destroy()
                        del self._dock_rows[k]
                        break
                else:
                    break
            f = ctk.CTkFrame(self._dock_body, fg_color=C['panel2'], corner_radius=10)
            f.pack(fill='x', padx=4, pady=3)
            name = ctk.CTkLabel(f, text=ell(task.title, 26), width=230, anchor='w',
                                font=FONT, text_color=C['text'])
            name.pack(side='left', padx=(10, 6), pady=6)
            chip = ctk.CTkLabel(f, text='', width=82, anchor='w', font=FONT_SMALL)
            chip.pack(side='left', padx=2)
            bar = ctk.CTkProgressBar(f, height=8, corner_radius=4, progress_color=C['accent'])
            bar.set(0)
            bar.pack(side='left', fill='x', expand=True, padx=6)
            pct = ctk.CTkLabel(f, text='', width=44, anchor='e', font=FONT_SMALL)
            pct.pack(side='left')
            info = ctk.CTkLabel(f, text='', width=200, anchor='e', font=FONT_SMALL,
                                text_color=C['dim'])
            info.pack(side='left', padx=6)
            btn = ctk.CTkButton(f, text='✕', width=30, height=26, corner_radius=7,
                                font=FONT_SMALL, fg_color=C['panel3'], hover_color=C['err'],
                                command=lambda t=task: self._dock_btn(t))
            btn.pack(side='left', padx=(0, 8))
            row = {'task': task, 'f': f, 'chip': chip, 'bar': bar, 'pct': pct,
                   'info': info, 'btn': btn}
            self._dock_rows[task.id] = row
        text, key = task_chip(task)
        row['chip'].configure(text=text, text_color=C[key])
        row['pct'].configure(text='' if task.state == 'pending'
                             else f'{int(task.progress() * 100)}%')
        row['info'].configure(text=task_info(task))
        self._anim(row['bar'], task.progress())
        if task.state == 'error':
            row['btn'].configure(text='↻')
        elif task.state in ('done', 'cancelled'):
            row['btn'].configure(text='·', state='disabled')
        else:
            row['btn'].configure(text='✕', state='normal')

    def _dock_btn(self, task):
        if task.state in ('pending', 'running'):
            self.queue.cancel(task)
        elif task.state == 'error':
            self.queue.retry(task)

    def _dock_header_update(self):
        snap = self.queue.snapshot()
        act = sum(1 for t in snap if t.state in ('pending', 'running'))
        done = sum(1 for t in snap if t.state == 'done')
        err = sum(1 for t in snap if t.state == 'error')
        txt = f'进行中 {act} · 完成 {done}'
        if err:
            txt += f' · 失败 {err}'
        self._dock_count.configure(text=txt)
        self.btn_clear_done.configure(state='normal' if (done + err) else 'disabled')

    def _on_parallel(self, v):
        try:
            n = int(v)
        except Exception:
            return
        self.parallel_now = n
        self.queue.set_parallel(n)
        self.cfg.set('parallel', n)
        self.cfg.save()
        self._st(f'并行连接数已设为 {n}', 'dim')

    def _clear_done(self):
        snap = self.queue.snapshot()
        for t in snap:
            if t.terminal:
                row = self._dock_rows.pop(t.id, None)
                if row:
                    row['f'].destroy()
        self.queue.prune_finished()
        self._dock_header_update()
        self._status_overall()

    def _status_overall(self):
        snap = self.queue.snapshot()
        act = [t for t in snap if t.state in ('pending', 'running')]
        if act:
            p = sum(t.progress() for t in act) / len(act)
            self._anim(self.overall, p)
            self.queue_lbl.configure(text=f'任务 {len(act)} 个进行中', text_color=C['accent'])
        else:
            self._anim(self.overall, 0.0)
            self.queue_lbl.configure(text='队列空闲', text_color=C['dim'])
        self._dock_header_update()

    # ---- 监听器 / 退出 ----
    def add_task_listener(self, fn):
        self._task_listeners.append(fn)

    def remove_task_listener(self, fn):
        try:
            self._task_listeners.remove(fn)
        except ValueError:
            pass

    def _on_close(self):
        self.queue.shutdown()
        self.cfg.set('geometry', self.winfo_geometry())
        self.cfg.save()
        self.destroy()

# ---------------- 入口 ----------------
def main():
    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('blue')
    if SMBConnection is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('缺少依赖',
                             '未安装 impacket, 请先运行:\n\n  pip install impacket\n\n'
                             '本程序通过 impacket 访问 SMB 共享, 缺少该库无法运行。')
        root.destroy()
        return
    app = BoxerApp()
    app.mainloop()



# ---------------- 命令行模式 (供自动化调用) ----------------
def _cli(argv):
    """命令行接口: discover / ls / stat / dl。
    结果 JSON 打到 stdout(无 stdout 的打包环境用 --out 写文件), 进度打 stderr。
    退出码: 0 成功 / 2 下载有失败 / 3 未发现盒子 / 4 参数或远程路径错误。"""
    _load_creds(AppConfig())
    import argparse

    ap = argparse.ArgumentParser(prog='stellavita_browser.py --cli',
                                 description='AstroStation 天文盒子: 发现/浏览/下载 (自动化调用)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    sp = sub.add_parser('discover', help='发现盒子地址')
    sp.add_argument('--host', default='')
    sp.add_argument('--out', default='')

    sp = sub.add_parser('ls', help='列出远程目录(JSON)')
    sp.add_argument('remote', nargs='?', default='')
    sp.add_argument('--host', default='')
    sp.add_argument('--out', default='')

    sp = sub.add_parser('stat', help='统计远程路径文件数/字节数')
    sp.add_argument('remote', nargs='?', default='')
    sp.add_argument('--host', default='')
    sp.add_argument('--out', default='')

    sp = sub.add_parser('dl', help='下载文件/文件夹(并行)')
    sp.add_argument('remote')
    sp.add_argument('local', help='目标: 文件夹=目标目录; 文件=文件路径或所在目录')
    sp.add_argument('--jobs', type=int, default=PARALLEL_DEFAULT)
    sp.add_argument('--host', default='')
    sp.add_argument('--out', default='')

    a = ap.parse_args(argv)

    def finish(obj, code):
        text = json.dumps(obj, ensure_ascii=False)
        if getattr(sys, 'stdout', None):
            print(text)
        p = getattr(a, 'out', '')
        if p:
            try:
                with open(p, 'w', encoding='utf-8') as f:
                    f.write(text)
            except Exception:
                pass
        return code

    def resolve():
        h = (getattr(a, 'host', '') or '').strip()
        if h:
            return h, []
        logs = []
        ip = discover_host(AppConfig(), log=logs.append)
        return ip, logs

    ip, logs = resolve()
    if not ip:
        return finish({'ok': False, 'error': '未发现盒子', 'logs': logs}, 3)

    if a.cmd == 'discover':
        return finish({'ok': True, 'host': ip, 'logs': logs}, 0)

    if a.cmd == 'ls':
        try:
            svc = SMBService(host=ip)
            svc.connect()
            items = [{'name': e.name, 'dir': e.is_dir, 'size': e.size, 'mtime': e.mtime}
                     for e in svc.list_dir(norm_path(a.remote))]
            svc.close()
        except Exception as e:
            return finish({'ok': False, 'host': ip, 'error': _short(e, 200)}, 4)
        return finish({'ok': True, 'host': ip, 'path': norm_path(a.remote), 'items': items}, 0)

    if a.cmd == 'stat':
        try:
            svc = SMBService(host=ip)
            svc.connect()
            n, b = svc.count_tree(norm_path(a.remote))
            svc.close()
        except Exception as e:
            return finish({'ok': False, 'host': ip, 'error': _short(e, 200)}, 4)
        return finish({'ok': True, 'host': ip, 'path': norm_path(a.remote), 'files': n, 'bytes': b}, 0)

    # dl
    remote = norm_path(a.remote)
    try:
        svc = SMBService(host=ip)
        svc.connect()
        entry = next((e for e in svc.list_dir(parent_path(remote))
                      if e.name == basename_path(remote)), None)
        svc.close()
    except Exception as e:
        return finish({'ok': False, 'host': ip, 'error': _short(e, 200)}, 4)
    if entry is None:
        return finish({'ok': False, 'host': ip, 'error': '远程不存在: %s' % remote}, 4)

    local = os.path.abspath(a.local)
    rename_to = ''
    q = DownloadQueue(on_change=None, host_getter=lambda: ip)
    q.set_parallel(max(1, min(16, a.jobs)))
    if entry.is_dir:
        task = q.submit(remote, local, kind='batch', title=entry.name)   # 目录内容直接进 local
    else:
        if os.path.isdir(local) or a.local.endswith(('/', '\\')):
            dst_dir = local
        else:
            dst_dir = os.path.dirname(local) or '.'
            if os.path.basename(local) != entry.name:
                rename_to = local
        task = q.submit(remote, dst_dir, kind='file', size_hint=entry.size, title=entry.name)

    t0 = time.time()
    last_n = -1
    while not task.terminal:
        time.sleep(0.3)
        if task.files_done != last_n:
            last_n = task.files_done
            if getattr(sys, 'stderr', None):
                print('  [%s/%s] %s' % (task.files_done, task.files_total or '?',
                                        size_str(task.bytes_done)), file=sys.stderr)
    q.shutdown()
    ok = (task.state == 'done')
    if ok and rename_to:
        try:
            os.replace(os.path.join(dst_dir, entry.name), rename_to)
        except Exception:
            ok = False
    res = {'ok': ok, 'host': ip, 'remote': remote, 'local': rename_to or local,
           'state': task.state, 'files': task.files_done, 'files_total': task.files_total,
           'bytes': task.bytes_done, 'seconds': round(time.time() - t0, 1)}
    if not ok:
        res['error'] = task.error or '下载失败'
    return finish(res, 0 if ok else 2)


def _selftest():
    """无头自检: 搜索盒子并列出根目录 (打包后验证/排障用)。"""
    import sys as _s
    _load_creds(AppConfig())
    logs = []
    ip = discover_host(None, log=logs.append)
    out = ['StellaVitaBrowser %s' % VERSION,
           'config -> %s' % CONFIG_PATH,
           'discover -> %s' % ip] + ['  ' + l for l in logs]
    ok = False
    if ip:
        try:
            s = SMBService(host=ip)
            s.connect()
            out.append('root: %s' % [e.name for e in s.list_dir('')])
            s.close()
            ok = True
        except Exception as e:
            out.append('ERR: %s' % e)
    text = '\n'.join(str(x) for x in out + (['SELFTEST OK'] if ok else ['SELFTEST FAIL']))
    if _s.stdout:
        print(text)
    _base = os.path.dirname(os.path.abspath(_s.executable if getattr(_s, 'frozen', False) else __file__))
    try:
        with open(os.path.join(_base, 'selftest_result.txt'), 'w', encoding='utf-8') as f:
            f.write(text)
    except Exception:
        pass
    return ok

if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(0 if _selftest() else 2)
    if '--cli' in sys.argv:
        sys.exit(_cli(sys.argv[sys.argv.index('--cli') + 1:]))
    main()
