"""Bake collision-free warning markers and a local cue into the draft level."""

from array import array
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import wave

import unreal

from perimeter import check_polygon_outline


TAG = "T3MissionWarning"
CUE = TAG + "Cue"
VERTEX = TAG + "Vertex"
FLOOR = TAG + "Floor"
CEILING = TAG + "Ceiling"
SOUND_NAME = "SW_MissionBoundary"


def import_cue(folder):
    rate = 24_000
    frames = array("h")
    for frequency in (520, 360):
        for frame in range(rate // 6):
            t = frame / rate
            gain = min(1, t * 120, (1 / 6 - t) * 24)
            frames.append(round(12_000 * gain * math.sin(2 * math.pi * frequency * t)))
        frames.extend([0] * (rate // 24))
    with TemporaryDirectory() as scratch:
        path = Path(scratch) / f"{SOUND_NAME}.wav"
        with wave.open(str(path), "wb") as output:
            output.setparams((1, 2, rate, 0, "NONE", ""))
            output.writeframes(frames.tobytes())
        task = unreal.AssetImportTask()
        task.set_editor_property("automated", True)
        task.set_editor_property("destination_path", folder)
        task.set_editor_property("filename", str(path))
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        assets = task.get_objects()
    if len(assets) != 1 or not isinstance(assets[0], unreal.SoundWave) or assets[0].get_name() != SOUND_NAME:
        raise ValueError("Unreal could not import the mission boundary warning tone.")
    if not unreal.EditorAssetLibrary.save_loaded_asset(assets[0]):
        raise ValueError("Unreal could not save the mission boundary warning tone.")
    return assets[0]


def place_warning(actors, draft, perimeter):
    points = perimeter.vertices
    check_polygon_outline(points, "Mission boundary warning")
    folder = f"/Game/Community/{draft}"
    if unreal.EditorAssetLibrary.does_asset_exist(f"{folder}/{SOUND_NAME}"):
        raise ValueError("The reserved mission boundary warning asset already exists.")

    def add(kind, pos, name, tags):
        actor = actors.spawn_actor_from_class(kind, unreal.Vector(*pos))
        if not actor:
            raise ValueError(f"Unreal could not place the mission warning {name}.")
        actor.set_actor_label(f"Mission warning · {name}")
        actor.set_folder_path("TRIBES CTF/Mission perimeter")
        actor.set_actor_enable_collision(False)
        actor.set_actor_hidden_in_game(True)
        actor.tags = [unreal.Name(tag) for tag in tags]
        return actor

    for index, (x, y) in enumerate(points, 1):
        add(unreal.TargetPoint, (x, y, 0), f"vertex {index:02d}", (TAG, f"{VERTEX}{index}"))
    for cap, height, enabled in ((FLOOR, perimeter.z_min, perimeter.floor),
                                 (CEILING, perimeter.z_max, perimeter.ceiling)):
        if enabled:
            add(unreal.TargetPoint, (*points[0], height), cap[len(TAG):].lower(), (TAG, cap))
    cue_tags = [CUE, f"{TAG}Count{len(points)}"]
    cue_tags.extend(tag for tag, active in ((FLOOR, perimeter.floor), (CEILING, perimeter.ceiling)) if active)
    cue = add(unreal.AmbientSound, (*points[0], 0), "local tone", cue_tags)
    component = cue.audio_component
    component.set_editor_property("auto_activate", False)
    component.set_editor_property("allow_spatialization", False)
    component.set_editor_property("sound", import_cue(folder))


def verify_warning(actors, draft):
    markers = [actor for actor in actors if actor.actor_has_tag(unreal.Name(TAG))]
    cues = [actor for actor in actors if actor.actor_has_tag(unreal.Name(CUE))]
    if not markers and not cues:
        return
    if len(cues) != 1 or not isinstance(cues[0], unreal.AmbientSound):
        raise ValueError("Mission warning needs one local tone actor.")
    cue, = cues
    component = cue.audio_component
    tags = list(map(str, cue.tags))
    caps = {tag for tag in (FLOOR, CEILING) if tag in tags}
    vertices = {}
    for actor in markers:
        keys = [tag for tag in map(str, actor.tags) if tag in (FLOOR, CEILING) or tag.startswith(VERTEX)]
        if len(keys) != 1 or keys[0] in vertices or not isinstance(actor, unreal.TargetPoint) or actor.get_actor_enable_collision():
            raise ValueError("Mission warning needs distinct collision-free vertices and caps.")
        vertices[keys[0]] = actor.get_actor_location().to_tuple()
    shape = {key: value[:2] for key, value in vertices.items() if key not in (FLOOR, CEILING)}
    ordered = [f"{VERTEX}{index}" for index in range(1, len(shape)+1)]
    if ({key for key in vertices if key in (FLOOR, CEILING)} != caps or set(shape) != set(ordered)
            or [tag for tag in tags if tag.startswith(f"{TAG}Count")] != [f"{TAG}Count{len(shape)}"]):
        raise ValueError("Mission warning outline or caps are incomplete.")
    check_polygon_outline(tuple(shape[key] for key in ordered), "Mission boundary warning")
    if len(caps) == 2 and vertices[FLOOR][2] >= vertices[CEILING][2]:
        raise ValueError("Mission warning floor must be below its ceiling.")
    sound = component.get_editor_property("sound")
    if (not isinstance(sound, unreal.SoundWave) or sound.get_path_name() != f"/Game/Community/{draft}/{SOUND_NAME}.{SOUND_NAME}"
            or component.get_editor_property("auto_activate") or component.get_editor_property("allow_spatialization")):
        raise ValueError("Mission warning tone must be local and start only on crossing.")
