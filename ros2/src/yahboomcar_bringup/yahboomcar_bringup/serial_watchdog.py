#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
串口看门狗 (Serial Watchdog)
自动检测 USB 串口掉线 (EPIPE / 超时) 并执行 USB 重置恢复。

用法:
    python3 serial_watchdog.py [--max-fail 5] [--usb-port 1-1.3] [--serial /dev/ttyUSB0]

依赖:
    - Rosmaster_Lib
    - pyserial
    - sudo 免密权限 (见部署说明)
"""

import os
import sys
import time
import serial
import subprocess
import argparse
import threading
from datetime import datetime


# ============================================================
#  USB 重置工具
# ============================================================

class USBReset:
    """USB 端口 unbind/bind 重置。"""

    RESET_SCRIPT = "/usr/local/bin/usb-reset"

    def __init__(self, usb_port: str):
        """
        Args:
            usb_port: USB 设备物理路径, 如 '1-1.3'
        """
        self.usb_port = usb_port

    def reset(self) -> bool:
        """执行 unbind + bind 重置, 返回是否成功。"""
        ts = _ts()
        print(f"[{ts}] [USB] 正在重置 USB 端口 {self.usb_port} ...")
        try:
            result = subprocess.run(
                ["sudo", self.RESET_SCRIPT, self.usb_port],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                print(f"[{ts}] [USB] 重置失败: {result.stderr.strip()}")
                return False
            print(f"[{_ts()}] [USB] 重置完成")
            return True
        except FileNotFoundError:
            print(f"[{ts}] [USB] {self.RESET_SCRIPT} 不存在, 请先部署 usb-reset 脚本")
            return False
        except subprocess.TimeoutExpired:
            print(f"[{ts}] [USB] 重置超时")
            return False
        except Exception as e:
            print(f"[{ts}] [USB] 重置异常: {e}")
            return False


# ============================================================
#  带看门狗的 Rosmaster 包装器
# ============================================================

class RosmasterWatchdog:
    """
    包装 Rosmaster, 自动检测串口掉线并恢复。

    检测策略:
        1. 每次读取数据时, 如果所有字段均为零/默认值, 计为一次"空读"
        2. 连续空读次数达到 max_fail 时, 判定为掉线, 触发恢复
        3. 串口 read() 抛出 OSError/IOError 时, 立即触发恢复
    """

    def __init__(
        self,
        com: str = "/dev/myserial",
        usb_port: str = "1-1.3",
        max_fail: int = 5,
        baudrate: int = 115200,
    ):
        self.com = com
        self.usb_port = usb_port
        self.max_fail = max_fail
        self.baudrate = baudrate
        self._car = None
        self._usb_reset = USBReset(usb_port)
        self._consecutive_fails = 0
        self._lock = threading.Lock()
        # 冻结检测: 保存最近一次的读数快照
        self._last_snapshot = None
        self._frozen_count = 0

    # ---------- 生命周期 ----------

    def connect(self, retries: int = 5, retry_delay: float = 2.0):
        """
        创建 Rosmaster 实例并启动接收线程。
        USB 重置后设备节点会短暂消失, 因此需要重试等待。
        """
        from Rosmaster_Lib import Rosmaster

        self._safe_close()

        for attempt in range(1, retries + 1):
            ts = _ts()
            # 等待设备节点出现
            if not os.path.exists(self.com):
                print(f"[{ts}] [WAIT] 等待设备 {self.com} 出现... ({attempt}/{retries})")
                time.sleep(retry_delay)
                continue

            try:
                self._car = Rosmaster(com=self.com)
                self._car.create_receive_threading()
                time.sleep(0.3)

                version = self._car.get_version()
                if version == -1:
                    print(f"[{ts}] [WARN] 版本号 -1, 数据尚未就绪")
                    time.sleep(0.5)
                    version = self._car.get_version()

                if version != -1:
                    print(f"[{_ts()}] [OK] 扩展板已连接, 固件版本: {version}")
                else:
                    print(f"[{_ts()}] [OK] 串口已打开, 等待数据刷新")

                self._consecutive_fails = 0
                return

            except (OSError, IOError, serial.SerialException) as e:
                print(f"[{ts}] [WARN] 连接失败: {e} ({attempt}/{retries})")
                self._safe_close()
                time.sleep(retry_delay)

        raise ConnectionError(f"无法连接 {self.com}, 已重试 {retries} 次")

    def _safe_close(self):
        if self._car is not None:
            try:
                self._car.ser.close()
            except Exception:
                pass
            self._car = None

    # ---------- 健康检查 ----------

    def _check_alive(self) -> bool:
        """
        底层串口存活探测: 直接用 pyserial 尝试读 1 字节。
        返回 True 表示串口硬件层正常。
        """
        try:
            ser = serial.Serial(self.com, self.baudrate, timeout=1)
            data = ser.read(1)
            ser.close()
            return len(data) > 0
        except (OSError, IOError, serial.SerialException):
            return False

    def _is_data_dead(self, version, battery, vx, vy, vz) -> bool:
        """判断读取到的数据是否全部为默认零值 (从未收到过数据)。"""
        return (
            version == -1
            and battery == 0.0
            and vx == 0.0
            and vy == 0.0
            and vz == 0.0
        )

    def _is_data_frozen(self, version, battery, ax, ay, az, vx, vy, vz) -> bool:
        """
        判断数据是否冻结 (USB 掉线后接收线程崩溃, 数据停在上次有效值)。
        正常工作时 IMU 加速度计的最低位噪声会让 ax/ay/az 每帧都不同;
        如果连续多次完全一样 (含加速度), 说明数据源已中断。
        """
        snapshot = (version, battery, ax, ay, az, vx, vy, vz)
        if snapshot == self._last_snapshot:
            self._frozen_count += 1
        else:
            self._frozen_count = 0
            self._last_snapshot = snapshot
        return self._frozen_count >= self.max_fail

    # ---------- 自动恢复 ----------

    def _recover(self):
        """
        完整恢复流程:
        1. USB unbind/bind 重置
        2. 等待设备节点重新出现
        3. 重新连接 Rosmaster
        """
        with self._lock:
            ts = _ts()
            print(f"[{ts}] [RECOVER] ===== 开始自动恢复 =====")

            # Step 1: 关闭旧连接
            self._safe_close()

            # Step 2: USB 重置
            if not self._usb_reset.reset():
                print(f"[{_ts()}] [RECOVER] USB 重置失败, 请检查硬件!")
                return False

            # Step 3: 重新连接 (带重试)
            try:
                self.connect(retries=8, retry_delay=2.0)
                print(f"[{_ts()}] [RECOVER] ===== 恢复成功! =====")
                return True
            except ConnectionError as e:
                print(f"[{_ts()}] [RECOVER] 恢复失败: {e}")
                return False

    # ---------- 对外接口 ----------

    def read_all(self):
        """
        读取全部状态数据, 自动处理掉线恢复。

        Returns:
            dict: {version, battery, ax, ay, az, vx, vy, vz}
                  恢复失败时返回 None
        """
        if self._car is None:
            self.connect()

        try:
            version = self._car.get_version()
            battery = self._car.get_battery_voltage()
            ax, ay, az = self._car.get_accelerometer_data()
            vx, vy, vz = self._car.get_motion_data()

            if self._is_data_dead(version, battery, vx, vy, vz):
                self._consecutive_fails += 1
                ts = _ts()
                print(f"[{ts}] [WARN] 数据全零 ({self._consecutive_fails}/{self.max_fail})")

                if self._consecutive_fails >= self.max_fail:
                    print(f"[{_ts()}] [FAIL] 数据持续为零, 触发恢复")
                    if not self._recover():
                        return None
                    return self.read_all()

            elif self._is_data_frozen(version, battery, ax, ay, az, vx, vy, vz):
                ts = _ts()
                print(f"[{ts}] [FAIL] 数据冻结 (连续 {self._frozen_count} 次读数相同), USB 已掉线")
                if not self._recover():
                    return None
                return self.read_all()
            else:
                self._consecutive_fails = 0

            return {
                "version": version,
                "battery": battery,
                "ax": ax, "ay": ay, "az": az,
                "vx": vx, "vy": vy, "vz": vz,
            }

        except (OSError, IOError, serial.SerialException) as e:
            ts = _ts()
            print(f"[{ts}] [FAIL] 串口异常: {e}")
            if not self._recover():
                return None
            return self.read_all()

    # ---------- 直接获取内部 car 实例 (供 ROS 节点用) ----------

    @property
    def car(self):
        """获取底层 Rosmaster 实例。"""
        return self._car


# ============================================================
#  工具函数
# ============================================================

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ============================================================
#  独立运行模式
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="串口看门狗 - 自动检测并恢复 USB 串口掉线")
    parser.add_argument("--max-fail", type=int, default=5, help="连续空读次数阈值 (默认 5)")
    parser.add_argument("--usb-port", type=str, default="1-1.3", help="USB 物理端口路径 (默认 1-1.3)")
    parser.add_argument("--serial", type=str, default="/dev/myserial", help="串口设备路径")
    parser.add_argument("--interval", type=float, default=0.5, help="读取间隔秒数 (默认 0.5)")
    args = parser.parse_args()

    print("=" * 50)
    print("  串口看门狗 (Serial Watchdog)")
    print(f"  串口: {args.serial}  USB端口: {args.usb_port}")
    print(f"  恢复阈值: 连续 {args.max_fail} 次空读")
    print("=" * 50)

    wd = RosmasterWatchdog(
        com=args.serial,
        usb_port=args.usb_port,
        max_fail=args.max_fail,
    )
    wd.connect()

    try:
        while True:
            data = wd.read_all()
            if data is None:
                print(f"[{_ts()}] 恢复失败, 5 秒后重试...")
                time.sleep(5)
                continue

            print(
                f"[{_ts()}] "
                f"V={data['version']} | "
                f"Bat={data['battery']:.1f}V | "
                f"Acc=({data['ax']:.2f},{data['ay']:.2f},{data['az']:.2f}) | "
                f"Vel=({data['vx']:.3f},{data['vy']:.3f},{data['vz']:.3f})"
            )
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n看门狗已停止")


if __name__ == "__main__":
    main()
