"""图像文件内容提取器"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Set, Union

from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)


class ImageExtractor(BaseExtractor):
    """图像文件内容提取器"""

    @property
    def supported_extensions(self) -> Set[str]:
        return {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"}

    def extract_text(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> str:
        """从图像中提取文本（OCR）

        Args:
            file_content: 图像文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            str: 通过OCR提取的文本内容

        Raises:
            ExtractionError: 当图像处理或OCR失败时抛出
        """
        try:
            logger.debug(f"开始从图像提取文本内容，文件大小: {len(file_content)} bytes")

            # 图像文件本身不包含可直接提取的文本，需要通过OCR
            # 这里返回空字符串，让上层服务决定是否使用OCR
            logger.debug("图像文件需要OCR处理才能提取文本")
            return ""

        except Exception as e:
            raise ExtractionError(f"图像文本提取失败: {str(e)}", file_type="Image", original_error=e)

    def extract_metadata(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """提取图像的元数据

        Args:
            file_content: 图像文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            Dict[str, Any]: 包含图像元数据的字典
        """
        try:
            import io

            from PIL import Image
            from PIL.ExifTags import TAGS

            metadata = {"file_size": len(file_content), "format": "Image"}

            # 使用PIL读取图像
            image = Image.open(io.BytesIO(file_content))

            # 基本信息
            metadata.update(
                {
                    "width": image.width,
                    "height": image.height,
                    "mode": image.mode,
                    "format": image.format or "Unknown",
                }
            )

            # 提取EXIF数据
            if hasattr(image, "_getexif") and image._getexif() is not None:
                exif_data = {}
                exif = image._getexif()

                for tag_id, value in exif.items():
                    tag = TAGS.get(tag_id, tag_id)
                    exif_data[tag] = value

                if exif_data:
                    metadata["exif"] = exif_data

            image.close()
            return metadata

        except ImportError:
            logger.warning("PIL库未安装，无法提取图像元数据")
            return {"format": "Image", "file_size": len(file_content)}
        except Exception as e:
            logger.error(f"图像元数据提取失败: {str(e)}")
            return {"format": "Image", "file_size": len(file_content), "error": str(e)}
