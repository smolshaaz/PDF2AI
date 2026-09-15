from pdf2ai.extraction.validation import missing_token_count


def test_formatting_does_not_flag_complete_table():
    assert missing_token_count('Policy limit 5000\nFlood excluded',
                               '| Policy limit | 5000 |\n| Flood | **excluded** |') == 0


def test_missing_exclusion_number_and_repeated_word_are_detected():
    assert missing_token_count('Flood excluded. Limit 5000. Flood excluded.',
                               'Flood excluded. Limit.') == 3


def test_unicode_and_html_are_compared_without_changing_output():
    assert missing_token_count('Café & ﬂood', 'Cafe\u0301 &amp; **flood**') == 0
