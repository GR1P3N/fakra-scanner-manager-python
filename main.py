import sys
import serial
from collections import deque
from dataclasses import dataclass, field
from PyQt5 import QtCore, QtWidgets, uic
from PyQt5.QtGui import QPalette, QColor
from PyQt5.QtWidgets import QTableWidgetItem, QAbstractItemView

# Constants
SCAN_LIMIT = 5
PROCESSING_TIME = 7  # seconds
BOX_SEND_DELAY = 500  # ms
WIRE_SEND_DELAY = 300  # ms
SCANNER_PORTS = {'wire': 'COM37', 'box': 'COM14'}
ARDUINO_PORTS = {'ept': 'COM31', 'packing': 'COM33'}
AUTOSCAN = True
UI_FILE = 'main.ui'

@dataclass
class QueueItem:
    wire: str
    box: str
    remaining: int = PROCESSING_TIME
    processing: bool = False

class ScannerThread(QtCore.QThread):
    data_received = QtCore.pyqtSignal(str, str)

    def __init__(self, ports, baudrate=9600):
        super().__init__()
        self.ports = ports
        self.baudrate = baudrate
        self.serials: dict[str, serial.Serial] = {}
        self._running = True

    def run(self):
        # Initialize serial ports
        for name, port in self.ports.items():
            try:
                self.serials[name] = serial.Serial(port, self.baudrate, timeout=1)
            except serial.SerialException as e:
                QtCore.qWarning(f"Failed to open {name}@{port}: {e}")
        # Poll loop
        while self._running:
            for name, ser in list(self.serials.items()):
                try:
                    if ser.in_waiting:
                        raw = ser.readline().decode(errors='ignore').strip().replace('.$!', '')
                        if raw:
                            self.data_received.emit(name, raw)
                except serial.SerialException as e:
                    QtCore.qWarning(f"Error reading {name}: {e}")
                    self.serials.pop(name, None)
            self.msleep(100)
        # Cleanup
        for ser in self.serials.values(): ser.close()

    def stop(self):
        self._running = False
        self.quit()
        self.wait()

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, ui_file: str):
        super().__init__()
        uic.loadUi(ui_file, self)
        # State
        self.current_scans = 0
        self.queue: deque[QueueItem] = deque()
        self.current_wire: str = ''
        self.waiting_for = 'wire'

        self._setup_ui()
        self._init_arduinos()
        self._init_scanner()
        self._init_timer()

    def _setup_ui(self):
        tw = self.tableWidget
        tw.setColumnCount(3)
        tw.setHorizontalHeaderLabels(['Vezeték', 'Doboz', 'Állapot'])
        tw.verticalHeader().setVisible(False)
        tw.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tw.setSelectionMode(QAbstractItemView.NoSelection)
        tw.setFocusPolicy(QtCore.Qt.NoFocus)
        for i, w in enumerate([150, 150, 150]): tw.setColumnWidth(i, w)
        tw.verticalHeader().setDefaultSectionSize(30)

        self.btnNewBox.clicked.connect(self.new_box)
        self.lblStatus.setStyleSheet('color: white;')
        self._update_status('Várakozás a vezetékre...', 'purple')

    def _init_arduinos(self):
        self.arduinos: dict[str, serial.Serial | None] = {}
        for key, port in ARDUINO_PORTS.items():
            try:
                self.arduinos[key] = serial.Serial(port, 9600, timeout=1)
            except serial.SerialException:
                self.arduinos[key] = None

    def _init_scanner(self):
        self.scanner_thread = ScannerThread(SCANNER_PORTS)
        self.scanner_thread.data_received.connect(self.on_scanner_data)
        self.scanner_thread.start()

    def _init_timer(self):
        self.global_timer = QtCore.QTimer(self)
        self.global_timer.timeout.connect(self.process_queue)

    def _update_status(self, text: str, color: str):
        self.lblStatus.setText(text)
        pal = QPalette()
        pal.setColor(QPalette.Window, QColor(color))
        self.setAutoFillBackground(True)
        self.setPalette(pal)

    def on_scanner_data(self, name: str, data: str):
        if name == self.waiting_for:
            if self.waiting_for == 'wire' and self.current_scans < SCAN_LIMIT:
                self.current_wire = data
                self.waiting_for = 'box'
                self._update_status('Várakozás a dobozra...', 'blue')
                self._send_to_arduino('ept', data)
                if AUTOSCAN and 'box' in self.scanner_thread.serials:
                    self.scanner_thread.serials['box'].write(b'\x16T\r')
            elif name == 'box':
                self._handle_box_scan(data)

    def _handle_box_scan(self, data: str):
        self.current_scans += 1
        self.queue.append(QueueItem(self.current_wire, data))
        self._append_row(self.queue[-1])
        # reset scanners
        if 'box' in self.scanner_thread.serials:
            self.scanner_thread.serials['box'].write(b'\x16U\r')
        # reset state
        self.waiting_for = 'wire'
        status = f'Limit elérve! ({self.current_scans}/{SCAN_LIMIT})' if self.current_scans >= SCAN_LIMIT else 'Várakozás a vezetékre...'
        self._update_status(status, 'purple')
        if not self.global_timer.isActive():
            self.global_timer.start(1000)

    def _append_row(self, item: QueueItem):
        row = self.tableWidget.rowCount()
        self.tableWidget.insertRow(row)
        self.tableWidget.setItem(row, 0, QTableWidgetItem(item.wire))
        self.tableWidget.setItem(row, 1, QTableWidgetItem(item.box))
        self.tableWidget.setItem(row, 2, QTableWidgetItem(f"{item.remaining} másodperc"))

    def process_queue(self):
        if not self.queue:
            self.global_timer.stop()
            return
        for item in list(self.queue):
            if item.remaining > 0 and not item.processing:
                item.remaining -= 1
                self._update_row(item, f"{item.remaining} másodperc")
            elif item.remaining <= 0 and not item.processing:
                self._start_processing(item)

    def _update_row(self, item: QueueItem, text: str):
        for r in range(self.tableWidget.rowCount()):
            if self._match_row(r, item):
                self.tableWidget.setItem(r, 2, QTableWidgetItem(text))
                return

    def _start_processing(self, item: QueueItem):
        item.processing = True
        self._update_row(item, 'Doboz küldése...')
        QtWidgets.QApplication.processEvents()
        self._send_to_arduino('packing', item.box)
        QtCore.QTimer.singleShot(BOX_SEND_DELAY, lambda: self._send_wire(item))

    def _send_wire(self, item: QueueItem):
        self._update_row(item, 'Vezeték küldése...')
        QtWidgets.QApplication.processEvents()
        self._send_to_arduino('packing', item.wire)
        QtCore.QTimer.singleShot(WIRE_SEND_DELAY, lambda: self._remove_item(item))

    def _remove_item(self, item: QueueItem):
        for r in range(self.tableWidget.rowCount()):
            if self._match_row(r, item):
                self.tableWidget.removeRow(r)
                break
        self.queue.remove(item)

    def _match_row(self, row: int, item: QueueItem) -> bool:
        w = self.tableWidget.item(row, 0).text()
        b = self.tableWidget.item(row, 1).text()
        return w == item.wire and b == item.box

    def _send_to_arduino(self, key: str, msg: str):
        ar = self.arduinos.get(key)
        if ar:
            try:
                ar.write(msg.encode() + b"\r")
            except serial.SerialException:
                pass

    def new_box(self):
        if self.queue:
            return
        self.current_scans = 0
        self.waiting_for = 'wire'
        self.queue.clear()
        self.tableWidget.setRowCount(0)
        self._update_status('Új doboz. Várakozás a vezetékre...', 'blue')
        self.global_timer.stop()

    def closeEvent(self, event):
        self.scanner_thread.stop()
        for ser in self.arduinos.values():
            if ser and ser.is_open:
                ser.close()
        super().closeEvent(event)

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow(UI_FILE)
    win.show()
    sys.exit(app.exec_())