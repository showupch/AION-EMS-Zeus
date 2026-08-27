# AION EMS Zeus — HACS Foundation

Version: `14.8.0`

This repository is prepared for installation as a **HACS custom repository**.

## Repository

`https://github.com/showupch/AION-EMS-Zeus`

## Add Zeus to HACS

1. Make sure HACS is installed in Home Assistant.
2. Open **HACS → Integrations**.
3. Open the HACS menu and choose **Custom repositories**.
4. Add:
   `https://github.com/showupch/AION-EMS-Zeus`
5. Select category:
   **Integration**
6. Add the repository.
7. Open **AION EMS Zeus** in HACS and install it.
8. Restart Home Assistant.
9. Go to:
   **Settings → Devices & services → Add Integration**
10. Search for:
   **AION EMS Zeus**

## Frontend

The HACS-managed integration contains its required frontend module inside:

`custom_components/aion_ems_zeus/frontend/`

Zeus serves that bundled frontend through its Home Assistant integration endpoint.
The root `www/aion_ems_zeus` copy remains in the repository for backward-compatible
manual release installation, but HACS does not depend on it.

## Validation

The repository now contains:

- `hacs.json`
- `.github/workflows/validate.yml`
- `.github/workflows/hassfest.yml`
- `brand/icon.png`
- HACS-required manifest metadata:
  - domain
  - documentation
  - issue_tracker
  - codeowners
  - name
  - version

## Stable release status

Zeus v14.8.0 is a stable GitHub/HACS release. Existing HACS installations can
receive stable updates through the repository release channel.

## Important

The GitHub `main` branch must contain this HACS metadata and the matching Zeus
integration source. A release asset alone is not enough to make the repository
HACS-ready.
