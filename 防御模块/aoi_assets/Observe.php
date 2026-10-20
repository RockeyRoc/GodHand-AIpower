<?php
namespace aoiawd\plugin;
// Observation only: never changes traffic or suppresses evidence.
$manager = PluginManager::getInstance();
$manager->register('Web', 'processLog', function ($data) use ($manager) {
    if (preg_match('/flag\{[^}]+\}|"flag"\s*:/i', $data['buffer'])) {
        $manager->getInvoker()->setAlert('GodHandObserve', 'Web 响应包含 flag 特征');
    }
    return $data;
});
