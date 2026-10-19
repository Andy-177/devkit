"""devkit.errors 异常体系测试。"""

from __future__ import annotations

import _common  # noqa: F401
import unittest

from devkit.errors import (
    CloneError,
    DevkitError,
    DownloadError,
    InteractiveInputError,
    JobStop,
    RecipeError,
    StepError,
    UnzipError,
)


class TestErrors(unittest.TestCase):
    def test_hierarchy(self):
        for cls in (RecipeError, CloneError, DownloadError, UnzipError,
                    InteractiveInputError, StepError, JobStop):
            self.assertTrue(issubclass(cls, DevkitError), cls)
            self.assertTrue(issubclass(cls, Exception), cls)

    def test_step_error_returncode(self):
        exc = StepError("boom", returncode=7)
        self.assertEqual(exc.returncode, 7)
        self.assertIn("boom", str(exc))
        self.assertEqual(StepError("x").returncode, None)

    def test_job_stop_fields(self):
        cause = StepError("inner", returncode=3)
        stop = JobStop("job-a", "step-b", cause)
        self.assertEqual(stop.job_name, "job-a")
        self.assertEqual(stop.step_name, "step-b")
        self.assertIs(stop.cause, cause)
        self.assertIn("inner", str(stop))


if __name__ == "__main__":
    unittest.main()
