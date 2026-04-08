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
            {
                "name": "tops with number before labels",
                "description": (
                    "single stitch tee\n"
                    "21 pit to pit\n"
                    "28 length\n"
                    "great fade"
                ),
                "expected": (21.0, 28.0),
            },
            {
                "name": "tops with shoulder to hem label",
                "description": (
                    "vintage crewneck\n"
                    "23 chest\n"
                    "27.5 shoulder to hem\n"
                ),
                "expected": (23.0, 27.5),
            },
            {
                "name": "tops with x pair after measurements label",
                "description": (
                    "measurements 22 x 28\n"
                    "fits boxy"
                ),
                "expected": (22.0, 28.0),
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

    def test_bottom_measurements_parse_number_before_label(self):
        description = (
            "y2k light blue bluenotes baggy wide leg jeans\n"
            "tagged 32/32\n"
            "33 waist\n"
            "31 inseam\n"
            "11.5 rise\n"
            "9.5 leg opening\n"
            "rips as shown"
        )

        measurements = parser.extract_bottoms(description)

        self.assertEqual(
            measurements,
            {
                "waist": 33.0,
                "inseam": 31.0,
                "rise": 11.5,
                "legOpening": 9.5,
            },
        )

    def test_bottom_measurements_parse_label_after_value_without_cross_line_bleed(self):
        description = (
            "washed denim\n"
            "34 waist\n"
            "30.5 inseam\n"
            "12 rise\n"
            "10 leg opening\n"
            "tagged 32/32\n"
        )

        measurements = parser.extract_bottoms(description)

        self.assertEqual(
            measurements,
            {
                "waist": 34.0,
                "inseam": 30.5,
                "rise": 12.0,
                "legOpening": 10.0,
            },
        )


if __name__ == "__main__":
    unittest.main()
