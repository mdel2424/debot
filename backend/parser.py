"""Measurement parsing utilities for extracting P2P and length from listing descriptions."""

import re
from fractions import Fraction
from typing import Optional, Tuple


class MeasurementParser:
    """Parses clothing measurements from text descriptions."""
    
    NUM = r'(?P<val>\d+(?:\.\d+)?(?:\s+\d\/\d)?)'
    UNIT = r'(?P<unit>\s*(?:cm|mm|in|inch|inches|["″"]))?'
    P2P_LABELS = r'(?:p2p|pit\s*[- ]?to\s*[- ]?pit|pit[- ]?to[- ]?pit|pit\s*to\s*pit|chest|width|across\s*chest)'
    LENGTH_LABELS = r'(?:length|top\s*to\s*bottom|back\s*length|hps\s*to\s*hem)'
    
    RE_P2P = re.compile(rf'\b{P2P_LABELS}\b[^0-9]{{0,10}}{NUM}{UNIT}', re.I)
    RE_LENGTH = re.compile(rf'\b{LENGTH_LABELS}\b[^0-9]{{0,10}}{NUM}{UNIT}', re.I)
    RE_PAIR_X = re.compile(
        r'\b'
        r'(?P<w>\d+(?:\.\d+)?)(?P<u1>\s*(?:cm|mm|in|inch|inches|["″"]))?'
        r'\s*[x×]\s*'
        r'(?P<l>\d+(?:\.\d+)?)(?P<u2>\s*(?:cm|mm|in|inch|inches|["″"]))?'
        r'\b', re.I
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

    def extract_tops(self, text: str) -> Tuple[Optional[float], Optional[float]]:
        """Extract P2P and length measurements from text."""
        t = (text or "").lower().replace("\u201d", '"').replace("\u2033", '"')
        
        p2p_vals = [self.to_inches(m.group("val"), m.group("unit") or "") for m in self.RE_P2P.finditer(t)]
        len_vals = [self.to_inches(m.group("val"), m.group("unit") or "") for m in self.RE_LENGTH.finditer(t)]
        
        # Check for WxL patterns
        for m in self.RE_PAIR_X.finditer(t):
            w = self.to_inches(m.group("w"), m.group("u1") or "")
            l = self.to_inches(m.group("l"), m.group("u2") or "")
            if l < w:
                w, l = l, w
            p2p_vals.append(w)
            len_vals.append(l)
        
        p2p = p2p_vals[0] if p2p_vals else None
        length = len_vals[0] if len_vals else None
        
        # Fallback: line-by-line search
        if p2p is None or length is None:
            sp = re.compile(rf'\b{self.P2P_LABELS}\b.*?{self.NUM}{self.UNIT}', re.I)
            sl = re.compile(rf'\b{self.LENGTH_LABELS}\b.*?{self.NUM}{self.UNIT}', re.I)
            for line in t.splitlines():
                if p2p is None:
                    m = sp.search(line)
                    if m:
                        try:
                            p2p = self.to_inches(m.group("val"), m.group("unit") or "")
                        except Exception:
                            pass
                if length is None:
                    m = sl.search(line)
                    if m:
                        try:
                            length = self.to_inches(m.group("val"), m.group("unit") or "")
                        except Exception:
                            pass
                if p2p is not None and length is not None:
                    break
        
        return p2p, length

    def within(self, val: Optional[float], target: Optional[float], tol: float) -> bool:
        """Check if a value is within tolerance of target."""
        if target is None:
            return True
        if val is None:
            return False
        return abs(val - target) <= tol


# Singleton instance
parser = MeasurementParser()
