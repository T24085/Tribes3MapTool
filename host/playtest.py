"""Portable private-session launcher. Original game files are read-only inputs."""
import argparse
import csv
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'lib'))
from community_maps import BY_ID, declared_definitions
from host_catalog_runtime import runtime_paths
REL = Path('Topaz/Binaries/Win64')
MODE = '/Game/Topaz/Game/BP_TopazGameMode_CTF_Persistent_BotBackfill.BP_TopazGameMode_CTF_Persistent_BotBackfill_C'
MAPS = tuple(BY_ID)


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def engine_command_line(arguments):
    """Match UE 5.3's BuildFromArgV format after Windows parses the transport."""
    result = []
    for arg in arguments:
        if ' ' in arg and '"' not in arg:
            equals = arg.find('=')
            if equals > 0 and arg.find(' ') > equals:
                arg = arg[:equals + 1] + '"' + arg[equals + 1:] + '"'
            else:
                arg = '"' + arg + '"'
        result.append(arg)
    return ' '.join(result)




def save(path, value):
    temp = path.with_suffix('.tmp-' + uuid.uuid4().hex)
    temp.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)


def relative(value):
    p = PurePosixPath(value.replace('\\', '/'))
    if p.is_absolute() or '..' in p.parts or ':' in str(p):
        raise ValueError('Unsafe inventory path: ' + value)
    return Path(*p.parts)


from verified_file_cache import verify_package as verify_package_files


def verify_package():
    return verify_package_files(ROOT)


def discover_game(explicit):
    if explicit:
        candidates = [Path(explicit).expanduser()]
    else:
        import winreg
        steam_roots = []
        for hive, key, name in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
                                (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath')):
            try:
                with winreg.OpenKey(hive, key) as k:
                    steam_roots.append(Path(winreg.QueryValueEx(k, name)[0]))
            except OSError:
                pass
        libraries = list(steam_roots)
        for root in steam_roots:
            f = root / 'steamapps/libraryfolders.vdf'
            if f.is_file():
                for value in re.findall(r'"path"\s+"((?:\\.|[^"\\])*)"', f.read_text(encoding='utf-8')):
                    libraries.append(Path(value.replace('\\\\', '\\')))
        candidates = []
        for library in libraries:
            manifest = library / 'steamapps/appmanifest_2687970.acf'
            if manifest.is_file():
                match = re.search(r'"installdir"\s+"([^"/\\]+)"', manifest.read_text(encoding='utf-8'))
                if match:
                    candidates.append(library / 'steamapps/common' / match[1])
    found = list(dict.fromkeys(p.resolve() for p in candidates if (p / REL / 'TribesRivals.exe').is_file()))
    if len(found) != 1:
        raise RuntimeError('Choose your original game folder with --game-dir "X:\\SteamLibrary\\steamapps\\common\\TRIBES 3 Rivals". Found: ' + str(found))
    return found[0]


def file_identity(path):
    st = path.stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def verify_game(source, workspace, force=False):
    manifest = json.loads((ROOT / 'base-game-manifest.json').read_text())
    cache = workspace / 'verified-source.json'
    key = digest(ROOT / 'base-game-manifest.json')
    identities = {x['path']: file_identity(source / relative(x['path'])) for x in manifest['files']}
    old = {}
    if cache.is_file() and not force:
        try:
            old = json.loads(cache.read_text())
        except (OSError, ValueError):
            pass
    reused = (old.get('source') == str(source) and old.get('manifest') == key
              and old.get('identities') == identities and 0 <= time.time() - old.get('verified_at', 0) < 86400)
    if not reused:
        print('Verifying original game files (9.2 GB). The first check may take several minutes.', flush=True)
        for n, item in enumerate(manifest['files']):
            path = source / relative(item['path'])
            if path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
                raise RuntimeError('Unsupported or changed game file: ' + item['path'])
            if file_identity(path) != identities[item['path']]:
                raise RuntimeError('Source changed while hashing: ' + item['path'])
            if n % 25 == 0:
                print(f'Verified {n + 1}/{len(manifest["files"])}', flush=True)
        save(cache, dict(source=str(source), manifest=key, identities=identities, verified_at=time.time()))
    if digest(source / REL / 'TribesRivals.exe') != manifest['executable_sha256']:
        raise RuntimeError('Game executable version differs from this playtest.')
    return manifest, identities, 'retained hashes with 24-hour file-identity/size/time guards' if reused else 'fresh SHA256'


def running_image(name):
    result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ' + name, '/FO', 'CSV', '/NH'], capture_output=True, text=True, check=True)
    return any(row and row[0].lower() == name.lower() for row in csv.reader(io.StringIO(result.stdout)))


def endpoint(value, bind=False):
    address, separator, port = value.rpartition(':')
    if not separator or not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError('Use an IPv4 address and port, for example 127.0.0.1:7787.')
    ip = ipaddress.IPv4Address(address)
    private = ip.is_loopback or ip in ipaddress.IPv4Network('10.0.0.0/8') or ip in ipaddress.IPv4Network('172.16.0.0/12') or ip in ipaddress.IPv4Network('192.168.0.0/16') or ip in ipaddress.IPv4Network('100.64.0.0/10')
    if not private or ip.is_unspecified or ip.is_multicast:
        raise ValueError('This experimental build supports loopback, LAN or Tailscale IPv4 addresses only.')
    if bind:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind((str(ip), int(port)))
    return str(ip), int(port)


def prepare(args):
    from startup_timing import StartupTiming
    startup = StartupTiming()
    with startup.phase('package-verification'):
        package = verify_package()
    definitions = declared_definitions(package)
    definition = definitions.get(args.map) or BY_ID.get(args.map)
    preview = args.authoring_preview
    if preview and (args.role != 'offline' or args.map in BY_ID or package.get('authoring_preview') != args.map
                    or not {f'lua/{args.map}/{name}' for name in ('offline.lua', 'standalone_bootstrap.lua')}
                    <= {row['path'] for row in package['files']}):
        raise ValueError('A map editor playtest requires its own inventoried offline draft.')
    if definition is None or (args.map not in BY_ID and args.role != "host" and not args.check and not preview):
        raise ValueError("The selected map is not available for this launch.")
    source = discover_game(args.game_dir)
    workspace = Path(args.workspace).resolve() if args.workspace else source.parent / 'TRIBES3 Community Sessions'
    if workspace == source or source in workspace.parents:
        raise ValueError('Session workspace must be outside the original game folder.')
    workspace.mkdir(parents=True, exist_ok=True)
    with startup.phase('original-verification'):
        manifest, identities, verification = verify_game(source, workspace, args.rehash)
    if args.check:
        print(json.dumps(dict(package=package['build_id'], maps=tuple(definitions), game=str(source), verification=verification, workspace=str(workspace)), indent=2))
        return
    if not running_image('steam.exe'):
        raise RuntimeError('Start Steam and sign in with your own account first.')
    if args.role == 'offline' and running_image('TribesRivals.exe'):
        raise RuntimeError('Close the current TRIBES game before starting an offline test.')
    from local_http_proxy import loopback_http_failure_preflight
    loopback_http_failure_preflight()
    bind_address = args.bind or json.loads((ROOT / 'server.json').read_text())['address']
    bind, port = endpoint(bind_address + ':' + str(args.port), bind=True) if args.role == 'host' else (None, 0)
    target = ':'.join(map(str, endpoint(args.connect))) if args.role == 'join' else ''
    run = workspace / (args.map + '-' + args.role + '-' + uuid.uuid4().hex[:12])
    game = run / 'game'
    game.mkdir(parents=True)
    print('Session: ' + str(run), flush=True)
    transport = dict(hardlinked_files=0, copied_files=0, copied_bytes=0)
    from bulk_asset_cache import BulkStageCache
    bulk_stage = BulkStageCache(workspace / 'asset-cache')
    package_members = {item['path']: item for item in package['files']}
    def stage_cooked_file(src, dst):
        selected = Path(src).absolute().relative_to(ROOT).as_posix()
        record = package_members.get(selected)
        if record is None or not selected.startswith('content/'):
            raise ValueError('Cooked staging input is outside the verified inventory.')
        return bulk_stage.stage(src, dst, expected_sha256=record['sha256'],
                                expected_bytes=record['bytes'])
    with startup.phase('original-staging'):
        for item in manifest['files']:
            rel = relative(item['path'])
            src, dst = source / rel, game / rel
            if file_identity(src) != identities[item['path']]:
                raise RuntimeError('Source changed after verification: ' + item['path'])
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Executable is always private. Bulk assets are never opened for writing.
            if rel == REL / 'TribesRivals.exe':
                shutil.copyfile(src, dst)
                mode = 'copy'
            else:
                mode = bulk_stage.stage(src, dst, expected_sha256=item['sha256'], expected_bytes=item['bytes'], expected_source_identity=identities[item['path']])
            transport['hardlinked_files' if mode == 'hardlink' else 'copied_files'] += 1
            if mode == 'copy':
                transport['copied_bytes'] += item['bytes']
    binary = game / REL
    exe = binary / 'TribesRivals.exe'
    if os.path.samefile(exe, source / REL / 'TribesRivals.exe') or digest(exe) != manifest['executable_sha256']:
        raise RuntimeError('Private executable copy verification failed.')
    patches = [(0x1a8b909, 'e8621439ff'), (0x1a8b933, 'e8381439ff'), (0x1a8b95d, 'e80e1439ff'), (0x1a8b987, 'e8e41339ff')]
    with exe.open('r+b') as f:
        for offset, before in patches:
            f.seek(offset)
            if f.read(5) != bytes.fromhex(before):
                raise RuntimeError('Unsupported private executable at ' + hex(offset))
        for offset, before in patches:
            f.seek(offset)
            f.write(b'\x90' * 5)
    with startup.phase('runtime-staging'):
        shutil.copytree(ROOT / 'runtime', binary, dirs_exist_ok=True)
    with startup.phase('map-staging'):
        shutil.copytree(ROOT / 'content', game / 'Topaz/Content', dirs_exist_ok=True, copy_function=stage_cooked_file)
    print('BULK_STAGE_SUMMARY|' + json.dumps(bulk_stage.snapshot(), separators=(',', ':')), flush=True)
    profile = run / 'profile'
    config = profile / 'Saved/Config/Windows/Engine.ini'
    config.parent.mkdir(parents=True)
    selected_map = definition.package
    map_definitions, objectives = runtime_paths(ROOT, run, definition)
    initial_map = '/Game/Maps/Active/Drydock/Arena_Drydock_P' if args.role == 'join' else selected_map
    config.write_text('[/Script/EngineSettings.GameMapsSettings]\nGameDefaultMap=' + initial_map + '\nGlobalDefaultGameMode=' + MODE + '\nLocalMapOptions=?game=' + MODE + ('?listen?bUseIPSockets' if args.role == 'host' else '') + '\n\n[/Script/OnlineSubsystemUtils.IpNetDriver]\nMaxPortCountToTry=0\n', encoding='utf-8')
    argv = [str(exe), 'Topaz', '-RallyHereEnv=Retail', '-EpicApp=TribesGameClientLive', '-storefront=Steam', '-NoEAC', '-NoEACLogin', '-offline', '-abslog=' + str(run / 'game.log'), '-UserDir=' + str(profile), '-nobots', '-REAResearchScope=' + uuid.uuid4().hex]
    if args.role != 'offline':
        argv += ['-httpproxy=127.0.0.1:1', '-DefaultPlatformService=NULL', '-nosteam', '-noSteamV2', '-noeos', '-Port=' + str(port)]
        if args.role == 'host':
            argv.append('-MULTIHOME=' + bind)
    argv += ['-nullrhi', '-nosound', '-unattended'] if args.headless or args.role == 'host' else ['-d3d11', '-windowed', '-ResX=1280', '-ResY=720']
    if args.role == 'host':
        argv.append('-server')
    mod = binary / 'ue4ss/Mods/CommunityLauncher/Scripts/main.lua'
    mod.parent.mkdir(parents=True)
    if (args.roster_template is None) != (args.roster_sha256 is None) or (args.roster_template and args.role != 'host'):
        raise ValueError('A host roster needs both its path and SHA256.')
    if args.roster_template:
        roster_path = Path(args.roster_template).resolve(strict=True)
        with roster_path.open('rb') as stream:
            raw = stream.read(1048577)
        if len(raw) > 1048576:
            raise ValueError('Roster template too large.')
        if hashlib.sha256(raw).hexdigest() != args.roster_sha256:
            raise ValueError('Roster template changed after allocation.')
        template = raw.decode('utf-8')
    else:
        template = (ROOT / 'lua' / args.map / 'offline.lua' if args.role == 'offline' else ROOT / 'lua/network.lua').read_text(encoding='utf-8')
    values = dict(EXPECTED=engine_command_line(argv[2:]), OUTPUT=(run / 'events.txt').as_posix(), ROLE='client' if args.role == 'join' else args.role, DESTINATION=target, BOOTSTRAP=(ROOT / 'lua' / args.map / 'standalone_bootstrap.lua').as_posix(), OBJECTIVES=objectives.as_posix(), SELF_DAMAGE=(ROOT / 'lua/self_damage.lua').as_posix(), ENERGY_REGEN=(ROOT / 'lua/energy_regen.lua').as_posix(), PAWN_LIFECYCLE=(ROOT / 'lua/pawn_lifecycle.lua').as_posix(), FLAG_MARKERS=(ROOT / 'lua/flag_markers.lua').as_posix(), JETPACK_INPUT=(ROOT / 'lua/jetpack_input.lua').as_posix(), FLAG_PROJECTION=(ROOT / 'lua/flag_projection.lua').as_posix(), ROTATION_SECONDS=args.rotation_seconds, MATCH_MAP=args.map)
    values.update(GEOMETRY=(ROOT/'lua/geometry_runtime.lua').as_posix(), LEVEL_SETTINGS=(ROOT/'lua/level_settings.lua').as_posix(), MAP_DEFINITIONS=map_definitions.as_posix(), SNOWBLIND_WEATHER=(ROOT/'lua/snowblind_weather.lua').as_posix(), FLAG_CAPTURE=(ROOT/'lua/flag_capture.lua').as_posix())
    for key, value in values.items():
        template = template.replace('@@' + key + '@@', json.dumps(value, ensure_ascii=False))
    if '@@' in template:
        raise RuntimeError('Unresolved runtime template.')
    mod.write_text(template, encoding='utf-8')
    (mod.parents[2] / 'mods.txt').write_text('CommunityLauncher : 1\n', encoding='utf-8')
    save(run / 'session.json', dict(build=package['build_id'], map=args.map, role=args.role, bind=bind, port=port, target=target, source=str(source), verification=verification, transport=transport, argv=argv, private_executable_sha256=digest(exe), original_game_modified=False, bots=False))
    print('Starting. Allow two to three minutes for teams and loadouts.', flush=True)
    if args.role == 'host':
        endpoint(bind + ':' + str(port), bind=True)
        print(f'Experimental private host: {bind}:{port}. Join from a separate launcher window. Close this window to stop this host.', flush=True)
    from owned_native_session import run_owned_job
    pointer = run / 'host-control.json'
    if args.role == 'host':
        save(pointer, dict(run=str(run), build=package['build_id']))
    monitor_stop = threading.Event()
    def monitor_adapter():
        path = run / 'events.txt'
        while not monitor_stop.wait(1):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding='utf-8', errors='replace') if path.stat().st_size <= 1048576 else 'RUNTIME_ERROR|Adapter output exceeded its bound'
                fatal = next((line for line in text.splitlines() if line.startswith(('RUNTIME_ERROR|', 'ERROR ', 'ENERGY_RECHARGE_ERROR|', 'JETPACK_INPUT_ERROR|', 'FLAG_MARKER_ERROR|', 'PAWN_LIFECYCLE_ERROR|', 'SELF_DAMAGE_ERROR|', 'FLAG_CAPTURE_ERROR|', 'SNOWFALL_ERROR|', 'PROJECTILE_LISTEN_COMPAT_ERROR|', 'TURRET_RECOVERY_ERROR|'))), None)
                if not fatal and args.role == 'host' and any(line.startswith('MATCH_FINISHED|') for line in text.splitlines()):
                    if not (run / 'cancel.json').exists():
                        save(run / 'cancel.json', dict(cancel=True, reason='The owned match completed.'))
                    return
                if fatal:
                    (run / 'adapter-error.txt').write_text(fatal[:2048], encoding='utf-8')
                    if not (run / 'cancel.json').exists():
                        save(run / 'cancel.json', dict(cancel=True, reason='Private adapter failed a runtime guard; see adapter-error.txt'))
                    print('The private game adapter stopped. See ' + str(run / 'adapter-error.txt'), flush=True)
                    return
            except OSError:
                continue
    monitor = threading.Thread(target=monitor_adapter, daemon=True)
    monitor.start()
    try:
        record = run_owned_job(argv, game, dict(os.environ, SteamAppId='2687970', SteamGameId='2687970'), run / 'execution', budget=args.seconds, cancellation_path=run / 'cancel.json')
    finally:
        monitor_stop.set()
        monitor.join(timeout=2)
        if args.role == 'host' and pointer.is_file():
            current = json.loads(pointer.read_text())
            if current.get('run') == str(run):
                pointer.unlink()
    print('Session ended: ' + record['status'], flush=True)
    if preview and (record.get('exit_code') != 0 or (run/'adapter-error.txt').is_file()):
        raise RuntimeError('Draft playtest stopped. See ' + str(run))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('offline', 'host', 'join'), nargs='?')
    parser.add_argument('--map', default='Damnation')
    parser.add_argument('--game-dir')
    parser.add_argument('--workspace')
    parser.add_argument('--connect', default=None)
    parser.add_argument('--bind', default=None)
    parser.add_argument('--roster-template')
    parser.add_argument('--roster-sha256')
    parser.add_argument('--port', type=int, default=7787)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--rehash', action='store_true')
    parser.add_argument('--headless', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--seconds', type=float, help=argparse.SUPPRESS)
    parser.add_argument('--rotation-seconds', type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument('--authoring-preview', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    local_path = ROOT / 'local-installation.json'
    if local_path.is_file():
        if local_path.stat().st_size > 4096:
            raise ValueError('Local installation settings exceed 4 KB.')
        local = json.loads(local_path.read_text(encoding='utf-8'))
        if not isinstance(local, dict) or set(local) - {'game_dir', 'workspace'} or not all(isinstance(value, str) and value for value in local.values()):
            raise ValueError('Invalid local installation settings.')
        args.game_dir = args.game_dir or local.get('game_dir')
        args.workspace = args.workspace or local.get('workspace')
    server = json.loads((ROOT / 'server.json').read_text())
    if args.connect is None:
        args.connect = server['address'] + ':' + str(server['port'])
    if args.role is None and not args.check:
        args.role = 'join'
        print('Joining TRIBES 3 Community Server at ' + args.connect, flush=True)
        print('Rotation: Damnation / HighOctane / Scarabrae / Stonehenge / Snowblind / Rollercoaster', flush=True)
    if args.game_dir is None:
        try:
            discover_game(None)
        except RuntimeError:
            if not args.check:
                args.game_dir = input('Original TRIBES 3 game folder: ').strip().strip('"')
            else:
                raise
    prepare(args)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print('Unable to start: ' + str(exc), file=sys.stderr)
        sys.exit(1)
