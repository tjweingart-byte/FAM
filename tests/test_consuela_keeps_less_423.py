"""Consuela keeps the key sentences only, each one that stands alone (§423)."""
from __future__ import annotations

from agents import housekeeper as hk
from config import settings


def test_she_is_told_to_keep_at_most_a_dozen_key_sentences():
    assert hk.MAX_KEPT == 12
    assert "At most 12 sentences an article" in hk.INSTRUCTIONS
    assert "fewer when fewer will do" in hk.INSTRUCTIONS


def test_she_is_told_to_keep_only_what_stands_alone_and_quotes_with_a_speaker():
    text = " ".join(hk.INSTRUCTIONS.split())
    assert "makes sense read alone" in text
    assert '"We had a really hard start."' in text
    assert "A quote is kept only in a sentence that names its speaker" in text


def test_the_old_rules_are_all_still_there():
    text = " ".join(hk.INSTRUCTIONS.split())
    for kept in ("Answer with sentence numbers only", "never a summary",
                 "**happened**", "**expected**", "Answer for every article."):
        assert kept in text


def test_room_for_a_batch_of_eight_answers():
    # Measured (§423): 3 articles used up to 1,925 output tokens with these
    # instructions; 1,500 cut batches off ("out_of_room").
    assert settings.housekeeper_max_tokens >= 6000
    assert settings.housekeeper_batch_articles <= 8
