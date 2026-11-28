# START — instrukcja krok po kroku

Plan: **przygotowanie ~60 min (dziś wieczorem albo jutro 9:00) → start o 10:00.**
Fabryka sama szuka kampanii, ocenia je, wybiera najlepsze, tnie klipy, robi napisy i kontrolę jakości.
Twoja rola to rzeczy, których platformy nie pozwalają zautomatyzować: logowanie, dołączanie do kampanii,
wrzucanie postów i zgłaszanie linków.

---

## CZĘŚĆ A — przygotowanie (przed 10:00)

### A1. Lista kont (5 min)
Zapisz sobie nazwy (@) wszystkich 11 kont: TikTok ×5, Instagram ×2, X ×2, YouTube ×2, plus niszę każdego
(np. gaming, muzyka, podcasty) i język (zwykle `en`).
Na Instagramie przełącz oba konta na **konto profesjonalne → Twórca** (Ustawienia → Typ konta). Jest za darmo
i część kampanii tego wymaga.

### A2. Klucz Crayo (5 min)
1. Zaloguj się na **crayo.ai** → w aplikacji otwórz **Developers**.
2. **Create key** → skopiuj klucz (zaczyna się od `crayo_sk_`). Wkleisz go w kroku A7.
3. Sprawdź tam, czy Twój plan ma kredyty API (eksport). Jeśli nie ma, fabryka i tak działa na silniku lokalnym.

### A3. Bot Telegram (5 min)
1. W Telegramie znajdź **@BotFather** → napisz `/newbot` → podaj nazwę (np. `Moja Fabryka`) i login kończący się
   na `bot` (np. `mojafabryka_bot`).
2. Skopiuj **token** (wygląda jak `1234567890:AA...`). Wkleisz go w kroku A7.
3. Otwórz swojego nowego bota i napisz do niego **`/start`** (potrzebne do wykrycia Twojego ID).

### A4. Konta na marketplace'ach (20 min)
- **Content Rewards**: wejdź na contentrewards.com/login → załóż konto lub zaloguj się → w ustawieniach
  **podłącz konta społecznościowe** (TikTok, Instagram, YouTube, X), na które chcesz publikować.
- **Clipster**: app.clipster.gg → załóż konto (e-mail) → **Settings → Connected Accounts → Link Account** dla każdego
  konta → **Login Methods → Discord → Connect** (potrzebne do wsparcia).
- **Vyro** (opcjonalnie): app.vyro.com → załóż konto → podłącz konta.
- Ustaw metodę wypłat (PayPal / przelew) tam, gdzie się da.

### A5. Pobranie fabryki (3 min)
1. Zaloguj się na GitHub → otwórz **github.com/jachovic/clipping-factory**.
2. Zielony przycisk **Code → Download ZIP**.
3. Rozpakuj do **`C:\clipping-factory`** (tak, żeby w środku był od razu folder `windows`, plik `README.md` itd.).

### A6. Instalacja (10–15 min, dwuklik)
1. Otwórz `C:\clipping-factory\windows` → dwuklik **`INSTALUJ.bat`**.
2. Jeśli Windows pokaże „System Windows ochronił ten komputer” → **Więcej informacji → Uruchom mimo to**.
3. Instalator sam:
   - doinstaluje Pythona i FFmpeg;
   - zainstaluje fabrykę i pobierze model mowy (~140 MB);
   - zrobi diagnostykę;
   - ustawi autostart w tle;
   - wyłączy usypianie komputera na zasilaczu;
   - doda skrót **Clipping Factory** na pulpicie.
4. Na końcu otworzy się przeglądarka ze stroną **Konfiguracja**.

> Jeśli pojawi się błąd „winget nie jest rozpoznawany”: zainstaluj **Instalator aplikacji (App Installer)**
> z Microsoft Store i uruchom `INSTALUJ.bat` jeszcze raz.
> Jeśli cokolwiek innego się wysypie: zrób zrzut ekranu i wyślij mi go.

### A7. Konfiguracja (10 min, tylko wklejanie)
Na stronie **⚙ Konfiguracja** (skrót na pulpicie albo http://127.0.0.1:8080/s/setup):
1. **Crayo API key** → wklej. **Telegram bot token** → wklej. Kliknij **Zapisz i przetestuj**.
2. Kliknij **Wykryj moje Telegram ID**. Bot odpisze „✅ Połączono…”.
3. Opcjonalnie: limity wydatków (domyślnie $5/dzień, $60/miesiąc) i **Anthropic API key** (lepszy wybór momentów przez AI).
4. Sekcja **2. Konta**: wpisz nazwy, niszę i język 11 kont. W kolumnie *Marketplaces* wpisz, gdzie które
   konto jest podłączone (np. `content_rewards,clipster`). Kliknij **Zapisz konta**.
5. Sekcja **4**: kliknij **Pełna diagnostyka**. Ma być napisane **WSZYSTKO GOTOWE do startu**.

**Fabryka już pracuje w tle.** Do 10:00 zdąży znaleźć i ocenić kampanie.

---

## CZĘŚĆ B — start o 10:00

### B1. Wybrane kampanie (Konfiguracja → sekcja 3)
Fabryka wybiera do 4 najlepszych kampanii i przysyła je też na Telegram. Dla **każdej**:
1. Kliknij nazwę kampanii → **dołącz** (jeśli wymaga zgłoszenia, aplikuj i poczekaj na akceptację).
2. **Przeczytaj pełne zasady** (część jest widoczna tylko po zalogowaniu): wymagane hashtagi, oznaczenia (@marka),
   dopiski (#ad), długość klipu, z którego materiału wolno ciąć.
3. W wierszu pod kampanią fabryka **wstępnie wypełniła** to, co znalazła w publicznym opisie. Popraw / dopisz według
   pełnych zasad → zaznacz **Przeczytałem zasady** → **Zapisz zasady**.
   (Bez tego fabryka nie wypuści klipów do publikacji — to celowe zabezpieczenie.)
4. **Materiał źródłowy:** jeśli brief ma link do Google Drive / Dropbox, fabryka pobierze go **sama** po kroku 3.
   W przeciwnym razie pobierz materiał z *Reference Materials* i kliknij **Wgraj** (albo wklej link → **Pobierz**).

Po 10–20 minutach na jeden film (zależnie od długości) klipy są gotowe.

### B2. Publikacja (strona ✅ Do zrobienia + Telegram)
Dla każdego klipu dostaniesz na Telegramie **plik wideo z gotowym opisem** i wskazanym kontem.
1. Zapisz wideo na telefonie → wrzuć na **wskazane konto** z **dokładnie tym opisem**.
2. Skopiuj link do posta → na stronie **Do zrobienia** wklej go i kliknij **Opublikowane**
   (albo w Telegramie: `/posted <numer> <link>`).

Fabryka rozkłada posty w czasie, maksymalnie 3 dziennie na konto, co najmniej 3 h odstępu.

### B3. Zgłoszenie w kampanii (od razu po publikacji!)
1. Strona **Do zrobienia → Do zgłoszenia** pokazuje link do kampanii i **termin**
   (Clipster: 24 h od publikacji; liczą się tylko wyświetlenia po zgłoszeniu, więc zgłaszaj od razu).
2. Wklej link posta w kampanii (*Submit*) → w fabryce kliknij **Zgłoszone** (albo `/submitted <numer>`).

---

## CZĘŚĆ C — codziennie (10–20 min)
- 2–3 razy dziennie zajrzyj w **Do zrobienia** albo Telegram.
- Odrzucony klip: `/rejected <numer> <kategoria> <powód>`. Fabryka zapamięta zasadę i nie powtórzy błędu.
- Zaakceptowany: `/approved <numer>`. Wypłata na konto: `/paid 25 content_rewards`.
  Tylko to liczy się jako prawdziwe pieniądze.
- Raport dzienny przychodzi o 8:00 (czasu PL zimą). W poniedziałki przychodzi tygodniowy, 1. dnia miesiąca miesięczny.
- **Nie usuwaj opublikowanych klipów** (Clipster wymaga 30 dni po wypłacie).

## Jak sterować
| Co | Jak |
|---|---|
| Otworzyć panel | skrót **Clipping Factory** na pulpicie |
| Uruchomić ponownie | `windows\URUCHOM.bat` |
| Zatrzymać | `windows\ZATRZYMAJ.bat` albo `/stopall` w Telegramie |
| Wstrzymać nowe klipy/publikacje | `/pause`, wznowić: `/resume` |
| Sprawdzić co nie działa | `windows\DIAGNOSTYKA.bat` albo przycisk *Pełna diagnostyka* |
| Logi | `C:\clipping-factory\data\factory.log` |

Komputer musi być włączony; uśpienie na zasilaczu instalator wyłączył. Po restarcie komputera fabryka startuje
sama po zalogowaniu do Windows.

## Zasady, których pilnujemy (żeby nie stracić wypłat)
Tylko materiał z briefu kampanii. Żadnych kupionych wyświetleń, botów ani grup wzajemnego lajkowania. Wymagane
hashtagi i oznaczenia zawsze w opisie. Nie wrzucamy tego samego klipu na dwa konta tej samej platformy w jednej kampanii.
