"""Tribes: Ascend (Unreal Engine 3) to the MILF environment component (T3BackEndServer#146).

One converter per source engine. This one reads the Ascend intake's environment block (the
source sun, sky light and height fog) and the draft's conversion-profile values, which hold the
source numbers and any per-map tuning, and returns a MILF environment component.

The factors below are the Ascend defaults, the same for every Ascend map ("ascend-environment-1").
A change to them is a new rules version. Per-map tuning goes through the conversion profile's
layers, not through these constants.
"""

import math
from milf_environment import FORMAT, ev100_for_scale, exposure_scale, to_milf_axes, validate

RULES = "ascend-environment-2"  # 2: lux x pi, grade folded as a display-space fit.
# UE3 lights a surface as albedo x brightness with no camera exposure. A fixed exposure of 1/10
# (one brightness unit at 10 lux shows as white) anchors the conversion; the lux carry pi below.
EXPOSURE_UNIT_LUX = 10.0  # Fixed exposure shows one UE3 brightness unit as white (EV100 3.06).
# UE3 multiplied light brightness straight onto surface colour; UE5 turns illuminance into reflected
# brightness by dividing by pi. So one UE3 unit needs pi x 10 lux to look the same: at 10 lux Arx
# Novena's sunlit desert came out about three times too dark (checked live in the editor).
LUX_PER_BRIGHTNESS = math.pi * EXPOSURE_UNIT_LUX
EV100 = round(ev100_for_scale(1 / EXPOSURE_UNIT_LUX), 4)
# UE3 shows an emissive value as it is; under the fixed exposure above, UE5 needs it x this gain.
EMISSIVE_GAIN = round(1 / exposure_scale(EV100), 4)
# UE3 clipped each colour channel at white, so an over-bright glow kept its hue (a deep blue pod
# stays deep blue). UE5's filmic tonemapper turns over-bright colours toward white instead, so the
# glow's brightest channel is capped here and bloom supplies the halo (Tribes3MapTool#40).
GLOW_CLIP = 1.0


def glow_colour(tint):
    """An Ascend glow tint in UE3 display units, capped like UE3's per-channel clip, keeping its hue."""
    peak = max(tint)
    scale = GLOW_CLIP / peak if peak > GLOW_CLIP else 1.0
    return [value * scale for value in tint]


def convert(environment, values):
    """Return the MILF environment for an Ascend intake environment and its projected values."""
    environment = environment or {}
    source_sun, source_fog = environment.get("sun", {}), environment.get("fog", {})
    component = {"format": FORMAT, "exposure": {"mode": "fixed", "ev100": EV100},
                 "provenance": {"game": "ascend", "rules": RULES}}
    sun = {}
    if "direction" in source_sun:
        sun["direction"] = to_milf_axes(source_sun["direction"])
    if "color" in source_sun:
        sun["color"] = list(source_sun["color"])
    scale = values.get("sun.intensity_scale")
    if scale is not None:
        sun["illuminance_lux"] = round(scale * LUX_PER_BRIGHTNESS, 4)
    if sun:
        component["sun"] = sun
    sky = {}
    if "ambient" in source_sun:
        sky["color"] = list(source_sun["ambient"])
    if values.get("sun.ambient_scale") is not None:
        sky["intensity"] = values["sun.ambient_scale"]
    if sky:
        component["sky"] = sky
    start, density = values.get("fog.start_cm"), values.get("fog.density")
    if start is not None and density is not None:
        fog = {"start_m": round(start / 100, 4), "density": density}
        if "height_cm" in source_fog:
            fog["height_m"] = round(source_fog["height_cm"] / 100, 4)
        for key in ("height_falloff", "max_opacity", "color", "sun_color"):
            if key in source_fog:
                fog[key] = source_fog[key]
        component["fog"] = fog
    source_grade = environment.get("grade")
    if source_grade:
        # UE3 scene shadows, mid-tones and highlights carry over as MILF tones; UE3 bloom scale is
        # the MILF bloom intensity. The LUT stays a 256 x 16 image (#38).
        grade = {key: list(source_grade[key]) for key in ("shadows", "midtones", "highlights")}
        bloom = source_grade["bloom"]
        grade["bloom"] = {"intensity": bloom["scale"], "threshold": bloom["threshold"], "tint": list(bloom["tint"])}
        if "lut" in source_grade:
            grade["lut"] = source_grade["lut"]
        component["grade"] = grade
    return validate(component)
