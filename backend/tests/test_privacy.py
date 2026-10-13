from buddy.privacy import mask


def test_card_keeps_last_four():
    out = mask("Paid with 4111 1111 1111 1111 yesterday")
    assert "4111 1111" not in out and "card ****1111" in out


def test_non_luhn_long_number_is_an_account():
    out = mask("Transfer to A/c 50100234567812 done")
    assert "50100234567812" not in out and "account ****7812" in out


def test_aadhaar_pan_otp_cvv_password():
    text = ("Aadhaar 2345 6789 0123, PAN ABCDE1234F, your OTP is 482913, "
            "CVV: 123, password is hunter2")
    out = mask(text)
    for secret in ("2345 6789 0123", "ABCDE1234F", "482913", "123,", "hunter2"):
        assert secret not in out, secret
    assert "[Aadhaar hidden]" in out and "[PAN hidden]" in out


def test_ordinary_text_untouched():
    text = "Spent 450 rupees on petrol at 6:30 on 12/10/2026, flight AI 503"
    assert mask(text) == text



def test_otp_phrasings():
    for text in ("OTP is 482913", "your one-time password: 482913", "verification code 482913"):
        assert "482913" not in mask(text), text


def test_postal_pin_code_kept():
    assert mask("Kochi, PIN code 682001") == "Kochi, PIN code 682001"
