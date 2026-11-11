import random
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from presencecam.engine import Engine, EngineConfig, Glitcher, pingpong_index, test_pattern


def frame(value: int) -> np.ndarray:
    return np.full((72, 128, 3), value, np.uint8)


class PingPongTest(unittest.TestCase):
    def test_runs_forward_then_back_without_repeating_the_ends(self):
        self.assertEqual([pingpong_index(step, 4) for step in range(8)], [0, 1, 2, 3, 2, 1, 0, 1])
        self.assertEqual(pingpong_index(5, 1), 0)


class GlitcherTest(unittest.TestCase):
    def test_freeze_holds_the_last_clean_frame(self):
        glitcher = Glitcher()
        glitcher.apply(frame(10), 0.0)
        glitcher.set_mode("freeze")
        self.assertTrue((glitcher.apply(frame(200), 1.0) == 10).all())

    def test_weak_blurs_instead_of_pixelating(self):
        glitcher = Glitcher(random.Random(1))
        glitcher.set_mode("weak")
        checker = np.dstack([np.indices((72, 128)).sum(axis=0) % 2 * 255] * 3).astype(np.uint8)
        sent = [out for out in (glitcher.apply(checker, step / 30) for step in range(90)) if out is not checker]
        self.assertTrue(sent)
        # Fine detail is lost to blur: no hard 0/255 edges survive, as they would with pixel squares.
        for out in sent:
            self.assertLess(np.abs(np.diff(out.astype(int), axis=1)).max(), 128)

    def test_weak_jumps_between_better_and_worse_with_freezes(self):
        glitcher = Glitcher(random.Random(4))
        glitcher.set_mode("weak")
        levels, frames = [], []
        for step in range(30 * 60):  # one minute at 30 fps
            frames.append(id(glitcher.apply(frame(step % 250), step / 30)))
            levels.append(glitcher.level)
        changes = [b - a for a, b in zip(levels, levels[1:]) if a != b]
        self.assertTrue(any(c > 0 for c in changes) and any(c <= -2 for c in changes))  # climbs, then drops hard
        # Freezes: runs of the same picture longer than 0.4 s.
        runs, run = [], 1
        for a, b in zip(frames, frames[1:]):
            run = run + 1 if a == b else (runs.append(run) or 1)
        self.assertGreaterEqual(sum(r > 12 for r in runs), 3)
        self.assertLess(len(set(frames)), 15 * 60)  # never full frame rate

    def test_choppy_stalls_for_seconds_then_recovers(self):
        glitcher = Glitcher(random.Random(2))
        glitcher.set_mode("choppy")
        held = [np.array_equal(glitcher.apply(frame(step % 250), step / 10), frame((step - 1) % 250)) for step in range(400)]
        self.assertTrue(any(held))
        self.assertFalse(all(held))


class EngineTest(unittest.TestCase):
    def test_records_a_clip_and_loops_it(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(EngineConfig(width=128, height=72, clips_dir=Path(directory)))
            with self.assertRaises(ValueError):
                engine.set_source("loop")

            name = engine.start_recording(3)
            start = time.monotonic()
            for step in range(5):
                engine._frame(start, frame(step * 40))
            engine._frame(start + 10, frame(250))  # past the end: the clip is finished

            self.assertTrue((Path(directory) / f"{name}.mp4").exists())
            self.assertEqual(engine.state()["clips"][0]["name"], name)
            engine.set_source("loop")
            time.sleep(0.7)  # let the cross-fade finish
            outputs = [int(engine._frame(time.monotonic(), frame(255)).mean()) for _ in range(8)]
            # The recorded frames forward then back, never the live 255.
            self.assertEqual(outputs, [0, 40, 80, 120, 160, 250, 160, 120])

            engine.set_source("live")
            engine.delete_clip(name)
            self.assertFalse((Path(directory) / f"{name}.mp4").exists())

    def test_reloads_saved_clips_at_start(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Engine(EngineConfig(width=128, height=72, clips_dir=Path(directory)))
            name = first.start_recording(3)
            start = time.monotonic()
            for step in range(4):
                first._frame(start, test_pattern(128, 72, step))
            first._frame(start + 10, test_pattern(128, 72, 9))

            second = Engine(EngineConfig(width=128, height=72, clips_dir=Path(directory)))
            self.assertEqual(second.state()["activeClip"], name)


if __name__ == "__main__":
    unittest.main()
