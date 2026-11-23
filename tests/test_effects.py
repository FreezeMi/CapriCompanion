import unittest

import numpy as np

from companion import effects

RATE = effects.SAMPLE_RATE


def tone(frequency: float, seconds: float = 1.0) -> np.ndarray:
    time = np.arange(int(RATE * seconds)) / RATE
    return (0.5 * np.sin(2 * np.pi * frequency * time)).astype(np.float32)


def saw(frequency: float, seconds: float = 1.0) -> np.ndarray:
    time = np.arange(int(RATE * seconds)) / RATE
    return (0.5 * (2 * ((time * frequency) % 1.0) - 1)).astype(np.float32)


def energy_near(x: np.ndarray, frequency: float, width: float = 8.0) -> float:
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    bins = np.fft.rfftfreq(len(x), 1 / RATE)
    return float(np.sum(spectrum[np.abs(bins - frequency) <= width] ** 2))


def strongest_frequency(x: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / RATE)[int(np.argmax(spectrum))])


class PresetTest(unittest.TestCase):
    def test_off_changes_nothing(self):
        x = saw(220)
        np.testing.assert_array_equal(effects.apply(x, "off"), x)

    def test_presets_keep_length_and_stay_clean(self):
        x = saw(220, 0.7)
        for effect in ("light", "robotic", "full"):
            with self.subTest(effect=effect):
                y = effects.apply(x, effect)
                self.assertEqual(len(y), len(x))
                self.assertTrue(np.all(np.isfinite(y)))
                self.assertLess(float(np.max(np.abs(y))), 2.0)

    def test_very_short_sound_is_safe(self):
        for effect in effects.EFFECTS:
            self.assertEqual(len(effects.apply(np.zeros(10, dtype=np.float32), effect)), 10)


class EffectTest(unittest.TestCase):
    def test_ring_modulation_adds_tones_beside_the_original(self):
        y = effects.ring(tone(400), frequency=50, amount=1.0)
        self.assertGreater(energy_near(y, 350), 100 * energy_near(tone(400), 350))
        self.assertGreater(energy_near(y, 450), 100 * energy_near(tone(400), 450))

    def test_vocoder_moves_the_sound_onto_its_note(self):
        x = saw(300)
        y = effects.vocoder(x, pitch_hz=190, amount=1.0, noise=0.0)
        self.assertGreater(energy_near(y, 190), energy_near(y, 300))
        self.assertGreater(energy_near(x, 300), energy_near(x, 190))

    def test_pitch_shift_raises_the_note_and_shortens_the_sound(self):
        y = effects.shift_pitch(tone(200), 1.1)
        self.assertEqual(len(y), int(RATE / 1.1))
        self.assertAlmostEqual(strongest_frequency(y), 220, delta=3)

    def test_crush_uses_fewer_steps(self):
        y = effects.crush(np.linspace(-0.5, 0.5, 1000, dtype=np.float32), bits=4)
        self.assertLessEqual(len(np.unique(y)), 9)

    def test_metallic_echo_rings_after_a_click(self):
        click = np.zeros(2400, dtype=np.float32)
        click[0] = 1.0
        y = effects.metallic(click, delay_ms=5.0, feedback=0.6, amount=1.0)
        echo = round(RATE * 0.005)
        self.assertAlmostEqual(float(y[echo]), 0.4 * 0.6, places=4)  # first echo
        self.assertGreater(y[echo], y[2 * echo])  # each echo is quieter
        self.assertGreater(y[2 * echo], 0)


if __name__ == "__main__":
    unittest.main()
