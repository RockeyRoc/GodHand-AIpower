"""stdio MCP transport for GodHand.

The bridge is a thin client of the single local hub, so stdio and HTTP clients
share one target store, scheduler and task database. If no hub is running the
bridge starts one and owns it for the lifetime of the stdio session.
"""
import sys

from .core import Store
from .server import Connection, register_godhand
from .version import VERSION


def prepare_stdio():
    """Recover inherited pipes in PyInstaller's Windows windowed executable."""
    import os
    if os.name != 'nt' or all(getattr(sys, name) is not None for name in ('stdin', 'stdout', 'stderr')):
        return
    import ctypes
    from ctypes import wintypes
    import io
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                      ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                      wintypes.BOOL, wintypes.DWORD]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    process = kernel.GetCurrentProcess()
    for fd, name, handle_id, mode in [(0, 'stdin', -10, 'rb'), (1, 'stdout', -11, 'wb'), (2, 'stderr', -12, 'wb')]:
        if getattr(sys, name) is not None:
            continue
        handle = kernel.GetStdHandle(handle_id & 0xffffffff)
        if not handle or handle == ctypes.c_void_p(-1).value:
            if name != 'stderr':
                raise RuntimeError('stdio 需要由 MCP 客户端通过输入输出管道启动。')
            recovered = os.open(os.devnull, os.O_WRONLY | os.O_BINARY)
        else:
            duplicate = wintypes.HANDLE()
            if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                recovered = msvcrt.open_osfhandle(duplicate.value,
                    (os.O_RDONLY if fd == 0 else os.O_WRONLY) | os.O_BINARY)
            except Exception:
                kernel.CloseHandle(duplicate)
                raise
        if recovered != fd:
            os.dup2(recovered, fd)
            os.close(recovered)
        stream = io.TextIOWrapper(os.fdopen(fd, mode, buffering=0, closefd=False),
                                  encoding='utf-8', errors='replace', write_through=fd != 0)
        setattr(sys, name, stream)
        setattr(sys, '__' + name + '__', stream)


def _dispatch(connection):
    def dispatch(action, values):
        from mcp.server.mcpserver.exceptions import ToolError
        try:
            return connection.call(action, **(values or {}))
        except Exception as exc:
            raise ToolError(Connection.describe_error(exc)) from None
    return dispatch


def _report(message):
    try:
        if sys.stderr is not None:
            sys.stderr.write(message + '\n')
            sys.stderr.flush()
    except Exception:
        pass


def serve(store=None):
    prepare_stdio()
    from mcp.server import MCPServer
    from .endpoint import ensure_fixed_endpoint
    store = store or Store()
    ensure_fixed_endpoint(store)
    connection = Connection(store)
    try:
        connection.start()
    except Exception as exc:
        _report('GodHand stdio 启动失败：' + str(exc))
        raise
    server = MCPServer(
        'GodHand',
        version=VERSION,
        instructions='stdio 通道与 HTTP 共用同一本机服务；统一工具 godhand(action, arguments)。',
    )
    register_godhand(server, _dispatch(connection))
    try:
        server.run('stdio')
    finally:
        connection.close()
    return 0


def main(argv=None):
    """Entry used by ``GodHand.exe --mcp-stdio``."""
    import os
    data_dir = None
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        data_dir = arguments.pop(0)
    store = Store(data_dir=data_dir or os.environ.get('GODHAND_DATA_DIR'))
    return serve(store)
