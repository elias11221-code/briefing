# Morning Briefing – iPhone-App

Jeden Morgen gegen 07:00 holt GitHub automatisch deine Garmin-Daten und die Wind-, Wellen- und Strömungsvorhersage für Vilanova. Daraus wird die Seite gebaut. Das geht ohne Laptop und ohne Claude, du öffnest sie einfach auf dem iPhone.

- **Daten sind verschlüsselt.** Das Projekt ist öffentlich (sonst kostet GitHub Pages Geld), aber deine Gesundheitsdaten liegen nur verschlüsselt darin (AES-256). Lesen kann sie nur, wer deine Passphrase kennt.
- **Der Garmin-Login liegt als GitHub-Secret.** Niemand kann ihn sehen, auch nicht in den Logs.

---

## Einrichtung (einmalig, ca. 20 Min., am Laptop)

### 1. GitHub-Konto
Leg auf https://github.com ein kostenloses Konto an, falls du noch keins hast.

### 2. Repository anlegen und Dateien hochladen
1. Oben rechts **+** → **New repository**
2. Name: `briefing`, Sichtbarkeit: **Public**, dann **Create repository**
3. Auf der leeren Seite auf **uploading an existing file** klicken
4. Den **gesamten Inhalt** des entpackten Ordners hineinziehen, also `briefing.py`, `schedule.json`, `README.md`, `.gitignore` und die Ordner `docs` und `.github`. Wichtig ist der Ordner `.github`, denn darin steckt der tägliche Zeitplan.
5. **Commit changes**

### 3. Zwei Secrets eintragen
Im Repository: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

**Secret 1: `GARMIN_TOKENS`**
In PowerShell eingeben:
```powershell
Get-Content $HOME\.garminconnect\garmin_tokens.json -Raw | Set-Clipboard
```
Damit ist der Inhalt in der Zwischenablage. Ins Feld **Secret** einfügen und speichern.

**Secret 2: `DASHBOARD_PASSPHRASE`**
Eine eigene Passphrase mit mindestens 8 Zeichen, z. B. drei Wörter. **Nicht** dein Garmin-Passwort. Mit ihr entsperrst du die Seite auf dem iPhone.

### 4. Seite einschalten
**Settings** → **Pages** → Source: **Deploy from a branch** → Branch: `main`, Ordner: `/docs` → **Save**

### 5. Ersten Durchlauf starten
**Actions** (oben) → falls nötig *"I understand my workflows, enable them"* → links **Morning Briefing** → **Run workflow** → **Run workflow**.
Nach 1–2 Minuten sollte ein grüner Haken erscheinen. Klick auf den Lauf → *Build encrypted briefing*. Dort sollte stehen:
```
Garmin: ok
Wind: ok | Marine: ok
```

### 6. Aufs iPhone
1. In **Safari** öffnen: `https://DEIN-GITHUB-NAME.github.io/briefing/`
2. Passphrase eingeben. Das iPhone merkt sie sich.
3. Teilen-Symbol → **Zum Home-Bildschirm**

Fertig: Ab jetzt aktualisiert sich die App jeden Morgen von selbst.

---

## Wenn etwas nicht klappt

| Problem | Lösung |
|---|---|
| `Garmin: FAILED` im Log | Der Garmin-Login ist abgelaufen (nach ca. 6–12 Monaten). Am Laptop erneut `uvx --python 3.12 --from git+https://github.com/Taxuspt/garmin_mcp python -m garmin_mcp.auth_cli` ausführen und das Secret `GARMIN_TOKENS` mit Schritt 3 neu setzen. Bleibt es trotzdem FAILED, blockiert Garmin evtl. GitHub-Server. Dann Claude fragen. |
| Push-Fehler (403) im Schritt *Publish* | **Settings** → **Actions** → **General** → *Workflow permissions* → **Read and write permissions** → Save |
| Gelber Hinweis "Today's update hasn't run yet" | GitHub startet geplante Läufe manchmal 5–30 Min. zu spät. Später neu laden oder in **Actions** manuell **Run workflow** drücken. |
| Seite zeigt "No briefing yet" | Schritt 5 wurde noch nicht erfolgreich ausgeführt. |
| Stundenplan ändert sich | `schedule.json` auf GitHub öffnen → Stift-Symbol → bearbeiten → Commit. |

**Uhrzeit:** Der Lauf ist auf 04:50 UTC gestellt, also etwa 07:00 im Sommer und 06:00 im Winter. Ändern kannst du das in `.github/workflows/briefing.yml`.
