# Finanzen – Einnahmen & Ausgaben auf dem iPhone

Zwei Teile:

| Teil | Wo läuft es? | Was macht es? |
|---|---|---|
| `importer/` | auf deinem Mac/PC | liest den Ordner mit Kontoauszügen (PDF), Screenshots und PayPal-Exporten und erzeugt `transactions.json` |
| `app/` | auf dem iPhone (Web-App) | zeigt Einnahmen, Ausgaben, Saldo, 12-Monats-Verlauf und Ausgaben nach Kategorie; Buchungen bearbeiten und manuell hinzufügen |

**Datenschutz:** Deine Finanzdaten landen nie im Repository oder auf einem Server.
Die App speichert alles nur lokal auf dem iPhone. `.gitignore` schließt PDFs, Bilder
und `transactions.*` aus.

---

## 1. Auszüge einlesen (Mac/PC)

```bash
cd importer
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Für Screenshots zusätzlich Tesseract (OCR) installieren:
#   macOS:   brew install tesseract tesseract-lang
#   Windows: https://github.com/UB-Mannheim/tesseract/wiki  (Deutsch mit auswählen)

python3 import_statements.py ~/Pfad/zum/Ordner -o transactions.json
```

Ausgabe pro Datei, z. B.:

```
    23 Buchungen  Kontoauszug_2026_03.pdf
    41 Buchungen  paypal_2026.csv
     0 Buchungen  IMG_1234.png   <- nichts erkannt, mit --debug prüfen
```

Zusätzlich entsteht `transactions.csv` (Excel/Numbers) zum Prüfen.

### Tipps

- **PayPal:** Statt Screenshots lieber den offiziellen Export nehmen:
  paypal.com → *Aktivitäten* → *Kontoauszüge* → *Benutzerdefiniert* → **CSV**.
  Das ist exakt und braucht keine Texterkennung.
- **Doppelte Buchungen:** Ein Umsatz, der als PDF *und* als Screenshot vorliegt, wird nur einmal gezählt.
- **PayPal-Abbuchungen auf dem Girokonto** bekommen die Kategorie *Umbuchung* und zählen nicht als Ausgabe,
  weil der eigentliche Einkauf schon in den PayPal-Daten steht.
- **Kategorien** stehen in `importer/categories.json` (Kategorie → Suchbegriffe, erste passende gewinnt).
- **Nichts erkannt?** `--debug` zeigt den gelesenen Text. Jede Bank formatiert anders – mit dem
  Rohtext lässt sich der Parser in `parse.py` an deine Bank anpassen.

## 2. App aufs iPhone

Die App ist eine *Progressive Web App*: kein App Store, kein Xcode nötig.

1. **Veröffentlichen** (einmalig): Auf GitHub unter *Settings → Pages → Source* **GitHub Actions** wählen
   und den Branch nach `main` mergen. Der Workflow `.github/workflows/pages.yml` stellt den Ordner `app/`
   unter `https://<benutzer>.github.io/<repo>/` bereit.
   *(Bei privaten Repos braucht GitHub Pages einen bezahlten Plan. Alternative: den Ordner `app/`
   kostenlos per Drag & Drop bei Netlify Drop oder Cloudflare Pages hochladen.)*
2. Die URL in **Safari** auf dem iPhone öffnen → Teilen-Symbol → **Zum Home-Bildschirm**.
3. `transactions.json` per AirDrop oder iCloud Drive aufs iPhone legen.
4. In der App: **Import → Datei wählen**.

Neue Monate einfach erneut importieren: Bereits vorhandene Buchungen werden übersprungen,
deine Änderungen an Kategorien bleiben erhalten.

### Funktionen

- Monats- und Jahresansicht mit Einnahmen, Ausgaben, Saldo und Sparquote
- 12-Monats-Verlauf (antippen zeigt Details und springt zum Monat)
- Ausgaben nach Kategorie → antippen öffnet die passenden Buchungen
- Suche und Filter, Buchungen bearbeiten, löschen und manuell hinzufügen
- „Kategorie für ähnliche Buchungen übernehmen“ merkt sich die Zuordnung auch für künftige Importe
- Backup-Export als JSON, funktioniert offline, Hell- und Dunkelmodus

### Lokal ausprobieren

```bash
cd app && python3 -m http.server 8000
# -> http://localhost:8000
```

## Tests

```bash
cd importer && python3 -m pytest -q
```
