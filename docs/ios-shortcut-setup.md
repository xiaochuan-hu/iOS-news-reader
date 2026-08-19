# iPhone Shortcuts setup

V4 keeps generation and playback independent. The phone's current timezone owns local 05:00, and
current-location weather is fetched by the phone at actual playback time.

## A. Morning Brief Refresh at local 05:00

Create or retain `Morning Brief Refresh`:

1. Current Date.
2. Format Date with custom `yyyy-MM-dd`.
3. URL: `https://YOUR_HOST/v1/briefings/daily-refresh`.
4. Get Contents of URL: POST, Bearer Authorization header, JSON body with `local_date` set to the
   formatted date.
5. Do not add Speak Text or any weather action.

The complete body remains:

~~~json
{"local_date":"2026-08-20"}
~~~

There is no `weather_location`. Configure a daily 05:00 Personal Automation to run this Shortcut
immediately.

## B. Morning Brief after an alarm

Retain the device-hour guard (05:00–08:59), then:

1. GET `https://YOUR_HOST/v1/briefings/latest/text` with the existing Bearer header.
2. Use Shortcuts' `Get Current Weather` at `Current Location`.
3. Extract City, Condition, Daily Low, Daily High, and Precipitation Probability.
4. Use Text actions to construct a deterministic sentence such as: “最后来看一下天气。今天滨州
   预计多云，最低气温二十二度，最高气温三十一度，降水概率百分之四十。”
5. Combine the cached backend text, a line break, and the weather sentence.
6. Speak the combined text with the preferred Chinese voice.

Create or retain the `Alarm -> Is Stopped -> Run Immediately -> Morning Brief` automation. Do not
put POST daily-refresh or POST generate in this playback Shortcut. Repeated playback GETs produce
zero source, GitHub, or DeepSeek requests.

Because weather is obtained at playback rather than 05:00 generation, it follows the phone's actual
location and freshest Apple Weather result. The backend never receives location data and never
contacts a weather or geocoding API.

## Authentication, timing, and verification

Keep `LLM_API_KEY` off the phone. Use the existing API token in a Bearer header over HTTPS. At
05:00:05 a briefing may still be generating, so cache-only GET can correctly return the prior
complete briefing; a 05:02/05:05 wake alarm reduces this race.

Verify: first refresh is generated, duplicate refresh is already_claimed, repeated GET text is
identical, weather changes with the device's current location, the final spoken order is backend
date/news/GitHub followed by iPhone weather, and no API request occurs outside the hour guard.
