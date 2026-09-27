import unittest

from eristic import ERISTIC_SELLER_PROMPT, eristic_role_prompt
from protocol import FORMAT_PARAGRAPHS, role_prompt


class EristicPromptTests(unittest.TestCase):
    def setUp(self):
        self.scenario = {"item": "bike", "reserve": 80, "budget": 120}

    def test_buyer_is_unchanged(self):
        for condition in FORMAT_PARAGRAPHS:
            self.assertEqual(
                eristic_role_prompt("buyer", self.scenario, condition),
                role_prompt("buyer", self.scenario, condition),
            )

    def test_seller_treatment_preserves_the_final_format_paragraph(self):
        for condition, paragraph in FORMAT_PARAGRAPHS.items():
            prompt = eristic_role_prompt("seller", self.scenario, condition)
            self.assertIn(ERISTIC_SELLER_PROMPT, prompt)
            self.assertEqual(prompt.rsplit("\n\n", 1)[1], paragraph)


if __name__ == "__main__":
    unittest.main()
