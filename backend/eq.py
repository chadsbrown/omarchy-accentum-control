# SPDX-License-Identifier: MIT
# Gen1 EQ maths ported from xndbogdan/sennheiser-control (crates/protocol/src/eq.rs);
# see THIRD_PARTY.md.
"""Five-band "Gen1" user EQ used by ACCENTUM firmware below 3.16.4.

The headphones do not store five slider gains. They store a master gain plus four
cascaded high-shelf filters, a level shift and a flags word ("knob Y"). The five
gains shown to the user are converted to that form here, the same way the official
app does it, and converted back when reading.
"""
import math

BANDS = 5
LABELS = ['50 Hz', '250 Hz', '800 Hz', '3 kHz', '8 kHz']
MIN_GAIN, MAX_GAIN = -6.0, 6.0
PRESETS = {
    'Neutral': [0, 0, 0, 0, 0],
    'Rock': [0, 0, 3, 3, -1],
    'Pop': [0, -5, 0, 5, 0],
    'Dance': [2, -3, -3, 1, 1],
    'Hip-Hop': [3, -1, -4, 2, 0],
    'Classical': [0, 0, 0, 1, 2],
    'Movie': [0, 0, 2, 2, -1],
    'Jazz': [-3, 0, 2, 2, 0],
}

SQRT1_2 = math.sqrt(0.5)
ACCENTUM = dict(
    bass_boost=[5.0, -2.0, 0.0, 0.0, 0.0],
    loudness_weights=[-26.683, -10.813, -4.773, -4.813, -5.993],
    max_headroom=[0.0, 0.0, 0.0, 0.0, 0.0],
    q_factor=[SQRT1_2] * 4,
    shelf_freq=[125.0, 400.0, 1600.0, 5000.0],
)

KNOB_Y_INDEX = 1
KNOB_NEW_STORAGE = 0x4000
KNOB_OLD_CLASSIC = 0xFF00
KNOB_BASS_BOOST = 0x1000
FILTER_HIGH_SHELF = 11
GAIN_QUERY = [5, 0x0101, 0, 0x0112, 0, 0x0122, 0, 0x0132, 0, 0x0142, 0]
TYPE_QUERY = [4, 0x0110, 0, 0x0120, 0, 0x0130, 0, 0x0140, 0]


def _js_round(x):
    """JavaScript Math.round: nearest, ties toward +infinity."""
    floor = math.floor(x)
    return floor + 1.0 if x - floor >= 0.5 else float(floor)


def _wrap(x, bits):
    value = int(math.trunc(x)) & ((1 << bits) - 1)
    return value - (1 << bits) if value >= 1 << (bits - 1) else value


def validate(gains):
    if (not isinstance(gains, list) or len(gains) != BANDS or
            not all(isinstance(g, (int, float)) and not isinstance(g, bool) and math.isfinite(g) and
                    MIN_GAIN <= g <= MAX_GAIN for g in gains)):
        raise ValueError('EQ needs five gains from -6 to +6 dB')
    return [float(g) for g in gains]


def encode(gains, bass_boost, params=ACCENTUM):
    """Return (int16 parameter list, level shift in dB x 10, knob Y word)."""
    gains = validate(gains)
    shaped = [g + b for g, b in zip(gains, params['bass_boost'])] if bass_boost else gains
    loudness = -10.0 * math.log10(sum(10.0 ** ((g + w) / 10.0)
                                      for g, w in zip(shaped, params['loudness_weights'])))
    excess = max(0.0, max(g + loudness - h for g, h in zip(shaped, params['max_headroom'])))
    shift = _js_round(10.0 * (loudness - excess if excess > 0 else loudness)) / 10.0
    shifted = [g + shift for g in shaped]
    steps = [shifted[0]] + [shifted[k] - shifted[k - 1] for k in range(1, BANDS)]
    if math.trunc(100.0 * steps[0]) == 425:
        steps[0] = 4.24
    values = [17.0, 0x0101, 60.0 * steps[0]]
    for band in range(4):
        base = 0x0110 + 0x10 * band
        values += [base, FILTER_HIGH_SHELF, base + 1, 3.0 * params['shelf_freq'][band],
                   base + 2, 60.0 * steps[band + 1], base + 3, 4096.0 * params['q_factor'][band]]
    values.append(0.0)
    knob = KNOB_NEW_STORAGE | (KNOB_BASS_BOOST if bass_boost else 0)
    return [_wrap(v, 16) for v in values], _wrap(10.0 * shift, 8), knob


def knob_bass_boost(knob_y):
    """Bass-boost flag, or None when the EQ has never been stored in this format."""
    if knob_y & KNOB_OLD_CLASSIC == KNOB_OLD_CLASSIC or not knob_y & KNOB_NEW_STORAGE:
        return None
    return bool(knob_y & KNOB_BASS_BOOST)


def configured(type_reply):
    """False when the last two shelves are bypassed: the EQ was never written, or
    another sound mode overwrote it."""
    return len(type_reply) == 9 and not (type_reply[6] == 0 and type_reply[8] == 0)


def decode(gain_reply, level_shift_x10, bass_boost, params=ACCENTUM):
    """Five user gains (dB, 0.1 steps) from the stored master and shelf gains."""
    if len(gain_reply) < 11:
        raise ValueError('Short EQ reply')
    steps = [gain_reply[2 + 2 * k] / 60.0 for k in range(BANDS)]
    gains, total = [], 0.0
    for step in steps:
        total += step
        gains.append(total)
    if bass_boost:
        gains = [g - b for g, b in zip(gains, params['bass_boost'])]
    shift = level_shift_x10 / 10.0
    return [max(MIN_GAIN, min(MAX_GAIN, _js_round(10.0 * (g - shift)) / 10.0)) for g in gains]
