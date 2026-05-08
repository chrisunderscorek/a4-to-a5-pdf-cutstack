# A4-zu-A5 PDF Cut-Stack

English version: [README_en.md](README_en.md)

Kleines Python-Skript, um A4-Dokumentation in ein A5-Drucklayout zu
verwandeln.

Das Skript erzeugt eine A4-Querformat-PDF mit zwei A5-Seiten pro Blatt,
vorbereitet fuer beidseitigen Cut-Stack-Druck. Nach dem Drucken wird der
A4-Stapel in der Mitte geschnitten und der rechte A5-Stapel hinter den linken
A5-Stapel gelegt.

## Signaturen

Das Skript erkennt PDF-Signaturfelder und rendert diese Seiten vor der
Imposition. Dadurch bleiben sichtbare Unterschriften fuer den Druck erhalten.

Die gerenderten Signaturseiten-Ausgaben sind visuell identisch mit den vorher
manuell korrigierten Signatur-Ausgaben.

Wichtig: Das Skript erhaelt die Unterschriften sichtbar fuer den Druck, aber
eine kryptografische PDF-Signatur-Gueltigkeit bleibt nach Rendering/Imposition
nicht erhalten. Fuer das Druckziel ist das normalerweise genau die gewuenschte
Vorgehensweise.

## Installation

Lokale Python-Umgebung anlegen und PyMuPDF installieren:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Poppler ist fuer die beste Signaturdarstellung empfohlen, weil das Skript
`pdftoppm` nutzt, wenn es auf `PATH` verfuegbar ist. Wenn Poppler nicht
installiert ist, faellt das Skript auf PyMuPDF-Rendering zurueck.

## Verwendung

```sh
.venv/bin/python pdf_a5_cutstack_auto_signature_flatten_v2.py input.pdf output.pdf
```

Beispiel:

```sh
.venv/bin/python pdf_a5_cutstack_auto_signature_flatten_v2.py manual.pdf out/manual_cutstack.pdf
```

## Drucken

Die erzeugte PDF wie vorher drucken:

- Papier: A4
- Ausrichtung: Querformat
- Skalierung: 100% / Tatsaechliche Groesse
- Beidseitig: aktiviert
- Bindung: meistens kurze Kante spiegeln
- In Acrobat nicht noch einmal "Mehrere" oder "Broschuere" auswaehlen

Nach dem Drucken den kompletten A4-Stapel in der Mitte schneiden und den rechten
A5-Stapel hinter den linken A5-Stapel legen.
