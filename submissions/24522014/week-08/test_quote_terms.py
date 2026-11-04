"""The quote filter blocks only explicit violations, reads structured fields first and logs disagreement."""

import unittest

from quote_terms import check_quote, structured_terms, text_grades, text_hours

NOW = 1793788000.0
FOOD = {"cert": True, "grades": ["A", "B"], "min_hours_left": 128.0}
GOODS = {"cert": True, "grades": ["A", "B"]}


def task(text="asking 100.", metadata=None, data=None, task_metadata=None):
    parts = [{"text": text}]
    if data is not None:
        parts.append({"data": data, "mediaType": "application/json"})
    message = {"parts": parts}
    if metadata is not None:
        message["metadata"] = metadata
    result = {"id": "t", "status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": message}}
    if task_metadata is not None:
        result["metadata"] = task_metadata
    return result


def check(accept=GOODS, text="asking 100.", **kwargs):
    return check_quote(task(text, **kwargs), text, accept, NOW)


class StructuredFirst(unittest.TestCase):
    def test_bidet_shape_grade_only_in_message_metadata(self):
        text = "Bidet DDS-S550R: asking 178698. Send your offer as a number.\nReply with a number, or ok to take the ask: 178698"
        result = check(GOODS, text, metadata={"ask": 178698.0, "warranty_hours": 6.0, "certificate": True, "grade": "C"},
                       data={"ask": 178698.0, "sku": "G0aebcd94"})
        self.assertEqual((result["reason_code"], result["basis"]), ("quote_grade_unacceptable", "structured"))

    def test_every_observed_location_and_field_name(self):
        for kwargs in ({"metadata": {"grade": "C"}}, {"data": {"grade": "C"}}, {"data": {"grade_now": "C"}},
                       {"task_metadata": {"grade": "C"}}, {"data": {"offer_terms": {"grade": "C"}}},
                       {"data": {"cert": {"cert": {"grade": "C"}, "sig": "x"}}}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(check(**kwargs)["reason_code"], "quote_grade_unacceptable")
        for kwargs in ({"metadata": {"hours_left": 50.0}}, {"data": {"hours_left": 50}},
                       {"data": {"shelf_hours_left": 50.0}},
                       {"data": {"offer_terms": {"expires_at": NOW + 50 * 3600}}},
                       {"data": {"cert": {"cert": {"expires_at": NOW + 50 * 3600}}}}):
            with self.subTest(kwargs=kwargs):
                result = check(FOOD, **kwargs)
                self.assertEqual((result["reason_code"], result["basis"]), ("quote_shelf_life_short", "structured"))

    def test_a_bare_expires_at_may_be_quote_validity_and_is_ignored(self):
        self.assertIsNone(check(FOOD, metadata={"expires_at": NOW + 600})["reason_code"])

    def test_passing_values_do_not_set_a_reason(self):
        self.assertIsNone(check(FOOD, metadata={"grade": "B", "hours_left": 128.0})["reason_code"])

    def test_shelf_life_is_not_applied_without_a_minimum(self):
        self.assertIsNone(check(GOODS, metadata={"grade": "A", "hours_left": 1.0})["reason_code"])

    def test_shelf_life_is_reported_first_like_the_kit(self):
        self.assertEqual(check(FOOD, metadata={"grade": "C", "hours_left": 5})["reason_code"], "quote_shelf_life_short")


class MissingOrUnclear(unittest.TestCase):
    def test_absent_or_invalid_values_never_block(self):
        for kwargs in ({}, {"metadata": {"grade": None, "hours_left": None}}, {"metadata": {"grade": "Z", "hours_left": "soon"}},
                       {"metadata": {"grade": "premium"}}, {"metadata": {"hours_left": float("nan")}},
                       {"metadata": {"hours_left": True}}, {"metadata": "not a dict"}, {"data": ["x"]},
                       {"data": {"cert": "market-signed", "offer_terms": "none"}}):
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(check(FOOD, **kwargs)["reason_code"])

    def test_hostile_message_shapes_do_not_raise(self):
        for shaped in ({"status": None}, {"status": {"message": "x"}}, {"status": {"message": {"parts": "x"}}},
                       {"status": {"message": {"parts": [None, 3]}}}, {"metadata": []}):
            with self.subTest(shaped=shaped):
                self.assertIsNone(check_quote(shaped, "asking 1.", FOOD, NOW)["reason_code"])

    def test_vague_or_lower_bound_shelf_life_never_blocks(self):
        for text, note in (("A fair amount of shelf life left. asking 1.", "vague"),
                           ("Grade A, close to its date. asking 1.", "vague"),
                           ("at least 38 hours of shelf life left. asking 1.", "lower_bound"),
                           ("over 5 h shelf life left. asking 1.", "lower_bound"),
                           ("shelf life left: more than 5 h. asking 1.", "lower_bound"),
                           ("120 h shelf life total. asking 1.", "vague"),
                           ("24 h warranty. asking 1.", "absent")):
            with self.subTest(text=text):
                result = check(FOOD, text)
                self.assertIsNone(result["reason_code"])
                self.assertEqual(result["text_hours_note"], note)

    def test_several_or_negated_text_grades_do_not_block(self):
        for text in ("grade A now, grade C next week. asking 1.", "This is not grade C. asking 1.",
                     "Better than grade C. asking 1."):
            with self.subTest(text=text):
                result = check(GOODS, text)
                self.assertIsNone(result["reason_code"])
                self.assertTrue(result["text_grade_ambiguous"])

    def test_words_that_only_look_like_grades(self):
        for text in ("Grade 304 steel. asking 1.", "grade A4 paper. asking 1.", "true grade shown. asking 1.",
                     "C급 포장 [한우1등급]. asking 1.", "grade a lot. asking 1."):
            with self.subTest(text=text):
                self.assertEqual(text_grades(text), (set(), False))


class TextFallback(unittest.TestCase):
    def test_real_grade_wording(self):
        for text in ("Foo (G5f), grade C, 243.2 h shelf life left.", "grade=C cert=market-signed", "This is Grade C kimchi",
                     "a grade C unit", "| grade C now, market certificate", "등급: C", "C등급 상품"):
            with self.subTest(text=text):
                result = check(GOODS, text)
                self.assertEqual((result["reason_code"], result["basis"]), ("quote_grade_unacceptable", "text"))

    def test_real_shelf_life_wording(self):
        for text, hours in (("grade A, 243.2 h shelf life left.", 243.2), ("102 hours of shelf life left", 102),
                            ("162 h of shelf life left", 162), ("shelf life left 35.68h;", 35.68),
                            ("shelf_left=136h", 136), ("5 days of shelf life left", 120),
                            ("Remaining shelf life is 39.7h (not a long-term guarantee).", 39.7),
                            ("about 7 h of shelf life left", 7.7), ("유통기한 35시간 남음", 35), ("소비기한 3일", 72)):
            with self.subTest(text=text):
                value, note = text_hours(text)
                self.assertAlmostEqual(value, hours)
                self.assertEqual(note, "stated")

    def test_text_shortfall_blocks_only_below_the_minimum(self):
        self.assertEqual(check(FOOD, "grade A, 127 h of shelf life left.")["reason_code"], "quote_shelf_life_short")
        self.assertIsNone(check(FOOD, "grade A, 128 h of shelf life left.")["reason_code"])

    def test_about_gets_a_margin(self):
        self.assertIsNone(check(FOOD, "about 120 h of shelf life left.")["reason_code"])
        self.assertEqual(check(FOOD, "about 100 h of shelf life left.")["reason_code"], "quote_shelf_life_short")

    def test_two_different_text_hours_do_not_block(self):
        self.assertEqual(text_hours("5 h of shelf life left, or 500 h of shelf life left"), (None, "ambiguous"))


class Conflicts(unittest.TestCase):
    def test_grade_disagreement_takes_the_worse_and_is_logged(self):
        for text, metadata, basis in (("grade A. asking 1.", {"grade": "C"}, "structured"),
                                      ("grade C. asking 1.", {"grade": "A"}, "text")):
            with self.subTest(text=text):
                result = check(GOODS, text, metadata=metadata)
                self.assertEqual((result["reason_code"], result["basis"], result["grade"]),
                                 ("quote_grade_unacceptable", basis, "C"))
                self.assertEqual(result["conflicts"], ["grade"])

    def test_disagreement_within_policy_passes_but_is_logged(self):
        result = check(GOODS, "grade A. asking 1.", metadata={"grade": "B"})
        self.assertIsNone(result["reason_code"])
        self.assertEqual((result["grade"], result["conflicts"]), ("B", ["grade"]))

    def test_hours_disagreement_takes_the_lower_and_is_logged(self):
        result = check(FOOD, "500 h of shelf life left.", metadata={"hours_left": 50.0})
        self.assertEqual((result["reason_code"], result["basis"], result["conflicts"]),
                         ("quote_shelf_life_short", "structured", ["hours_left"]))
        result = check(FOOD, "50 h of shelf life left.", metadata={"hours_left": 500.0})
        self.assertEqual((result["reason_code"], result["basis"]), ("quote_shelf_life_short", "text"))

    def test_rounding_is_not_a_conflict(self):
        self.assertEqual(check(FOOD, "200 h of shelf life left.", metadata={"hours_left": 200.4})["conflicts"], [])

    def test_agreement_names_both_sources(self):
        result = check(GOODS, "grade C. asking 1.", metadata={"grade": "C"})
        self.assertEqual((result["basis"], result["conflicts"]), ("structured+text", []))


class Reading(unittest.TestCase):
    def test_structured_terms_collects_all_values(self):
        grades, hours = structured_terms(task(metadata={"grade": "b", "hours_left": "12.5"},
                                              data={"grade_now": "C", "shelf_hours_left": 11}), NOW)
        self.assertEqual(grades, {"B", "C"})
        self.assertEqual(sorted(hours), [11, 12.5])


if __name__ == "__main__":
    unittest.main()
