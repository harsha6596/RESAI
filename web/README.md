# RDR Support Console demo

This is a buildless synthetic dashboard for the browser agent. It reads transaction records from `grid_data/rdr_grid_static_data.json`.

From the repository root, run:

```bash
python3 -m http.server 8765
```

Open [http://localhost:8765/web/](http://localhost:8765/web/).

Demo credentials:

- Email: `support@rdr.company.com`
- Password: `RDR-Support-2026!`

The server must run from the repository root because the page fetches the JSON at `/grid_data/rdr_grid_static_data.json`.
