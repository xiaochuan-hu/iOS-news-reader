from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import httpx

AZURE_OUTPUT_FORMAT = "audio-48khz-96kbitrate-mono-mp3"
SSML_NAMESPACE = "http://www.w3.org/2001/10/synthesis"
MSTTS_NAMESPACE = "https://www.w3.org/2001/mstts"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"


class AzureSpeechTTSProvider:
    name = "azure"

    def __init__(
        self,
        *,
        key: str,
        region: str,
        voice: str,
        style: str,
        rate: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = key
        self._region = region
        self._voice = voice
        self._style = style
        self._rate = rate
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    @property
    def cache_identity(self) -> Mapping[str, str]:
        return {
            "provider": self.name,
            "voice": self._voice,
            "style": self._style,
            "rate": self._rate,
            "output_format": AZURE_OUTPUT_FORMAT,
        }

    async def synthesize(self, text: str, output_path: Path) -> None:
        url = (
            f"https://{self._region}.tts.speech.microsoft.com/"
            "cognitiveservices/v1"
        )
        headers = {
            "Ocp-Apim-Subscription-Key": self._key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": AZURE_OUTPUT_FORMAT,
            "User-Agent": "morning-intelligence",
        }
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            response = await client.post(url, headers=headers, content=self._ssml(text))
            response.raise_for_status()
        await asyncio.to_thread(output_path.write_bytes, response.content)

    def _ssml(self, text: str) -> bytes:
        ET.register_namespace("", SSML_NAMESPACE)
        ET.register_namespace("mstts", MSTTS_NAMESPACE)
        speak = ET.Element(
            f"{{{SSML_NAMESPACE}}}speak",
            {
                "version": "1.0",
                f"{{{XML_NAMESPACE}}}lang": "zh-CN",
            },
        )
        voice = ET.SubElement(
            speak,
            f"{{{SSML_NAMESPACE}}}voice",
            {"name": self._voice},
        )
        parent = voice
        if self._style:
            parent = ET.SubElement(
                voice,
                f"{{{MSTTS_NAMESPACE}}}express-as",
                {"style": self._style},
            )
        prosody = ET.SubElement(
            parent,
            f"{{{SSML_NAMESPACE}}}prosody",
            {"rate": self._rate},
        )
        prosody.text = text
        return cast(bytes, ET.tostring(speak, encoding="utf-8", xml_declaration=True))
