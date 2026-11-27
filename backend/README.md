# Hello Guys Backend

See the root [README.md](../README.md) for the full architecture overview, routes, and setup instructions, and [docs/WEBSITE_CHAT.md](../docs/WEBSITE_CHAT.md) for the public chat API.

```bash
# From repo root: start web server, Dramatiq worker, scheduler, and Google sync
./run.sh

# Run Django check and pytest from backend/
../.venv/bin/python manage.py check
../.venv/bin/python -m pytest
```
