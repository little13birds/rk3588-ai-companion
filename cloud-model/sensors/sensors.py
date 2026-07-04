"""GY-30 光照 + MPU6050 运动传感器读取"""
import subprocess, struct, time

SUDO_PASS = "elf"
I2C_BUS = 4
GY30_ADDR = "0x23"
MPU_ADDR = "0x68"
_mpu_inited = False


def _sudo_write(cmd: str):
    """执行 sudo 写命令，忽略输出"""
    full = f"echo '{SUDO_PASS}' | sudo -S {cmd} 2>&1"
    subprocess.run(full, shell=True, capture_output=True, text=True, timeout=5)


def _sudo_read(cmd: str) -> list:
    """执行 sudo 读命令，返回 hex 字符串列表"""
    full = f"echo '{SUDO_PASS}' | sudo -S {cmd} 2>&1"
    r = subprocess.run(full, shell=True, capture_output=True, text=True, timeout=5)
    out = r.stdout.strip()
    # 去掉 sudo 提示前缀: "[sudo] password for elf: 0x00 0x60"
    marker = f"[sudo] password for elf: "
    if out.startswith(marker):
        out = out[len(marker):]
    return [x for x in out.split() if x.startswith("0x")]


def read_light() -> float:
    """读取 GY-30 光照强度，返回 lux"""
    _sudo_write(f"i2ctransfer -y {I2C_BUS} w1@{GY30_ADDR} 0x10")
    time.sleep(0.15)
    hex_parts = _sudo_read(f"i2ctransfer -y {I2C_BUS} r2@{GY30_ADDR}")
    if len(hex_parts) < 2:
        raise RuntimeError(f"GY-30 读取失败")
    msb, lsb = int(hex_parts[0], 16), int(hex_parts[1], 16)
    return (msb << 8 | lsb) / 1.2


def read_motion() -> dict:
    """读取 MPU6050 加速度+陀螺仪，返回 dict"""
    global _mpu_inited
    if not _mpu_inited:
        _sudo_write(f"i2ctransfer -y {I2C_BUS} w2@{MPU_ADDR} 0x6B 0x00")
        _mpu_inited = True
        time.sleep(0.05)

    hex_parts = _sudo_read(f"i2ctransfer -y {I2C_BUS} w1@{MPU_ADDR} 0x3B r14")
    if len(hex_parts) < 14:
        raise RuntimeError(f"MPU6050 读取失败")
    raw_bytes = bytes([int(h, 16) for h in hex_parts[:14]])
    vals = struct.unpack('>hhhhhhh', raw_bytes)
    return {
        "accel_g": {"x": round(vals[0]/16384, 3), "y": round(vals[1]/16384, 3), "z": round(vals[2]/16384, 3)},
        "gyro_dps": {"x": round(vals[4]/131, 2), "y": round(vals[5]/131, 2), "z": round(vals[6]/131, 2)},
    }


def read_temperature() -> dict:
    """读取 DHT11 温湿度，返回 dict {temperature, humidity}"""
    out = _sudo_read_dht()
    if len(out) < 2:
        raise RuntimeError(f"DHT11 读取失败")
    return {"temperature": float(out[0]), "humidity": float(out[1])}


def _sudo_read_dht() -> list:
    """执行 sudo /home/elf/dht11 3 11，返回 [温度, 湿度]"""
    full = f"echo '{SUDO_PASS}' | sudo -S /home/elf/dht11 3 11 2>&1"
    r = subprocess.run(full, shell=True, capture_output=True, text=True, timeout=5)
    out = r.stdout.strip()
    # 去掉 sudo 提示前缀 "[sudo] password for elf: "
    marker = f"[sudo] password for elf: "
    if out.startswith(marker):
        out = out[len(marker):]
    parts = out.split()
    if len(parts) >= 2:
        try:
            float(parts[0])
            return parts[:2]
        except ValueError:
            pass
    return []
