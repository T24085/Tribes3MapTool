"""Standing foliage collision rules for converted maps.

Ground plants never block players: T3 players want clean skiing lines, and DeadManWalking's rule for the
T2 2026 maps (CwFoliageCollision.py, 19 Nov 2026) is that bushes are not dead stops. A ground plant keeps
the "soft foliage" response those maps use: Pawn and Vehicle ignored, every other channel as BlockAll, so
it still stops shots, grenades and traces like terrain does.

Trees are not ground plants. They have their own modes (whole tree solid or not, shots blocked or not, or a
solid trunk with a soft canopy that still stops shots). Giant trees such as Tartarus's swamp trunks are not
"trees" for those modes either: they are solid structures, like terrain and buildings.
"""

import re

# Name parts that mark a mesh as a ground plant, found anywhere in a word (waterplant, fencevine).
PLANT_WORDS = ("plant", "grass", "fern", "bush", "shrub", "flower", "lily", "lilypad", "reed", "weed", "vine",
               "ivy", "tendril", "bulrush", "cattail", "clover", "sapling", "biolamp", "foliage", "frond")
# Words that make a mesh a tree (or part of one), never a ground plant.
TREE_WORDS = ("tree", "trunk", "canopy", "branch", "bough", "cypress", "pine", "oak", "palm", "willow")
# Words that mean the object is not foliage at all, even with a plant word in its name.
NOT_PLANT_WORDS = ("rock", "stone", "boulder", "cliff", "building", "pipe", "ship", "metal", "planter")


def name_words(name):
    """Lower-case words of a mesh name: package.group.SM_SwampPlanet_LilyPad -> [..., 'lilypad']."""
    return [word for word in re.split(r"[^a-z0-9]+|(?<=[a-z])(?=[0-9])", name.casefold()) if word]


def has_word(words, vocabulary):
    return any(term in word for word in words for term in vocabulary)


def is_ground_plant(name):
    """True for a ground plant (grass, bush, lily pad, water plant...), never a tree or a rock."""
    words = name_words(name.rsplit(".", 1)[-1].removeprefix("unrealmesh:"))
    words = [word for word in words if word not in ("sm", "ase", "spt")]
    if has_word(words, TREE_WORDS) or has_word(words, NOT_PLANT_WORDS):
        return False
    return has_word(words, PLANT_WORDS)


def soft_foliage(component):
    """Give a placed mesh the soft-foliage response: players and vehicles pass, shots and traces stop."""
    import unreal

    component.set_collision_profile_name("BlockAll")
    component.set_collision_enabled(unreal.CollisionEnabled.QUERY_ONLY)
    component.set_collision_response_to_channel(unreal.CollisionChannel.ECC_PAWN, unreal.CollisionResponseType.ECR_IGNORE)
    component.set_collision_response_to_channel(unreal.CollisionChannel.ECC_VEHICLE,
                                                unreal.CollisionResponseType.ECR_IGNORE)
