import threading
import time
from typing import List, Dict, Optional
import queue
import sys
from collections import deque

# ---------- Уолша-коды длины 8 (±1) ----------
def hadamard(n: int) -> List[List[int]]:
    if n & (n - 1) != 0 or n == 0:
        raise ValueError("Длина должна быть степенью 2")
    H = [[1]]
    while len(H) < n:
        top = [row + row for row in H]
        bottom = [row + [-x for x in row] for row in H]
        H = top + bottom
    return H

def walsh_codes(L: int) -> List[List[int]]:
    return hadamard(L)  # строки — ортогональные коды (±1)

# ---------- Битовки ASCII и биполярное представление ----------
def bits_from_ascii(text: str) -> List[int]:
    data = text.encode('ascii', errors='strict')
    bits: List[int] = []
    for b in data:
        for i in range(7, -1, -1):  # MSB -> LSB
            bits.append((b >> i) & 1)
    return bits

def bipolar(bit: int) -> int:
    return 1 if bit == 1 else -1

def bits_to_byte(msb_first_bits: List[int]) -> int:
    val = 0
    for b in msb_first_bits:
        val = (val << 1) | (b & 1)
    return val

# ---------- Канал: суммирует чипы и публикует мягкие значения ----------
class Channel:
    def __init__(self, num_stations: int):
        self.num_stations = num_stations
        self.buf = [0] * num_stations  # текущие чипы от станций (±1)
        self._subscriber: Optional["queue.SimpleQueue[int]"] = None
        self._sub_lock = threading.Lock()

        def aggregate():
            total = sum(self.buf)  # мягкая сумма пользователей за чип
            with self._sub_lock:
                if self._subscriber is not None:
                    self._subscriber.put(total)  # публикуем без печати
            for i in range(self.num_stations):
                self.buf[i] = 0

        self.barrier = threading.Barrier(num_stations, action=aggregate)

    def send_chip(self, station_idx: int, value: int):
        self.buf[station_idx] = value
        self.barrier.wait()

    def attach_receiver(self, q: "queue.SimpleQueue[int]"):
        with self._sub_lock:
            self._subscriber = q

# ---------- Поток передатчика ----------
class Transmitter(threading.Thread):
    def __init__(self, name: str, message: str, code: List[int], station_idx: int, channel: Channel):
        super().__init__(name=f"TX-{name}", daemon=True)
        self.bits = bits_from_ascii(message)
        self.code = code
        self.station_idx = station_idx
        self.channel = channel

    def run(self):
        L = len(self.code)
        i = 0
        while True:
            b = self.bits[i]
            i = (i + 1) % len(self.bits)
            bpol = bipolar(b)  # 0->-1, 1->+1
            for k in range(L):
                chip = bpol * self.code[k]  # spreading по Уолшу
                self.channel.send_chip(self.station_idx, chip)

# ---------- Приёмник: чип-синхронизация + поиск байтового смещения ----------
class Receiver(threading.Thread):
    def __init__(self, channel, codes, words, station: str):
        super().__init__(name="RX", daemon=True)
        self.channel = channel
        self.codes = codes
        self.words = words
        self._station = station
        self._code = codes[station]
        self._expected = words[station].encode('ascii')
        self._chip_queue: "queue.SimpleQueue[int]" = queue.SimpleQueue()
        self._stop = threading.Event()
        self._paused = threading.Event()      # флаг паузы
        self._synced = False
        self._byte_shift = None
        self._window = deque(maxlen=8)        # окно на 8 чипов
        self._bits_stream = []                # восстановленные биты (MSB-first)

    # БЫЛО ОТСУТСТВУЕТ — ДОБАВИТЬ:
    def pause(self):
        self._paused.set()  # приостановить чтение/демодуляцию

    def resume(self):
        self._paused.clear()  # продолжить

    def _flush_queue(self):
        try:
            while True:
                self._chip_queue.get_nowait()
        except queue.Empty:
            pass

    def _reset_state(self):
        self._synced = False
        self._byte_shift = None
        self._window.clear()
        self._bits_stream.clear()
        self._flush_queue()

    def set_station(self, station: str):
        self._station = station
        self._code = self.codes[station]
        self._expected = self.words[station].encode('ascii')
        self._reset_state()

    def stop(self):
        self._stop.set()

    def run(self):
        self.channel.attach_receiver(self._chip_queue)
        L = 8
        while not self._stop.is_set():
            if self._paused.is_set():         # уважать паузу
                time.sleep(0.02)
                continue

            # 1) чиповая синхронизация
            if not self._synced:
                while len(self._window) < L:
                    self._window.append(self._chip_queue.get())
                dot = sum(self._window[i] * self._code[i] for i in range(L))
                if abs(dot) >= 7:             # порог
                    self._synced = True
                else:
                    self._window.append(self._chip_queue.get())
                continue

            # 2) восстановление бита из 8 чипов
            chips = list(self._window)
            self._window.clear()
            for _ in range(L):
                self._window.append(self._chip_queue.get())
            bit = 1 if sum(chips[i]*self._code[i] for i in range(L)) >= 0 else 0
            self._bits_stream.append(bit)

            # 3) поиск байтового смещения (0..7) один раз
            if self._byte_shift is None:
                need = 8 * (len(self._expected) * 3)
                if len(self._bits_stream) < need:
                    continue
                best_shift, best_score = 0, -1
                exp = list(self._expected)
                for s in range(8):
                    bytes_seq = [bits_to_byte(self._bits_stream[i:i+8])
                                 for i in range(s, len(self._bits_stream)-7, 8)]
                    score = sum(1 for i in range(len(bytes_seq)-len(exp)+1)
                                if bytes_seq[i:i+len(exp)] == exp)
                    if score > best_score:
                        best_score, best_shift = score, s
                self._byte_shift = best_shift
                drop = (len(self._bits_stream) - self._byte_shift) % 8
                if drop:
                    self._bits_stream = self._bits_stream[:-drop]
                continue

            # 4) печать ASCII, начиная с найденной границы байта
            while len(self._bits_stream) - self._byte_shift >= 8:
                i = self._byte_shift
                sys.stdout.write(chr(bits_to_byte(self._bits_stream[i:i+8])))
                sys.stdout.flush()
                self._byte_shift += 8

            if self._byte_shift and self._byte_shift > 256:
                self._bits_stream = self._bits_stream[self._byte_shift:]
                self._byte_shift = 0


# ---------- Обработчик ввода (ESC для переключения) ----------
class InputHandler(threading.Thread):
    def __init__(self, rx: Receiver):
        super().__init__(name="INPUT", daemon=True)
        self.rx = rx
        try:
            import msvcrt  # Windows
            self._msvcrt = msvcrt
        except Exception:
            self._msvcrt = None

    def run(self):
        if self._msvcrt:
            m = self._msvcrt
            while True:
                if m.kbhit():
                    ch = m.getch()
                    if ch == b'\x1b':  # ESC
                        self.rx.pause()
                        # ждём букву A/B/C/D
                        sel = None
                        while sel not in (b'A', b'B', b'C', b'D', b'a', b'b', b'c', b'd'):
                            sel = m.getch()
                        station = sel.decode('ascii').upper()
                        self.rx.set_station(station)
                        self.rx.resume()
                time.sleep(0.01)
        else:
            # Кроссплатформенно: ввести A/B/C/D + Enter
            while True:
                line = sys.stdin.readline().strip().upper()
                if line in ("A", "B", "C", "D"):
                    self.rx.pause()
                    self.rx.set_station(line)
                    self.rx.resume()

# ---------- Точка входа ----------
def main():
    # Коды Уолша 8x8
    W = walsh_codes(8)
    codes = {
        'A': W[0],
        'B': W[1],
        'C': W[2],
        'D': W[3],
    }

    words: Dict[str, str] = {
        'A': "GOD",
        'B': "CAT",
        'C': "HAM",
        'D': "SUN",
    }

    channel = Channel(num_stations=4)

    # Приёмник сразу запускаем и печатаем только ASCII выбранной станции
    rx = Receiver(channel=channel, codes=codes, words=words, station='D')
    rx.start()

    # Передатчики
    txs: List[Transmitter] = []
    for idx, name in enumerate(['A', 'B', 'C', 'D']):
        txs.append(Transmitter(name=name, message=words[name], code=codes[name], station_idx=idx, channel=channel))
    for tx in txs:
        tx.start()

    # ESC для переключения станции
    ih = InputHandler(rx)
    ih.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
