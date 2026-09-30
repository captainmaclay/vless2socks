# WSL2 Fail-Safe Network Isolation Guard & Socket 1015 Strict Restriction Tool

Утилита и исследовательский набор инструментов для гарантированной изоляции сетевого стека WSL2 на выделенный сокет локального прокси (`127.0.0.1:1015` SOCKS5 / `127.0.0.1:11015` HTTP CONNECT) с удаленным DNS-разрешением (`socks5h`) и защитой от утечек реального IP.

Разработано на основе архитектуры **Herdr Control Center** (`D:\My files\herdrControlGui`).

---

## 🏗️ Архитектурные принципы и устройство файрвола

### 1. Ядровая сетевая тюрьма (Linux Kernel Netfilter / nftables)
В WSL2 сетевой стек изолируется непосредственно в ядре Linux через `nftables` (с автоматическим fallback на `iptables`/`ip6tables`):
```nft
table inet herdr_filter {
    chain output {
        type filter hook output priority filter; policy accept;
        ip daddr 127.0.0.1 tcp dport 2080 counter reject
        oifname { "lo", "loopback0" } counter accept
        counter reject
    }
}
```
- **Разрешен только loopback:** Правило `oifname { "lo", "loopback0" } counter accept` пропускает трафик только внутри локальной петли (включая интерфейс `loopback0`, используемый в режиме `mirrored` сетевого стека WSL2).
- **Блокировка прямых подключений (Direct Egress):** Все пакеты, направленные на интерфейсы `eth0` или прямые IP-адреса интернета, отбрасываются правилом `counter reject` с отправкой ICMP port-unreachable.
- **Блокировка обходных каналов:** Попытки локальных агентов подключиться к сторонним отладочным прокси (например, сокет `127.0.0.1:2080`) отклоняются отдельным правилом `ip daddr 127.0.0.1 tcp dport 2080 counter reject`.

### 2. Защита от DNS-утечек (socks5h Remote DNS)
При обычном обращении через SOCKS5 резолвинг доменных имен может осуществляться хостовым DNS через прямой сокет (вызывая утечку DNS). В решении используется протокол `socks5h://`:
```bash
export ALL_PROXY='socks5h://127.0.0.1:1015'
export HTTPS_PROXY='http://127.0.0.1:11015'
```
Суффикс `h` гарантирует, что доменные имена передаются в туннель в неразрешенном виде и опрашиваются удаленным сервером прокси.

### 3. Автономная персистентность (Boot Containment Guard)
Даже если GUI закрыт, или WSL2/Windows перезагружены:
- Конфигурация сохраняется в `/etc/nftables.conf`.
- Скрипт инициализации `/usr/local/bin/herdr_boot_isolation.sh` регистрируется в `/etc/wsl.conf` в блоке `[boot] command=...`.
- Служба `nftables` активируется (`systemctl enable nftables`).
- С первой же миллисекунды запуска подсистемы Linux сетевой контур герметичен.

### 4. Полная свобода хоста Windows
В отличие от наивных реализаций, блокирующих порты в Windows Firewall, в данном решении Windows-хост (`node.exe`, IDE, браузеры, медиа-сервисы) работает в режиме прямого выхода (`Direct IP`), а любые блокирующие правила хоста с именем `Claude Node Isolate` принудительно удаляются.

---

## 🚀 Быстрый запуск

Утилита написана на чистом Python 3 со стандартной библиотекой (zero external dependencies):

```powershell
# 1. Проверить статус WSL
python cli.py check-wsl

# 2. Применить изоляцию на сокет 1015
python cli.py apply --port 1015

# 3. Запустить полный цикл тестов и аудита утечек
python cli.py test --port 1015

# 4. Выполнить проверку -> настройку -> тест в одну команду
python cli.py full-setup

# 5. Снять изоляцию при необходимости прямого доступа
python cli.py remove
```

---

## 🧪 Этапы тестирования (Isolation Diagnostic Suite)

1. **TCP Handshake:** Замер скорости установления TCP соединения с сокетами `127.0.0.1:1015` (SOCKS5) и `127.0.0.1:11015` (HTTP CONNECT).
2. **Kernel Filter Inspection:** Проверка присутствия таблицы `inet herdr_filter` и цепочки `output` в ядре WSL2.
3. **Profile Environment Check:** Проверка наличия переменных `ALL_PROXY=socks5h://` в `/etc/profile.d/herdr_claude_env.sh`.
4. **Direct IP Leak Test:** Попытка прямого соединения `curl --noproxy '*' https://ifconfig.me`. Ожидаемый результат: отклонено ядром Linux.
5. **SOCKS5 Remote DNS Test:** Выход через `curl --socks5-hostname 127.0.0.1:1015 https://ifconfig.me`. Ожидаемый результат: получение IP-адреса прокси.
6. **Bypass Protection Test:** Попытка обхода через сокет `2080`. Ожидаемый результат: соединение отклонено.
7. **Killswitch Verification:** Подтверждение Zero Leaks при оффлайн-порте.

---

## 🤖 Поддержка для Gemini / Antigravity Skills

Скилл зарегистрирован в глобальном каталоге:
`C:\Users\f\.gemini\config\skills\wsl-proxy-isolation\SKILL.md`

Модели Gemini и Antigravity автоматически обнаруживают скилл `wsl-proxy-isolation` и могут проводить экспресс-настройку и диагностику окружения WSL2 автономно.
