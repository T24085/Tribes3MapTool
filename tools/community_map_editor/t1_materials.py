"""Read texture names from a local Tribes TS::MaterialList."""

import struct


CLASS_NAME = b"TS::MaterialList"
HEADER = struct.Struct("<4sIH16sIII")
STRIDES = {2: 48, 3: 60, 4: 64}


def material_names(data, *, keep_path=False):
    if len(data) < HEADER.size:
        raise ValueError("Truncated Tribes material list.")
    tag, size, length, kind, version, details, count = HEADER.unpack_from(data)
    stride = STRIDES.get(version)
    end = size + 8
    if (tag != b"PERS" or length != len(CLASS_NAME) or kind != CLASS_NAME or stride is None
            or not 1 <= details <= 32 or not 1 <= count <= 256
            or end != HEADER.size + count * details * stride or not 0 <= len(data) - end <= 3):
        raise ValueError("Unsupported or invalid Tribes material list.")
    names = []
    for index in range(count):
        # Each detail set has the same slot offsets. Terrain uses the first set.
        offset = HEADER.size + index * stride + 16
        value = data[offset:offset + 32].split(b"\0", 1)[0].decode("latin-1").replace("\\", "/")
        name = value if keep_path else value.rsplit("/", 1)[-1]
        names.append(name if name and name.isprintable() else None)
    return names
