import unittest

from reading_pack.errors import ReadingPackError
from reading_pack_producer.pipeline_answer_evidence import answer_quote_bindings


class AnswerEvidenceTests(unittest.TestCase):
    def test_bold_markup_restores_the_actual_substring_and_offsets(self):
        answer = '**Morning rule**: daylight and a green lamp are both required.'
        quote = 'Morning rule: daylight and a green lamp'
        result = answer_quote_bindings(answer, [quote])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['verbatim_quote'], 'Morning rule**: daylight and a green lamp')
        self.assertEqual(answer[result[0]['start']:result[0]['end']],result[0]['verbatim_quote'])
        self.assertEqual(answer_quote_bindings(answer,['daylight and a green lamp']),[])

    def test_paraphrases_reversed_conditions_and_changed_numbers_still_fail(self):
        answer = '**Morning rule**: daylight and a green lamp are both required. Budget 50.'
        for quote in ['Morning rule: daylight or a green lamp','Budget 500.',
                      'daylight alone is sufficient','', 'Morning  rule: daylight']:
            with self.assertRaises(ReadingPackError):answer_quote_bindings(answer,[quote])

    def test_unmatched_markers_multiplication_and_code_are_not_erased(self):
        for answer,quote in [('a*b','ab'),('a**b','ab'),('**bad marker: value','bad marker: value')]:
            # A literal substring remains valid even inside unmatched markup.
            if quote not in answer:
                with self.assertRaises(ReadingPackError):answer_quote_bindings(answer,[quote])
        with self.assertRaises(ReadingPackError):
            answer_quote_bindings('**`literal`**: value',['`literal`: value'])
        with self.assertRaises(ReadingPackError):
            answer_quote_bindings('`**literal**: value`',['literal: value'])
