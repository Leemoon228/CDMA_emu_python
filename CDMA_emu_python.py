import threading
import queue
import sys
from collections import deque
from typing import List, Dict

def hadamard(n: int) -> List[List[int]]:
    H = [[1]]
    while len(H) < n:
        H = [r + r for r in H] + [r + [-x for x in r] for r in H]
    return H

def walsh_codes(L: int) -> List[List[int]]:
    return hadamard(L)

def bits_from_ascii(text: str) -> List[int]:
    bits: List[int] = []
    for b in text.encode('ascii'):
        bits.extend([(b >> i) & 1 for i in range(7, -1, -1)])
    return bits

def bipolar(bit: int) -> int:
    return 1 if bit else -1

def bits_to_byte(bits: List[int]) -> int:
    v = 0
    for b in bits:
        v = (v << 1) | (b & 1)
    return v

class Station:
    def __init__(self, name: str, msg: str, code: List[int]):
        self.bits = bits_from_ascii(msg)
        self.code = code
        self.i = 0

    def transmit(self) -> List[int]:
        bpol = bipolar(self.bits[self.i])
        self.i = (self.i + 1) % len(self.bits)
        return [bpol * c for c in self.code]

class Channel:
    def __init__(self):
        self.q = queue.SimpleQueue()

    def put(self, val: int):
        self.q.put(val)

    def attach(self, q: queue.SimpleQueue):
        self.q = q

class Transmitter(threading.Thread):
    def __init__(self, stations: List[Station], ch: Channel):
        super().__init__(daemon=True)
        self.stations = stations
        self.ch = ch

    def run(self):
        L = len(self.stations[0].code)
        bit_count = 0 
        while True:
            chips = [0] * L
            for st in self.stations:
                sig = st.transmit()
                for i in range(L):
                    chips[i] += sig[i]
            for val in chips:
                self.ch.put(val)
            bit_count += 1
            if bit_count % 8 == 0:
                for _ in range(L):
                    self.ch.put(0)

class BitTap(threading.Thread):
    def __init__(self, ch: Channel):
        super().__init__(daemon=True)
        self.ch = ch
        self.q = queue.SimpleQueue()

    def run(self):
        self.ch.attach(self.q)
        for _ in range(30):
            row = []
            for _ in range(8):
                val = self.q.get()
                row.append(f"{val:+d}")
            print(" ".join(row))

class Receiver(threading.Thread):
    def __init__(self, ch: Channel, code: List[int]):
        super().__init__(daemon=True)
        self.ch = ch
        self.code = code
        self.q = queue.SimpleQueue()
        self.win = deque(maxlen=len(code))
        self.bits: List[int] = []

    def run(self):
        self.ch.attach(self.q)
        L = len(self.code)
        bits_buffer: List[int] = []
        while True:
            chips = [self.q.get() for _ in range(L)]
            if all(c == 0 for c in chips):
                if bits_buffer:
                    byte_val = bits_to_byte(bits_buffer)
                    sys.stdout.write(chr(byte_val))
                    sys.stdout.flush()
                    bits_buffer.clear()
                continue
            bit = 1 if sum(chips[i]*self.code[i] for i in range(L)) >= 0 else 0
            bits_buffer.append(bit)

def main():
    W = walsh_codes(8)
    codes = {'A': W[0], 'B': W[1], 'C': W[2], 'D': W[3]}
    words = {'A': "GOD", 'B': "CAT", 'C': "HAM", 'D': "SUN"}

    print("Режим: 0 — канал; 1 — A; 2 — B; 3 — C; 4 — D")
    mode = input("Введите: ").strip()

    ch = Channel()
    stations = [Station(name, words[name], codes[name]) for name in ['A', 'B', 'C', 'D']]
    Transmitter(stations, ch).start()

    if mode == '0':
        BitTap(ch=ch).start()
    else:
        st = {'1': 'A', '2': 'B', '3': 'C', '4': 'D'}[mode]
        Receiver(ch=ch, code=codes[st]).start()

    threading.Event().wait()

if __name__ == "__main__":
    main()
