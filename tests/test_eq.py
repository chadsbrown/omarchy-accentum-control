"""EQ encoder checks. Run: python -m unittest discover -s tests -v

The M4 vectors were produced by the official app's own EQ functions (via
xndbogdan/sennheiser-control, crates/protocol/tests/eq_gen1_vectors.json); they
prove the maths. The ACCENTUM constants differ only in the parameter table.
"""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import eq  # noqa: E402

M4 = dict(
    bass_boost=[5.0, 5.0, 0.0, 0.0, 0.0],
    loudness_weights=[-26.683, -10.813, -4.773, -4.813, -5.993],
    max_headroom=[5.0, 5.0, 4.0, 3.0, 2.0],
    q_factor=[math.sqrt(0.5)] * 4,
    shelf_freq=[120.0, 400.0, 3000.0, 6000.0],
)
VECTORS = [
    ([0, 0, 0, 0, 0], False, 0, 16384,
     [17, 257, 0, 272, 11, 273, 360, 274, 0, 275, 2896, 288, 11, 289, 1200, 290, 0, 291, 2896,
      304, 11, 305, 9000, 306, 0, 307, 2896, 320, 11, 321, 18000, 322, 0, 323, 2896, 0]),
    ([0, 0, 0, 0, 0], True, -7, 20480,
     [17, 257, 258, 272, 11, 273, 360, 274, 0, 275, 2896, 288, 11, 289, 1200, 290, -300, 291, 2896,
      304, 11, 305, 9000, 306, 0, 307, 2896, 320, 11, 321, 18000, 322, 0, 323, 2896, 0]),
    ([0, 2, 2.5, 1.5, -2], False, -13, 16384,
     [17, 257, -78, 272, 11, 273, 360, 274, 120, 275, 2896, 288, 11, 289, 1200, 290, 30, 291, 2896,
      304, 11, 305, 9000, 306, -60, 307, 2896, 320, 11, 321, 18000, 322, -210, 323, 2896, 0]),
    ([0, 2, 2.5, 1.5, -2], True, -21, 20480,
     [17, 257, 174, 272, 11, 273, 360, 274, 120, 275, 2896, 288, 11, 289, 1200, 290, -270, 291, 2896,
      304, 11, 305, 9000, 306, -60, 307, 2896, 320, 11, 321, 18000, 322, -209, 323, 2896, 0]),
    ([0, -2.5, 0, 2.5, 0], True, -12, 20480,
     [17, 257, 228, 272, 11, 273, 360, 274, -150, 275, 2896, 288, 11, 289, 1200, 290, -150, 291, 2896,
      304, 11, 305, 9000, 306, 150, 307, 2896, 320, 11, 321, 18000, 322, -150, 323, 2896, 0]),
]


class EqTest(unittest.TestCase):
    def test_matches_official_app_vectors(self):
        for gains, bass, shift, knob, params in VECTORS:
            with self.subTest(gains=gains, bass=bass):
                self.assertEqual(eq.encode(gains, bass, M4), (params, shift, knob))

    def test_accentum_flat_is_flat(self):
        params, shift, knob = eq.encode([0, 0, 0, 0, 0], False)
        self.assertEqual((params[2], params[8], params[16], params[24], params[32]), (0, 0, 0, 0, 0))
        self.assertEqual((shift, knob), (0, 0x4000))
        self.assertEqual((params[6], params[14], params[22], params[30]), (375, 1200, 4800, 15000))

    def test_accentum_round_trip(self):
        for gains in eq.PRESETS.values():
            for bass in (False, True):
                with self.subTest(gains=gains, bass=bass):
                    params, shift, knob = eq.encode(gains, bass)
                    reply = [5, 257, params[2], 274, params[8], 290, params[16], 306, params[24], 322, params[32]]
                    back = eq.decode(reply, shift, eq.knob_bass_boost(knob))
                    for want, got in zip(gains, back):
                        self.assertAlmostEqual(want, got, delta=0.11)

    def test_accentum_never_boosts_above_full_scale(self):
        params, shift, _ = eq.encode([6, 6, 6, 6, 6], False)
        self.assertLessEqual(shift, -60)

    def test_rejects_bad_input(self):
        for bad in ([0] * 4, [0, 0, 0, 0, 7], [0, 0, 0, 0, float('nan')], 'flat', [True] * 5):
            with self.assertRaises(ValueError):
                eq.encode(bad, False)

    def test_unconfigured_detection(self):
        self.assertFalse(eq.configured([4, 272, 11, 288, 11, 304, 0, 320, 0]))
        self.assertTrue(eq.configured([4, 272, 11, 288, 11, 304, 11, 320, 11]))
        self.assertIsNone(eq.knob_bass_boost(0))
        self.assertIs(eq.knob_bass_boost(0x5000), True)


if __name__ == '__main__':
    unittest.main()
