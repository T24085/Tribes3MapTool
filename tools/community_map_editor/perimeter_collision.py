"""Persist and check the native collision channels selected for solid perimeters."""

TAG = "T3MissionPerimeter"
FILTER = TAG + "Filtered"
CHANNELS = {"pawn": ("Pawn", "ECC_PAWN"), "vehicle": ("Vehicle", "ECC_VEHICLE")}
STOCK_IGNORED = ("ECC_WORLD_STATIC", "ECC_WORLD_DYNAMIC", "ECC_VISIBILITY",
                 "ECC_CAMERA", "ECC_PHYSICS_BODY", "ECC_DESTRUCTIBLE")


def validate_channels(selection):
    if selection is None:
        return None
    if (not isinstance(selection, (list, tuple)) or not 1 <= len(selection) <= len(CHANNELS)
            or any(not isinstance(name, str) or name not in CHANNELS for name in selection)
            or len(set(selection)) != len(selection)):
        raise ValueError("Choose Pawn, Vehicle, or both for filtered walls.")
    return tuple(name for name in CHANNELS if name in selection)


def blocks_pawns(selection):
    return selection is None or "pawn" in selection


def filter_tags(selection):
    if selection is None:
        return []
    return [FILTER, *(TAG + "Block" + CHANNELS[name][0] for name in selection)]


def read_channels(walls):
    modes = set()
    for wall in walls:
        tags = {str(tag) for tag in wall.tags}
        selected = tuple(name for name, (profile, _) in CHANNELS.items() if TAG + "Block" + profile in tags)
        if (FILTER in tags) != bool(selected):
            raise ValueError("A solid perimeter has missing or inconsistent collision channel tags.")
        modes.add(selected or None)
    if len(modes) > 1:
        raise ValueError("All mission perimeter faces must use the same collision channels.")
    return next(iter(modes), None)


def configure_wall(actor, component, selection):
    import unreal

    component.set_collision_profile_name("BlockAll")
    if selection is not None:
        component.set_collision_response_to_all_channels(unreal.CollisionResponseType.ECR_IGNORE)
        for name in selection:
            channel = getattr(unreal.CollisionChannel, CHANNELS[name][1])
            component.set_collision_response_to_channel(channel, unreal.CollisionResponseType.ECR_BLOCK)
    component.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
    component.set_editor_property("generate_overlap_events", False)
    component.set_visibility(False)
    component.set_cast_shadow(False)
    actor.set_actor_hidden_in_game(True)
    actor.set_actor_enable_collision(True)


def has_wall_collision(actor, component, selection):
    import unreal

    if (not component or component.get_collision_enabled() != unreal.CollisionEnabled.QUERY_AND_PHYSICS
            or component.get_collision_object_type() != unreal.CollisionChannel.ECC_WORLD_STATIC
            or not actor.get_actor_enable_collision() or component.is_visible()):
        return False
    if selection is None:
        return component.get_collision_profile_name() == unreal.Name("BlockAll")
    blocked = {CHANNELS[name][1] for name in selection}
    for key in (*STOCK_IGNORED, *(channel for _, channel in CHANNELS.values())):
        expected = unreal.CollisionResponseType.ECR_BLOCK if key in blocked else unreal.CollisionResponseType.ECR_IGNORE
        if component.get_collision_response_to_channel(getattr(unreal.CollisionChannel, key)) != expected:
            return False
    return True


def probe_profiles(selection):
    if selection is None:
        return (("Pawn", True),)
    return tuple((profile, name in selection) for name, (profile, _) in CHANNELS.items()) + (("BlockAllDynamic", False),)
