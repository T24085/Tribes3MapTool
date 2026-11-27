"""Resolve layered, versioned authoring rules against a verified map intake."""

from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

if __package__:
    from .profile_exchange import active_profile
    from .source_profiles import ANY_DRAFT, NAMES, T1
else:
    from profile_exchange import active_profile
    from source_profiles import ANY_DRAFT, NAMES, T1


PROFILE = Path(__file__).with_name("tribes2_conversion.json")
HEX = re.compile("[0-9a-f]{64}")
DRAFT = re.compile(ANY_DRAFT)
SELECTOR = re.compile(r"(?:game-[a-z0-9-]{1,40}|map-[0-9a-f]{64}|object-[0-9a-f]{64}-[1-9][0-9]{0,4})")
LABEL = re.compile(r"[a-z][a-z0-9_.]{0,47}")


def encode(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def checksum(data):
    return sha256(encode(data)).hexdigest()


def read(path, limit=4*1024*1024):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"{path.name} exceeds the rule file size limit.")
    return json.loads(raw)


def write(path, data):
    raw = encode(data) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def numeric(value):
    return type(value) in (int, float) and -2_000_000 <= value <= 2_000_000 and isfinite(value)


def within(value, limits):
    return numeric(value) and limits[0] <= value <= limits[1]


def value_range(values):
    return (isinstance(values, list) and len(values) == 2
            and all(numeric(value) for value in values) and values[0] < values[1])


def profile_config(profile=None, *, source_profile=None):
    if profile is None:
        profile = read(PROFILE, 64 * 1024)
        if source_profile is not None:
            profile["id"] = source_profile
        if source_profile == T1:
            profile["groups"]["sun"]["source"] = "planet"
            profile["groups"]["terrain"]["source"] = "simterrain"
    if (not isinstance(profile, dict) or set(profile) != {"format", "id", "revision", "groups", "conversions"}
            or type(profile["format"]) is not int or profile["format"] != 1
            or type(profile["revision"]) is not int or profile["revision"] < 1
            or not isinstance(profile["id"], str) or profile["id"] not in NAMES
            or source_profile is not None and profile["id"] != source_profile):
        raise ValueError("Invalid conversion profile.")
    groups, records = profile["groups"], profile["conversions"]
    if (not isinstance(groups, dict) or not groups or not isinstance(records, dict) or not records
            or len(records) > 100):
        raise ValueError("The conversion profile needs groups and conversion records.")
    for key, group in groups.items():
        if (not LABEL.fullmatch(key) or not isinstance(group, dict) or set(group) != {"source"}
                or not isinstance(group["source"], str) or not LABEL.fullmatch(group["source"])):
            raise ValueError("Invalid source group in the conversion profile.")
    for key, record in records.items():
        if not LABEL.fullmatch(key) or not isinstance(record, dict):
            raise ValueError("Invalid conversion record.")
        group, limits, display = (record.get(param) for param in ("group", "range", "display"))
        common = {"formula", "group", "range", "display", "label", "unit"}
        if (not isinstance(group, str) or group not in groups or not value_range(limits) or not isinstance(display, dict)
                or set(display) != {"unit", "factor"} or not numeric(display["factor"]) or display["factor"] <= 0
                or not isinstance(display["unit"], str)
                or any(not isinstance(record.get(param), str) or not record[param].strip()
                       for param in ("label", "unit"))):
            raise ValueError(f"Invalid bounds or units for {key}.")
        formula = record["formula"]
        if "source_field" in record:
            field = record["source_field"]
            if formula not in {"constant", "inverse_span"} or not isinstance(field, str) or not LABEL.fullmatch(field):
                raise ValueError(f"Invalid optional source value for {key}.")
            common.add("source_field")
        if formula == "constant":
            valid = set(record) == common | {"value"} and within(record["value"], limits)
        elif formula == "source":
            valid = (set(record) == common | {"field", "input_unit"}
                     and isinstance(record["field"], str) and LABEL.fullmatch(record["field"])
                     and isinstance(record["input_unit"], str) and bool(record["input_unit"]))
        elif formula == "inverse_span":
            valid = (set(record) == common | {"start", "end", "fallback_span", "numerator", "clamp", "input_unit"}
                     and all(isinstance(record[n], str) and record[n] in records and record[n] != key
                             for n in ("start", "end"))
                     and all(numeric(record[n]) and record[n] > 0 for n in ("fallback_span", "numerator"))
                     and value_range(record["clamp"]) and all(within(v, limits) for v in record["clamp"])
                     and isinstance(record["input_unit"], str) and bool(record["input_unit"]))
        else:
            valid = False
        if not valid:
            raise ValueError(f"Invalid conversion formula for {key}.")
    return profile


def selectors(profile, header, rows):
    sources = header["sources"][0]["sha256"]
    objects = header["files"]["objects.json"]
    if profile["id"] != header["profile"] or not (HEX.fullmatch(sources) and HEX.fullmatch(objects)):
        raise ValueError("The conversion profile does not match the map intake.")
    object_context = checksum({"mission": sources, "objects": objects})
    groups = {}
    for group, rule in profile["groups"].items():
        matches = [row["id"] for row in rows if row["source_class"].casefold() == rule["source"]
                   and row["status"] != "excluded"]
        if len(matches) == 1:
            groups[group] = f"object-{object_context}-{matches[0]}"
    return {"game": "game-" + profile["id"], "map": "map-" + sources, "objects": groups}


def revision_path(library, scope):
    if not SELECTOR.fullmatch(scope):
        raise ValueError("Invalid rule scope.")
    return library / "ConversionRules" / (scope + ".json")


def check_event(event):
    if (not isinstance(event, dict) or set(event) != {"previous", "values", "sha256"}
            or (event["previous"] is not None and
                (not isinstance(event["previous"], str) or not HEX.fullmatch(event["previous"])))
            or not isinstance(event["values"], dict)
            or any(not LABEL.fullmatch(key) or not numeric(value) for key, value in event["values"].items())
            or event["sha256"] != checksum({"previous": event["previous"], "values": event["values"]})):
        raise ValueError("A rule revision has changed or is malformed.")


def history(library, scope):
    path = revision_path(library, scope)
    if not path.is_file():
        return []
    events = read(path)
    if not isinstance(events, list) or not events:
        raise ValueError("Invalid rule history.")
    previous = None
    for event in events:
        check_event(event)
        if event["previous"] != previous:
            raise ValueError("Rule history has a missing revision.")
        previous = event["sha256"]
    return events


def validate_values(profile, values, group=None):
    records = profile["conversions"]
    for key, value in values.items():
        rule = records.get(key)
        if rule is None or (group is not None and group != rule["group"]) or not within(value, rule["range"]):
            raise ValueError(f"The value for {key} is outside the conversion rule or its allowed range.")


def edit_scope(library, scope, profile, values, expected, *, group=None):
    validate_values(profile, values, group)
    chain = history(library, scope)
    latest = chain[-1]["sha256"] if chain else None
    if latest != expected:
        raise ValueError("These rules changed while the dialog was open. Reopen it to see the current revision.")
    if values == (chain[-1]["values"] if chain else {}):
        return chain
    change = {"previous": latest, "values": values}
    record = {**change, "sha256": checksum(change)}
    chain.append(record)
    write(revision_path(library, scope), chain)
    return chain


def current_layers(library, scopes):
    layers = {}
    if library is not None:
        for scope in {scopes["game"], scopes["map"], *scopes["objects"].values()}:
            chain = history(library, scope)
            if chain:
                layers[scope] = chain[-1]
    return layers


def project(profile, settings, scopes, layers):
    records = profile["conversions"]
    results, pending = {}, set()

    def resolve(key):
        if key in results:
            return results[key]["value"]
        if key in pending:
            raise ValueError("Circular conversion rules.")
        pending.add(key)
        rule = records[key]
        group = rule["group"]
        object_scope = scopes["objects"].get(group)
        level = "profile"
        value = None
        for name, scope in (("object", object_scope), ("map", scopes["map"]), ("game", scopes["game"])):
            layer = layers.get(scope, {}).get("values", {})
            if key in layer:
                value, level = layer[key], name
                break
        else:
            formula = rule["formula"]
            authored = settings.get(group, {}).get(rule.get("source_field"))
            if authored is not None:
                value = authored
            elif formula == "constant":
                value = rule["value"]
            elif formula == "source":
                value = settings.get(group, {}).get(rule["field"])
            elif formula == "inverse_span":
                start, end = resolve(rule["start"]), resolve(rule["end"])
                if start is not None:
                    span = end - start if end is not None else rule["fallback_span"]
                    if span > 0:
                        lower, upper = rule["clamp"]
                        value = min(upper, max(lower, rule["numerator"] / span))
        if value is not None and not within(value, rule["range"]):
            raise ValueError(f"The conversion result for {key} exceeds its allowed range.")
        results[key] = {"value": value, "layer": level}
        pending.remove(key)
        return value

    for key in records:
        resolve(key)
    return results


def validate_layers(profile, scopes, layers):
    valid = set(scopes["objects"].values()) | {scopes["game"], scopes["map"]}
    if not isinstance(layers, dict) or layers.keys() - valid:
        raise ValueError("Unexpected rule scopes for this source.")
    for scope, event in layers.items():
        check_event(event)
        group = next((key for key, ident in scopes["objects"].items() if scope == ident), None)
        validate_values(profile, event["values"], group)


def create_snapshot(library, header, rows, manifest, options, *, replay=None, pipeline=None, catalog=None, bundle=None):
    if __package__:
        from .map_pipeline import pipeline_config, validate_pipeline
        from .object_catalog import load_catalog
    else:
        from map_pipeline import pipeline_config, validate_pipeline
        from object_catalog import load_catalog

    digest = sha256_file(manifest)
    source_profile = header["profile"]
    if bundle is None and replay is None:
        bundle = active_profile(library, source_profile) or {}
    source = replay["profile"] if replay is not None else bundle.get("conversion")
    profile = profile_config(source, source_profile=source_profile)
    scopes = selectors(profile, header, rows)
    layers = replay["layers"] if replay is not None else current_layers(library, scopes)
    validate_layers(profile, scopes, layers)
    data = {
        "format": 3 if replay is None else replay["format"], "intake_sha256": digest, "profile": profile, "scopes": scopes,
        "layers": layers, "results": project(profile, header.get("environment", {}), scopes, layers),
        "options": options,
    }
    if data["format"] >= 2:
        document = (pipeline_config(library, snapshot=replay, source_profile=source_profile, bundle=bundle)
                    if pipeline is None else validate_pipeline(pipeline))
        if document["profile"] != source_profile:
            raise ValueError("The conversion pipeline does not match the intake profile.")
        data["pipeline"] = document
    if data["format"] >= 3:
        data["catalog"] = load_catalog(library, snapshot=replay, document=catalog,
                                       source_profile=source_profile, bundle=bundle).document
    snapshot = {**data, "sha256": checksum(data)}
    if replay is not None and snapshot != replay:
        raise ValueError("The recorded rule revision does not match this intake and its original build options.")
    return snapshot


def sha256_file(path):
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rule_tags_match(tags, snapshot):
    """Whether a draft's T3RuleSet_ tags match its recorded rules snapshot.

    The importer tags every terrain tile, so a tiled terrain carries the same tag several times;
    what must hold is exactly one distinct rule set, equal to the snapshot. A draft with neither
    tags nor snapshot (made before rules were recorded) also matches. (Tribes3MapTool#26)
    """
    if not tags and snapshot is None:
        return True
    return snapshot is not None and set(tags) == {"T3RuleSet_" + snapshot["sha256"]}


def draft_rules_path(library, draft):
    if not isinstance(draft, str) or not DRAFT.fullmatch(draft):
        raise ValueError("Open a supported map draft.")
    return library / "DraftRules" / (draft + ".json")


def draft_snapshot(library, draft):
    path = draft_rules_path(library, draft)
    if not path.is_file():
        return None
    return verify_snapshot(read(path))


def verify_snapshot(snapshot):
    fields = {"format", "intake_sha256", "profile", "scopes", "layers", "results", "options", "sha256"}
    if isinstance(snapshot, dict):
        if snapshot.get("format") in (2, 3):
            fields.add("pipeline")
        if snapshot.get("format") == 3:
            fields.add("catalog")
    if (not isinstance(snapshot, dict)
            or set(snapshot) != fields or type(snapshot["format"]) is not int or snapshot["format"] not in (1, 2, 3)
            or snapshot["sha256"] != checksum({key: value for key, value in snapshot.items() if key != "sha256"})):
        raise ValueError("The draft's recorded conversion rules could not be verified.")
    return snapshot
