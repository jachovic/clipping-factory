# START TUTAJ — uruchomienie w 3 krokach

## 1. Instalacja (raz, ~10 minut)
1. Na GitHubie: **Code → Download ZIP**. Rozpakuj do `C:\clipping-factory`.
2. Otwórz folder `C:\clipping-factory\windows` i kliknij dwukrotnie **`INSTALUJ.bat`**.
   Instalator sam doinstaluje Pythona i FFmpeg, ustawi autostart, wyłączy usypianie komputera (na zasilaczu)
   i doda na pulpit skrót **Clipping Factory**.
3. Na końcu otworzy się przeglądarka ze stroną **Konfiguracja**.

> Jeśli coś się wysypie, zrób zrzut ekranu okna i wklej go w następnej sesji — poprawię.

## 2. Konfiguracja (tylko wklejanie)
Na stronie **Konfiguracja** (skrót na pulpicie albo http://127.0.0.1:8080/s/setup):
- **Crayo API key** — w aplikacji Crayo: Developers → Create key → wklej → *Zapisz i przetestuj*.
- **Telegram** — w Telegramie napisz do **@BotFather**: `/newbot`, skopiuj token → wklej → zapisz.
  Potem napisz `/start` do swojego nowego bota i kliknij **Wykryj moje Telegram ID**. Bot odpisze „Połączono”.
- **Konta** — wpisz nazwy swoich 11 kont (TikTok ×5, Instagram ×2, X ×2, YouTube ×2) → *Zapisz konta*.

## 3. Codzienna praca (to, czego nie da się zautomatyzować zgodnie z zasadami platform)
Fabryka sama szuka kampanii, ocenia je, wybiera najlepsze, robi klipy i sprawdza jakość. Ty:
- **Dołączasz do wybranej kampanii** (link w Telegramie i na stronie Konfiguracja, sekcja 3) i **wgrywasz materiał
  źródłowy** z briefu (przycisk *Wgraj* albo link).
- **Publikujesz klip**, gdy bot przyśle plik i gotowy opis → odpisujesz `/posted <id> <link>`.
- **Zgłaszasz link w marketplace**, gdy bot przypomni (z terminem) → `/submitted <id>`.
- Wpisujesz wypłaty: `/paid 25 content_rewards`.

Wszystkie komendy: `/help` w bocie. Awaryjne zatrzymanie: `/stopall`.
