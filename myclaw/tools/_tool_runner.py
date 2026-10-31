"""Runs ONE agent-made tool in a locked-down process (2026-10-28, rules re-review).

Started by ``myclaw/tools/tool_sandbox.py`` as ``python -I -X utf8 _tool_runner.py``
with a JSON request on stdin; prints one result line on stdout. Standard
library only — it must not import ZenSynora.

Before the tool's code runs:

1. **Landlock** (Linux 5.13+): the process may write only inside the agent
   workspace, read only the Python installation and system folders (not the
   home folder, so not ``~/.myclaw``), connect only to the allowed TCP ports
   (abi 4+), and cannot signal or reach processes outside the sandbox (abi 6+).
2. **Audit hook** (everywhere, also without Landlock): no programs started
   (subprocess, os.system, exec, fork, spawn), no ctypes, no files written
   outside the workspace, nothing read from ``~/.myclaw`` outside the
   workspace, network connections only to public addresses on allowed ports.
3. Resource limits: CPU time and file size.

The parent also sets HOME to a folder inside the workspace and drops proxy
settings.
"""

import asyncio
import inspect
import io
import ipaddress
import json
import logging
import os
import socket
import stat
import sys

MARK = "@@ZENSYNORA-TOOL-RESULT@@ "

# Landlock constants (linux/landlock.h)
_SYS_CREATE, _SYS_ADD, _SYS_RESTRICT = 444, 445, 446
_FS_EXECUTE, _FS_WRITE_FILE, _FS_READ_FILE, _FS_READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
_FS_TRUNCATE, _FS_IOCTL_DEV, _FS_REFER = 1 << 14, 1 << 15, 1 << 13
_NET_BIND, _NET_CONNECT = 1 << 0, 1 << 1
_SCOPE_ABSTRACT_UNIX, _SCOPE_SIGNAL = 1 << 0, 1 << 1


def _public(ip) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        inner = ip.ipv4_mapped or ip.sixtofour
        if inner is None and ip.teredo:
            inner = ip.teredo[1]
        if inner is not None:
            return _public(inner)
        if ip in ipaddress.ip_network("64:ff9b::/96"):
            return _public(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return bool(ip.is_global) and not ip.is_multicast


def _within(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def landlock(workspace: str, ports: list) -> str:
    """Restrict this process with Landlock. Returns a short status."""
    if not sys.platform.startswith("linux"):
        return "unavailable (not Linux)"
    try:
        import ctypes
    except ImportError:
        return "unavailable (no ctypes)"
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(_SYS_CREATE, None, ctypes.c_size_t(0), ctypes.c_uint32(1))
    if abi < 1:
        return "unavailable (kernel)"
    fs = (1 << 13) - 1
    if abi >= 2:
        fs |= _FS_REFER
    if abi >= 3:
        fs |= _FS_TRUNCATE
    if abi >= 5:
        fs |= _FS_IOCTL_DEV
    net = (_NET_BIND | _NET_CONNECT) if abi >= 4 else 0
    scoped = (_SCOPE_ABSTRACT_UNIX | _SCOPE_SIGNAL) if abi >= 6 else 0

    class Attr(ctypes.Structure):
        _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64), ("scoped", ctypes.c_uint64)]

    class PathBeneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    class NetPort(ctypes.Structure):
        _fields_ = [("allowed", ctypes.c_uint64), ("port", ctypes.c_uint64)]

    size = 8 if abi < 4 else (16 if abi < 6 else 24)
    attr = Attr(fs, net, scoped)
    rfd = libc.syscall(_SYS_CREATE, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if rfd < 0:
        return f"unavailable (create: errno {ctypes.get_errno()})"
    file_rights = _FS_EXECUTE | _FS_WRITE_FILE | _FS_READ_FILE | _FS_TRUNCATE | _FS_IOCTL_DEV
    read = _FS_EXECUTE | _FS_READ_FILE | _FS_READ_DIR

    def allow(path: str, rights: int) -> None:
        try:
            pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError:
            return
        try:
            r = rights & fs
            if not stat.S_ISDIR(os.fstat(pfd).st_mode):
                r &= file_rights
            pb = PathBeneath(r, pfd)
            libc.syscall(_SYS_ADD, ctypes.c_int(rfd), ctypes.c_int(1), ctypes.byref(pb), ctypes.c_uint32(0))
        finally:
            os.close(pfd)

    # Python itself (stdlib and site-packages), never a source folder that may hold .env
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    for entry in sys.path:
        if entry and ("site-packages" in entry or "dist-packages" in entry
                      or any(_within(os.path.realpath(entry), os.path.realpath(r)) for r in roots)):
            roots.add(entry)
    for p in sorted(roots) + ["/usr", "/lib", "/lib64", "/lib32", "/bin", "/sbin", "/etc", "/opt/homebrew",
                              "/run/systemd/resolve", "/nix/store"]:
        if p and os.path.exists(p):
            allow(os.path.realpath(p), read)
    allow("/dev", read | _FS_WRITE_FILE | _FS_IOCTL_DEV)
    allow(workspace, fs)
    if net:
        for port in ports:
            np_ = NetPort(_NET_CONNECT, int(port))
            libc.syscall(_SYS_ADD, ctypes.c_int(rfd), ctypes.c_int(2), ctypes.byref(np_), ctypes.c_uint32(0))
    libc.prctl(38, 1, 0, 0, 0)  # PR_SET_NO_NEW_PRIVS
    ok = libc.syscall(_SYS_RESTRICT, ctypes.c_int(rfd), ctypes.c_uint32(0)) == 0
    os.close(rfd)
    return f"on (abi {abi})" if ok else f"failed (errno {ctypes.get_errno()})"


def install_hook(workspace: str, data_dir: str, ports: list) -> None:
    """Refuse programs, ctypes, writes outside the workspace and private network addresses."""
    blocked = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork",
               "os.forkpty", "pty.spawn", "os.kill", "os.killpg", "signal.pthread_kill", "os.startfile",
               "ctypes.dlopen", "ctypes.dlsym", "ctypes.call_function", "ctypes.cdata", "ctypes.addressof",
               "ctypes.string_at", "ctypes.wstring_at", "ctypes.create_string_buffer", "sys._current_frames",
               "socket.bind", "mmap.__new__", "webbrowser.open", "os.chroot"}
    write_events = {"os.remove": (0,), "os.rmdir": (0,), "os.mkdir": (0,), "os.rename": (0, 1),
                    "os.symlink": (1,), "os.link": (1,), "os.chmod": (0,), "os.chown": (0,), "os.truncate": (0,),
                    "os.utime": (0,), "os.mkfifo": (0,), "os.mknod": (0,), "os.setxattr": (0,),
                    "os.removexattr": (0,), "shutil.copyfile": (1,), "shutil.copymode": (1,),
                    "shutil.copystat": (1,), "shutil.copytree": (1,), "shutil.rmtree": (0,), "shutil.move": (0, 1),
                    "shutil.chown": (0,), "os.chflags": (0,), "os.lchflags": (0,)}
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    allowed_ports = {int(p) for p in ports}

    def real(p) -> str:
        if isinstance(p, bytes):
            p = os.fsdecode(p)
        return os.path.realpath(os.path.join(workspace, os.path.expanduser(str(p))))

    def check_read(path: str) -> None:
        if _within(path, data_dir) and not _within(path, workspace):
            raise PermissionError("ZenSynora's data folder (~/.myclaw) is not available to tools; "
                                  "use files in the workspace.")

    def check_write(path: str) -> None:
        if not (_within(path, workspace) or path == "/dev/null"):
            raise PermissionError(f"Tools may only write inside the workspace ({workspace}); '{path}' is "
                                  "outside.")

    def check_addr(address) -> None:
        if not isinstance(address, tuple) or len(address) < 2:
            raise PermissionError("Tools may only use internet (TCP/UDP) connections.")
        host, port = str(address[0]), int(address[1])
        try:
            ips = [ipaddress.ip_address(host.split("%", 1)[0])]
        except ValueError:
            ips = []
            for info in socket.getaddrinfo(host, port):
                ips.append(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
        for ip in ips:
            if not _public(ip):
                raise PermissionError(f"Tools may not connect to {ip} (this computer or a local network); "
                                      "only public internet addresses.")
        if allowed_ports and port not in allowed_ports:
            raise PermissionError(f"Tools may connect only to ports {sorted(allowed_ports)} "
                                  "(Settings → Features → sandbox.tool_ports).")

    def hook(event: str, args: tuple) -> None:
        if event in blocked:
            raise PermissionError(f"'{event}' is not allowed in tools (no programs, no native code).")
        if event == "open":
            path, mode, flags = (list(args) + [None, None, None])[:3]
            if path is None or isinstance(path, int):
                return
            rp = real(path)
            check_read(rp)
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
                (isinstance(flags, int) and flags & write_flags)
            if writing:
                check_write(rp)
        elif event in write_events:
            for i in write_events[event]:
                if i < len(args) and isinstance(args[i], (str, bytes, os.PathLike)):
                    rp = real(args[i])
                    check_read(rp)
                    check_write(rp)
        elif event in ("os.listdir", "os.scandir", "glob.glob"):
            if args and isinstance(args[0], (str, bytes, os.PathLike)):
                check_read(real(args[0]))
        elif event == "socket.__new__":
            family = args[1] if len(args) > 1 else None
            if family not in (socket.AF_INET, socket.AF_INET6, getattr(socket, "AF_UNIX", -2), -1, None):
                raise PermissionError("Tools may only use internet (TCP/UDP) sockets.")  # AF_UNIX: asyncio pipes; connect is checked
        elif event in ("socket.connect", "socket.sendto"):
            check_addr(args[1] if len(args) > 1 else None)
        elif event == "import" and args and str(args[0]).split(".")[0] in ("ctypes", "_ctypes", "_posixsubprocess"):
            raise PermissionError(f"Importing {args[0]} is not allowed in tools.")

    sys.addaudithook(hook)


def limits(timeout: int) -> None:
    try:
        import resource

        cpu = max(5, int(timeout)) + 5
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_FSIZE, (100 * 2 ** 20, 100 * 2 ** 20))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:  # pragma: no cover - not available on this platform
        pass


def main() -> None:
    req = json.loads(sys.stdin.read())
    out = sys.stdout
    name = req["name"]
    workspace = os.path.realpath(req["workspace"])
    data_dir = os.path.realpath(os.path.join(req["real_home"], ".myclaw"))
    ports = list(req.get("ports") or [])
    limits(int(req.get("timeout") or 30))
    status = landlock(workspace, ports) if req.get("landlock", True) else "off"
    if req.get("hook", True):  # off only in tests that check Landlock alone
        install_hook(workspace, data_dir, ports)
    for mod in ("ctypes", "_ctypes"):
        sys.modules.pop(mod, None)
    printed = io.StringIO()
    sys.stdout = printed
    payload: dict = {"landlock": status}
    try:
        ns = {"__name__": "zensynora_tool", "__file__": req.get("file") or f"{name}.py",
              "logger": logging.getLogger(f"tool.{name}")}
        exec(compile(req["code"], req.get("file") or f"{name}.py", "exec"), ns)
        fn = ns.get(name)
        if not callable(fn):
            raise ValueError(f"the code does not define a function named '{name}'")
        result = None
        if req.get("mode") != "check":
            result = fn(**(req.get("args") or {}))
            if inspect.isawaitable(result):
                result = asyncio.run(result)  # type: ignore[arg-type]
            if result is None and printed.getvalue():
                result = printed.getvalue()
        payload.update(ok=True, result=result)
        try:
            line = json.dumps(payload)
        except (TypeError, ValueError):
            payload["result"] = str(result)
            line = json.dumps(payload)
    except BaseException as e:  # noqa: BLE001 - report anything to the parent
        payload.update(ok=False, error=f"{type(e).__name__}: {e}"[:2000])
        line = json.dumps(payload)
    out.write("\n" + MARK + line + "\n")
    out.flush()


if __name__ == "__main__":
    main()
