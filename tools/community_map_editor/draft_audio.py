"""Build supported mission emitters using stock Unreal audio assets."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

import unreal

from intake_contract import bounded
from mission_audio import MAX_WAVE_BYTES, repeat_schedule, write_pcm16


ACTOR_TAG = "T3MissionAudio_"


def schedule_key(schedule):
    return sha256(repr(schedule).encode("ascii")).hexdigest()[:16]


def import_wave(folder, source, clip, looping, *, layout=None, schedule=None):
    name = "SW_Ambient_" + clip["source_sha256"][:32]
    if layout is not None:
        name += "_Fixed_" + schedule_key(schedule)
    elif looping:
        name += "_Loop"
    path = (Path(source) / clip["file"]).resolve(strict=True)
    if path.parent != Path(source).resolve():
        raise ValueError("Mission audio WAV is outside the intake.")
    raw = bounded(path, MAX_WAVE_BYTES)
    if sha256(raw).hexdigest() != clip["source_sha256"]:
        raise ValueError("Mission audio WAV changed during draft creation.")
    with TemporaryDirectory(prefix="t3-mission-audio-") as scratch:
        path = Path(scratch) / (name + ".wav")
        write_pcm16(raw, path, layout=layout)
        task = unreal.AssetImportTask()
        task.set_editor_property("automated", True)
        task.set_editor_property("destination_path", folder)
        task.set_editor_property("filename", str(path))
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        imported = task.get_objects()
    if len(imported) != 1 or not isinstance(imported[0], unreal.SoundWave) or imported[0].get_name() != name:
        raise RuntimeError(f"Could not import mission sound {name}.")
    sound = imported[0]
    sound.set_editor_property("looping", looping)
    if layout is not None:
        sound.set_sound_asset_compression_type(unreal.SoundAssetCompressionType.PCM)
    if looping:
        sound.set_editor_property("virtualization_mode", unreal.VirtualizationMode.PLAY_WHEN_SILENT)
    if not unreal.EditorAssetLibrary.save_loaded_asset(sound, False):
        raise RuntimeError(f"Could not save mission sound {name}.")
    return sound


def repeat_cue(folder, wave_asset, schedule):
    count, minimum, maximum = schedule
    name = "SC_" + wave_asset.get_name() + "_" + schedule_key(schedule)
    cue = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, folder, unreal.SoundCue.static_class(), unreal.SoundCueFactoryNew())
    if cue is None or not unreal.CommunityMapMotionLibrary.configure_ambient_repeat(
            cue, wave_asset, count, minimum, maximum):
        raise RuntimeError(f"Could not build ambient repeat sound {name}.")
    if count == -1:
        cue.set_editor_property("virtualization_mode", unreal.VirtualizationMode.PLAY_WHEN_SILENT)
    if not unreal.EditorAssetLibrary.save_loaded_asset(cue, False):
        raise RuntimeError(f"Could not save ambient repeat sound {name}.")
    return cue


def place_audio(actors, draft, source, planned):
    folder = f"/Game/Community/{draft}/Audio"
    sounds, cues, placed = {}, {}, set()
    for emitter, clip, layout in planned:
        settings = emitter["settings"]
        spatial = settings["spatial"]
        schedule = repeat_schedule(settings)
        continuous = settings["looping"] and settings["loop_count"] == -1 and schedule is None
        key = (clip["source_sha256"], continuous, schedule if layout is not None else None)
        if key not in sounds:
            looping = layout["looping"] if layout is not None else continuous
            sounds[key] = import_wave(folder, source, clip, looping, layout=layout, schedule=schedule)
        sound = sounds[key]
        if schedule is not None and layout is None:
            cue_key = (key, schedule)
            if cue_key not in cues:
                cues[cue_key] = repeat_cue(folder, sound, schedule)
            sound = cues[cue_key]
        source_id = emitter["source_id"]
        actor = actors.spawn_actor_from_class(unreal.AmbientSound, unreal.Vector(*emitter["position_cm"]))
        if not actor:
            raise RuntimeError(f"Could not place mission audio emitter {source_id}.")
        actor.set_actor_label(f"Mission audio · {source_id}")
        actor.set_folder_path("TRIBES Import/Mission audio")
        actor.set_actor_enable_collision(False)
        actor.tags = [unreal.Name(f"{ACTOR_TAG}{source_id}")]
        component = actor.audio_component
        component.set_editor_property("auto_activate", True)
        component.set_editor_property("sound", sound)
        component.set_editor_property("volume_multiplier", settings["volume"])
        component.set_editor_property("allow_spatialization", spatial)
        if spatial:
            near, far = settings["min_distance_cm"], settings["max_distance_cm"]
            attenuation = unreal.SoundAttenuationSettings(
                attenuation_shape=unreal.AttenuationShape.SPHERE,
                distance_algorithm=unreal.AttenuationDistanceModel.LINEAR,
                attenuation_shape_extents=unreal.Vector(near, 0, 0),
                falloff_distance=far - near,
                attenuate=True, spatialize=True,
            )
            component.set_editor_property("override_attenuation", True)
            component.set_editor_property("attenuation_overrides", attenuation)
        placed.add(source_id)
    return placed


def placed_audio_ids(actors):
    placed = set()
    for actor in actors:
        if not isinstance(actor, unreal.AmbientSound) or not actor.audio_component.get_editor_property("sound"):
            continue
        for tag in map(str, actor.tags):
            if tag.startswith(ACTOR_TAG) and (source_id := tag[len(ACTOR_TAG):]).isascii() and source_id.isdecimal():
                placed.add(int(source_id))
    return placed
