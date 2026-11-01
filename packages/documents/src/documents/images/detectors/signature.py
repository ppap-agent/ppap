import base64
import logging
import os

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def detect_signature_or_stamp(image_base64: str, debug: bool = False) -> bool:
    """
    在图像中检测红色印章或签名。
    此版本放宽了位置限制，使其能够检测位于图像中心区域的印章。

    Args:
        image_base64 (str): 图片的base64编码字符串。
        debug (bool): 如果为 True，则显示中间步骤的调试窗口。

    Returns:
        bool: 如果成功检测到印章/签名则返回 True，否则返回 False。
    """
    try:
        # 1. 解码base64图片数据
        try:
            if "," in image_base64:
                image_base64 = image_base64.split(",")[1]
            image_data = base64.b64decode(image_base64)
            nparr = np.frombuffer(image_data, np.uint8)
            original_image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if original_image is None:
                logger.error("错误: 无法解码base64图像数据")
                return False
        except Exception as exc:
            logger.error("错误: base64图像解码失败 - %s", str(exc))
            return False

        debug_image = original_image.copy() if debug else None
        img_height, img_width, _ = original_image.shape

        # 2. 颜色检测和处理 (继续使用红色检测)
        hsv_image = cv2.cvtColor(original_image, cv2.COLOR_BGR2HSV)

        # 定义红色的两个HSV范围
        lower_red1 = np.array([0, 70, 50])
        upper_red1 = np.array([10, 255, 255])
        mask1 = cv2.inRange(hsv_image, lower_red1, upper_red1)

        lower_red2 = np.array([170, 70, 50])
        upper_red2 = np.array([180, 255, 255])
        mask2 = cv2.inRange(hsv_image, lower_red2, upper_red2)

        mask = cv2.add(mask1, mask2)

        kernel = np.ones((5, 5), np.uint8)
        mask_processed = cv2.dilate(mask, kernel, iterations=3)

        if debug:
            cv2.imshow("Debug: Red Color Mask", mask_processed)
            cv2.waitKey(0)

        # 3. 寻找所有轮廓
        contours, _ = cv2.findContours(mask_processed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # 4. 智能筛选
        candidate_signatures = []
        if debug:
            logger.debug("[调试模式] 开始智能筛选...")

        for contour in contours:
            # 筛选器 1: 最小面积 (保持不变, 有效过滤小的红色噪点/文字)
            area = cv2.contourArea(contour)
            if area < 1000:
                continue

            x, y, w, h = cv2.boundingRect(contour)

            # 筛选器 2: 位置 (***核心修改点：放宽位置限制***)
            # 允许目标出现在图像中心靠右的区域，而不仅仅是右下角
            if not (x > img_width * 0.25 and y > img_height * 0.25):
                continue

            # 筛选器 3: 宽高比 (保持不变, 有效过滤细长的红色线条)
            if h == 0:
                continue
            aspect_ratio = w / h
            if not (0.5 < aspect_ratio < 2.0):  # 印章接近圆形，宽高比接近1
                continue

            if debug and debug_image is not None:
                logger.debug(
                    "找到候选者: 面积=%.0f 位置=(%d,%d) 宽高比=%.2f",
                    area,
                    x,
                    y,
                    aspect_ratio,
                )
                cv2.rectangle(debug_image, (x, y), (x + w, y + h), (0, 255, 255), 2)

            candidate_signatures.append(contour)

        if debug and debug_image is not None:
            cv2.imshow("Debug: All Candidates (Yellow)", debug_image)
            logger.debug("[调试模式] 显示所有候选者，等待按键继续")
            cv2.waitKey(0)

        # 5. 从候选者中选出最终结果
        if candidate_signatures:
            final_signature_contour = max(candidate_signatures, key=cv2.contourArea)
            x, y, w, h = cv2.boundingRect(final_signature_contour)

            cv2.rectangle(original_image, (x, y), (x + w, y + h), (0, 255, 0), 3)
            cv2.putText(
                original_image,
                "Stamp/Signature Detected",
                (x, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (0, 255, 0),
                2,
            )

            logger.debug("成功: 检测到印章/签名区域")

            if debug:
                cv2.imshow("Final Result", original_image)
                logger.debug("[调试模式] 显示最终结果，等待按键关闭")
                cv2.waitKey(0)

            return True
        else:
            logger.debug("失败: 未找到符合条件的红色印章/签名区域")
            return False

    finally:
        if debug:
            cv2.destroyAllWindows()
            logger.debug("[调试模式] 所有窗口已关闭")


# --- 主程序入口 ---
if __name__ == "__main__":
    # 替换为您的新图片路径
    input_image_file = "/Users/wangbin/Desktop/8.22.42.png"  # 请将此文件名替换为您的实际文件名

    if os.path.exists(input_image_file):
        try:
            with open(input_image_file, "rb") as image_file:
                image_base64 = base64.b64encode(image_file.read()).decode("utf-8")

            print("--- 运行模式: 调试模式 (debug=True) ---")
            success = detect_signature_or_stamp(image_base64=image_base64, debug=False)
            if success:
                print("程序运行成功。")
            else:
                print("程序运行失败。")

        except Exception as e:
            print(f"主程序出现错误: {str(e)}")
    else:
        print(f"输入文件未找到: {input_image_file}")
