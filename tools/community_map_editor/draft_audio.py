"""Build supported mission emitters using stock Unreal audio assets."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import wave

import unreal

from intake_contract import bounded
from mission_audio import MAX_WAVE_BYTES


ACTOR_TAG = "T3MissionAudio_"


def import_wave(folder, source, clip, looping):
    name = "SW_Ambient_" + clip["source_sha256"][:32] + ("_Loop" if looping else "")
    path = (Path(source) / clip["file"]).resolve(strict=True)
    if path.parent != Path(source).resolve():
        raise ValueError("Mission audio WAV is outside the intake.")
    raw = bounded(path, MAX_WAVE_BYTES)
    if sha256(raw).hexdigest() != clip["source_sha256"]:
        raise ValueError("Mission audio WAV changed during draft creation.")
    with TemporaryDirectory(prefix="t3-mission-audio-") as scratch:
        path = Path(scratch) / (name + ".wav")
        if clip["pcm"]["sample_bits"] == 16:
            path.write_bytes(raw)
        else:
            # The Unreal import uses 16-bit PCM. The intake keeps the original.
            with wave.open(BytesIO(raw), "rb") as original:
                width = original.getsampwidth()
                samples = original.readframes(original.getnframes())
                if width == 1:
                    pcm16 = bytes(value for sample in samples for value in (0, sample ^ 0x80))
                else:
                    pcm16 = bytearray()
                    for start in range(width - 2, len(samples), width):
                        pcm16.extend(samples[start:start + 2])
                with wave.open(str(path), "wb") as converted:
                    converted.setparams((original.getnchannels(), 2, original.getframerate(), 0, "NONE", ""))
                    converted.writeframes(pcm16)
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
    if looping:
        sound.set_editor_property("virtualization_mode", unreal.VirtualizationMode.PLAY_WHEN_SILENT)
    if not unreal.EditorAssetLibrary.save_loaded_asset(sound, False):
        raise RuntimeError(f"Could not save mission sound {name}.")
    return sound


def place_audio(actors, draft, source, planned):
    folder = f"/Game/Community/{draft}/Audio"
    sounds, placed = {}, set()
    for emitter, clip in planned:
        settings = emitter["settings"]
        spatial = settings["spatial"]
        key = (clip["source_sha256"], settings["looping"])
        if key not in sounds:
            sounds[key] = import_wave(folder, source, clip, settings["looping"])
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
        component.set_editor_property("sound", sounds[key])
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
