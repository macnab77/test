from parse import (
    Transaction, categorize, dedupe, parse_amount, parse_paypal_csv, parse_statement_text,
)


def test_parse_amount_variants():
    assert parse_amount("-1.234,56") == -1234.56
    assert parse_amount("1.234,56-") == -1234.56
    assert parse_amount("+12,00") == 12.0
    assert parse_amount("5,00 S") == -5.0
    assert parse_amount("5,00 H") == 5.0
    assert parse_amount("23,45 EUR") == 23.45
    assert parse_amount("keine Zahl") is None


def test_sparkasse_style_with_two_dates_and_continuation():
    text = """Kontoauszug 3/2026
Buchungstag Wert Vorgang Betrag
01.03.2026 01.03.2026 Lastschrift REWE Markt GmbH -23,45
Einkauf vom 28.02.
03.03.2026 03.03.2026 Gutschrift Gehalt Firma XY 2.500,00
Neuer Kontostand 01.03.2026 3.456,78
"""
    txs = parse_statement_text(text, "maerz.pdf", "Giro")
    assert [(t.date, t.amount) for t in txs] == [("2026-03-01", -23.45), ("2026-03-03", 2500.0)]
    assert "REWE" in txs[0].description and "Einkauf vom" in txs[0].description
    assert "01.03.2026" not in txs[0].description


def test_balance_column_is_ignored():
    text = "15.04.2026 Kartenzahlung Lidl -12,99 1.000,01\n"
    [t] = parse_statement_text(text, "ing.pdf")
    assert t.amount == -12.99


def test_date_without_year_uses_document_year():
    text = "Auszug 2025\n07.12. Netflix 13,99-\n"
    [t] = parse_statement_text(text, "x.png")
    assert t.date == "2025-12-07" and t.amount == -13.99


def test_paypal_german_csv():
    csv_text = (
        '﻿"Datum","Uhrzeit","Zeitzone","Name","Typ","Status","Währung","Brutto","Gebühr","Netto"\n'
        '"02.05.2026","10:00:00","CEST","Spotify AB","Abonnementzahlung","Abgeschlossen","EUR","-10,99","0,00","-10,99"\n'
        '"02.05.2026","10:00:01","CEST","","Bankgutschrift auf PayPal-Konto","Abgeschlossen","EUR","10,99","0,00","10,99"\n'
        '"05.05.2026","12:00:00","CEST","Max","Zahlung erhalten","Abgeschlossen","EUR","1.050,00","0,00","1.050,00"\n'
    )
    txs = parse_paypal_csv(csv_text, "paypal.csv")
    assert [(t.date, t.amount) for t in txs] == [("2026-05-02", -10.99), ("2026-05-05", 1050.0)]
    assert txs[0].account == "PayPal" and "Spotify" in txs[0].description


def test_categorize():
    rules = {"Lebensmittel": ["rewe"], "Gehalt & Einnahmen": ["gehalt"]}
    assert categorize(Transaction("2026-01-01", -5, "REWE Markt", "x"), rules) == "Lebensmittel"
    assert categorize(Transaction("2026-01-01", 5, "Überweisung von Oma", "x"), rules) == "Gehalt & Einnahmen"
    assert categorize(Transaction("2026-01-01", -5, "Unbekannt", "x"), rules) == "Sonstiges"


def test_dedupe_across_files_but_keeps_repeats_within_file():
    a = Transaction("2026-01-01", -3.5, "Bäcker", "jan.pdf").finalize()
    b = Transaction("2026-01-01", -3.5, "Bäcker", "jan.pdf").finalize()
    c = Transaction("2026-01-01", -3.5, "Bäcker", "screenshot.png").finalize()
    assert len(dedupe([a, b, c])) == 2


def test_paypal_debit_on_bank_is_transfer_but_not_inside_paypal():
    rules = {"Umbuchung": ["paypal"], "Abos & Streaming": ["spotify"]}
    bank = Transaction("2026-01-01", -10.99, "Lastschrift PayPal Europe Spotify", "jan.pdf", "Giro")
    pp = Transaction("2026-01-01", -10.99, "Spotify AB via PayPal", "pp.csv", "PayPal")
    assert categorize(bank, rules) == "Umbuchung"
    assert categorize(pp, rules) == "Abos & Streaming"


# --- Bankspezifische Formate (nachgebaute Beispiele) -------------------------

def test_trade_republic_sign_from_balance_and_split_year():
    text = """TRADE REPUBLIC BANK GMBH
UMSATZÜBERSICHT
DATUM TYP BESCHREIBUNG ZAHLUNGSEINGANG ZAHLUNGSAUSGANG SALDO
Anfangssaldo 1.000,00 €
01 Okt. Kartentransaktion REWE Markt 23,45 € 976,55 €
2024
02 Okt. Überweisung Einzahlung von Max Mustermann 500,00 € 1.476,55 €
2024
05 Okt. Handel Sparplan iShares Core MSCI World 100,00 € 1.376,55 €
2024
"""
    txs = parse_statement_text(text, "tr.pdf", "Trade Republic", "traderepublic")
    assert [(t.date, t.amount) for t in txs] == [
        ("2024-10-01", -23.45), ("2024-10-02", 500.0), ("2024-10-05", -100.0)]
    assert txs[0].description == "Kartentransaktion REWE Markt"


def test_trade_republic_first_row_without_start_balance_uses_keywords():
    text = "01 Nov. 2024 Zinszahlung Ihre Zinsen 3,12 € 1.003,12 €\n03 Nov. 2024 Kartentransaktion Aldi 10,00 € 993,12 €\n"
    txs = parse_statement_text(text, "tr.pdf", "Trade Republic", "traderepublic")
    assert [t.amount for t in txs] == [3.12, -10.0]


def test_postbank_date_with_value_date_and_spaced_sign():
    text = """Postbank Kontoauszug 2026
Buchung/Wert Vorgang/Buchungsinformation Soll Haben
02.03./02.03. Lastschrift Stadtwerke Strom - 64,00
Referenz 123
03.03./03.03. Gutschrift Gehalt Firma XY + 2.500,00
"""
    txs = parse_statement_text(text, "pb.pdf", "Postbank", "bank")
    assert [(t.date, t.amount) for t in txs] == [("2026-03-02", -64.0), ("2026-03-03", 2500.0)]
    assert txs[0].description.startswith("Lastschrift Stadtwerke")


def test_credit_card_unsigned_amounts_are_expenses():
    text = """easybank Kreditkartenabrechnung März 2026
12.03.26 13.03.26 AMAZON EU SARL 45,90
14.03.26 15.03.26 BILLA DANKT 12,30
20.03.26 20.03.26 Gutschrift AMAZON 45,90-
28.03.26 28.03.26 Zahlungseingang - Danke 58,20
"""
    txs = parse_statement_text(text, "eb.pdf", "easybank Kreditkarte", "creditcard")
    assert [t.amount for t in txs] == [-45.90, -12.30, 45.90, 58.20]


def test_credit_card_with_minus_on_purchases_keeps_bank_semantics():
    text = "12.03.2026 AMAZON -45,90\n13.03.2026 BILLA -12,30\n20.03.2026 Gutschrift 5,00\n"
    txs = parse_statement_text(text, "eb.pdf", "easybank Kreditkarte", "creditcard")
    assert [t.amount for t in txs] == [-45.90, -12.30, 5.0]


def test_paypal_app_screenshot():
    from datetime import date
    from parse import parse_paypal_screenshot
    text = """Aktivitäten
Spotify AB -10,99 €
2. Mai · Automatische Zahlung
Max Mustermann +50,00 €
28. Apr. · Geld erhalten
Von Bankkonto +10,99 €
2. Mai · Bankgutschrift
Zalando SE -79,95 €
15. Dez. · Zahlung
"""
    txs = parse_paypal_screenshot(text, "IMG_1.png", date(2026, 5, 10))
    assert [(t.date, t.amount) for t in txs] == [
        ("2026-05-02", -10.99), ("2026-04-28", 50.0), ("2025-12-15", -79.95)]
    assert txs[0].account == "PayPal" and txs[0].description.startswith("Spotify AB")


def test_transfers_between_own_accounts():
    import json
    from pathlib import Path
    rules = json.loads((Path(__file__).parent / "categories.json").read_text())
    def cat(desc, acc, amount=-10.0):
        return categorize(Transaction("2026-01-01", amount, desc, "x", acc), rules)
    assert cat("Lastschrift PayPal Europe", "Postbank") == "Umbuchung"
    assert cat("Überweisung Trade Republic Bank", "Postbank") == "Umbuchung"
    assert cat("easybank Kreditkartenabrechnung", "Postbank") == "Umbuchung"
    assert cat("Zahlungseingang - Danke", "easybank Kreditkarte", 58.2) == "Umbuchung"
    assert cat("Spotify AB · Automatische Zahlung", "PayPal") == "Abos & Streaming"
    assert cat("Handel Sparplan iShares", "Trade Republic") == "Sparen & Investieren"
    assert cat("Kartentransaktion REWE Markt", "Trade Republic") == "Lebensmittel"
