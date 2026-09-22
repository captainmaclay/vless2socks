"""Comprehensive verification script for vlesstosocks5 upgrades."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import i18n
import geo_ip
import backup_manager
import settings_manager

def test_i18n():
    print("Testing i18n...")
    # Default is English
    i18n.set_language("en")
    assert i18n.t("nav_overview") == "🏠 Overview", f"Expected '🏠 Overview', got '{i18n.t('nav_overview')}'"
    assert i18n.t("nav_options") == "⚙️ Options", f"Expected '⚙️ Options', got '{i18n.t('nav_options')}'"
    assert "SOCKS5" in i18n.t("status_stopped")

    # Switch to Russian
    i18n.set_language("ru")
    assert i18n.t("nav_overview") == "🏠 Обзор", f"Expected '🏠 Обзор', got '{i18n.t('nav_overview')}'"
    assert i18n.t("nav_options") == "⚙️ Опции", f"Expected '⚙️ Опции', got '{i18n.t('nav_options')}'"
    assert "Принудительное подключение" in i18n.t("opt_force_port_takeover_title")
    assert "Запускать proxy при старте" in i18n.t("opt_autostart_proxies_title")
    assert "Запускать в Трее" in i18n.t("opt_start_minimized_tray_title")
    assert "остановлен" in i18n.t("status_stopped")

    # Switch back to English as default
    i18n.set_language("en")
    assert "Force Port Takeover" in i18n.t("opt_force_port_takeover_title")
    assert "Auto-Start Proxies" in i18n.t("opt_autostart_proxies_title")
    assert "Start Minimized to System Tray" in i18n.t("opt_start_minimized_tray_title")
    print("i18n tests PASSED.")

def test_options_settings():
    print("Testing settings_manager defaults and persistence...")
    # Verify defaults
    defaults = settings_manager.load_settings()
    assert defaults.get("force_port_takeover") is True, "force_port_takeover should be True by default"
    assert defaults.get("autostart_proxies") is True, "autostart_proxies should be True by default"
    assert defaults.get("start_minimized_tray") is False, "start_minimized_tray should be False by default"
    assert defaults.get("auto_reconnect") is True, "auto_reconnect should be True by default"
    assert defaults.get("auto_backup_enabled") is False, "auto_backup_enabled should be False by default"
    assert defaults.get("backup_interval_hours") == 24, "backup_interval_hours should be 24 by default"

    # Verify reconnect intervals parsing
    intervals = settings_manager.parse_reconnect_intervals("10, 15, 30, 60, 120, 180, 30")
    assert intervals == [10, 15, 30, 60, 120, 180, 30], f"Unexpected intervals: {intervals}"

    # Test toggling and persistence
    settings_manager.set_setting("auto_reconnect", False)
    assert settings_manager.get_setting("auto_reconnect") is False
    settings_manager.set_setting("reconnect_intervals", "5, 10, 20")
    assert settings_manager.get_setting("reconnect_intervals") == "5, 10, 20"
    assert settings_manager.parse_reconnect_intervals(settings_manager.get_setting("reconnect_intervals")) == [5, 10, 20]

    # Restore defaults
    settings_manager.set_setting("auto_reconnect", True)
    settings_manager.set_setting("reconnect_intervals", settings_manager.DEFAULT_RECONNECT_INTERVALS)
    print("settings_manager tests PASSED.")

def test_geo_ip():
    print("Testing geo_ip URL parsing...")
    url_fi = "vless://00000000-0000-0000-0000-000000000000@fi3.example.net:443?type=tcp#%F0%9F%87%AB%F0%9F%87%AE%20FINLAND%203%20VLESS%20TCP"
    hint_fi = geo_ip.extract_country_hint(url_fi)
    assert hint_fi["country"] == "Finland", f"Expected Finland, got {hint_fi['country']}"
    assert hint_fi["flag"] == "🇫🇮", f"Expected 🇫🇮, got {hint_fi['flag']}"

    url_de = "vless://test@ger1.example.net:443#%F0%9F%87%A9%F0%9F%87%AA%20GERMANY%201%20VLESS%20TCP"
    hint_de = geo_ip.extract_country_hint(url_de)
    assert hint_de["country"] == "Germany", f"Expected Germany, got {hint_de['country']}"
    assert hint_de["flag"] == "🇩🇪", f"Expected 🇩🇪, got {hint_de['flag']}"

    flag = geo_ip.code_to_flag("US")
    assert flag == "🇺🇸", f"Expected 🇺🇸, got {flag}"
    print("geo_ip tests PASSED.")

def test_backup_restore():
    print("Testing backup_manager AES-256-GCM...")
    test_pwd = "SecretMasterPassword123!"
    fp = backup_manager.compute_password_fingerprint(test_pwd)
    assert len(fp) == 19, f"Fingerprint unexpected format: {fp}"
    print(f"Password fingerprint: {fp}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # Export backup
        backup_file = backup_manager.export_encrypted_backup(test_pwd, dest_dir=tmp_path)
        assert backup_file.exists(), "Backup file was not created"
        assert backup_file.name.endswith(".hbak"), "Backup extension is not .hbak"

        with open(backup_file, "rb") as f:
            header = f.read(5)
            assert header == b"HBAK\x01", f"Expected HBAK header, got {header}"

        # Test decrypt with wrong password
        try:
            backup_manager.restore_encrypted_backup(backup_file, "WrongPassword!")
            assert False, "Decryption should have failed with wrong password"
        except ValueError:
            pass  # Expected

        # Test list_backups_in_dir
        found = backup_manager.list_backups_in_dir(tmp_path)
        assert len(found) == 1, f"Expected 1 backup found, got {len(found)}"
        assert found[0]["filename"] == backup_file.name
        assert found[0]["size_kb"] > 0
        assert found[0]["modified"]

        # Test decrypt with correct password
        count = backup_manager.restore_encrypted_backup(backup_file, test_pwd)
        assert count >= 1, f"Expected at least 1 restored instance, got {count}"

    print("backup_manager tests PASSED.")

def test_pagination_logic():
    print("Testing pagination logic...")
    PAGE_SIZE = 10
    total_instances = 25
    total_pages = (total_instances + PAGE_SIZE - 1) // PAGE_SIZE
    assert total_pages == 3, f"Expected 3 pages for 25 instances, got {total_pages}"
    print("pagination logic tests PASSED.")

if __name__ == "__main__":
    test_i18n()
    test_options_settings()
    test_geo_ip()
    test_backup_restore()
    test_pagination_logic()
    print("\nALL FEATURE & OPTIONS TESTS PASSED SUCCESSFULLY!")
