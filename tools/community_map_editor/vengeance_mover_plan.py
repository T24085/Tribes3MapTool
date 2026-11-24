"""Convert Vengeance brush keyframes into timed engine transform tracks."""

import math
from collections import defaultdict
from graphlib import CycleError, TopologicalSorter

from source_profiles import PROXIMITY_CHAIN_MODES
from unreal_legacy_reader import quaternion


FRAMES_PER_SECOND = 1000


def normal_proximity_senders(senders, chains=()):
    if len({item["source_export"] for item in senders}) != len(senders):
        return ()
    for sender in senders:
        trigger = sender["proximity"]
        if (sender["review"] or not trigger or trigger["activation"] not in (0, 1, 5) or not trigger["collision"]
                or trigger["blocking"] or not trigger["touch"]):
            return ()
        state = trigger["state"].casefold()
        if sender["source_export"] in chains:
            if state not in PROXIMITY_CHAIN_MODES or not trigger["receives_events"]:
                return ()
        elif state != "normaltrigger" or not trigger["active"] or trigger["receives_events"]:
            return ()
    return tuple(senders)


def plan_proximity_chains(chains, brushes, available):
    """Reject an entire connected group when one of its authored receivers cannot be built."""
    if not chains:
        return {}, set()
    authored = {int(index): links for index, brush in brushes.items()
                if (links := brush["behavior"].get("trigger_senders"))}
    parents, neighbors = {}, defaultdict(set)
    chains = {int(index): senders for index, senders in chains.items()}
    for receiver, senders in (authored | chains).items():
        parents[receiver] = {sender["source_export"] for sender in senders}
        for sender in senders:
            source = sender["source_export"]
            neighbors[source].add(receiver)
            neighbors[receiver].add(source)
    allowed, rejected, visited = {}, set(), set()
    for root in chains:
        if root in visited:
            continue
        pending, component = [root], {root}
        while pending:
            for peer in neighbors[pending.pop()] - component:
                component.add(peer)
                pending.append(peer)
        visited.update(component)
        relays = chains.keys() & component
        leaves = authored.keys() & component
        if not leaves <= available or any(not normal_proximity_senders(chains[key], chains) for key in relays):
            rejected.update(leaves)
            continue
        try:
            tuple(TopologicalSorter({key: parents.get(key, ()) for key in component}).static_order())
        except CycleError:
            rejected.update(leaves)
            continue
        allowed.update((key, chains[key]) for key in sorted(relays))
    return allowed, rejected


def rotate(vector, rotation):
    def cross(a, b):
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]

    first = cross(rotation, vector)
    second = cross(rotation, first)
    return [v + 2 * (rotation[3] * t + u) for v, t, u in zip(vector, first, second)]


def mover_ready(brush, state):
    motion, behavior = brush["motion"], brush["behavior"]
    return (motion and motion["initial_state"].casefold() == state and len(motion["keys"]) >= 2
            and motion["current_key"] == 0 and motion["glide_type"] in (0, 1) and not behavior["linked"]
            and behavior["encroach_type"] == 3 and not any(
                key != "tag" and value.casefold() not in {"", "none"} for key, value in motion["events"].items()))


def loop_track(brush):
    if not mover_ready(brush, "constantloop"):
        return None
    count = len(brush["motion"]["keys"])
    order = [*range(count)]
    if brush["behavior"]["oscillating"]:
        order.extend(range(count - 2, 0, -1))
    order.append(0)
    return transform_track(brush, order)


def bump_track(brush):
    if not mover_ready(brush, "bumpopentimed") or brush["behavior"].get("bump_type") not in (0, 2):
        return None
    return timed_track(brush)


def trigger_track(brush, chains=()):
    if not mover_ready(brush, "triggeropentimed"):
        return None, ()
    senders = normal_proximity_senders(brush["behavior"].get("trigger_senders", ()), chains)
    return (timed_track(brush), senders) if senders else (None, ())


def timed_track(brush):
    motion = brush["motion"]
    final = len(motion["keys"]) - 1
    pauses = {0: motion["timing_seconds"]["delaytime"], final: motion["timing_seconds"]["stayopentime"]}
    opening = list(range(final + 1))
    if brush["behavior"].get("opens_once"):
        moves = transform_track(brush, opening, pauses)
        if moves:
            frame, opened = moves[-1]
            dwell = max(1, round(pauses[final] * FRAMES_PER_SECOND))
            moves.append((frame + dwell, opened))
        return moves
    order = opening + list(range(final - 1, -1, -1))
    return transform_track(brush, order, pauses)


def transform_track(brush, order, pauses=None):
    motion, behavior = brush["motion"], brush["behavior"]
    keys = motion["keys"]

    def subtract(a, b):
        return [left - right for left, right in zip(a, b)]

    def add(a, b):
        return [left + right for left, right in zip(a, b)]

    def scaled(value, factor):
        return [element * factor for element in value]

    saved = brush["transform"]
    saved_angles = behavior["saved_rotation_units"]
    pivot = [100 * offset * scale for offset, scale in zip(behavior["pivot_m"], saved["scale"])]
    base_angles = motion["base_rotation_units"]
    if base_angles is None:
        base_angles = subtract(saved_angles, keys[0]["rotation_offset_units"])
    base_position = motion["base_position_m"]
    if base_position is None:
        saved_origin = add(saved["position_cm"], rotate(pivot, quaternion(saved_angles)))
        origin = subtract(saved_origin, scaled(keys[0]["position_offset_m"], 100))
    else:
        origin = scaled(base_position, 100)
    positions = [add(origin, scaled(key["position_offset_m"], 100)) for key in keys]
    rotations = [add(base_angles, key["rotation_offset_units"]) for key in keys]
    pauses = pauses or {}
    duration = round(max(0.005, motion["timing_seconds"]["movetime"]) * FRAMES_PER_SECOND)
    moves, start_angles = [], rotations[0]
    elapsed = 0
    for leg, (left, right) in enumerate(zip(order, order[1:])):
        change = subtract(rotations[right], rotations[left])
        if behavior["shortest_rotation"]:
            change = [math.remainder(angle, 65536) for angle in change]
        if any(abs(angle) > 65536 for angle in change):
            return None
        start, offset = positions[left], subtract(positions[right], positions[left])
        samples = min(duration, 60)
        pause = round(pauses.get(leg, 0) * FRAMES_PER_SECOND)
        elapsed += pause
        for sample in range(samples + 1):
            if leg and sample == 0 and not pause:
                continue
            frame = round(duration * sample / samples)
            progress = frame / duration
            if motion["glide_type"] == 1:
                progress = progress * progress * (3 - 2 * progress)
            angles = add(start_angles, scaled(change, progress))
            position = subtract(add(start, scaled(offset, progress)), rotate(pivot, quaternion(angles)))
            if any(abs(value) > 2_000_000 for value in position):
                return None
            pitch, yaw, roll = scaled(angles, 360 / 65536)
            moves.append((elapsed + frame, (*position, roll, pitch, yaw, *saved["scale"])))
        start_angles = add(start_angles, change)
        elapsed += duration
    if moves[0][0]:
        moves.insert(0, (0, moves[0][1]))
    return moves
