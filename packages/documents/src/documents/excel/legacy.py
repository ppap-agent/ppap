"""One LibreOffice conversion path for binary Excel workbooks."""

from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET

from documents.word.legacy import LegacyDocConversionError, LegacyDocConversionLimits, _local_environment, _resolve_executable, _run_converter


def convert_excel(content: bytes, *, source_suffix: str, target_suffix: str, output_filter: str) -> bytes:
    limits = LegacyDocConversionLimits()
    if len(content) > limits.max_input_bytes:
        raise LegacyDocConversionError("input_too_large", "Excel原件超过本地转换大小限制")
    with TemporaryDirectory(prefix="ppap-office-") as directory:
        work = Path(directory)
        profile = work / "profile"
        user = profile / "user"
        user.mkdir(parents=True)
        (user / "registrymodifications.xcu").write_text(
            '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
            '<item oor:path="/org.openoffice.Office.Calc/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>1</value></prop></item></oor:items>',
            encoding="utf-8",
        )
        source = work / f"source.{source_suffix}"
        source.write_bytes(content)
        command = (
            _resolve_executable(None),
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--nolockcheck",
            f"-env:UserInstallation={profile.as_uri()}",
            "--convert-to",
            output_filter,
            "--outdir",
            str(work),
            str(source),
        )
        environment = dict(_local_environment(work))
        fonts = ET.Element("fontconfig")
        for directory in ("/usr/share/fonts", "/usr/local/share/fonts", "/System/Library/Fonts", "/Library/Fonts"):
            if Path(directory).is_dir():
                ET.SubElement(fonts, "dir").text = directory
        ET.SubElement(fonts, "cachedir").text = str(work / "font-cache")
        font_config = work / "fonts.conf"
        font_config.write_bytes(ET.tostring(fonts, encoding="utf-8", xml_declaration=True))
        environment["FONTCONFIG_FILE"] = str(font_config)
        code, _, _ = _run_converter(command, environment=environment, limits=limits)
        target = work / f"source.{target_suffix}"
        if code or not target.is_file():
            raise LegacyDocConversionError("conversion_failed", "LibreOffice未生成转换结果")
        if target.stat().st_size > limits.max_output_bytes:
            raise LegacyDocConversionError("output_too_large", "转换结果超过大小限制")
        return target.read_bytes()
