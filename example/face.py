import machine
import sh1106
import time
import math

i2c = machine.I2C(0, scl=machine.Pin(9), sda=machine.Pin(8), freq=400000)
display = sh1106.SH1106_I2C(128, 64, i2c, rotate=180)

def clear():
    display.fill(0)

def draw_oval_fill(x0, y0, a, b):
    """
    """
    for y in range(-b, b + 1):
        for x in range(-a, a + 1):
            if (a > 0 and b > 0):
                if (x * x) / (a * a) + (y * y) / (b * b) <= 1:
                    display.pixel(x0 + x, y0 + y, 1)

def draw_thick_line(x1, y1, x2, y2, thickness=3):
    """通过多重绘制实现线条加粗"""
    for i in range(thickness):
        display.line(x1, y1 + i, x2, y2 + i, 1)
        display.line(x1 + i, y1, x2 + i, y2, 1)


def face_idle(blink_h=18):
    """1. 正常呆滞椭圆眼"""
    clear()
    if blink_h > 2:
        draw_oval_fill(37, 32, 12, blink_h)
        draw_oval_fill(91, 32, 12, blink_h)
    else:
        display.fill_rect(25, 31, 24, 4, 1)
        display.fill_rect(79, 31, 24, 4, 1)
    display.show()
    
def face_thinking(tick=0):
    """
    4. 思考表情：一只眼大，一只眼小，带个挑眉
    tick: 可用于做微小的动态效果（如眼珠微动）
    """
    clear()
    draw_oval_fill(37, 32, 12, 18)
    draw_oval_fill(91, 35, 12, 10)
    for i in range(3):
        display.line(80, 20+i, 105, 15+i, 1)
    display.show()

def face_excited():
    """3. 兴奋表情 (> <) ：超粗线条"""
    clear()
    for i in range(5):
        display.line(25, 20+i, 50, 32+i, 1)
        display.line(50, 32+i, 25, 44+i, 1)
        display.line(103, 20+i, 78, 32+i, 1)
        display.line(78, 32+i, 103, 44+i, 1)
    display.show()

def face_sad_tears(tick):
    clear()
    for i in range(5):
        display.line(50+i, 20, 25+i, 40, 1)
        display.line(78-i, 20, 103-i, 40, 1)
    tear_w = 6 if tick % 2 == 0 else 3
    tear_h = 4 if tick % 2 == 0 else 2
    display.fill_rect(22, 42, tear_w, tear_h, 1)
    display.fill_rect(100, 42, tear_w, tear_h, 1)
    display.show()

def face_sleepy(step):
    """5. 睡觉表情 (- -) ：带动态 Zzz"""
    clear()
    display.fill_rect(25, 32, 25, 5, 1)
    display.fill_rect(78, 32, 25, 5, 1)
    zs = [(105, 25), (112, 15), (119, 5)]
    for i in range(step % 4):
        if i < 3:
            display.text("z", zs[i][0], zs[i][1], 1)
    display.show()

def blink_anim():
    """执行一次眨眼动画"""
    for h in [12, 2, 12, 18]:
        face_idle(h)
        time.sleep(0.05)


def face_disappointed():
    """
    失望表情：下垂的眼帘 + 倒八字眉
    """
    clear()
    draw_oval_fill(37, 36, 12, 8)
    draw_oval_fill(91, 36, 12, 8)
    for i in range(3):
        display.line(25, 25 + i, 45, 18 + i, 1)
    for i in range(3):
        display.line(83, 18 + i, 103, 25 + i, 1)  
    display.show()