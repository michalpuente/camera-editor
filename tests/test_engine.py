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

    def test_weak_drops_to_about_eight_fps_and_degrades_the_picture(self):
        glitcher = Glitcher(random.Random(1))
        glitcher.set_mode("weak")
        gradient = np.dstack([np.tile(np.arange(128, dtype=np.uint8), (72, 1))] * 3)
        out = [glitcher.apply(gradient, step / 30) for step in range(30)]  # first second, before any stall
        distinct = {id(item) for item in out}
        self.assertLessEqual(len(distinct), 9)
        self.assertFalse(np.array_equal(out[0], gradient))

    def test_choppy_stalls_for_seconds_then_recovers(self):
        glitcher = Glitcher(random.Random(2))
        glitcher.set_mode("choppy")
        held = [np.array_equal(glitcher.apply(frame(step % 250), step / 10), frame((step - 1) % 250)) for step in range(400)]
        self.assertTrue(any(held))
        self.assertFalse(all(held))


class EngineTest(unittest.TestCase):
    def test_records_a_clip_and_loops_it(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(EngineConfig(width=128, height=72, clips_dir=Path(directory), virtual_camera=False))
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
            first = Engine(EngineConfig(width=128, height=72, clips_dir=Path(directory), virtual_camera=False))
            name = first.start_recording(3)
            start = time.monotonic()
            for step in range(4):
                first._frame(start, test_pattern(128, 72, step))
            first._frame(start + 10, test_pattern(128, 72, 9))

            second = Engine(EngineConfig(width=128, height=72, clips_dir=Path(directory), virtual_camera=False))
            self.assertEqual(second.state()["activeClip"], name)


if __name__ == "__main__":
    unittest.main()
