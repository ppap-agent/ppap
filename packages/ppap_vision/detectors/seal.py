import base64
import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def detect_seal(image_base64: str, debug: bool = False) -> bool:
    """
    检测图像中是否存在印章（终极通用版）。
    该版本通过放宽几何形状限制，可同时检测圆形、椭圆形和方形印章。

    Args:
        image_base64 (str): 图片的base64编码字符串
        debug (bool): 是否开启调试模式

    Returns:
        bool: 如果检测到印章返回True，否则返回False
    """
    try:
        # 解码base64图片数据
        if "," in image_base64:
            # 如果包含数据URL前缀，则去除前缀
            image_base64 = image_base64.split(",")[1]

        # 将base64字符串解码为字节数据
        image_data = base64.b64decode(image_base64)

        # 将字节数据转换为numpy数组
        nparr = np.frombuffer(image_data, np.uint8)

        # 使用cv2解码图像
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if image is None:
            logger.error("错误: 无法解码base64图像数据")
            return False

    except Exception as exc:
        logger.error("错误: base64图像解码失败 - %s", str(exc))
        return False

    output_image = image.copy()
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    # 保持上一版极度放宽的颜色阈值，以应对各种颜色和光照条件
    lower_red1 = np.array([0, 50, 20])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([156, 50, 20])
    upper_red2 = np.array([179, 255, 255])

    red_mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    red_mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    red_mask = cv2.bitwise_or(red_mask1, red_mask2)

    combined_mask = red_mask

    if debug:
        cv2.imshow("1. Combined Color Mask", combined_mask)

    # 保持大尺寸的卷积核，以连接所有类型的印章（圆、椭圆）
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (30, 30))
    cleaned_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel, iterations=3)

    if debug:
        cv2.imshow("2. Cleaned Mask", cleaned_mask)

    contours, _ = cv2.findContours(cleaned_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    found_seal = False

    for contour in contours:
        area = cv2.contourArea(contour)
        perimeter = cv2.arcLength(contour, True)

        # 面积过滤依然重要，过滤掉太小或太大的干扰
        if area < 5000:
            continue

        # 【核心修改 1】: 大幅放宽宽高比限制，以接受椭圆形
        x, y, w, h = cv2.boundingRect(contour)
        if h == 0:
            continue  # 避免除以零
        aspect_ratio = w / float(h)
        # 允许从比较“瘦高”的椭圆(0.4)到比较“矮胖”的椭圆(2.5)
        if not (0.4 < aspect_ratio < 2.5):
            if debug:
                logger.debug("轮廓因宽高比被过滤: %.2f", aspect_ratio)
            continue

        # 【核心修改 2】: 大幅降低圆度阈值下限
        if perimeter > 0:
            circularity = (4 * np.pi * area) / (perimeter**2)
            # 这个检查现在只用于排除非常不规则、细长的形状，而不是强制要求“圆”
            if circularity < 0.25:
                if debug:
                    logger.debug("轮廓因圆度被过滤: %.2f", circularity)
                continue
        else:
            continue

        # 密度验证现在变得更加重要，因为形状的约束放宽了
        contour_mask = np.zeros(cleaned_mask.shape, dtype="uint8")
        cv2.drawContours(contour_mask, [contour.astype(np.int32)], -1, (255.0, 255.0, 255.0), -1)

        ink_mask = cv2.bitwise_and(combined_mask, combined_mask, mask=contour_mask)
        ink_pixels = cv2.countNonZero(ink_mask)
        total_pixels = cv2.countNonZero(contour_mask)

        if total_pixels == 0:
            continue

        density = ink_pixels / float(total_pixels)

        if 0.15 < density < 0.9:
            found_seal = True
            logger.debug(
                "成功检测到印章: 面积=%d 宽高比=%.2f 圆度=%.2f 密度=%.2f",
                int(area),
                aspect_ratio,
                circularity,
                density,
            )
            # 使用旋转的边界框来更好地框出椭圆
            rot_rect = cv2.minAreaRect(contour)
            box_points = cv2.boxPoints(rot_rect)
            contour_box = box_points.astype(np.int32)
            cv2.drawContours(output_image, [contour_box], 0, (0.0, 255.0, 0.0), 3)
            cv2.putText(
                output_image,
                "Seal",
                (x, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (0, 255, 0),
                2,
            )
            break
        else:
            if debug:
                logger.debug(
                    "轮廓被密度过滤: 面积=%d 宽高比=%.2f 圆度=%.2f 密度=%.2f",
                    int(area),
                    aspect_ratio,
                    circularity,
                    density,
                )

    if debug:
        cv2.imshow("3. Final Result (Universal)", output_image)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    return found_seal


# --- 使用方法 ---
if __name__ == "__main__":
    # 示例：从文件读取图片并转换为base64
    import base64

    image_path = "/Users/wangbin/Desktop/10.43.42.png"

    try:
        # 读取图片文件并转换为base64
        with open(image_path, "rb") as image_file:
            image_base64 = base64.b64encode(image_file.read()).decode("utf-8")

        # 使用base64数据进行印章检测
        is_seal_present = detect_seal(image_base64, debug=False)

        if is_seal_present:
            print("\n最终结论: 图像中包含印章。")
        else:
            print("\n最终结论: 图像中未检测到印章。")

    except FileNotFoundError:
        print(f"错误: 找不到图片文件 '{image_path}'")
    except Exception as e:
        print(f"错误: {str(e)}")
