---
name: wsl_gui_hidpi
description: >-
  Спецификация и руководство по устранению диалоговых окон связки ключей GNOME Keyring ("Unlock Login Keyring")
  и настройке масштабирования высокого разрешения (HiDPI / 2K / 3K / 4K) для GUI-приложений (Claude Desktop,
  Electron, GTK, Wayland) в подсистеме Windows Subsystem for Linux (WSL2 / WSLg).
---

# WSLg High-DPI Scaling & Keyring Bypass Protocol

> [!IMPORTANT]
> **Первая версия этого скилла задавала масштаб 2** (`--force-device-scale-factor=2`, `GDK_SCALE=2`), что вместе
> с масштабом Weston 2× давало 400% (баг двойного масштабирования). Раздел 2 приведён к актуальной конфигурации:
> `--force-device-scale-factor=1.0`, `GDK_SCALE=1`, `QT_SCALE_FACTOR=1`; математика описана в
> [`skills/claude_wsl_gui/SKILL.md`](../claude_wsl_gui/SKILL.md#4-hidpi-математика-масштабирования).
> Там же — полная спецификация запуска Claude Desktop в WSLg (vless2socks, геометрия окна, lockfile, COPY MODE).

Данный скилл регламентирует устранение блокирующих модальных окон авторизации связки ключей GNOME Keyring (`Unlock Login Keyring`) и настройку безупречного, кристально четкого масштабирования интерфейса графических приложений Linux (WSLg) на мониторах с высоким разрешением (Retina, 2K, 3K 3072×1920, 4K).

---

## 1. Проблема 1: Диалоговое окно «Unlock Login Keyring»

### 1.1. Анатомия сбоя
В среде WSL2 отсутствует дисплейный менеджер с PAM-аутентификацией (GDM/LightDM), который в полноценном дистрибутиве Linux автоматически расшифровывает связку ключей паролем пользователя при входе в систему.

Когда Electron-приложение (например, `claude-desktop`) запускается с флагом `--password-store=gnome-libsecret`:
1. Приложение запрашивает Secret Service по сессионной шине DBus (`org.freedesktop.secrets`).
2. Демон `gnome-keyring-daemon` обнаруживает, что связка `login.keyring` заблокирована.
3. Библиотека `gcr` вызывает графический агент `pinentry-gnome3`, который принудительно открывает модальное окно:
   > **Unlock Login Keyring**  
   > *Authentication required: The login keyring did not get unlocked when you logged into your computer.*  
   > *The unlock password was incorrect.*
4. Пользователь вынужден вводить пароль вручную или нажимать Cancel, что ломает автоматизацию.

### 1.2. Решение и регламент устранения
1. **Отключение обращения к GNOME Keyring в Electron:**
   В лаунчерах приложений заменяется флаг хранилища:
   ```bash
   # БЫЛО (вызывает модальное окно pinentry):
   --password-store=gnome-libsecret

   # СТАЛО (полностью устраняет модальное окно, сохраняя сессии в профиле):
   --password-store=basic
   ```
2. **Очистка заблокированных бинарных кейсторов в WSL:**
   Принудительное удаление зашифрованных бинарных файлов `user.keystore` и замена на беспарольный манифест:
   ```bash
   pkill -9 -f gnome-keyring 2>/dev/null || true
   rm -rf ~/.local/share/keyrings/*
   cat << 'EOF' > ~/.local/share/keyrings/login.keyring
   [keyring]
   display-name=Login
   ctime=1695477404
   mtime=0
   lock-on-idle=false
   lock-after=false
   EOF
   echo "login" > ~/.local/share/keyrings/default
   ```
3. **Беспарольный запуск демона:**
   Передача пустой строки (`printf ""`) вместо символа перевода строки (`printf "\n"`):
   ```bash
   eval $(printf "" | gnome-keyring-daemon --daemonize --login --components=secrets 2>/dev/null)
   ```

---

## 2. Проблема 2: Размытие или мелкий интерфейс на HiDPI (3K / 4K)

### 2.1. Анатомия масштабирования WSLg
При разрешении дисплея **3072×1920** с системным DPI **192** (масштаб Windows 200%):
1. **XWayland (legacy X11):** Запуск через `--ozone-platform=x11` форсирует рендеринг через XWayland, который отображает приложение с размытием (растровый апскейлинг) либо в крошечном размере (96 DPI).
2. **Ограничения дробного масштабирования:** Композитор WSLg Weston по умолчанию может блокировать передачу дробного масштаба хоста Windows.

### 2.2. Решение и регламент настройки
1. **Конфигурация WSLg на хосте Windows (`%USERPROFILE%\.wslgconfig`):**
   ```ini
   # C:\Users\<Username>\.wslgconfig
   [system-distro-env]
   WESTON_RDP_DISABLE_FRACTIONAL_HI_DPI_SCALING=false
   ```
   `WESTON_RDP_DEBUG_DESKTOP_SCALING_FACTOR` не задаётся: Weston берёт масштаб монитора Windows сам.
2. **Флаги графического бэкенда и масштабирования Chromium:**
   ```bash
   # БЫЛО (первая версия, 400% на экране 200%):
   --ozone-platform-hint=auto --enable-features=WaylandWindowDecorations --force-device-scale-factor=2

   # СТАЛО (масштаб уже даёт Weston: логическая сетка 1536×960 × 2 = 3072×1920):
   --ozone-platform-hint=auto --enable-features=WaylandWindowDecorations --force-device-scale-factor=1.0
   ```
   Переопределение: env `CLAUDE_SCALE_FACTOR` или файл `<профиль>/scale-factor.conf`.
   Фактически Claude Desktop работает через XWayland (`--ozone-platform=x11` у дочерних процессов), см.
   [`claude_wsl_gui` §4.3](../claude_wsl_gui/SKILL.md#43-фактический-бэкенд-x11-а-не-wayland).
3. **Глобальные переменные окружения Linux (`/etc/profile.d/wsl_hidpi.sh`):**
   ```bash
   export ELECTRON_OZONE_PLATFORM_HINT=auto
   export GDK_SCALE=1
   export GDK_DPI_SCALE=1
   export QT_AUTO_SCREEN_SCALE_FACTOR=1
   export QT_SCALE_FACTOR=1
   export XCURSOR_SIZE=24
   ```
   > [!NOTE]
   > С 2026-10-23 эти значения записывают `new_claude/configure_wsl_hidpi_keyring.sh` и
   > `setup_helpers/09_configure_wsl_hidpi_keyring.ps1` (`-ScaleFactor 1 -Dpi 96`, `Xft.dpi: 96`).
   > Раньше они писали `GDK_SCALE=2`, `QT_SCALE_FACTOR=2` и `WESTON_RDP_DEBUG_DESKTOP_SCALING_FACTOR=200`.

---

## 3. Реестр скриптов автоматизации

| Скрипт | Назначение | Платформа |
| :--- | :--- | :--- |
| [`setup_helpers/09_configure_wsl_hidpi_keyring.ps1`](file:///C:/proj/vless2socks/setup_helpers/09_configure_wsl_hidpi_keyring.ps1) | Автоматическая генерация `.wslgconfig`, провижининг `/etc/profile.d/wsl_hidpi.sh` и сброс кейрингов. | Windows PowerShell |
| [`new_claude/configure_wsl_hidpi_keyring.sh`](file:///C:/proj/vless2socks/new_claude/configure_wsl_hidpi_keyring.sh) | Баш-скрипт сброса `login.keyring` и фиксации переменных HiDPI внутри дистрибутива. | Linux (WSL) |
| [`new_claude/template_claude_gui.sh`](file:///C:/proj/vless2socks/new_claude/template_claude_gui.sh) | Эталонный шаблон шелл-лаунчера с флагами `--password-store=basic` и `--force-device-scale-factor=1.0`. | Linux (WSL) |

---

## 4. Верификация

1. **Проверка устранения Keyring:**
   * Запуск приложения через ярлык на Рабочем столе: `Claude GUI.lnk` или `Claude Code GUI 2.lnk`.
   * **Результат:** Окно `Unlock Login Keyring` **не появляется**. Приложение сразу открывает главный интерфейс.
2. **Проверка четкости HiDPI:**
   * Интерфейс отображается в масштабе 200% (как остальные окна Windows), а не 400%; окно по умолчанию помещается в экран.
