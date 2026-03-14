from __future__ import annotations

import inspect
import sys
import tempfile
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
                "PPT_LAYOUT_BLOCK_MODEL": "deepseek-ai/DeepSeek-OCR",
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
        self.temp_dir = tempfile.TemporaryDirectory()
        self.demo_pdf = Path(self.temp_dir.name) / "demo.pdf"
        self.demo_pdf.write_bytes(b"%PDF-1.4\n%stub\n")
        server._ROUTE_WORKFLOWS.clear()

    def tearDown(self) -> None:
        server._ROUTE_WORKFLOWS.clear()
        self.temp_dir.cleanup()
        self.env_patcher.stop()

    def _lock_workflow(self, route: str = "本地切块识别") -> str:
        result = server.ppt_check_route(route=route, route_confirmed=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["route_workflow"]["locked"])
        return result["route_workflow_id"]

    def _set_target(
        self,
        workflow_id: str,
        *,
        pdf_path: str | None = None,
        page_range_decision: str = "all_pages",
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> dict[str, object]:
        result = server.ppt_set_conversion_target(
            route_workflow_id=workflow_id,
            pdf_path=pdf_path or str(self.demo_pdf),
            page_range_decision=page_range_decision,
            page_start=page_start,
            page_end=page_end,
        )
        self.assertTrue(result["ok"])
        return result

    def _list_models(
        self,
        workflow_id: str,
        *,
        models: list[str] | None = None,
    ) -> dict[str, object]:
        payload = {"models": models or ["deepseek-ai/DeepSeek-OCR", "olmOCR"]}
        with patch.object(server.client, "list_ai_models", return_value=payload):
            result = server.ppt_list_route_models(route_workflow_id=workflow_id)
        self.assertTrue(result["ok"])
        return result

    def _set_options(
        self,
        workflow_id: str,
        *,
        scanned_page_mode: str = "fullpage",
        remove_footer_notebooklm: bool = False,
        ocr_ai_model_decision: str = "route_default",
        ocr_ai_model_choice_index: int | None = None,
        ocr_ai_model: str | None = None,
    ) -> dict[str, object]:
        result = server.ppt_set_route_options(
            route_workflow_id=workflow_id,
            scanned_page_mode=scanned_page_mode,
            remove_footer_notebooklm=remove_footer_notebooklm,
            ocr_ai_model_decision=ocr_ai_model_decision,
            ocr_ai_model_choice_index=ocr_ai_model_choice_index,
            ocr_ai_model=ocr_ai_model,
        )
        self.assertTrue(result["ok"])
        return result

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
        self.assertEqual(
            result["preferred_tool_sequence"],
            [
                "ppt_list_routes",
                "ppt_check_route",
                "ppt_set_conversion_target",
                "ppt_list_route_models",
                "ppt_set_route_options",
                "ppt_convert_pdf",
            ],
        )
        self.assertNotIn("page_range_decision", result["workflow_guidance"]["defaults"])
        routes = result["routes"]
        layout_block = next(item for item in routes if item["route"] == "layout_block")
        self.assertEqual(layout_block["display_name"], "本地切块识别")
        self.assertEqual(layout_block["recommended_input"], "本地切块识别")

    def test_check_route_requires_explicit_route_confirmation(self) -> None:
        result = server.ppt_check_route(route="本地切块识别")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_required_decision")
        self.assertEqual(result["error"]["details"]["missing_fields"], ["route_confirmed"])
        self.assertEqual(result["error"]["details"]["next_field"], "route_confirmed")
        self.assertEqual(result["error"]["details"]["next_tool"], "ppt_check_route")

    def test_check_route_locks_workflow_and_points_to_target_tool(self) -> None:
        result = server.ppt_check_route(route="本地切块识别", route_confirmed=True)

        self.assertTrue(result["ok"])
        self.assertFalse(result["ready_for_submit"])
        self.assertEqual(
            result["missing_fields"],
            [
                "pdf_path",
                "page_range_decision",
                "scanned_page_mode",
                "remove_footer_notebooklm",
                "ocr_ai_model_decision",
            ],
        )
        self.assertEqual(result["next_field"], "pdf_path")
        self.assertEqual(result["next_tool"], "ppt_set_conversion_target")
        self.assertTrue(result["route_workflow"]["locked"])
        self.assertEqual(result["current_decisions"]["page_range_label"], "pending")
        steps = result["workflow_guidance"]["steps"]
        self.assertEqual(steps[0]["field"], "route_confirmed")
        self.assertEqual(steps[1]["tool"], "ppt_set_conversion_target")
        self.assertEqual(steps[5]["tool"], "ppt_set_route_options")

    def test_set_conversion_target_requires_workflow_id(self) -> None:
        result = server.ppt_set_conversion_target(route_workflow_id="")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_route_workflow")
        self.assertEqual(result["error"]["details"]["next_field"], "route_workflow_id")

    def test_set_conversion_target_rejects_nonexistent_pdf_path(self) -> None:
        workflow_id = self._lock_workflow()

        result = server.ppt_set_conversion_target(
            route_workflow_id=workflow_id,
            pdf_path=str(self.demo_pdf.parent / "missing.pdf"),
            page_range_decision="all_pages",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "pdf_path_not_found")
        self.assertEqual(result["error"]["details"]["next_field"], "pdf_path")
        self.assertEqual(result["error"]["details"]["next_tool"], "ppt_set_conversion_target")

    def test_set_conversion_target_persists_pdf_path_and_all_pages(self) -> None:
        workflow_id = self._lock_workflow()

        result = self._set_target(
            workflow_id,
            pdf_path=str(self.demo_pdf),
            page_range_decision="all_pages",
        )

        self.assertEqual(result["current_decisions"]["pdf_path"], str(self.demo_pdf.resolve()))
        self.assertEqual(result["current_decisions"]["page_range_decision"], "all_pages")
        self.assertEqual(result["current_decisions"]["page_range_label"], "all_pages")
        self.assertEqual(result["next_tool"], "ppt_set_route_options")

    def test_set_conversion_target_persists_page_range(self) -> None:
        workflow_id = self._lock_workflow()

        result = self._set_target(
            workflow_id,
            pdf_path=str(self.demo_pdf),
            page_range_decision="page_range",
            page_start=4,
            page_end=6,
        )

        self.assertEqual(result["current_decisions"]["page_range_decision"], "page_range")
        self.assertEqual(result["current_decisions"]["page_start"], 4)
        self.assertEqual(result["current_decisions"]["page_end"], 6)
        self.assertEqual(result["current_decisions"]["page_range_label"], "4-6")
        self.assertEqual(result["route_workflow"]["conversion_target"]["page_range_label"], "4-6")

    def test_set_conversion_target_requires_page_bounds_for_page_range(self) -> None:
        workflow_id = self._lock_workflow()

        result = server.ppt_set_conversion_target(
            route_workflow_id=workflow_id,
            pdf_path=str(self.demo_pdf),
            page_range_decision="page_range",
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["ready_for_submit"])
        self.assertEqual(result["missing_fields"], ["page_start", "page_end", "scanned_page_mode", "remove_footer_notebooklm", "ocr_ai_model_decision"])
        self.assertEqual(result["next_field"], "page_start")
        self.assertEqual(result["next_tool"], "ppt_set_conversion_target")

    def test_set_conversion_target_rejects_all_pages_with_explicit_bounds(self) -> None:
        workflow_id = self._lock_workflow()

        result = server.ppt_set_conversion_target(
            route_workflow_id=workflow_id,
            pdf_path=str(self.demo_pdf),
            page_range_decision="all_pages",
            page_start=1,
            page_end=2,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_page_range_decision")

    def test_set_route_options_requires_workflow_id(self) -> None:
        result = server.ppt_set_route_options(route_workflow_id="")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_route_workflow")

    def test_set_route_options_requires_conversion_target_first(self) -> None:
        workflow_id = self._lock_workflow()

        result = server.ppt_set_route_options(
            route_workflow_id=workflow_id,
            scanned_page_mode="fullpage",
            remove_footer_notebooklm=False,
            ocr_ai_model_decision="route_default",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "workflow_step_out_of_order")
        self.assertEqual(result["error"]["details"]["next_tool"], "ppt_set_conversion_target")
        self.assertEqual(result["error"]["details"]["blocking_fields"], ["pdf_path", "page_range_decision"])

    def test_set_route_options_rejects_ai_override_for_non_ai_route(self) -> None:
        workflow_id = self._lock_workflow(route="基础本地解析")
        self._set_target(workflow_id)

        result = server.ppt_set_route_options(
            route_workflow_id=workflow_id,
            scanned_page_mode="fullpage",
            remove_footer_notebooklm=False,
            ocr_ai_model="gpt-4.1-mini",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_override")

    def test_set_route_options_requires_model_listing_for_explicit_model(self) -> None:
        workflow_id = self._lock_workflow()
        self._set_target(workflow_id)

        result = server.ppt_set_route_options(
            route_workflow_id=workflow_id,
            scanned_page_mode="fullpage",
            remove_footer_notebooklm=False,
            ocr_ai_model_decision="explicit",
            ocr_ai_model="olmOCR",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_model_listing")

    def test_set_route_options_marks_ready_after_required_decisions(self) -> None:
        workflow_id = self._lock_workflow()
        self._set_target(workflow_id)

        result = self._set_options(workflow_id)

        self.assertTrue(result["ready_for_submit"])
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(result["current_decisions"]["ocr_ai_model_decision"], "route_default")

    def test_list_route_models_uses_route_credentials_and_returns_choice_lines(self) -> None:
        workflow_id = self._lock_workflow()
        self._set_target(workflow_id, page_range_decision="page_range", page_start=4, page_end=6)

        with patch.object(
            server.client,
            "list_ai_models",
            return_value={"models": ["deepseek-ai/DeepSeek-OCR", "olmOCR"]},
        ) as list_ai_models:
            result = server.ppt_list_route_models(route_workflow_id=workflow_id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["display_name"], "本地切块识别")
        self.assertEqual(result["route_workflow_id"], workflow_id)
        self.assertEqual(
            result["route_workflow"]["next_required_step"]["next_tool"],
            "ppt_set_route_options",
        )
        self.assertEqual(result["capability"], "ocr")
        self.assertEqual(result["choice_display_lines"], ["0. deepseek-ai/DeepSeek-OCR [route_default]", "1. olmOCR"])
        self.assertEqual(result["selection_instructions"]["preferred_choice_field"], "ocr_ai_model_choice_index")
        self.assertEqual(result["selection_instructions"]["submit_tool"], "ppt_set_route_options")
        self.assertEqual(result["current_decisions"]["page_range_label"], "4-6")

        list_ai_models.assert_called_once_with(
            provider="siliconflow",
            api_key="test-key",
            base_url="https://api.siliconflow.cn/v1",
            capability="ocr",
        )

    def test_list_route_models_rejects_non_ai_route(self) -> None:
        workflow_id = self._lock_workflow(route="基础本地解析")
        self._set_target(workflow_id)

        result = server.ppt_list_route_models(route_workflow_id=workflow_id)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_route_model_listing")

    def test_list_route_models_requires_conversion_target_first(self) -> None:
        workflow_id = self._lock_workflow()

        result = server.ppt_list_route_models(route_workflow_id=workflow_id)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "workflow_step_out_of_order")
        self.assertEqual(result["error"]["details"]["next_tool"], "ppt_set_conversion_target")
        self.assertEqual(result["error"]["details"]["blocking_fields"], ["pdf_path", "page_range_decision"])

    def test_set_route_options_accepts_model_choice_index_on_same_gateway(self) -> None:
        workflow_id = self._lock_workflow()
        self._set_target(workflow_id, page_range_decision="page_range", page_start=4, page_end=6)
        self._list_models(workflow_id, models=["deepseek-ai/DeepSeek-OCR", "olmOCR"])

        result = self._set_options(
            workflow_id,
            ocr_ai_model_decision="explicit",
            ocr_ai_model_choice_index=1,
        )

        self.assertTrue(result["ready_for_submit"])
        self.assertEqual(result["effective_config"]["ocr_ai_model"], "olmOCR")
        self.assertEqual(result["effective_config"]["page_start"], 4)
        self.assertEqual(result["effective_config"]["page_end"], 6)
        self.assertEqual(result["current_decisions"]["ocr_ai_model_choice_index"], 1)

    def test_convert_pdf_requires_workflow_id(self) -> None:
        result = server.ppt_convert_pdf(route_workflow_id="")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_route_workflow")

    def test_convert_pdf_requires_target_before_submit(self) -> None:
        workflow_id = self._lock_workflow()

        result = server.ppt_convert_pdf(route_workflow_id=workflow_id)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_required_decision")
        self.assertEqual(result["error"]["details"]["missing_fields"], ["pdf_path", "page_range_decision", "scanned_page_mode", "remove_footer_notebooklm", "ocr_ai_model_decision"])
        self.assertEqual(result["error"]["details"]["next_tool"], "ppt_set_conversion_target")

    def test_convert_pdf_requires_options_after_target(self) -> None:
        workflow_id = self._lock_workflow()
        self._set_target(workflow_id)

        result = server.ppt_convert_pdf(route_workflow_id=workflow_id)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_required_decision")
        self.assertEqual(result["error"]["details"]["missing_fields"], ["scanned_page_mode", "remove_footer_notebooklm", "ocr_ai_model_decision"])
        self.assertEqual(result["error"]["details"]["next_tool"], "ppt_set_route_options")

    def test_convert_pdf_submit_only_uses_stored_route_default_state(self) -> None:
        workflow_id = self._lock_workflow()
        self._set_target(workflow_id, page_range_decision="page_range", page_start=4, page_end=6)
        self._set_options(workflow_id)

        with patch.object(
            server.client, "create_job", return_value={"job_id": "job-session"}
        ) as create_job:
            result = server.ppt_convert_pdf(route_workflow_id=workflow_id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["job"]["job_id"], "job-session")
        create_options = create_job.call_args.kwargs["options"]
        self.assertEqual(create_options["page_start"], 4)
        self.assertEqual(create_options["page_end"], 6)
        self.assertEqual(create_options["scanned_page_mode"], "fullpage")
        self.assertFalse(create_options["remove_footer_notebooklm"])
        self.assertEqual(create_options["ocr_ai_model"], "deepseek-ai/DeepSeek-OCR")

    def test_convert_pdf_submit_only_uses_stored_explicit_model_state(self) -> None:
        workflow_id = self._lock_workflow()
        self._set_target(workflow_id)
        self._list_models(workflow_id, models=["deepseek-ai/DeepSeek-OCR", "olmOCR"])
        self._set_options(
            workflow_id,
            ocr_ai_model_decision="explicit",
            ocr_ai_model_choice_index=1,
        )

        with patch.object(
            server.client, "create_job", return_value={"job_id": "job-explicit"}
        ) as create_job:
            result = server.ppt_convert_pdf(
                route_workflow_id=workflow_id,
                retain_process_artifacts=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["effective_config"]["retain_process_artifacts"], True)
        create_options = create_job.call_args.kwargs["options"]
        self.assertEqual(create_options["ocr_ai_model"], "olmOCR")
        self.assertTrue(create_options["retain_process_artifacts"])

    def test_conversion_intake_mentions_new_split_tools(self) -> None:
        prompt = server.ppt_conversion_intake()

        self.assertIn("ppt_set_conversion_target", prompt)
        self.assertIn("ppt_set_route_options", prompt)
        self.assertIn("page_range_decision", prompt)
        self.assertIn("不要静默默认成整份 PDF", prompt)
        self.assertIn("ocr_ai_model_choice_index", prompt)
        self.assertIn("route_workflow_id", prompt)

    def test_high_level_route_tools_expose_split_signatures(self) -> None:
        check_route_params = inspect.signature(server.ppt_check_route).parameters
        set_target_params = inspect.signature(server.ppt_set_conversion_target).parameters
        set_options_params = inspect.signature(server.ppt_set_route_options).parameters
        convert_pdf_params = inspect.signature(server.ppt_convert_pdf).parameters
        list_route_models_params = inspect.signature(server.ppt_list_route_models).parameters

        self.assertEqual(list(check_route_params), ["route", "route_confirmed"])
        self.assertIn("route_workflow_id", set_target_params)
        self.assertIn("pdf_path", set_target_params)
        self.assertIn("page_range_decision", set_target_params)
        self.assertIn("page_start", set_target_params)
        self.assertIn("page_end", set_target_params)
        self.assertIn("route_workflow_id", set_options_params)
        self.assertIn("ocr_ai_model_choice_index", set_options_params)
        self.assertEqual(list(convert_pdf_params), ["route_workflow_id", "retain_process_artifacts"])
        self.assertEqual(list(list_route_models_params), ["route_workflow_id"])
        for params in (
            check_route_params,
            set_target_params,
            set_options_params,
            convert_pdf_params,
            list_route_models_params,
        ):
            self.assertNotIn("ocr_ai_provider", params)
            self.assertNotIn("ocr_ai_base_url", params)

    def test_list_ai_models_returns_model_ids_and_listing_policy(self) -> None:
        with patch.object(
            server.client,
            "list_ai_models",
            return_value={"models": ["deepseek-ai/DeepSeek-OCR", "olmOCR"]},
        ) as list_ai_models:
            result = server.ppt_list_ai_models(
                provider="siliconflow",
                api_key="test-key",
                base_url="https://api.siliconflow.cn/v1",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["capability"], "ocr")
        self.assertEqual(result["model_ids"], ["deepseek-ai/DeepSeek-OCR", "olmOCR"])
        self.assertTrue(result["listing_policy"]["only_repeat_returned_model_ids"])
        list_ai_models.assert_called_once_with(
            provider="siliconflow",
            api_key="test-key",
            base_url="https://api.siliconflow.cn/v1",
            capability="ocr",
        )

    def test_create_job_requires_explicit_low_level_override_confirmation(self) -> None:
        result = server.ppt_create_job(pdf_path=str(self.demo_pdf))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_required_decision")
        self.assertEqual(result["error"]["details"]["missing_fields"], ["low_level_override_confirmed"])
        self.assertEqual(result["error"]["details"]["next_field"], "low_level_override_confirmed")
        self.assertEqual(
            result["error"]["details"]["preferred_tool_sequence"],
            [
                "ppt_list_routes",
                "ppt_check_route",
                "ppt_set_conversion_target",
                "ppt_list_route_models",
                "ppt_set_route_options",
                "ppt_convert_pdf",
            ],
        )

    def test_create_job_rejects_nonexistent_pdf_path_after_override(self) -> None:
        result = server.ppt_create_job(
            pdf_path=str(self.demo_pdf.parent / "missing.pdf"),
            low_level_override_confirmed=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "pdf_path_not_found")
        self.assertEqual(result["error"]["details"]["next_tool"], "ppt_create_job")

    def test_create_job_allows_low_level_override_after_confirmation(self) -> None:
        with patch.object(
            server.client, "create_job", return_value={"job_id": "job-low-level"}
        ) as create_job:
            result = server.ppt_create_job(
                pdf_path=str(self.demo_pdf),
                options={"parse_provider": "local"},
                low_level_override_confirmed=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["job"]["job_id"], "job-low-level")
        create_job.assert_called_once_with(
            pdf_path=str(self.demo_pdf.resolve()),
            options={"parse_provider": "local"},
        )


if __name__ == "__main__":
    unittest.main()
