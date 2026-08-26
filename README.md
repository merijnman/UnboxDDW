# DDW installatie — v1

Lelijke maar werkende v1 voor het uittesten van de interactie. Robuustheid en
schoonheid komen later.

Bezoekers beelden een verhaal uit op een tafel. Een top-down webcam neemt 30
seconden op (beeld + geluid). Een top-down projector projecteert op datzelfde
tafelblad: in rust het vorige verhaal, tijdens de opname instructies en
feedback.

## Vereisten

- Windows 11
- Python 3.11+
- [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) in PATH (of pad instellen via
  `ffmpeg_path` in `config.json`)
- Eén machine, twee displays: de projector als tweede scherm, het
  laptopscherm als bedieningsscherm

## Installatie

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Controleer dat ffmpeg werkt:

```powershell
ffmpeg -version
```

## Voor de eerste run

Doe dit één keer (en na elke Windows-update, want sommige instellingen
resetten):

1. **Instellingen → Privacy en beveiliging → Camera** en **Microfoon** →
   toegang voor bureaubladapps aan.
2. **Energiebeheer** → USB-instellingen → **USB selective suspend** uit.
   Anders kan Windows de camera/microfoon tussentijds uitzetten.
3. Sluit de beamer aan en zet Windows-weergave op **Uitbreiden**, met de
   beamer op schaal **100%** (niet 125%/150%). Anders klopt de kalibratie
   niet met de werkelijke pixels.
4. Zet **slaapstand**, **schermbeveiliging** en **meldingen** (Focus
   assist / Do Not Disturb aan) uit. Een meldingspopup midden op de
   projectie tijdens een opname is vervelend.

## Devicenamen vinden (Windows / dshow)

```powershell
python main.py --list-devices
```

Dit draait `ffmpeg -list_devices true -f dshow -i dummy` en print de
beschikbare video- en audiodevices. Gebruik de **alternative name**
(`@device_pnp_...`), niet de friendly name — die is stabieler over reboots.
Zet ze in `config.json`:

```json
"video_device": "@device_pnp_\\\\?\\usb#vid_...",
"audio_device": "@device_pnp_\\\\?\\usb#vid_..."
```

(Let op de backslashes: dit is een JSON-string, dus elke `\` wordt `\\`.)

### Camera-instellingen (belichting, witbalans)

Zet exposure en witbalans op handmatig zodat de belichting niet
voortdurend verspringt tijdens opnames:

```powershell
python main.py --camera-settings
```

Dit opent het dshow-eigenschappenvenster van de camera. **Let op:** deze
instellingen kunnen per sessie resetten (na herstarten van de app of de
pc) — controleer dit voor elke opbouw opnieuw.

## Testen zonder camera/projector

Genereer een testvideo (testbeeld + toon, via ffmpeg) zodat de statemachine
en rendering te zien zijn zonder dat er al iets is opgenomen:

```powershell
python main.py --make-dummy-seed
python main.py
```

Met één beeldscherm opent de app een venster van 1280×800 in plaats van
fullscreen op een tweede display, zodat je op je laptop kunt testen.

## Kalibratieprocedure (in deze volgorde)

Kalibreer de **camera vóór de projector**: de camerakalibratie bepaalt hoe
een cameraframe naar het tafelblad-coördinatenstelsel wordt vervormd; de
projectorkalibratie bepaalt hoe dat coördinatenstelsel vervolgens op de
projector wordt uitgelijnd. Andersom kalibreren werkt ook, maar dan zie je
tijdens de camerakalibratie geen correct beeld terug op tafel totdat beide
klaar zijn.

### 1. Camera (`V`)

1. Start de app: `python main.py`
2. Druk op **V**. Er wordt één frame opgenomen en getoond in een apart
   venster op het laptopscherm.
3. Klik de vier hoeken van het fysieke tafelblad aan, in deze volgorde:
   **linksboven, rechtsboven, rechtsonder, linksonder**.
4. Na de vierde klik wordt `H_cam` automatisch berekend en opgeslagen in
   `config.json`. **Esc** annuleert.

### 2. Projector (`C`)

1. Druk op **C**. Op de projectie verschijnt een blauw raster met vier
   genummerde hoekpunten (1–4).
2. Op het laptopscherm (de terminal) verschijnt een statusregel met de
   huidige hoekcoördinaten.
3. Toetsen **1–4** selecteren een hoek. **Pijltjes** verplaatsen 1 pixel,
   **Shift+pijltjes** 10 pixels.
4. Sleep elke hoek tot het geprojecteerde raster exact samenvalt met de
   fysieke rand van de tafel.
5. **S** slaat `H_proj` op in `config.json`. **Esc** sluit de
   kalibratiemodus (zonder op te slaan als je geen `S` hebt gedrukt).

Herkalibreren werkt met terugwerkende kracht: video's worden ongerectificeerd
opgeslagen, dus een nieuwe kalibratie corrigeert ook alle bestaande opnames.

## Bediening tijdens gebruik

| Toets | Actie |
|---|---|
| Spatie | Start opname (alleen werkzaam in rust/IDLE) |
| C | Projectorkalibratie |
| V | Camerakalibratie |
| H | Verberg de laatst opgenomen video (telt niet meer mee voor selectie) |
| Esc | Afsluiten |

## Bestandsstructuur

```
main.py           statemachine + hoofdloop
config.py         laden/opslaan/defaults (config.json)
projection.py     table surface, warp, per-state rendering
capture.py        ffmpeg-opname, devicedetectie
playback.py       videodecode + audio-sidecar
library.py        index.jsonl, selectiestrategieën
calibrate.py      camera- en projectorkalibratie
media/videos/     opnames (mp4 + wav-sidecar) + index.jsonl
media/seeds/      vooraf gemaakte startvideo's (mp4 + wav)
logs/app.log      logbestand
```

## Bekende beperkingen (bewust, voor v1)

- Geen foutafhandeling voor exotische ffmpeg-versies; getest tegen een
  recente gyan.dev-build.
- Audio-sync tussen video en wav-sidecar wordt niet actief gecorrigeerd
  (drift over 30s is verwaarloosbaar).
- Kalibratie is modaal en pauzeert de bezoekersflow; een lopende opname
  (het ffmpeg-subprocess) loopt gewoon door, want die staat los van de
  Python-hoofdlus.
