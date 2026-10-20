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


def test_us_ids():
    out = mask("My SSN is 123-45-6789 and routing 021000021")
    assert "123-45-6789" not in out and "021000021" not in out
    assert "6789" not in mask("social security number 123456789")


def test_south_american_ids():
    cases = {
        "CPF 123.456.789-09": "123.456.789-09",
        "CNPJ 12.345.678/0001-95": "12.345.678/0001-95",
        "mi RUT es 12.345.678-K": "12.345.678-K",
        "CUIT 20-12345678-3": "20-12345678-3",
        "DNI 12.345.678": "12.345.678",
        "cédula 1020304050": "1020304050",
        "passport X1234567": "X1234567",
    }
    for text, secret in cases.items():
        assert secret not in mask(text), text


def test_spanish_portuguese_codes_and_passwords():
    for text, secret in [("su código de verificación es 482913", "482913"),
                         ("o código é 482913", "482913"),
                         ("mi contraseña: Sol2026!", "Sol2026!"),
                         ("minha senha é Praia99", "Praia99")]:
        assert secret not in mask(text), text


def test_money_amounts_untouched():
    for text in ("Paid R$ 1.234,56 at the market", "Cost was $1,250.00", "ARS 45.000 for the taxi",
                 "Flight LA 2470 departs 14:35", "Call me at +1 415 555 0134"):
        assert mask(text) == text, text


def test_codes_in_notifications():
    assert "482913" not in mask("Can you join now? Code 482913 is OTP")
    assert "482913" not in mask("482913 is your verification code")
    assert "7731" not in mask("Use 7731 as your one time password")
    assert "8812" not in mask("Seu código: 8812")
    # Not secrets: addresses and phone prefixes stay.
    assert mask("Ship to zip code 10001") == "Ship to zip code 10001"
    assert mask("Call area code 212 first") == "Call area code 212 first"
