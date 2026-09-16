"""Byte-accounting helper shared by the format readers.

Every reader registers each byte range it interprets with Acct.add(); report()
returns the gaps (bytes nobody explained) and overlaps (bytes claimed twice).
A file is "fully accounted" when both lists are empty.
"""


class Acct:
    def __init__(self, size):
        self.size = size
        self.regions = []

    def add(self, off, n, label):
        if n > 0:
            self.regions.append((off, n, label))

    def report(self):
        gaps, overlaps = [], []
        pos = 0
        for off, n, label in sorted(self.regions):
            if off > pos:
                gaps.append((pos, off - pos))
            elif off < pos:
                overlaps.append((off, pos - off, label))
            pos = max(pos, off + n)
        if pos < self.size:
            gaps.append((pos, self.size - pos))
        if pos > self.size:
            overlaps.append((self.size, pos - self.size, "PAST-EOF"))
        return gaps, overlaps

    def summary(self):
        gaps, overlaps = self.report()
        if not gaps and not overlaps:
            return "fully accounted"
        parts = []
        if gaps:
            parts.append("UNACCOUNTED " + ", ".join(f"0x{o:X}+{n}" for o, n in gaps))
        if overlaps:
            parts.append("OVERLAP " + ", ".join(f"0x{o:X}+{n}({l})" for o, n, l in overlaps))
        return "; ".join(parts)
