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
    LEG_OPENING_LABELS = r'(?:leg\s*opening|hem\s*opening|ankle\s*opening|leg\s*hem)'
    
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

    def extract_tops(self, text: str) -> Tuple[Optional[float], Optional[float]]:
        """Extract P2P and length measurements from text."""
        t = (text or "").lower().replace("\u201d", '"').replace("\u2033", '"')

        p2p = None
        length = None

        for raw_line in t.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if p2p is None:
                p2p = self._extract_labeled_value_from_line(line, self.P2P_LABELS)
            if length is None:
                length = self._extract_labeled_value_from_line(line, self.LENGTH_LABELS)

            if p2p is None or length is None:
                pair = self._extract_pair_from_line(
                    line,
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

        return p2p, length

    def extract_bottoms(self, text: str) -> dict[str, Optional[float]]:
        """Extract waist, inseam, rise, and leg opening from text."""
        t = (text or "").lower().replace("\u201d", '"').replace("\u2033", '"')

        measurements = {
            "waist": [],
            "inseam": [],
            "rise": [],
            "legOpening": [],
        }
        label_map = {
            "waist": self.WAIST_LABELS,
            "inseam": self.INSEAM_LABELS,
            "rise": self.RISE_LABELS,
            "legOpening": self.LEG_OPENING_LABELS,
        }

        for line in t.splitlines():
            normalized_line = line.strip()
            if not normalized_line:
                continue

            for key, label_pattern in label_map.items():
                if measurements[key]:
                    continue
                value = self._extract_labeled_value_from_line(normalized_line, label_pattern)
                if value is not None:
                    measurements[key].append(value)

            pair = self._extract_pair_from_line(
                normalized_line,
                self.RE_BOTTOMS_PAIR,
                skip_if_contains=("tagged",),
            )
            if pair:
                waist_value, inseam_value = pair
                if not measurements["waist"]:
                    measurements["waist"].append(waist_value)
                if not measurements["inseam"]:
                    measurements["inseam"].append(inseam_value)

        return {
            "waist": measurements["waist"][0] if measurements["waist"] else None,
            "inseam": measurements["inseam"][0] if measurements["inseam"] else None,
            "rise": measurements["rise"][0] if measurements["rise"] else None,
            "legOpening": measurements["legOpening"][0] if measurements["legOpening"] else None,
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
