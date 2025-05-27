import sys
import subprocess
import re
from collections import deque
from dataclasses import dataclass

from PyQt5 import QtCore, QtWidgets, uic
from PyQt5.QtGui import QPalette, QColor
from PyQt5.QtWidgets import QTableWidgetItem, QAbstractItemView

# OpenCV és OCR
import cv2
import pytesseract

# --- Konstansok --------------------------------------------------
PROCESSING_TIME      = 7       # másodperc
BOX_SEND_DELAY_MS    = 500     # ms
WIRE_SEND_DELAY_MS   = 300     # ms
SCANNER_PORTS        = {'wire': '/dev/wireScanner', 'box': '/dev/boxScanner'}
ARDUINO_PORTS        = {'ept': 'COM34', 'packing': 'COM36'}
AUTOSCAN             = True
UI_FILE              = 'main.ui'
GPIO_CHIP            = 'gpiochip0'
GPIO_PIN             = '144'

@dataclass
class QueueItem:
    wire: str
    box: str
    remaining: int = PROCESSING_TIME
    processing: bool = False

# --- GPIO olvasó --------------------------------------------------
class GpioReader:
    def __init__(self, chip: str, pin: str):
        self.chip = chip
        self.pin  = pin

    def get_state(self) -> str:
        try:
            r = subprocess.run(
                ['gpioget', self.chip, self.pin],
                capture_output=True, text=True, check=True
            )
            return r.stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"GPIO read error: {e}", file=sys.stderr)
            return '0'
        except FileNotFoundError:
            print("gpioget hiányzik: telepítsd a libgpiod-tools csomagot", file=sys.stderr)
            sys.exit(1)

# --- Scanner szál -------------------------------------------------
class ScannerThread(QtCore.QThread):
    data_received = QtCore.pyqtSignal(str, str)

    def __init__(self, ports: dict[str, str], baudrate=9600):
        super().__init__()
        self.ports     = ports
        self.baudrate  = baudrate
        self.serials   : dict[str, object] = {}
        self._running  = True

    def run(self):
        for name, port in self.ports.items():
            try:
                import serial
                self.serials[name] = serial.Serial(port, self.baudrate, timeout=0.1)
            except Exception as e:
                QtCore.qWarning(f"Failed to open {name}@{port}: {e}")
        while self._running:
            for name, ser in list(self.serials.items()):
                try:
                    line = ser.readline().decode(errors='ignore').strip()
                    if line:
                        clean = re.sub(r'[.$!]', '', line)
                        if clean:
                            self.data_received.emit(name, clean)
                except Exception:
                    self.serials.pop(name, None)
            self.msleep(50)

    def stop(self):
        self._running = False
        self.quit()
        self.wait()
        for ser in self.serials.values():
            try:
                ser.close()
            except:
                pass

# --- OCR és Videócapture szál --------------------------------------
class OcrThread(QtCore.QThread):
    # limit, current without queue
    count_updated = QtCore.pyqtSignal(int, int)

    def __init__(self, x: int, y: int, w: int, h: int, parent=None):
        super().__init__(parent)
        self.running = True
        self.x, self.y, self.w, self.h = x, y, w, h
        self.cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            raise RuntimeError("Nem sikerült megnyitni a kamerát")
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def run(self):
        while self.running:
            for _ in range(5):
                self.cap.grab()
            ret, frame = self.cap.read()
            limit = 0
            current = 0
            if ret:
                roi = frame[self.y:self.y+self.h, self.x:self.x+self.w]
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                gray = cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
                _, binar = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY|cv2.THRESH_OTSU)
                config = '--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789/'
                raw = pytesseract.image_to_string(binar, config=config).strip()
                clean = re.sub(r'[^0-9/]', '', raw)
                m = re.match(r'^\s*(\d+)\s*/\s*(\d+)\s*$', clean)
                if m:
                    limit, current = map(int, m.groups())
            self.count_updated.emit(limit, current)
            self.msleep(1000)

    def stop(self):
        self.running = False
        self.wait()
        try:
            self.cap.release()
        except:
            pass

# --- Főablak ------------------------------------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, ui_file: str):
        super().__init__()
        uic.loadUi(ui_file, self)

        self.queue         = deque[QueueItem]()
        self.current_scans = 0
        self.scan_limit    = PROCESSING_TIME
        self.waiting_for   = 'wire'
        self.current_wire  = ''

        self.gpio = GpioReader(GPIO_CHIP, GPIO_PIN)
        self._setup_ui()

        # OCR-thread indítása
        self.ocr_thread = OcrThread(x=200, y=200, w=80, h=50)
        self.ocr_thread.count_updated.connect(self.on_count_updated)
        self.ocr_thread.start()

        import serial
        self.arduinos : dict[str, object] = {}
        for key, port in ARDUINO_PORTS.items():
            try:
                self.arduinos[key] = serial.Serial(port, 9600, timeout=1)
            except Exception:
                self.arduinos[key] = None

        self.scanner = ScannerThread(SCANNER_PORTS)
        self.scanner.data_received.connect(self.on_scanner_data)
        self.scanner.start()

        self.process_timer = QtCore.QTimer(self)
        self.process_timer.timeout.connect(self.process_queue)

    def _setup_ui(self):
        tw = self.tableWidget
        tw.setColumnCount(3)
        tw.setHorizontalHeaderLabels(['Vezeték', 'Doboz', 'Állapot'])
        tw.verticalHeader().setVisible(False)
        tw.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tw.setSelectionMode(QAbstractItemView.NoSelection)
        tw.setFocusPolicy(QtCore.Qt.NoFocus)
        for i, w in enumerate([192,193,193]):
            tw.setColumnWidth(i, w)
        self.btnNewBox.clicked.connect(self.new_box)
        self._update_status('Várakozás a vezetékre...', 'purple')
        self.lblCount.setText('')

    def _update_status(self, text: str, color: str):
        self.lblStatus.setText(text)
        pal = QPalette()
        pal.setColor(QPalette.Window, QColor(color))
        self.setAutoFillBackground(True)
        self.setPalette(pal)

    @QtCore.pyqtSlot(int, int)
    def on_count_updated(self, limit: int, current: int):
        # current_scans plus pending items
        total = current + len(self.queue)
        self.lblCount.setText(f"{limit}/{total}")

    def on_scanner_data(self, name: str, data: str):
        if name != self.waiting_for:
            return
        if name == 'wire' and self.current_scans < self.scan_limit:
            self.current_wire = data
            self.waiting_for = 'box'
            self._update_status('Várakozás az EPT-re...', 'purple')
            self._send_arduino('ept', data)
            QtCore.QTimer.singleShot(500, self._check_ept)
        elif name == 'box':
            self._handle_box(data)

    def _check_ept(self):
        if self.gpio.get_state() != '1':
            QtCore.QTimer.singleShot(500, self._check_ept)
        else:
            if AUTOSCAN and 'box' in self.scanner.serials:
                self.scanner.serials['box'].write(b'\x16T\r')
            self._update_status('EPT ok, várakozás dobozra...', 'blue')

    def _handle_box(self, code: str):
        self.current_scans += 1
        item = QueueItem(self.current_wire, code)
        self.queue.append(item)
        self._append_row(item)
        if 'box' in self.scanner.serials:
            self.scanner.serials['box'].write(b'\x16U\r')
        self.waiting_for = 'wire'
        status = (f"Limit elérve! ({self.scan_limit}/{self.current_scans + len(self.queue)})"
                  if self.current_scans + len(self.queue) >= self.scan_limit
                  else 'Várakozás a vezetékre...')
        self._update_status(status, 'purple')
        if not self.process_timer.isActive():
            self.process_timer.start(1000)

    def _append_row(self, item: QueueItem):
        tw = self.tableWidget
        r = tw.rowCount()
        tw.insertRow(r)
        tw.setItem(r, 0, QTableWidgetItem(item.wire))
        tw.setItem(r, 1, QTableWidgetItem(item.box))
        tw.setItem(r, 2, QTableWidgetItem(f"{item.remaining} s"))

    def process_queue(self):
        if not self.queue:
            self.process_timer.stop()
            return
        for item in list(self.queue):
            if item.remaining > 0 and not item.processing:
                item.remaining -= 1
                self._update_row(item, f"{item.remaining} s")
            elif item.remaining <= 0 and not item.processing:
                self._start_processing(item)

    def _update_row(self, item: QueueItem, text: str):
        tw = self.tableWidget
        for row in range(tw.rowCount()):
            w = tw.item(row,0).text()
            b = tw.item(row,1).text()
            if w==item.wire and b==item.box:
                tw.setItem(row,2, QTableWidgetItem(text))
                return

    def _start_processing(self, item: QueueItem):
        item.processing = True
        self._update_row(item, 'Doboz küldése...')
        QtWidgets.QApplication.processEvents()
        self._send_arduino('packing', item.box)
        QtCore.QTimer.singleShot(BOX_SEND_DELAY_MS, lambda: self._send_wire(item))

    def _send_wire(self, item: QueueItem):
        self._update_row(item, 'Vezeték küldése...')
        QtWidgets.QApplication.processEvents()
        self._send_arduino('packing', item.wire)
        QtCore.QTimer.singleShot(WIRE_SEND_DELAY_MS, lambda: self._remove_item(item))

    def _remove_item(self, item: QueueItem):
        tw = self.tableWidget
        for row in range(tw.rowCount()):
            w = tw.item(row,0).text()
            b = tw.item(row,1).text()
            if w==item.wire and b==item.box:
                tw.removeRow(row)
                break
        self.queue.remove(item)

    def _send_arduino(self, key: str, msg: str):
        ser = self.arduinos.get(key)
        if ser:
            try:
                ser.write(msg.encode()+b'\r')
            except Exception:
                pass

    def new_box(self):
        if self.queue:
            return
        self.current_scans = 0
        self.scan_limit    = PROCESSING_TIME
        self.waiting_for   = 'wire'
        self.queue.clear()
        self.tableWidget.setRowCount(0)
        self._update_status('Új doboz, várakozás vezetékre...', 'purple')
        self.lblCount.setText('')
        self.process_timer.stop()

    def closeEvent(self, event):
        self.ocr_thread.stop()
        self.scanner.stop()
        for ser in self.arduinos.values():
            if ser and hasattr(ser, 'is_open') and ser.is_open:
                try:
                    ser.close()
                except:
                    pass
        super().closeEvent(event)

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow(UI_FILE)
    win.showFullScreen()
    win.raise_()
    sys.exit(app.exec_())
