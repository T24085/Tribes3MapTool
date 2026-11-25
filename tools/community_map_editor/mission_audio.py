"""Preserve authored Tribes 2 emitter settings and reference-matched PCM audio."""

from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path, PurePosixPath
import re
import wave

AUDIO_INDEX = 'mission-audio.json'
AUDIO_MEMBER = re.compile(r'audio-[0-9a-f]{64}\.wav')
MAX_EMITTERS = 256
MAX_CLIPS = 64
MAX_WAVE_BYTES = 8 * 1024 * 1024
MAX_AUDIO_BYTES = 24 * 1024 * 1024
MAX_AUDIO_INDEX = 256 * 1024
UNRESOLVED = {'missing', 'ambiguous', 'unreadable', 'capacity'}


def wave_metadata(raw):
    if (not 44 <= len(raw) <= MAX_WAVE_BYTES or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE'
            or int.from_bytes(raw[4:8], 'little') + 8 != len(raw)):
        raise ValueError('Expected a complete bounded RIFF/WAVE file.')
    try:
        with wave.open(BytesIO(raw), 'rb') as audio:
            channels, width, rate, frames, compression, _ = audio.getparams()
            # Unreal can import the original PCM with mono spatialization or stereo playback.
            if (compression != 'NONE' or channels not in (1, 2) or width not in (1, 2, 3, 4)
                    or not 8000 <= rate <= 96000 or not 0 < frames <= 120 * rate):
                raise ValueError('Unsupported PCM channel count, sample rate, width or duration.')
            if len(audio.readframes(frames)) != frames * width * channels:
                raise ValueError('Truncated PCM samples.')
    except (EOFError, wave.Error) as error:
        raise ValueError('Unsupported or truncated WAV data.') from error
    return dict(channels=channels, sample_rate=rate, sample_bits=8 * width, frames=frames)


def clip_reference(raw):
    name = raw.strip().replace('\\', '/').casefold()
    path = PurePosixPath(name)
    if (not 1 <= len(name) <= 180 or path.as_posix() != name or path.is_absolute()
            or any(part in ('.', '..') or not re.fullmatch(r'[a-z0-9_ .-]+', part)
                   or part.endswith(('.', ' ')) for part in path.parts)
            or path.suffix != '.wav'):
        raise ValueError('Invalid relative WAV reference.')
    return name


def number(raw, low, high, *, integer=False):
    value = float(raw)
    if not low <= value <= high or integer and not value.is_integer():
        raise ValueError('Invalid authored audio value.')
    return int(value) if integer else value


def switch(raw):
    value = {'0': False, '1': True, 'false': False, 'true': True}.get(raw.strip().casefold())
    if value is None:
        raise ValueError('Invalid authored audio switch.')
    return value


def emitters(rows, terrain):
    from mesh_mappings import source_transform

    result = []
    scale = terrain['cm_per_m']
    y_sign = terrain['y_sign']
    for row in rows:
        if row['source_class'].casefold() != 'audioemitter' or row['status'] == 'excluded':
            continue
        if len(result) == MAX_EMITTERS:
            raise ValueError('Too many mission audio emitters for one intake.')
        fields = {key.casefold(): value for key, value in row['properties'].items() if isinstance(value, str)}
        settings, review = {}, []
        for source, key, parse in (
            ('filename', 'clip', clip_reference),
            ('volume', 'volume', lambda value: number(value, 0, 1)),
            ('islooping', 'looping', switch), ('is3d', 'spatial', switch),
            ('outsideambient', 'outside_only', switch), ('useprofiledescription', 'use_profile', switch),
            ('mindistance', 'min_distance_cm', lambda value: number(value, 0, 200000) * scale),
            ('maxdistance', 'max_distance_cm', lambda value: number(value, 0, 200000) * scale),
            ('minloopgap', 'min_gap_s', lambda value: number(value, 0, 600000, integer=True) / 1000),
            ('maxloopgap', 'max_gap_s', lambda value: number(value, 0, 600000, integer=True) / 1000),
            ('loopcount', 'loop_count', lambda value: number(value, -1, 10000, integer=True)),
            ('coneinsideangle', 'cone_inside_degrees', lambda value: number(value, 0, 360)),
            ('coneoutsideangle', 'cone_outside_degrees', lambda value: number(value, 0, 360)),
            ('coneoutsidevolume', 'cone_outside_volume', lambda value: number(value, 0, 1)),
        ):
            if source in fields:
                try:
                    settings[key] = parse(fields[source])
                except ValueError:
                    review.append(source)
        if 'conevector' in fields:
            try:
                x, y, z = (number(value, -1e6, 1e6) for value in fields['conevector'].split())
                length = math.hypot(x, y, z)
                if not length:
                    raise ValueError('Zero-length cone direction.')
                settings['cone_direction'] = [x / length, y_sign * y / length, z / length]
            except ValueError:
                review.append('conevector')
        for low, high in (('min_distance_cm', 'max_distance_cm'), ('min_gap_s', 'max_gap_s'),
                          ('cone_inside_degrees', 'cone_outside_degrees')):
            if settings.get(low, 0) > settings.get(high, math.inf):
                review.extend((low, high))
        transform = None
        try:
            position, _, _ = source_transform(fields, terrain)
            transform = list(position)
        except ValueError:
            review.append('position')
        if 'clip' not in settings and 'filename' not in review:
            review.append('filename')
        if settings.get('use_profile') or 'profile' in fields:
            # Profile inheritance needs literal datablock resolution before playback.
            review.append('profile')
        result.append(dict(source_id=row['id'], position_cm=transform, settings=settings, review=review))
    return result


def validate_audio_index(index, rows, terrain, wave_files, inventory, receipts):
    expected = emitters(rows, terrain)
    if (not isinstance(index, dict) or index.keys() != {'format', 'emitters', 'clips'}
            or type(index['format']) is not int or index['format'] != 1 or not expected
            or json.dumps(index['emitters'], sort_keys=True) != json.dumps(expected, sort_keys=True)):
        raise ValueError('Intake audio emitters do not match the mission.')
    clips = index['clips']
    requested = {row['settings']['clip'] for row in expected if 'clip' in row['settings']}
    if not isinstance(clips, dict) or clips.keys() != requested or len(wave_files) > MAX_CLIPS:
        raise ValueError('Intake audio clips do not match the mission references.')
    used = set()
    for reference, record in clips.items():
        if not isinstance(record, dict):
            raise ValueError('Invalid intake audio clip.')
        if record.keys() == {'reason'} and isinstance(record['reason'], str) and record['reason'] in UNRESOLVED:
            continue
        if record.keys() != {'file', 'source_sha256', 'origin', 'pcm'} or not isinstance(record['file'], str):
            raise ValueError('Invalid intake audio clip binding.')
        name = record['file']
        digest = inventory.get(name)
        if (name not in wave_files or name != f'audio-{digest}.wav' or record['source_sha256'] != digest
                or json.dumps(record['pcm'], sort_keys=True) != json.dumps(wave_files[name], sort_keys=True)):
            raise ValueError('Intake audio clip checksum or PCM layout differs.')
        origin = record['origin']
        if (not isinstance(origin, str) or not 1 <= len(origin) <= 260
                or any(character in origin for character in '/\\:')
                or not origin.casefold().endswith(('.wav', '.vl2'))
                or (reference, origin, digest) not in receipts):
            raise ValueError('Intake audio clip is missing its source receipt.')
        used.add(name)
    if used != wave_files.keys():
        raise ValueError('Intake contains unreferenced mission audio.')


def repeat_schedule(settings):
    if settings['looping'] and settings['loop_count'] != 0:
        schedule = settings['loop_count'], settings['min_gap_s'], settings['max_gap_s']
        if schedule != (-1, 0, 0):
            return schedule
    return None


def fixed_layout(schedule, pcm):
    if schedule is None or schedule[1] != schedule[2]:
        return None
    count, delay, _ = schedule
    gap_frames = round(delay * pcm['sample_rate'])
    looping = count == -1
    plays = 1 if looping else count + 1
    gaps = 1 if looping else count
    size = (pcm['frames'] * plays + gap_frames * gaps) * pcm['channels'] * 2 + 44
    if size > MAX_WAVE_BYTES:
        return None
    return dict(plays=plays, gap_frames=gap_frames, looping=looping, size=size)


def write_pcm16(raw, path, *, layout=None):
    """Write one clip or a fixed schedule. Keep timing in the sample data."""
    with wave.open(BytesIO(raw), 'rb') as original:
        width, channels, rate = original.getsampwidth(), original.getnchannels(), original.getframerate()
        if layout is None and width == 2:
            path.write_bytes(raw)
            return
        samples = original.readframes(original.getnframes())
    if width == 1:
        samples = bytes(value for sample in samples for value in (0, sample ^ 0x80))
    elif width > 2:
        pcm16 = bytearray()
        for start in range(width - 2, len(samples), width):
            pcm16.extend(samples[start:start + 2])
        samples = pcm16
    with wave.open(str(path), 'wb') as converted:
        converted.setparams((channels, 2, rate, 0, 'NONE', ''))
        if layout is None:
            converted.writeframes(samples)
        else:
            silence = bytes(layout['gap_frames'] * channels * 2)
            for play in range(layout['plays']):
                converted.writeframesraw(samples)
                if layout['looping'] or play + 1 < layout['plays']:
                    converted.writeframesraw(silence)


def playback_issue(emitter, clip, *, repeat_tools=False):
    settings = emitter['settings']
    if emitter['review'] or not clip or 'file' not in clip:
        return 'The source or clip needs review before playback.'
    if not {'volume', 'spatial', 'looping', 'outside_only', 'use_profile'} <= settings.keys():
        return 'Playback requires explicit gain, spatial, looping, outside, and profile settings.'
    if settings['outside_only']:
        return 'Outside-only audio needs a mapped indoor volume.'
    if settings['spatial']:
        low, high = settings.get('min_distance_cm'), settings.get('max_distance_cm')
        if low is None or high is None or high <= low:
            return 'Spatial playback needs a valid minimum and maximum distance.'
        if (settings.get('cone_inside_degrees'), settings.get('cone_outside_degrees')) != (360, 360):
            return 'Directional audio needs a cone mapping.'
        if clip['pcm']['channels'] != 1:
            return 'Spatial playback needs a mono WAV.'
    if settings['looping']:
        if 'loop_count' not in settings or (settings['loop_count'] != 0
                and not {'min_gap_s', 'max_gap_s'} <= settings.keys()):
            return 'Looping audio needs an explicit repeat count and delay bounds.'
        schedule = repeat_schedule(settings)
        if schedule is not None and not repeat_tools and fixed_layout(schedule, clip['pcm']) is None:
            return 'This repeat schedule requires the updated Community Map Motion editor plugin.'
    return None


def audio_playback(index, allowed, *, repeat_tools=False):
    if index is None:
        return []
    playable, fixed_assets, total_fixed_size = [], {}, 0
    for emitter in index['emitters']:
        if emitter['source_id'] in allowed:
            clip = index['clips'].get(emitter['settings'].get('clip'))
            if playback_issue(emitter, clip, repeat_tools=True) is not None:
                continue
            schedule = repeat_schedule(emitter['settings'])
            layout = None
            if schedule is not None:
                key = (clip['source_sha256'], schedule)
                if key not in fixed_assets:
                    layout = fixed_layout(schedule, clip['pcm'])
                    if layout is not None and total_fixed_size + layout['size'] <= MAX_AUDIO_BYTES:
                        total_fixed_size += layout['size']
                    else:
                        layout = None
                    fixed_assets[key] = layout
                layout = fixed_assets[key]
                if layout is None and not repeat_tools:
                    continue
            playable.append((emitter, clip, layout))
    return playable


def audio_review(index, placed=()):
    if index is None:
        return {}
    allowed = {row['source_id'] for row in index['emitters']}
    ready = {row['source_id'] for row, _, _ in audio_playback(index, allowed)}
    reasons = {
        'missing': 'The WAV was missing from the supplied content.',
        'ambiguous': 'Multiple WAVs matched the source reference.',
        'unreadable': 'The WAV could not be read as supported PCM.',
        'capacity': 'The intake audio budget was reached.',
    }
    results = {}
    for emitter in index['emitters']:
        clip = index['clips'].get(emitter['settings'].get('clip'))
        if clip is None:
            detail = 'The emitter needs a valid WAV reference.'
        elif 'reason' in clip:
            detail = reasons[clip['reason']]
        elif emitter['source_id'] in placed:
            detail = 'Mapped to an Unreal ambient sound actor in the saved draft.'
        else:
            detail = playback_issue(emitter, clip)
            if detail is None:
                if emitter['source_id'] in ready:
                    detail = 'Ready for ambient playback when the draft is built.'
                else:
                    detail = 'The fixed schedule needs the Community Map Motion plugin after the draft PCM budget is used.'
        if emitter['review']:
            detail += ' Review source fields: ' + ', '.join(emitter['review']) + '.'
        results[emitter['source_id']] = detail
    return results


def collect_audio(rows, terrain, source, asset_root):
    from zipfile import BadZipFile

    from mission_interiors import indexed_assets, pick_exact, read_asset

    placements = emitters(rows, terrain)
    if not placements:
        return {}, []
    requested = sorted({row['settings']['clip'] for row in placements if 'clip' in row['settings']})
    files, clips, receipts, total = {}, {}, [], 0
    if requested:
        with indexed_assets(source, asset_root, '.wav') as (primary, secondary, loose_roots):
            for reference in requested:
                try:
                    asset = pick_exact(reference, primary, secondary, loose_roots)
                except ValueError:
                    clips[reference] = {'reason': 'ambiguous'}
                    continue
                if asset is None:
                    clips[reference] = {'reason': 'missing'}
                    continue
                try:
                    raw, _ = read_asset(asset, limit=MAX_WAVE_BYTES)
                    metadata = wave_metadata(raw)
                except (ValueError, OSError, BadZipFile, KeyError, RuntimeError, NotImplementedError):
                    clips[reference] = {'reason': 'unreadable'}
                    continue
                digest = sha256(raw).hexdigest()
                member = f'audio-{digest}.wav'
                if member not in files:
                    if len(files) == MAX_CLIPS or total + len(raw) > MAX_AUDIO_BYTES:
                        clips[reference] = {'reason': 'capacity'}
                        continue
                    files[member] = raw
                    total += len(raw)
                if isinstance(asset, Path):
                    origin = asset.name
                else:
                    owner, _ = asset
                    origin = (owner if isinstance(owner, Path) else Path(owner.filename)).name
                clips[reference] = dict(file=member, source_sha256=digest, origin=origin, pcm=metadata)
                receipts.append(dict(name=reference, origin=origin, sha256=digest))
    files[AUDIO_INDEX] = json.dumps(dict(format=1, emitters=placements, clips=clips), ensure_ascii=False).encode()
    return files, receipts
