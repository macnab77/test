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
