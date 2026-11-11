import sys
import tempfile
import unittest
from pathlib import Path

from presencecam import config, driver


class ConfigTest(unittest.TestCase):
    def test_round_trips_and_falls_back_on_a_broken_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config.save(config.Config(camera_name="Kamera Michała", input_device="HD Pro Webcam C920"), path)
            loaded = config.load(path)
            self.assertEqual((loaded.camera_name, loaded.input_device), ("Kamera Michała", "HD Pro Webcam C920"))

            path.write_text('{"camera_name": "x\\" & del *", "width": 1280}', encoding="utf-8")
            self.assertEqual(config.load(path).camera_name, "PresenceCam")
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(config.load(path), config.Config())

    def test_locks_the_real_camera_by_default_and_rejects_non_bool(self):
        self.assertTrue(config.Config().lock_input)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config.save(config.Config(lock_input=False), path)
            self.assertFalse(config.load(path).lock_input)
            path.write_text('{"lock_input": "no"}', encoding="utf-8")
            self.assertTrue(config.load(path).lock_input)

    def test_rejects_names_that_would_break_the_driver_command(self):
        for bad in ("", "a" * 49, 'Cam"', "Cam & calc", "Cam%PATH%", "line\nbreak"):
            with self.assertRaises(ValueError, msg=bad):
                config.Config(camera_name=bad).validate()


class DriverTest(unittest.TestCase):
    def test_registers_both_dlls_under_the_chosen_name(self):
        commands = driver.regsvr32_commands(Path("C:/PresenceCam/drivers/windows"), "Kamera Michała")
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[1][-1], "/i:UnityCaptureName=Kamera Michała")
        self.assertTrue(commands[0][2].endswith("UnityCaptureFilter32.dll"))
        uninstall = driver.regsvr32_commands(Path("C:/x"), "", uninstall=True)
        self.assertTrue(all("/u" in command for command in uninstall))


@unittest.skipUnless(sys.platform == "win32", "DirectShow is Windows only")
class DirectShowTest(unittest.TestCase):
    def test_lists_video_inputs_without_generated_com_wrappers(self):
        from presencecam.dshow import video_inputs

        names = video_inputs()  # a CI machine has no camera: an empty list, not an error
        self.assertIsInstance(names, list)
        self.assertTrue(all(isinstance(name, str) and name for name in names))


if __name__ == "__main__":
    unittest.main()
