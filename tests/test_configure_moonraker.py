from __future__ import annotations

import pathlib
import tempfile
import unittest

import sys

sys.path.insert(0, str(pathlib.Path(__file__).parents[1].joinpath("scripts")))

from configure_moonraker import ensure_section, restore_section  # noqa: E402


class ConfigureMoonrakerTest(unittest.TestCase):
    def test_ensure_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = pathlib.Path(directory, "moonraker.conf")
            config.write_text("[server]\nhost: 0.0.0.0\n", encoding="utf-8")
            self.assertTrue(ensure_section(config))
            self.assertFalse(ensure_section(config))
            self.assertEqual(
                config.read_text(encoding="utf-8").count("[cloud_backup]"), 1
            )

    def test_restore_removes_only_added_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            baseline = root.joinpath("baseline.conf")
            current = root.joinpath("moonraker.conf")
            baseline.write_text("[server]\nhost: 0.0.0.0\n", encoding="utf-8")
            current.write_text(
                "[server]\nhost: 127.0.0.1\n\n"
                "[cloud_backup]\nprovider: baidu\n\n"
                "[update_manager]\nenable_auto_refresh: true\n",
                encoding="utf-8",
            )
            self.assertTrue(restore_section(current, baseline))
            result = current.read_text(encoding="utf-8")
            self.assertNotIn("[cloud_backup]", result)
            self.assertIn("host: 127.0.0.1", result)
            self.assertIn("[update_manager]", result)

    def test_restore_original_section_without_reverting_other_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            baseline = root.joinpath("baseline.conf")
            current = root.joinpath("moonraker.conf")
            baseline.write_text(
                "[server]\nhost: old\n\n"
                "[cloud_backup]\nprovider: github\nretain_local: 2\n",
                encoding="utf-8",
            )
            current.write_text(
                "[server]\nhost: new\n\n"
                "[cloud_backup]\nprovider: baidu\nretain_local: 5\n",
                encoding="utf-8",
            )
            self.assertTrue(restore_section(current, baseline))
            result = current.read_text(encoding="utf-8")
            self.assertIn("host: new", result)
            self.assertIn("provider: github", result)
            self.assertNotIn("provider: baidu", result)


if __name__ == "__main__":
    unittest.main()
