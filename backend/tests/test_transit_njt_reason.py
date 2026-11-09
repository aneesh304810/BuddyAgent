"""NJ Transit's own reason for refusing a sign-in reaches the user and the key test, never a token."""

from buddy import keytest, transit


def test_reason_hides_tokens_and_keeps_the_message():
    said = transit.njt_reason({"Authenticated": "False", "UserToken": "secret", "errorMessage": "API not approved"})
    assert said == "API not approved" and "secret" not in said
    assert "secret" not in transit.njt_reason({"Authenticated": "False", "UserToken": "secret"})


def test_key_test_signs_in_to_both_apis(monkeypatch):
    answers = iter([(200, '{"Authenticated": "True", "UserToken": "abc"}'),
                    (200, '{"Authenticated": "False", "UserToken": "", "errorMessage": "Invalid user"}')])
    monkeypatch.setattr(keytest, "_send", lambda req, timeout, limit=20000: next(answers))
    rows = keytest._njt({"BUDDY_NJT_USERNAME": "me", "BUDDY_NJT_PASSWORD": "pw"})
    assert rows[0].startswith("  OK    njt rail")
    assert "FAIL  njt bus/lr" in rows[1] and "Invalid user" in rows[1] and "abc" not in "".join(rows)
    assert "no login" in keytest._njt({})[0]


def test_key_test_names_the_wrong_username(monkeypatch):
    answers = iter([(500, '{"errorMessage": "Missing user account."}'), (200, '{"Authenticated": "False"}')])
    monkeypatch.setattr(keytest, "_send", lambda req, timeout, limit=20000: next(answers))
    rows = keytest._njt({"BUDDY_NJT_USERNAME": "me", "BUDDY_NJT_PASSWORD": "pw"})
    assert "doesn't know this username" in rows[0] and "BUDDY_NJT_USERNAME" in rows[0]
