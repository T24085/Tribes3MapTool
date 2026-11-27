"""Decode LZO1X blocks used by Tribes: Ascend packages and texture caches.

Pure Python, standard-library only, no keys involved. Ascend cooks its packages
and its Textures.tfc mips with UE3's LZO codec (compression flag 2, bulk flag
0x10) instead of zlib. Each block is a plain LZO1X stream, so a bounded
"safe" decoder is enough: every read and back-reference is range-checked and
the output may never exceed the size recorded by the package.
"""

M2_MAX_OFFSET = 0x0800


def _run_length(src, ip, base):
    """Read an LZO extended length: zero bytes add 255 each, then one final byte."""
    extra = 0
    limit = len(src)
    while ip < limit and src[ip] == 0:
        extra += 255
        ip += 1
    if ip >= limit:
        raise ValueError("Truncated LZO length.")
    return base + extra + src[ip], ip + 1


def _copy_match(out, distance, length, expected):
    start = len(out) - distance
    if distance <= 0 or start < 0:
        raise ValueError("LZO back-reference points before the block.")
    if len(out) + length > expected:
        raise ValueError("LZO output exceeds its recorded size.")
    if distance >= length:
        out += out[start:start + length]
    else:
        for index in range(length):
            out.append(out[start + index])


def _copy_literals(out, src, ip, count, expected):
    end = ip + count
    if end > len(src):
        raise ValueError("Truncated LZO literal run.")
    if len(out) + count > expected:
        raise ValueError("LZO output exceeds its recorded size.")
    out += src[ip:end]
    return end


def decompress(src, expected):
    """Return exactly ``expected`` bytes decoded from one LZO1X block."""
    src = memoryview(src)
    limit = len(src)
    if not limit or expected < 0:
        raise ValueError("Empty LZO block.")
    out = bytearray()
    ip = 0

    def byte():
        nonlocal ip
        if ip >= limit:
            raise ValueError("Truncated LZO block.")
        value = src[ip]
        ip += 1
        return value

    # state: 0 = a literal run may follow, 4 = just after a literal run of 4+ bytes,
    # 1..3 = just after a match that carried that many trailing literals.
    state = 0
    t = None
    if src[0] > 17:
        ip = 1
        count = src[0] - 17
        ip = _copy_literals(out, src, ip, count, expected)
        state = 4 if count >= 4 else count
    while True:
        t = byte()
        if t < 16:
            if state == 0:
                if t == 0:
                    count, ip = _run_length(src, ip, 15)
                else:
                    count = t
                ip = _copy_literals(out, src, ip, count + 3, expected)
                state = 4
                continue
            if state == 4:
                distance = 1 + M2_MAX_OFFSET + (t >> 2) + (byte() << 2)
                length = 3
            else:
                distance = 1 + (t >> 2) + (byte() << 2)
                length = 2
            trailing = t & 3
        elif t >= 64:
            distance = 1 + ((t >> 2) & 7) + (byte() << 3)
            length = (t >> 5) + 1
            trailing = t & 3
        elif t >= 32:
            length = t & 31
            if length == 0:
                length, ip = _run_length(src, ip, 31)
            length += 2
            low, high = byte(), byte()
            distance = 1 + (low >> 2) + (high << 6)
            trailing = low & 3
        else:
            length = t & 7
            if length == 0:
                length, ip = _run_length(src, ip, 7)
            length += 2
            low, high = byte(), byte()
            distance = ((t & 8) << 11) + (low >> 2) + (high << 6)
            trailing = low & 3
            if distance == 0:
                if length != 3:
                    raise ValueError("Invalid LZO end marker.")
                break
            distance += 0x4000
        _copy_match(out, distance, length, expected)
        if trailing:
            ip = _copy_literals(out, src, ip, trailing, expected)
        state = trailing
    if ip != limit:
        raise ValueError("Unexpected data after the LZO end marker.")
    if len(out) != expected:
        raise ValueError("LZO block did not match its recorded length.")
    return bytes(out)
