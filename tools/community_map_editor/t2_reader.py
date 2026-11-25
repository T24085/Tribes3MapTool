"""Decode a T2 mission as data, without evaluating TorqueScript."""

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct
import tempfile
import zipfile

from intake_contract import (
    EQUIPMENT_FILE, INTERIOR_FILE, SHAPE_FILE, LATEST_INTAKE_VERSION, MAX_INTAKE_FILES,
    MAX_INTAKE_INTERIOR_BYTES, MAX_MANIFEST_BYTES, PLAYABLE_BOUNDS,
    bounded, intake_file_limit, intake_filename, load_intake, max_intake_files, validate_minimap,
)
from minimap import framing, grayscale_png, terrain_png
from mission_audio import AUDIO_INDEX, AUDIO_MEMBER, MAX_AUDIO_BYTES, MAX_CLIPS, collect_audio
from mission_equipment import mission_equipment
from mission_lighting import CALL_PREFIX, COMMAND_CLASS
from mission_weather import WEATHER_FILE, mission_weather
from model_textures import MAX_TEXTURE_BYTES, TEXTURE_FILE, TEXTURE_INDEX
from native_terrain_paint import MAX_PAINT_PNG_BYTES, PAINT_FILE
from sky_intake import MAX_SKY_BYTES, SKY_PNG
from source_profiles import (T2_ACOUSTIC_COLLISION_VERSION, T2_ACOUSTICS_VERSION,
                             T2_AUDIO_PROFILES_VERSION, T2_AUDIO_VERSION, T2_DIF_ANIMATION_VERSION,
                             T2_DIF_LOD_LIGHT_VERSION, T2_DIF_LOD_VERSION,
                             T2_GENERATOR_SCOPE_VERSION)
from interior_lighting import LIGHTING_INDEX, LIGHTMAP_FILE, MAX_LIGHTMAP_BYTES
from terrain_art import ART_FILE, MAX_ART_BYTES

PROFILE = Path(__file__).with_name("tribes2_profile.json")
LIMIT = 8 * 1024 * 1024
TOKEN = re.compile(
    r'\s+|//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|[\w.$:%/\-]+|.', re.DOTALL
)
IDENT = re.compile(r"[A-Za-z_]\w*")
READER = "t2-mission-v3.0"


def append_source_script(tokens, start, rows):
    depth = 0
    statement = []
    for token in tokens[start:]:
        statement.append(token)
        if token in ("(", "{", "["):
            depth += 1
        elif token in (")", "}", "]"):
            depth -= 1
        end_function = token == "}" and statement[0].casefold() == "function"
        if depth == 0 and (token == ";" or end_function):
            if statement == [";"]:
                statement.clear()
                continue
            text = " ".join(statement)
            source_class = COMMAND_CLASS if CALL_PREFIX.search(text) else "MissionStatement"
            rows.append(dict(id=len(rows) + 1, parent=None, name="", source_class=source_class,
                             properties={"statement": text[:4096]}))
            statement.clear()
    if statement:
        rows.append(dict(id=len(rows) + 1, parent=None, name="", source_class="MissionStatement",
                         properties={"statement": " ".join(statement)[:4096]}))


def parse_mission(raw, *, legacy=False):
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        source = raw.decode("cp1252")
    tokens = [
        m[0]
        for m in TOKEN.finditer(source)
        if not m[0].isspace() and not m[0].startswith(("//", "/*"))
    ]
    rows = []
    stack = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == ";":
            i += 1
            continue
        if token == "}" and stack:
            stack.pop()
            i += 1
            continue
        if legacy and token.casefold() == "instant" and i + 3 < len(tokens):
            delimiter = tokens[i + 3]
            if not IDENT.fullmatch(tokens[i + 1]) or not tokens[i + 2].startswith('"') or delimiter not in ("{", ";"):
                raise ValueError(f"Unsupported legacy mission object declaration at token {i}.")
            rows.append(dict(id=len(rows) + 1, parent=stack[-1]["id"] if stack else None,
                             name=tokens[i + 2][1:-1], source_class=tokens[i + 1], properties={}))
            if delimiter == "{":
                stack.append(rows[-1])
            i += 4
            continue
        if (
            not legacy and token.lower() == "new"
            and i + 4 < len(tokens)
            and IDENT.fullmatch(tokens[i + 1])
            and tokens[i + 2] == "("
        ):
            j = i + 3
            name = "" if tokens[j] == ")" else tokens[j].strip('"')
            if name:
                j += 1
            if tokens[j : j + 2] != [")", "{"]:
                raise ValueError(
                    f"Unsupported mission object declaration at token {i}."
                )
            rows.append(
                dict(
                    id=len(rows) + 1,
                    parent=stack[-1]["id"] if stack else None,
                    name=name,
                    source_class=tokens[i + 1],
                    properties={},
                )
            )
            stack.append(rows[-1])
            i = j + 2
            continue
        if legacy and not stack and (token.startswith("$") or tokens[i + 1:i + 2] == ["("]):
            start = i
            while i < len(tokens) and tokens[i] not in (";", "{", "}"):
                i += 1
            if i == len(tokens) or tokens[i] != ";":
                raise ValueError(f"Unsupported top-level mission statement at token {start}.")
            rows.append(dict(id=len(rows) + 1, parent=None, name=token, source_class="MissionStatement",
                             properties={"statement": " ".join(tokens[start:i])}))
            i += 1
            continue
        if not legacy and not stack and rows:
            # The mission object section is complete. Keep trailing script
            # as data so nested construction and calls cannot become objects.
            append_source_script(tokens, i, rows)
            break
        if not stack or not IDENT.fullmatch(token):
            raise ValueError(
                f"Unsupported mission statement at token {i}: {token[:60]}"
            )
        key = token
        i += 1
        if tokens[i : i + 1] == ["["] and tokens[i + 2 : i + 3] == ["]"]:
            key += "[" + tokens[i + 1] + "]"
            i += 3
        if tokens[i : i + 1] != ["="]:
            raise ValueError("Unsupported assignment for " + key)
        i += 1
        start = i
        while i < len(tokens) and tokens[i] not in (";", "{", "}"):
            i += 1
        if i == len(tokens) or tokens[i] != ";":
            raise ValueError("Incomplete assignment for " + key)
        value = " ".join(tokens[start:i])
        if len(tokens[start:i]) == 1 and value.startswith('"'):
            value = value[1:-1]
        if key in stack[-1]["properties"]:
            raise ValueError("Repeated mission property needs review: " + key)
        stack[-1]["properties"][key] = value
        i += 1
    if stack or not rows:
        raise ValueError("Incomplete mission object tree.")
    return rows


def vector(value, n):
    parts = [float(v) for v in value.split()]
    if len(parts) != n or not all(math.isfinite(v) for v in parts):
        raise ValueError("Invalid vector: " + value[:60])
    return parts


def convert_rows(rows, profile):
    scale = profile["centimeters_per_source_meter"]
    y_sign = -1 if profile["reflect_y"] else 1
    parents = {}
    terrains = []
    for row in rows:
        props = {k.lower(): v for k, v in row["properties"].items()}
        cls = row["source_class"].lower()
        team, modes = parents.get(row["parent"], (0, ""))
        if cls in profile["groups"] and row["name"].lower() in ("team1", "team2"):
            team = int(row["name"][-1])
        if props.get("team") in ("1", "2"):
            team = int(props["team"])
        modes = props.get("missiontypeslist", modes)
        parents[row["id"]] = (team, modes)
        row.update(
            status="review", reason="No conversion rule for " + row["source_class"]
        )
        rules = profile["markers"].get(cls, {})
        kind = rules.get(props.get("datablock", "").lower(), rules.get("*"))
        if cls in profile["groups"]:
            row.update(status="group", reason="Hierarchy retained")
        elif row["source_class"] == COMMAND_CLASS:
            row.update(status="review", reason="Source interior light command.")
        elif not row["parent"] and row["source_class"] == "MissionStatement":
            row.update(status="review", reason="Source script retained for review.")
        elif modes and "ctf" not in re.findall(r"\w+", modes.lower()):
            row.update(status="excluded", reason="Other game mode: " + modes)
        elif cls == profile["terrain_class"]:
            terrains.append(props)
            row.update(status="terrain", reason="Heightfield and holes")
        elif kind:
            if not team or "position" not in props:
                row["reason"] = "Missing team or position."
                continue
            try:
                x, y, z = vector(props["position"], 3)
                ax, ay, az, angle = vector(props.get("rotation", "0 0 1 0"), 4)
                if max(abs(x), abs(y), abs(z)) * scale > 2e6:
                    raise ValueError("Position is outside the supported draft area.")
                length = math.hypot(ax, ay, az)
                if math.remainder(angle, 360) and not length:
                    raise ValueError("Rotation needs a nonzero axis.")
            except (ValueError, TypeError, AttributeError) as exc:
                row["reason"] = f"Invalid {kind} transform. {exc}"
                continue
            # Planar heading of the transformed forward vector, including tilted markers.
            if length:
                ax, ay, az = ax / length, ay / length, az / length
            c = math.cos(math.radians(angle))
            s = math.sin(math.radians(angle))
            yaw = (
                math.degrees(
                    math.atan2(az * s - ax * ay * (1 - c), c + ax * ax * (1 - c))
                )
                if length
                else 0
            )
            row.update(
                status="marker",
                reason="CTF " + kind,
                kind=kind,
                team=team,
                position_cm=[x * scale, y_sign * y * scale, z * scale],
                yaw=-y_sign * yaw,
            )
    if len(terrains) != 1:
        raise ValueError("The first T2 profile supports one terrain block per mission.")
    return terrains[0]


def mission_environment(rows, profile):
    result = {}
    units = profile["centimeters_per_source_meter"]
    y_sign = -1 if profile["reflect_y"] else 1
    for kind, rule in profile["environment"].items():
        source_class = rule["class"]
        matches = [row for row in rows
                   if row["status"] == "review" and row["source_class"].lower() == source_class]
        if len(matches) > 1:
            for row in matches:
                row["reason"] = f"Multiple {source_class} objects. Review before choosing one."
            continue
        if not matches:
            continue
        row = matches[0]
        properties = {key.lower(): value for key, value in row["properties"].items()}
        mapped, invalid = {}, []
        for field, original in rule.items():
            if field == "class" or original not in properties:
                continue
            raw = properties[original]
            try:
                if field == "direction":
                    x, y, z = vector(raw, 3)
                    length = math.hypot(x, y, z)
                    if not math.isfinite(length) or length == 0:
                        raise ValueError("Invalid sun direction.")
                    value = [x/length, y_sign*y/length, z/length]
                elif field in ("color", "ambient"):
                    channels = [float(item) for item in raw.split()]
                    if len(channels) not in (3, 4) or not all(0 <= item <= 1 for item in channels):
                        raise ValueError("Invalid RGB color.")
                    value = channels[:3]
                else:
                    value = float(raw) * units
                    if not 0 <= value <= 2e6:
                        raise ValueError("Invalid distance.")
            except ValueError:
                invalid.append(original)
            else:
                mapped[field] = value
        if kind == "fog" and "start_cm" not in mapped:
            mapped = {}
        if mapped:
            result[kind] = mapped
            row["reason"] = f"{kind.title()} mapped to draft lighting. Review remaining properties."
        else:
            row["reason"] = f"No supported {kind} settings."
        if invalid:
            row["reason"] += " Invalid source fields: " + ", ".join(invalid) + "."
    return result


def mission_minimap(rows, profile):
    candidates = [
        row for row in rows
        if row["source_class"].casefold() == "missionarea" and row["status"] != "excluded"
    ]
    if len(candidates) != 1:
        for row in candidates:
            row["reason"] = "Multiple mission areas need review before framing the minimap"
        return None
    row = candidates[0]
    scale = profile["centimeters_per_source_meter"]
    y_sign = -1 if profile["reflect_y"] else 1
    try:
        properties = {key.casefold(): value for key, value in row["properties"].items()}
        x, y, width, height = (int(value) for value in properties.get("area", "").split())
        if width <= 0 or height <= 0:
            raise ValueError("Nonpositive playable area")
        bottom, top = sorted((y * scale * y_sign, (y + height) * scale * y_sign))
        bounds = [[x * scale, bottom], [(x + width) * scale, top]]
        if any(abs(value) > 10_000_000 for corner in bounds for value in corner):
            raise ValueError("Playable boundary exceeds the host coordinate limits")
        settings = framing(bounds)
        validate_minimap(settings)
    except ValueError:
        row["reason"] = "Missing or invalid MissionArea rectangle"
        return None
    row.update(status="boundary", reason="Playable area for minimap framing")
    row["properties"][PLAYABLE_BOUNDS] = bounds
    return settings


def terrain_authoring(raw, start):
    # Version 3 appends two little-endian length-prefixed authoring strings.
    authoring = {"source_version": 3}
    for field in ("texture_rules", "terraformer_rules"):
        if len(raw) - start < 4:
            raise ValueError("Truncated T2 terrain authoring length.")
        length, = struct.unpack_from("<I", raw, start)
        start += 4
        end = start + length
        if end > len(raw):
            raise ValueError("Truncated T2 terrain authoring data.")
        # Preserve the original bytes as data, including legacy characters.
        authoring[field] = raw[start:end].decode("latin-1")
        start = end
    if start != len(raw):
        raise ValueError("Unexpected data after T2 terrain authoring rules.")
    return authoring


def terrain_files(raw, props, profile):
    scale = profile["centimeters_per_source_meter"]
    y_sign = -1 if profile["reflect_y"] else 1
    if raw[:1] not in (b"\x02", b"\x03") or len(raw) < 196617:
        raise ValueError("Expected a complete T2 version 2 or 3 terrain.")
    if (
        vector(props.get("scale", "1 1 1"), 3) != [1, 1, 1]
        or vector(props.get("rotation", "0 0 1 0"), 4)[3] != 0
    ):
        raise ValueError("Scaled or rotated terrain needs its own placement rule.")
    heights = struct.unpack_from("<65536H", raw, 1)
    files = {
        "height.png": grayscale_png(struct.pack(">65536H", *heights), 16),
        "material-index.png": grayscale_png(raw[131073:196609], 8),
    }
    pos = 196609
    layers = []
    for slot in range(8):
        if pos >= len(raw):
            raise ValueError("Truncated material table.")
        n = raw[pos]
        pos += 1
        if pos + n > len(raw):
            raise ValueError("Truncated material name.")
        name = raw[pos : pos + n].rstrip(b"\0").decode("cp1252")
        pos += n
        if name:
            layers.append((slot, name))
    for slot, name in layers:
        layer = raw[pos : pos + 65536]
        pos += 65536
        if len(layer) != 65536:
            raise ValueError("Truncated terrain layer " + name)
        files[f"material-{slot}.png"] = grayscale_png(layer, 8)
    authoring = terrain_authoring(raw, pos) if raw[0] == 3 else None
    if authoring is None and pos != len(raw):
        raise ValueError("Unsupported trailing terrain data.")
    holes = set()
    for run in props.get("emptysquares", "").split():
        code = int(run)
        start = code & 65535
        end = start + (code >> 16)
        if code < 0 or end > 65536:
            raise ValueError("Invalid terrain hole run.")
        holes.update(range(start, end))
    x, y, z = vector(props["position"], 3)
    step = float(props["squaresize"]) * scale
    if not 0 < step <= 20000:
        raise ValueError("Invalid terrain spacing.")
    terrain = dict(
        height_m=[v / 32 for v in heights],
        holes=sorted(holes),
        step_cm=step,
        cm_per_m=scale,
        y_sign=y_sign,
        origin_cm=[x * scale, y_sign * y * scale, z * scale],
        material_layers=layers,
    )
    if authoring is not None:
        terrain["authoring"] = authoring
    files["terrain.json"] = json.dumps(terrain).encode()
    return files


def members(archive, suffix):
    return sorted(
        i.filename
        for i in archive.infolist()
        if not i.is_dir() and i.filename.lower().endswith(suffix)
    )


def read_member(archive, name):
    matches = [i for i in archive.infolist() if i.filename == name]
    if len(matches) != 1 or matches[0].file_size > LIMIT:
        raise ValueError("Ambiguous or oversized archive member.")
    with archive.open(matches[0]) as stream:
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("Oversized archive member.")
    return data


def convert(source, output, mission=None, terrain=None, *, dif_decoder=None, dif_assets=None,
            dts_decoder=None, dts_assets=None, sky_assets=None, audio_assets=None):
    source = Path(source)
    output = Path(output)
    profile_bytes = PROFILE.read_bytes()
    profile = json.loads(profile_bytes)
    archive = source.suffix.lower() == ".vl2"
    if not archive and source.suffix.lower() != ".mis":
        raise ValueError("Choose a .mis or .vl2 file.")
    if archive:
        with zipfile.ZipFile(source) as z:
            choices = members(z, ".mis")
            if mission is None and len(choices) == 1:
                mission = choices[0]
            if mission not in choices:
                raise ValueError("Select one mission from the archive.")
            raw = read_member(z, mission)
    else:
        mission = source.name
        raw = bounded(source, LIMIT)
    rows = parse_mission(raw)
    props = convert_rows(rows, profile)
    basename = PurePosixPath(props["terrainfile"].replace("\\", "/")).name
    if not basename.lower().endswith(".ter") or ":" in basename:
        raise ValueError("Invalid terrain reference.")
    origin = basename
    if terrain is not None:
        terrain = Path(terrain)
        if terrain.name.casefold() != basename.casefold():
            raise ValueError("Mission expects " + basename)
        ground = bounded(terrain, LIMIT)
    elif archive:
        with zipfile.ZipFile(source) as z:
            choices = [
                n
                for n in members(z, ".ter")
                if PurePosixPath(n).name.casefold() == basename.casefold()
            ]
            if len(choices) != 1:
                raise FileNotFoundError("Select the terrain file " + basename)
            origin = choices[0]
            ground = read_member(z, origin)
    else:
        candidates = [
            source.parent / basename,
            source.parent.parent / "terrains" / basename,
        ]
        found = next((p for p in candidates if p.is_file()), None)
        if not found:
            raise FileNotFoundError("Select the terrain file " + basename)
        ground = bounded(found, LIMIT)
    files = terrain_files(ground, props, profile)
    terrain_data = json.loads(files["terrain.json"])
    settings = mission_minimap(rows, profile)
    if settings is not None:
        files["minimap.png"] = terrain_png(terrain_data, settings["image_bounds"])
    environment = mission_environment(rows, profile)
    files["objects.json"] = json.dumps(rows, ensure_ascii=False).encode()
    sha = lambda data: hashlib.sha256(data).hexdigest()
    header = dict(
        format=3 if environment else 2,
        profile=profile["id"],
        reader=READER,
        source_game="Tribes 2",
        name=PurePosixPath(mission).stem,
        profile_sha256=sha(profile_bytes),
        sources=[
            dict(name=mission, origin=source.name, sha256=sha(raw)),
            dict(
                name=origin,
                origin=source.name if archive and terrain is None else basename,
                sha256=sha(ground),
            ),
        ],
        files={n: sha(data) for n, data in files.items()},
        coverage=dict(Counter(r["status"] for r in rows)),
    )
    if settings is not None:
        header["minimap"] = settings
    if environment:
        header["environment"] = environment
    audio_root = audio_assets or dif_assets or dts_assets
    audio, receipts, inherited = collect_audio(
        rows, terrain_data, source.resolve(), Path(audio_root).resolve() if audio_root else None)
    acoustic_version = None
    if dif_decoder is not None:
        from mission_interiors import collect_interiors

        emitters = json.loads(audio[AUDIO_INDEX])['emitters'] if AUDIO_INDEX in audio else ()
        outside_audio = any(emitter['settings'].get('outside_only') is True for emitter in emitters)
        found, review, geometry = collect_interiors(
            rows, source.resolve(), Path(dif_decoder), Path(dif_assets).resolve() if dif_assets else None,
            include_acoustics=outside_audio)
        if outside_audio:
            for name in found.values():
                acoustic = json.loads(geometry[name]).get('acoustics')
                if acoustic:
                    acoustic_version = T2_ACOUSTICS_VERSION
                    if 'collision' in acoustic:
                        acoustic_version = T2_ACOUSTIC_COLLISION_VERSION
                        break
        if found or review:
            header.update(format=4, reader="t2-mission-v4.0", interiors=found, interior_review=review)
            files.update(geometry)
            header["files"].update({name: sha(data) for name, data in geometry.items()})
    if dts_decoder is not None:
        from mission_interiors import collect_shapes

        assets = dts_assets or dif_assets
        found, review, geometry = collect_shapes(
            rows, source.resolve(), Path(dts_decoder), Path(assets).resolve() if assets else None)
        if found or review:
            header.update(format=5, reader="t2-mission-v5.0", shapes=found, shape_review=review)
            files.update(geometry)
            header["files"].update({name: sha(data) for name, data in geometry.items()})
    if header.get("interiors") or header.get("shapes"):
        from mission_textures import collect_textures

        groups = ((header.get("interiors"), dif_assets), (header.get("shapes"), dts_assets or dif_assets))
        artwork = collect_textures(source.resolve(), files, [(refs, Path(root).resolve() if root else None)
                                                             for refs, root in groups])
        if artwork:
            header.update(format=6, reader="t2-mission-v6.0")
            files.update(artwork)
            header["files"].update({name: sha(data) for name, data in artwork.items()})
    equipment = mission_equipment(rows, terrain_data, generator_scopes=True)
    generator_scopes = equipment != mission_equipment(rows, terrain_data)
    if equipment["equipment"]:
        files[EQUIPMENT_FILE] = json.dumps(equipment, ensure_ascii=False).encode()
        header["files"][EQUIPMENT_FILE] = sha(files[EQUIPMENT_FILE])
        header.update(format=7, reader="t2-mission-v7.0")
    weather = mission_weather(rows, terrain_data)
    if weather["objects"]:
        files[WEATHER_FILE] = json.dumps(weather, ensure_ascii=False).encode()
        header["files"][WEATHER_FILE] = sha(files[WEATHER_FILE])
        header.update(format=8, reader="t2-mission-v8.0")
        from mission_sky_assets import collect_sky_artwork

        root = sky_assets or dif_assets or dts_assets
        artwork = collect_sky_artwork(source.resolve(), Path(root).resolve() if root else None, weather)
        if artwork:
            files.update(artwork)
            header["files"].update({name: sha(data) for name, data in artwork.items()})
            header.update(format=9, reader="t2-mission-v9.0")
    lighting = json.loads(files[LIGHTING_INDEX]) if LIGHTING_INDEX in files else {}
    if lighting:
        header.update(format=10, reader="t2-mission-v10.0")
    if any("surface_rects" in record.get("animation", {}) for record in lighting.values()):
        header.update(format=T2_DIF_ANIMATION_VERSION, reader="t2-mission-v45.0")
    elif any("#" in key for key in lighting):
        header.update(format=T2_DIF_LOD_LIGHT_VERSION, reader="t2-mission-v44.0")
    elif any('lods' in json.loads(files[name]) for name in header.get("interiors", {}).values()):
        header.update(format=T2_DIF_LOD_VERSION, reader="t2-mission-v43.0")
    if generator_scopes:
        header.update(format=T2_GENERATOR_SCOPE_VERSION, reader="t2-mission-v46.0")
    if audio:
        files.update(audio)
        header['files'].update({name: sha(data) for name, data in audio.items()})
        header['sources'].extend(receipts)
        version = acoustic_version or (T2_AUDIO_PROFILES_VERSION if inherited else T2_AUDIO_VERSION)
        header.update(format=version, reader=f't2-mission-v{version}.0')
    return publish_intake(header, files, output)


def publish_intake(header, files, output):
    manifest = json.dumps(header, sort_keys=True, indent=2).encode()
    output.mkdir(parents=True, exist_ok=True)
    target = output / (
        re.sub("[^A-Za-z0-9_-]", "_", header["name"])[:40] + "-" + hashlib.sha256(manifest).hexdigest()[:16]
    )
    if target.exists():
        if (target / "manifest.json").read_bytes() != manifest:
            raise ValueError("Existing intake manifest differs.")
        load_intake(target / "manifest.json")
        return target / "manifest.json"
    with tempfile.TemporaryDirectory(dir=output) as temp:
        stage = Path(temp) / "intake"
        stage.mkdir()
        for n, data in files.items():
            (stage / n).write_bytes(data)
        (stage / "manifest.json").write_bytes(manifest)
        load_intake(stage / "manifest.json")
        try:
            stage.rename(target)
        except OSError:
            if (
                not target.exists()
                or (target / "manifest.json").read_bytes() != manifest
            ):
                raise
            load_intake(target / "manifest.json")
    return target / "manifest.json"


def unpack_intake(source, library):
    """Read only inventoried flat members. Verify hashes before publishing the folder."""
    with zipfile.ZipFile(source) as archive:
        entries = [i for i in archive.infolist() if not i.is_dir()]
        manifests = [
            i for i in entries if PurePosixPath(i.filename).name == "manifest.json"
        ]
        if len(manifests) != 1 or len(entries) > 1 + MAX_INTAKE_FILES:
            raise ValueError("Choose a ZIP with one intake manifest.")
        entry = manifests[0]
        if entry.file_size > MAX_MANIFEST_BYTES:
            raise ValueError("Oversized intake manifest.")
        manifest = read_member(archive, entry.filename)
        header = json.loads(manifest)
        names = header["files"]
        version = header.get("format")
        if not isinstance(names, dict) or type(version) is not int or not 1 <= version <= LATEST_INTAKE_VERSION:
            raise ValueError("Invalid intake file list.")
        prefix = entry.filename[: -len("manifest.json")]
        if (
            set(i.filename for i in entries)
            != {prefix + n for n in ["manifest.json", *names]}
            or len(entries) != len(names) + 1
        ):
            raise ValueError("ZIP members do not match the manifest.")
        if any(not intake_filename(n, version) for n in names):
            raise ValueError("Unexpected intake member.")
        if sum(archive.getinfo(prefix + n).file_size for n in names if INTERIOR_FILE.fullmatch(n) or SHAPE_FILE.fullmatch(n)) > MAX_INTAKE_INTERIOR_BYTES:
            raise ValueError("Oversized intake model geometry.")
        if sum(archive.getinfo(prefix + n).file_size for n in names if TEXTURE_FILE.fullmatch(n)) > MAX_TEXTURE_BYTES:
            raise ValueError("Oversized intake textures.")
        if sum(archive.getinfo(prefix + n).file_size for n in names if SKY_PNG.fullmatch(n)) > MAX_SKY_BYTES:
            raise ValueError("Oversized intake sky artwork.")
        if sum(archive.getinfo(prefix + n).file_size for n in names if LIGHTMAP_FILE.fullmatch(n)) > MAX_LIGHTMAP_BYTES:
            raise ValueError("Oversized intake interior lightmaps.")
        if sum(archive.getinfo(prefix + n).file_size for n in names if ART_FILE.fullmatch(n)) > MAX_ART_BYTES:
            raise ValueError("Oversized intake terrain artwork.")
        if sum(archive.getinfo(prefix + n).file_size for n in names if PAINT_FILE.fullmatch(n)) > MAX_PAINT_PNG_BYTES:
            raise ValueError("Oversized native terrain paint files.")
        audio = [archive.getinfo(prefix + n) for n in names if AUDIO_MEMBER.fullmatch(n)]
        if len(audio) > MAX_CLIPS or sum(entry.file_size for entry in audio) > MAX_AUDIO_BYTES:
            raise ValueError("Oversized intake mission audio.")
        if len(names) > max_intake_files(version):
            raise ValueError("Oversized intake inventory.")
        library = Path(library)
        library.mkdir(parents=True, exist_ok=True)
        destination = library / ("intake-" + hashlib.sha256(manifest).hexdigest()[:16])
        with tempfile.TemporaryDirectory(dir=library) as tmp:
            stage = Path(tmp) / "intake"
            stage.mkdir()
            for n in names:
                info = archive.getinfo(prefix + n)
                if info.file_size > intake_file_limit(n, version):
                    raise ValueError("Oversized intake member.")
                (stage / n).write_bytes(archive.read(info))
            (stage / "manifest.json").write_bytes(manifest)
            load_intake(stage / "manifest.json")
            try:
                stage.rename(destination)
            except OSError:
                if not destination.is_dir():
                    raise
            if (destination / "manifest.json").read_bytes() != manifest:
                raise ValueError("Intake library collision.")
            load_intake(destination / "manifest.json")
        return destination / "manifest.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mission")
    parser.add_argument("--terrain", type=Path)
    parser.add_argument("--dif-decoder", type=Path)
    parser.add_argument("--dif-assets", type=Path, help="Tribes 2 content directory or asset VL2")
    parser.add_argument("--dts-decoder", type=Path, help="Installed io_scene_dts or dtslib folder")
    parser.add_argument("--dts-assets", type=Path, help="Tribes 2 content directory or asset VL2")
    parser.add_argument("--sky-assets", type=Path, help="Tribes 2 content directory or asset VL2")
    parser.add_argument("--audio-assets", type=Path, help="Tribes 2 content directory or VL2 with WAVs and sound profiles")
    args = parser.parse_args()
    print(convert(args.source, args.output, args.mission, args.terrain,
                  dif_decoder=args.dif_decoder, dif_assets=args.dif_assets,
                  dts_decoder=args.dts_decoder, dts_assets=args.dts_assets, sky_assets=args.sky_assets,
                  audio_assets=args.audio_assets))
