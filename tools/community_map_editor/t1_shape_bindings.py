"""Read literal StaticShapeData references without running mission scripts."""

from collections import defaultdict
from hashlib import sha256
from pathlib import PurePosixPath

from t1_terrain import resource_name
from t2_reader import IDENT, TOKEN


SHAPE_CLASSES = {"staticshape", "tsstatic"}


def dts_name(value):
    path = resource_name(value.strip())
    if not PurePosixPath(path).suffix:
        path += ".dts"
    if not path.casefold().endswith(".dts"):
        raise ValueError("The source shape must reference a DTS file.")
    return path


def literal_datablocks(raw, requested):
    tokens = [match[0] for match in TOKEN.finditer(raw.decode("latin-1"))
              if not match[0].isspace() and not match[0].startswith(("//", "/*"))]
    depth = 0
    for index, token in enumerate(tokens):
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
        if depth or token.casefold() != "staticshapedata" or index + 2 >= len(tokens):
            continue
        name, opening = tokens[index + 1:index + 3]
        if opening != "{" or not IDENT.fullmatch(name) or name.casefold() not in requested:
            continue
        start = index + 3
        end = start
        while end < len(tokens) and tokens[end] not in ("{", "}"):
            end += 1
        if end == len(tokens) or tokens[end] != "}" or tokens[end + 1:end + 2] != [";"]:
            continue
        assignments = [j for j in range(start, end)
                       if (j == start or tokens[j - 1] == ";") and tokens[j].casefold() == "shapefile"]
        if len(assignments) == 1:
            j = assignments[0]
            equals, value, terminator = (tokens[j+1:j+4] + ["", "", ""])[:3]
            if equals == "=" and value.startswith('"') and terminator == ";":
                try:
                    yield name.casefold(), dts_name(value[1:-1])
                except ValueError:
                    continue


def bind_shape_rows(rows, scripts, primary, receipts):
    pending = []
    for row in rows:
        if row["status"] != "review" or row["source_class"].casefold() not in SHAPE_CLASSES:
            continue
        props = {key.casefold(): value for key, value in row["properties"].items()}
        path = props.get("shapename") or props.get("shapefile")
        if path:
            try:
                row["properties"] = {key: value for key, value in row["properties"].items()
                                     if key.casefold() != "shapename"} | {"shapename": dts_name(path)}
            except ValueError as error:
                row["reason"] = str(error)
            continue
        block = props.get("datablock", "").strip().casefold()
        if block:
            pending.append((row, block))
    if not pending:
        return

    definitions = defaultdict(list)
    requested = {block for _, block in pending}
    script_bytes = 0
    for asset in scripts:
        try:
            name, origin, raw = asset.read()
        except (OSError, ValueError):
            continue
        script_bytes += len(raw)
        if script_bytes > 32 * 1024 * 1024:
            raise ValueError("The selected Tribes shape data scripts exceed the import limit.")
        for block, path in literal_datablocks(raw, requested):
            receipt = dict(name=PurePosixPath(name).name, origin=origin, sha256=sha256(raw).hexdigest())
            definitions[block].append((asset.container != primary, path, receipt))
    for row, block in pending:
        choices = definitions.get(block)
        if not choices:
            row["reason"] = "No literal DTS shapeFile was found for the source StaticShapeData."
            continue
        priority = min(choice[0] for choice in choices)
        preferred = {path.casefold(): (path, source) for rank, path, source in choices if rank == priority}
        if len(preferred) != 1:
            row["reason"] = "Several source StaticShapeData definitions name different DTS files."
            continue
        path, source = next(iter(preferred.values()))
        row["properties"]["shapename"] = path
        receipts[source["origin"]] = source
