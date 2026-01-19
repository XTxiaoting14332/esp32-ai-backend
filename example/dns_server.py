import uasyncio as asyncio
import socket

class DNSServer:
    def __init__(self, ip="192.168.4.1"):
        self.ip = ip
        self.ip_bytes = bytes(map(int, ip.split('.')))

    async def run(self):
        udps = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udps.setblocking(False)
        udps.bind(('0.0.0.0', 53))
        
        while True:
            try:
                yield asyncio.core._io_queue.queue_read(udps)
                data, addr = udps.recvfrom(512)
                
                if len(data) < 12: continue
                packet = data[:2] + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"
                packet += data[12:]
                packet += b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04"
                packet += self.ip_bytes
                
                udps.sendto(packet, addr)
            except Exception:
                await asyncio.sleep_ms(10)
            await asyncio.sleep_ms(1)