"""Every tunable constant must be justified. The owner's condition on SPEC 7."""

from afc import config

# Spec text and enumerations, not tuning knobs: these carry no "why this value".
EXEMPT = {
    "VERIFIED", "EXPLAINED", "AWAITING_BANK", "HUMAN_REVIEW", "UNRESOLVED",
    "STATES", "STATE_SEVERITY", "CHECK_NAMES", "JUSTIFICATIONS",
    "SPEC9_SENTENCES", "ADDENDUM_SENTENCES", "HONESTY_SENTENCES", "HONESTY_CLAUSE",
    "HONESTY_CLAUSE_HEADLINE", "HONESTY_CLAUSE_DETAIL",
}


def tunable_constants() -> set[str]:
    return {
        name
        for name in dir(config)
        if name.isupper() and not name.startswith("_") and name not in EXEMPT
    }


def test_every_tunable_constant_has_a_one_line_justification():
    missing = tunable_constants() - set(config.JUSTIFICATIONS)
    assert not missing, (
        f"no README justification for: {sorted(missing)}. "
        "A judge asking 'why this value?' must get an answer."
    )


def test_no_justification_describes_a_constant_that_no_longer_exists():
    assert not set(config.JUSTIFICATIONS) - tunable_constants()


def test_the_five_states_are_exactly_the_spec7_table():
    assert set(config.STATES) == {
        "VERIFIED", "EXPLAINED", "AWAITING_BANK", "HUMAN_REVIEW", "UNRESOLVED"
    }
    assert len(config.STATES) == len(config.STATE_SEVERITY) == 5


def test_compound_severity_ordering_is_the_approved_one():
    ranked = sorted(config.STATE_SEVERITY, key=config.STATE_SEVERITY.get, reverse=True)
    assert ranked == ["UNRESOLVED", "HUMAN_REVIEW", "AWAITING_BANK", "EXPLAINED", "VERIFIED"]


def test_spec8_checklist_is_nine_checks_all_weighted_equally():
    assert len(config.CHECK_NAMES) == 9
    assert set(config.CHECK_WEIGHTS.values()) == {1.0}


def test_money_constants_are_integer_paise():
    for name in ("TOLERANCE_PAISE", "MATERIALITY_PAISE"):
        assert isinstance(getattr(config, name), int)
    assert config.MATERIALITY_PAISE > config.TOLERANCE_PAISE


def test_spec9_sentences_are_verbatim_and_unedited():
    assert config.SPEC9_SENTENCES == (
        "The data generator and the classifier share the same fee arithmetic, so "
        "single-cause faults are correct by construction.",
        "This score demonstrates internal coherence, not that the system would "
        "survive a real settlement file.",
    )


def test_the_clause_is_spec9_plus_the_three_approved_addendum_sentences():
    assert len(config.ADDENDUM_SENTENCES) == 3
    assert config.HONESTY_SENTENCES[:2] == config.SPEC9_SENTENCES
    assert config.HONESTY_CLAUSE == " ".join(config.HONESTY_SENTENCES)


def test_ui_split_hides_nothing_that_is_not_also_in_the_plaintext_clause():
    # UI shows headline next to the number, detail behind an expand. The two must
    # reconstitute the exact text that goes into the README and metrics_summary.json.
    assert (
        config.HONESTY_CLAUSE_HEADLINE + " " + config.HONESTY_CLAUSE_DETAIL
        == config.HONESTY_CLAUSE
    )
    assert config.HONESTY_CLAUSE_HEADLINE == config.SPEC9_SENTENCES[0]
