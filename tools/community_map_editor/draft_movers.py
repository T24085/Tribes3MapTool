"""Author self-running brush motion using UE stock runtime classes."""

import unreal

from vengeance_mover_plan import FRAMES_PER_SECOND


def place_mover(actors, draft, mesh_actor, source_index, moves, *, door_bump=None, linked=False, opens_once=False):
    name = f"LS_Mover_{source_index}"
    sequence = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, f"/Game/Community/{draft}/MoverTracks", unreal.LevelSequence, unreal.LevelSequenceFactoryNew())
    if not sequence:
        raise RuntimeError(f"Could not create the mover sequence {name}.")
    sequence.set_display_rate(unreal.FrameRate(FRAMES_PER_SECOND, 1))
    sequence.set_playback_start(0)
    sequence.set_playback_end(moves[-1][0])
    binding = sequence.add_possessable(mesh_actor)
    section = binding.add_track(unreal.MovieScene3DTransformTrack).add_section()
    section.set_range(0, moves[-1][0])
    channels = section.get_all_channels()
    if len(channels) != 9:
        raise RuntimeError("The mover requires the nine Unreal transform channels.")
    for frame, transform in moves:
        number = unreal.FrameNumber(frame)
        for channel, value in zip(channels, transform):
            channel.add_key(number, value, interpolation=unreal.MovieSceneKeyInterpolation.LINEAR)
    sequence_actor = actors.spawn_actor_from_class(unreal.ReplicatedLevelSequenceActor, mesh_actor.get_actor_location())
    if not sequence_actor:
        raise RuntimeError(f"Could not place the sequence actor {name}.")
    sequence_actor.set_sequence(sequence)
    looping = door_bump is None and not linked
    sequence_actor.set_editor_property("playback_settings", unreal.MovieSceneSequencePlaybackSettings(
        auto_play=looping, loop_count=unreal.MovieSceneSequenceLoopCount(-1 if looping else 0)))
    sequence_actor.set_editor_property("replicate_playback", True)
    sequence_actor.set_actor_label("Path · " + mesh_actor.get_actor_label())
    sequence_actor.set_folder_path("TRIBES Import/Moving brushes")
    sequence_actor.tags = [f"T3MoverSequence_{source_index}"]
    if opens_once and not unreal.CommunityMapMotionLibrary.configure_permanent_door(sequence_actor):
        raise RuntimeError(f"Could not retain the open endpoint for {name}.")
    if door_bump is not None:
        mesh_actor.static_mesh_component.set_notify_rigid_body_collision(True)
        if not unreal.CommunityMapMotionLibrary.bind_timed_door(mesh_actor, sequence_actor, door_bump == 0):
            raise RuntimeError(f"Could not bind the blocking hit event for {name}.")
    if not unreal.EditorAssetLibrary.save_loaded_asset(sequence, only_if_is_dirty=False):
        raise RuntimeError(f"Could not save the mover sequence {name}.")
    return sequence_actor
