<?php
class RSA {
    private static $PRIVATE_KEY = <<<EOF
__GODHAND_RSA_PRIVATE__
EOF;
    private static $PUBLIC_KEY = <<<EOF
__GODHAND_RSA_PUBLIC__
EOF;
    private static function getPrivateKey() { return openssl_pkey_get_private(self::$PRIVATE_KEY); }
    private static function getPublicKey()  { return openssl_pkey_get_public(self::$PUBLIC_KEY); }
    public static function privateEncrypt($data) {
        $res = ''; foreach (str_split($data, 245) as $chunk) {
            openssl_private_encrypt($chunk, $enc, self::getPrivateKey()); $res .= $enc;
        } return $res ? base64_encode($res) : null;
    }
    public static function publicEncrypt($data) {
        $res = ''; foreach (str_split($data, 245) as $chunk) {
            openssl_public_encrypt($chunk, $enc, self::getPublicKey()); $res .= $enc;
        } return $res ? base64_encode($res) : null;
    }
    public static function privateDecrypt($data) {
        $res = ''; foreach (str_split(base64_decode($data), 256) as $chunk) {
            openssl_private_decrypt($chunk, $dec, self::getPrivateKey()); $res .= $dec;
        } return $res ?: null;
    }
    public static function publicDecrypt($data) {
        $res = ''; foreach (str_split(base64_decode($data), 256) as $chunk) {
            openssl_public_decrypt($chunk, $dec, self::getPublicKey()); $res .= $dec;
        } return $res ?: null;
    }
}
$rsa = new RSA(); $encrypt = isset($_GET['encrypt']); $data = file_get_contents("php://input");
if ($encrypt) { echo $rsa->privateEncrypt($data); }
else { $ret = $rsa->privateDecrypt($data); if (!$ret) $ret = $rsa->publicDecrypt($data); echo $ret ?: 'DECRYPT_FAILED'; }
