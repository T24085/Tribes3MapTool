"""Share data-only conversion profiles between authoring workspaces."""

from ipaddress import IPv6Address
from pathlib import Path
import re

if __package__:
    from .source_profiles import NAMES, PREFIX, imported_draft
else:
    from source_profiles import NAMES, PREFIX, imported_draft


LIMIT = 320 * 1024
COMPONENTS = ("conversion", "catalog", "pipeline")
PRIVATE_REFERENCE = re.compile(
    r"(?i)[a-z]:[\\/]|\\\\|://|(?:^|\s)(?:~|\.{1,2})?[\\/](?=\S)"
    r"|\b(?:localhost|(?:\d{1,3}\.){3}\d{1,3})\b|\b[\w.+-]+@[\w.-]+\.\w+\b"
    r"|\b[\w.-]+(?:[\\/][\w.-]+)+\.[a-z0-9]{1,12}\b"
)


def bundle_file(library, source_profile):
    if not isinstance(source_profile, str) or source_profile not in NAMES:
        raise ValueError("Unsupported map conversion profile.")
    return Path(library) / "GameProfiles" / (source_profile + ".json")


def envelope(document):
    if __package__:
        from .conversion_rules import checksum
    else:
        from conversion_rules import checksum

    if (not isinstance(document, dict) or set(document) != {"format", "profile", "sha256", *COMPONENTS}
            or type(document["format"]) is not int or document["format"] != 1
            or not isinstance(document["profile"], str) or document["profile"] not in NAMES
            or not all(isinstance(document[key], dict) for key in COMPONENTS)):
        raise ValueError("Invalid game profile bundle.")
    signature = checksum({key: value for key, value in document.items() if key != "sha256"})
    if signature != document["sha256"]:
        raise ValueError("The game profile bundle checksum does not match.")
    return document


def active_profile(library, source_profile):
    if library is None:
        return None
    if __package__:
        from .conversion_rules import read
    else:
        from conversion_rules import read

    path = bundle_file(library, source_profile)
    try:
        document = envelope(read(path, LIMIT))
    except FileNotFoundError:
        return None
    if document["profile"] != source_profile:
        raise ValueError("The installed profile does not match this source game.")
    return document


def public_values(value):
    if isinstance(value, str):
        if ((value and not value.isprintable()) or len(value) > 1600 or PRIVATE_REFERENCE.search(value)
                or any(ipv6_address(token) for token in re.findall(r"[0-9a-fA-F:]*:[0-9a-fA-F:]+", value))):
            raise ValueError("Game profiles may only contain rules and labels. Remove file paths and connection details.")
    elif isinstance(value, list):
        for item in value:
            public_values(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            public_values(key)
            public_values(item)


def ipv6_address(token):
    try:
        IPv6Address(token)
    except ValueError:
        return False
    return True


def validate_bundle(document):
    if __package__:
        from .conversion_rules import encode, profile_config, project
        from .map_pipeline import validate_pipeline
        from .object_catalog import load_catalog
    else:
        from conversion_rules import encode, profile_config, project
        from map_pipeline import validate_pipeline
        from object_catalog import load_catalog

    envelope(document)
    if len(encode(document)) + 1 > LIMIT:
        raise ValueError("The game profile bundle is too large.")
    public_values(document)
    source_profile = document["profile"]
    profile = profile_config(document["conversion"], source_profile=source_profile)
    defaults = profile_config(source_profile=source_profile)
    if any(key not in profile["conversions"] or
           any(profile["conversions"][key][name] != row[name] for name in ("group", "unit"))
           for key, row in defaults["conversions"].items()):
        raise ValueError("Keep the built-in conversion records, their groups, and their output units.")
    fallback = project(profile, {}, {"objects": {}, "map": None, "game": None}, {})
    for key in ("terrain.tile_cm", "sun.intensity_scale", "sun.ambient_scale"):
        if fallback[key]["value"] is None:
            raise ValueError(f"{key} needs a value even when the source omits this setting.")
    if profile["conversions"]["terrain.tile_cm"]["range"][0] <= 0:
        raise ValueError("Terrain texture tiling requires a positive range.")
    pipeline = validate_pipeline(document["pipeline"])
    if pipeline["profile"] != source_profile:
        raise ValueError("The conversion pipeline does not match the game profile.")
    catalog = document["catalog"]
    classes, layout = catalog.get("classes"), catalog.get("layout")
    if (not isinstance(classes, dict) or len(classes) > 128
            or any(not isinstance(row, dict) or
                   row.keys() - {"label", "feature", "base", "transform", "fields", "required", "aliases"}
                   for row in classes.values())
            or not isinstance(layout, dict) or layout.keys() != {"tabs", "columns", "filters"}):
        raise ValueError("Game profiles only support the documented object classes and review layout.")
    for section, keys in (("tabs", {"id", "label"}), ("columns", {"id", "label", "width"})):
        if not isinstance(layout[section], list) or any(not isinstance(row, dict) or row.keys() != keys
                                                       for row in layout[section]):
            raise ValueError("Invalid game profile review layout.")
    load_catalog(document=catalog, source_profile=source_profile)
    return document


def collect_bundle(library, source_profile):
    if __package__:
        from .conversion_rules import checksum, profile_config
        from .map_pipeline import pipeline_config
        from .object_catalog import load_catalog
    else:
        from conversion_rules import checksum, profile_config
        from map_pipeline import pipeline_config
        from object_catalog import load_catalog

    saved = active_profile(library, source_profile)
    if saved is not None:
        return validate_bundle(saved)
    bundle = {
        "format": 1, "profile": source_profile,
        "conversion": profile_config(source_profile=source_profile),
        "catalog": load_catalog(library, source_profile=source_profile, bundle={}).document,
        "pipeline": pipeline_config(library, source_profile=source_profile, bundle={}),
    }
    return validate_bundle({**bundle, "sha256": checksum(bundle)})


def install_bundle(library, source):
    if __package__:
        from .conversion_rules import current_layers, draft_snapshot, history, read, validate_layers, validate_values, write
    else:
        from conversion_rules import current_layers, draft_snapshot, history, read, validate_layers, validate_values, write

    document = validate_bundle(read(source, LIMIT))
    profile = document["profile"]
    conversion = document["conversion"]
    game_history = history(library, "game-" + profile)
    if game_history:
        validate_values(conversion, game_history[-1]["values"])
    for path in (Path(library) / "DraftRules").glob(PREFIX[profile] + "*.json"):
        if not imported_draft(path.stem):
            continue
        snapshot = draft_snapshot(library, path.stem)
        if snapshot and snapshot["profile"]["id"] == profile:
            scopes = snapshot["scopes"]
            validate_layers(conversion, scopes, current_layers(library, scopes))

    path = bundle_file(library, profile)
    write(path.parent / "History" / (document["sha256"] + ".json"), document)
    write(path, document)
    return document
