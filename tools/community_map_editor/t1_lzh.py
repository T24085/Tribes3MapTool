"""Decode the byte-aligned adaptive LZH streams used by Tribes terrain."""

from bisect import bisect_right


WINDOW = 4096
LOOKAHEAD = 60
SYMBOLS = 256 + LOOKAHEAD - 2
LEAVES = 2 * SYMBOLS - 1
ROOT = LEAVES - 1


def _distance_codes():
    codes = []
    upper = 0
    for length, count in enumerate((1, 3, 8, 12, 24, 16), start=3):
        for _ in range(count):
            codes.extend([(length, upper)] * (1 << (8 - length)))
            upper += 1
    return codes


DISTANCES = _distance_codes()


class AdaptiveTree:
    def __init__(self):
        self.weight = [1] * SYMBOLS + [0] * (SYMBOLS - 1)
        self.child = list(range(LEAVES, LEAVES + SYMBOLS)) + [0] * (SYMBOLS - 1)
        self.parent = [0] * (LEAVES + SYMBOLS)
        for node in range(SYMBOLS, LEAVES):
            left = 2 * (node - SYMBOLS)
            self.child[node] = left
            self.weight[node] = self.weight[left] + self.weight[left + 1]
        for node in range(LEAVES):
            self._link(node)

    def _link(self, node):
        left = self.child[node]
        self.parent[left] = node
        if left < LEAVES:
            self.parent[left + 1] = node

    def _rescale(self):
        leaves = [(child, (weight + 1) // 2) for child, weight in zip(self.child, self.weight)
                  if child >= LEAVES]
        self.child = [child for child, _ in leaves]
        self.weight = [weight for _, weight in leaves]
        for left in range(0, ROOT, 2):
            weight = self.weight[left] + self.weight[left + 1]
            # The format inserts a new parent after existing equal weights.
            node = bisect_right(self.weight, weight, left + 2)
            self.weight.insert(node, weight)
            self.child.insert(node, left)
        for node in range(LEAVES):
            self._link(node)

    def update(self, symbol):
        if self.weight[ROOT] == 0x8000:
            self._rescale()
        node = self.parent[LEAVES + symbol]
        while True:
            weight = self.weight[node] + 1
            other = bisect_right(self.weight, weight - 1, node + 1) - 1
            if other > node:
                self.weight[node] = self.weight[other]
                self.child[node], self.child[other] = self.child[other], self.child[node]
                self._link(node)
                self._link(other)
                node = other
            self.weight[node] = weight
            node = self.parent[node]
            if not node:
                break


def decode(data, size):
    """Return the decoded bytes and the number of input bytes read by LZH."""
    if not 0 < size <= 16 * 1024 * 1024:
        raise ValueError("Invalid Tribes LZH output length.")
    tree = AdaptiveTree()
    cursor = WINDOW - LOOKAHEAD
    # Darkstar seeds the portion before the initial write cursor with spaces.
    history = [ord(" ")] * cursor + [-1] * LOOKAHEAD
    offset = 0
    available = 0
    bits = 0

    def take(count):
        nonlocal offset, available, bits
        # The legacy stream prefetches another byte even when eight remain.
        while available <= 8:
            if offset == len(data):
                raise ValueError("Truncated Tribes LZH stream.")
            bits = (bits << 8) | data[offset]
            offset += 1
            available += 8
        available -= count
        value = bits >> available
        bits &= (1 << available) - 1
        return value

    result = bytearray()
    while len(result) < size:
        node = tree.child[ROOT]
        while node < LEAVES:
            node = tree.child[node + take(1)]
        symbol = node - LEAVES
        tree.update(symbol)
        if symbol < 256:
            result.append(symbol)
            history[cursor] = symbol
            cursor = (cursor + 1) % WINDOW
            continue

        prefix = take(8)
        length, upper = DISTANCES[prefix]
        for _ in range(length - 2):
            prefix = (prefix << 1) | take(1)
        distance = (upper << 6) + (prefix & 63) + 1
        count = symbol - 253
        if len(result) + count > size:
            raise ValueError("Tribes LZH match exceeds its declared output length.")
        for _ in range(count):
            byte = history[(cursor - distance) % WINDOW]
            if byte < 0:
                raise ValueError("Tribes LZH match uses uninitialized history.")
            result.append(byte)
            history[cursor] = byte
            cursor = (cursor + 1) % WINDOW
    return bytes(result), offset
