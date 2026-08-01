# Marstek Venus A – Nulleinspeisung (Zero Feed-In) Blueprint

Home Assistant Automation Blueprint zur automatischen Nulleinspeisung mit dem **Marstek Venus A** Batteriespeicher über die [Marstek Venus Modbus Integration](https://github.com/ViperRNMC/marstek_venus_modbus).

## Funktionen

- **Automatische Nulleinspeisung** – Regelt Entladeleistung so, dass kein (oder nur minimaler) Strom ins Netz eingespeist wird
- **Manuelle Einspeisung** – Feste Entladeleistung manuell vorgeben (hat Vorrang vor Nulleinspeisung)
- **Maximale Einspeisung (Max-Ertrag)** – Reicht die PV-Leistung verlustfrei durch und fährt den Speicher ab einem einstellbaren SOC gezielt leer, damit für den nächsten PV-Überschuss wieder Platz ist ([Details](#modus-maximale-einspeisung-max-ertrag))
- **Minimaler Netzbezug** – Einstellbar, wie viel Leistung immer aus dem Netz bezogen werden soll
- **Konfigurierbare Totband-Toleranz** – Symmetrisches Totband um den Ziel-Netzbezug (weniger Modbus-Schreibzugriffe)
- **Maximale Lade-/Entladeleistung** – Leistungsgrenzen der Batterie einstellbar
- **SOC-Schutz** – Minimaler und maximaler Ladezustand konfigurierbar
- **SOC-Recovery** – Automatisches Notladen aus dem Netz, wenn der SOC unter einen kritischen Schwellwert fällt
- **PV-Passthrough** – Wenn die Batterie voll ist (SOC ≥ max. SOC) und PV erzeugt wird, gibt der Wechselrichter die PV-Leistung direkt ins Haus/Netz ab (verhindert Abregelung)
- **Entladeverzögerung** – Kurze Lastspitzen werden gefiltert; Entladung startet erst nach konfigurierbarer Wartezeit
- **Schrittweise Entladung** – Batterie rampt schrittweise hoch statt sofort mit voller Leistung zu entladen

## Hardware-Architektur (Marstek Venus A)

Der Marstek Venus A verfügt über zwei unabhängige Energiepfade:

| Seite | Beschreibung |
|---|---|
| **DC / MPPT** | PV → Batterie. Läuft **immer automatisch**, unabhängig vom Force Mode. |
| **AC / Wechselrichter** | `discharge`: Batterie+PV → Haus/Netz · `charge`: Netz → Batterie · `stop`: AC-Seite inaktiv (MPPT läuft weiter) |

**Wichtig:** `force_mode = charge` bedeutet AC-seitiges Laden **aus dem Netz**. PV-Überschuss wird dagegen automatisch vom MPPT aufgenommen – dafür ist kein `charge`-Force-Mode nötig. Der Blueprint setzt `charge` daher **ausschließlich** beim SOC-Recovery (Notladen aus dem Netz).

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
| Force Mode Auswahl | `select.marstek_venus_modbus_force_mode` |
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

Vor dem Erstellen der Automatisierung müssen fünf Helfer in Home Assistant angelegt werden:

#### Input Boolean: Nulleinspeisung aktivieren
1. **Einstellungen** → **Geräte & Dienste** → **Helfer** → **Helfer erstellen**
2. Typ: **Schalter** (Toggle)
3. Name: `Nulleinspeisung aktiv`

#### Input Boolean: Manuelle Einspeisung aktivieren
1. **Helfer erstellen** → Typ: **Schalter** (Toggle)
2. Name: `Manuelle Einspeisung aktiv`

#### Input Boolean: Maximale Einspeisung aktivieren
1. **Helfer erstellen** → Typ: **Schalter** (Toggle)
2. Name: `Maximale Einspeisung aktiv`

#### Input Boolean: Speicher wird leergefahren
1. **Helfer erstellen** → Typ: **Schalter** (Toggle)
2. Name: `Speicher wird leergefahren`

> Diesen Schalter **nicht selbst bedienen** – die Automatisierung nutzt ihn als
> Statusspeicher für den Modus „Maximale Einspeisung" und schaltet ihn selbstständig
> ein und aus. Er ist bewusst als sichtbarer Helfer umgesetzt, damit man im Dashboard
> und im Verlauf sieht, ob gerade leergefahren wird.

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
| **Force Mode Auswahl** | Select-Entity der Modbus-Integration | – |
| **Nulleinspeisung aktivieren** | Input Boolean Helfer | – |
| **Manuelle Einspeisung aktivieren** | Input Boolean Helfer | – |
| **Manuelle Entladeleistung** | Input Number Helfer (Watt) | – |
| **Maximale Einspeisung aktivieren** | Input Boolean Helfer | – |
| **Helfer: Speicher wird leergefahren** | Input Boolean Helfer – Statusspeicher, wird automatisch gesetzt | – |
| **Leerfahren ab SOC** | Ab diesem SOC wird der Speicher leergefahren; 0 = deaktiviert (%) | 80 % |
| **Leerfahren bis SOC** | Bis zu diesem SOC wird leergefahren (%) | 20 % |
| **Minimaler Netzbezug** | Mindest-Import aus dem Netz (W) | 0 W |
| **Totband (W)** | Symmetrische Toleranz um den Ziel-Netzbezug | 10 W |
| **Maximale Entladeleistung** | Max. Batterie-Entladung / PV-Passthrough (W) | 800 W |
| **Maximale Ladeleistung** | Max. Batterie-Ladung aus dem Netz via Recovery (W) | 800 W |
| **Minimaler SOC** | Untergrenze Ladezustand – darunter keine Entladung (%) | 10 % |
| **Maximaler SOC** | Obergrenze – darüber kein Laden, PV-Passthrough aktiv (%) | 100 % |
| **Recovery SOC** | Notladen aus dem Netz wenn SOC ≤ dieser Wert; 0 = deaktiviert (%) | 0 % |
| **Entladeverzögerung** | Wartezeit vor Start der Entladung (s) | 3 s |
| **Entlade-Schrittweite** | Max. Erhöhung pro Zyklus (W) | 200 W |

## Funktionsweise

### Regelungsalgorithmus

Die Automatisierung läuft als proportionaler Regler mit symmetrischem Totband:

1. **Netzleistung messen** – Aktuellen Import/Export am Netzanschluss lesen
2. **PV-Eingangsleistung lesen** – Aktuelle Solarleistung erfassen (nicht verfügbar → 0 W)
3. **Soll-Leistung berechnen** – Entladeleistung so anpassen, dass der Netzbezug auf den eingestellten minimalen Netzbezug geregelt wird
4. **Grenzen anwenden** – SOC-Limits, Leistungsgrenzen und PV-Passthrough-Failsafe einhalten
5. **Lastspitzen filtern** – Beim Start der Entladung (aus dem Ruhezustand) wird die konfigurierte Verzögerung abgewartet
6. **Rampe anwenden** – Entladeleistung wird schrittweise erhöht, nicht sprunghaft
7. **Force Mode setzen** – Prioritätsreihenfolge: Recovery → Entladen → Stop
8. **Marstek steuern** – Neue Lade-/Entladeleistung per Modbus setzen

```
Totzone = [Min. Netzbezug − Totband, Min. Netzbezug + Totband]

Wenn Netzimport oberhalb Totzone:
    → Entladeleistung erhöhen (schrittweise per Rampe)
    → Force Mode = entladen

Wenn Netzexport unterhalb Totzone:
    → Entladeleistung reduzieren (sofort)
    → Force Mode = stop (MPPT lädt Batterie aus PV automatisch weiter)

Wenn innerhalb Totzone:
    → Keine Änderung (Oszillation vermeiden)

Wenn SOC ≥ Maximaler SOC und PV > 0 (PV-Passthrough):
    → Entladeleistung = PV-Leistung (begrenzt auf max. Entladeleistung)
    → Force Mode = entladen (PV wird über Wechselrichter ins Haus/Netz geleitet)

Wenn SOC ≤ Recovery SOC (Recovery aktiv):
    → Ladeleistung = max. Ladeleistung (auch aus dem Netz)
    → Force Mode = laden (höchste Priorität)

Beim Start der Entladung aus dem Ruhezustand:
    → Erst X Sekunden warten (Lastspitzen filtern)
    → Dann schrittweise hochfahren (z.B. 0 → 200 → 400 → 600 W)
```

### Betriebsmodi

Die Modi werden in dieser Reihenfolge geprüft – der erste aktive gewinnt:

| Priorität | Modus | Bedingung | Verhalten |
|---|---|---|---|
| 1 | **Manuelle Einspeisung** | Toggle „Manuelle Einspeisung" = AN | Feste Entladeleistung |
| 2 | **Maximale Einspeisung** | Toggle „Maximale Einspeisung" = AN | PV durchreichen + Speicher leerfahren |
| 3 | **Nulleinspeisung** | Toggle „Nulleinspeisung" = AN | Automatische Regelung auf den Ziel-Netzbezug |
| – | **Inaktiv** | Alle Toggles = AUS | Keine Steuerung durch die Automatisierung |

### Modus „Maximale Einspeisung" (Max-Ertrag)

Dieser Modus ist für Anlagen gedacht, bei denen **jede kWh am AC-Ausgang gleich viel
wert ist** – etwa bei fester Einspeisevergütung. Er ist besonders dann sinnvoll, wenn
ein **kleiner Speicher auf viel Modulleistung** trifft und die AC-Ausgangsleistung hart
begrenzt ist (z.B. 2 kWh Speicher, 1,8 kWp Module, 800 W AC).

Die Nulleinspeisung ist in so einem Setup kontraproduktiv: Sie regelt die Abgabe auf
nahezu Null, der kleine Speicher ist mittags voll – und danach muss der Wechselrichter
abregeln, der PV-Ertrag verfällt.

**Regeln** (die Reihenfolge ist bindend):

```
Wenn SOC ≤ Minimaler SOC:
    → Entladeleistung = 0, Force Mode = stop      (Schutz geht immer vor)

Wenn Leerfahren aktiv (Latch) ODER PV = 0 ODER SOC ≥ Maximaler SOC:
    → Entladeleistung = max. Entladeleistung

Sonst:
    → Entladeleistung = PV-Leistung (begrenzt auf max. Entladeleistung)
```

**Warum PV durchreichen statt konstant Volllast?**
Bei 400 W PV und konstant 800 W Abgabe werden 400 W aus der Batterie genommen, die
kurz darauf wieder aus der PV nachgeladen werden. Jeder dieser Durchläufe kostet
Round-Trip-Wirkungsgrad. Wird stattdessen genau die PV-Leistung abgegeben, liefert der
MPPT den Strom direkt nach – die Batterie bleibt netto unangetastet, es entstehen keine
Umwandlungsverluste und kein Micro-Cycling an der SOC-Grenze.

**Warum das Leerfahren (Latch)?**
Ohne diese Regel pendelt der Ladezustand knapp unter dem maximalen SOC. Kommt nach einer
Verschattungsphase wieder die volle Modulleistung, ist kein Puffer mehr da und der
Wechselrichter regelt ab. Deshalb gilt: Erreicht der SOC **„Leerfahren ab SOC"**, wird
mit voller Entladeleistung entladen – und zwar **durchgehend, bis „Leerfahren bis SOC"
erreicht ist**, auch wenn der SOC zwischendurch längst wieder unter der oberen Schwelle
liegt. Danach wird wieder nur die PV-Leistung durchgereicht.

Genau dafür wird der Helfer `Speicher wird leergefahren` gebraucht: Ein Blueprint hat
kein Gedächtnis über einzelne Durchläufe hinweg, und der Zustand lässt sich auch nicht
aus der eingestellten Entladeleistung ableiten – bei PV oberhalb der Grenze steht dort
ebenfalls die volle Entladeleistung, ohne dass leergefahren wird.

**Tagesablauf am Beispiel** (Leerfahren ab 80 %, bis 20 %):

| Situation | PV | SOC | Abgabe | Latch |
|---|---|---|---|---|
| Morgens, schwache Sonne | 300 W | 20 % | 300 W | aus |
| Es klart auf | 1800 W | 25 % | 800 W (1000 W laden den Speicher) | aus |
| Speicher erreicht Schwelle | 1800 W | 80 % | 800 W | **an** |
| Verschattung | 400 W | 60 % | 800 W (Speicher wird weiter geleert) | an |
| Untere Schwelle erreicht | 400 W | 20 % | 400 W | aus |
| Sonne kommt zurück | 1800 W | 22 % | 800 W – Puffer ist wieder da | aus |
| Nacht | 0 W | 45 % | 800 W bis Minimaler SOC | aus |

**Hinweise:**
- Der Netzleistungs-Sensor, das Totband und die Entladeverzögerung werden in diesem
  Modus nicht verwendet – geregelt wird ausschließlich nach PV und SOC.
- Fällt der PV-Sensor aus, wird das wie Nacht behandelt (0 W) und der Speicher entladen.
- „Leerfahren bis SOC" wird nie unter den minimalen SOC gelassen; niedrigere Werte werden
  automatisch auf diesen angehoben.
- Höhere Werte bei „Leerfahren bis SOC" bedeuten weniger Ladezyklen, aber auch weniger
  Puffer für die nächste Sonnenphase.

### Schutzmechanismen

- **SOC-Schutz unten**: Batterie wird nicht entladen wenn SOC ≤ Minimaler SOC
- **SOC-Schutz oben**: Batterie wird nicht geladen wenn SOC ≥ Maximaler SOC
- **PV-Passthrough**: Wenn Batterie voll (SOC ≥ max. SOC) und PV aktiv → Entladung mit PV-Leistung, damit der Wechselrichter PV ins Haus/Netz leitet
- **SOC-Recovery**: Wenn SOC ≤ Recovery SOC (und > 0 konfiguriert) → Notladen aus dem Netz mit maximaler Ladeleistung bis SOC = min. SOC
- **Leistungsbegrenzung**: Lade-/Entladeleistung wird auf die konfigurierten Maximalwerte begrenzt
- **Sensorprüfung**: Automation pausiert bei nicht verfügbarem SOC-Sensor. Der Netzsensor wird nur für die Nulleinspeisung benötigt – fällt er aus, laufen die manuelle und die maximale Einspeisung weiter. PV-Sensor-Ausfall wird als 0 W behandelt (Nachtbetrieb bleibt funktionsfähig)
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
Force Mode Auswahl:          select.marstek_venus_modbus_force_mode
Nulleinspeisung aktivieren:  input_boolean.nulleinspeisung_aktiv
Manuelle Einspeisung:        input_boolean.manuelle_einspeisung_aktiv
Manuelle Entladeleistung:    input_number.manuelle_entladeleistung
Maximale Einspeisung:        input_boolean.maximale_einspeisung_aktiv
Speicher wird leergefahren:  input_boolean.speicher_wird_leergefahren
Leerfahren ab SOC:           80 %
Leerfahren bis SOC:          20 %
Minimaler Netzbezug:         50 W
Totband:                     10 W
Maximale Entladeleistung:    800 W
Maximale Ladeleistung:       800 W
Minimaler SOC:               10 %
Maximaler SOC:               100 %
Recovery SOC:                0 %  (deaktiviert)
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
  - entity: input_boolean.maximale_einspeisung_aktiv
    name: Maximale Einspeisung
  - entity: input_boolean.speicher_wird_leergefahren
    name: Speicher wird leergefahren
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
