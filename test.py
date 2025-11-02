import sys
import threading
import queue
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTableWidgetItem
from PyQt6.QtCore import QTimer
from designe import Ui_MainWindow
from serial_reader import read_list_ports, open_serial_port, read_from_com
from decode import Frame


class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)

        # Очереди для взаимодействия потоков
        self.raw_data_queue = queue.Queue()  # Сырые данные из COM-порта
        self.decoded_data_queue = queue.Queue()  # Декодированные данные

        # Потоки
        self.read_thread = None
        self.decode_thread = None
        self.running = False

        # Инициализация интерфейса
        self.populate_com_ports()
        self.pushButton_connect.clicked.connect(self.on_connect_clicked)

        # Объект Serial для работы с COM-портом
        self.serial_connection = None

    def populate_com_ports(self):
        """Заполняет comboBox_COM списком доступных COM-портов"""
        try:
            ports = read_list_ports()
            self.comboBox_COM.clear()
            self.comboBox_COM.addItems(ports)
        except ValueError as e:
            QMessageBox.warning(self, "Ошибка", str(e))

    def on_connect_clicked(self):
        """Обработчик кнопки Подключить (включает/выключает соединение)"""
        if self.running:  # Если соединение активно, отключаемся
            self.running = False
            if self.serial_connection and self.serial_connection.is_open:
                try:
                    self.serial_connection.close()  # Закрываем порт
                except Exception as e:
                    print(f"Ошибка при закрытии COM-порта: {e}")
            self.pushButton_connect.setText("Подключить")  # Меняем текст кнопки
            QMessageBox.information(self, "Отключено", "Соединение закрыто.")
        else:  # Если соединение не активно, подключаемся
            try:
                com_port = self.comboBox_COM.currentText()
                baud_rate = int(self.comboBox_baudrate.currentText())
                parity = self.comboBox_parity.currentText()
                stop_bit = int(self.comboBox_stop_bit.currentText())    
                bytesize = int(self.comboBox_date_bit.currentText())

                parity_dict = {"None": 'N', "Even": 'E', "Odd": 'O', "Mark": 'M', "Space": 'S'}
                parity = parity_dict.get(parity, 'N')

                # Открываем COM-порт
                self.serial_connection = open_serial_port(com_port, baud_rate, bytesize, parity)

                # Запускаем потоки
                self.running = True
                self.read_thread = threading.Thread(target=self.read_from_com, daemon=True)
                self.decode_thread = threading.Thread(target=self.decode_data, daemon=True)
                self.read_thread.start()
                self.decode_thread.start()

                self.pushButton_connect.setText("Отключить")  # Меняем текст кнопки
                QMessageBox.information(self, "Успех", f"Подключение к {com_port} выполнено!")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось подключиться: {e}")

    def read_from_com(self):
        """Читает данные из COM-порта и добавляет их в очередь"""
        try:
            while self.running:
                read_from_com(self.serial_connection, self.raw_data_queue)
        except Exception as e:
            print(f"Ошибка чтения из COM-порта: {e}")

    def decode_data(self):
        """Обрабатывает сырые данные и добавляет их в очередь декодированных данных"""
        try:
            while self.running:
                if not self.raw_data_queue.empty():
                    raw_data = self.raw_data_queue.get()
                    try:
                        # Создаем объект Frame для декодирования
                        frame = Frame(bytes.fromhex(raw_data))
                        self.decoded_data_queue.put(frame.get_list())
                    except Exception as e:
                        print(f"Ошибка декодирования: {e}")
        except Exception as e:
            print(f"Ошибка обработки данных: {e}")

    def update_ui(self):
        """Обновляет таблицу на основе декодированных данных"""
        try:
            while not self.decoded_data_queue.empty():
                row_data = self.decoded_data_queue.get()
                self.add_row_to_table(row_data)
        except Exception as e:
            print(f"Ошибка обновления интерфейса: {e}")

    def add_row_to_table(self, row_data):
        """Добавляет строку в таблицу"""
        row_position = self.SnifferTable.rowCount()
        self.SnifferTable.insertRow(row_position)
        for column, data in enumerate(row_data):
            item = QTableWidgetItem(str(data))
            self.SnifferTable.setItem(row_position, column, item)

    def closeEvent(self, event):
        """Обработчик закрытия приложения"""
        self.running = False
        if self.serial_connection:
            try:
                if self.serial_connection.is_open:
                    self.serial_connection.close()  # Закрываем порт
            except Exception as e:
                print(f"Ошибка при закрытии COM-порта в closeEvent: {e}")
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()

    # Таймер для обновления интерфейса
    timer = QTimer()
    timer.timeout.connect(window.update_ui)
    timer.start(100)  # Обновление каждые 100 мс

    sys.exit(app.exec())