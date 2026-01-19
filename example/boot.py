import time
import gc
import machine


gc.threshold(4096)
gc.collect()
machine.freq(160000000)




print(">>> 将在 5 秒后加载 main.py")

for i in range(5, 0, -1):
    print(f"倒计时: {i}...")
    time.sleep(1)

gc.collect()
print(">>> 正在进入主程序循环...\n")