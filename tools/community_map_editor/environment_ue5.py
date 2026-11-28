"""The UE5 draft adapter for the MILF environment component (T3BackEndServer#146).

Every source converter (environment_ascend.py first) produces the same component; this module
is the one place that turns it into UE5 actors. Adapter rules from the spec: exposure is never
automatic, each field is applied once, and the grade covers the whole map.

plan() is pure arithmetic so it can be tested without Unreal; apply() places the actors.
"""

import math

from milf_environment import exposure_scale, from_milf_axes, lut_fit, read_png_rgb, validate

EXPOSURE_LABEL = "MILF environment · Exposure and grade"
DEFAULT_SUN_LUX = 10.0  # UE5's own default sun, used when a component has no sun brightness.
UE5_BLOOM_INTENSITY = 0.675  # UE5's default bloom; a MILF bloom intensity of 1 keeps it.
DISPLAY_GAMMA = 2.2  # Display-space gain to linear gain.


def plan(component, lut_rows=None):
    """Return the UE5 settings for a validated component, as plain numbers.

    ``lut_rows`` are the decoded pixels of the grade's LUT, when it has one. The TRIBES 3 client
    ignores a map's colour-grading LUT (it honours exposure and colour grading), so the LUT is
    folded into the per-channel gain and gamma instead of being set as a texture.
    """
    validate(component)
    result = {}
    sun = component.get("sun", {})
    pitch, yaw = -48.0, 120.0
    if "direction" in sun:
        x, y, z = from_milf_axes(sun["direction"])
        pitch, yaw = math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))
    result["sun"] = {"pitch": pitch, "yaw": yaw, "lux": sun.get("illuminance_lux", DEFAULT_SUN_LUX),
                     "color": sun.get("color")}
    sky = component.get("sky", {})
    result["sky"] = {"intensity": sky.get("intensity", 1.0), "color": sky.get("color")}
    exposure = component.get("exposure")
    scale = exposure_scale(exposure["ev100"]) if exposure else None
    if exposure:
        # Manual exposure with the physical camera off multiplies the scene by 2^bias.
        result["exposure"] = {"bias": math.log2(scale)}
    fog = component.get("fog")
    if fog:
        settings = {"start_cm": fog.get("start_m", 0) * 100, "density": fog.get("density", 0.02),
                    "height_cm": fog.get("height_m", 0) * 100}
        for key in ("height_falloff", "max_opacity"):
            if key in fog:
                settings[key] = fog[key]
        if "color" in fog:
            # The component gives fog as it is seen; UE5 multiplies fog light by the exposure.
            gain = 1 / scale if scale else 1
            settings["inscattering"] = [value * gain for value in fog["color"]]
        result["fog"] = settings
    grade = component.get("grade")
    if grade:
        # UE3-style tones onto UE5 colour grading. Mid-tones above 1 darken the mid-tones in UE3
        # but a UE5 gamma above 1 brightens them, so the gamma is 1 / mid-tones; highlights scale
        # brightness (gain = highlights) and shadows subtract (offset = -shadows). Checked in the
        # editor on Tartarus: the direct mapping washed the scene out.
        tones = {"gamma": [1 / max(value, 1e-3) for value in grade.get("midtones", [1, 1, 1])],
                 "gain": list(grade.get("highlights", [1, 1, 1])),
                 "offset": [-value for value in grade.get("shadows", [0, 0, 0])]}
        if lut_rows is not None:
            # The LUT acts after tonemapping, in display space: out = a x in^p per channel. A power
            # carries over to linear unchanged and a display gain a becomes a^2.2, so after the
            # tones: gamma = tone gamma / p and gain = a^2.2 x tone gain^p.
            fits = lut_fit(lut_rows)
            tones["gamma"] = [gamma / max(p, 1e-3) for gamma, (_, p) in zip(tones["gamma"], fits)]
            tones["gain"] = [a ** DISPLAY_GAMMA * gain ** p for gain, (a, p) in zip(tones["gain"], fits)]
        bloom = grade.get("bloom", {})
        # The UE3 bloom threshold is not carried over: glows are capped at white (the source
        # clipped them there), so a UE3 threshold of 3 would stop them blooming. UE5's own
        # threshold keeps the halo; the intensity follows the source scale.
        result["grade"] = {**tones, "lut_folded": lut_rows is not None,
                           "bloom_intensity": bloom.get("intensity", 1) * UE5_BLOOM_INTENSITY,
                           "bloom_threshold": None}
    return result


def apply(actors, component, assets=None):
    """Place the sun, sky light, atmosphere, fog, fixed exposure and grade for a component.

    ``assets`` gives the folder, file inventory and Unreal destination for any images the
    component names (the grade's LUT).
    """
    import unreal

    lut_rows = None
    lut = component.get("grade", {}).get("lut")
    if lut and assets:
        from intake_contract import bounded

        lut_rows = read_png_rgb(bounded(assets["folder"] / lut, 1024 * 1024))
    settings = plan(component, lut_rows)
    sun = settings["sun"]
    light = actors.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 3000),
                                          unreal.Rotator(pitch=sun["pitch"], yaw=sun["yaw"]))
    light.light_component.set_editor_property("atmosphere_sun_light", True)
    light.light_component.set_intensity(sun["lux"])
    if sun["color"]:
        light.set_light_color(unreal.LinearColor(*sun["color"], 1))
    sky_actor = actors.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 1500))
    sky_light = sky_actor.get_component_by_class(unreal.SkyLightComponent)
    sky_light.set_intensity(settings["sky"]["intensity"])
    if settings["sky"]["color"]:
        sky_light.set_light_color(unreal.LinearColor(*settings["sky"]["color"], 1))
    actors.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
    fog = settings.get("fog")
    if fog:
        actor = actors.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(0, 0, fog["height_cm"]))
        component_fog = actor.get_component_by_class(unreal.ExponentialHeightFogComponent)
        component_fog.set_start_distance(fog["start_cm"])
        component_fog.set_fog_density(fog["density"])
        if "height_falloff" in fog:
            component_fog.set_fog_height_falloff(fog["height_falloff"])
        if "max_opacity" in fog:
            component_fog.set_fog_max_opacity(fog["max_opacity"])
        if "inscattering" in fog:
            component_fog.set_fog_inscattering_color(unreal.LinearColor(*fog["inscattering"], 1))
    exposure, grade = settings.get("exposure"), settings.get("grade")
    if exposure or grade:
        # One unbound volume holds the whole-map exposure and grade.
        volume = actors.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector(0, 0, 0))
        volume.set_actor_label(EXPOSURE_LABEL)
        volume.set_editor_property("unbound", True)
        post = volume.get_editor_property("settings")
        values = []
        if exposure:
            values += [("override_auto_exposure_method", True),
                       ("auto_exposure_method", unreal.AutoExposureMethod.AEM_MANUAL),
                       ("override_auto_exposure_bias", True), ("auto_exposure_bias", exposure["bias"]),
                       ("override_auto_exposure_apply_physical_camera_exposure", True),
                       ("auto_exposure_apply_physical_camera_exposure", False)]
        if grade:
            for key in ("gamma", "gain", "offset"):
                values += [(f"override_color_{key}", True), (f"color_{key}", unreal.Vector4(*grade[key], 1 if key != "offset" else 0))]
            values += [("override_bloom_intensity", True), ("bloom_intensity", grade["bloom_intensity"])]
            if grade["bloom_threshold"] is not None:
                values += [("override_bloom_threshold", True), ("bloom_threshold", grade["bloom_threshold"])]
        for key, value in values:
            post.set_editor_property(key, value)
        volume.set_editor_property("settings", post)
    unreal.log("T3_MILF_ENVIRONMENT|" + __import__("json").dumps(component, separators=(",", ":")))
    return settings
