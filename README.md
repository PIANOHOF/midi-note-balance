# MIDI Note Balance

<img src="pianohof-logo.png" alt="PIANOHOF" width="110">

**Note-On und Note-Off im MIDI-Export prüfen.** Dieses lesende Python-Werkzeug zeigt, welche gespeicherten Notenstarts keinen einzelnen passenden Notenabschluss haben. Es öffnet keine MIDI-Ausgänge, spielt nichts ab und verändert die Eingabedatei nicht. Die Ausgabe hilft beim Untersuchen eines Exports; sie ist keine Diagnose eines hörbar hängen gebliebenen Tons.

## Start mit dem mitgelieferten Beispiel

Benötigt werden Python ab 3.10 und die in `requirements.txt` festgelegte Mido-Version. Lade die Projektdateien in einen gemeinsamen Ordner. `demo.mid` ist ein eigens erzeugtes Beispiel ohne Musikstück, Kundenaufnahme oder persönliche Metadaten.

```sh
python3 -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell stattdessen: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python midi_note_balance.py demo.mid
python midi_note_balance.py demo.mid --format json
python -m unittest -v
```

Nach der Installation arbeitet das Programm lokal und ohne Netzwerk. Der Bericht geht auf die Standardausgabe. Eine Datei lässt sich beispielsweise mit `--format json > bericht.json` speichern; als Ziel niemals die MIDI-Eingabedatei selbst verwenden. Rückgabecode 0 bedeutet, dass die Datei ausgewertet wurde, nicht dass Instrument oder Aufnahme fehlerfrei sind. Nicht lesbare oder nicht unterstützte Eingaben ergeben Code 2.

## Das Beispiel richtig lesen

Das Beispiel enthält vier Note-On-Ereignisse mit positiver Anschlagstärke. Zwei bekommen einen ausdrücklichen Abschluss. Ein weiterer Notenstart wird durch ein Note-On mit Anschlagstärke null beendet; dieses zählt hier ebenfalls als Note-Off. Bei MIDI-Notennummer 67 fehlt ein individueller Abschluss. Zusätzlich steht ein Note-Off für Nummer 72 ohne vorherigen Start in der Datei. Der JSON-Bericht nennt deshalb drei Paare, einen offenen Start und einen Abschluss ohne Start.

Vor dem Dateiende ist außerdem CC123, „All Notes Off“, gespeichert. Dieser Befehl erscheint gesondert im Bericht. Er ersetzt in dieser **Bilanz einzelner Ereignisse** keinen Note-Off. Ein Empfänger kann darauf dennoch reagieren und den Ton beenden. Aus „offener Start“ folgt deshalb ausdrücklich nicht „dieser Ton muss noch hörbar sein“. `example-report.json` enthält die vollständige erwartete Ausgabe des synthetischen Beispiels.

## Welche Ereignisse zusammengehören

Die Zuordnung verwendet logischen Port, MIDI-Kanal und Notennummer. Kanäle erscheinen für Menschen als 1 bis 16, Notennummern als 0 bis 127. Spur und Ereignis beginnen bei 1, Ticks bei 0. Es werden keine Oktavnamen vergeben: verschiedene Programme können dieselbe MIDI-Notennummer mit unterschiedlichen Oktavbezeichnungen anzeigen.

In synchronen Dateien des Typs 1 werden Ereignisse mehrerer Spuren zeitlich gemeinsam betrachtet. Ein Start auf einer Spur kann daher zu einem späteren Abschluss auf einer anderen passen. Enthalten verschiedene Spuren beim gleichen Tick gegensätzliche Aktionen für dieselbe Note, Route und denselben Kanal, kann ihre Reihenfolge das Ergebnis beeinflussen. Dann bleibt die Paarbilanz absichtlich leer: `balance_available` ist `false`, die betreffenden Positionen stehen unter `ambiguous_order_examples`.

Wiederholte Starts derselben Note können sich überlappen. Das Werkzeug meldet diese Stellen und ordnet Abschlüsse dem jeweils ältesten noch offenen Start zu (FIFO). Diese nachvollziehbare Buchführungsregel sagt nichts darüber aus, wie ein bestimmter Synthesizer seine Stimmen verteilt. Eine Überlappung wird nicht automatisch als Fehler bewertet.

## Pedal, Rücksetzbefehle und Routing

CC120, CC121 und CC123 bis CC127 werden mit Position und Wert aufgelistet. Sie verändern die explizite Paarzählung nicht. Die letzte gespeicherte CC64-Meldung pro logischer Route und Kanal wird ebenfalls gezeigt. Bei zeitgleichen Meldungen richtet sich „letzte“ nach der Berichtsreihenfolge Tick, Spur, Ereignis; daraus lässt sich kein sicherer Zustand des angeschlossenen Pedals ableiten. Sostenuto, Release-Zeiten, Klangprogramme und herstellerspezifische SysEx-Befehle werden nicht simuliert.

Ein MIDI-Port-Metaereignis gilt für die folgenden Ereignisse seiner Spur. Nicht näher bezeichnete Routen heißen `default`; explizite Portnummern bleiben davon getrennt. Diese Nummern sind Dateiinformationen, keine erkannten USB-Geräte. Wenn die DAW Spuren anders auf Geräte oder Plugins verteilt, muss diese tatsächliche Zuordnung zusätzlich geprüft werden.

## Sinnvoller nächster Vergleich

Exportiere einen kurzen betroffenen Abschnitt aus der DAW und vergleiche die gemeldete Position mit deren Ereignisliste. Bewahre das Original auf. Ein offener Start kann beispielsweise durch eine Abschnittsgrenze entstehen; ein zusätzlicher Abschluss durch einen davor liegenden, nicht mit exportierten Start. Prüfe auch, ob der Fehler im gespeicherten Export überhaupt vorkommt oder nur beim Live-Spielen entsteht. Dieses Programm hört keine Aufnahme und überwacht keine Verbindung.

Wenn der Ton bereits im internen Klang ohne Computer auffällt, ist das ein anderer Prüfpfad. Informationen zur fachlichen [E-Piano-Reparatur bei PIANOHOF](https://www.pianohof.de/leistungen/e-piano-reparatur) betreffen das Instrument; eine auffällige Dateibilanz beweist keinen Hardwaredefekt.

## Grenzen und technische Grundlagen

Unterstützt werden Standard-MIDI-Dateien 0 und 1 mit positiver PPQN-Auflösung, bis 8 MiB und 250.000 Ereignissen. Typ 2, SMPTE-Zeitteilung, Audio, UMP, SYX und RIFF/RMID sind nicht unterstützt. Der Reader ist keine vollständige SMF-Konformitätsprüfung. Detail-Listen sind auf je 2.000 Zeilen begrenzt; Zähler berücksichtigen sämtliche gelesenen Ereignisse. Ticks werden nicht in Sekunden umgerechnet.

Fachliche Ausgangspunkte sind die [MIDI Association – Nachrichtenübersicht](https://midi.org/summary-of-midi-1-0-messages), ihre [Controller-Tabelle](https://midi.org/midi-1-0-control-change-messages) und die [Mido-Dokumentation zu MIDI-Dateien](https://mido.readthedocs.io/en/stable/files/midi.html). Die konkrete Bilanz und ihre Grenzen sind im Quelltext offengelegt. Code, Anleitung und synthetisches Beispiel wurden mit KI-Unterstützung erstellt und anhand der mitgelieferten Tests geprüft. Keine behauptete reale Reparatur oder Herstellerfreigabe. Lizenz: MIT.
