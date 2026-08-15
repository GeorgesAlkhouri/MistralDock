# MistralDock v1 – Designspezifikation

**Datum:** 2026-08-15  
**Status:** Zur Freigabe  
**Ziel:** Ein kleiner, externer Sidecar-Dienst ersetzt nach dem Import den OCR-Inhalt sowie ausgewählte Metadaten eines Paperless-ngx-Dokuments durch validierte Mistral-Ergebnisse, ohne Paperless-Code, -Datenbank, -Dateisystem oder Originaldatei zu verändern.

## 1. Produktumfang

MistralDock läuft als eigener Container neben Paperless-ngx. Paperless meldet neu hinzugefügte Dokumente per Workflow-Webhook. MistralDock lädt das Dokument und die sichtbaren Tags ausschließlich über die Paperless-REST-API, verarbeitet jede Seite mit Mistral OCR, validiert das Ergebnis und schreibt es über einen einzigen Paperless-PATCH zurück.

Geschrieben werden ausschließlich:

- `content`: vollständiges, in Seitenreihenfolge zusammengesetztes Mistral-OCR-Markdown;
- `title`: kurzer, archivtauglicher Dokumenttitel;
- `created`: fachliches Dokumentdatum, sofern sicher und plausibel;
- `tags`: Vereinigungsmenge aus bestehenden Tags und gültigen Mistral-Vorschlägen.

Nicht Bestandteil von v1 sind neue Tags, Korrespondenten, Dokumenttypen, Custom Fields, Bounding Boxes, Mistral-Blocks in Paperless, PDF-Manipulation, eine Paperless-Erweiterung, eine Weboberfläche oder direkter Zugriff auf Paperless-Speicher.

## 2. Technische Architektur

### Laufzeit und Deployment

- Python 3.12, FastAPI, Pydantic v2, `httpx`, offizielles `mistralai`-SDK, SQLAlchemy 2, Alembic und `pypdf`.
- Ein OCI-Container mit einem API-Prozess und einem internen, dauerhaften Worker; v1 unterstützt genau eine Containerinstanz.
- SQLite im WAL-Modus auf einem persistenten Volume hält Queue-, Run- und Cleanup-Zustand. Es wird kein zusätzlicher Broker benötigt.
- Der Container erhält keinen Mount auf Paperless-Medien oder die Paperless-Datenbank. Ein temporäres Verzeichnis im Container dient nur der laufenden Verarbeitung.
- Das Beispiel-Compose-Fragment hängt MistralDock an dasselbe interne Docker-Netz wie Paperless. Der API-Port wird standardmäßig nicht am Host veröffentlicht.

### Komponenten

1. **HTTP API:** authentifiziert Webhooks und manuelle Aufträge, liefert Status sowie Health-Endpunkte und bestätigt angenommene Aufträge sofort mit HTTP 202.
2. **Durable Queue:** dedupliziert automatische Aufträge pro Dokument, vergibt zeitlich begrenzte Worker-Leases und macht abgebrochene Arbeit nach einem Neustart wieder ausführbar.
3. **Paperless Client:** spricht API-Version 10 mit `Accept: application/json; version=10`, verfolgt Pagination und verwendet `Authorization: Token …`.
4. **Document Processor:** lädt die aktuelle Originaldatei, ermittelt Typ und Seitenzahl, erzeugt PDF-Chunks und setzt OCR-Seiten wieder geordnet zusammen.
5. **Mistral Client:** lädt Dateien mit Zweck `ocr`, erzeugt signierte URLs, ruft OCR/Annotations auf und löscht Remote-Dateien anschließend.
6. **Metadata Pipeline:** erzeugt und konsolidiert ausschließlich `title`, `created` und `tags`.
7. **Validator/Updater:** prüft OCR und Metadaten, erkennt konkurrierende Paperless-Änderungen und führt genau einen PATCH aus.

## 3. Öffentliche Schnittstellen

Alle `/v1`-Endpunkte verlangen `Authorization: Bearer <MISTRALDOCK_API_TOKEN>`. Health-Endpunkte benötigen keine Authentifizierung.

### `POST /v1/webhooks/paperless`

Payload:

```json
{"document_id": 123}
```

Ein gültiger neuer Auftrag liefert `202 Accepted` mit `document_id`, `job_id` und Zustand `queued`. Ein Duplikat eines bereits laufenden oder erfolgreichen automatischen Auftrags liefert ebenfalls 202 und den vorhandenen Zustand, erzeugt aber keinen weiteren Run. Ungültige Authentifizierung liefert 401, ungültiges JSON oder eine nichtpositive ID liefert 422.

Paperless wird mit einem „Document Added“-Workflow konfiguriert. Die Webhook-Aktion sendet JSON `{"document_id": {{doc_id}}}` sowie den Bearer-Header an MistralDock.

### `POST /v1/documents/{document_id}/reprocess`

Erzeugt einen erzwungenen neuen Run für das aktuellste Dokument/Version, auch wenn zuvor ein Run erfolgreich war. Der Endpunkt respektiert den globalen Schreibmodus und liefert 202. Mehrere gleichzeitige Reprocess-Aufträge für dasselbe Dokument werden zu einem aktiven Auftrag zusammengeführt.

### `GET /v1/documents/{document_id}/runs`

Liefert Run-Zustand, Zeitpunkte, Modelle, Prompt-Version, Seiten-/Chunkzahl, vorgeschlagene und angewandte Metadaten, Validierungswarnungen und Fehlercodes. Vollständiger OCR-Text, Dokumentdateien und Geheimnisse werden weder hier noch in der Datenbank gespeichert.

### `GET /health/live` und `GET /health/ready`

`live` bestätigt den laufenden Prozess. `ready` prüft geladene Konfiguration, Datenbankzugriff und ausgeführte Migrationen; vorübergehende Paperless- oder Mistral-Ausfälle machen den Dienst nicht unready, sondern werden über Runs sichtbar.

### `GET /metrics`

Liefert Prometheus-Metriken ohne dokumentbezogene Labels. Der Endpunkt ist nicht authentifiziert und darf wie die Health-Endpunkte nur im internen Container-Netz beziehungsweise gezielt über einen Monitoring-Proxy erreichbar sein.

## 4. Konfiguration

Pflichtwerte:

- `PAPERLESS_URL`
- `PAPERLESS_TOKEN`
- `MISTRAL_API_KEY`
- `MISTRALDOCK_API_TOKEN`

Festgelegte Defaults:

- `PAPERLESS_API_VERSION=10`
- `MISTRAL_OCR_MODEL=mistral-ocr-latest`
- `MISTRAL_METADATA_MODEL=mistral-small-latest`
- `DATABASE_URL=sqlite:////data/mistraldock.db`
- `WRITE_MODE=dry-run` (`dry-run` oder `live`)
- `OCR_CHUNK_PAGES=8`
- `WORKER_CONCURRENCY=1`
- `MAX_ATTEMPTS=5`
- `RETRY_BASE_SECONDS=30`
- `RETRY_MAX_SECONDS=3600`
- `DOCUMENT_DATE_MIN=1900-01-01`

Der Dienst bricht beim Start mit einer klaren Konfigurationsmeldung ab, wenn Pflichtwerte fehlen, URLs ungültig sind, `PAPERLESS_TOKEN` und `MISTRALDOCK_API_TOKEN` identisch sind oder ein unbekannter Modus gesetzt ist. Geheimnisse werden ausschließlich über Umgebungsvariablen beziehungsweise Docker Secrets eingespeist und in keiner Antwort oder Logzeile ausgegeben.

## 5. Verarbeitungsablauf

1. Webhook oder manueller Reprocess legt transaktional einen Queue-Eintrag an.
2. Ein Worker leased den Auftrag und lädt `GET /api/documents/{id}/`. Nicht vorhanden, nicht sichtbar oder nicht änderbar beendet den Run dauerhaft mit einem eindeutigen Fehlercode.
3. MistralDock merkt sich die aktuelle Versions-ID und den Änderungszeitpunkt. Es lädt mit `GET /api/documents/{id}/download/?original=true&version={version_id}` die Originaldatei in eine temporäre Datei.
4. `GET /api/tags/` wird vollständig paginiert. Nur für den Service-Account sichtbare Namen bilden das erlaubte Vokabular.
5. PDFs werden mit `pypdf` in fortlaufende Blöcke von höchstens acht Seiten geteilt. Unterstützte Rasterbilder (PNG, JPG/JPEG, TIFF, BMP, GIF, WEBP) bilden einen einzelnen Block. Andere Typen, beschädigte/verschlüsselte PDFs oder einzelne Chunks über dem Mistral-Limit von 512 MB führen ohne Paperless-Änderung zu einem permanenten Fehler.
6. Jeder Chunk wird zu Mistral Files hochgeladen, per signierter URL pro Verarbeitungsversuch genau einmal mit `mistral-ocr-latest` verarbeitet und anschließend gelöscht. Für OCR werden keine Base64-Bilder oder Bounding Boxes angefordert; Tabellen bleiben als Markdown erhalten.
7. Die Markdown-Ausgaben werden anhand ihrer originalen Seitenposition mit zwei Leerzeilen verbunden. Annotationen werden nie in `content` gemischt. Leerer oder offensichtlich unvollständiger OCR-Text lässt den gesamten Run scheitern.
8. Bei einem einzelnen Chunk ist dessen Document Annotation das Metadatenergebnis. Bei mehreren Chunks werden deren geordnete Kandidaten in genau einem strukturierten Chat-Aufruf mit `mistral-small-latest`, Temperatur 0 und demselben Ausgabeschema konsolidiert. Dadurch kann jedes Chunk zur dokumentweiten Entscheidung beitragen, ohne den vollständigen OCR-Text in ein begrenztes Chat-Kontextfenster zu zwingen.
9. Direkt vor dem Schreiben lädt MistralDock das Dokument und die Tags erneut. Hat sich die Dokumentversion oder der Änderungszeitpunkt verändert, wird kein PATCH ausgeführt; der Run endet als `conflict` und kann manuell wiederholt werden.
10. Nach erfolgreicher Validierung wird im Live-Modus genau ein `PATCH /api/documents/{id}/?version={version_id}` gesendet. Im Dry-run-Modus wird derselbe Payload nur im Run protokolliert.
11. Erst ein erfolgreicher PATCH markiert einen Live-Run als `succeeded`. Ein vollständig validierter Dry-run endet ebenfalls als `succeeded`, trägt aber explizit `applied=false`. Temporäre lokale Dateien werden in jedem Ausgang gelöscht.

## 6. OCR- und Metadatenschema

Das Schema enthält bewusst genau drei fachliche Felder:

```json
{
  "title": "string, 5 bis 128 Zeichen",
  "created": "YYYY-MM-DD oder null",
  "tags": ["exakte Namen vorhandener Paperless-Tags"]
}
```

Der Annotation-Prompt wird versioniert und enthält folgende Regeln:

- Titel aus dem fachlichen Inhalt ableiten, Absender/Organisation, Dokumentart und relevanten Zeitraum/Gegenstand bevorzugen; keine bloße generische Überschrift wie „Ihre Rechnung“ übernehmen.
- Als Datum das Ausstellungs-, Rechnungs-, Vertrags- oder vergleichbare fachliche Datum verwenden; niemals Scan-, Upload- oder Verarbeitungsdatum. Bei Unsicherheit `null` liefern.
- Null, einen oder mehrere Tag-Namen auswählen; nur Namen aus der übergebenen Liste exakt übernehmen; keine neuen Namen erfinden.
- Bei Chunk-Annotationen nur Informationen aus diesem Seitenbereich melden. Der Konsolidierungsprompt entscheidet anschließend aus allen geordneten Kandidaten für das Gesamtdokument.

Das erlaubte Tag-Vokabular wird bei jedem Run aktuell aus Paperless geladen und sowohl im Prompt als auch im nachgelagerten Validator erzwungen.

## 7. Validierung und Paperless-Update

- **Content:** mindestens ein sichtbares alphanumerisches Zeichen je nichtleerer OCR-Seite; die Antwort muss dieselbe Seitenanzahl wie der Chunk enthalten; die Gesamtreihenfolge muss lückenlos sein.
- **Titel:** Whitespace wird normalisiert; 5–128 Zeichen; keine Steuerzeichen/Zeilenumbrüche; nicht nur Ziffern/Satzzeichen; nicht identisch mit dem Originaldateinamen ohne Endung. Nach Unicode-Casefolding gelten `document`, `dokument`, `scan`, `untitled`, `ohne titel`, `ihre rechnung`, `rechnung`, `brief` und `schreiben` als unbrauchbare exakte Platzhalter. Ein ungültiger Titel verhindert den gesamten PATCH.
- **Datum:** striktes ISO-Datum zwischen `DOCUMENT_DATE_MIN` und dem aktuellen Datum plus einem Tag. `null` oder ein ungültiges/unplausibles Datum wird aus dem PATCH ausgelassen, sodass Paperless `created` unverändert bleibt.
- **Tags:** exakter, case-sensitiver Namensabgleich gegen die unmittelbar vor dem PATCH neu geladene Tag-Liste. Unbekannte Vorschläge werden verworfen und als Warnung protokolliert. Der Payload enthält die Vereinigungsmenge der dann bestehenden Dokument-Tags und gültigen Vorschläge; kein vorhandener Tag wird entfernt.
- **Atomarität:** Schemaparsing, Content- und Titelprüfung müssen vollständig erfolgreich sein, bevor Paperless geschrieben wird. Der PATCH enthält nur `content`, `title`, `tags` und optional `created`.

## 8. Zustandsmodell, Idempotenz und Fehler

Queue-Zustände sind `queued`, `processing`, `retry_wait`, `succeeded`, `failed` und `conflict`. Jeder Versuch erzeugt einen eigenen Run-Datensatz mit Zeitstempeln für seine Zustandsübergänge; der aktuelle Job verweist auf den aktiven/letzten Run.

- Automatische Webhook-Duplikate werden über die Paperless-Dokument-ID dedupliziert. Ein erfolgreicher automatischer Job läuft erst durch den expliziten Reprocess-Endpunkt erneut.
- Ein Worker-Lease läuft nach Prozessabbruch ab; der Auftrag kehrt dann in `retry_wait` zurück.
- HTTP 429, 5xx, Timeouts und vorübergehende Netzwerkfehler sind retriable. Backoff: `min(30 * 2^(attempt-1), 3600)` Sekunden plus Jitter, höchstens fünf Versuche.
- Authentifizierungs-/Berechtigungsfehler, nicht unterstützte Formate, ungültige Dokumente und fachliche Validierungsfehler sind permanent. Konflikte werden nicht automatisch wiederholt, um Nutzeränderungen nicht zu überschreiben.
- Falls eine Mistral-Datei im `finally`-Block nicht gelöscht werden kann, wird ihre ID in einer Cleanup-Tabelle gespeichert. Ein Hintergrundlauf versucht die Löschung mit Backoff erneut, bis Mistral Erfolg oder „nicht gefunden“ meldet.
- Fehler vor dem Paperless-PATCH lassen das Dokument unverändert. Ein unklarer Netzwerkabbruch während des PATCH wird durch erneutes GET verifiziert: Entspricht der aktuelle Zustand exakt dem geplanten Payload, gilt der Run als erfolgreich; andernfalls endet er als Konflikt statt blind erneut zu schreiben.

Als Idempotenzmerkmal dient ausschließlich die Sidecar-Datenbank. MistralDock erstellt oder benötigt keinen technischen Paperless-Tag.

## 9. Sicherheit und Datenschutz

- Paperless-Servicekonto: globale Rechte „View Document“, „Change Document“ und „View Tag“ sowie passende Objektberechtigungen für alle zu verarbeitenden Dokumente/Tags; keine Add-/Delete-/Admin-Rechte.
- Der Workflow authentifiziert sich mit einem eigenen zufälligen MistralDock-Bearer-Token. Paperless- und Mistral-Token sind davon getrennt.
- Dokumentinhalt, OCR-Volltext und Dateien erscheinen nicht in Logs, API-Antworten oder dauerhafter Sidecar-Speicherung. Run-Daten enthalten nur Längen/Hashes, Metadatenvorschläge, IDs, Zustände und Fehlercodes.
- Mistral-Dateien werden nach jedem Chunk sofort gelöscht. Fehlgeschlagene Löschungen werden dauerhaft nachverfolgt; laut Mistral werden Uploads andernfalls bis zu 30 Tage aufbewahrt.
- Der Dienst wird nur im internen Netz betrieben. Für eine hostübergreifende Verbindung ist TLS am Reverse Proxy zwingend; Mistral-Verbindungen verwenden HTTPS und Zertifikatsprüfung.
- Logfelder und Metriklabels enthalten keine Token, Dokumenttitel, Tag-Namen, OCR-Fragmente oder Originaldateinamen.

## 10. Beobachtbarkeit und Betrieb

Strukturierte JSON-Logs enthalten `job_id`, `run_id`, `document_id`, Phase, Versuch, Dauer, Seiten-/Chunkzahl, Provider-Statusklasse und stabilen Fehlercode. Metriken zählen angenommene, erfolgreiche, fehlgeschlagene und erneut versuchte Runs sowie Laufzeiten und verarbeitete Seiten; `document_id` wird nicht als Metriklabel verwendet.

Beim Start werden Datenbankmigrationen ausgeführt, abgelaufene Leases freigegeben und ausstehende Mistral-Cleanups eingeplant. Ein SIGTERM stoppt die Annahme neuer Worker-Aufgaben, lässt den aktuellen Schritt bis zu einer konfigurierten Grace Period abschließen und gibt andernfalls den Lease für den Neustart frei.

## 11. Test- und Einführungsstrategie

### Automatisierte Tests

- Unit-Tests für Konfiguration, Authentifizierung, Pagination, Chunkgrenzen, Seitenreihenfolge, Schema-/Titel-/Datumsvalidierung, exaktes Tag-Mapping, Tag-Vereinigung, Backoff und Zustandsübergänge.
- Contract-Tests gegen simulierte Paperless- und Mistral-HTTP-Antworten: API-v10-Header, Originaldownload, versionierter PATCH, Mistral Upload/Signed URL/OCR/Delete sowie Fehlerklassen.
- Integrations-Test mit temporärer SQLite-Datenbank und Fake-Providern für den vollständigen Pfad: Webhook → Queue → ein/mehrere Chunks → Konsolidierung → Dry-run beziehungsweise ein PATCH.
- Recovery-Tests für Prozessabbruch, abgelaufenen Lease, doppelten Webhook, PATCH-Timeout mit erfolgreicher Verifikation, konkurrierende Dokumentänderung und fehlgeschlagene Mistral-Löschung.
- Container-Smoke-Test für nicht-root-Ausführung, read-only Root-Filesystem, beschreibbares `/data` und `/tmp`, Health-Endpunkte und graceful shutdown.

### Pilot und Abnahme

1. Mit `WRITE_MODE=dry-run` 20–50 repräsentative Dokumente verarbeiten, darunter Rechnungen, Briefe, Verträge, Behördenpost, schlechte Scans sowie Dokumente unter, genau und über acht Seiten.
2. Über den Run-Endpunkt OCR-Länge/Seitenzahl, Titel, Datum, Tags, verworfene Tags und Fehler prüfen; erwartete Werte in einer versionierten Evaluationsdatei festhalten.
3. Akzeptanz: 100 % vollständige Seitenreihenfolge; keine erfundenen/angelegten oder entfernten Tags; kein Überschreiben bei unsicherem Datum; keine Paperless-Änderung bei Fehler/Conflict; keine verbliebenen Mistral-Dateien nach erfolgreichem Cleanup.
4. Nach fachlicher Freigabe auf `WRITE_MODE=live` umstellen und den Workflow zunächst auf einen Paperless-Testtag begrenzen.
5. Nach einer stabilen Beobachtungsphase den Workflow auf alle neu hinzugefügten Dokumente erweitern. Rollback erfolgt allein durch `WRITE_MODE=dry-run` oder Stoppen des MistralDock-Containers; Paperless selbst bleibt unverändert.

## 12. Verbindliche Quellen

- [Paperless-ngx REST API, Authentifizierung, Dokumentversionen und API-Versionierung](https://docs.paperless-ngx.com/api/)
- [Paperless-ngx Workflows, Webhooks und `doc_id`](https://docs.paperless-ngx.com/usage/#workflows)
- [Mistral OCR API](https://docs.mistral.ai/api/endpoint/ocr)
- [Mistral Document Annotations](https://docs.mistral.ai/studio/document-processing/annotations)
- [Mistral Document Chunking](https://docs.mistral.ai/resources/cookbooks/mistral-ocr-documentchunking-readme)
- [Mistral Structured Outputs](https://docs.mistral.ai/studio-api/conversations/structured-output/custom)
- [Mistral bekannte Limits und Dateiaufbewahrung](https://docs.mistral.ai/resources/known-limitations)
