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
        result = server.ppt_list_routes()

        self.assertTrue(result["ok"])
        self.assertEqual(result["next_field"], "route")
        self.assertTrue(result["route_selection"]["user_must_choose_route"])
        self.assertTrue(result["route_selection"]["do_not_choose_for_user"])
        self.assertTrue(result["route_selection"]["do_not_infer_from_pdf"])
        self.assertIn("最适合", result["route_selection"]["bad_agent_reply_example"])
        self.assertIn("请您先选一条", result["route_selection"]["good_agent_reply_example"])
        self.assertEqual(
            result["preferred_tool_sequence"],
            ["ppt_list_routes", "ppt_check_route", "ppt_convert_pdf"],
        )
        self.assertEqual(len(result["workflow_guidance"]["steps"]), 1)
        self.assertEqual(result["workflow_guidance"]["steps"][0]["field"], "route")

        routes = result["routes"]
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
                route_confirmed=True,
                scanned_page_mode="fullpage",
                remove_footer_notebooklm=False,
                ocr_ai_model_decision="explicit",
                ocr_ai_provider="openai",
                ocr_ai_base_url="https://example.com/v1",
                ocr_ai_model="gpt-4.1-mini",
                ocr_ai_prompt_preset="custom_preset",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["display_name"], "本地切块识别")
        self.assertEqual(result["effective_config"]["ocr_ai_model"], "gpt-4.1-mini")
        self.assertEqual(result["effective_config"]["ocr_ai_provider"], "openai")
        self.assertEqual(result["effective_config"]["ocr_ai_model_decision"], "explicit")
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
                route_confirmed=True,
                scanned_page_mode="segmented",
                remove_footer_notebooklm=True,
                ocr_ai_model_decision="route_default",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["effective_config"]["scanned_page_mode"], "segmented")
        self.assertTrue(result["effective_config"]["remove_footer_notebooklm"])
        self.assertEqual(result["effective_config"]["ocr_ai_model_decision"], "route_default")

        create_options = create_job.call_args.kwargs["options"]
        self.assertEqual(create_options["scanned_page_mode"], "segmented")
        self.assertTrue(create_options["remove_footer_notebooklm"])

    def test_convert_pdf_rejects_route_default_with_model_overrides(self) -> None:
        result = server.ppt_convert_pdf(
            pdf_path="/tmp/demo.pdf",
            route="本地切块识别",
            route_confirmed=True,
            scanned_page_mode="fullpage",
            remove_footer_notebooklm=False,
            ocr_ai_model_decision="route_default",
            ocr_ai_model="gpt-4.1-mini",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_model_decision")
        self.assertEqual(
            result["error"]["details"]["override_fields"],
            ["ocr_ai_model"],
        )

    def test_check_route_rejects_ai_overrides_for_non_ai_route(self) -> None:
        result = server.ppt_check_route(
            route="基础本地解析",
            route_confirmed=True,
            ocr_ai_model="gpt-4.1-mini",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_override")

    def test_check_route_requires_explicit_route_confirmation(self) -> None:
        result = server.ppt_check_route(route="本地切块识别")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_required_decision")
        self.assertEqual(
            result["error"]["details"]["missing_fields"],
            ["route_confirmed"],
        )
        self.assertEqual(result["error"]["details"]["next_field"], "route_confirmed")

    def test_check_route_returns_ordered_workflow_guidance(self) -> None:
        result = server.ppt_check_route(route="本地切块识别", route_confirmed=True)

        self.assertTrue(result["ok"])
        self.assertFalse(result["ready_for_submit"])
        self.assertEqual(
            result["missing_fields"],
            ["scanned_page_mode", "remove_footer_notebooklm", "ocr_ai_model_decision"],
        )
        self.assertEqual(result["next_field"], "scanned_page_mode")
        steps = result["workflow_guidance"]["steps"]
        self.assertEqual(steps[0]["field"], "route_confirmed")
        self.assertEqual(steps[1]["field"], "scanned_page_mode")
        self.assertEqual(steps[2]["field"], "remove_footer_notebooklm")
        self.assertEqual(steps[3]["field"], "ocr_ai_model_decision")
        self.assertEqual(steps[4]["field"], "ocr_ai_model")
        self.assertEqual(
            result["ai_model_selection"]["route_default"]["ocr_ai_model"],
            "Qwen/Qwen2.5-VL-72B-Instruct",
        )
        self.assertTrue(result["route_selection"]["route_confirmed_required"])
        self.assertTrue(result["route_selection"]["do_not_claim_best_route"])
        self.assertTrue(
            result["ai_model_selection"]["user_must_choose_or_accept_default_explicitly"]
        )

    def test_check_route_requires_model_decision_after_other_decisions(self) -> None:
        result = server.ppt_check_route(
            route="本地切块识别",
            route_confirmed=True,
            scanned_page_mode="fullpage",
            remove_footer_notebooklm=False,
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["ready_for_submit"])
        self.assertEqual(result["missing_fields"], ["ocr_ai_model_decision"])
        self.assertEqual(result["next_field"], "ocr_ai_model_decision")

    def test_check_route_marks_submit_ready_after_required_decisions(self) -> None:
        result = server.ppt_check_route(
            route="本地切块识别",
            route_confirmed=True,
            scanned_page_mode="fullpage",
            remove_footer_notebooklm=False,
            ocr_ai_model_decision="route_default",
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["ready_for_submit"])
        self.assertEqual(result["missing_fields"], [])

    def test_convert_pdf_rejects_missing_required_decisions(self) -> None:
        result = server.ppt_convert_pdf(
            pdf_path="/tmp/demo.pdf",
            route="本地切块识别",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_required_decision")
        self.assertEqual(
            result["error"]["details"]["missing_fields"],
            ["route_confirmed"],
        )
        self.assertEqual(result["error"]["details"]["next_field"], "route_confirmed")

    def test_convert_pdf_rejects_explicit_model_decision_without_model(self) -> None:
        result = server.ppt_convert_pdf(
            pdf_path="/tmp/demo.pdf",
            route="本地切块识别",
            route_confirmed=True,
            scanned_page_mode="fullpage",
            remove_footer_notebooklm=False,
            ocr_ai_model_decision="explicit",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_required_decision")
        self.assertEqual(result["error"]["details"]["missing_fields"], ["ocr_ai_model"])
        self.assertEqual(result["error"]["details"]["next_field"], "ocr_ai_model")

    def test_list_route_models_uses_route_credentials_and_default_capability(self) -> None:
        with patch.object(
            server.client,
            "list_ai_models",
            return_value={"models": ["deepseek-ai/DeepSeek-OCR", "olmOCR"]},
        ) as list_ai_models:
            result = server.ppt_list_route_models(
                route="模型直出框和文字",
                route_confirmed=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["display_name"], "模型直出框和文字")
        self.assertEqual(result["capability"], "ocr")
        self.assertEqual(result["models"], ["deepseek-ai/DeepSeek-OCR", "olmOCR"])
        self.assertEqual(
            result["route_default"]["ocr_ai_model"],
            "deepseek-ai/DeepSeek-OCR",
        )

        list_ai_models.assert_called_once_with(
            provider="deepseek",
            api_key="test-key",
            base_url="https://api.siliconflow.cn/v1",
            capability="ocr",
        )

    def test_list_route_models_requires_route_confirmation(self) -> None:
        result = server.ppt_list_route_models(route="模型直出框和文字")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_required_decision")
        self.assertEqual(
            result["error"]["details"]["missing_fields"],
            ["route_confirmed"],
        )
        self.assertEqual(
            result["error"]["details"]["workflow_guidance"]["hard_rules"][2],
            "不要说“我来为你选择最适合的路线”。",
        )

    def test_list_route_models_rejects_non_ai_route(self) -> None:
        result = server.ppt_list_route_models(
            route="基础本地解析",
            route_confirmed=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_route_model_listing")

    def test_conversion_intake_warns_against_auto_route_selection(self) -> None:
        prompt = server.ppt_conversion_intake()

        self.assertIn("不要自己替用户选路线", prompt)
        self.assertIn("最适合的路线", prompt)
        self.assertIn("1. 先让用户明确选择要走什么链路", prompt)
        self.assertIn("请您先选一条", prompt)

    def test_create_job_requires_explicit_low_level_override_confirmation(self) -> None:
        result = server.ppt_create_job(pdf_path="/tmp/demo.pdf")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_required_decision")
        self.assertEqual(
            result["error"]["details"]["missing_fields"],
            ["low_level_override_confirmed"],
        )
        self.assertEqual(
            result["error"]["details"]["next_field"],
            "low_level_override_confirmed",
        )
        self.assertTrue(
            result["error"]["details"]["low_level_escape_hatch"][
                "user_must_request_bypass_explicitly"
            ]
        )

    def test_create_job_allows_low_level_override_after_confirmation(self) -> None:
        with patch.object(
            server.client, "create_job", return_value={"job_id": "job-low-level"}
        ) as create_job:
            result = server.ppt_create_job(
                pdf_path="/tmp/demo.pdf",
                options={"parse_provider": "local"},
                low_level_override_confirmed=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["job"]["job_id"], "job-low-level")
        self.assertTrue(result["low_level_escape_hatch"]["escape_hatch"])
        create_job.assert_called_once_with(
            pdf_path="/tmp/demo.pdf",
            options={"parse_provider": "local"},
        )


if __name__ == "__main__":
    unittest.main()
