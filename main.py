import queue
import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QTableWidget, QTableWidgetItem
from designe import Ui_MainWindow  
from new_frame import Frame

class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)  # Настройка UI из сгенерированного файла

    def add_row_to_table(self, row_data):
        # Получаем текущую строку в таблице
        row_position = self.tableWidget.rowCount()
        self.tableWidget.insertRow(row_position)  # Добавляем новую строку

 # Заполняем ячейки новыми данными
        for column, data in enumerate(row_data):
            # Если data является байтовыми данными, преобразуем их в строку
            if isinstance(data, bytes): # Преобразуем байты в строку в шестнадцатеричном формате
                data = ' '.join(f'{byte:02x}' for byte in data) 
            else: # остальные значения просто преобразовываем в строку
                data = str(data)
            item = QTableWidgetItem(data)  # Создаем новый элемент для ячейки
            self.tableWidget.setItem(row_position, column, item)  # Устанавливаем элемент в таблицу

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()  # Показываем окно
    # Создаем объект Frame, передавая данные в виде байтов
    Message = Frame(b'\x01\x03p\x08\x00\x01\x1f\x08')
    window.add_row_to_table(Message.get_list())
    sys.exit(app.exec())  # Запуск главного цикла приложения
 


