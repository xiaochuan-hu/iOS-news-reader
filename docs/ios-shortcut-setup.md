# iPhone Shortcuts setup

V3 uses two independent Shortcuts automations. The phone's current system timezone controls the
schedule, so travel requires no Railway timezone change.

## A. Daily generation at local 05:00

Create a Shortcut named Morning Brief Refresh:

1. Add Current Date.
2. Add Format Date, choose Custom, and enter yyyy-MM-dd.
3. Add URL: https://YOUR_HOST/v1/briefings/daily-refresh.
4. Add Get Contents of URL:
   - Method: POST.
   - Header Authorization: Bearer YOUR_TOKEN.
   - Request Body: JSON.
   - local_date: the formatted date from step 2.
5. Optionally inspect/log the returned status while testing. Do not add Speak Text.

Create its Personal Automation:

1. Shortcuts -> Automation -> + -> Time of Day.
2. Select 05:00 and Daily.
3. Choose Run Immediately and disable confirmation if offered.
4. Add Run Shortcut -> Morning Brief Refresh.

The request is intentionally simple:

~~~json
{"local_date":"2026-08-19"}
~~~

No IANA timezone, JWT, signature, or Unix timestamp is required.

## B. Speak the cached briefing after an alarm

Keep or update the existing Morning Brief Shortcut:

1. Current Date.
2. Format Date with custom H (the device's 0–23 hour).
3. Convert the formatted hour to Number.
4. If the number is at least 5 and at most 8:
   - URL: https://YOUR_HOST/v1/briefings/latest/text.
   - Get Contents of URL with GET.
   - Header Authorization: Bearer YOUR_TOKEN.
   - If the response has a value, Speak Text using the preferred Chinese voice.
5. Otherwise, end the Shortcut without a request.

Create or retain the Personal Automation:

1. Alarm -> Is Stopped -> Any Alarm (or the desired Wake-Up alarm).
2. Choose Run Immediately.
3. Run Shortcut -> Morning Brief.

This flow is cache-only. Running it once, ten times or one hundred times creates zero additional
DeepSeek requests. Do not add POST /daily-refresh or POST /generate to the alarm Shortcut, and do
not retry generation if Speak Text is empty.

## Authentication and security

Use the existing MORNING_API_TOKEN only. Never put LLM_API_KEY on the phone. Bearer headers are
preferred; the existing query-token fallback remains compatible but may be exposed in proxy logs,
so use it only over HTTPS with query-string logging disabled.

## Timing behavior

At 05:00 the new briefing can still be in flight. Stopping an alarm at 05:00:05 may read yesterday's
complete cache. That is expected: GET neither waits nor calls the LLM, and the old cache remains
readable until the new briefing is fully saved. A 05:02/05:05 wake alarm, or generation a few
minutes before the wake alarm, reduces this race.

## Device verification

- Run Morning Brief Refresh once with a valid local_date and verify status generated.
- Run it again and verify already_claimed.
- Run Morning Brief repeatedly and confirm identical cached text and no extra provider call.
- Test both Shortcuts while the phone is locked and once on cellular data.
- Stop a test alarm between 05:00 and 08:59 and confirm speech.
- Stop a test alarm outside that window and confirm the Shortcut does not call the API.
- After a timezone change, confirm the Time of Day automation follows the phone's new local 05:00.
