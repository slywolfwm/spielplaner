# Cloudflare-Proxy

Der Worker veröffentlicht die auf Streamlit Community Cloud laufende App unter
`https://spielplaner.handamball.de`. HTTP-Anfragen, Weiterleitungen und
WebSocket-Verbindungen werden an
`https://spielplaner-handamball.streamlit.app` weitergereicht.

## Bereitstellung

1. In Streamlit Community Cloud die App mit der URL
   `spielplaner-handamball.streamlit.app` veröffentlichen.
2. Falls für `spielplaner.handamball.de` bereits ein DNS-Eintrag existiert,
   diesen vor dem ersten Worker-Deployment entfernen.
3. In GitHub unter **Settings → Secrets and variables → Actions** diese
   Repository-Secrets anlegen:

   - `CLOUDFLARE_API_TOKEN` mit den Berechtigungen `Workers Scripts: Edit` und
     `Workers Routes: Edit` für die Zone `handamball.de`
   - `CLOUDFLARE_ACCOUNT_ID` mit der Cloudflare-Konto-ID

4. Den Workflow **Cloudflare-Proxy bereitstellen** in GitHub Actions starten.

Alternativ kann der Worker auf einem von Wrangler unterstützten Rechner oder
in einer Linux-Shell manuell veröffentlicht werden:

   ```powershell
   cd cloudflare
   npm install
   npx wrangler login
   npm run deploy
   ```

Cloudflare legt den DNS-Eintrag und das Zertifikat für die konfigurierte Custom
Domain beim Deployment an. Falls Streamlit eine andere URL erhält, muss
`UPSTREAM_ORIGIN` in `wrangler.jsonc` vorher entsprechend geändert werden.
