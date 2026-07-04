"""Tests for the optional HDMI eye GUI controller.

Run from the repository root with:
    python3 -m display.test_eye_controller
"""
import os
import unittest
from unittest import mock


class EyeControllerTests(unittest.TestCase):
    def test_disabled_controller_is_noop(self):
        os.environ["EYE_GUI_ENABLED"] = "0"
        from display.eye_controller import EyeDisplayController

        controller = EyeDisplayController.from_env()

        self.assertFalse(controller.enabled)
        self.assertFalse(controller.started)
        controller.start()
        controller.set_mode("reading")
        controller.blink()
        self.assertFalse(controller.started)

    def test_start_failure_does_not_raise(self):
        os.environ["EYE_GUI_ENABLED"] = "1"
        from display.eye_controller import EyeDisplayController

        with mock.patch("eye_engine.start", side_effect=RuntimeError("no display")):
            controller = EyeDisplayController.from_env()
            controller.start()

        self.assertTrue(controller.enabled)
        self.assertFalse(controller.started)

    def test_mode_mapping_drives_eye_state(self):
        os.environ["EYE_GUI_ENABLED"] = "1"
        from display.eye_controller import EyeDisplayController

        with mock.patch("eye_engine.start", return_value=object()):
            controller = EyeDisplayController.from_env()
            controller.start()
            controller.set_mode("thinking")
            self.assertEqual(controller.expression, "thinking")
            controller.set_mode("speaking")
            self.assertEqual(controller.expression, "happy")
            controller.set_mode("reading")
            self.assertEqual(controller.expression, "reading")
            controller.set_mode("following")
            self.assertEqual(controller.expression, "navigation")
            controller.set_mode("sleep")
            self.assertEqual(controller.expression, "sleepy")


if __name__ == "__main__":
    unittest.main()
