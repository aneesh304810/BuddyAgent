

def test_the_inventory_says_what_is_set_and_what_is_missing_without_showing_a_key():
    from buddy import gateway, keytest
    env = {"ANTHROPIC_API_KEY": "sk-secret-value", "BUDDY_PLIVO_AUTH_ID": "id-only"}
    text = "\n".join(keytest.inventory(env))
    assert "sk-secret-value" not in text and "id-only" not in text  # values are never printed
    assert "set            Claude (Anthropic)" in text
    assert "MISSING BUDDY_PLIVO_AUTH_TOKEN" in text  # a service with several keys needs all of them
    assert "MISSING BUDDY_TOKEN  (required)" in text and "no key needed  Open-Meteo (weather)" in text
    assert "BUDDY_SEC_CONTACT is not a key" in text
    for entry in gateway.CATALOG.values():  # every catalogue service is listed
        assert entry["title"] in text
