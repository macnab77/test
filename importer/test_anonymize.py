import re

from anonymize import Anonymizer


def make():
    a = Anonymizer(seed=7)
    a.add_name("Jonas Beispielkunde")
    a.add_street("Lindenallee 42a")
    a.add_city("50667 Köln")
    return a


def iban_valid(iban: str) -> bool:
    s = iban.replace(" ", "")
    return int("".join(str(int(c, 36)) for c in s[4:] + s[:4])) % 97 == 1


def test_replaces_personal_and_detected_values_but_keeps_amounts_and_dates():
    a = make()
    text = ("Herrn Jonas Beispielkunde, Lindenallee 42a, 50667 Köln\n"
            "IBAN DE89 3704 0044 0532 0130 00 Kundennummer: 4711081512\n"
            "02.03./02.03. Lastschrift Stadtwerke - 1.264,00\n"
            "Karte 4111 **** **** 1234  E-Mail jonas@mail.de  Tel +49 221 1234567\n"
            "Gläubiger-ID DE98ZZZ09999999999 Mandatsref. MX-99812734\n"
            "BEISPIELKUNDE, JONAS\n")
    out = a.replace(text)
    for secret in ("Jonas", "Beispielkunde", "Lindenallee", "50667", "3704 0044", "4711081512",
                   "1234567", "jonas@mail.de", "ZZZ09999999999", "99812734", "4111"):
        assert secret.lower() not in out.lower(), secret
    assert "02.03./02.03. Lastschrift Stadtwerke - 1.264,00" in out
    assert "Max Mustermann" in out and "MUSTERMANN, MAX" in out
    assert a.leaks(out) == []
    fake_iban = re.search(r"IBAN (DE[\d ]+?) Kunden", out).group(1)
    assert len(fake_iban) == len("DE89 3704 0044 0532 0130 00") and iban_valid(fake_iban)


def test_same_value_gets_same_replacement_everywhere():
    a = make()
    one = a.replace("IBAN DE89370400440532013000")
    two = a.replace("an DE89 3704 0044 0532 0130 00 überwiesen")
    assert one.split()[1] == "".join(two.split()[1:-1])


def test_isin_and_short_numbers_untouched():
    a = make()
    text = "05 Okt. 2024 Handel Sparplan IE00B4L5Y983 100,00 € 1.376,55 € Rechnung 2024"
    assert a.replace(text) == text


def test_leak_detection():
    a = make()
    assert "Beispielkunde" in a.leaks("Hallo Jonas Beispielkunde")
    assert a.leaks("Hallo Max Mustermann") == []
