# PresenceCam

Kamera USB → wirtualna kamera dla Teams / Zoom / Meet, z pętlą z nagrania i symulacją problemów z połączeniem. Sterowanie z przeglądarki (także z telefonu).

## Co umie

- **Na żywo**: obraz z kamery przechodzi bez zmian.
- **Pętla z nagrania**: nagrywasz 15–60 s siebie „skupionego”, potem jednym kliknięciem (albo klawiszem `P`) przełączasz na pętlę. Przejście jest przenikaniem (0,6 s), a pętla grana jest tam i z powrotem, więc nie ma skoku na jej końcu. Powrót na żywo: `L`.
- **Problemy z połączeniem** (na żywo i na pętli):
  - **Zamrożenie** `F`: obraz staje.
  - **Słaby internet** `W`: kwadraty, ~8 kl./s, krótkie zawieszki.
  - **Przycinanie** `C`: normalny obraz, który co jakiś czas staje na 1–4 s.
  - **Brak** `N`: wszystko wraca do normy.
- Nagrania zapisują się w `clips/` (mp4) i są dostępne po restarcie.

## Instalacja (raz)

1. **Wirtualna kamera**: zainstaluj [OBS Studio](https://obsproject.com) 30 lub nowszy, otwórz go, kliknij **Start Virtual Camera** (na macOS zatwierdź rozszerzenie systemowe w Ustawieniach → Prywatność i ochrona, może być potrzebny restart), potem zamknij OBS. PresenceCam sam używa tej kamery, OBS nie musi być włączony.
   - Linux: zamiast OBS wystarczy `v4l2loopback`.
2. **Program** (Python 3.10+):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Użycie

```bash
.venv/bin/python -m presencecam
```

Otwórz http://localhost:8765. W Teams wybierz kamerę **OBS Virtual Camera** (najlepiej przed wejściem na spotkanie).

Opcje:

- `--camera 1`: inna kamera (0 = pierwsza);
- `--width 1920 --height 1080 --fps 30`: rozdzielczość;
- `--host 0.0.0.0`: sterowanie z telefonu w tej samej sieci (uwaga: każdy w tej sieci może wtedy klikać);
- `--no-virtual`: tylko podgląd, bez wirtualnej kamery.

Bez kamery program pokazuje ruchomy obraz testowy z zegarem, więc da się go uruchomić i przetestować wszędzie.

## Jak to wygląda w praktyce

1. Uruchom PresenceCam, w Teams wybierz OBS Virtual Camera.
2. Na początku spotkania nagraj 30–60 s: patrz w ekran, od czasu do czasu kiwnij głową, nie mów (pętla nie ma dźwięku ani ruchu ust).
3. Przełącz na **Pętlę** i rób swoje. Gdy ktoś Cię wywoła: `L` (na żywo) i rozmawiasz.
4. Jeśli potrzebujesz chwili: **Słaby internet** albo **Zamrożenie** brzmią lepiej niż „kamera mi się zepsuła”.

## Testy

```bash
.venv/bin/python -m unittest discover -s tests
```

## Ograniczenia

- Pętla nie zmienia oświetlenia ani pory dnia: przy długim spotkaniu przy oknie różnica może być widoczna. Nagraj nową pętlę co jakiś czas.
- Mikrofon nie jest obsługiwany: wycisz go sam.
- Teams na macOS bywa wybredny co do kamery wybranej w trakcie spotkania; wybierz ją przed dołączeniem.
