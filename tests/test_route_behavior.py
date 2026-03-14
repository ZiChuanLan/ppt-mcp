from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ppt_mcp import route_config, server  # noqa: E402


class RouteBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patcher = patch.dict(
            route_config.os.environ,
            {
                "SILICONFLOW_API_KEY": "test-key",
                "PPT_LAYOUT_BLOCK_PROVIDER": "siliconflow",
                "PPT_LAYOUT_BLOCK_BASE_URL": "https://api.siliconflow.cn/v1",
                "PPT_LAYOUT_BLOCK_MODEL": "Qwen/Qwen2.5-VL-72B-Instruct",
                "PPT_DIRECT_PROVIDER": "deepseek",
                "PPT_DIRECT_BASE_URL": "https://api.siliconflow.cn/v1",
                "PPT_DIRECT_MODEL": "deepseek-ai/DeepSeek-OCR",
                "PPT_DOC_PARSER_PROVIDER": "openai",
                "PPT_DOC_PARSER_BASE_URL": "https://api.siliconflow.cn/v1",
                "PPT_DOC_PARSER_MODEL": "PaddlePaddle/PaddleOCR-VL-1.5",
            },
            clear=False,
        )
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def test_ai_ocr_routes_enable_ocr_and_accept_chinese_aliases(self) -> None:
        expected = {
            "本地切块识别": "layout_block",
            "模型直出框和文字": "direct",
            "内置文档解析": "doc_parser",
        }

        for alias, route_id in expected.items():
            with self.subTest(alias=alias):
                resolved = route_config.resolve_route(alias)
                self.assertEqual(resolved.route, route_id)
                self.assertTrue(resolved.options["enable_ocr"])
                self.assertTrue(resolved.effective_config["enable_ocr"])

    def test_list_routes_exposes_human_friendly_names(self) -> None:
        routes = route_config.list_routes()
        layout_block = next(item for item in routes if item["route"] == "layout_block")
        self.assertEqual(layout_block["display_name"], "本地切块识别")
        self.assertEqual(layout_block["recommended_input"], "本地切块识别")
        self.assertIn("本地切块识别", layout_block["aliases"])

    def test_convert_pdf_allows_explicit_model_overrides(self) -> None:
        with patch.object(
            server.client, "create_job", return_value={"job_id": "job-123"}
        ) as create_job:
            result = server.ppt_convert_pdf(
                pdf_path="/tmp/demo.pdf",
                route="本地切块识别",
                ocr_ai_provider="openai",
                ocr_ai_base_url="https://example.com/v1",
                ocr_ai_model="gpt-4.1-mini",
                ocr_ai_prompt_preset="custom_preset",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["display_name"], "本地切块识别")
        self.assertEqual(result["effective_config"]["ocr_ai_model"], "gpt-4.1-mini")
        self.assertEqual(result["effective_config"]["ocr_ai_provider"], "openai")
        self.assertTrue(result["effective_config"]["enable_ocr"])
        self.assertEqual(result["effective_config"]["scanned_page_mode"], "fullpage")
        self.assertFalse(result["effective_config"]["remove_footer_notebooklm"])

        create_job.assert_called_once()
        create_options = create_job.call_args.kwargs["options"]
        self.assertEqual(create_options["ocr_ai_model"], "gpt-4.1-mini")
        self.assertEqual(create_options["ocr_ai_provider"], "openai")
        self.assertEqual(create_options["ocr_ai_base_url"], "https://example.com/v1")
        self.assertEqual(create_options["ocr_ai_prompt_preset"], "custom_preset")
        self.assertTrue(create_options["enable_ocr"])
        self.assertEqual(create_options["scanned_page_mode"], "fullpage")
        self.assertFalse(create_options["remove_footer_notebooklm"])

    def test_convert_pdf_applies_explicit_scanned_page_and_footer_overrides(self) -> None:
        with patch.object(
            server.client, "create_job", return_value={"job_id": "job-456"}
        ) as create_job:
            result = server.ppt_convert_pdf(
                pdf_path="/tmp/demo.pdf",
                route="本地切块识别",
                scanned_page_mode="segmented",
                remove_footer_notebooklm=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["effective_config"]["scanned_page_mode"], "segmented")
        self.assertTrue(result["effective_config"]["remove_footer_notebooklm"])

        create_options = create_job.call_args.kwargs["options"]
        self.assertEqual(create_options["scanned_page_mode"], "segmented")
        self.assertTrue(create_options["remove_footer_notebooklm"])

    def test_check_route_rejects_ai_overrides_for_non_ai_route(self) -> None:
        result = server.ppt_check_route(
            route="基础本地解析", ocr_ai_model="gpt-4.1-mini"
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_override")

    def test_check_route_returns_ordered_workflow_guidance(self) -> None:
        result = server.ppt_check_route(route="本地切块识别")

        self.assertTrue(result["ok"])
        steps = result["workflow_guidance"]["steps"]
        self.assertEqual(steps[0]["field"], "scanned_page_mode")
        self.assertEqual(steps[1]["field"], "remove_footer_notebooklm")
        self.assertEqual(steps[2]["field"], "ocr_ai_model")


if __name__ == "__main__":
    unittest.main()
