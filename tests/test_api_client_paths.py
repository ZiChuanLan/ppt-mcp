from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ppt_mcp import api_client  # noqa: E402


class ApiClientPathTests(unittest.TestCase):
    def test_normalize_local_pdf_path_keeps_posix_path(self) -> None:
        path = api_client._normalize_local_pdf_path("/home/lan/demo.pdf")

        self.assertEqual(path, Path("/home/lan/demo.pdf"))

    def test_normalize_local_pdf_path_converts_windows_drive_path_for_wsl(self) -> None:
        path = api_client._normalize_local_pdf_path(
            r"C:\Users\27783\Desktop\+AI智能体开发大学生指南.pdf"
        )

        self.assertEqual(
            path,
            Path("/mnt/c/Users/27783/Desktop/+AI智能体开发大学生指南.pdf"),
        )

    def test_normalize_local_pdf_path_converts_wsl_unc_path(self) -> None:
        path = api_client._normalize_local_pdf_path(
            r"\\wsl.localhost\Ubuntu-24.04\home\lan\workspace\demo.pdf"
        )

        self.assertEqual(path, Path("/home/lan/workspace/demo.pdf"))

    def test_normalize_local_pdf_path_keeps_windows_path_on_native_windows(self) -> None:
        with patch.object(api_client.os, "name", "nt"):
            path = api_client._normalize_local_pdf_path(
                r"C:\Users\27783\Desktop\+AI智能体开发大学生指南.pdf"
            )

        self.assertEqual(
            str(path),
            r"C:\Users\27783\Desktop\+AI智能体开发大学生指南.pdf",
        )


if __name__ == "__main__":
    unittest.main()
