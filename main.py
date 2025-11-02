import queue
import sys
from serial_reader import  read_list_ports, open_serial_port, read_from_com

from PyQt6.QtWidgets import QApplication, QMainWindow, QTableWidget, QTableWidgetItem
from designe import Ui_MainWindow  
from decode import Frame


class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)  # Настройка UI из сгенерированного файла

    # Заполняем comboBox_COM при запуске
        self.populate_com_ports()

    def populate_com_ports(self):
        """
        Заполняет comboBox_COM списком доступных COM-портов
        """
        try:
            # Получаем список портов
            ports = read_list_ports()
            self.comboBox_COM.clear()  # Очищаем ComboBox
            self.comboBox_COM.addItems(ports)  # Добавляем найденные порты
        except ValueError as e:
            # Если порты не найдены, выводим сообщение
            QMessageBox.warning(self, "Ошибка", str(e))

    # Подключаем кнопку Подключить к функции подключения к COM-порту
        self.pushButton_connect.clicked.connect(self.on_connect_clicked)

    # Получаем значения всех комбо боксов и...
    def on_connect_clicked(self):
        # Получаем значения из всех ComboBox
        com_port = self.comboBox_COM.currentText()
        baud_rate = self.comboBox_baudrate.currentText()
        date_bit= self.comboBox_date_bit.currentText()
        parity = self.comboBox_parity.currentText()
        stop_bit = self.comboBox_stop_bit.currentText()

    # Функция добавления новой строки к таблице
    def add_row_to_table(self, row_data):
        # Получаем текущую строку в таблице
        row_position = self.SnifferTable.rowCount()
        self.SnifferTable.insertRow(row_position)  # Добавляем новую строку

    # Заполняем ячейки новыми данными
        for column, data in enumerate(row_data):
            # Если data является байтовыми данными, преобразуем их в строку
            if isinstance(data, bytes): # Преобразуем байты в строку в шестнадцатеричном формате
                data = ' '.join(f'{byte:02x}' for byte in data) 
            else: # остальные значения просто преобразовываем в строку
                data = str(data)
            item = QTableWidgetItem(data)  # Создаем новый элемент для ячейки
            self.SnifferTable.setItem(row_position, column, item)  # Устанавливаем элемент в таблицу

if __name__ == "__main__":

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()  # Показываем окно
    # Создаем объект Frame, передавая данные в виде байтов
    Message = Frame(b'\x01\x03p\x08\x00\x01\x1f\x08')
    window.add_row_to_table(Message.get_list())
    current_text = window.comboBox_baudrate.currentText()
    print(current_text)
    sys.exit(app.exec())  # Запуск главного цикла приложения
 


