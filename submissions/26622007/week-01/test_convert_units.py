"""Offline checks of conversion facts, invalid inputs, and the task answer."""
import unittest

from first_agent import calculator, convert_units


class ConversionTests(unittest.TestCase):
    def test_known_conversions(self):
        cases = [
            (10, "inch", "cm", "25.4 cm"),
            (1, "ft", "inch", "12 inch"),
            (1, "km", "mm", "1000000 mm"),
            (2.54, "cm", "inch", "1 inch"),
            (35, "cm", "cm", "35 cm"),
            (0, "m", "ft", "0 ft"),
            (-100, "cm", "m", "-1 m"),
            (0.1, "inch", "mm", "2.54 mm"),
        ]
        for value, source, target, expected in cases:
            with self.subTest(value=value, source=source, target=target):
                self.assertEqual(convert_units(value, source, target), expected)

    def test_invalid_numbers(self):
        for value in (True, "10", None, float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    convert_units(value, "m", "cm")

    def test_unsupported_units(self):
        for source, target in (("g", "m"), ("m", "kg"), ("feet", "m"),
                               (None, "cm"), ("m", [])):
            with self.subTest(source=source, target=target):
                with self.assertRaises(ValueError):
                    convert_units(1, source, target)

    def test_mixed_unit_task(self):
        converted = [convert_units(2, "m", "cm"),
                     convert_units(35, "cm", "cm"),
                     convert_units(10, "inch", "cm")]
        expression = " + ".join(x.split()[0] for x in converted)
        self.assertEqual(calculator(expression), "260.4")

    def test_mixed_unit_subtraction(self):
        kilometers = convert_units(1, "km", "m").split()[0]
        feet = convert_units(10, "ft", "m").split()[0]
        self.assertEqual(kilometers, "1000")
        self.assertEqual(feet, "3.048")
        self.assertEqual(calculator(f"1 - {kilometers} + {feet}"), "-995.952")


if __name__ == "__main__":
    unittest.main(verbosity=2)
