# Marstek Venus A – Nulleinspeisung (Zero Feed-In) Blueprint

Home Assistant Automation Blueprint zur automatischen Nulleinspeisung mit dem **Marstek Venus A** Batteriespeicher über die [Marstek Venus Modbus Integration](https://github.com/ViperRNMC/marstek_venus_modbus).

## Funktionen

- **Automatische Nulleinspeisung** – Regelt Lade-/Entladeleistung so, dass kein (oder nur minimaler) Strom ins Netz eingespeist wird
- **Manuelle Einspeisung** – Feste Entladeleistung manuell vorgeben (hat Vorrang vor Nulleinspeisung)
- **Maximale Netzeinspeisung** – Erlaubte Einspeiseleistung ins Netz konfigurierbar (0 W = echte Nulleinspeisung)
- **Maximale Lade-/Entladeleistung** – Leistungsgrenzen der Batterie einstellbar
- **SOC-Schutz** – Minimaler und maximaler Ladezustand konfigurierbar

## Voraussetzungen

| Komponente | Beschreibung |
|---|---|
| **Marstek Venus A** | Batteriespeicher mit Modbus-Anbindung |
| **Modbus-Integration** | [marstek_venus_modbus](https://github.com/ViperRNMC/marstek_venus_modbus) in Home Assistant installiert |
| **Leistungsmesser** | z.B. Shelly Pro3 EM am Netzanschluss (Hausanschlusspunkt) |

### Benötigte Entitäten der Modbus-Integration

| Entität | Beispiel Entity-ID |
|---|---|
| Entladeleistung einstellen | `number.marstek_venus_modbus_entladeleistung_einstellen` |
| Ladeleistung einstellen | `number.marstek_venus_modbus_ladeleistung_einstellen` |
| Batterie-Ladezustand (SOC) | `sensor.marstek_venus_a_batterie_ladezustand` |

### Netzleistungs-Sensor (Shelly Pro3 EM)

Der Sensor muss die **Gesamtleistung am Netzanschluss** messen:
- **Positive Werte** = Netzbezug (Strom wird aus dem Netz bezogen)
- **Negative Werte** = Netzeinspeisung (Strom wird ins Netz eingespeist)

Dies ist der Standard bei Shelly-Geräten.

## Installation

### 1. Blueprint importieren

[![Open your Home Assistant instance and show the blueprint import dialog with this blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Ftechmhe%2Fhomeassistant_blueprints%2Fblob%2Fmain%2Fmarstek_venus_a_zero_feed_in.yaml)

Oder manuell:
1. **Einstellungen** → **Automatisierungen & Szenen** → **Blueprints**
2. **Blueprint importieren** klicken
3. URL eingeben:
   ```
   https://github.com/techmhe/homeassistant_blueprints/blob/main/marstek_venus_a_zero_feed_in.yaml
   ```

### 2. Helfer erstellen

Vor dem Erstellen der Automatisierung müssen drei Helfer in Home Assistant angelegt werden:

#### Input Boolean: Nulleinspeisung aktivieren
1. **Einstellungen** → **Geräte & Dienste** → **Helfer** → **Helfer erstellen**
2. Typ: **Schalter** (Toggle)
3. Name: `Nulleinspeisung aktiv`

#### Input Boolean: Manuelle Einspeisung aktivieren
1. **Helfer erstellen** → Typ: **Schalter** (Toggle)
2. Name: `Manuelle Einspeisung aktiv`

#### Input Number: Manuelle Entladeleistung
1. **Helfer erstellen** → Typ: **Zahl** (Number)
2. Name: `Manuelle Entladeleistung`
3. Minimum: `0`, Maximum: `2500`, Schrittweite: `10`
4. Einheit: `W`

### 3. Automatisierung erstellen

1. **Einstellungen** → **Automatisierungen & Szenen** → **Automatisierung erstellen**
2. **Blueprint verwenden** → **Marstek Venus A – Nulleinspeisung** auswählen
3. Alle Felder konfigurieren (siehe unten)
4. **Speichern**

## Konfigurationsparameter

| Parameter | Beschreibung | Standard |
|---|---|---|
| **Netzleistung Sensor** | Leistungssensor am Netzanschluss (z.B. Shelly Pro3 EM) | – |
| **Batterie-Ladezustand** | SOC-Sensor des Marstek Venus A | – |
| **Entladeleistung einstellen** | Number-Entity der Modbus-Integration | – |
| **Ladeleistung einstellen** | Number-Entity der Modbus-Integration | – |
| **Nulleinspeisung aktivieren** | Input Boolean Helfer | – |
| **Manuelle Einspeisung aktivieren** | Input Boolean Helfer | – |
| **Manuelle Entladeleistung** | Input Number Helfer (Watt) | – |
| **Maximale Netzeinspeisung** | Erlaubter Export ins Netz (W) | 0 W |
| **Maximale Entladeleistung** | Max. Batterie-Entladung (W) | 800 W |
| **Maximale Ladeleistung** | Max. Batterie-Ladung (W) | 800 W |
| **Minimaler SOC** | Untergrenze Ladezustand (%) | 10 % |
| **Maximaler SOC** | Obergrenze Ladezustand (%) | 100 % |

## Funktionsweise

### Regelungsalgorithmus

Die Automatisierung läuft als proportionaler Regler:

1. **Netzleistung messen** – Aktuellen Import/Export am Netzanschluss lesen
2. **Soll-Leistung berechnen** – Lade-/Entladeleistung so anpassen, dass der Netzbezug gegen 0 geht
3. **Grenzen anwenden** – SOC-Limits und Leistungsgrenzen einhalten
4. **Marstek steuern** – Neue Lade-/Entladeleistung per Modbus setzen

```
Wenn Netzimport > 10 W:
    → Entladeleistung erhöhen (Batterie speist mehr ein)

Wenn Netzexport > erlaubte Einspeisung:
    → Entladeleistung reduzieren oder Batterie laden

Wenn im Toleranzbereich:
    → Keine Änderung (Oszillation vermeiden)
```

### Betriebsmodi

| Modus | Bedingung | Verhalten |
|---|---|---|
| **Nulleinspeisung** | Toggle „Nulleinspeisung" = AN | Automatische Regelung aktiv |
| **Manuelle Einspeisung** | Toggle „Manuelle Einspeisung" = AN | Feste Entladeleistung (hat Vorrang) |
| **Inaktiv** | Beide Toggles = AUS | Keine Steuerung durch die Automatisierung |

### Schutzmechanismen

- **SOC-Schutz unten**: Batterie wird nicht entladen wenn SOC ≤ Minimaler SOC
- **SOC-Schutz oben**: Batterie wird nicht geladen wenn SOC ≥ Maximaler SOC
- **Leistungsbegrenzung**: Lade-/Entladeleistung wird auf die konfigurierten Maximalwerte begrenzt
- **Sensorprüfung**: Automation pausiert bei nicht verfügbaren Sensoren
- **Entprellung**: 3 Sekunden Verzögerung zur Vermeidung von Modbus-Überlastung

## Beispielkonfiguration

```yaml
# Typische Konfiguration für Shelly Pro3 EM + Marstek Venus A
Netzleistung Sensor:        sensor.shellyem3_total_power
Batterie-Ladezustand:       sensor.marstek_venus_a_batterie_ladezustand
Entladeleistung einstellen:  number.marstek_venus_modbus_entladeleistung_einstellen
Ladeleistung einstellen:     number.marstek_venus_modbus_ladeleistung_einstellen
Nulleinspeisung aktivieren:  input_boolean.nulleinspeisung_aktiv
Manuelle Einspeisung:        input_boolean.manuelle_einspeisung_aktiv
Manuelle Entladeleistung:    input_number.manuelle_entladeleistung
Maximale Netzeinspeisung:    0 W
Maximale Entladeleistung:    800 W
Maximale Ladeleistung:       800 W
Minimaler SOC:               10 %
Maximaler SOC:               100 %
```

## Dashboard-Steuerung (optional)

Für eine komfortable Steuerung können Sie folgende Karten zu Ihrem Dashboard hinzufügen:

```yaml
type: entities
title: Marstek Venus A – Steuerung
entities:
  - entity: input_boolean.nulleinspeisung_aktiv
    name: Nulleinspeisung
  - entity: input_boolean.manuelle_einspeisung_aktiv
    name: Manuelle Einspeisung
  - entity: input_number.manuelle_entladeleistung
    name: Manuelle Leistung
  - type: divider
  - entity: sensor.marstek_venus_a_batterie_ladezustand
    name: Batterie SOC
  - entity: sensor.marstek_venus_a_batterieleistung
    name: Batterieleistung
  - entity: sensor.marstek_venus_a_ac_leistung
    name: AC-Leistung
```

## Lizenz

MIT License – siehe [LICENSE](LICENSE)
