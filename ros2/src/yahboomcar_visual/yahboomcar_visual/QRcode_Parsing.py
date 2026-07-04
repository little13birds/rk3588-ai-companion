import rclpy
from rclpy.node import Node
import time
import cv2 as cv
import numpy as np
import pyzbar.pyzbar as pyzbar

from sensor_msgs.msg import Image as CamImage
from PIL import Image, ImageDraw, ImageFont
from cv_bridge import CvBridge

class QRCode_Parsing(Node):
	def __init__(self,name):
		super().__init__(name)
		self.bridge = CvBridge()
		self.sub_img = self.create_subscription(CamImage,'/camera/color/image_raw',self.handleTopic,100)
		self.font_path = "./Block_Simplified.TTF"
	
	def handleTopic(self,msg):
		if not isinstance(msg, CamImage):
			return
		frame = self.bridge.imgmsg_to_cv2(msg, "bgr8")
		frame = cv.resize(frame, (640, 480))
		frame = self.decodeDisplay(frame)
		text = "test"
		cv.putText(frame, text, (30, 30), cv.FONT_HERSHEY_SIMPLEX, 0.6, (100, 200, 200), 1)
		cv.imshow('frame', frame)
		cv.waitKey(10)




	def decodeDisplay(self,image):
		gray = cv.cvtColor(image, cv.COLOR_BGR2GRAY)
    	# 需要先把输出的中文字符转换成Unicode编码形式
    	# The output Chinese characters need to be converted to Unicode encoding first
		barcodes = pyzbar.decode(gray)
		for barcode in barcodes:
        	# 提取二维码的边界框的位置
        	# Extract the position of the boundary box of the TWO-DIMENSIONAL code
        	# 画出图像中条形码的边界框
        	# Draw the bounding box for the bar code in the image
			(x, y, w, h) = barcode.rect
			cv.rectangle(image, (x, y), (x + w, y + h), (225, 0, 0), 5)
			encoding = 'UTF-8'
        	# 画出来，就需要先将它转换成字符串
        	# to draw it, you need to convert it to a string
			barcodeData = barcode.data.decode(encoding)
			barcodeType = barcode.type
        	# 绘出图像上数据和类型
        	# Draw the data and type on the image
			pilimg = Image.fromarray(image)
        	# 创建画笔
        	# create brush
			draw = ImageDraw.Draw(pilimg)  # 图片上打印  Print on picture
        	# 参数1：字体文件路径，参数2：字体大小
        	# parameter 1: font file path, parameter 2: font size
			fontStyle = ImageFont.truetype("/home/yahboom/orbbec_ws/src/yahboomcar_visual/yahboomcar_visual/Block_Simplified.TTF", size=12, encoding=encoding)
        	# # 参数1：打印坐标，参数2：文本，参数3：字体颜色，参数4：字体
        	# Parameter 1: print coordinates, parameter 2: text, parameter 3: font color, parameter 4: font
			draw.text((x, y - 25), str(barcode.data, encoding), fill=(255, 0, 0), font=fontStyle)
        	# # PIL图片转cv2 图片
        	# PIL picture to CV2 picture
			image = np.array(pilimg)
        	# 向终端打印条形码数据和条形码类型
        	# Print barcode data and barcode type to terminal
			print("[INFO] Found {} barcode: {}".format(barcodeType, barcodeData))
		return image
    	
def main():
	rclpy.init()
	qrcode_parse = QRCode_Parsing('Parse_QRCode')
	rclpy.spin(qrcode_parse)


