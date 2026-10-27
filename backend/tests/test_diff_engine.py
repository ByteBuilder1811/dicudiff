import numpy as np

from app.services.diff_engine import PART_MARKER, compare_units, summarize
from app.services.normalization import NormalizationConfig
from app.services.types import DocUnit


def U(text, kind="paragraph", page=1):
    return DocUnit(0, text, kind, page)


def doc(*items):
    out = []
    for it in items:
        out.append(U(*it) if isinstance(it, tuple) else U(it))
    return out


def cats(records):
    return [r.category for r in records]


BASE = lambda: doc(
    ("1. Scope", "heading"),
    "1.1 This policy applies to all customers opening accounts after the effective date.",
    ("2. Verification", "heading"),
    "2.1 The institution shall verify the identity of each customer using an officially valid document.",
    "2.2 Verification must be completed within 30 days of account opening.",
    ("3. Penalties", "heading"),
    "3.1 A penalty of 2% of the outstanding amount applies for late compliance.",
)


def test_identical_documents():
    recs, _ = compare_units(BASE(), BASE())
    assert set(cats(recs)) == {"UNCHANGED"} and len(recs) == 7


def test_added_paragraph():
    new = BASE()
    new.insert(5, U("2.3 Biometric verification is additionally required for accounts above the high-value threshold."))
    recs, _ = compare_units(BASE(), new)
    added = [r for r in recs if r.category == "ADDED"]
    assert len(added) == 1 and "Biometric" in added[0].new.text and added[0].old is None
    assert cats(recs).count("UNCHANGED") == 7


def test_deleted_paragraph():
    new = BASE(); del new[4]
    recs, _ = compare_units(BASE(), new)
    removed = [r for r in recs if r.category == "REMOVED"]
    assert len(removed) == 1 and "30 days" in removed[0].old.text and removed[0].new is None


def test_modified_paragraph_has_inline_diff_and_flags():
    new = BASE(); new[4] = U("2.2 Verification must be completed within 15 days of account opening.")
    recs, _ = compare_units(BASE(), new)
    mod = [r for r in recs if r.category == "MODIFIED"]
    assert len(mod) == 1
    assert "number_changed" in mod[0].flags and mod[0].match_method == "fuzzy"
    assert ["del", "30"] in mod[0].inline_diff["old"] and ["ins", "15"] in mod[0].inline_diff["new"]


def test_changed_percentage_amount_date_negation():
    cases = [
        ("A penalty of 2% applies.", "A penalty of 4% applies.", "percentage_changed"),
        ("The limit is Rs. 50,000 per day.", "The limit is Rs. 75,000 per day.", "amount_changed"),
        ("Submit the form by 1 January 2024 to the office.", "Submit the form by 1 March 2024 to the office.", "date_changed"),
        ("The customer is required to pay the fee.", "The customer is not required to pay the fee.", "negation_changed"),
    ]
    for a, b, flag in cases:
        recs, _ = compare_units(doc(a), doc(b))
        assert recs[0].category == "MODIFIED" and flag in recs[0].flags, (a, recs[0].category, recs[0].flags)


def test_reordered_sections_flagged_relocated_not_deleted():
    new = BASE()
    new[1], new[6] = new[6], new[1]  # swap two paragraphs far apart
    recs, _ = compare_units(BASE(), new)
    assert "REMOVED" not in cats(recs) and "ADDED" not in cats(recs)
    assert "POSSIBLY_RELOCATED" in cats(recs)
    reloc = [r for r in recs if r.category == "POSSIBLY_RELOCATED"][0]
    assert reloc.old.text != reloc.new.text or reloc.old.page == reloc.new.page
    assert "different position" in reloc.explanation


def test_renumbered_clauses_unchanged_with_flag():
    new = BASE()
    new[1] = U("1.2 This policy applies to all customers opening accounts after the effective date.")
    recs, _ = compare_units(BASE(), new)
    r = [x for x in recs if "renumbered" in x.flags]
    assert len(r) == 1 and r[0].category == "UNCHANGED" and "1.1" in r[0].explanation and "1.2" in r[0].explanation
    recs2, _ = compare_units(BASE(), new, NormalizationConfig(renumbering_is_change=True))
    assert any(x.category == "MODIFIED" and "renumbered" in x.flags for x in recs2)


def test_whitespace_and_page_break_changes_are_unchanged():
    old = [U("2.1 The institution shall verify the\nidentity of each customer.", page=1)]
    new = [U("2.1   The institution shall verify the identity   of each customer.", page=2)]
    recs, _ = compare_units(old, new)
    assert recs[0].category == "UNCHANGED" and recs[0].old.page == 1 and recs[0].new.page == 2


def test_punctuation_only_change_reported_unless_configured():
    old, new = doc("The fee, if any, is payable."), doc("The fee if any is payable")
    recs, _ = compare_units(old, new)
    assert recs[0].category == "MODIFIED" and "punctuation_or_case_only" in recs[0].flags
    recs, _ = compare_units(old, new, NormalizationConfig(ignore_punctuation_case=True))
    assert recs[0].category == "UNCHANGED"


def test_duplicate_paragraphs_matched_in_order():
    dup = "The customer shall keep records for five years from the date of transaction."
    old = doc(dup, "Unique middle paragraph about reporting obligations to the regulator.", dup)
    new = doc(dup, "Unique middle paragraph about reporting obligations to the regulator.", dup)
    recs, _ = compare_units(old, new)
    assert cats(recs) == ["UNCHANGED"] * 3
    new2 = doc(dup, "Unique middle paragraph about reporting obligations to the regulator.")
    recs, _ = compare_units(old, new2)
    assert cats(recs).count("REMOVED") == 1 and cats(recs).count("UNCHANGED") == 2


def test_repeated_headings_do_not_cross_match():
    old = doc(("Definitions", "heading"), "Alpha clause is about apples and oranges in markets.",
              ("Definitions", "heading"), "Beta clause is about gears and pulleys in engines.")
    new = doc(("Definitions", "heading"), "Alpha clause is about apples and oranges in markets.",
              ("Definitions", "heading"), "Beta clause is about gears and pulleys in motors.")
    recs, _ = compare_units(old, new)
    assert cats(recs) == ["UNCHANGED", "UNCHANGED", "UNCHANGED", "MODIFIED"]


def test_low_confidence_match_goes_to_review():
    old = doc("The institution shall verify the identity of every customer before onboarding.")
    new = doc("Customer onboarding requires the institution to confirm who the customer is, using approved documents.")
    recs, _ = compare_units(old, new)
    assert recs[0].category in ("NEEDS_REVIEW", "REMOVED", "ADDED")  # never a confident MODIFIED
    assert recs[0].category != "MODIFIED"


def test_semantic_pass_only_yields_needs_review():
    old = doc("Customers must prove who they are before an account is opened.")
    new = doc("Identity confirmation is mandatory prior to account creation by clients.")
    embed = lambda texts: np.ones((len(texts), 3)) / np.sqrt(3)  # everything "similar"
    recs, notes = compare_units(old, new, embed=embed)
    assert recs[0].category == "NEEDS_REVIEW" and recs[0].match_method == "semantic" and recs[0].review_status == "pending"
    recs, notes = compare_units(old, new)  # no embedder => honest REMOVED + ADDED and a note
    assert sorted(cats(recs)) == ["ADDED", "REMOVED"] and any("Semantic" in n for n in notes)


def test_empty_documents():
    assert compare_units([], [])[0] == []
    recs, _ = compare_units([], doc("New text exists here."))
    assert cats(recs) == ["ADDED"]
    recs, _ = compare_units(doc("Old text exists here."), [])
    assert cats(recs) == ["REMOVED"]


def test_summary_counts():
    new = BASE(); new[4] = U("2.2 Verification must be completed within 15 days of account opening."); del new[6]
    recs, _ = compare_units(BASE(), new)
    s = summarize(recs)
    assert s["counts"]["MODIFIED"] == 1 and s["counts"]["REMOVED"] == 1 and s["critical_changes"] == 1
    assert all(r.explanation for r in recs)


def test_unrelated_clause_replaced_in_place_is_removed_plus_added():
    # A deleted clause and an unrelated inserted clause in the same gap must not be paired just because
    # character ratios of English prose are high (~0.5) from shared letters and stop-words.
    old = BASE(); old.insert(5, U("2.3 Video based verification is permitted only for resident customers."))
    new = BASE(); new[4] = U("2.2 Verification must be completed within 15 days of account opening.")
    new.insert(5, U("2.4 Biometric authentication is additionally required for high value accounts."))
    recs, _ = compare_units(old, new)
    s = summarize(recs)["counts"]
    assert (s["MODIFIED"], s["REMOVED"], s["ADDED"], s["NEEDS_REVIEW"], s["UNCHANGED"]) == (1, 1, 1, 0, 6)


def test_modified_confidence_is_raw_similarity_not_bonus_capped():
    new = BASE(); new[4] = U("2.2 Verification must be completed within 15 days of account opening.")
    mod = [r for r in compare_units(BASE(), new)[0] if r.category == "MODIFIED"][0]
    assert 0.75 <= mod.confidence < 1.0


# ---------------------------------------------------------------------------------------------------------------
# Agent A audit: regression tests for alignment edge cases (see docs/benchmarks/AGENT_A_FINDINGS.md).
# Some tests pin a KNOWN LIMITATION (current, documented behaviour) rather than ideal behaviour; they say so.
# ---------------------------------------------------------------------------------------------------------------
import pytest  # noqa: E402

from app.services.section_alignment import lexical_similarity, prepare, term_weights  # noqa: E402

CTX_A = lambda: [U("1. General", "heading"), U("1.1 This circular applies to every regulated entity operating in the country."),  # noqa: E731
                 U("1.2 Entities must appoint a principal officer responsible for compliance reporting.")]
CTX_B = lambda: [U("3. Penalties", "heading"), U("3.1 A penalty of 2% of the outstanding amount applies for late compliance.")]  # noqa: E731


def around(*units):
    return CTX_A() + list(units) + CTX_B()


def non_unchanged(recs):
    return [r for r in recs if r.category != "UNCHANGED"]


def test_unrelated_clauses_sharing_template_vocabulary_are_never_confident_modified():
    # Same clause number, same template frame ("... shall ... and keep a copy for N years"), different provision.
    a = "1.3 The reporting entity shall verify the identity of each applicant and keep a copy for 22 years."
    b = "1.3 The bank shall report suspicious activity concerning linked accounts and keep a copy for 69 years."
    for old, new in ((around(U(a)), around(U(b))), ([U(a[4:])], [U(b[4:])])):
        recs = non_unchanged(compare_units(old, new)[0])
        assert "MODIFIED" not in cats(recs), recs
        # at most a low-confidence review candidate (raw similarity is reported, not the bonus-inflated score)
        for r in recs:
            if r.category == "NEEDS_REVIEW":
                assert r.confidence < 0.6 and r.review_status == "pending" and "low_confidence" in r.flags


def test_distinctive_term_weights_downweight_template_words_only():
    template = [U(f"The {s} shall {v} and keep a copy for {n} years.") for s, v, n in
                (("bank", "verify the identity of each applicant", 3), ("custodian", "segregate client funds", 5),
                 ("lender", "disclose all fees", 7), ("branch", "train its staff", 9))]
    old = prepare(template + [U("The entity shall verify the identity of each applicant and keep a copy for 22 years.")])
    new = prepare([U(t.text) for t in template] + [U("The bank shall report suspicious activity concerning linked accounts and keep a copy for 69 years.")])
    idf = term_weights(old, new)
    assert idf["shall"] < idf["suspicious"] and idf["copy"] < idf["identity"]
    unrelated = (old[-1], new[-1])
    assert lexical_similarity(*unrelated, idf) < lexical_similarity(*unrelated)  # template overlap counts less
    one_word = prepare([U("The entity shall verify the identity of each applicant and keep a copy for 25 years.")])[0]
    assert lexical_similarity(old[-1], one_word, idf) >= 0.75  # a genuine small edit stays a confident match


def test_small_vs_large_rewording():
    old = around(U("1.3 The bank shall inform customers of any change in charges at least 30 days in advance."))
    small = around(U("1.3 The bank shall notify customers of any change in charges at least 30 days in advance."))
    large = around(U("1.3 Customers are to be told about revised fees no less than a month before they take effect."))
    r = non_unchanged(compare_units(old, small)[0])
    assert cats(r) == ["MODIFIED"] and r[0].confidence >= 0.75
    r = non_unchanged(compare_units(old, large)[0])
    assert "MODIFIED" not in cats(r)  # lexical engine cannot know it is the same rule: REMOVED + ADDED or NEEDS_REVIEW
    r = non_unchanged(compare_units(old, large, embed=lambda t: np.ones((len(t), 2)) / np.sqrt(2))[0])
    assert all(x.category == "NEEDS_REVIEW" for x in r if x.old and x.new)  # a semantic hint is only ever a candidate


SPLIT_WHOLE = ("1.3 The bank shall verify the identity of each customer using an officially valid document, "
               "and shall complete the verification within 30 days of account opening.")
SPLIT_P1 = "1.3 The bank shall verify the identity of each customer using an officially valid document."
SPLIT_P2 = "1.4 The bank shall complete the verification within 30 days of account opening."


def test_split_clause_is_one_modified_record_with_both_parts():
    # 1 -> 2 split (formerly a known limitation: MODIFIED + ADDED). Now one MODIFIED record flagged 'split' whose new
    # side carries BOTH parts (joined with a marker line) and whose explanation names both parts' locations.
    old = around(U(SPLIT_WHOLE))
    new = around(U(SPLIT_P1), U(SPLIT_P2, page=2))
    r = non_unchanged(compare_units(old, new)[0])
    assert cats(r) == ["MODIFIED"] and "split" in r[0].flags and r[0].review_status == "none"
    assert SPLIT_P1 in r[0].new.text and SPLIT_P2 in r[0].new.text and PART_MARKER in r[0].new.text  # nothing is lost
    assert r[0].old.text == SPLIT_WHOLE and r[0].new.page == 1 and r[0].new.page_end == 2
    assert "§1.3" in r[0].explanation and "§1.4" in r[0].explanation and "p.2" in r[0].explanation
    assert r[0].inline_diff is not None and 0.75 <= r[0].confidence <= 1.0


def test_merged_clauses_is_one_modified_record_with_both_parts():
    old = around(U(SPLIT_P1), U(SPLIT_P2))
    new = around(U(SPLIT_WHOLE))
    r = non_unchanged(compare_units(old, new)[0])
    assert cats(r) == ["MODIFIED"] and "merged" in r[0].flags
    assert SPLIT_P1 in r[0].old.text and SPLIT_P2 in r[0].old.text and r[0].new.text == SPLIT_WHOLE
    assert "§1.3" in r[0].explanation and "§1.4" in r[0].explanation


def test_split_keeps_high_risk_flags():
    old = around(U(SPLIT_WHOLE))
    new = around(U(SPLIT_P1), U(SPLIT_P2.replace("30 days", "15 days")))
    r = non_unchanged(compare_units(old, new)[0])
    assert cats(r) == ["MODIFIED"] and "split" in r[0].flags and "number_changed" in r[0].flags


def test_partial_split_goes_to_review():
    # the second part only partly comes from the old clause (half of it is new wording): a candidate, not a fact
    old = around(U(SPLIT_WHOLE))
    new = around(U(SPLIT_P1), U("1.4 The bank shall complete the verification within 30 days, and shall report each "
                                "failed verification to the compliance officer together with supporting evidence."))
    r = non_unchanged(compare_units(old, new)[0])
    assert "MODIFIED" not in cats(r) or not any("split" in x.flags for x in r if x.category == "MODIFIED")
    split = [x for x in r if "split" in x.flags]
    assert len(split) <= 1 and all(x.category == "NEEDS_REVIEW" and x.review_status == "pending" for x in split)


def test_unrelated_neighbour_is_not_a_split_or_merge():
    # A one-word edit next to an unrelated inserted (or deleted) clause: still MODIFIED + ADDED / MODIFIED + REMOVED.
    other = "1.4 Branch premises must display the grievance officer's contact details at the entrance."
    old = around(U(SPLIT_P1))
    new = around(U(SPLIT_P1.replace("verify", "confirm")), U(other))
    r = non_unchanged(compare_units(old, new)[0])
    assert sorted(cats(r)) == ["ADDED", "MODIFIED"] and not any({"split", "merged"} & set(x.flags) for x in r)
    r = non_unchanged(compare_units(new, old)[0])
    assert sorted(cats(r)) == ["MODIFIED", "REMOVED"] and not any({"split", "merged"} & set(x.flags) for x in r)


def test_two_unrelated_added_paragraphs_do_not_explain_a_removed_one():
    old = around(U("1.3 Lockers shall be allotted strictly in order of the waiting list maintained at each branch."))
    new = around(U("1.3 Mobile banking passwords must be changed every ninety days by the customer."),
                 U("1.4 Branch premises must display the grievance officer's contact details at the entrance."))
    r = non_unchanged(compare_units(old, new)[0])
    assert not any({"split", "merged"} & set(x.flags) for x in r) and "MODIFIED" not in cats(r)


def test_repeated_boilerplate_removed_from_the_right_section():
    bp = "This requirement is subject to the provisions of applicable law."
    def build(drop_middle):
        out = [U("1. A", "heading"), U("1.1 Banks shall file returns monthly with the supervisory department."), U(bp),
               U("2. B", "heading", 2), U("2.1 Banks shall publish audited accounts within six months of year end.", page=2), U(bp, page=2),
               U("3. C", "heading", 3), U("3.1 Banks shall maintain a liquidity coverage ratio of at least 100%.", page=3), U(bp, page=3)]
        if drop_middle:
            del out[5]
        return out
    r = non_unchanged(compare_units(build(False), build(True))[0])
    assert cats(r) == ["REMOVED"] and r[0].old.page == 2 and r[0].old.idx == 5


def test_section_swap_with_renumbering_is_relocation_not_deletion():
    old = [U("1. Scope", "heading"), U("1.1 Applies to all lenders registered under the Act."),
           U("2. Fees", "heading"), U("2.1 Processing fees shall not exceed 1% of the loan amount."),
           U("2.2 Fees shall be disclosed upfront in the key fact statement."),
           U("3. Grievances", "heading"), U("3.1 Complaints shall be resolved within 30 days."),
           U("3.2 Unresolved complaints may be escalated to the ombudsman.")]
    new = [old[0], old[1], U("2. Grievances", "heading"), U("2.1 Complaints shall be resolved within 30 days."),
           U("2.2 Unresolved complaints may be escalated to the ombudsman."), U("3. Fees", "heading"),
           U("3.1 Processing fees shall not exceed 1% of the loan amount."), U("3.2 Fees shall be disclosed upfront in the key fact statement.")]
    new = [U(u.text, u.kind, u.page) for u in new]
    recs, _ = compare_units(old, new)
    assert not {"ADDED", "REMOVED", "MODIFIED"} & set(cats(recs))
    assert cats(recs).count("POSSIBLY_RELOCATED") == 3
    assert all("renumbered" in r.flags for r in recs if r.category == "UNCHANGED" and r.old.section_id != r.new.section_id)


def test_clause_added_next_to_a_similar_clause():
    keep = "1.3 The bank shall verify the identity of each customer within 30 days of account opening."
    sib = "1.4 The bank shall verify the address of each customer within 30 days of account opening."
    r = non_unchanged(compare_units(around(U(keep)), around(U(keep), U(sib)))[0])
    assert cats(r) == ["ADDED"] and "address" in r[0].new.text
    r = non_unchanged(compare_units(around(U(keep), U(sib)), around(U(sib.replace("1.4", "1.3"))))[0])
    assert cats(r) == ["REMOVED"] and "identity" in r[0].old.text


def test_modified_clause_with_equally_similar_new_sibling_goes_to_review():
    # Old 1.3 (identity, 30 days) could have become new 1.3 (address, 30 days) or new 1.4 (identity, 15 days):
    # both are ~0.9 similar. Before the fix the section-number bonus silently chose 1.3 as a confident MODIFIED.
    old = around(U("1.3 The bank shall verify the identity of each customer within 30 days of account opening."))
    new = around(U("1.3 The bank shall verify the address of each customer within 30 days of account opening."),
                 U("1.4 The bank shall verify the identity of each customer within 15 days of account opening."))
    r = non_unchanged(compare_units(old, new)[0])
    review = [x for x in r if x.category == "NEEDS_REVIEW"]
    assert len(review) == 1 and "MODIFIED" not in cats(r) and "ADDED" in cats(r)
    assert "ambiguous_match" in review[0].flags and review[0].review_status == "pending" and "Ambiguous" in review[0].explanation


@pytest.mark.parametrize("a,b,flag", [
    ("Payment is due on 31/03/2024 for all accounts.", "Payment is due on 30/06/2024 for all accounts.", "date_changed"),
    ("The cap is USD 5 million per borrower.", "The cap is USD 5 billion per borrower.", "amount_changed"),
    ("Interest of 7.5% per annum is payable.", "Interest of 8.5% per annum is payable.", "percentage_changed"),
    ("Refer to Section 12(3) of the Act.", "Refer to Section 12(4) of the Act.", "legal_reference_changed"),
    ("Submit Form KYC-7 to the office.", "Submit Form KYC-9 to the office.", "identifier_changed"),
    ("The lender shall refund the fee.", "The lender may refund the fee.", "obligation_strength_changed"),
    ("The lender must refund the fee within 7 days.", "The lender should refund the fee within 7 days.", "obligation_strength_changed"),
    ("Customers may withdraw cash.", "Customers may not withdraw cash.", "negation_changed"),
    ("Entities shall report, unless exempted, every transaction.", "Entities shall report every transaction.", "negation_changed"),
])
def test_high_risk_token_changes_are_modified_and_flagged(a, b, flag):
    recs, _ = compare_units(doc(a), doc(b))
    assert cats(recs) == ["MODIFIED"] and flag in recs[0].flags


def test_alphanumeric_identifier_change_is_flagged():
    recs, _ = compare_units(doc("Quote reference ABC123 on every remittance form."), doc("Quote reference ABC124 on every remittance form."))
    assert recs[0].category == "MODIFIED" and recs[0].flags


def test_page_footer_noise_is_never_a_confident_change():
    # Footers should be removed by extraction; if one leaks through, the engine must not present it as a real edit.
    r = non_unchanged(compare_units(around(U("Page 1 of 4")), around(U("Page 1 of 5")))[0])
    assert "MODIFIED" not in cats(r)


def test_renamed_heading_is_review_not_modified():
    old = [U("2. Customer Due Diligence", "heading"), U("2.1 Identify each customer before onboarding.")]
    new = [U("2. Know Your Customer", "heading"), U("2.1 Identify each customer before onboarding.")]
    r = non_unchanged(compare_units(old, new)[0])
    assert cats(r) in (["NEEDS_REVIEW"], ["REMOVED", "ADDED"], ["ADDED", "REMOVED"])


def test_unmerged_page_break_fragment_is_reported_as_merge():
    # If extraction fails to merge a paragraph continued on the next page, the engine now recognises the two old
    # fragments as one merged passage (formerly MODIFIED + REMOVED).
    old = around(U("1.3 The bank shall verify the identity of each customer using an officially", page=1),
                 U("valid document and record the outcome in the customer file.", page=2))
    new = around(U("1.3 The bank shall verify the identity of each customer using an officially valid document and "
                   "record the outcome in the customer file.", page=2))
    r = non_unchanged(compare_units(old, new)[0])
    assert cats(r) == ["MODIFIED"] and "merged" in r[0].flags and "p.2" in r[0].explanation


def test_semantic_candidate_keeps_high_risk_flags():
    old = doc("Customers must prove who they are within 30 days before an account is opened.")
    new = doc("Identity confirmation is mandatory within 60 days prior to account creation by clients.")
    recs, _ = compare_units(old, new, embed=lambda t: np.ones((len(t), 3)) / np.sqrt(3))
    assert recs[0].match_method == "semantic" and "number_changed" in recs[0].flags and "semantic_candidate" in recs[0].flags


def test_every_record_carries_its_evidence():
    old = BASE() + [U("4. Misc", "heading", 2), U("4.1 Video based verification is permitted only for resident customers.", page=2)]
    new = BASE()
    new[4] = U("2.2 Verification must be completed within 15 days of account opening.")
    new[1], new[6] = new[6], new[1]
    new += [U("4. Misc", "heading", 3), U("4.2 Biometric authentication is additionally required for high value accounts.", page=3)]
    recs, _ = compare_units(old, new, embed=lambda t: np.ones((len(t), 2)) / np.sqrt(2))
    assert {"UNCHANGED", "MODIFIED", "POSSIBLY_RELOCATED", "NEEDS_REVIEW"} <= set(cats(recs))
    for r in recs:
        assert r.explanation and r.match_method and r.category
        if r.category != "ADDED":
            assert r.old is not None and r.old.text and r.old.page >= 1
        if r.category != "REMOVED":
            assert r.new is not None and r.new.text and r.new.page >= 1
        for u in (r.old, r.new):
            if u is not None and u.text[:1].isdigit():
                assert u.section_id  # numbered clause => section evidence
        if r.old is not None and r.new is not None:
            assert r.confidence is not None and 0.0 <= r.confidence <= 1.0
        if r.category == "NEEDS_REVIEW":
            assert r.review_status == "pending" and r.inline_diff is not None
        if r.category in ("MODIFIED",):
            assert r.inline_diff is not None


# ---------------------------------------------------------------------------------------------------------------
# Shared template frame with different subject matter; section bonus scaled by distinctive-term share.
# ---------------------------------------------------------------------------------------------------------------
FRAME_OLD = "1.3 Banks shall submit a report on cyber security incidents to the regulator by the end of every quarter."
FRAME_NEW = "1.3 Banks shall submit a report on unclaimed deposits to the regulator by the end of every year."


def test_shared_frame_with_different_subject_is_not_confident_modified():
    for old, new in ((around(U(FRAME_OLD)), around(U(FRAME_NEW))), (doc(FRAME_OLD[4:]), doc(FRAME_NEW[4:]))):
        r = non_unchanged(compare_units(old, new)[0])
        assert "MODIFIED" not in cats(r), r
        assert any(x.category == "NEEDS_REVIEW" and "shared_frame_only" in x.flags and x.review_status == "pending" for x in r)


@pytest.mark.parametrize("edit", [("quarter", "half-year"), ("30", "15"), ("shall", "may"), ("shall submit", "shall not submit"),
                                  ("cyber security incidents", "cyber security breaches")])
def test_localized_edits_in_a_long_frame_stay_confident_modified(edit):
    old = FRAME_OLD.replace("every quarter", "every quarter within 30 days")
    r = non_unchanged(compare_units(around(U(old)), around(U(old.replace(*edit, 1))))[0])
    assert cats(r) == ["MODIFIED"] and r[0].confidence >= 0.75 and "shared_frame_only" not in r[0].flags


def test_same_slot_template_clause_is_removed_plus_added_not_review():
    # Many clauses share the template "... shall X and keep a copy for N years"; slot 1.5 holds a different provision
    # in each version. The equal clause number alone used to lift the pair into NEEDS_REVIEW; the bonus is now scaled
    # by how much of the shared wording is distinctive (here: almost only template words).
    frame = [("bank", "verify the identity of each applicant", 3), ("custodian", "segregate client funds", 5),
             ("lender", "disclose all fees to borrowers", 7), ("branch", "train its staff on complaints", 9)]
    head = [U(f"1.{k} The {s} shall {v} and keep a copy for {n} years.") for k, (s, v, n) in enumerate(frame, 1)]
    old = head + [U("1.5 The reporting entity shall review the risk rating of dormant accounts and keep a copy for 22 years.")]
    new = [U(u.text) for u in head] + [U("1.5 The bank shall report suspicious activity concerning linked accounts and keep a copy for 69 years.")]
    r = non_unchanged(compare_units(old, new)[0])
    assert sorted(cats(r)) == ["ADDED", "REMOVED"], [(x.category, x.confidence) for x in r]
    # ... while a heavy rewording that keeps the provision-specific words (identity, onboarding, institution) in the
    # same slot of the same templated document still reaches NEEDS_REVIEW (cf. tests/test_api.py::test_review_workflow).
    old = head + [U("1.5 The institution shall verify the identity of every customer before onboarding them to any service offered.")]
    new = [U(u.text) for u in head] + [U("1.5 Before onboarding, each client must have their identity verified by the institution, using approved documents only.")]
    r = non_unchanged(compare_units(old, new)[0])
    assert cats(r) == ["NEEDS_REVIEW"] and r[0].review_status == "pending"
