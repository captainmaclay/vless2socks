---
name: wsl-proxy-isolation
description: >-
  Автоматическая проверка WSL, изоляция всего сетевого трафика WSL2 ядра Linux на локальный прокси
  (сокетом SOCKS5 1015 / HTTP 11015) через nftables/iptables с защитой от DNS-утечек (socks5h)
  и полный аудит герметичности сетевого контура по стандарту Herdr Control Center.
---

# Скилл: Сетевая изоляция WSL2 и ограничение трафика через сокет 1015

Этот скилл предназначен для автономных агентов и моделей (Gemini, Claude, Antigravity) для гарантированного развертывания, проверки и тестирования сетевой изоляции WSL2.

## Архитектурные принципы изоляции (Herdr Standard)

1. **Изоляция на уровне ядра Linux (Zero Bypass):**
   - Внутри WSL2 трафик фильтруется подсистемой `nftables` (fallback на `iptables`/`ip6tables`).
   - Разрешается **только** интерфейс обратной петли (`lo` и `loopback0` для mirrored network mode WSL2).
   - Любой прямой трафик WAN/LAN (eth0, прямые коннекты без прокси) безусловно отвергается правилом `counter reject`.
   - Локальные обходные порты (например, `127.0.0.1:2080`) блокируются отдельным правилом `ip daddr 127.0.0.1 tcp dport 2080 counter reject`.
2. **Предотвращение DNS-утечек (socks5h):**
   - Переменные окружения `/etc/profile.d/herdr_claude_env.sh` устанавливают:
     `ALL_PROXY='socks5h://127.0.0.1:1015'` (суффикс `h` форсирует разрешение доменных имен удаленной стороной прокси, предотвращая обращение к локальным DNS-серверам хоста).
     `HTTPS_PROXY='http://127.0.0.1:11015'`
3. **Персистентность (Autonomous Boot Containment):**
   - Правила ядра сохраняются в `/etc/nftables.conf`.
   - Создается загрузчик `/usr/local/bin/herdr_boot_isolation.sh`.
   - В `/etc/wsl.conf` добавляется директива `[boot] command=/usr/local/bin/herdr_boot_isolation.sh` и активируется служба `nftables`.
   - Изоляция восстанавливается мгновенно с первой секунды старта дистрибутива.
4. **Гарантия свободы Windows-хоста (Direct IP):**
   - Хостовые процессы Windows (`node.exe`, IDE, браузеры) не блокируются; правила Windows Firewall очищаются (`Remove-NetFirewallRule -DisplayName 'Claude Node Isolate'`).

---

## Быстрые команды через утилиту

Утилита расположена в `c:\Users\f\Documents\antigravity\delightful-tesla\cli.py`:

### 1. Быстрая проверка наличия WSL
```powershell
python "c:\Users\f\Documents\antigravity\delightful-tesla\cli.py" check-wsl
```
*Критерий успеха:* `WSL установлен: ДА (True)`, отображен список дистрибутивов и активный дистрибутив (например, `Ubuntu`).

### 2. Применение правил изоляции (Lockdown на сокет 1015)
```powershell
python "c:\Users\f\Documents\antigravity\delightful-tesla\cli.py" apply --port 1015
```
*Что происходит:*
- В ядро WSL2 загружается таблица `herdr_filter` в `nftables`.
- Трафик вне `lo` перекрывается.
- Настраивается `/etc/profile.d/herdr_claude_env.sh`.
- Прописывается автозагрузка в `/etc/wsl.conf`.

### 3. Полный аудит изоляции и тест утечек
```powershell
python "c:\Users\f\Documents\antigravity\delightful-tesla\cli.py" test --port 1015
```
Или вывод в формате JSON для программного разбора:
```powershell
python "c:\Users\f\Documents\antigravity\delightful-tesla\cli.py" test --port 1015 --json
```

### 4. Автоматический конвейер в одну команду (Full Setup)
```powershell
python "c:\Users\f\Documents\antigravity\delightful-tesla\cli.py" full-setup
```

### 5. Снятие изоляции (возврат прямого выхода Direct IP)
```powershell
python "c:\Users\f\Documents\antigravity\delightful-tesla\cli.py" remove
# Для полной очистки включая автозагрузку:
python "c:\Users\f\Documents\antigravity\delightful-tesla\cli.py" remove --full
```

---

## Прямые низкоуровневые Bash-команды для агента в WSL

Если требуется выполнить проверку или настройку напрямую через bash/wsl:

### Проверка статуса сетевой тюрьмы
```bash
wsl -u root -e bash -c "nft list table inet herdr_filter 2>/dev/null || iptables -L HERDR_ISOLATE -n"
```

### Тест 1: Прямая утечка IP (Direct Leak Test)
Должен вернуть ошибку соединения (код != 0):
```bash
wsl -e bash -c "curl -s --connect-timeout 2 --noproxy '*' https://ifconfig.me"
```
*Если вернулся IP-адрес — тревога: обнаружена утечка сети!*

### Тест 2: Выход через сокет SOCKS5 1015 (Remote DNS)
Должен вернуть внешний IP прокси:
```bash
wsl -e bash -c "curl -s --connect-timeout 4 --socks5-hostname 127.0.0.1:1015 https://ifconfig.me"
```

### Тест 3: Проверка защиты от обходного порта 2080
Должен вернуть отказ в подключении:
```bash
wsl -e bash -c "curl -s --connect-timeout 2 -x http://127.0.0.1:2080 https://ifconfig.me"
```

---

## Критерии и статусы вердикта

| Статус | Описание | Действие агента |
|---|---|---|
| **ISOLATED** | Прямой выход заблокирован, выход через 1015 успешен, DNS удаленный, обход закрыт. | Сеть безопасна для бенчмарков и агентов. Можно продолжать работу. |
| **LOCKDOWN** | Ядро изолировано, но порт 1015 оффлайн. Все сетевые пакеты дропаются ядром (Killswitch). | Запустить локальный прокси-сервер на порту 1015 (или проверить соединение). |
| **LEAK** | Прямой выход без прокси успешен! | Срочно применить изоляцию: `python cli.py apply`. |
| **DIRECT** | Ограничение выключено, свободный интернет-доступ. | При необходимости включить изоляцию через `apply`. |
