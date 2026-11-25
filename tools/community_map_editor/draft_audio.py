"""Build supported mission emitters using stock Unreal audio assets."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

import unreal

from intake_contract import bounded
from mission_audio import MAX_WAVE_BYTES, directional_audio, repeat_schedule, write_pcm16
from outside_audio import OUTSIDE_TAG


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


def repeat_sound(folder, wave_asset, schedule, channels):
    count, minimum, maximum = schedule
    bridge = unreal.CommunityMapMotionLibrary
    use_meta = hasattr(bridge, "configure_ambient_meta_sound")
    suffix = wave_asset.get_name() + "_" + schedule_key(schedule)
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    if use_meta:
        name = "MS_" + suffix
        sound = tools.create_asset(name, folder, unreal.MetaSoundSource, unreal.MetaSoundSourceFactory())
        if sound is not None:
            output = unreal.MetaSoundOutputAudioFormat
            sound.set_editor_property("output_format", output.STEREO if channels == 2 else output.MONO)
        configure = bridge.configure_ambient_meta_sound
    else:
        name = "SC_" + suffix
        sound = tools.create_asset(name, folder, unreal.SoundCue, unreal.SoundCueFactoryNew())
        configure = bridge.configure_ambient_repeat
    if sound is None or not configure(sound, wave_asset, count, minimum, maximum):
        raise RuntimeError(f"Could not build ambient repeat sound {name}.")
    if count == -1:
        sound.set_editor_property("virtualization_mode", unreal.VirtualizationMode.PLAY_WHEN_SILENT)
    if not unreal.EditorAssetLibrary.save_loaded_asset(sound, False):
        raise RuntimeError(f"Could not save ambient repeat sound {name}.")
    return sound


def place_audio(actors, draft, source, planned):
    folder = f"/Game/Community/{draft}/Audio"
    sounds, repeats, placed = {}, {}, set()
    directional_class = None
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
            repeat_key = (key, schedule)
            if repeat_key not in repeats:
                repeats[repeat_key] = repeat_sound(folder, sound, schedule, clip["pcm"]["channels"])
            sound = repeats[repeat_key]
        source_id = emitter["source_id"]
        position = unreal.Vector(*emitter["position_cm"])
        directional = directional_audio(settings)
        outside = settings["outside_only"]
        orientation = (unreal.MathLibrary.make_rot_from_x(unreal.Vector(*settings["cone_direction"]))
                       if directional else unreal.Rotator())
        stock_cone = directional and not outside
        if stock_cone:
            bridge = unreal.CommunityMapMotionLibrary
            if directional_class is None:
                path = folder + "/BP_DirectionalAmbient"
                directional_class = bridge.build_directional_ambient_class(path)
                if directional_class is None or not unreal.EditorAssetLibrary.save_asset(path, False):
                    raise RuntimeError("Could not save the directional ambient sound Blueprint.")
            actor = actors.spawn_actor_from_class(directional_class, position, orientation)
        else:
            actor = actors.spawn_actor_from_class(unreal.AmbientSound, position, orientation)
        if not actor:
            raise RuntimeError(f"Could not place mission audio emitter {source_id}.")
        actor.set_actor_label(f"Mission audio · {source_id}")
        actor.set_folder_path("TRIBES Import/Mission audio")
        actor.set_actor_enable_collision(False)
        tags = [unreal.Name(f"{ACTOR_TAG}{source_id}")]
        if outside:
            tags.append(unreal.Name(f"{OUTSIDE_TAG}{source_id}"))
        actor.tags = tags
        component = actor.get_component_by_class(unreal.AudioComponent)
        if not component:
            raise RuntimeError(f"Mission audio emitter {source_id} is missing its audio component.")
        properties = dict(auto_activate=not stock_cone and not outside, sound=sound, volume_multiplier=settings["volume"],
                          allow_spatialization=spatial)
        if spatial:
            near, far = settings["min_distance_cm"], settings["max_distance_cm"]
            attenuation = unreal.SoundAttenuationSettings(
                attenuation_shape=unreal.AttenuationShape.SPHERE,
                distance_algorithm=unreal.AttenuationDistanceModel.LINEAR,
                attenuation_shape_extents=unreal.Vector(near, 0, 0),
                falloff_distance=far - near,
                attenuate=True, spatialize=True,
            )
            properties.update(override_attenuation=True, attenuation_overrides=attenuation)
        # Blueprint construction can replace the component after an editor
        # notification. Apply the complete set before that notification.
        component.set_editor_properties(properties)
        if stock_cone and not bridge.configure_directional_ambient(
            actor, settings["cone_inside_degrees"] / 2, settings["cone_outside_degrees"] / 2,
            settings["volume"], settings["volume"] * settings["cone_outside_volume"],
        ):
            raise RuntimeError(f"Could not configure directional audio on emitter {source_id}.")
        placed.add(source_id)
    return placed


def placed_audio_ids(actors):
    placed = set()
    for actor in actors:
        for tag in map(str, actor.tags):
            if tag.startswith(ACTOR_TAG) and (source_id := tag[len(ACTOR_TAG):]).isascii() and source_id.isdecimal():
                component = actor.get_component_by_class(unreal.AudioComponent)
                if component and component.get_editor_property("sound"):
                    placed.add(int(source_id))
                break
    return placed
