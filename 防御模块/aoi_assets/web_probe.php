<?php
// AoiAWD wire protocol, fail-open PHP 5.5+ capture. Original request arrays are never changed.
if (!defined('GODHAND_AOI_MONITOR')) {
    define('GODHAND_AOI_MONITOR', true);
    ob_start(function ($buffer) {
        $errno = 0; $error = '';
        $socket = @stream_socket_client('tcp://127.0.0.1:SERVER_PORT', $errno, $error, 0.15);
        if (!$socket) { return $buffer; }
        stream_set_timeout($socket, 0, 200000);
        $encode = function ($value) use (&$encode) {
            if (is_array($value)) { return array_map($encode, $value); }
            return urlencode((string)$value);
        };
        $headers = array();
        foreach ($_SERVER as $key => $value) {
            if (strpos($key, 'HTTP_') === 0) { $headers[$key] = $encode($value); }
        }
        $payload = array('type' => 'web', 'data' => array(
            'script' => isset($_SERVER['SCRIPT_FILENAME']) ? $_SERVER['SCRIPT_FILENAME'] : '',
            'method' => isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : 'UNKNOWN',
            'uri' => isset($_SERVER['REQUEST_URI']) ? $_SERVER['REQUEST_URI'] : '',
            'remote' => isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : '',
            'header' => $headers, 'get' => $encode($_GET), 'post' => $encode($_POST),
            'cookie' => $encode($_COOKIE), 'file' => $encode($_FILES),
            'buffer' => urlencode(substr($buffer, 0, 1048576))
        ));
        $message = json_encode($payload) . "\n";
        $offset = 0;
        while ($offset < strlen($message)) {
            $written = @fwrite($socket, substr($message, $offset));
            if (!$written) { break; }
            $offset += $written;
        }
        // Core acknowledges the event; monitoring never replaces the application response.
        if ($offset === strlen($message)) { @fgets($socket, 2097152); }
        fclose($socket);
        return $buffer;
    });
}
