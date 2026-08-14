from django.conf import settings
from django.test import SimpleTestCase


class LLMConfigurationDocumentationTests(SimpleTestCase):
    def test_environment_example_documents_universal_llm_settings(self):
        contents = (settings.BASE_DIR / ".env.example").read_text(encoding="utf-8")
        configured_names = {
            line.partition("=")[0].strip()
            for line in contents.splitlines()
            if line.strip() and not line.lstrip().startswith("#") and "=" in line
        }

        self.assertTrue(
            {
                "LLM_API_KEY",
                "LLM_MODEL",
                "LLM_BASE_URL",
                "LLM_TIMEOUT_SECONDS",
                "LLM_TEMPERATURE",
                "LLM_REASONING_EFFORT",
                "LLM_MAX_TOKENS_PARAMETER",
            }.issubset(configured_names)
        )
        self.assertTrue(
            {
                "VOCABULARY_LLM_PROVIDER",
                "OPENAI_API_KEY",
                "GEMINI_API_KEY",
                "FIREWORKS_API_KEY",
            }.isdisjoint(configured_names)
        )
