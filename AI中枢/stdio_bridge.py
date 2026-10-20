"""stdio MCP transport for GodHand.

The bridge is a thin client of the single local hub, so stdio and HTTP clients
share one target store, scheduler and task database. If no hub is running the
bridge starts one and owns it for the lifetime of the stdio session.
"""
import sys

from .core import Store
from .server import Connection, register_godhand
from .version import VERSION


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
    from mcp.server import MCPServer
    connection = Connection(store or Store())
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
