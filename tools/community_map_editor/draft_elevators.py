"""Place stock Unreal overlap volumes and bind saved elevator switches."""

from hashlib import sha256
import math

import unreal

from draft_interiors import build_asset
from source_profiles import PROXIMITY_CHAIN_MODES


def configure_pawn_overlap(actor, mesh):
    component = actor.static_mesh_component
    if not component.set_static_mesh(mesh):
        raise RuntimeError(f"Could not set the overlap hull for {actor.get_actor_label()}.")
    actor.set_editor_property("generate_overlap_events_during_level_streaming", True)
    component.set_visibility(False)
    component.set_collision_profile_name("OverlapAllDynamic")
    component.set_collision_enabled(unreal.CollisionEnabled.QUERY_ONLY)
    component.set_collision_response_to_all_channels(unreal.CollisionResponseType.ECR_IGNORE)
    component.set_collision_response_to_channel(unreal.CollisionChannel.ECC_PAWN, unreal.CollisionResponseType.ECR_OVERLAP)
    component.set_editor_property("generate_overlap_events", True)


def trigger_cylinder(package):
    # The source uses a flat-ended cylinder. Keep the rendered mesh hidden and use its convex hull for queries.
    count = 48
    angles = [math.tau * i / count for i in range(count)]
    vertices = [(math.cos(angle), math.sin(angle), z) for z in (-1, 1) for angle in angles]
    faces = [(0, i + 1, i) for i in range(1, count - 1)]
    faces.extend((count, count + i, count + i + 1) for i in range(1, count - 1))
    for i in range(count):
        n = (i + 1) % count
        faces.extend(((i, n, i + count), (n, n + count, i + count)))
    identity = sha256(b"vengeance-lift-trigger-unit-cylinder-48-v1").hexdigest()
    geometry = dict(format=5, vertices_m=vertices, source={"sha256": identity}, materials=["Trigger hull"],
                    triangles=[[0, *face, 0, 0, 1, 0, 0, 1] for face in faces])
    hull = [[x * 100, y * 100, z * 100] for x, y, z in vertices]
    return build_asset(geometry, identity, package, convex_hull=hull)


def add_trigger_receivers(triggers, senders, actor):
    for sender in senders:
        known, destinations = triggers.setdefault(sender["source_export"], (sender, []))
        if known != sender:
            raise ValueError("A Vengeance trigger has inconsistent source settings.")
        destinations.append(actor)


def place_elevators(actors, draft, lifts, triggers):
    folder = f"/Game/Community/{draft}/PhysicsVolumes"
    bridge = unreal.CommunityMapMotionLibrary
    linked_class = None
    if any(senders for *_, senders in lifts):
        package = f"{folder}/BP_LinkedElevatorField"
        linked_class = bridge.build_linked_elevator_class(package)
        if linked_class is None or not unreal.EditorAssetLibrary.save_asset(package, False):
            raise RuntimeError("Could not save the replicated elevator field class.")
    volumes = []
    for row, index, geometry, digest, hull, behavior, senders in lifts:
        package = f"{folder}/SM_ElevatorHull_{index}"
        identity = sha256((digest + ":vengeance-elevator-hull-v1").encode()).hexdigest()
        mesh = build_asset(geometry, identity, package, convex_hull=hull)
        transform = row["transform"]
        actor_class = linked_class if senders else unreal.StaticMeshActor
        volume = actors.spawn_actor_from_class(actor_class, unreal.Vector(*transform["position_cm"]),
                                               unreal.Quat(*transform["rotation_xyzw"]).rotator())
        if not volume:
            raise RuntimeError(f"Could not place elevator hull {index}.")
        volume.set_actor_scale3d(unreal.Vector(*transform["scale"]))
        volume.set_actor_label(f"Elevator field · {row['name']}")
        volume.set_folder_path("TRIBES Import/Elevator fields")
        volume.tags = [f"T3ElevatorField_{index}"]
        configure_pawn_overlap(volume, mesh)
        if senders and not bridge.configure_linked_elevator(volume, behavior["active"]):
            raise RuntimeError(f"Could not apply the authored startup state of elevator {index}.")
        add_trigger_receivers(triggers, senders, volume)
        volumes.append(volume)
    if volumes and not bridge.bind_zero_gravity_volumes(volumes):
        raise RuntimeError("Unreal could not author the elevator overlap graph.")


def place_brush_triggers(actors, draft, triggers, relays=None):
    bridge = unreal.CommunityMapMotionLibrary
    folder_path = f"/Game/Community/{draft}/PhysicsVolumes"
    mesh = trigger_cylinder(f"{folder_path}/SM_ElevatorTriggerCylinder")
    relays = relays or {}
    chain_sources = set()
    for senders in relays.values():
        for sender in senders:
            known, _ = triggers.setdefault(sender["source_export"], (sender, []))
            if known != sender:
                raise ValueError("A Vengeance trigger has inconsistent source settings.")
            chain_sources.add(sender["source_export"])
    placed, classes = {}, {}
    for index, (sender, destinations) in triggers.items():
        source = sender["proximity"]
        actor_class = unreal.StaticMeshActor
        if index in relays:
            change = PROXIMITY_CHAIN_MODES[source["state"].casefold()]
            if change not in classes:
                package = f"{folder_path}/BP_ProximityReceiver_{change}"
                classes[change] = bridge.build_proximity_receiver_class(package, change)
                if classes[change] is None or not unreal.EditorAssetLibrary.save_asset(package, False):
                    raise RuntimeError("Could not author the chained proximity receiver class.")
            actor_class = classes[change]
        trigger = actors.spawn_actor_from_class(actor_class, unreal.Vector(*source["position_cm"]))
        if not trigger:
            raise RuntimeError(f"Could not place brush trigger {index}.")
        radius, half_height = source["radius_cm"] / 100, source["half_height_cm"] / 100
        trigger.set_actor_scale3d(unreal.Vector(radius, radius, half_height))
        has_links = index in relays or index in chain_sources or any(isinstance(actor, unreal.LevelSequenceActor)
                                                                     for actor in destinations)
        label, folder, tag = (("Brush trigger", "Brush triggers", "T3BrushTrigger") if has_links
                              else ("Elevator trigger", "Elevator fields", "T3ElevatorTrigger"))
        trigger.set_actor_label(f"{label} · {sender['source_name']}")
        trigger.set_folder_path(f"TRIBES Import/{folder}")
        trigger.tags = [f"{tag}_{index}"]
        configure_pawn_overlap(trigger, mesh)
        trigger.set_actor_enable_collision(source["active"])
        placed[index] = trigger
    for receiver, senders in relays.items():
        add_trigger_receivers(triggers, senders, placed[receiver])
    for index, (sender, destinations) in triggers.items():
        trigger, source = placed[index], sender["proximity"]
        mode, once, delay = source["activation"], source["once"], source["delay_seconds"]
        interval = 0.0 if once else source["repeat_seconds"]
        if index in chain_sources or index in relays or any(isinstance(actor, unreal.LevelSequenceActor)
                                                             for actor in destinations):
            bound = bridge.bind_brush_trigger(trigger, destinations, mode, once, delay, interval)
        elif interval:
            bound = bridge.bind_repeating_elevator_trigger(trigger, destinations, mode, delay, interval)
        else:
            bound = bridge.bind_elevator_trigger(trigger, destinations, mode, once, delay)
        if not bound:
            raise RuntimeError(f"Unreal could not link brush trigger {index} to its receivers.")
