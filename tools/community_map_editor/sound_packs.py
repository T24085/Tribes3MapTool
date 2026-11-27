"""Build sound packs from a Tribes: Ascend client (Tribes3-Modded/Tribes3MapTool#7).

Follows the layered sound lookup of Tribes3-Modded/T3BackEndServer#90 (and #60, client #85):
every sound resolves through (1) the map's override, (2) the map's selected game pack,
(3) the default pack. Tribes: Ascend splits its sounds the same way:

* **Game pack** (``pack``): built once per game from the shared script and content packages
  (``.u`` / ``.upk``): weapons, armor, jetpack, flag, announcer, shared music.
* **Map override** (``map``): built per map from its levels (gameplay, ``_Ter``, ``_Sound``).
  It holds only sounds the game pack lacks (environment groups, map music) and every placed
  ambient sound, each referring to an event id with its position in T3 centimetres.

Layout (asset paths assume the cooked layout below; distances in the game pack stay in
source units, the map override converts positions and radii with the import scale)::

  <out>/<SET>/<Package>/<Group>/<Name>.ogg|.wav   game pack waves (original Ogg + 16-bit PCM WAV)
  <out>/<SET>/pack.json, SHA256SUMS.txt           -> /Game/Community/Liquids2026/<SET>/.../SW_<Name>
  <out>/maps/<MapId>/<Package>/<Group>/<Name>.*   map override waves
  <out>/maps/<MapId>/override.json, SHA256SUMS.txt -> /Game/Community/<AssetGroup>/Sounds/.../SW_<Name>

Event ids are sound cue object paths (``Package.Group.Name``); their category comes from the
``AUD_<CATEGORY>_`` package prefix. Decoding uses ffmpeg (``--ffmpeg``, then
T3_MAP_TOOL_FFMPEG, then PATH) or the optional ``soundfile`` package. Original game files
and extracted audio stay outside Git.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from source_profiles import UNITS_PER_METRE_DEFAULT
from unreal_binary_scene import property_tag, read_properties
from unreal_package_reader import Cursor, decode_chunk, open_package, table_name

PACK_FORMAT = 1
PACK_ROOT = "/Game/Community/Liquids2026"
SETS = ("T1", "T2", "TV", "TA", "T3", "CW")
SHARED_TYPES = (".u", ".upk")
OGG = b"OggS"
WAVE_BULKS = 4  # RawData, CompressedPCData, CompressedXbox360Data, CompressedPS3Data.
MAX_WAVE_BYTES = 256 * 1024 * 1024
MAX_GRAPH_DEPTH = 32
SAFE_PART = re.compile(r"[^\w.-]+")
CATEGORY = re.compile(r"AUD_([A-Za-z0-9]+)_", re.I)
MAP_ID = re.compile(r"[A-Za-z][\w-]{0,63}")
BULK_SEPARATE_FILE, BULK_ZLIB, BULK_LZO, BULK_UNUSED = 0x01, 0x02, 0x10, 0x20
AMBIENTS = {"ambientsound", "ambientsoundsimple", "ambientsoundnonloop", "ambientsoundsimpletoggleable",
            "ambientsoundmovable", "ambientsoundsimplespline", "ambientsoundspline"}


# ---------------------------------------------------------------------------------------------
# Decoders


def file_sha256(path):
    digest = sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FfmpegDecoder:
    name = "ffmpeg"

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise ValueError(f"ffmpeg was not found at {self.path}.")
        version = subprocess.run([str(self.path), "-hide_banner", "-version"], capture_output=True, text=True,
                                 timeout=60)
        decoders = subprocess.run([str(self.path), "-hide_banner", "-decoders"], capture_output=True, text=True,
                                  timeout=60)
        if version.returncode or not re.search(r"^\s*A\S*\s+vorbis\b", decoders.stdout, re.M):
            raise ValueError("This ffmpeg build cannot decode Vorbis audio.")
        self.version = version.stdout.splitlines()[0].strip()

    def receipt(self):
        return dict(name=self.name, version=self.version, sha256=file_sha256(self.path))

    def to_wav(self, ogg, wav):
        command = [str(self.path), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(ogg),
                   "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
                   "-c:a", "pcm_s16le", str(wav)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        if result.returncode or not wav.is_file():
            raise ValueError(f"ffmpeg could not decode {ogg.name}: {result.stderr.strip()[:200]}")


class SoundfileDecoder:
    name = "soundfile"

    def __init__(self):
        import soundfile  # Optional dependency, only needed for sound packs.

        self.module = soundfile
        self.version = f"soundfile {soundfile.__version__}, {soundfile.__libsndfile_version__}"

    def receipt(self):
        return dict(name=self.name, version=self.version)

    def to_wav(self, ogg, wav):
        data, rate = self.module.read(str(ogg), dtype="int16", always_2d=True)
        self.module.write(str(wav), data, rate, subtype="PCM_16", format="WAV")


def find_decoder(ffmpeg=None):
    """Return a Vorbis-to-WAV decoder: an explicit ffmpeg, T3_MAP_TOOL_FFMPEG, soundfile, then PATH."""
    for candidate in (ffmpeg, os.environ.get("T3_MAP_TOOL_FFMPEG")):
        if candidate:
            return FfmpegDecoder(candidate)
    try:
        return SoundfileDecoder()
    except ImportError:
        pass
    if found := shutil.which("ffmpeg"):
        return FfmpegDecoder(found)
    raise ValueError("Sound packs need a Vorbis decoder. Set the ffmpeg path (--ffmpeg or "
                     "T3_MAP_TOOL_FFMPEG) or install the optional soundfile package.")


# ---------------------------------------------------------------------------------------------
# Property values


def tags_of(export, report, stream, objects):
    tags, _, issues, end = read_properties(stream, report["summary"], export, report["names"], objects)
    if issues:
        raise ValueError("; ".join(issues)[:200])
    return tags, end


def object_path(index, objects):
    entry = objects.get(index) if index else None
    return entry["object_path"] if entry else None


def value_of(tag, report, stream, objects):
    """Decode simple tagged values: numbers, booleans, names, enums, object paths and vectors."""
    cursor = Cursor(stream, tag["value_offset"], tag["value_offset"] + tag["size"])
    kind = tag["kind"]
    if kind == "FloatProperty":
        return cursor.fields("f")[0]
    if kind == "IntProperty":
        return cursor.fields("i")[0]
    if kind == "BoolProperty":
        return bool(tag["boolean"])
    if kind in ("NameProperty", "ByteProperty") and tag["size"] == 8:
        return table_name(cursor, report["names"], True)
    if kind == "ByteProperty" and tag["size"] == 1:
        return cursor.fields("B")[0]
    if kind == "ObjectProperty":
        return object_path(cursor.fields("i")[0], objects)
    if kind == "StructProperty" and tag["structure"] == "Vector" and tag["size"] == 12:
        return list(cursor.fields("3f"))
    return None


def array_of(tag, stream, form):
    cursor = Cursor(stream, tag["value_offset"], tag["value_offset"] + tag["size"])
    return [cursor.fields(form)[0] for _ in range(cursor.count(4096))]


def struct_array(tag, report, stream, objects):
    """Read an array of tagged structs (for example AmbientSoundSlot) as dictionaries."""
    cursor = Cursor(stream, tag["value_offset"], tag["value_offset"] + tag["size"])
    rows = []
    for _ in range(cursor.count(4096)):
        row = {}
        while (inner := property_tag(cursor, report["names"], True)) is not None:
            if (value := value_of(inner, report, stream, objects)) is not None:
                row[inner["name"]] = value
            cursor.offset = inner["value_offset"] + inner["size"]
        rows.append(row)
    return rows


def simple_properties(tags, report, stream, objects, skip=()):
    return {tag["name"]: value for tag in tags if tag["name"] not in skip
            and (value := value_of(tag, report, stream, objects)) is not None}


# ---------------------------------------------------------------------------------------------
# Sound objects


def wave_payload(export, report, stream, objects):
    """Return (properties, Ogg bytes) for a SoundNodeWave, or raise with the reason."""
    tags, end = tags_of(export, report, stream, objects)
    properties = {key: value for key, value in simple_properties(tags, report, stream, objects).items()
                  if key in ("Duration", "NumChannels", "SampleRate", "Volume", "Pitch")}
    cursor = Cursor(stream, end, export["serial_offset"] + export["serial_bytes"])
    for _ in range(WAVE_BULKS):
        if cursor.limit - cursor.offset < 16:
            break
        flags, count, size, offset = cursor.fields("I3i")
        if flags & BULK_UNUSED or size <= 0:
            continue
        if flags & BULK_SEPARATE_FILE:
            raise ValueError("Audio is stored outside the package.")
        if offset != cursor.offset or size > cursor.limit - offset or not 0 < count <= MAX_WAVE_BYTES:
            raise ValueError("Invalid sound bulk data header.")
        codec = 2 if flags & BULK_LZO else 1 if flags & BULK_ZLIB else 0
        data = decode_chunk(stream, offset, size, count, codec) if codec else bytes(stream[offset:offset + size])
        cursor.offset += size
        if data[:4] == OGG:
            return properties, bytes(data)
    raise ValueError("No Ogg Vorbis payload in this sound.")


def node_graph(index, report, stream, objects, depth=0, seen=frozenset()):
    """Describe a sound node tree: kind, simple properties, wave slots and children."""
    entry = objects.get(index)
    if entry is None or depth > MAX_GRAPH_DEPTH or index in seen:
        return dict(kind="unresolved")
    kind = entry["class_name"]
    if kind == "SoundNodeWave":
        return dict(kind="wave", wave=entry["object_path"])
    if index < 0 or not entry["serial_bytes"]:
        return dict(kind=kind, object_path=entry["object_path"], note="Node is outside this package.")
    node = dict(kind=kind.removeprefix("SoundNode").lower() or kind, properties={}, children=[])
    tags, _ = tags_of(entry, report, stream, objects)
    for tag in tags:
        if tag["name"] == "ChildNodes" and tag["kind"] == "ArrayProperty":
            node["children"] = [node_graph(child, report, stream, objects, depth + 1, seen | {index}) if child
                                else None for child in array_of(tag, stream, "i")]
        elif tag["name"] == "Weights" and tag["kind"] == "ArrayProperty":
            node["properties"]["Weights"] = array_of(tag, stream, "f")
        elif tag["name"] == "SoundSlots" and tag["kind"] == "ArrayProperty":
            node["slots"] = [dict(wave=slot.get("Wave"), weight=slot.get("Weight", 1.0),
                                  pitch_scale=slot.get("PitchScale", 1.0), volume_scale=slot.get("VolumeScale", 1.0))
                             for slot in struct_array(tag, report, stream, objects)]
        elif (value := value_of(tag, report, stream, objects)) is not None:
            node["properties"][tag["name"]] = value
    return node


def category(path):
    match = CATEGORY.match(path.split(".", 1)[0])
    return match.group(1).lower() if match else "misc"


def event_record(export, report, stream, objects):
    tags, _ = tags_of(export, report, stream, objects)
    record = dict(id=export["object_path"], category=category(export["object_path"]), properties={}, graph=None)
    for tag in tags:
        if tag["name"] == "FirstNode" and tag["kind"] == "ObjectProperty":
            index = Cursor(stream, tag["value_offset"], tag["value_offset"] + 4).fields("i")[0]
            record["graph"] = node_graph(index, report, stream, objects) if index else None
        elif (value := value_of(tag, report, stream, objects)) is not None:
            record["properties"][tag["name"]] = value
    return record


def walk(node):
    if not node:
        return
    yield node
    for child in node.get("children", ()):
        yield from walk(child)


# ---------------------------------------------------------------------------------------------
# Extraction


def pack_parts(path):
    parts = [SAFE_PART.sub("_", part) for part in path.split(".") if part]
    if len(parts) < 2 or any(part in (".", "..") for part in parts):
        raise ValueError(f"Unusable sound object path {path}.")
    return parts


def relative_file(path, suffix):
    *folders, name = pack_parts(path)
    return Path(*folders, name + suffix)


def asset_path(root, path):
    *folders, name = pack_parts(path)
    return f"{root}/{'/'.join(folders)}/SW_{name}"


class Extraction:
    """Collect unique waves and events from packages into one folder, skipping known ones."""

    def __init__(self, folder, asset_root, known_waves=(), known_events=(), game="ascend"):
        self.folder, self.asset_root, self.game = Path(folder), asset_root, game
        self.folder.mkdir(parents=True, exist_ok=True)
        self.known_waves = {key.casefold() for key in known_waves}
        self.known_events = {key.casefold() for key in known_events}
        self.waves, self.events, self.sources, self.issues = {}, {}, {}, []

    def note(self, path, source, reason):
        self.issues.append(dict(object_path=path, source=source, reason=str(reason)[:240]))

    def read(self, package, report, stream, objects):
        used = False
        for export in report["exports"]:
            klass, path = export["class_name"], export["object_path"]
            key = path.casefold()
            try:
                if klass == "SoundNodeWave":
                    if key in self.known_waves:
                        continue
                    if key in self.waves:
                        self.waves[key]["copies"] += 1
                        continue
                    properties, data = wave_payload(export, report, stream, objects)
                    ogg = self.folder / relative_file(path, ".ogg")
                    ogg.parent.mkdir(parents=True, exist_ok=True)
                    ogg.write_bytes(data)
                    self.waves[key] = dict(object_path=path, source=package.name,
                                           ogg=ogg.relative_to(self.folder).as_posix(),
                                           asset=asset_path(self.asset_root, path), properties=properties, copies=1)
                    used = True
                elif klass == "SoundCue" and key not in self.known_events and key not in self.events:
                    if objects.get(export["outer"], {}).get("class_name", "").casefold() in AMBIENTS:
                        continue  # An AmbientSoundSimple's private cue: recorded with the placement instead.
                    self.events[key] = dict(event_record(export, report, stream, objects), source=package.name)
                    used = True
            except ValueError as error:
                self.note(path, package.name, error)
        if used:
            self.sources[package.name] = dict(name=package.name, sha256=report["source"]["sha256"])

    def decode(self, decoder, workers=None):
        def run(entry):
            ogg = self.folder / entry["ogg"]
            try:
                decoder.to_wav(ogg, ogg.with_suffix(".wav"))
                entry["wav"] = ogg.with_suffix(".wav").relative_to(self.folder).as_posix()
            except (OSError, ValueError, RuntimeError) as error:
                entry["decode_issue"] = str(error)[:240]

        with ThreadPoolExecutor(max_workers=workers or min(8, os.cpu_count() or 2)) as pool:
            list(pool.map(run, self.waves.values()))
        for entry in self.waves.values():
            if issue := entry.pop("decode_issue", None):
                self.note(entry["object_path"], entry["source"], issue)

    def link(self, assets):
        """Attach asset paths to waves named in event graphs and ambient slots."""
        for node in (node for event in self.events.values() for node in walk(event["graph"])):
            if node.get("kind") == "wave":
                node["asset"] = assets.get(node["wave"].casefold())
            for slot in node.get("slots", ()):
                slot["asset"] = assets.get((slot["wave"] or "").casefold())

    def assets(self):
        return {key: entry["asset"] for key, entry in self.waves.items() if "wav" in entry}


def write_sums(folder):
    folder = Path(folder)
    lines = [f"{file_sha256(path)}  {path.relative_to(folder).as_posix()}"
             for path in sorted(folder.rglob("*"), key=lambda item: item.as_posix().casefold())
             if path.is_file() and path.name != "SHA256SUMS.txt"]
    (folder / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def shared_packages(cooked):
    cooked = Path(cooked)
    if not cooked.is_dir():
        raise ValueError(f"{cooked} is not a folder. Choose the client's CookedPC folder.")
    rank = {suffix: order for order, suffix in enumerate(SHARED_TYPES)}
    files = [path for path in cooked.rglob("*") if path.suffix.casefold() in rank and path.is_file()
             and not path.is_symlink()]
    return sorted(files, key=lambda path: (rank[path.suffix.casefold()], str(path).casefold()))


def read_packages(extraction, packages):
    for package in packages:
        try:
            with open_package(package, extraction.game) as (report, stream):
                if any(row["class_name"] in ("SoundNodeWave", "SoundCue") for row in report["exports"]):
                    objects = {row["index"]: row for row in report["imports"] + report["exports"]}
                    extraction.read(package, report, stream, objects)
        except ValueError as error:
            extraction.note(None, package.name, f"Package not read. {error}")


def build_pack(cooked, output, sound_set="TA", *, decoder=None, game="ascend", workers=None):
    """Build the game pack from the client's shared packages into <output>/<sound_set>/."""
    if sound_set not in SETS:
        raise ValueError(f"Choose a sound set from {', '.join(SETS)}.")
    decoder = decoder or find_decoder()
    root = Path(output) / sound_set
    extraction = Extraction(root, f"{PACK_ROOT}/{sound_set}", game=game)
    read_packages(extraction, shared_packages(cooked))
    extraction.decode(decoder, workers)
    extraction.link(extraction.assets())
    waves = sorted(extraction.waves.values(), key=lambda row: row["object_path"].casefold())
    events = sorted(extraction.events.values(), key=lambda row: row["id"].casefold())
    pack = dict(format=PACK_FORMAT, kind="pack", set=sound_set, source_game=game,
                lookup="map override, then this pack, then the default pack (T3BackEndServer#90)",
                asset_root=f"{PACK_ROOT}/{sound_set}",
                wave_import="16-bit PCM WAV imported as SW_<Name>; the .ogg beside it is the unmodified original",
                units=dict(distance="source units", volume="multiplier", pitch="multiplier"),
                decoder=decoder.receipt(),
                sources=sorted(extraction.sources.values(), key=lambda row: row["name"].casefold()),
                categories=dict(sorted(count_categories(events).items())), waves=waves, events=events,
                issues=extraction.issues,
                coverage=dict(waves=len(waves), decoded=sum("wav" in row for row in waves),
                              events=len(events), issues=len(extraction.issues)))
    (root / "pack.json").write_text(json.dumps(pack, indent=1), encoding="utf-8")
    write_sums(root)
    return root / "pack.json"


def count_categories(events):
    counts = {}
    for event in events:
        counts[event["category"]] = counts.get(event["category"], 0) + 1
    return counts


# ---------------------------------------------------------------------------------------------
# Map override


def ambient_placements(report, stream, objects, cm_per_unit, assets, events):
    """Return every placed ambient sound with its event, position (T3 cm) and inline settings."""
    children = {}
    for export in report["exports"]:
        children.setdefault(export["outer"], []).append(export)
    placements, issues = [], []
    for actor in report["exports"]:
        if actor["class_name"].casefold() not in AMBIENTS:
            continue
        try:
            tags, _ = tags_of(actor, report, stream, objects)
            fields = {tag["name"]: tag for tag in tags}
            location = value_of(fields["Location"], report, stream, objects) if "Location" in fields else [0, 0, 0]
            row = dict(actor=actor["object_path"], kind=actor["class_name"],
                       position_cm=[round(value * cm_per_unit, 3) for value in location],
                       source_position=location)
            if "AmbientProperties" in fields:
                index = Cursor(stream, fields["AmbientProperties"]["value_offset"],
                               fields["AmbientProperties"]["value_offset"] + 4).fields("i")[0]
                node = node_graph(index, report, stream, objects)
                properties = node.get("properties", {})
                for key in ("RadiusMin", "RadiusMax", "LPFRadiusMin", "LPFRadiusMax"):
                    if key in properties:
                        properties[key + "_cm"] = round(properties[key] * cm_per_unit, 3)
                for slot in node.get("slots", ()):
                    slot["asset"] = assets.get((slot["wave"] or "").casefold())
                row["ambient"] = node
            else:
                for component in children.get(actor["index"], ()):
                    if component["class_name"] == "AudioComponent":
                        ctags, _ = tags_of(component, report, stream, objects)
                        cue = next((value_of(tag, report, stream, objects) for tag in ctags
                                    if tag["name"] == "SoundCue"), None)
                        row["event"] = cue
                        row["event_source"] = ("override" if (cue or "").casefold() in events
                                               else "pack or default" if cue else None)
            placements.append(row)
        except (KeyError, ValueError) as error:
            issues.append(dict(object_path=actor["object_path"], source=report["source"]["name"],
                               reason=str(error)[:240]))
    return placements, issues


def build_map_override(levels, pack_manifest, output, map_id, *, units_per_metre=None, asset_group=None,
                       decoder=None, game="ascend", workers=None):
    """Build a map's sound override: sounds its levels add over the game pack, and its ambients."""
    if not MAP_ID.fullmatch(map_id or ""):
        raise ValueError("Enter a map id of letters, digits, '_' or '-'.")
    pack = json.loads(Path(pack_manifest).read_text(encoding="utf-8"))
    units_per_metre = units_per_metre or UNITS_PER_METRE_DEFAULT[game]
    if not math.isfinite(units_per_metre) or units_per_metre <= 0:
        raise ValueError("Enter a positive source units per metre value.")
    cm_per_unit = 100 / units_per_metre
    decoder = decoder or find_decoder()
    root = Path(output) / "maps" / map_id
    asset_root = f"/Game/Community/{asset_group or map_id}/Sounds"
    extraction = Extraction(root, asset_root, (row["object_path"] for row in pack["waves"]),
                            (row["id"] for row in pack["events"]), game=game)
    levels = [Path(level) for level in levels]
    read_packages(extraction, levels)
    extraction.decode(decoder, workers)
    assets = {row["object_path"].casefold(): row["asset"] for row in pack["waves"]} | extraction.assets()
    extraction.link(assets)
    ambients = []
    for level in levels:
        with open_package(level, game) as (report, stream):
            objects = {row["index"]: row for row in report["imports"] + report["exports"]}
            rows, issues = ambient_placements(report, stream, objects, cm_per_unit, assets, extraction.events)
            for row in rows:
                row["level"] = level.name
            ambients.extend(rows)
            extraction.issues.extend(issues)
    events = sorted(extraction.events.values(), key=lambda row: row["id"].casefold())
    override = dict(format=PACK_FORMAT, kind="map-override", map_id=map_id, set=pack["set"], source_game=game,
                    lookup="this override, then the selected pack, then the default pack (T3BackEndServer#90)",
                    asset_root=asset_root, pack_asset_root=pack["asset_root"],
                    scale=dict(units_per_metre=units_per_metre, cm_per_source_unit=cm_per_unit),
                    units=dict(position="T3 cm (source values kept)", distance="source units, *_cm converted"),
                    decoder=decoder.receipt(),
                    sources=sorted(extraction.sources.values(), key=lambda row: row["name"].casefold())
                    + [dict(name=level.name, sha256=file_sha256(level)) for level in levels
                       if level.name not in extraction.sources],
                    music=[event["id"] for event in events if event["category"] == "mus"],
                    waves=sorted(extraction.waves.values(), key=lambda row: row["object_path"].casefold()),
                    events=events, ambients=ambients, issues=extraction.issues,
                    coverage=dict(waves=len(extraction.waves), decoded=sum("wav" in row for row in extraction.waves.values()),
                                  events=len(events), ambients=len(ambients), issues=len(extraction.issues)))
    root.mkdir(parents=True, exist_ok=True)
    (root / "override.json").write_text(json.dumps(override, indent=1), encoding="utf-8")
    write_sums(root)
    return root / "override.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("pack", help="Build the game pack from the client's shared packages.")
    pack.add_argument("cooked", type=Path, help="The client's CookedPC folder.")
    pack.add_argument("output", type=Path)
    pack.add_argument("--set", default="TA", choices=SETS)
    one = commands.add_parser("map", help="Build one map's override from its levels.")
    one.add_argument("pack", type=Path, help="The game pack's pack.json.")
    one.add_argument("output", type=Path)
    one.add_argument("map_id")
    one.add_argument("levels", type=Path, nargs="+", help="The map's level packages (gameplay, _Ter, _Sound).")
    one.add_argument("--units-per-metre", type=float, help="Import scale; defaults to the Ascend preset.")
    one.add_argument("--asset-group")
    for command in (pack, one):
        command.add_argument("--ffmpeg", type=Path, help="Path to ffmpeg.exe with Vorbis support.")
    arguments = parser.parse_args()
    try:
        decoder = find_decoder(arguments.ffmpeg)
        if arguments.command == "pack":
            manifest = build_pack(arguments.cooked, arguments.output, arguments.set, decoder=decoder)
        else:
            manifest = build_map_override(arguments.levels, arguments.pack, arguments.output, arguments.map_id,
                                          units_per_metre=arguments.units_per_metre,
                                          asset_group=arguments.asset_group, decoder=decoder)
        coverage = json.loads(manifest.read_text(encoding="utf-8"))["coverage"]
        print(f"{manifest} · {coverage}")
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
