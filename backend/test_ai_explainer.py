"""Tests for the optional Gemini explainer using mocked SDK calls."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import ai_explainer


class GeminiExplainerTests(unittest.TestCase):
    EVIDENCE = [
        "src/payment.py:12: [match: payment, timeout; nearby terms: payment, timeout] "
        "retry_payment(timeout)",
    ]

    def make_mock_client(self, response=None, side_effect=None):
        client = Mock()
        client.models.generate_content.return_value = response
        if side_effect is not None:
            client.models.generate_content.side_effect = side_effect
        fake_types = SimpleNamespace(
            HttpOptions=Mock(),
            HttpRetryOptions=Mock(),
            ThinkingConfig=Mock(side_effect=lambda **kwargs: kwargs),
            ThinkingLevel=SimpleNamespace(MINIMAL="MINIMAL"),
            GenerateContentConfig=Mock(side_effect=lambda **kwargs: kwargs),
        )
        fake_genai = SimpleNamespace(Client=Mock(return_value=client))
        return client, fake_genai, fake_types

    def run_explainer(self, response, description="Payment requests time out."):
        client, fake_genai, fake_types = self.make_mock_client(response)
        with (
            patch.object(ai_explainer, "_read_api_key", return_value="test-key"),
            patch.object(ai_explainer, "genai", fake_genai),
            patch.object(ai_explainer, "types", fake_types),
        ):
            result = ai_explainer.explain_bug(description, self.EVIDENCE)
        return result, client

    def test_accepted_response_uses_mocked_gemini_client(self):
        explanation = (
            "The reported issue is that payment requests time out. The retry path "
            "shown in the matching source may be related."
        )
        response = SimpleNamespace(
            text=explanation,
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

        with self.assertLogs(ai_explainer._LOGGER, level="WARNING") as captured:
            result, client = self.run_explainer(response)

        self.assertTrue(result.available)
        self.assertEqual(result.explanation, explanation)
        self.assertIn("finish_reason=STOP", captured.output[0])
        prompt = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("matching evidence may relate and state uncertainty", prompt)
        self.assertIn("one focused, complete paragraph of about 40–60 words", prompt)
        self.assertIn("Start exactly with 'The reported issue is that'", prompt)
        self.assertIn("Do not include steps, lists, headings, code", prompt)
        client.models.generate_content.assert_called_once()
        self.assertEqual(
            client.models.generate_content.call_args.kwargs["model"],
            ai_explainer.MODEL_NAME,
        )
        client.close.assert_called_once()

    def test_incomplete_leading_fragment_is_rejected(self):
        response = SimpleNamespace(
            text="this limit is exceeded, a 413 response is returned.",
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

        result, client = self.run_explainer(
            response,
            "When this limit is exceeded, a 413 response is returned.",
        )

        self.assertFalse(result.available)
        self.assertEqual(result.reason, "incomplete_response")
        client.models.generate_content.assert_called_once()

    def test_reported_awkward_lowercase_sentence_is_not_accepted(self):
        response = SimpleNamespace(
            text="this limit is exceeded, a 413 response is returned.",
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

        result, _client = self.run_explainer(
            response,
            "When this limit is exceeded, a 413 response is returned.",
        )

        self.assertFalse(result.available)
        self.assertEqual(result.reason, "incomplete_response")

    def test_lowercase_opening_is_normalized_for_complete_response(self):
        response = SimpleNamespace(
            text=(
                "the reported issue is that payment requests time out. The retry "
                "path could be related."
            ),
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

        with self.assertLogs(ai_explainer._LOGGER, level="WARNING") as captured:
            result, client = self.run_explainer(response)

        self.assertTrue(result.available)
        self.assertIn("finish_reason=STOP", captured.output[0])
        self.assertTrue(result.explanation.startswith("The reported issue is that"))

    def test_complete_text_is_accepted_when_finish_metadata_is_missing(self):
        response = SimpleNamespace(
            text=(
                "The reported issue is that payment requests time out. The retry "
                "path may be related."
            )
        )

        with self.assertLogs(ai_explainer._LOGGER, level="WARNING") as captured:
            result, _client = self.run_explainer(response)

        self.assertTrue(result.available)
        self.assertEqual(result.explanation, response.text)
        self.assertIn("finish_reason=unavailable", captured.output[0])

    def test_unspecified_finish_reason_uses_text_validation(self):
        response = SimpleNamespace(
            text=(
                "The reported issue is that payment requests time out. The retry "
                "path may be related."
            ),
            candidates=[
                SimpleNamespace(finish_reason="FINISH_REASON_UNSPECIFIED")
            ],
        )

        result, _client = self.run_explainer(response)

        self.assertTrue(result.available)

    def test_token_limited_response_is_rejected_even_if_text_looks_complete(self):
        response = SimpleNamespace(
            text="Payment requests time out and may reach the retry handler.",
            candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
        )

        with self.assertLogs(ai_explainer._LOGGER, level="WARNING") as captured:
            result, client = self.run_explainer(response)

        self.assertFalse(result.available)
        self.assertEqual(result.reason, "incomplete_response")
        self.assertIn("finish_reason=MAX_TOKENS", captured.output[0])
        config = client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(config["max_output_tokens"], 256)
        self.assertEqual(
            config["thinking_config"],
            {"thinking_level": "MINIMAL"},
        )

    def test_oversized_response_is_rejected_instead_of_clipped(self):
        response = SimpleNamespace(
            text=(
                "Payment requests time out and may reach the retry handler. "
                + ("More detail. " * 300)
            ),
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

        result, _client = self.run_explainer(response)

        self.assertFalse(result.available)
        self.assertEqual(result.reason, "incomplete_response")

    def test_missing_key_falls_back_without_creating_a_client(self):
        client, fake_genai, fake_types = self.make_mock_client()

        with (
            patch.object(ai_explainer, "_read_api_key", return_value=""),
            patch.object(ai_explainer, "genai", fake_genai),
            patch.object(ai_explainer, "types", fake_types),
        ):
            result = ai_explainer.explain_bug(
                "Payment requests time out.",
                self.EVIDENCE,
            )

        self.assertFalse(result.available)
        self.assertEqual(result.reason, "missing_api_key")
        fake_genai.Client.assert_not_called()
        client.models.generate_content.assert_not_called()

    def test_provider_error_falls_back_without_logging_provider_details_or_key(self):
        api_key = "test-key-do-not-log"
        provider_detail = "private provider response detail"
        client, fake_genai, fake_types = self.make_mock_client()
        client.models.generate_content.side_effect = RuntimeError(provider_detail)

        with (
            patch.object(ai_explainer, "_read_api_key", return_value=api_key),
            patch.object(ai_explainer, "genai", fake_genai),
            patch.object(ai_explainer, "types", fake_types),
            self.assertLogs(ai_explainer._LOGGER, level="WARNING") as captured,
        ):
            result = ai_explainer.explain_bug(
                "Payment requests time out.",
                self.EVIDENCE,
            )

        self.assertFalse(result.available)
        self.assertEqual(result.reason, "service_error")
        self.assertEqual(len(captured.output), 1)
        self.assertNotIn(api_key, captured.output[0])
        self.assertNotIn(provider_detail, captured.output[0])
        client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
