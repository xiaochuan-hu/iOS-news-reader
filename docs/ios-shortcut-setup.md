# iPhone Shortcut setup

The project does not require or ship an iOS app. Use one ordinary Shortcut plus one Personal Automation. Test the flow once while the phone is locked before relying on it as a morning routine.

## Prerequisites

1. Deploy the API behind an HTTPS URL reachable by the iPhone.
2. Set a long random `MORNING_API_TOKEN` on the server.
3. Confirm the endpoint from another device:

```bash
curl -H "Authorization: Bearer $MORNING_API_TOKEN" \
  https://YOUR_HOST/v1/briefings/latest/text
```

## Create “Morning Brief”

In Shortcuts, create a new shortcut named `Morning Brief` with these actions in this order:

1. **URL** — `https://YOUR_HOST/v1/briefings/latest/text`
2. **Get Contents of URL**
   - Method: `GET`
   - Headers: add `Authorization` with value `Bearer YOUR_TOKEN`
   - Timeout: use the system default; the endpoint is the cached fast path.
3. **If** the result has any value:
   - **Speak Text** — speak the result from Get Contents of URL.
   - Suggested options: wait until finished; choose a preferred Chinese voice and a comfortable rate.
4. **Otherwise**:
   - **Wait** — 2 seconds.
   - Add a second **Get Contents of URL** with the same URL and header.
   - **Speak Text** — speak the second result.

Header auth is preferred. If the Shortcuts version on the device makes custom headers impractical, use `https://YOUR_HOST/v1/briefings/latest/text?token=YOUR_TOKEN`. Query tokens are more likely to be captured by proxy/access logs, so use HTTPS and configure the proxy not to log query strings.

For a forced refresh instead, use `POST https://YOUR_HOST/v1/briefings/generate` with JSON body `{"mode":"wake","target_minutes":5,"incremental_refresh":true}` and extract `spoken_text` from the dictionary response. The GET path normally starts speaking sooner.

## Create the Personal Automation

1. Open **Shortcuts → Automation** and tap **+**.
2. Create a **Personal Automation**.
3. Choose **Alarm**.
4. Select **Is Stopped**.
5. Select **Wake-Up** or the specific alarm you use.
6. Choose **Run Immediately**. Disable confirmation prompts if your iOS version offers that choice.
7. Add **Run Shortcut** and select `Morning Brief`.
8. Save the automation.

The resulting path is:

```text
Alarm Is Stopped -> Run Morning Brief -> GET cached text -> Speak Text
```

## Verification checklist

- Run `Morning Brief` manually with the phone on Wi-Fi and cellular data.
- Lock the phone and run it again; confirm Speak Text is audible at the intended volume.
- Stop the selected test alarm and confirm the automation runs without asking permission.
- Temporarily stop the server and confirm the one-retry path behaves acceptably.
- Do not place API keys for the LLM in the Shortcut; it receives only the service access token.
