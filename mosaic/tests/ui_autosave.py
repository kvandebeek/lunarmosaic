"""Autosave behavior checks against the shipped JavaScript; requires Node.js, no npm packages."""
from pathlib import Path
import shutil
import subprocess
import unittest


class Autosave(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which('node')
        if cls.node is None:
            raise RuntimeError('Node.js is required to run the UI autosave checks.')

    def check(self, scenario):
        result = subprocess.run([self.node, str(Path(__file__).with_suffix('.js')), scenario],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'PASS {scenario}', result.stdout, 'The JavaScript scenario did not finish.')

    def test_failed_save_retains_values_for_retry(self):
        self.check('single_failure')

    def test_different_fields_survive_failed_save(self):
        self.check('different_fields_failure')

    def test_different_fields_survive_successful_save(self):
        self.check('different_fields_success')

    def test_newer_same_field_value_wins_including_revert(self):
        self.check('same_field')

    def test_disconnected_state_refresh_retains_both_edits(self):
        self.check('offline')

    def test_separate_wavelet_layers_survive_in_flight_save(self):
        self.check('wavelets')

    def test_later_edit_uses_refreshed_server_state(self):
        self.check('later_edit')


if __name__ == '__main__':
    unittest.main(verbosity=2)
