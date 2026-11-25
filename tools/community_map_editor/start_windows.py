"""Open a Windows authoring workspace from the Map Tool distribution."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

from bootstrap import create_workspace, discover_engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    local = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    parser.add_argument('--workspace', type=Path, default=local / 'Tribes3MapTool',
                        help='Parent directory for the CommunityMapEditor workspace')
    parser.add_argument('--engine', type=Path, help='Unreal Engine 5.3 path when creating a workspace')
    parser.add_argument('--smoke', action='store_true', help=argparse.SUPPRESS)
    options = parser.parse_args()
    settings = options.workspace.resolve() / 'CommunityMapEditor' / 'local-settings.json'
    try:
        if not settings.is_file():
            engine = options.engine if options.engine is not None else discover_engine()
            settings = create_workspace(options.workspace, engine)
    except ImportError as error:
        print('Install Pillow with py -3.13 -m pip install Pillow before opening the Map Tool.', file=sys.stderr)
        print(error, file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    print(f'Map Tool workspace {settings.parent}', flush=True)
    command = [sys.executable, '-B', str(Path(__file__).with_name('launch.py')), str(settings)]
    if options.smoke:
        command.append('--smoke')
    return subprocess.call(command)


if __name__ == '__main__':
    raise SystemExit(main())
