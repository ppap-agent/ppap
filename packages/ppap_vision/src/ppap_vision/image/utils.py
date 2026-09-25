"""不负责文件下载的纯函数图像处理工具。"""

import base64
import io
import logging
import os
import tempfile
import zipfile
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _image_bytes_to_base64(image_content: bytes) -> str:
    """
    将图像内容转换为base64格式

    Args:
        image_content: 图像二进制内容

    Returns:
        base64编码的图像字符串
    """
    try:
        from PIL import Image

        # 使用PIL处理图像以确保格式正确
        image = Image.open(io.BytesIO(image_content))

        # 转换为RGB模式（处理RGBA和其他模式）
        if image.mode in ("RGBA", "LA", "P"):
            # 创建白色背景
            background = Image.new("RGB", (image.width, image.height), (255, 255, 255))
            if image.mode == "P":
                image = image.convert("RGBA")
            background.paste(image, mask=image.split()[-1] if image.mode == "RGBA" else None)
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")  # 调整图像大小以控制token使用
        max_size = 1024
        if max(image.size) > max_size:
            ratio = max_size / max(image.size)
            new_width = int(image.size[0] * ratio)
            new_height = int(image.size[1] * ratio)
            new_size = (new_width, new_height)
            image = image.resize(new_size, Image.Resampling.LANCZOS)

        # 转换为JPEG格式以减少文件大小
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85, optimize=True)
        image_bytes = output.getvalue()

        # 编码为base64
        base64_string = base64.b64encode(image_bytes).decode("utf-8")

        image.close()
        return f"data:image/jpeg;base64,{base64_string}"

    except Exception as e:
        logger.error(f"图像base64转换失败: {str(e)}")
        return ""


def _extract_images_from_pdf_bytes(file_content: bytes, **kwargs: Any) -> List[str]:
    """
    从PDF字节内容中提取图像

    Args:
        file_content: PDF文件的二进制内容
        **kwargs: 额外参数
            - render_all_pages: 是否渲染所有页面为图像 (默认 False)
            - specific_pages: 指定要渲染的页面索引列表 (0-based)

    Returns:
        List[str]: base64编码的图像数组
    """
    try:
        import fitz  # PyMuPDF

        images = []

        # 打开PDF文档
        pdf_stream = io.BytesIO(file_content)
        doc = fitz.open(stream=pdf_stream.read(), filetype="pdf")

        render_all_pages = kwargs.get("render_all_pages", False)
        specific_pages = kwargs.get("specific_pages", None)

        if specific_pages is not None:
            # 渲染指定页面
            for page_num in specific_pages:
                if 0 <= page_num < doc.page_count:
                    page = doc[page_num]
                    mat = fitz.Matrix(1.5, 1.5)  # 1.5倍缩放
                    pix = page.get_pixmap(matrix=mat)
                    img_data = pix.tobytes("png")

                    base64_image = _image_bytes_to_base64(img_data)
                    if base64_image:
                        images.append(base64_image)
        elif render_all_pages:
            # 渲染所有页面
            for page_num in range(doc.page_count):
                page = doc[page_num]
                # 将页面渲染为图像
                mat = fitz.Matrix(1.5, 1.5)  # 1.5倍缩放
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes("png")

                base64_image = _image_bytes_to_base64(img_data)
                if base64_image:
                    images.append(base64_image)
        else:
            # 默认行为：提取页面中嵌入的图像
            for page_num in range(doc.page_count):
                page = doc[page_num]
                image_list = page.get_images()
                for img_index, img in enumerate(image_list):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]

                    # 转换为base64
                    base64_image = _image_bytes_to_base64(image_bytes)
                    if base64_image:
                        images.append(base64_image)

        doc.close()
        return images

    except ImportError:
        logger.error("PyMuPDF (fitz) 未安装，无法从PDF提取图像")
        return []
    except Exception as e:
        logger.error(f"从PDF提取图像失败: {str(e)}")
        return []


def _extract_images_from_office_bytes(file_content: bytes, **kwargs: Any) -> List[str]:
    """
    从Office文档字节内容中提取图像

    Args:
        file_content: Office文档的二进制内容
        **kwargs: 额外参数

    Returns:
        List[str]: base64编码的图像数组
    """
    try:
        images = []

        # 创建临时文件
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as temp_file:
            temp_file.write(file_content)
            temp_path = temp_file.name

        try:
            # 将Office文档当作ZIP文件处理
            with zipfile.ZipFile(temp_path, "r") as zip_file:
                # 查找媒体文件夹中的图像
                for file_name in zip_file.namelist():
                    if "media/" in file_name and any(file_name.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".bmp"]):
                        try:
                            image_data = zip_file.read(file_name)
                            base64_image = _image_bytes_to_base64(image_data)
                            if base64_image:
                                images.append(base64_image)
                        except Exception as e:
                            logger.warning(f"无法读取图像文件 {file_name}: {str(e)}")

        finally:
            # 清理临时文件
            if os.path.exists(temp_path):
                os.unlink(temp_path)

        return images

    except Exception as e:
        logger.error(f"从Office文档提取图像失败: {str(e)}")
        return []


def extract_images_from_bytes(file_content: bytes, file_type: str, **kwargs: Any) -> List[str]:
    """
    从文件字节内容提取所有图像并转换为base64编码的数组

    Args:
        file_content: 文件的二进制内容
        file_type: 文件类型（如'.pdf', '.docx'等）
        **kwargs: 额外参数

    Returns:
        List[str]: base64编码的图像数组，格式为 "data:image/jpeg;base64,..."
    """
    try:
        # 支持的图像格式
        image_formats = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}
        # 支持提取图像的文档格式

        logger.debug(f"检测到文件类型: {file_type}")

        # 根据文件类型提取图像
        if file_type in image_formats:
            # 直接是图像文件
            base64_image = _image_bytes_to_base64(file_content)
            return [base64_image] if base64_image else []
        elif file_type == ".pdf":
            return _extract_images_from_pdf_bytes(file_content, **kwargs)
        elif file_type in {".docx", ".pptx", ".xlsx", ".xls", ".doc", ".ppt"}:
            return _extract_images_from_office_bytes(file_content, **kwargs)
        else:
            logger.warning(f"不支持的文件类型: {file_type}")
            return []

    except Exception as e:
        logger.error(f"图像提取失败: {str(e)}")
        return []


def create_openai_image_message(text: str, image_urls: List[str]) -> Dict[str, Any]:
    """创建 Responses 图像输入消息

    Args:
        text: 文本内容
        image_urls: base64编码的图像URL列表

    Returns:
        Dict[str, Any]: Responses 输入消息
    """
    content: List[Dict[str, Any]] = []

    for image_base64 in image_urls:
        content.append(
            {
                "type": "input_image",
                "image_url": image_base64,
                "detail": "high",
            }
        )
    if text:
        content.append({"type": "input_text", "text": text})
    return {"role": "user", "content": content}


class ImageExtractor:
    """从调用方提供的文件内容中提取图像。"""

    def __init__(self) -> None:
        # 支持的图像格式
        self.image_formats = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}
        # 支持提取图像的文档格式
        self.document_formats = {".pdf", ".docx", ".pptx"}

    def extract_images_from_content(self, file_content: bytes, file_type: str, **kwargs: Any) -> List[str]:
        """
        从文件内容提取所有图像并转换为base64编码的数组

        Args:
            file_content: 文件的二进制内容
            file_type: 文件类型（如'.pdf', '.docx'等）
            **kwargs: 额外参数

        Returns:
            List[str]: base64编码的图像数组，格式为 "data:image/jpeg;base64,..."
        """
        return extract_images_from_bytes(file_content, file_type, **kwargs)


class OpenAIImageMessageBuilder:
    """OpenAI图像消息构建器"""

    @staticmethod
    def create_message(text: str, image_urls: List[str]) -> Dict[str, Any]:
        """创建包含图像的OpenAI消息"""
        return create_openai_image_message(text, image_urls)

    @staticmethod
    def create_message_from_file_content(text: str, file_content: bytes, file_type: str, **kwargs: Any) -> Dict[str, Any]:
        """从文件内容创建包含图像的OpenAI消息"""
        images = extract_images_from_bytes(file_content, file_type, **kwargs)
        return create_openai_image_message(text, images)

    @staticmethod
    def create_image_analysis_messages(system_prompt: str, user_text: str, image_base64_list: List[str]) -> List[Dict[str, Any]]:
        """
        创建图像分析消息列表

        Args:
            system_prompt: 系统提示
            user_text: 用户文本描述
            image_base64_list: base64编码的图像列表

        Returns:
            List[Dict[str, Any]]: OpenAI消息列表
        """
        messages = []

        # 添加系统消息
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # 创建用户消息，包含文本和图像
        user_message = create_openai_image_message(user_text, image_base64_list)
        messages.append(user_message)

        return messages


__all__ = [
    "extract_images_from_bytes",
    "create_openai_image_message",
    "ImageExtractor",
    "OpenAIImageMessageBuilder",
]
