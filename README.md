# Marstek Venus A – Nulleinspeisung (Zero Feed-In) Blueprint

Home Assistant Automation Blueprint zur automatischen Nulleinspeisung mit dem **Marstek Venus A** Batteriespeicher über die [Marstek Venus Modbus Integration](https://github.com/ViperRNMC/marstek_venus_modbus).

## Funktionen

- **Automatische Nulleinspeisung** – Regelt Lade-/Entladeleistung so, dass kein (oder nur minimaler) Strom ins Netz eingespeist wird
- **Automatische Force-Mode-Steuerung** – Setzt den Force Mode automatisch auf „laden" bei PV-Überschuss und „entladen" bei Netzbezug
- **Manuelle Einspeisung** – Feste Entladeleistung manuell vorgeben (hat Vorrang vor Nulleinspeisung)
- **Minimaler Netzbezug** – Einstellbar, wie viel Leistung immer aus dem Netz bezogen werden soll
- **Maximale Netzeinspeisung** – Erlaubte Einspeiseleistung ins Netz konfigurierbar (0 W = echte Nulleinspeisung)
- **Maximale Lade-/Entladeleistung** – Leistungsgrenzen der Batterie einstellbar
- **SOC-Schutz** – Minimaler und maximaler Ladezustand konfigurierbar
- **Entladeverzögerung** – Kurze Lastspitzen werden gefiltert; Entladung startet erst nach konfigurierbarer Wartezeit
- **Schrittweise Entladung** – Batterie rampt schrittweise hoch statt sofort mit voller Leistung zu entladen

## Voraussetzungen

| Komponente | Beschreibung |
|---|---|
| **Marstek Venus A** | Batteriespeicher mit Modbus-Anbindung |
| **Modbus-Integration** | [marstek_venus_modbus](https://github.com/ViperRNMC/marstek_venus_modbus) in Home Assistant installiert |
| **Leistungsmesser** | z.B. Shelly Pro3 EM am Netzanschluss (Hausanschlusspunkt) |
| **PV-Sensor** | Sensor für die aktuelle PV-Eingangsleistung (Solarleistung) |

### Benötigte Entitäten der Modbus-Integration

| Entität | Beispiel Entity-ID |
|---|---|
| Entladeleistung einstellen | `number.marstek_venus_modbus_entladeleistung_einstellen` |
| Ladeleistung einstellen | `number.marstek_venus_modbus_ladeleistung_einstellen` |
| Batterie-Ladezustand (SOC) | `sensor.marstek_venus_a_batterie_ladezustand` |
| PV-Eingangsleistung | `sensor.marstek_venus_a_pv_eingangsleistung` |

### Netzleistungs-Sensor (Shelly Pro3 EM)

Der Sensor muss die **Gesamtleistung am Netzanschluss** messen:
- **Positive Werte** = Netzbezug (Strom wird aus dem Netz bezogen)
- **Negative Werte** = Netzeinspeisung (Strom wird ins Netz eingespeist)

Dies ist der Standard bei Shelly-Geräten.

## Installation

### 1. Blueprint importieren

[![Open your Home Assistant instance and show the blueprint import dialog with this blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftechmhe%2Fhomeassistant_blueprints%2Fmain%2Fmarstek_venus_a_zero_feed_in.yaml)

Oder manuell:
1. **Einstellungen** → **Automatisierungen & Szenen** → **Blueprints**
2. **Blueprint importieren** klicken
3. URL eingeben:
   ```
   https://raw.githubusercontent.com/techmhe/homeassistant_blueprints/main/marstek_venus_a_zero_feed_in.yaml
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
| **PV-Eingangsleistung Sensor** | Sensor für die aktuelle PV-Eingangsleistung (Solarleistung) | – |
| **Batterie-Ladezustand** | SOC-Sensor des Marstek Venus A | – |
| **Entladeleistung einstellen** | Number-Entity der Modbus-Integration | – |
| **Ladeleistung einstellen** | Number-Entity der Modbus-Integration | – |
| **Nulleinspeisung aktivieren** | Input Boolean Helfer | – |
| **Manuelle Einspeisung aktivieren** | Input Boolean Helfer | – |
| **Manuelle Entladeleistung** | Input Number Helfer (Watt) | – |
| **Minimaler Netzbezug** | Mindest-Import aus dem Netz (W) | 0 W |
| **Maximale Netzeinspeisung** | Erlaubter Export ins Netz (W) | 0 W |
| **Maximale Entladeleistung** | Max. Batterie-Entladung (W) | 800 W |
| **Maximale Ladeleistung** | Max. Batterie-Ladung (W) | 800 W |
| **Minimaler SOC** | Untergrenze Ladezustand (%) | 10 % |
| **Maximaler SOC** | Obergrenze Ladezustand (%) | 100 % |
| **Entladeverzögerung** | Wartezeit vor Start der Entladung (s) | 3 s |
| **Entlade-Schrittweite** | Max. Erhöhung pro Zyklus (W) | 200 W |

## Funktionsweise

### Regelungsalgorithmus

Die Automatisierung läuft als proportionaler Regler mit Totzone:

1. **Netzleistung messen** – Aktuellen Import/Export am Netzanschluss lesen
2. **PV-Eingangsleistung lesen** – Aktuelle Solarleistung erfassen
3. **Soll-Leistung berechnen** – Lade-/Entladeleistung so anpassen, dass der Netzbezug auf den eingestellten minimalen Netzbezug geregelt wird
4. **Grenzen anwenden** – SOC-Limits und Leistungsgrenzen einhalten
5. **Lastspitzen filtern** – Beim Start der Entladung (aus dem Ruhezustand) wird die konfigurierte Verzögerung abgewartet
6. **Rampe anwenden** – Entladeleistung wird schrittweise erhöht, nicht sprunghaft
7. **Force Mode setzen** – Automatisch „entladen" bei Netzbezug, „laden" bei PV-Überschuss, „halt" bei Inaktivität
8. **Marstek steuern** – Neue Lade-/Entladeleistung per Modbus setzen

```
Totzone = [Min. Netzbezug − Max. Einspeisung, Min. Netzbezug + 10 W]

Wenn Netzimport oberhalb Totzone:
    → Entladeleistung erhöhen (schrittweise per Rampe)
    → Force Mode = entladen

Wenn Netzexport unterhalb Totzone:
    → Entladeleistung reduzieren oder Batterie laden (sofort)
    → Force Mode = laden (nur wenn PV-Eingangsleistung > 0 und Ladeziel > 0)

Wenn innerhalb Totzone:
    → Keine Änderung (Oszillation vermeiden)

Wenn weder Entladung noch Ladung aktiv:
    → Force Mode = halt

Beim Start der Entladung aus dem Ruhezustand:
    → Erst X Sekunden warten (Lastspitzen filtern)
    → Dann schrittweise hochfahren (z.B. 0 → 200 → 400 → 600 W)
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
- **Lastspitzenfilter**: Entladung startet erst nach konfigurierbarer Verzögerung (Standard: 3 s)
- **Sanfter Anlauf**: Entladeleistung wird schrittweise erhöht (Standard: 200 W pro Zyklus), Reduzierung erfolgt sofort

## Beispielkonfiguration

```yaml
# Typische Konfiguration für Shelly Pro3 EM + Marstek Venus A
Netzleistung Sensor:        sensor.shellyem3_total_power
PV-Eingangsleistung Sensor: sensor.marstek_venus_a_pv_eingangsleistung
Batterie-Ladezustand:       sensor.marstek_venus_a_batterie_ladezustand
Entladeleistung einstellen:  number.marstek_venus_modbus_entladeleistung_einstellen
Ladeleistung einstellen:     number.marstek_venus_modbus_ladeleistung_einstellen
Nulleinspeisung aktivieren:  input_boolean.nulleinspeisung_aktiv
Manuelle Einspeisung:        input_boolean.manuelle_einspeisung_aktiv
Manuelle Entladeleistung:    input_number.manuelle_entladeleistung
Minimaler Netzbezug:         50 W
Maximale Netzeinspeisung:    0 W
Maximale Entladeleistung:    800 W
Maximale Ladeleistung:       800 W
Minimaler SOC:               10 %
Maximaler SOC:               100 %
Entladeverzögerung:          3 s
Entlade-Schrittweite:        200 W
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
