import threading
import time
from typing import List, Dict, Optional
import queue
import sys
from collections import deque

# --- Уолш-коды ---
def hadamard(n: int) -> List[List[int]]:
    if n & (n - 1) != 0 or n == 0:
        raise ValueError("Длина должна быть степенью 2")
    H = [[1]]
    while len(H) < n:
        top = [r + r for r in H]
        bottom = [r + [-x for x in r] for r in H]
        H = top + bottom
    return H

def walsh_codes(L: int) -> List[List[int]]:
    return hadamard(L)

# --- Биты и преобразования ---
def bits_from_ascii(text: str) -> List[int]:
    data = text.encode('ascii')
    bits: List[int] = []
    for b in data:
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)
    return bits

def bipolar(bit: int) -> int:
    return 1 if bit else -1

def bits_to_byte(bits: List[int]) -> int:
    v = 0
    for b in bits:
        v = (v << 1) | (b & 1)
    return v

# --- Канал ---
class Channel:
    def __init__(self, n: int):
        self.n = n
        self.buf = [0] * n
        self._sub: Optional["queue.SimpleQueue[int]"] = None
        self._lock = threading.Lock()

        def aggregate():
            total = sum(self.buf)
            with self._lock:
                if self._sub is not None:
                    self._sub.put(total)
            for i in range(self.n):
                self.buf[i] = 0

        self.barrier = threading.Barrier(n, action=aggregate)

    def send(self, idx: int, val: int):
        self.buf[idx] = val
        self.barrier.wait()

    def attach(self, q: "queue.SimpleQueue[int]"):
        with self._lock:
            self._sub = q

# --- Передатчик ---
class Transmitter(threading.Thread):
    def __init__(self, name: str, msg: str, code: List[int], idx: int, ch: Channel):
        super().__init__(name=f"TX-{name}", daemon=True)
        self.bits = bits_from_ascii(msg)
        self.code = code
        self.idx = idx
        self.ch = ch

    def run(self):
        L = len(self.code)
        i = 0
        while True:
            b = self.bits[i]
            i = (i + 1) % len(self.bits)
            bpol = bipolar(b)
            for k in range(L):
                self.ch.send(self.idx, bpol * self.code[k])

# --- Сырой вывод канала ---
class BitTap(threading.Thread):
    def __init__(self, ch: Channel, group: int = 64):
        super().__init__(name="BIT-TAP", daemon=True)
        self.ch = ch
        self.group = group
        self.q: "queue.SimpleQueue[int]" = queue.SimpleQueue()

    def run(self):
        self.ch.attach(self.q)
        count = 0
        while True:
            total = self.q.get()
            bit = 1 if total >= 0 else 0
            print(bit, end='', flush=True)
            count += 1
            if count % self.group == 0:
                print()

# --- Приёмник ---
class Receiver(threading.Thread):
    def __init__(self, ch: Channel, codes: Dict[str, List[int]], words: Dict[str, str], st: str):
        super().__init__(name="RX", daemon=True)
        self.ch = ch
        self.code = codes[st]
        self.exp = words[st].encode('ascii')
        self.q: "queue.SimpleQueue[int]" = queue.SimpleQueue()
        self._stop = threading.Event()
        self._synced = False
        self._shift: Optional[int] = None
        self._win = deque(maxlen=8)
        self._bits: List[int] = []

    def stop(self):
        self._stop.set()

    def run(self):
        self.ch.attach(self.q)
        L = 8
        while not self._stop.is_set():
            if not self._synced:
                while len(self._win) < L:
                    self._win.append(self.q.get())
                dot = sum(self._win[i] * self.code[i] for i in range(L))
                if abs(dot) >= 7:
                    self._synced = True
                else:
                    self._win.append(self.q.get())
                continue

            chips = list(self._win)
            self._win.clear()
            for _ in range(L):
                self._win.append(self.q.get())
            bit = 1 if sum(chips[i] * self.code[i] for i in range(L)) >= 0 else 0
            self._bits.append(bit)

            if self._shift is None:
                need = 8 * (len(self.exp) * 3)
                if len(self._bits) < need:
                    continue
                best_shift, best_score = 0, -1
                exp = list(self.exp)
                for s in range(8):
                    bytes_seq = [bits_to_byte(self._bits[i:i+8])
                                 for i in range(s, len(self._bits)-7, 8)]
                    score = sum(1 for i in range(len(bytes_seq)-len(exp)+1)
                                if bytes_seq[i:i+len(exp)] == exp)
                    if score > best_score:
                        best_score, best_shift = score, s
                self._shift = best_shift
                drop = (len(self._bits) - self._shift) % 8
                if drop:
                    self._bits = self._bits[:-drop]
                continue

            while len(self._bits) - self._shift >= 8:
                i = self._shift
                ch = chr(bits_to_byte(self._bits[i:i+8]))
                sys.stdout.write(ch)
                sys.stdout.flush()
                self._shift += 8

            if self._shift and self._shift > 256:
                self._bits = self._bits[self._shift:]
                self._shift = 0

# --- main ---
def main():
    W = walsh_codes(8)
    codes = {'A': W[0], 'B': W[1], 'C': W[2], 'D': W[3]}
    words: Dict[str, str] = {'A': "GOD", 'B': "CAT", 'C': "HAM", 'D': "SUN"}

    print("Режим: 0 — канал; 1 — A; 2 — B; 3 — C; 4 — D")
    mode = input("Введите: ").strip()

    ch = Channel(n=4)

    if mode == '0':
        tap = BitTap(ch=ch, group=64)
        tap.start()
    else:
        sel = {'1': 'A', '2': 'B', '3': 'C', '4': 'D'}
        st = sel.get(mode, 'A')
        rx = Receiver(ch=ch, codes=codes, words=words, st=st)
        rx.start()

    txs = []
    for idx, name in enumerate(['A', 'B', 'C', 'D']):
        txs.append(Transmitter(name=name, msg=words[name], code=codes[name], idx=idx, ch=ch))
    for tx in txs:
        tx.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
