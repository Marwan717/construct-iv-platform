# FieldView — Construction Flight Archive

A Flask portal for securely browsing construction drone footage and project documents. It includes admin-managed accounts, protected media access, flight metadata, tags, audit logging, and a small authenticated JSON API.

## Run locally

Requires Python 3.10 or newer.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY="replace-with-a-long-random-secret"
export ADMIN_USERNAME="admin"
export ADMIN_PASSWORD="replace-with-a-strong-password"
python app.py
```

Open `http://127.0.0.1:5000`. On first run, the app creates `instance/drone_portal.db` and an administrator account from the environment variables. `ADMIN_PASSWORD` is required when creating the initial administrator; the app will stop with a clear error if it is missing.

## Configuration

- `SECRET_KEY`: Flask session signing key; set a stable secret in production.
- `ADMIN_USERNAME`, `ADMIN_PASSWORD`: initial administrator account. Existing accounts are not overwritten.
- `PORT`: HTTP port (default `5000`).
- `MAX_CONTENT_LENGTH`: maximum request/upload size in bytes (default 8 GiB).
- `SESSION_COOKIE_SECURE=1`: enable secure cookies when HTTPS is configured.

Uploads are stored under `static/uploads/` and SQLite data under `instance/`. Back up both directories. The app sets `SESSION_COOKIE_SECURE` to false by default for local HTTP development; turn it on behind HTTPS.

## Features

- Date, project, direction, tag, and text filtering for flights.
- Protected video streaming and document downloads with SHA-256 duplicate detection.
- Admin account management, flight uploads, and document uploads.
- Authenticated endpoints at `/api/health`, `/api/flights`, `/api/tags`, `/api/projects`, `/api/stats`, and admin-only `/api/audit`.

For a production deployment, run behind a production WSGI server and HTTPS reverse proxy, use a persistent upload volume, and configure secrets and upload size limits for the host.

## Read-only project sharing

Sign in as an administrator and open **Admin → Project viewer access**. Create a project viewer by name, or generate access for a project that already has uploaded footage/documents. The admin page displays a shareable viewer URL and a unique four-letter code; regenerating the code invalidates the previous URL/code. Visitors can either open the share URL or enter the code at `/`.

The project viewer is read-only. Visitors can browse and stream only the project attached to that code, download media/documents, and copy the project link/code from **Project Access**. Upload a site map in Admin to use it as the viewer overview; map pins are joined in placement order to draw the critical path.

## Multi-flight uploads and site maps

On the admin page, **Upload flights** accepts multiple video files in one submission. Set the project and date once, then assign a direction and title to each selected file. Under **Site overview map & direction pins**, upload a JPG, PNG, or WEBP plan/aerial image, then click the map to place and label compass pins. The viewer displays that map and pins for the project; if no map is uploaded, it keeps the schematic overview.
