"""Data-driven source class definitions, typed fields and feature coverage."""

import math
from pathlib import Path
import re
if __package__:
    from .conversion_rules import read
    from .profile_exchange import active_profile
    from .source_profiles import ASCEND, NAMES, T1, UNREAL_PROFILES, VENGEANCE, config_name, selected_profile
else:
    from conversion_rules import read
    from profile_exchange import active_profile
    from source_profiles import ASCEND, NAMES, T1, UNREAL_PROFILES, VENGEANCE, config_name, selected_profile


DEFAULT = Path(__file__).with_name("tribes2_catalog.json")
SUPPORT = {"native": "Native", "mapped": "Mapped", "retained": "Source only", "unsupported": "Unsupported"}
FIELDS = {"text", "number", "integer", "boolean", "vector", "color"}
ID = re.compile(r"[a-z][a-z0-9-]{0,47}")


def display_value(definition, raw):
    if type(raw) not in (str, int, float) or not str(raw).strip():
        raise ValueError("Expected a value.")
    kind = definition["type"]
    if kind == "text":
        return str(raw)
    if kind == "boolean":
        names = {"true": "Yes", "1": "Yes", "false": "No", "0": "No"}
        choice = names.get(str(raw).strip().casefold())
        if choice is None:
            raise ValueError("Expected true, false, 1, or 0.")
        return choice
    try:
        numbers = tuple(float(item) for item in str(raw).split())
    except ValueError as exc:
        raise ValueError("Expected numeric data.") from exc
    count = (3, 4) if kind == "color" else (definition.get("components", 1),)
    minimum = definition.get("min", 0 if kind == "color" else -2e6)
    maximum = definition.get("max", 1 if kind == "color" else 2e6)
    if len(numbers) not in count:
        raise ValueError(f"Expected {' or '.join(map(str, count))} numeric values.")
    if not all(math.isfinite(n) and minimum <= n <= maximum for n in numbers):
        raise ValueError(f"Expected values between {minimum:g} and {maximum:g}.")
    if kind == "integer" and not numbers[0].is_integer():
        raise ValueError("Expected a whole number.")
    return "  ".join(format(n, "g") for n in numbers)


class ObjectCatalog:
    def __init__(self, document):
        if (not isinstance(document, dict) or set(document) != {
                "format", "profile", "revision", "fields", "classes", "features", "relationships", "layout",
        } or document["format"] != 1 or document["profile"] not in NAMES
                or type(document["revision"]) is not int or document["revision"] < 1):
            raise ValueError("Invalid Tribes 2 object profile.")
        self.document = document
        self.fields = document["fields"]
        self.features = document["features"]
        self.layout = document["layout"]
        self.relationships = document["relationships"]
        self.aliases = {}
        self.definitions = {}
        if not all(isinstance(value, dict) for value in (
                self.fields, self.features, self.layout, self.relationships, document["classes"])):
            raise ValueError("Invalid sections in the Tribes 2 object profile.")
        self._validate()
        for name in document["classes"]:
            self._class(name, set())

    def _validate(self):
        for field, row in self.fields.items():
            if (not isinstance(row, dict) or not good_label(row.get("label")) or row.get("type") not in FIELDS
                    or row.keys() - {"label", "type", "unit", "components", "min", "max", "default", "placement"}):
                raise ValueError(f"Invalid object field: {field}.")
            if (row.get("type") == "vector" and row.get("components") not in (2, 3, 4)
                    or any(type(row[part]) not in (int, float) or not math.isfinite(row[part])
                           for part in ("min", "max") if part in row)
                    or type(row.get("placement", False)) is not bool or not isinstance(row.get("unit", ""), str)):
                raise ValueError(f"Invalid field constraints for {field}.")
            if "default" in row:
                display_value(row, row["default"])
        for key, row in self.features.items():
            if (not ID.fullmatch(key) or not isinstance(row, dict) or set(row) != {"label", "draft", "host", "detail"}
                    or not good_label(row["label"]) or row["draft"] not in SUPPORT or row["host"] not in SUPPORT
                    or not isinstance(row["detail"], str)):
                raise ValueError("Invalid feature support record.")
        if {"unknown", "ctf", "equipment", "stations"} - self.features.keys():
            raise ValueError("The profile is missing required coverage features.")
        if (self.relationships.keys() - {"parent", "team", "power", "model"}
                or not all(good_label(value) for value in self.relationships.values())):
            raise ValueError("Invalid relationship layout.")
        for section, keys, required in (
                ("tabs", {"all", "features"}, {"all"}),
                ("columns", {"object", "status", "type", "reason", "feature"}, {"object", "status", "reason"})):
            rows = self.layout.get(section)
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise ValueError("Invalid object review tabs or columns.")
            identities = [row.get("id") for row in rows]
            if (any(not isinstance(key, str) or key not in keys for key in identities)
                    or not required <= set(identities) or len(identities) != len(set(identities))
                    or not all(good_label(row.get("label")) for row in rows)
                    or section == "columns" and any(type(row.get("width")) is not int
                                                    or not 65 <= row["width"] <= 650 for row in rows)):
                raise ValueError(f"Invalid object review {section}.")
        if (not isinstance(self.layout.get("filters"), list)
                or any(name not in ("query", "review", "feature") for name in self.layout["filters"])):
            raise ValueError("Invalid object review filters.")

    def _class(self, name, active):
        if name in self.definitions:
            return self.definitions[name]
        if name in active:
            raise ValueError("Circular object class inheritance.")
        source = self.document["classes"].get(name)
        if (not isinstance(source, dict) or not good_label(source.get("label"))
                or not isinstance(source.get("feature"), str) or source["feature"] not in self.features
                or type(source.get("transform", False)) is not bool):
            raise ValueError(f"Invalid object class: {name}.")
        parent = source.get("base")
        inherited = self._class(parent, active | {name}) if parent else {"fields": (), "required": ()}
        arrays = [source.get(key, []) for key in ("fields", "required", "aliases")]
        if any(not isinstance(values, list) or not all(isinstance(key, str) for key in values) for values in arrays):
            raise ValueError(f"Invalid field or alias list for {name}.")
        fields = (*inherited["fields"], *arrays[0])
        required = (*inherited["required"], *arrays[1])
        if set(fields) - self.fields.keys() or set(required) - set(fields):
            raise ValueError(f"Unknown object field in {name}.")
        definition = {**source, "fields": tuple(dict.fromkeys(fields)), "required": tuple(dict.fromkeys(required))}
        for alias in arrays[2]:
            key = alias.casefold()
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) or key in self.aliases:
                raise ValueError(f"Invalid or duplicate class alias: {alias}.")
            self.aliases[key] = definition
        self.definitions[name] = definition
        return definition

    def definition(self, row):
        return self.aliases.get(row.get("conversion_class", row["source_class"]).casefold())


def good_label(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 100 and value.isprintable()


def load_catalog(library=None, *, snapshot=None, recorded=False, document=None, source_profile=None, bundle=None):
    source_profile = selected_profile(source_profile, snapshot)
    if document is None:
        if snapshot is not None:
            document = snapshot.get("catalog")
        if document is None:
            if bundle is None and not recorded and snapshot is None:
                bundle = active_profile(library, source_profile)
            if bundle and not recorded:
                document = bundle["catalog"]
        if document is None:
            override = library / config_name(source_profile, "catalog") if library is not None else None
            if not recorded and override is not None and override.is_file():
                document = read(override, 128*1024)
            else:
                document = read(DEFAULT, 128*1024)
                if source_profile == T1:
                    document["profile"] = T1
                    fields, classes = document["fields"], document["classes"]
                    fields["rotation"].update(components=3, unit="radians", default="0 0 0")
                    fields["filename"] = {"label": "Source model", "type": "text"}
                    classes["groups"]["aliases"].append("teamgroup")
                    classes["terrain"].update(aliases=["simterrain"], fields=["visibledistance", "fogcolor"])
                    classes["boundary"].update(aliases=["missioncenterpos"], fields=[])
                    classes["interior"].update(aliases=["interiorshape"], fields=["filename"], required=["position", "filename"])
                    classes["lighting"].update(aliases=["planet"], fields=[])
                    classes["marker"] = {"label": "Legacy marker", "aliases": ["marker"], "base": "world-object", "feature": "metadata"}
                    supported_host = {"terrain", "ctf", "boundaries", "buildings", "props", "lighting", "minimap"}
                    for key, feature in document["features"].items():
                        if key not in supported_host:
                            feature["host"] = "unsupported"
                    document["features"]["terrain"]["detail"] = "Authored 16-bit heightmap with explicit metric placement."
                    document["features"]["buildings"]["detail"] = "Static interiors and supported source materials."
                    document["features"]["props"]["detail"] = "Darkstar DTS props in their default pose. Animation and authored collision details remain in review."
                    document["features"]["sky"]["detail"] = "Normalized fog distances are mapped. Source sky artwork stays in review."
                    document["features"]["lighting"]["detail"] = "Default and alarm lightmaps with mission overlays. Timed tracks need review."
                elif source_profile in UNREAL_PROFILES.values():
                    document["profile"] = source_profile
                    document["features"]["lighting"].update(
                        label="Map lighting", detail="Saved global lighting and supported local light actors.")
                    classes = {}
                    for key, label, feature in (
                        ("unrealterrain", "Metric heightfield", "terrain"),
                        ("unrealbounds", "Playable rectangle", "boundaries"),
                        ("unrealctfmarker", "Explicit CTF objective", "ctf"),
                        ("unrealmeshactor", "Mapped Unreal mesh actor", "props"),
                        ("unreallight", "Placed light", "lighting"),
                    ):
                        classes[key] = dict(label=label, aliases=[key], feature=feature,
                                            transform=key in {"unrealmeshactor", "unreallight"})
                    if source_profile == VENGEANCE:
                        features = document["features"]
                        features["trigger-links"] = dict(label="Trigger links", draft="retained", host="retained",
                                                         detail="Review saved event senders and their elevator targets.")
                        classes["unrealtriggerevent"] = dict(label="Proximity trigger", aliases=["unrealtriggerevent"],
                                                            feature="trigger-links")
                    document["classes"] = classes
    catalog = ObjectCatalog(document)
    if catalog.document["profile"] != source_profile:
        raise ValueError("The object catalog does not match the source profile.")
    return catalog


def source_fields(row, catalog):
    definition = catalog.definition(row)
    props = {key.casefold(): value for key, value in row["properties"].items()}
    seen = set()
    if definition:
        for key in definition["fields"]:
            field = catalog.fields[key]
            seen.add(key)
            raw = props[key] if key in props else field.get("default")
            if raw is None and key not in definition["required"]:
                continue
            status, problem = "Source" if key in props else "Default", ""
            try:
                value = display_value(field, raw)
            except ValueError as exc:
                status, value = "Needs review", "" if raw is None else str(raw)
                problem = "Required value is missing." if raw is None else str(exc)
            yield {"key": key, "label": field["label"], "value": value, "unit": field.get("unit", ""),
                   "status": status, "problem": problem, "placement": field.get("placement", False)
                   or key in definition["required"]}
    for key in sorted(props.keys() - seen):
        yield {"key": key, "label": key, "value": str(props[key]), "unit": "",
               "status": "Source only", "problem": "", "placement": False}


def classify(rows, terrain, catalog):
    from mesh_mappings import placement_transform
    from mission_equipment import EQUIPMENT_ROLES

    report = {}
    for row in rows:
        definition = catalog.definition(row)
        cls = row["source_class"].casefold()
        props = {key.casefold(): str(value).casefold() for key, value in row["properties"].items()}
        objective = (row["status"] == "marker" or cls == "spawnsphere" or cls == "item" and props.get("datablock") == "flag"
                     or row["reason"].startswith("Invalid spawn marker."))
        feature = "ctf" if objective else definition["feature"] if definition else "unknown"
        if role := EQUIPMENT_ROLES.get((cls, props.get("datablock", ""))):
            feature = "stations" if role in ("inventory_station", "vehicle_station") else "equipment"
        blocked, notices, extras = [], [], []
        if row["status"] != "excluded":
            if not definition:
                blocked.append("No class definition in this conversion profile.")
            elif catalog.features[feature]["draft"] == "unsupported":
                blocked.append("This source feature has no Unreal conversion.")
            elif objective and row["status"] != "marker":
                blocked.append(row["reason"])
            elif cls in ("tsstatic", "staticshape") and not role and not props.get("shapename", "").strip():
                blocked.append(row["reason"] if catalog.document["profile"] == T1 else "No source shape file for this object.")
            for field in source_fields(row, catalog):
                if field["problem"]:
                    reason = f"{field['label']}: {field['problem']}"
                    placement = field["placement"] and (objective or definition and definition.get("transform"))
                    (blocked if placement else notices).append(reason)
                elif field["status"] == "Source only":
                    extras.append(field["key"])
            if definition and definition.get("transform") and not blocked:
                try:
                    placement_transform(row, terrain)
                except ValueError as exc:
                    blocked.append(str(exc))
        report[row["id"]] = {"feature": feature, "blocked": blocked, "notices": notices,
                             "source_only_fields": extras}
    return report


def build_rows(rows, coverage):
    return [
        {**row, "status": "excluded", "reason": " · ".join(coverage[row["id"]]["blocked"])}
        if coverage[row["id"]]["blocked"] else row
        for row in rows
    ]


def applicable_mappings(mappings, source_rows, active_rows):
    from mesh_mappings import source_reference

    allowed = {ref for row in source_rows if (ref := source_reference(row))}
    if not isinstance(mappings, dict) or mappings.keys() - allowed:
        raise ValueError("Mesh mappings contain an unknown source object reference.")
    active = {ref for row in active_rows if (ref := source_reference(row))}
    return {ref: asset for ref, asset in mappings.items() if ref in active}
