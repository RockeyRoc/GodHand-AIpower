"""The production endpoint and migration of pre-1.6 port settings."""
import socket
import time
from .storage import exclusive_lock

HOST = '127.0.0.1'
PORT = 18765
MCP_URL = f'http://{HOST}:{PORT}/mcp'


def ensure_fixed_endpoint(store):
    """Migrate an idle legacy configuration without stopping another process."""
    with store.lock, exclusive_lock(store.path / 'state.lock'):
        config = dict(store.reload_config())
        old_port = int(config.get('port', PORT))
        if config.get('host') == HOST and old_port == PORT:
            return ''
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if old_port != PORT and probe.connect_ex((HOST, old_port)) == 0:
                raise RuntimeError(f'旧端口 {old_port} 仍有服务运行。请先停止旧服务，再启动 GodHand；配置尚未迁移。')
        backup = store.config_path.with_name('hub.json.before-1.6-' + str(time.time_ns()) + '.bak')
        backup.write_bytes(store.config_path.read_bytes())
        config.update(host=HOST, port=PORT)
        store._write_config(config)
        return str(backup)
