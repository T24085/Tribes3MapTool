"""Boundaries presets from a Tribes 1 or Tribes 2 mission area (Tribes3-Modded/Tribes3MapTool#15).

Tribes 2 maps carry only the play area, not its rules. A ``MissionArea`` holds the rectangle
(``area = "x y w h"``) and a soft flight ceiling (``flightCeiling``, faded over
``flightCeilingRange``). Each game mode's script decides what crossing it does. In the stock
scripts (base/scripts.vl2):

* Nothing is solid. Players, vehicles and projectiles can all cross the line.
* Every mode tells the player "You have left the mission area" with a warning beep, and says
  "You are back in the mission area" on return.
* CTF: a carrier who leaves loses the flag at once. It is thrown back toward its home stand, and
  the normal 45 second return timer starts. A player who picks the flag up while outside loses it
  after 3 seconds. There is no damage.
* Deathmatch, Rabbit (the rabbit only), Bounty and Defend and Destroy: alerts at 1, 2 and 3
  seconds, then from 4 seconds 0.05 damage per second until the player returns or dies. That kills
  a full-health Light armor (0.66) in about 17 s, Medium (1.1) in about 25 s and Heavy (1.32) in
  about 30 s. A rabbit repairing itself takes 0.15 per second.
* Hunters: a player carrying more than one flag loses them every 3 seconds while outside.

Tribes 1 (base/scripts/server/game, 1.40) uses a ``MissionCenterPos`` rectangle and no flight
ceiling. Nothing is solid either:

* Leaving shows "You have left the mission area" and plays LeftMissionArea.wav at 0, 1.5 and 3 s.
* CTF and other objective missions: if the player is still outside at 4.5 s, a carried flag is
  returned straight to its base. A flag cannot be picked up from outside the area.
* Deathmatch: the same alerts, then from 4 s 0.05 damage per second until the player returns or dies.

The presets match CTF with what TRIBES 3 can already do, a HUD warning and the native flag rules:
Tribes 2 warns and drops a carried flag at the line at once; Tribes 1 warns and gives the carrier
5 s (4.5 s rounded up) to come back before the flag returns home. Neither adds solid walls or
damage.
Grace-time damage and the flight ceiling need the runtime boundary layer
(Tribes3-Modded/T3BackEndServer#111).
"""

from intake_contract import BOUNDARY_RECIPE, PLAYABLE_BOUNDS
from perimeter import flag_bounds, terrain_elevations

# The flag-only limit must sit inside the mission area; one metre keeps it on the line.
FLAG_INSET_M = 1
# Seconds a carrier may stay outside before the flag returns home (None: drop at once).
FLAG_RETURN = {"Tribes 2": None, "Tribes 1": 5}


def soft_boundary_recipe(rows, terrain, *, game, cm_per_source_m=None):
    """Store a warn-and-drop Boundaries recipe on the boundary row. Return the recipe, or None."""
    boundary = next((row for row in rows if row["status"] == "boundary"), None)
    if boundary is None or PLAYABLE_BOUNDS not in boundary["properties"]:
        return None
    bounds = boundary["properties"][PLAYABLE_BOUNDS]
    flags = [(row.get("name") or "Flag", row["position_cm"])
             for row in rows if row["status"] == "marker" and row.get("kind") == "flag" and "position_cm" in row]
    options = {"perimeter": False, "flag_inset_m": FLAG_INSET_M, "warn_players": True}
    if FLAG_RETURN.get(game) is not None:
        options["flag_return_delay_s"] = FLAG_RETURN[game]
    notes = []
    try:
        flag_bounds(bounds, FLAG_INSET_M, flags)
    except ValueError as error:
        # A flag stand sits on or outside the line; keep the warning only.
        options["flag_inset_m"] = None
        options.pop("flag_return_delay_s", None)
        notes.append(f"Flag-only limit left off: {error}")
    try:
        elevation = terrain_elevations(terrain)
    except (KeyError, TypeError, ValueError):
        return None
    recipe = {"format": 1, "elevation_cm": elevation, "options": options}
    boundary["properties"][BOUNDARY_RECIPE] = recipe
    boundary["reason"] = describe(game, boundary["properties"], cm_per_source_m, options, notes)
    return recipe


def describe(game, properties, cm_per_source_m, options, notes):
    source = "MissionArea" if game == "Tribes 2" else "MissionCenterPos"
    actions = ["warn players on crossing"]
    if options["flag_inset_m"] is not None and options.get("flag_return_delay_s"):
        actions.append(f"return a carried flag home after {options['flag_return_delay_s']} s outside")
    elif options["flag_inset_m"] is not None:
        actions.append("drop a carried flag at the line")
    text = f"Playable area from the {game} {source}. Boundaries preset: {' and '.join(actions)}; no solid walls."
    fields = {key.casefold(): value for key, value in properties.items()}
    if cm_per_source_m and "flightceiling" in fields:
        try:
            ceiling = float(fields["flightceiling"]) * cm_per_source_m / 100
            soft = float(fields.get("flightceilingrange", 0)) * cm_per_source_m / 100
            text += f" Not applied: flight ceiling {ceiling:,.0f} m (soft over {soft:,.0f} m)"
            text += ", and grace-time damage used by non-CTF modes."
        except ValueError:
            pass
    elif game == "Tribes 1":
        text += (" Tribes 1 returns the flag after 4.5 s, rounded up to 5 s here. Not applied: no flag pickup"
                 " from outside, and Deathmatch damage after 4 s.")
    for note in notes:
        text += " " + note
    return text[:480]
