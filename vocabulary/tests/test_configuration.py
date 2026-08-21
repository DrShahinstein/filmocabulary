from django.conf import settings
from django.test import SimpleTestCase


class LLMConfigurationDocumentationTests(SimpleTestCase):
    def test_environment_example_documents_universal_llm_settings(self):
        contents = (settings.BASE_DIR / ".env.example").read_text(encoding="utf-8")
        active_assignments = {}
        documented_names = set()
        for line in contents.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            is_comment = stripped.startswith("#")
            candidate = stripped.removeprefix("#").strip()
            name, separator, value = candidate.partition("=")
            name = name.strip()
            if not separator or not name.replace("_", "").isalnum() or not name.isupper():
                continue

            documented_names.add(name)
            if not is_comment:
                active_assignments[name] = value.strip()

        self.assertTrue(
            {
                "LLM_API_KEY",
                "LLM_MODEL",
                "LLM_BASE_URL",
                "LLM_TIMEOUT_SECONDS",
                "LLM_TEMPERATURE",
                "LLM_REASONING_EFFORT",
                "LLM_MAX_TOKENS_PARAMETER",
                "LLM_EDITORIAL_REVIEW",
            }.issubset(documented_names)
        )
        self.assertTrue(
            {
                "VOCABULARY_LLM_PROVIDER",
                "OPENAI_API_KEY",
                "GEMINI_API_KEY",
                "FIREWORKS_API_KEY",
            }.isdisjoint(documented_names)
        )
        required_assignments = {
            "LLM_API_KEY",
            "LLM_MODEL",
            "LLM_BASE_URL",
            "LLM_TIMEOUT_SECONDS",
            "LLM_MAX_TOKENS_PARAMETER",
            "LLM_EDITORIAL_REVIEW",
        }
        self.assertTrue(required_assignments.issubset(active_assignments))
        self.assertFalse(
            {
                name
                for name in required_assignments
                if not active_assignments[name]
            },
            "Required LLM example assignments must be non-empty.",
        )
        self.assertEqual(active_assignments["LLM_MODEL"], "gpt-4.1-mini")
        self.assertEqual(active_assignments["LLM_EDITORIAL_REVIEW"], "true")
        self.assertEqual(
            active_assignments["LLM_BASE_URL"],
            "https://api.openai.com/v1",
        )
