<?php
namespace aoicommon\socket;
use Amp\Loop;
use aoicommon\helper\CommonHelper;
use function Amp\Socket\listen;

// AoiAWD packet/stream server with lossless transition and EOF handling.
class AsyncTCPServer {
    private $tcpServer, $callback, $logger;
    private $clients = [], $cache = [];
    public function __construct($uri) { $this->tcpServer = listen($uri); $this->logger = CommonHelper::getLogger(self::class); }
    public function start() { Loop::onReadable($this->tcpServer->getResource(), [$this, 'onAccept']); }
    public function close() { $this->tcpServer->close(); }
    public function setCallback(callable $callback) { $this->callback = $callback; }
    public function useStream($id) { $this->cache[$id][0] = false; }
    public function usePacket($id) { $this->cache[$id][0] = true; }
    public function onAccept($watcher, $socket) {
        $client = @stream_socket_accept($socket, 0);
        if (!$client) { return; }
        stream_set_blocking($client, false);
        $id = (int)$client;
        $this->clients[$id] = $client;
        $this->cache[$id] = [true, ''];
        Loop::onReadable($client, [$this, 'onClientReadable']);
    }
    public function onClientReadable($watcher, $socket) {
        $id = (int)$socket;
        $data = @fread($socket, 65536);
        if ($data === false || ($data === '' && feof($socket))) {
            if (isset($this->cache[$id]) && !$this->cache[$id][0] && $this->cache[$id][1] !== '') {
                ($this->callback)($this->cache[$id][1], $socket);
            }
            ($this->callback)(false, $socket);
            Loop::cancel($watcher);
            unset($this->cache[$id], $this->clients[$id]);
            fclose($socket);
            return;
        }
        if ($data === '') { return; }
        $this->cache[$id][1] .= $data;
        while ($this->cache[$id][0] && ($position = strpos($this->cache[$id][1], "\n")) !== false) {
            $packet = substr($this->cache[$id][1], 0, $position);
            $this->cache[$id][1] = substr($this->cache[$id][1], $position + 1);
            ($this->callback)($packet, $socket);
        }
        if (!$this->cache[$id][0] && $this->cache[$id][1] !== '') {
            $stream = $this->cache[$id][1];
            $this->cache[$id][1] = '';
            ($this->callback)($stream, $socket);
        }
        if (strlen($this->cache[$id][1]) > 4194304) {
            ($this->callback)(false, $socket);
            Loop::cancel($watcher);
            unset($this->cache[$id], $this->clients[$id]);
            fclose($socket);
        }
    }
}
