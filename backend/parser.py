"""Measurement parsing utilities for extracting listing measurements."""

import re
from fractions import Fraction
from typing import Optional, Tuple


class MeasurementParser:
    """Parses clothing measurements from text descriptions."""
    
    NUM = r'(?P<val>\d+(?:\.\d+)?(?:\s+\d\/\d)?)'
    UNIT = r'(?P<unit>\s*(?:cm|mm|in|inch|inches|["″"]))?'
    LINE_LABEL_GAP = 40
    P2P_LABELS = r'(?:p2p|pit\s*[- ]?to\s*[- ]?pit|pit[- ]?to[- ]?pit|pit\s*to\s*pit|chest|width|across\s*chest)'
    LENGTH_LABELS = (
        r'(?:length|top\s*to\s*bottom|back\s*length|hps\s*to\s*hem|'
        r'neck\s*to\s*hem|shoulder\s*to\s*hem|'
        r'collar\s*(?:[- ]?down|to\s*(?:bottom|hem)))'
    )
    WAIST_LABELS = r'(?:waist|waistband|across\s*waist)'
    INSEAM_LABELS = r'(?:inseam)'
    RISE_LABELS = r'(?:front\s*rise|rise)'
    LEG_OPENING_LABELS = r'(?:leg\s*opening|bottom\s*hem|bottom\s*opening|hem\s*opening|ankle\s*opening|leg\s*hem|leg)'
    
    RE_P2P = re.compile(rf'\b{P2P_LABELS}\b[^0-9]{{0,10}}{NUM}{UNIT}', re.I)
    RE_LENGTH = re.compile(rf'\b{LENGTH_LABELS}\b[^0-9]{{0,10}}{NUM}{UNIT}', re.I)
    RE_WAIST = re.compile(rf'\b{WAIST_LABELS}\b[^0-9]{{0,10}}{NUM}{UNIT}', re.I)
    RE_INSEAM = re.compile(rf'\b{INSEAM_LABELS}\b[^0-9]{{0,10}}{NUM}{UNIT}', re.I)
    RE_RISE = re.compile(rf'\b{RISE_LABELS}\b[^0-9]{{0,10}}{NUM}{UNIT}', re.I)
    RE_LEG_OPENING = re.compile(rf'\b{LEG_OPENING_LABELS}\b[^0-9]{{0,10}}{NUM}{UNIT}', re.I)
    RE_PAIR_X = re.compile(
        r'\b'
        r'(?P<w>\d+(?:\.\d+)?)(?P<u1>\s*(?:cm|mm|in|inch|inches|["″"]))?'
        r'\s*[x×]\s*'
        r'(?P<l>\d+(?:\.\d+)?)(?P<u2>\s*(?:cm|mm|in|inch|inches|["″"]))?'
        r'\b', re.I
    )
    RE_BOTTOMS_PAIR = re.compile(
        r'\b'
        r'(?P<waist>\d+(?:\.\d+)?)(?P<u1>\s*(?:cm|mm|in|inch|inches|["″"]))?'
        r'\s*[x×]\s*'
        r'(?P<inseam>\d+(?:\.\d+)?)(?P<u2>\s*(?:cm|mm|in|inch|inches|["″"]))?'
        r'\b',
        re.I,
    )
    RE_BOTTOMS_WL_PAIR = re.compile(
        r'\bw\s*(?P<waist>\d+(?:\.\d+)?)\b'
        r'[^0-9\n]{0,12}'
        r'l\s*(?P<inseam>\d+(?:\.\d+)?)\b',
        re.I,
    )
    RE_BOTTOMS_SIZE_HINT = re.compile(r'\bsize[:\s]+(?P<waist>\d+(?:\.\d+)?)\b', re.I)
    RE_BOTTOMS_FITS_WAIST = re.compile(r'\bfits\s+a\s+(?P<waist>\d+(?:\.\d+)?)\s*waist\b', re.I)

    def to_inches(self, num_str: str, unit_str: str = "") -> float:
        """Convert a measurement string to inches."""
        s = (num_str or "").strip().replace("\u2033", '"').replace("\u201d", '"')
        if " " in s and "/" in s:
            a, b = s.split(None, 1)
            value = float(a) + float(Fraction(b))
        elif "/" in s:
            value = float(Fraction(s))
        else:
            value = float(s)
        
        u = (unit_str or "").lower().strip()
        if u.startswith("cm"):
            return value / 2.54
        return value

    def _extract_labeled_value_from_line(self, line: str, label_pattern: str) -> Optional[float]:
        """Extract a labeled measurement from a single line, supporting either label order."""
        patterns = (
            re.compile(rf'\b{label_pattern}\b[^0-9\n]{{0,{self.LINE_LABEL_GAP}}}{self.NUM}{self.UNIT}', re.I),
            re.compile(rf'{self.NUM}{self.UNIT}[^a-z0-9\n]{{0,{self.LINE_LABEL_GAP}}}\b{label_pattern}\b', re.I),
        )
        for pattern in patterns:
            match = pattern.search(line)
            if not match:
                continue
            try:
                return self.to_inches(match.group("val"), match.group("unit") or "")
            except Exception:
                continue
        return None

    def _extract_pair_from_line(
        self,
        line: str,
        pair_regex: re.Pattern,
        *,
        skip_if_contains: tuple[str, ...] = (),
    ) -> Optional[Tuple[float, float]]:
        """Extract a WxL-style pair from a single line with optional guard words."""
        lowered = line.lower()
        if any(token in lowered for token in skip_if_contains):
            return None

        match = pair_regex.search(line)
        if not match:
            return None

        try:
            first = self.to_inches(match.group(1), match.group(2) or "")
            second = self.to_inches(match.group(3), match.group(4) or "")
        except Exception:
            return None

        return first, second

    def _extract_bottoms_waist_hint(self, text: str) -> Optional[float]:
        """Best-effort waist hint extraction for flat-measured bottoms."""
        for raw_line in text.splitlines():
            line = raw_line.strip().lower()
            if not line:
                continue

            for pattern in (
                self.RE_BOTTOMS_WL_PAIR,
                self.RE_BOTTOMS_PAIR,
                self.RE_BOTTOMS_FITS_WAIST,
                self.RE_BOTTOMS_SIZE_HINT,
            ):
                match = pattern.search(line)
                if not match:
                    continue
                try:
                    return float(match.group("waist"))
                except Exception:
                    continue

        return None

    def _normalize_bottoms_waist(self, waist: Optional[float], text: str) -> Optional[float]:
        """Convert laid-flat waist measurements to full-waist values when strongly indicated."""
        if waist is None:
            return None

        if waist > 22:
            return waist

        doubled = waist * 2
        waist_hint = self._extract_bottoms_waist_hint(text)
        if waist_hint is not None and abs(doubled - waist_hint) <= 1.5:
            return doubled

        return doubled if 24 <= doubled <= 50 else waist

    def _split_measurement_segments(self, line: str) -> list[str]:
        """Split dense inline measurement lines into smaller fragments."""
        segments = [segment.strip(" ()[]") for segment in re.split(r"[,;&|]+", line) if segment.strip()]
        return segments or [line]

    def extract_tops(self, text: str) -> Tuple[Optional[float], Optional[float]]:
        """Extract P2P and length measurements from text."""
        t = (text or "").lower().replace("\u201d", '"').replace("\u2033", '"')

        p2p = None
        length = None

        for raw_line in t.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            for segment in self._split_measurement_segments(line):
                if p2p is None:
                    p2p = self._extract_labeled_value_from_line(segment, self.P2P_LABELS)
                if length is None:
                    length = self._extract_labeled_value_from_line(segment, self.LENGTH_LABELS)

                if p2p is None or length is None:
                    pair = self._extract_pair_from_line(
                        segment,
                        self.RE_PAIR_X,
                        skip_if_contains=("tagged",),
                    )
                    if pair:
                        pair_p2p, pair_length = pair
                        if pair_length < pair_p2p:
                            pair_p2p, pair_length = pair_length, pair_p2p
                        if p2p is None:
                            p2p = pair_p2p
                        if length is None:
                            length = pair_length

                if p2p is not None and length is not None:
                    break

            if p2p is not None and length is not None:
                break

        return p2p, length

    def extract_bottoms(self, text: str) -> dict[str, Optional[float]]:
        """Extract waist, inseam, rise, and leg opening from text."""
        t = (text or "").lower().replace("\u201d", '"').replace("\u2033", '"')

        measurements = {
            "waist": None,
            "inseam": None,
            "rise": None,
            "legOpening": None,
        }
        priorities = {key: -1 for key in measurements}
        label_map = {
            "waist": self.WAIST_LABELS,
            "inseam": self.INSEAM_LABELS,
            "rise": self.RISE_LABELS,
            "legOpening": self.LEG_OPENING_LABELS,
        }

        def assign_value(key: str, value: Optional[float], priority: int) -> None:
            if value is None:
                return
            if priority >= priorities[key]:
                measurements[key] = value
                priorities[key] = priority

        def pair_priority(line: str) -> int:
            lowered = line.lower()
            if any(token in lowered for token in ("measurement", "measured", "waist", "inseam")):
                return 3
            if any(token in lowered for token in ("tagged", "size", "fits")):
                return 1
            return 2

        for line in t.splitlines():
            normalized_line = line.strip()
            if not normalized_line:
                continue

            for segment in self._split_measurement_segments(normalized_line):
                for key, label_pattern in label_map.items():
                    value = self._extract_labeled_value_from_line(segment, label_pattern)
                    assign_value(key, value, 3)

                pair = self._extract_pair_from_line(
                    segment,
                    self.RE_BOTTOMS_PAIR,
                    skip_if_contains=(),
                )
                if pair:
                    waist_value, inseam_value = pair
                    priority = pair_priority(segment)
                    assign_value("waist", waist_value, priority)
                    assign_value("inseam", inseam_value, priority)

                wl_pair = self._extract_pair_from_line(
                    segment,
                    self.RE_BOTTOMS_WL_PAIR,
                    skip_if_contains=(),
                )
                if wl_pair:
                    waist_value, inseam_value = wl_pair
                    priority = pair_priority(segment)
                    assign_value("waist", waist_value, priority)
                    assign_value("inseam", inseam_value, priority)

        waist_value = measurements["waist"]
        waist_value = self._normalize_bottoms_waist(waist_value, t)

        return {
            "waist": waist_value,
            "inseam": measurements["inseam"],
            "rise": measurements["rise"],
            "legOpening": measurements["legOpening"],
        }

    def within(self, val: Optional[float], target: Optional[float], tol: float) -> bool:
        """Check if a value is within tolerance of target."""
        if target is None:
            return True
        if val is None:
            return False
        return abs(val - target) <= tol


# Singleton instance
parser = MeasurementParser()
