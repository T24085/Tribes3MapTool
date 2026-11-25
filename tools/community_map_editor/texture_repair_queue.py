"""Run a locally selected PNG processor on the unfinished items in a draft batch."""

import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory

from texture_batches import original_png, replacement_png


FIELDS = re.compile(r'\{(source|output|width|height)\}')


def queue_command(executable, arguments):
    if not isinstance(executable, str) or not executable.strip():
        raise ValueError('Choose the installed texture processor executable.')
    program = Path(executable).expanduser().resolve(strict=True)
    if not program.is_file() or (os.name == 'nt' and program.suffix.lower() != '.exe'):
        raise ValueError('Choose a regular executable file. On Windows choose an .exe.')
    if (not isinstance(arguments, list) or not 1 <= len(arguments) <= 64
            or any(not isinstance(arg, str) or len(arg) > 2048 or '\0' in arg for arg in arguments)):
        raise ValueError('Enter arguments as a JSON array of strings.')
    fields = {match for arg in arguments for match in FIELDS.findall(arg)}
    if not {'source', 'output'} <= fields:
        raise ValueError('Include {source} and {output} in the arguments.')
    return program, tuple(arguments)


def pending_jobs(root, items):
    """Keep every existing replacement, including manual and rejected edits."""
    return [row for row in items.values()
            if not os.path.lexists(root / 'replacements' / (row['id'] + '.png'))]


def run_queue(root, jobs, command, *, stopped, report):
    lock_path = root / 'queue.lock'
    if lock_path.is_symlink():
        raise ValueError('The texture queue lock must be local to the batch.')
    with lock_path.open('a+b') as lock:
        try:
            if os.name == 'nt':
                import msvcrt

                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError('This batch already has an active queue, or its lock is unavailable.') from error
        return process_jobs(root, jobs, command, stopped=stopped, report=report)


def process_jobs(root, jobs, command, *, stopped, report):
    from PIL import Image

    program, arguments = command
    folder = root / 'replacements'
    if not folder.is_dir() or folder.resolve(strict=True) != folder:
        raise ValueError('The replacements folder must remain inside the texture batch.')
    results = {}
    for row in jobs:
        if stopped():
            break
        ident = row['id']
        dest = folder / (ident + '.png')
        report(f'Processing {dest.name}…')
        try:
            if os.path.lexists(dest):
                results[ident] = 'kept existing replacement'
                continue
            with TemporaryDirectory(prefix='processor-', dir=root) as work:
                work = Path(work)
                source, candidate = work / 'source.png', work / 'output.png'
                shutil.copyfile(original_png(root, row), source)
                width, height = row['target']
                values = dict(source=str(source), output=str(candidate), width=str(width), height=str(height))
                args = [str(program), *(FIELDS.sub(lambda match: values[match[1]], arg) for arg in arguments)]
                result = subprocess.run(args, cwd=program.parent, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                        timeout=1800, check=False)
                if result.returncode:
                    raise ValueError(f'The processor exited with code {result.returncode}.')
                replacement_png(row['source'], row['target'], candidate)
                with Image.open(candidate) as image:
                    image.verify()
                try:
                    # The temp directory is on the batch volume. The final link never replaces an edit.
                    os.link(candidate, dest)
                except FileExistsError:
                    results[ident] = 'kept existing replacement'
                else:
                    results[ident] = 'ready for review'
        except subprocess.TimeoutExpired:
            results[ident] = 'The processor exceeded 30 minutes.'
        except (OSError, ValueError, SyntaxError) as error:
            results[ident] = str(error)
        finally:
            report(f'{dest.name} · {results.get(ident, "interrupted")}')
    return results
