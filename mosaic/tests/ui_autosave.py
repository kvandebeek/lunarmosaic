"""Autosave behaviour checks against the shipped ui/save.js (plan phase 7, item 6); requires Node.js, no npm packages."""
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

    def test_failed_save_retains_the_document_and_retries(self):
        self.check('single_failure')

    def test_newer_edit_wins_over_a_failed_save(self):
        self.check('different_fields_failure')

    def test_edit_during_a_successful_save_follows_it(self):
        self.check('different_fields_success')

    def test_newer_same_field_value_wins_including_revert(self):
        self.check('same_field')

    def test_lost_connection_is_retried(self):
        self.check('offline')

    def test_separate_wavelet_layers_survive_in_flight_save(self):
        self.check('wavelets')

    def test_server_document_shown_only_when_nothing_waits(self):
        self.check('later_edit')

    def test_refused_document_waits_for_the_next_edit(self):
        self.check('refused')

    def test_refused_document_with_a_newer_edit_sends_the_newer(self):
        self.check('refused_with_newer_edit')

    def test_default_timers_work_unbound(self):
        self.check('default_timers')


if __name__ == '__main__':
    unittest.main(verbosity=2)
