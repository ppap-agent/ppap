"""Paddle layout OCR client used by doc-cache pipeline."""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import requests
from ppap_core.settings import get_settings
from ppap_docops.files import parse_file_size

logger = logging.getLogger(__name__)


class PaddleStructureService:
    """Thin client for Paddle layout parsing API."""

    def __init__(self, host: str | None = None, token: str | None = None) -> None:
        settings = get_settings()
        self.host = (host or settings.get_ocr_paddle_url() or "").strip()
        self.token = (token or settings.get_ocr_paddle_token() or "").strip()

    def analyze_full_pdf(self, file_path: str) -> dict[str, Any]:
        if not self.host:
            logger.warning("Paddle OCR URL is empty; local OCR will return empty result")
            return {}

        max_size = parse_file_size(get_settings().MAX_FILE_SIZE)
        file_size = os.path.getsize(file_path)
        if file_size > max_size:
            logger.warning(
                "Skip Paddle OCR: file too large (%s > %s): %s",
                file_size,
                max_size,
                file_path,
            )
            return {
                "error": "file_too_large",
                "detail": "input file exceeds MAX_FILE_SIZE",
                "max_file_size": max_size,
                "file_size": file_size,
            }

        with open(file_path, "rb") as file:
            file_data = base64.b64encode(file.read()).decode("ascii")

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"

        payload = {
            "file": file_data,
            "fileType": 0,
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useTextlineOrientation": False,
            "useChartRecognition": False,
            "useSealRecognition": True,
            "useTableRecognition": True,
        }

        try:
            logger.info("Calling Paddle layout OCR: %s", file_path)
            response = requests.post(self.host, json=payload, headers=headers, timeout=300)
            response.raise_for_status()

            body = response.json()
            if isinstance(body, dict):
                result = body.get("result")
                if isinstance(result, dict) and "layoutParsingResults" in result and "dataInfo" in result:
                    return dict(result)

            logger.error("Unexpected Paddle response structure")
            return {}
        except Exception as exc:
            logger.error("Paddle OCR request failed: %s", exc)
            return {}
