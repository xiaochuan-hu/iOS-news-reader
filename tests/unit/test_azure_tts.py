from __future__ import annotations

from pathlib import Path

import httpx

from morning_intelligence.tts import AZURE_OUTPUT_FORMAT, AzureSpeechTTSProvider


async def test_azure_provider_uses_regional_rest_ssml_and_mp3_format(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["body"] = request.content
        return httpx.Response(200, content=b"ID3-fake-mp3")

    provider = AzureSpeechTTSProvider(
        key="unit-test-key",
        region="uksouth",
        voice="zh-CN-YunxiNeural",
        style="newscast",
        rate="+5%",
        timeout_seconds=3,
        transport=httpx.MockTransport(handler),
    )
    output = tmp_path / "segment.mp3"

    await provider.synthesize("研发进展 & 后续观察。", output)

    headers = captured["headers"]
    assert isinstance(headers, httpx.Headers)
    assert captured["url"] == (
        "https://uksouth.tts.speech.microsoft.com/cognitiveservices/v1"
    )
    assert headers["Ocp-Apim-Subscription-Key"] == "unit-test-key"
    assert AZURE_OUTPUT_FORMAT == "audio-48khz-96kbitrate-mono-mp3"
    assert headers["X-Microsoft-OutputFormat"] == AZURE_OUTPUT_FORMAT
    assert provider.cache_identity["output_format"] == AZURE_OUTPUT_FORMAT
    body = captured["body"]
    assert isinstance(body, bytes)
    assert b'voice name="zh-CN-YunxiNeural"' in body
    assert b'style="newscast"' in body
    assert b'rate="+5%"' in body
    assert b"&amp;" in body
    assert output.read_bytes() == b"ID3-fake-mp3"
