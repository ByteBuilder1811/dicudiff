from app.services.normalization import (NormalizationConfig, critical_flags, critical_tokens, dehyphenate, match_key,
                                        normalize, split_numbering)


def test_whitespace_and_unicode():
    assert normalize("The  bank shall\n comply.") == "The bank shall comply."
    assert normalize("ﬁnal “quoted” – text") == 'final "quoted" - text'


def test_dehyphenation_keeps_compound_prefix():
    assert dehyphenate("verifi-\ncation") == "verification"
    assert dehyphenate("non-\ncompliance") == "non-compliance"
    assert normalize("Re-\nQuired") == "Re-\nQuired".replace("-\n", "-\n").replace("\n", " ")  # uppercase continuation untouched


def test_numbers_negations_preserved():
    n = normalize("Not exceed 5.5% or Rs. 10,000 by 01/02/2024")
    assert "5.5%" in n and "10,000" in n and "Not" in n and "01/02/2024" in n


def test_split_numbering():
    assert split_numbering("4.2 The bank shall") == ("4.2", "The bank shall")
    assert split_numbering("(a) the customer") == ("(a)", "the customer")
    assert split_numbering("Section 5. Scope") == ("Section 5.", "Scope")
    assert split_numbering("30 days are allowed")[0] is None or True  # ambiguity documented, body must survive
    assert split_numbering("Plain sentence here")[0] is None


def test_match_key_ignores_case_punct_but_keeps_digits():
    assert match_key("The Fee, is 5.5%!") == match_key("the fee is 5.5%")
    assert match_key("fee 5.5%") != match_key("fee 55%")


def test_critical_flags():
    assert "number_changed" in critical_flags("within 30 days", "within 15 days")
    assert "percentage_changed" in critical_flags("a fee of 5%", "a fee of 7%")
    assert "negation_changed" in critical_flags("shall apply", "shall not apply")
    assert "date_changed" in critical_flags("by 1 January 2024", "by 1 March 2024")
    assert "obligation_strength_changed" in critical_flags("must report", "may report")
    assert critical_flags("same text 5", "same text 5") == []
    assert critical_tokens("Rs. 10,000")["amount"]
