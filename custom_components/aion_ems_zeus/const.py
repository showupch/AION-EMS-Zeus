"""Constants for AION EMS."""

DOMAIN = "aion_ems_zeus"
NAME = "AION EMS"
VERSION = "14.7.0"

PLATFORMS = ["sensor"]

CONF_DEVELOPER_MODE = "developer_mode"

# Registry
SERVICE_EXPORT_REGISTRY = "export_registry"
SERVICE_BACKUP_REGISTRY = "backup_registry"
SERVICE_RESTORE_LATEST_BACKUP = "restore_latest_backup"
SERVICE_RELOAD_REGISTRY = "reload_registry"

# Discovery / pipeline
SERVICE_REFRESH_ENTITY_DISCOVERY = "refresh_entity_discovery"
SERVICE_REFRESH_INTEGRATION_HUB = "refresh_integration_hub"
SERVICE_REFRESH_DATA_BUS = "refresh_data_bus"
SERVICE_CAPTURE_DATA_LAKE_SNAPSHOT = "capture_data_lake_snapshot"
SERVICE_REFRESH_DATA_LAKE_SUMMARY = "refresh_data_lake_summary"
SERVICE_REFRESH_KNOWLEDGE_ENGINE = "refresh_knowledge_engine"
SERVICE_REFRESH_BRIEFING_CENTER = "refresh_briefing_center"
SERVICE_REFRESH_QUESTION_LIBRARY = "refresh_question_library"
SERVICE_REFRESH_CAPABILITY_REPORT = "refresh_capability_report"

# Device import
SERVICE_IMPORT_DISCOVERY_CANDIDATE = "import_discovery_candidate"
SERVICE_IMPORT_RECOMMENDED_DEVICES = "import_recommended_devices"
SERVICE_REMOVE_AUTO_IMPORTED_DEVICES = "remove_auto_imported_devices"

# Helios migration
SERVICE_HELIOS_MIGRATION_ANALYZE = "helios_migration_analyze"
SERVICE_HELIOS_MIGRATION_PREVIEW = "helios_migration_preview"
SERVICE_HELIOS_SMART_IMPORT_REPORT = "helios_smart_import_report"
SERVICE_APPLY_HELIOS_MIGRATION_PREVIEW = "apply_helios_migration_preview"

# Devices / rooms / groups
SERVICE_ADD_DEVICE = "add_device"
SERVICE_UPDATE_DEVICE = "update_device"
SERVICE_REMOVE_DEVICE = "remove_device"
SERVICE_ADD_ROOM = "add_room"
SERVICE_UPDATE_ROOM = "update_room"
SERVICE_REMOVE_ROOM = "remove_room"
SERVICE_ADD_GROUP = "add_group"
SERVICE_UPDATE_GROUP = "update_group"
SERVICE_REMOVE_GROUP = "remove_group"

# Legacy/no-op compatibility aliases
SERVICE_ADD_DEVICE_PREVIEW = "add_device_preview"
SERVICE_REFRESH_FORECAST = "refresh_forecast"
SERVICE_REFRESH_OPTIMIZER_PREVIEW = "refresh_optimizer_preview"
SERVICE_REFRESH_SCHEDULER_PREVIEW = "refresh_scheduler_preview"
SERVICE_REFRESH_LEARNING_PREVIEW = "refresh_learning_preview"

REGISTRY_STORAGE_KEY = f"{DOMAIN}.registry"
REGISTRY_STORAGE_VERSION = 2
DATA_LAKE_STORAGE_KEY = f"{DOMAIN}.data_lake"
DATA_LAKE_STORAGE_VERSION = 1

DATA_LAKE_AUTO_CAPTURE_MINUTES = 30

SERVICE_REFRESH_ENERGY_MAPPING = "refresh_energy_mapping"

SERVICE_REFRESH_DEVICE_IMPORT_REVIEW = "refresh_device_import_review"

SERVICE_IMPORT_REVIEWED_DEVICES = "import_reviewed_devices"

SERVICE_REFRESH_ENERGY_FLOW = "refresh_energy_flow"

SERVICE_DEVICE_MANAGER_BUILD_REVIEW = "device_manager_build_review"

SERVICE_DEVICE_MANAGER_IMPORT_READY = "device_manager_import_ready"

SERVICE_DEVICE_MANAGER_REMOVE_DEVICE = "device_manager_remove_device"

PANEL_URL_PATH = "aion-ems"
ENERGY_FLOW_PANEL_URL_PATH = "aion-ems-energy-flow"
COMMAND_CENTER_PANEL_URL_PATH = "aion-ems-zeus-kiosk"

SERVICE_LIFECYCLE_STATUS = "lifecycle_status"

SERVICE_TEST_ENTITY_MAPPING = "test_entity_mapping"
SERVICE_SAVE_ENTITY_MAPPING = "save_entity_mapping"
SERVICE_CLEAR_ENTITY_MAPPING = "clear_entity_mapping"

SERVICE_SAVE_WEATHER_SOURCE = "save_weather_source"
SERVICE_CLEAR_WEATHER_SOURCE = "clear_weather_source"

SERVICE_SAVE_TARIFF_SETTINGS = "save_tariff_settings"
SERVICE_CLEAR_TARIFF_SETTINGS = "clear_tariff_settings"
SERVICE_SAVE_BATTERY_CAPACITY = "save_battery_capacity"
SERVICE_SAVE_HOME_PROFILE = "save_home_profile"
SERVICE_CLEAR_BATTERY_CAPACITY = "clear_battery_capacity"
SERVICE_SET_DATA_EPOCH = "set_data_epoch"
SERVICE_CLEAR_DATA_EPOCH = "clear_data_epoch"

SERVICE_SAVE_NOTIFICATION_SETTINGS = "save_notification_settings"
SERVICE_TEST_NOTIFICATION = "test_notification"

# Integration Hub v10.18
SERVICE_SAVE_PLUGIN_SETTINGS = "save_plugin_settings"
SERVICE_TEST_PLUGIN = "test_plugin"
SERVICE_CREATE_NAS_BACKUP = "create_nas_backup"
SERVICE_REFRESH_PLUGIN_DISCOVERY = "refresh_plugin_discovery"

# QA diagnostics
SERVICE_RUN_QA_HEALTH_CHECK = "run_qa_health_check"

SERVICE_REGISTER_BATTERY_PROFILE = "register_battery_profile"
SERVICE_CLEAR_BATTERY_PROFILE = "clear_battery_profile"

# Opt-in Home Assistant Energy import
SERVICE_PREVIEW_HA_ENERGY_IMPORT = "preview_home_assistant_energy_import"
SERVICE_APPLY_HA_ENERGY_IMPORT = "apply_home_assistant_energy_import"
