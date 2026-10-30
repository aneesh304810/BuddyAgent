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


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}

    def json(self):
        return self._body


class FakeHTTP:
    def __init__(self, response):
        self.response, self.sent = response, {}

    async def post(self, url, data, headers, files):
        self.sent = {"url": url, "data": data, "file": files["file"]}
        return self.response


def groq_env(monkeypatch):
    monkeypatch.setenv("BUDDY_GROQ_KEY", "gsk_test")
    monkeypatch.delenv("BUDDY_WHISPER_TALK", raising=False)
    whisper._groq_resting_until = 0.0


def test_groq_hears_talk_mode_and_keeps_to_my_languages(monkeypatch):
    import asyncio

    groq_env(monkeypatch)
    http = FakeHTTP(FakeResponse(body={"language": "malayalam", "text": " എന്താ വിശേഷം?", "segments": [
        {"text": " എന്താ വിശേഷം?", "no_speech_prob": 0.05, "avg_logprob": -0.2, "compression_ratio": 1.1}]}))
    text, lang = asyncio.run(whisper._groq(b"\x00\x00" * 16000, None, "Names: Priya.", ["ml", "en"], http=http))
    assert (text, lang) == ("എന്താ വിശേഷം?", "ml")
    assert http.sent["url"] == whisper.GROQ_URL and http.sent["data"]["model"] == "whisper-large-v3-turbo"
    assert http.sent["file"][1][:4] == b"RIFF"  # a real WAV file
    italian = FakeHTTP(FakeResponse(body={"language": "italian", "text": " L'entremio è una vera."}))
    try:
        asyncio.run(whisper._groq(b"\x00\x00", None, "", ["ml", "en"], http=italian))
        raise AssertionError("should hand over to the local model")
    except whisper.GroqSkipped:
        pass


def test_groq_limit_rests_and_hands_over(monkeypatch):
    import asyncio

    groq_env(monkeypatch)
    http = FakeHTTP(FakeResponse(429, headers={"retry-after": "30"}))
    for _ in range(2):  # the second time it doesn't even ask
        try:
            asyncio.run(whisper._groq(b"\x00\x00", None, "", None, http=http))
        except whisper.GroqSkipped as e:
            reason = str(e)
    assert "resting after its free limit" in reason
    assert 25 <= whisper.groq_limit_seconds() <= 30  # the phone switches to its own recognizer meanwhile


def test_only_talk_mode_goes_to_groq_and_failures_fall_back(monkeypatch):
    import asyncio

    groq_env(monkeypatch)
    used = []

    async def fake_groq(pcm, hint, prompt, allowed, http=None):
        used.append("groq")
        return "hello", "en"
    monkeypatch.setattr(whisper, "_groq", fake_groq)
    monkeypatch.setattr(whisper, "_transcribe", lambda *a: (used.append("local"), ("hi", "en"))[1])
    meta = {}
    assert asyncio.run(whisper.transcribe(b"\x00\x00", "en-US", mode="talk", meta=meta)) == ("hello", "en")
    assert meta["engine"] == "groq"
    asyncio.run(whisper.transcribe(b"\x00\x00", "auto", mode="listen", meta=meta))  # room talk: never Groq
    assert used == ["groq", "local"] and meta["engine"] == "local"

    async def busy(*a, **k):
        raise whisper.GroqSkipped("at its free limit")
    monkeypatch.setattr(whisper, "_groq", busy)
    meta = {}
    assert asyncio.run(whisper.transcribe(b"\x00\x00", "en-US", mode="talk", meta=meta)) == ("hi", "en")
    assert meta == {"groq_skipped": "at its free limit", "engine": "local"}
    monkeypatch.setenv("BUDDY_WHISPER_TALK", "local")
    assert not whisper.groq_on()


def test_groq_is_free_unless_paid(monkeypatch):
    monkeypatch.delenv("BUDDY_GROQ_PAID", raising=False)
    assert whisper.groq_cost(3) == 0.0
    monkeypatch.setenv("BUDDY_GROQ_PAID", "1")
    assert abs(whisper.groq_cost(3) - 10 / 3600 * 0.04) < 1e-9  # billed as 10 s at least


def test_its_own_prompt_read_back_is_dropped():
    prompt = "Aneesh talking to Buddy. Names: Bro, Gmail, Kochi, India."
    assert whisper._clean([seg(" Names, Bro's, Gmail, Kochi, India.")], prompt) == ""
    assert whisper._clean([seg(" Can you check Gmail for my Kochi flight?")], prompt) != ""


def test_other_languages_forced_through_english_are_dropped():
    assert whisper._wrong_script("Enný, sannig að eftir að sína.", "en")
    assert not whisper._wrong_script("What are the latest news in New York?", "en")
    assert not whisper._wrong_script("Café near Kochi?", "en")
    assert not whisper._wrong_script("എന്താ വിശേഷം?", "ml")


def test_stock_video_phrases_are_dropped():
    assert whisper._clean([seg(" Thank you for watching.")]) == ""
    assert whisper._clean([seg(" Please subscribe to my channel!")]) == ""
    assert whisper._clean([seg(" Thank you.")]) == "Thank you."  # people do say that


def test_short_repeats_are_dropped():
    assert whisper._clean([seg(" Bero, Bero, Bero.")]) == ""
    assert whisper._clean([seg(" Bla, bla, bla, bla.")]) == ""
