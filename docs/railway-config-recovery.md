# Railway deployment recovery

The current `web` Railway deployment fails before Docker build with `config_error: Complete result exceeds size limit`.

## Safety constraints

- The production service has a persistent volume mounted on `/data`, containing procurement records and downloaded files. Do not delete or detach it.
- A prior version is running while new deployments fail. Avoid disrupting it until a replacement has passed a health check.
- Historic updates are held in `*_PY`, `*_GZ`, `*_B64_*`, and `*_HEX_*` environment variables. Do not bulk-delete these until they have been migrated and validated.
- The GitHub repository's `app.py` alone does not reproduce the full live patched runtime.

## Recovery

1. Back up a complete copy of all non-secret program patch variables, and persist their source as files under version control (exclude credentials and tokens).
2. Assemble a single startup module from those patches in historical application order, and test it in a non-production environment.
3. Move the validated program code into the Docker image, then remove only the confirmed migrated patch variables.
4. Trigger a fresh Docker build, verify `/health` and live scanning progress, and keep the existing volume attached.
