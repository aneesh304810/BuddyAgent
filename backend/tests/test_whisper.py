"""Whisper on the server: stays in the user's languages and drops text it made up."""

from types import SimpleNamespace as S

from buddy import whisper


def seg(text, no_speech=0.1, logprob=-0.3, ratio=1.2):
    return S(text=text, no_speech_prob=no_speech, avg_logprob=logprob, compression_ratio=ratio)


def test_real_speech_is_kept():
    assert whisper._clean([seg(" Hey Buddy,"), seg(" are you listening?")]) == "Hey Buddy, are you listening?"


def test_one_word_over_and_over_is_dropped():
    assert whisper._clean([seg(" " + "Bro, " * 40)]) == ""
    assert whisper._clean([seg(" Bro, can you check my email, bro?")]) != ""


def test_silence_and_garbled_segments_are_dropped():
    assert whisper._clean([seg(" Thanks for watching!", no_speech=0.9, logprob=-1.2)]) == ""
    assert whisper._clean([seg(" la la la la la la la", ratio=3.1)]) == ""
    assert whisper._clean([seg(" L'entremio è una vera.", logprob=-1.8)]) == ""


def test_language_stays_within_the_chosen_ones():
    info = S(language="it", language_probability=0.4, all_language_probs=[("it", 0.4), ("ml", 0.3), ("en", 0.2)])
    assert whisper._pick_language(info, ["ml", "en"]) == "ml"
    assert whisper._pick_language(S(language="en", language_probability=0.9, all_language_probs=[]), ["ml", "en"]) is None


def test_unsure_guess_falls_back_to_known_languages_but_a_confident_one_stays():
    unsure = S(language="it", language_probability=0.3, all_language_probs=[("it", 0.3), ("en", 0.25), ("ml", 0.2)])
    assert whisper._pick_language(unsure, None) == "en"
    sure = S(language="fr", language_probability=0.95, all_language_probs=[("fr", 0.95)])
    assert whisper._pick_language(sure, None) is None  # a French speaker in the room: Listen-in's gist
