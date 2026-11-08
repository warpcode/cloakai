import sys
# patch sys.argv before import
sys.argv = ['measure_coldstart.py']

import unittest
import time
from unittest.mock import patch, MagicMock

import scripts.measure_coldstart as mc

class TestMeasureColdstart(unittest.TestCase):
    @patch('scripts.measure_coldstart.report')
    @patch('scripts.measure_coldstart.sh')
    def test_create_only_timing(self, mock_sh, mock_report):
        def mock_sh_impl(cmd, **kw):
            if cmd[1] == "create":
                time.sleep(0.1) # create takes 0.1s
                return 0, "12345"
            elif cmd[1] == "rm":
                time.sleep(0.05) # rm takes 0.05s
                return 0, ""
            return 0, ""

        mock_sh.side_effect = mock_sh_impl

        # mock report to actually return real dictionaries so downstream calculations don't fail
        def mock_report_impl(label, samples, note=""):
            import statistics
            c = [s for s in samples if s is not None]
            if not c:
                return None
            return dict(n=len(c), min=min(c), median=statistics.median(c),
                     mean=statistics.mean(c), max=max(c), stdev=statistics.stdev(c) if len(c) > 1 else 0.0)

        mock_report.side_effect = mock_report_impl

        mc.REPS = 1

        # Suppress prints
        with patch('builtins.print'):
            with patch('pathlib.Path.write_text'):
                mc.main()

        found_create = False
        for call_args in mock_report.call_args_list:
            args, kwargs = call_args
            if args[0] == "docker create":
                found_create = True
                create_only_times = args[1]
                measured_time = create_only_times[0]
                self.assertAlmostEqual(measured_time, 0.1, places=1)
                self.assertLess(measured_time, 0.14)
                break

        self.assertTrue(found_create, "docker create report was not called")

if __name__ == '__main__':
    unittest.main()
