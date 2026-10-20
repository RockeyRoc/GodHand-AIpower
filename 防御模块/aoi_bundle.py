"""Build the offline Windows AoiAWD companion, using pinned upstream archives."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
ASSETS = Path(__file__).with_name('aoi_assets')
REVISION = '0d57da2317685964ccf62d9d7f05a87f699fc8c7'
ARCHIVES = {
 'upstream': ('https://codeload.github.com/DasSecurity-HatLab/AoiAWD/zip/' + REVISION, 'e74f3040c4866bf8d055cb228733be5e4f73926f7a4ec4456df2b38182e19312'),
 'php': ('https://windows.php.net/downloads/releases/archives/php-7.4.33-nts-Win32-vc15-x64.zip', '14ae3250d4447c8ccfc4c45a70d90adfbcd61e728d85f0be56a7ddf8f9c8aace'),
 'php-mongodb': ('https://downloads.php.net/~windows/pecl/releases/mongodb/1.16.2/php_mongodb-1.16.2-7.4-nts-vc15-x64.zip', 'e12d80f7a2773a2a59a7d62405ec2c664f55f25ec65803b57af6703e60ec1d0f'),
 'mongodb': ('https://fastdl.mongodb.org/windows/mongodb-windows-x86_64-6.0.26.zip', 'fc18d8993c591bdd6f5b4a1edd42acd627ace3876a4d1eb359a8a2b78f3077b6'),
 'zig': ('https://ziglang.org/download/0.13.0/zig-windows-x86_64-0.13.0.zip', 'd859994725ef9402381e557c60bb57497215682e355204d754ee3df75ee3c158'),
 'inotify': ('https://codeload.github.com/inotify-tools/inotify-tools/zip/refs/tags/v3.14', '5d6cd931da9b0a395f8acae3e0f9cff65bd6af032a9b3e07f46c390d4ae5c681'),
}


def fetch(cache, name):
    url, digest = ARCHIVES[name]
    archive = cache / (name + '.zip')
    if not archive.exists():
        with urllib.request.urlopen(url, timeout=60) as source, archive.open('wb') as dest:
            shutil.copyfileobj(source, dest)
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
            raise ValueError('下载文件校验失败：' + name)
    directory = cache / name
    if not directory.exists():
        with zipfile.ZipFile(archive) as source:
            for entry in source.infolist():
                (directory / entry.filename).resolve().relative_to(directory.resolve())
            source.extractall(directory)
    return directory


def replace(path, old, new):
    text = path.read_text(encoding='utf-8')
    if old not in text:
        raise ValueError('上游补丁位置不匹配：' + str(path))
    path.write_text(text.replace(old, new), encoding='utf-8')


def build(cache, output):
    cache.mkdir(parents=True, exist_ok=True)
    archives = {name: fetch(cache, name) for name in ARCHIVES}
    upstream = next(archives['upstream'].iterdir())
    if (output / 'bundle.json').exists():
        raise FileExistsError('请使用新的配套环境输出目录')
    output.mkdir(parents=True, exist_ok=True)
    source = output / 'source'
    shutil.copytree(upstream, source / 'AoiAWD', dirs_exist_ok=True)
    shutil.copytree(next(archives['inotify'].iterdir()), source / 'inotify-tools', dirs_exist_ok=True)
    original = source / 'AoiAWD'
    # Keep original upstream code and changed corresponding sources together.
    patched = source / 'patched'
    shutil.copytree(original / 'RoundWorm', patched / 'RoundWorm', dirs_exist_ok=True)
    shutil.copytree(original / 'Guardian', patched / 'Guardian', dirs_exist_ok=True)
    sender = patched / 'RoundWorm/src/common/sender.c'
    replace(sender, 'send(instance->socket, buffer, strlen(buffer), 0);',
        'size_t offset = 0, size = strlen(buffer);\n'
        '            while (offset < size) {\n'
        '                ssize_t sent = send(instance->socket, buffer + offset, size - offset, MSG_NOSIGNAL);\n'
        '                if (sent <= 0) { close(instance->socket); instance->connected = false; break; }\n'
        '                offset += sent;\n'
        '            }')
    replace(sender, 'static socklen_t peerlen;', 'socklen_t peerlen = sizeof(peeraddr);')
    replace(sender, 'free(newsender);', 'free(newsender);\n        sender_send(instance, buffer);')
    process = patched / 'RoundWorm/src/process.c'
    replace(process, 'memset(proc_addr, 0, 256);', 'memset(proc_addr, 0, sizeof(proc_addr));')
    shutil.copy2(ASSETS / 'native_json.h', patched / 'RoundWorm/src/native_json.h')
    replace(process, '#include "process.h"', '#include "process.h"\n#include "native_json.h"')
    replace(process, 'strncat(process_info->param, arg, PAM_LENGTH);', 'strncat(process_info->param, arg, PAM_LENGTH - strlen(process_info->param) - 1);')
    replace(process, 'strncat(process_info->param, " ", PAM_LENGTH);', 'strncat(process_info->param, " ", PAM_LENGTH - strlen(process_info->param) - 1);')
    replace(process, 'strncpy(process_info->cmdline, arg, CMD_LENGTH);', 'strncpy(process_info->cmdline, arg, CMD_LENGTH - 1);')
    replace(process, 'strncpy(process_info->cmdline, virtual_name, CMD_LENGTH);', 'strncpy(process_info->cmdline, virtual_name, CMD_LENGTH - 1);')
    replace(process, 'int end = snapshot->count;', 'int end = (int)snapshot->count - 1;\n    if (end < 0) return 0;')
    replace(process, 'int target = snapshot->count / 2;', 'int target = end / 2;')
    replace(process, 'current_snapshot.pid_snapshot[current_snapshot.count] = pid;',
        'if (current_snapshot.count >= MAX_PROCESS_COUNT) { free(process); break; }\n            current_snapshot.pid_snapshot[current_snapshot.count] = pid;')
    replace(process, 'int ret = asprintf(&json_buffer,', 'char *esc_cmd = aoi_json_escape(process->cmdline), *esc_param = aoi_json_escape(process->param), *esc_user = aoi_json_escape(process->user.username);\n                int ret = asprintf(&json_buffer,')
    replace(process, 'process->user.username, process->cmdline, process->param);', 'esc_user, esc_cmd, esc_param);\n                free(esc_cmd); free(esc_param); free(esc_user);')
    replace(process, 'memcpy(&snapshot, &current_snapshot, sizeof(PROCESS_SNAPSHOT_t));',
        'for (size_t i = 1; i < current_snapshot.count; i++) { unsigned int v = current_snapshot.pid_snapshot[i]; size_t j = i; while (j > 0 && current_snapshot.pid_snapshot[j-1] > v) { current_snapshot.pid_snapshot[j] = current_snapshot.pid_snapshot[j-1]; j--; } current_snapshot.pid_snapshot[j] = v; }\n    memcpy(&snapshot, &current_snapshot, sizeof(PROCESS_SNAPSHOT_t));')
    io = patched / 'RoundWorm/src/io.c'
    replace(io, 'struct stat my_stat;', 'struct stat my_stat = {0};')
    replace(io, 'static struct stat my_stat = {0};', 'static struct stat my_stat;')
    # Include rename events in the actual report, as well as in the inotify watch.
    replace(io, 'IN_DELETE | IN_DELETE_SELF;', 'IN_DELETE | IN_DELETE_SELF | IN_MOVED_FROM | IN_MOVED_TO;')
    replace(io, '#include "io.h"', '#include "io.h"\n#include "native_json.h"')
    replace(io, 'if (asprintf(&buffer,', 'char *escaped_path = aoi_json_escape(path);\n    if (asprintf(&buffer,')
    replace(io, 'path, my_stat.st_mode, event->mask, my_stat.st_size, file_b64_buffer)', 'escaped_path, my_stat.st_mode, event->mask, my_stat.st_size, file_b64_buffer)')
    replace(io, 'free(file_b64_buffer);', 'free(escaped_path);\n    free(file_b64_buffer);')
    guardian = patched / 'Guardian/src/main.c'
    replace(patched / 'Guardian/src/connection.c', 'struct timeval ts;', 'struct timeval ts = {0};')
    replace(guardian, 'char *self_path = realpath(buffer, NULL);',
        'char *self_path = calloc(PATH_MAX, 1);\n    readlink(buffer, self_path, PATH_MAX - 1);')
    replace(guardian, 'realpath(buffer, child_path);',
        'ssize_t length = readlink(buffer, child_path, PATH_MAX - 1);\n        if (length < 0) break;\n        child_path[length] = 0;\n        usleep(1000);')
    replace(guardian, 'int master;', 'signal(SIGPIPE, SIG_IGN);\n    int master;')
    replace(guardian, 'kill(pid, SIGKILL);\n            return 1;', 'sock_stdout = -1;\n            sock_stdin = -1;')
    replace(guardian, 'read(STDIN_FILENO, &input, 1);', 'if (read(STDIN_FILENO, &input, 1) <= 0) { FD_CLR(STDIN_FILENO, &read_fd); continue; }')
    replace(guardian, 'if (read(master, &output, 1) != -1)', 'if (read(master, &output, 1) > 0)')
    inotify = source / 'inotify-tools'
    (inotify / 'config.h').write_text('#define HAVE_SYS_INOTIFY_H 1\n#define HAVE_INOTIFY_INIT 1\n#define HAVE_INOTIFY_INIT1 1\n#define HAVE_SYS_TYPES_H 1\n#define HAVE_SYS_STAT_H 1\n#define HAVE_UNISTD_H 1\n', encoding='ascii')
    (inotify / 'libinotifytools/src/inotifytools/inotify.h').write_text('#include <sys/inotify.h>\n', encoding='ascii')
    zig = next(archives['zig'].rglob('zig.exe'))
    probes = output / 'probes'
    probes.mkdir(exist_ok=True)
    def compile(directory, filename, extra=()):
        files = [str(p) for p in directory.rglob('*.c')]
        command = [str(zig), 'cc', '-target', 'x86_64-linux-musl', '-static', '-O2', '-D_GNU_SOURCE', '-D_LARGEFILE64_SOURCE', '-Wno-incompatible-pointer-types',
            '-I', str(directory / 'src'), *files, *map(str, extra), '-pthread', '-o', str(probes / filename)]
        result = subprocess.run(command, capture_output=True, env=dict(os.environ,
            ZIG_GLOBAL_CACHE_DIR=str(cache / 'zig-cache'), ZIG_LOCAL_CACHE_DIR=str(cache / 'zig-local')))
        if result.returncode:
            raise RuntimeError('Linux probe compiler: ' + result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))
    compile(patched / 'RoundWorm', 'roundworm', ['-I', str(inotify / 'libinotifytools/src'),
        inotify / 'libinotifytools/src/inotifytools.c', inotify / 'libinotifytools/src/redblack.c'])
    compile(patched / 'Guardian', 'guardian')
    fixture = patched / 'echo_fixture.c'
    fixture.write_text('#include <stdio.h>\nint main(void){char s[256];if(fgets(s,sizeof(s),stdin)){printf("GODHAND_ECHO:%s",s);fflush(stdout);}return 0;}\n', encoding='ascii')
    subprocess.run([str(zig), 'cc', '-target', 'x86_64-linux-musl', '-static', '-O2', str(fixture), '-o', str(probes / 'echo_fixture')], check=True,
        env=dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(cache / 'zig-cache'), ZIG_LOCAL_CACHE_DIR=str(cache / 'zig-local')))
    php = output / 'php'
    php.mkdir()
    for file in archives['php'].iterdir():
        if file.is_file() and (file.suffix.lower() == '.dll' or file.name in ('php.exe', 'license.txt')):
            shutil.copy2(file, php / file.name)
    shutil.copy2(next(archives['php-mongodb'].rglob('php_mongodb.dll')), php / 'php_mongodb.dll')
    (php / 'php.ini').write_text('extension_dir="' + str(php).replace('\\', '/') + '"\nextension=php_mongodb.dll\ndate.timezone=Asia/Shanghai\ndisplay_errors=Off\nlog_errors=On\nmemory_limit=256M\n', encoding='utf-8')
    # The runtime resolves extension_dir itself, so the bundle remains movable.
    (php / 'php.ini').write_text('extension=php_mongodb.dll\ndate.timezone=Asia/Shanghai\ndisplay_errors=Off\nlog_errors=On\nmemory_limit=256M\n', encoding='utf-8')
    mongo = next(archives['mongodb'].iterdir())
    (output / 'mongodb/bin').mkdir(parents=True)
    shutil.copy2(mongo / 'bin/mongod.exe', output / 'mongodb/bin/mongod.exe')
    for file in mongo.iterdir():
        if file.is_file():
            shutil.copy2(file, output / 'mongodb' / file.name)
    core = output / 'core'
    shutil.copytree(original / 'AoiAWD/src', core)
    shutil.copy2(ASSETS / 'AsyncTCPServer.php', core / 'aoicommon/socket/AsyncTCPServer.php')
    helper = core / 'aoiawd/DBHelper.php'
    text = helper.read_text(encoding='utf-8')
    text = re.sub(r'static public function getCollactionCount\(\$collaction\).*?(?=static public function escape)',
        'static public function getCollactionCount($collaction)\n    {\n        return self::getDB()->$collaction->countDocuments();\n    }\n\n    static public function addCollactionCount($collaction, $count = 1)\n    {\n        // MongoDB owns the count; do not retain stale counts across restarts.\n    }\n\n    ', text, flags=re.S)
    helper.write_text(text, encoding='utf-8')
    receiver = core / 'aoiawd/LogReceiver.php'
    replace(receiver, 'public function onReceive($data, $socket)\n    {', 'public function onReceive($data, $socket)\n    {\n        $this->alert = null;\n        $this->insert = true;')
    replace(receiver, "if ($data === false) {\n                unset($data_storage->socket);", "if ($data === false) {\n                unset($this->pwnSocket[$resId]);\n                $data_storage->socket = array_filter($data_storage->socket, function ($item) use ($resId) { return (int)$item !== $resId; });\n                if (count($data_storage->socket)) { return; }\n                unset($data_storage->socket);")
    replace(core / 'aoiawd/AoiAWD.php', "PHAR_BASE . '/public'", "getenv('GODHAND_AOI_PUBLIC')")
    replace(core / 'aoiawd/AoiAWD.php', "$this->apiServer->getRouter()->addRoute('GET', '/websocket', new WebsocketHandler);", '// GodHand uses authenticated polling instead of the unauthenticated upstream websocket.')
    replace(core / 'aoiawd/AoiAWD.php', 'WebsocketHandler::triggerNotify();', '// Polling frontend: no websocket broadcast required.')
    replace(core / 'aoiawd/AoiAWD.php', 'WebsocketHandler::notifyAll($type);', '// Polling frontend reads persisted records.')
    replace(core / 'aoiawd/AoiAWD.php', '$this->logger->notice("AccessToken: {$this->accessToken}");', '$this->logger->notice("GodHand local authentication configured");')
    replace(core / 'aoicommon/api/BaseAPIController.php', "'Access-Control-Allow-Origin' => '*',", '')
    for file in ['LICENSE', 'README.md']:
        shutil.copy2(original / file, output / file)
    (output / 'GODHAND-NOTICE.txt').write_text('AoiAWD AGPL-3.0; inotify-tools GPL-2.0; PHP PHP-3.01; MongoDB SSPL-1.0.\nOriginal sources and local changes: source/ and core/.\nGodHand changes: native Windows lifecycle, isolated target stores, observation-only Web, bounded fail-open PHP capture, static Linux probes and compatibility fixes.\nMongoDB is bound exclusively to loopback and is not installed as a service.\n', encoding='utf-8')
    licenses = output / 'licenses'
    licenses.mkdir(exist_ok=True)
    for name in ['LICENSE', 'CREDITS', 'THIRD_PARTY_NOTICES']:
        shutil.copy2(archives['php-mongodb'] / name, licenses / ('php-mongodb-' + name + '.txt'))
    zig_root = next(archives['zig'].iterdir())
    for path, name in [(zig_root / 'LICENSE', 'zig-LICENSE.txt'), (zig_root / 'lib/libc/musl/COPYRIGHT', 'musl-COPYRIGHT.txt')]:
        if path.is_file():
            shutil.copy2(path, licenses / name)
    files = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob('*') if p.is_file()}
    (output / 'bundle.json').write_text(json.dumps({'revision': REVISION, 'platform': 'windows-x64', 'archives': ARCHIVES, 'files': files}, indent=2), encoding='utf-8')
    return {'output': str(output), 'files': len(files), 'revision': REVISION}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, default=ROOT.parent / 'GodHand-tmp/aoi-native')
    parser.add_argument('--output', type=Path, default=ROOT / 'tools/AoiAWD')
    args = parser.parse_args()
    print(json.dumps(build(args.cache.resolve(), args.output.resolve()), ensure_ascii=False))
