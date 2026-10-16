# Архитектура изолированных тестовых сред Вендора C

> [!NOTE]
> Документ обновлен и переименован в соответствии со стандартом изоляции сред Вендора C в исследовательских целях:
> См. актуальный мастер-документ: [`VENDOR_C_ENVIRONMENT_OVERVIEW.md`](VENDOR_C_ENVIRONMENT_OVERVIEW.md).

Все материалы и архитектурные решения подготовлены строго в исследовательских и образовательных целях для изучения механизмов изоляции процессов в Linux (WSL2/WSLg), криптографических подсистем Chromium OSCrypt/GNOME Keyring, диспетчера учетных данных Windows Credential Manager и сетевой маршрутизации приложений через локальные сокеты без взаимного влияния и утечек данных.

Подробное описание:
- Пошаговый гайдлайн: [`new_claude/howToCreate.md`](new_claude/howToCreate.md)
- Обзор архитектуры: [`VENDOR_C_ENVIRONMENT_OVERVIEW.md`](VENDOR_C_ENVIRONMENT_OVERVIEW.md)
- Изоляция GUI в WSL2: [`VENDOR_C_WSL_GUI_ENVIRONMENTS.md`](VENDOR_C_WSL_GUI_ENVIRONMENTS.md)
- Изоляция CLI на Windows: [`VENDOR_C_CLI_ENVIRONMENTS.md`](VENDOR_C_CLI_ENVIRONMENTS.md)
