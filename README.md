# PresenceCam

Aplikacja desktopowa: kamera USB → **wirtualna kamera o Twojej nazwie**, widoczna w Teams, Zoom, Meet i innych programach. Po drodze pętla z nagrania i udawane problemy z połączeniem. Windows 11 to główna platforma; działa też na macOS i Linuxie (z ograniczeniami opisanymi niżej).

## Co umie

- **Na żywo**: obraz z kamery przechodzi bez zmian.
- **Pętla z nagrania**: nagrywasz 15/30/60 s siebie „skupionego”, potem klawisz `P` przełącza na pętlę. Przełączenie wygląda jak chwilowy problem z łączem (zamrożenie 1–2,5 s, potem rozmyty obraz z przycięciem, który się wyostrza), więc nie widać skoku między nagraniem a obrazem na żywo. Pętla idzie tam i z powrotem. Powrót: `L`.
- **Kolejka nagrań**: zamiast jednego nagrania w kółko możesz ustawić kilka po kolei (przycisk „Do kolejki”, kolejność zmieniasz przeciąganiem). Każde leci raz tam i z powrotem, a przejście do następnego wygląda jak ten sam chwilowy problem z łączem. Pusta kolejka = jedno nagranie w pętli.
- **Podgląd na żywo**: gdy inni widzą pętlę albo problem z połączeniem, w rogu podglądu widzisz prawdziwą kamerę (tylko Ty), żeby wiedzieć, do czego wracasz.
- **Problemy z połączeniem** (na żywo i na pętli): **Zamrożenie** `F`, **Słaby internet** `W` (rozmyty obraz o niskiej rozdzielczości, nierówne 5–12 kl./s, krótkie zawieszki), **Przycinanie** `C` (stop na 1–4 s co jakiś czas), **Brak** `N`.
- **Nazwa kamery**: wpisujesz w aplikacji, np. „HD Webcam”; tak ją widzą wszystkie programy (Windows, Linux).
- Kamerę wejściową wybierasz po nazwie. Własna wirtualna kamera nigdy nie jest proponowana jako wejście.
- **Zajęta prawdziwa kamera** (Windows, domyślnie włączone): póki PresenceCam działa, trzyma prawdziwą kamerę na wyłączność, więc Teams i inne programy widzą ją jako zajętą i mogą użyć tylko wirtualnej. Wyłączasz to w Ustawieniach.

## Windows 11

1. Pobierz zbudowaną aplikację (folder `PresenceCam` z `PresenceCam.exe`, buduje go GitHub Actions: `.github/workflows/windows.yml`) i uruchom `PresenceCam.exe`.
2. W **Ustawieniach** wpisz nazwę kamery i kliknij **Zainstaluj / zmień nazwę kamery**. Windows raz zapyta o uprawnienia administratora (rejestracja sterownika Unity Capture, licencja MIT).
3. W Teams: Ustawienia → Urządzenia → Kamera → Twoja nazwa. Najlepiej przed dołączeniem do spotkania; jeśli Teams był otwarty podczas instalacji, uruchom go ponownie.

Zmiana nazwy: wpisz nową i kliknij ten sam przycisk (stara rejestracja jest usuwana).

## macOS i Linux

- **macOS**: zainstaluj [OBS Studio](https://obsproject.com) 30+, raz kliknij w nim **Start Virtual Camera** (zatwierdź rozszerzenie systemowe), zamknij OBS. Kamera nazywa się „OBS Virtual Camera”; własna nazwa wymagałaby rozszerzenia kamery podpisanego kontem Apple Developer.
- **Linux**: `sudo modprobe v4l2loopback exclusive_caps=1 card_label="Twoja nazwa"`.

## Uruchomienie ze źródeł

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m presencecam
```

Na Windowsie skopiuj też dwa pliki DLL sterownika do `drivers/windows/` (opis tam). Bez kamery aplikacja pokazuje ruchomy obraz testowy z zegarem.

## Ustawienia

Plik `config.json` (edytowalny ręcznie, aplikacja ignoruje błędne wartości):

| System | Miejsce |
|---|---|
| Windows | `%APPDATA%\PresenceCam\config.json` |
| macOS | `~/Library/Application Support/PresenceCam/config.json` |
| Linux | `~/.config/presencecam/config.json` |

Pola: `camera_name`, `input_device` (nazwa kamery wejściowej), `lock_input` (`true`/`false`, zajmowanie prawdziwej kamery), `width`, `height`, `fps`, `clips_dir` (domyślnie folder `clips` obok pliku), `loop_queue` (lista nazw nagrań w kolejce).

## Jak tego używać

1. Uruchom PresenceCam i w Teams wybierz swoją kamerę.
2. Nagraj 30–60 s: patrz w ekran, czasem kiwnij głową, nie mów (pętla nie ma ruchu ust).
3. `P` i robisz swoje. Gdy ktoś Cię wywoła: `L` i rozmawiasz.
4. Potrzebujesz chwili? **Słaby internet** albo **Zamrożenie**.

## Testy i budowanie

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/pyinstaller --noconfirm --windowed --name PresenceCam --add-data "drivers/windows:drivers/windows" --collect-submodules PySide6.QtMultimedia run.py
```

Na Windowsie separator w `--add-data` to `;`. Wersję dla Windows najprościej zbudować w GitHub Actions (workflow w repo).

## Ograniczenia

- Skróty klawiszowe działają, gdy okno PresenceCam jest aktywne (nie globalnie).
- Sterownik Unity Capture to filtr DirectShow: widzą go Teams, Zoom, przeglądarki i większość aplikacji. Aplikacje korzystające wyłącznie z Media Foundation (np. systemowa aplikacja Aparat) mogą go nie pokazać.
- Pętla nie zmienia światła: przy długim spotkaniu przy oknie nagraj nową co jakiś czas. Mikrofon wycisz sam.
