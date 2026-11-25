import json
from pathlib import Path
import tempfile
import unittest

from scripts.verification.cases import reference_cases, water_cases
from scripts.verification.report import Report, compare


class AccuracyTests(unittest.TestCase):
    def test_limits_are_inclusive_and_zero_requires_zero(self):
        report = Report()
        for ref, actual, limit in [(20, 21, 5), (20, 18, 10), (0, 0, 5)]:
            report.number("q", ref, actual, limit)
        self.assertEqual(report.failed, 0)
        for ref, actual, limit in [(20, 21.0000001, 5), (20, 17.99, 10), (0, 1e-18, 5),
                                   (True, 1, 5), (1, float("nan"), 5), (1, "1", 5)]:
            report.number("q", ref, actual, limit)
        self.assertEqual((report.passed, report.failed), (3, 6))

    def test_interior_errors_on_either_coordinate_are_reported(self):
        for field in ("q_liq", "p_wf"):
            req, ref = water_cases()["vlp"]
            actual = {k: v.copy() for k, v in ref.items()}
            actual[field][2] *= 1.06
            report = Report()
            compare(report, "vlp", req, ref, actual)
            self.assertEqual((report.passed, report.failed), (9, 1))
            self.assertTrue(any(field + "[2]" in line and "FAIL" in line for line in report.lines))

    def test_reject_truncated_data_and_pdf_placeholders(self):
        req, ref = water_cases()["ipr"]
        for values in (ref["q_liq"][:-1], [1, 2, "...", 4, 5, 0]):
            report = Report()
            compare(report, "ipr", req, {**ref, "q_liq": values}, ref)
            self.assertGreater(report.failed, 0)

    def test_read_manifest_with_original_requests_and_one_point_array(self):
        cases = water_cases()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {}
            for stage, (req, ref) in cases.items():
                config[stage] = {"expected": stage + "-ref.json"}
                if stage == "e2e":
                    ref = [ref]
                else:
                    config[stage]["request"] = stage + "-in.json"
                    (root / config[stage]["request"]).write_text(json.dumps(req))
                (root / config[stage]["expected"]).write_text(json.dumps(ref))
            path = root / "manifest.json"
            path.write_text(json.dumps(config))
            self.assertEqual(reference_cases(path), cases)
