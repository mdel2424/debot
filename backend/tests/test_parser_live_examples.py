import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from parser import parser  # noqa: E402


class ParserLiveExamplesTest(unittest.TestCase):
    def test_live_examples_parse_expected_measurements(self):
        cases = [
            {
                "name": "warner bros acme tee",
                "description": (
                    "Warner Bros x ACME Clothing 1995 Baseball Embroidered Looney Tunes Pocket Tee\n\n"
                    "Length 26.5\"\n"
                    "Pit-to-pit 20.5\"\n"
                    "Tagged Small\n\n"
                    "Made in Sri Lanka\n"
                    "#bugsBunny #taz"
                ),
                "expected": (20.5, 26.5),
            },
            {
                "name": "hard rock cafe tee",
                "description": (
                    "Vintage Tortola B.V.I. Hard Rock Cafe Promo Single Stitched T-shirt - XL - 90s\n\n"
                    "Great condition, no major wear or flaws.\n\n"
                    "Fabric made in USA. Assembled in Jamaica. 100% cotton. Single stitched sleeves.\n"
                    "__________________________________________________\n"
                    "Measurements\n"
                    "Length(shoulder-hem): 27\n"
                    "Chest(armpit-armpit): 23\n"
                    "Hem: 25\n"
                    "Neck: 7\n"
                    "Sleeves: 8\n"
                    "Shoulders: 23\n\n"
                    "All measurements taken laid flat.\n"
                    "__________________________________________________"
                ),
                "expected": (23.0, 27.0),
            },
            {
                "name": "radically canadian cfl tee",
                "description": (
                    "2000 Radically Canadian CFL Tee\n\n"
                    "- single stitch\n"
                    "- made in Canada\n\n"
                    "Size: XL\n"
                    "Fits like: XL\n"
                    "Measurements: 24x32”\n\n"
                    "All sales final"
                ),
                "expected": (24.0, 32.0),
            },
            {
                "name": "reo speedwagon tee",
                "description": (
                    "REO Speedwagon 1982 Good Trouble Tour T Shirt\n\n"
                    "Single stitch\n"
                    "Super bright graphics\n\n"
                    "Tagged large, fits true\n"
                    "Pit-to-Pit: 21.5\"\n"
                    "Length: 26\"\n\n"
                    "Excellent condition for age\n"
                    "Seems unworn"
                ),
                "expected": (21.5, 26.0),
            },
            {
                "name": "dime crewneck with detailed measurements",
                "description": (
                    "Dime MTL Sun-Faded Teal Crewneck Sweatshirt Size Medium Embroidered Logo\n"
                    "no.19\n\n"
                    "\u200bBrand: Dime\n"
                    "\u200bItem: Crewneck Sweatshirt\n"
                    "\u200bDetailed Measurements:\n\n"
                    "\u200bPit to Pit (Chest Width): 23 inches\n"
                    "\u200bLength (Neck to Hem): 27.5 inches\n"
                    "\u200bSleeve Length (Shoulder to Cuff): 25.5 inches\n"
                ),
                "expected": (23.0, 27.5),
            },
            {
                "name": "genius tee with collar down measurement",
                "description": (
                    "2000’s are you a genius tee\n"
                    "Cool graphic great condition\n"
                    "Measurements\n"
                    "Pit to pit 21”\n"
                    "Collar down 28”\n"
                    "#y2k #2000s #gr"
                ),
                "expected": (21.0, 28.0),
            },
        ]

        for case in cases:
            with self.subTest(case=case["name"]):
                p2p, length = parser.extract_tops(case["description"])
                expected_p2p, expected_length = case["expected"]
                self.assertIsNotNone(p2p)
                self.assertIsNotNone(length)
                self.assertAlmostEqual(p2p, expected_p2p)
                self.assertAlmostEqual(length, expected_length)

    def test_bottom_measurements_parse_expected_measurements(self):
        description = (
            "Vintage carpenter pants\n"
            "Great fade and fit\n"
            "Waist 34\"\n"
            "Inseam 31\"\n"
            "Rise 12\"\n"
            "Leg opening 9.5\"\n"
            "#workwear"
        )

        measurements = parser.extract_bottoms(description)

        self.assertEqual(
            measurements,
            {
                "waist": 34.0,
                "inseam": 31.0,
                "rise": 12.0,
                "legOpening": 9.5,
            },
        )


if __name__ == "__main__":
    unittest.main()
